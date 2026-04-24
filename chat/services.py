import base64

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from api.translation import stamp_message_language
from api.models import (
    Conversation,
    ConversationParticipant,
    Message,
    MessageAttachment,
    MessageHiddenForUser,
    MessageStatus,
)

from .runtime import get_connected_user_ids, get_globally_connected_user_ids


VALID_MESSAGE_TYPES = {choice[0] for choice in Message.MESSAGE_TYPES}


def _load_message_with_relations(message_id):
    return (
        Message.objects.select_related(
            "sender",
            "product",
            "product__status",
            "product__car_model__make",
            "reply_to__sender",
            "reply_to__product",
            "reply_to__product__status",
            "reply_to__product__car_model__make",
        )
        .prefetch_related(
            "attachments",
            "statuses__message",
            "statuses",
            "reply_to__hidden_for_users",
        )
        .get(pk=message_id)
    )


def serialize_message_status(status):
    return {
        "conversation_id": int(status.message.conversation_id),
        "message_id": int(status.message_id),
        "user_id": int(status.user_id),
        "status": status.status,
        "updated_at": status.updated_at.isoformat(),
    }


def serialize_user(user, *, online_user_ids=None):
    if online_user_ids is None:
        online_user_ids = get_globally_connected_user_ids() or set()

    return {
        "id": user.id,
        "name": user.name,
        "avatar": user.avatar.url if user.avatar else None,
        "is_online": user.id in online_user_ids,
        "last_seen_at": user.chat_last_seen_at.isoformat() if user.chat_last_seen_at else None,
    }


def serialize_product(product):
    if not product:
        return None

    return {
        "id": product.id,
        "title": product.title,
        "title_language": product.title_language or None,
        "min_price": str(product.min_price) if product.min_price is not None else None,
        "max_price": str(product.max_price) if product.max_price is not None else None,
        "status": product.status_id,
        "status_details": {
            "id": product.status_id,
            "code": product.status.code,
            "label": product.status.label,
            "is_terminal": product.status.is_terminal,
            "created_at": product.status.created_at.isoformat(),
        },
        "car_model_details": (
            {
                "id": product.car_model.id,
                "make_id": product.car_model.make_id,
                "make_name": product.car_model.make.name,
                "name": product.car_model.name,
                "display_name": f"{product.car_model.make.name} {product.car_model.name}",
                "image_url": product.car_model.image_url,
                "is_active": product.car_model.is_active,
            }
            if product.car_model_id
            else None
        ),
    }


def serialize_attachment(attachment):
    return {
        "id": attachment.id,
        "file_url": attachment.file.url if attachment.file else None,
        "content_type": attachment.content_type,
        "size": attachment.size,
        "created_at": attachment.created_at.isoformat(),
    }


def serialize_reply(message):
    if not message:
        return None

    return {
        "id": message.id,
        "sender": serialize_user(message.sender),
        "text": message.text,
        "text_language": message.text_language or None,
        "product": serialize_product(message.product),
        "client_timestamp": message.client_timestamp.isoformat(),
        "server_timestamp": message.server_timestamp.isoformat(),
        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
        "is_deleted": bool(message.is_deleted),
    }


def serialize_message_payload(message):
    if not hasattr(message, "sender"):
        message = _load_message_with_relations(message.pk)

    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "sender": serialize_user(message.sender),
        "message_type": message.message_type,
        "text": message.text,
        "text_language": message.text_language or None,
        "product": serialize_product(message.product),
        "reply_to": serialize_reply(message.reply_to),
        "media": [serialize_attachment(item) for item in message.attachments.all()],
        "client_timestamp": message.client_timestamp.isoformat(),
        "server_timestamp": message.server_timestamp.isoformat(),
        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
        "is_deleted": bool(message.is_deleted),
        "statuses": [
            serialize_message_status(status)
            for status in message.statuses.select_related("message").order_by("user_id")
        ],
    }


def _validate_attachment_payload(*, content_type, size):
    if not content_type:
        raise ValueError("Media attachments must include a content_type.")
    if settings.CHAT_ALLOWED_MEDIA_TYPES and content_type not in settings.CHAT_ALLOWED_MEDIA_TYPES:
        raise ValueError(f"Unsupported media content_type: {content_type}.")
    if size <= 0:
        raise ValueError("Media attachments must not be empty.")
    if size > settings.CHAT_MAX_MEDIA_BYTES:
        raise ValueError(
            f"Media attachments must be {settings.CHAT_MAX_MEDIA_BYTES} bytes or smaller."
        )


def mark_message_as_latest(message):
    Conversation.objects.filter(pk=message.conversation_id).update(
        last_message=message,
        last_message_time=message.server_timestamp,
    )


def initialize_message_statuses(message, delivered_user_ids=None):
    statuses = []

    sender_status, _ = MessageStatus.objects.update_or_create(
        message=message,
        user=message.sender,
        defaults={"status": MessageStatus.STATUS_SENT},
    )
    statuses.append(serialize_message_status(sender_status))

    participant_ids = ConversationParticipant.objects.filter(
        conversation_id=message.conversation_id
    ).exclude(user=message.sender).values_list("user_id", flat=True)

    delivered_user_ids = (
        set(delivered_user_ids)
        if delivered_user_ids is not None
        else set(participant_ids)
    )

    for user_id in participant_ids:
        if user_id not in delivered_user_ids:
            continue
        delivered_status, _ = MessageStatus.objects.update_or_create(
            message=message,
            user_id=user_id,
            defaults={"status": MessageStatus.STATUS_DELIVERED},
        )
        statuses.append(serialize_message_status(delivered_status))

    return statuses


