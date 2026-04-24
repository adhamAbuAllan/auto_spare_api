import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from api.models import ConversationParticipant


logger = logging.getLogger(__name__)


def chat_group_name(conversation_id):
    return f"chat_{int(conversation_id)}"


def inbox_group_name(user_id):
    return f"inbox_{int(user_id)}"


def broadcast_chat_event(conversation_id, event_type, payload):
    channel_layer = get_channel_layer()
    if not channel_layer:
        return

    try:
        async_to_sync(channel_layer.group_send)(
            chat_group_name(conversation_id),
            {
                "type": event_type,
                **payload,
            },
        )
    except Exception as exc:
        logger.warning(
            "Unable to broadcast chat event %s for conversation %s: %s",
            event_type,
            conversation_id,
            exc,
        )


def broadcast_created_message(message_payload, status_events):
    broadcast_chat_event(
        message_payload["conversation_id"],
        "message_created",
        {"message": message_payload},
    )
    for status_event in status_events:
        broadcast_chat_event(
            status_event["conversation_id"],
            "message_status",
            status_event,
        )


def broadcast_inbox_message(message_payload):
    channel_layer = get_channel_layer()
    if not channel_layer:
        return

    conversation_id = int(message_payload["conversation_id"])
    participant_user_ids = list(
        ConversationParticipant.objects.filter(conversation_id=conversation_id)
        .values_list("user_id", flat=True)
        .distinct()
    )
    for user_id in participant_user_ids:
        try:
            async_to_sync(channel_layer.group_send)(
                inbox_group_name(user_id),
                {
                    "type": "inbox_message",
                    "message": message_payload,
                },
            )
        except Exception as exc:
            logger.warning(
                "Unable to broadcast inbox event for conversation %s to user %s: %s",
                conversation_id,
                user_id,
                exc,
            )
