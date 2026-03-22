from django.db import transaction
from django.utils import timezone

from api.models import Conversation, ConversationParticipant, Message, MessageStatus


def serialize_message_status(status):
    return {
        "conversation_id": status.message.conversation_id,
        "message_id": status.message_id,
        "user_id": status.user_id,
        "status": status.status,
        "updated_at": status.updated_at.isoformat(),
    }


def mark_message_as_latest(message):
    Conversation.objects.filter(pk=message.conversation_id).update(
        last_message=message,
        last_message_time=message.server_timestamp,
    )


def initialize_message_statuses(message):
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

    for user_id in participant_ids:
        delivered_status, _ = MessageStatus.objects.update_or_create(
            message=message,
            user_id=user_id,
            defaults={"status": MessageStatus.STATUS_DELIVERED},
        )
        statuses.append(serialize_message_status(delivered_status))

    return statuses


def create_message_with_statuses(
    *,
    conversation_id,
    sender,
    text="",
    message_type="text",
    client_timestamp,
):
    with transaction.atomic():
        message = Message.objects.create(
            conversation_id=conversation_id,
            sender=sender,
            text=text,
            message_type=message_type,
            client_timestamp=client_timestamp,
        )
        mark_message_as_latest(message)
        statuses = initialize_message_statuses(message)

    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "sender_id": message.sender_id,
        "text": message.text,
        "message_type": message.message_type,
        "client_timestamp": message.client_timestamp.isoformat(),
        "server_timestamp": message.server_timestamp.isoformat(),
    }, statuses


def mark_conversation_seen(conversation_id, user):
    read_at = timezone.now()
    ConversationParticipant.objects.filter(
        conversation_id=conversation_id,
        user=user,
    ).update(last_read_at=read_at)

    statuses = []
    messages = Message.objects.filter(conversation_id=conversation_id).exclude(sender=user)

    for message in messages:
        status, created = MessageStatus.objects.get_or_create(
            message=message,
            user=user,
            defaults={"status": MessageStatus.STATUS_READ},
        )
        if created:
            statuses.append(serialize_message_status(status))
            continue

        if status.status != MessageStatus.STATUS_READ:
            status.status = MessageStatus.STATUS_READ
            status.save(update_fields=["status", "updated_at"])
            statuses.append(serialize_message_status(status))

    return statuses