def _decode_socket_media_payloads(media_files):
    decoded = []
    for index, item in enumerate(media_files or [], start=1):
        raw = item.get("data_base64")
        if not raw:
            raise ValueError("Each media_files item must include data_base64.")

        file_name = item.get("name") or f"socket-upload-{index}"
        try:
            binary_content = base64.b64decode(raw, validate=True)
        except Exception as exc:
            raise ValueError("Media file payload is not valid base64.") from exc

        content_type = item.get("content_type", "").strip()
        size = len(binary_content)
        _validate_attachment_payload(content_type=content_type, size=size)

        content = ContentFile(binary_content, name=file_name)
        decoded.append(
            {
                "file": content,
                "content_type": content_type,
                "size": size,
            }
        )

    return decoded


def _create_attachments(message, *, files=None, media_files=None):
    attachments = []

    for uploaded in files or []:
        content_type = getattr(uploaded, "content_type", "") or ""
        size = getattr(uploaded, "size", 0) or 0
        _validate_attachment_payload(content_type=content_type, size=size)
        attachments.append(
            MessageAttachment.objects.create(
                message=message,
                file=uploaded,
                content_type=content_type,
                size=size,
            )
        )

    for payload in _decode_socket_media_payloads(media_files):
        attachments.append(
            MessageAttachment.objects.create(
                message=message,
                file=payload["file"],
                content_type=payload["content_type"],
                size=payload["size"],
            )
        )

    return attachments


def get_default_delivered_user_ids(conversation_id):
    connected_user_ids = get_connected_user_ids(conversation_id)
    if connected_user_ids is None:
        return set()
    return set(connected_user_ids)


def create_message_with_statuses(
    *,
    conversation_id,
    sender,
    text="",
    message_type="text",
    client_timestamp,
    product=None,
    reply_to=None,
    files=None,
    media_files=None,
    delivered_user_ids=None,
):
    message_type = message_type or "text"
    text = (text or "").strip()

    if message_type not in VALID_MESSAGE_TYPES:
        raise ValueError(f"Unsupported message_type: {message_type}.")
    if message_type == "text" and not text:
        raise ValueError("Text is required for text messages.")
    if message_type == "product" and product is None:
        raise ValueError("Product is required for product messages.")
    if message_type == "media" and not files and not media_files:
        raise ValueError("Media message requires file attachments.")

    with transaction.atomic():
        message = Message.objects.create(
            conversation_id=conversation_id,
            sender=sender,
            text=text,
            message_type=message_type,
            product=product,
            reply_to=reply_to,
            client_timestamp=client_timestamp,
        )
        stamp_message_language(message)
        message.save(update_fields=["text_language"])
        _create_attachments(message, files=files, media_files=media_files)
        mark_message_as_latest(message)
        statuses = initialize_message_statuses(message, delivered_user_ids=delivered_user_ids)
        message = _load_message_with_relations(message.pk)

    return serialize_message_payload(message), statuses


def update_text_message(message, *, text):
    normalized_text = (text or "").strip()
    if not normalized_text:
        raise ValueError("Text is required to edit a message.")
    if message.is_deleted:
        raise ValueError("Deleted messages cannot be edited.")
    if message.message_type != "text":
        raise ValueError("Only text messages can be edited.")
    if message.product_id is not None or message.attachments.exists():
        raise ValueError("Only plain text messages can be edited.")

    if message.text == normalized_text:
        return serialize_message_payload(_load_message_with_relations(message.pk))

    Message.objects.filter(pk=message.pk).update(
        text=normalized_text,
        text_language=stamp_message_language(Message(text=normalized_text)).text_language,
        edited_at=timezone.now(),
    )
    return serialize_message_payload(_load_message_with_relations(message.pk))


def delete_message_for_everyone(message):
    with transaction.atomic():
        attachments = list(message.attachments.all())
        for attachment in attachments:
            if attachment.file:
                attachment.file.delete(save=False)
        if attachments:
            message.attachments.all().delete()

        Message.objects.filter(pk=message.pk).update(
            text="",
            text_language="",
            product=None,
            reply_to=None,
            edited_at=None,
            is_deleted=True,
        )

    return serialize_message_payload(_load_message_with_relations(message.pk))


def hide_message_for_user(message, user):
    MessageHiddenForUser.objects.get_or_create(message=message, user=user)


def mark_conversation_delivered(conversation_id, user):
    statuses = []
    messages = (
        Message.objects.filter(conversation_id=conversation_id)
        .exclude(sender=user)
        .exclude(hidden_for_users__user=user)
    )

    for message in messages:
        status, created = MessageStatus.objects.get_or_create(
            message=message,
            user=user,
            defaults={"status": MessageStatus.STATUS_DELIVERED},
        )
        if created:
            statuses.append(serialize_message_status(status))
            continue

        if status.status == MessageStatus.STATUS_SENT:
            status.status = MessageStatus.STATUS_DELIVERED
            status.save(update_fields=["status", "updated_at"])
            statuses.append(serialize_message_status(status))

    return statuses


def mark_conversation_seen(conversation_id, user):
    seen_at = timezone.now()
    ConversationParticipant.objects.filter(
        conversation_id=conversation_id,
        user=user,
    ).update(last_read_at=seen_at)

    statuses = []
    messages = (
        Message.objects.filter(conversation_id=conversation_id)
        .exclude(sender=user)
        .exclude(hidden_for_users__user=user)
    )

    for message in messages:
        status, created = MessageStatus.objects.get_or_create(
            message=message,
            user=user,
            defaults={"status": MessageStatus.STATUS_SEEN},
        )
        if created:
            statuses.append(serialize_message_status(status))
            continue

        if status.status != MessageStatus.STATUS_SEEN:
            status.status = MessageStatus.STATUS_SEEN
            status.save(update_fields=["status", "updated_at"])
            statuses.append(serialize_message_status(status))

    return statuses, seen_at.isoformat()
