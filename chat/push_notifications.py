import logging
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path

from django.conf import settings

from api.models import ApiUser, ConversationParticipant, MobileDevice

logger = logging.getLogger("chat.push")

try:
    import firebase_admin
    from firebase_admin import credentials, messaging
except ImportError:  # pragma: no cover - dependency is optional at import time
    firebase_admin = None
    credentials = None
    messaging = None


_FIREBASE_APP = None
_MISSING_SDK_LOGGED = False
_MISSING_SETTINGS_LOGGED = False
_MISSING_FILE_LOGGED = False


def _mask_push_token(value):
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if len(normalized) <= 16:
        return normalized
    return f"{normalized[:8]}...{normalized[-4:]}"


def _truncate_text(value, *, limit=140):
    normalized = " ".join(str(value or "").split()).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 3, 0)].rstrip() + "..."


def _resolve_service_account_path():
    configured = str(getattr(settings, "FCM_SERVICE_ACCOUNT_FILE", "") or "").strip()
    if not configured:
        return None

    path = Path(configured)
    if not path.is_absolute():
        path = Path(settings.BASE_DIR) / path
    return path


def _get_firebase_app():
    global _FIREBASE_APP, _MISSING_SDK_LOGGED, _MISSING_SETTINGS_LOGGED, _MISSING_FILE_LOGGED

    if _FIREBASE_APP is not None:
        return _FIREBASE_APP

    if firebase_admin is None or credentials is None or messaging is None:
        if not _MISSING_SDK_LOGGED:
            logger.info("Firebase Admin SDK is not installed; chat push notifications are disabled.")
            _MISSING_SDK_LOGGED = True
        return None

    service_account_path = _resolve_service_account_path()
    if service_account_path is None:
        if not _MISSING_SETTINGS_LOGGED:
            logger.info(
                "FCM_SERVICE_ACCOUNT_FILE is not configured; chat push notifications are disabled."
            )
            _MISSING_SETTINGS_LOGGED = True
        return None

    if not service_account_path.exists():
        if not _MISSING_FILE_LOGGED:
            logger.warning(
                "FCM service account file was not found at %s; chat push notifications are disabled.",
                service_account_path,
            )
            _MISSING_FILE_LOGGED = True
        return None

    try:
        _FIREBASE_APP = firebase_admin.get_app()
    except ValueError:
        try:
            _FIREBASE_APP = firebase_admin.initialize_app(
                credentials.Certificate(str(service_account_path))
            )
            logger.info(
                "Firebase Admin SDK initialized for chat push notifications using %s.",
                service_account_path,
            )
        except Exception as exc:  # pragma: no cover - only hit with real Firebase config failures
            logger.warning("Unable to initialize Firebase Admin SDK: %s", exc)
            return None

    return _FIREBASE_APP


def _group_active_android_devices(*, user_ids):
    device_map = defaultdict(list)
    if not user_ids:
        return device_map

    queryset = (
        MobileDevice.objects.select_related("user")
        .filter(
            user_id__in=set(user_ids),
            platform=MobileDevice.PLATFORM_ANDROID,
            is_active=True,
            user__chat_push_enabled=True,
        )
        .exclude(push_token="")
        .order_by("user_id", "id")
    )

    for device in queryset:
        device_map[device.user_id].append(device)
    return device_map


def _stringify_data(data):
    payload = {}
    for key, value in data.items():
        if value is None:
            continue
        payload[str(key)] = str(value)
    return payload


def _send_fcm_message(*, token, title, body, data, channel_id, app):
    message = messaging.Message(
        token=token,
        data=_stringify_data(data),
        android=messaging.AndroidConfig(
            priority="high",
        ),
    )
    return messaging.send(message, app=app)


def _base_dispatch_result(*, device, channel_id):
    return {
        "device_model_id": device.id,
        "device_id": device.device_id,
        "user_id": device.user_id,
        "platform": device.platform,
        "channel_id": channel_id,
        "push_token_preview": _mask_push_token(device.push_token),
    }


def _is_sent_dispatch_result(result):
    if isinstance(result, Mapping):
        return result.get("status") == "sent"
    return bool(result)


def _dispatch_notification(*, device, title, body, data, channel_id):
    app = _get_firebase_app()
    base_result = _base_dispatch_result(device=device, channel_id=channel_id)
    if app is None:
        return {
            **base_result,
            "status": "skipped",
            "error_code": "firebase_not_configured",
            "error_detail": (
                "Firebase Admin SDK is unavailable or FCM_SERVICE_ACCOUNT_FILE "
                "is not configured correctly."
            ),
        }
    if not device.push_token:
        return {
            **base_result,
            "status": "skipped",
            "error_code": "missing_push_token",
            "error_detail": "This device does not have a push token.",
        }

    try:
        firebase_message_id = _send_fcm_message(
            token=device.push_token,
            title=title,
            body=body,
            data=data,
            channel_id=channel_id,
            app=app,
        )
        logger.info(
            "Sent chat push notification to user %s device %s on channel %s.",
            device.user_id,
            device.device_id,
            channel_id,
        )
        return {
            **base_result,
            "status": "sent",
            "firebase_message_id": firebase_message_id,
        }
    except Exception as exc:  # pragma: no cover - depends on Firebase runtime behavior
        logger.warning(
            "Unable to send chat push notification to device %s for user %s: %s",
            device.device_id,
            device.user_id,
            exc,
        )
        return {
            **base_result,
            "status": "failed",
            "error_code": exc.__class__.__name__,
            "error_detail": str(exc),
        }


def _build_message_preview(message_payload):
    message_type = str(message_payload.get("message_type") or "text").strip().lower()
    text = _truncate_text(message_payload.get("text"), limit=180)

    if message_type == "text" and text:
        return text

    product = message_payload.get("product")
    if message_type == "product" and isinstance(product, Mapping):
        title = _truncate_text(product.get("title"), limit=120)
        if title:
            return f"Shared: {title}"
        return "Shared a product request."

    if message_type == "media":
        return "Sent an attachment."

    return "Sent you a new message."


def _build_request_preview(part_request):
    title = _truncate_text(getattr(part_request, "title", ""), limit=120)
    description = _truncate_text(getattr(part_request, "description", ""), limit=180)
    return {
        "title": title or "New seller request",
        "description": description or "A supplier posted a new request.",
    }


def _send_request_notification_to_devices(
    *,
    devices,
    request_id,
    requester_id,
    request_title,
    request_description,
    seller_name,
    server_timestamp,
):
    results = []
    for device in devices:
        body = (
            request_description
            if device.user.chat_message_preview_enabled
            else "A supplier posted a new request."
        )
        data = {
            "event_type": "request_created",
            "request_id": request_id,
            "requester_id": requester_id,
            "request_title": request_title,
            "request_description": request_description,
            "seller_name": seller_name,
            "title": request_title,
            "body": body,
            "app_name": "MTA Auto Spare",
            "server_timestamp": server_timestamp,
        }
        results.append(
            _dispatch_notification(
                device=device,
                title=request_title,
                body=body,
                data=data,
                channel_id=settings.FCM_ANDROID_ACTIVITY_CHANNEL_ID,
            )
        )
    return results


def send_chat_message_push_notifications(message_payload):
    sender = message_payload.get("sender") if isinstance(message_payload, Mapping) else {}
    sender_id = sender.get("id") if isinstance(sender, Mapping) else None
    sender_name = ""
    sender_avatar = ""
    if isinstance(sender, Mapping):
        sender_name = str(sender.get("name") or "").strip()
        sender_avatar = str(sender.get("avatar") or "").strip()

    conversation_id = message_payload.get("conversation_id")
    message_id = message_payload.get("id")

    if not sender_id or not conversation_id or not message_id:
        return 0

    recipient_ids = list(
        ConversationParticipant.objects.filter(conversation_id=conversation_id)
        .exclude(user_id=sender_id)
        .values_list("user_id", flat=True)
        .distinct()
    )
    devices_by_user = _group_active_android_devices(user_ids=recipient_ids)
    if not devices_by_user:
        logger.info(
            "Skipping chat push notification for message %s in conversation %s "
            "because recipients %s have no active Android devices registered.",
            message_id,
            conversation_id,
            recipient_ids,
        )
        return 0

    sent_count = 0

    for devices in devices_by_user.values():
        for device in devices:
            body = (
                _build_message_preview(message_payload)
                if device.user.chat_message_preview_enabled
                else "Sent you a new message."
            )
            title = sender_name or "New message"
            data = {
                "event_type": "chat_message",
                "conversation_id": conversation_id,
                "message_id": message_id,
                "actor_user_id": sender_id,
                "title": title,
                "body": body,
                "app_name": "MTA Auto Spare",
                "sender_name": title,
                "sender_avatar": sender_avatar,
                "chat_message_type": str(message_payload.get("message_type") or "text").strip()
                or "text",
                "server_timestamp": message_payload.get("server_timestamp"),
            }
            if _is_sent_dispatch_result(
                _dispatch_notification(
                device=device,
                title=title,
                body=body,
                data=data,
                channel_id=settings.FCM_ANDROID_MESSAGE_CHANNEL_ID,
                )
            ):
                sent_count += 1

    logger.info(
        "Finished chat push dispatch for message %s in conversation %s. "
        "Recipients=%s, devices=%s, sent=%s.",
        message_id,
        conversation_id,
        recipient_ids,
        sum(len(devices) for devices in devices_by_user.values()),
        sent_count,
    )
    return sent_count


def send_request_created_push_notifications(part_request):
    if not part_request or not getattr(part_request, "id", None):
        return 0

    requester = getattr(part_request, "requester", None)
    requester_id = getattr(part_request, "requester_id", None)
    if requester is None or requester_id is None:
        return 0

    preview = _build_request_preview(part_request)
    devices_queryset = (
        MobileDevice.objects.select_related("user")
        .filter(
            platform=MobileDevice.PLATFORM_ANDROID,
            is_active=True,
            user__chat_push_enabled=True,
            user__role=ApiUser.ROLE_SUPPLIER,
        )
        .exclude(user_id=requester_id)
        .exclude(push_token="")
    )
    if getattr(part_request, "car_model_id", None):
        devices_queryset = devices_queryset.filter(
            user__car_model_links__car_model_id=part_request.car_model_id
        )

    devices = list(devices_queryset.order_by("user_id", "id").distinct())
    if not devices:
        logger.info(
            "Skipping request-created push notification for request %s because "
            "no supplier devices are active.",
            part_request.id,
        )
        return 0

    sent_count = 0
    results = _send_request_notification_to_devices(
        devices=devices,
        request_id=part_request.id,
        requester_id=requester_id,
        request_title=preview["title"],
        request_description=preview["description"],
        seller_name=str(getattr(requester, "name", "") or "").strip() or "Supplier",
        server_timestamp=getattr(part_request, "created_at", None),
    )
    sent_count = sum(1 for result in results if _is_sent_dispatch_result(result))

    logger.info(
        "Finished request-created push dispatch for request %s. suppliers=%s sent=%s.",
        part_request.id,
        len(devices),
        sent_count,
    )
    return sent_count


def send_test_request_notification(
    *,
    device,
    request_id,
    requester_id,
    request_title,
    request_description,
    seller_name,
    server_timestamp=None,
):
    results = _send_request_notification_to_devices(
        devices=[device],
        request_id=request_id,
        requester_id=requester_id,
        request_title=request_title,
        request_description=request_description,
        seller_name=seller_name,
        server_timestamp=server_timestamp,
    )
    return results[0]


def send_typing_push_notifications(*, conversation_id, actor_user_id, is_typing):
    logger.info(
        "Skipping typing push notification for conversation %s actor %s because "
        "chat notifications are limited to real messages.",
        conversation_id,
        actor_user_id,
    )
    return 0


def send_message_status_push_notifications(status_events):
    logger.info(
        "Skipping message-status push notifications because chat notifications "
        "are limited to real messages. events=%s",
        1 if isinstance(status_events, Mapping) else len(status_events or []),
    )
    return 0
