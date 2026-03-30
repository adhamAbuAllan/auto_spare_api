import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from api.models import ApiUser, ConversationParticipant, PartRequest, Message
from .runtime import (
    add_connected_user,
    add_globally_connected_user,
    get_conversation_runtime_state,
    get_globally_connected_user_ids,
    remove_connected_user,
    remove_globally_connected_user,
    set_typing_state,
)
from .services import (
    create_message_with_statuses,
    get_default_delivered_user_ids,
    mark_conversation_delivered,
    mark_conversation_seen,
)

logger = logging.getLogger("chat.events")
trace_logger = logging.getLogger("chat.trace")


class ChatConsumer(AsyncWebsocketConsumer):
    def should_trace_payloads(self):
        trace_conversation_id = getattr(settings, "CHAT_TRACE_CONVERSATION_ID", None)
        if trace_conversation_id is None:
            return False

        try:
            return int(self.conversation_id) == trace_conversation_id
        except (TypeError, ValueError):
            return False

    def log_trace_payload(self, *, direction, payload=None, raw_text=None):
        if not self.should_trace_payloads():
            return

        if payload is not None:
            serialized_payload = json.dumps(payload, indent=2, sort_keys=True, default=str)
        else:
            serialized_payload = raw_text or ""

        trace_logger.info(
            "chat.trace direction=%s conversation_id=%s user_id=%s connection_id=%s\n%s",
            direction,
            self.conversation_id,
            getattr(self.user, "id", None),
            getattr(self, "connection_id", None),
            serialized_payload,
        )

    async def send(self, text_data=None, bytes_data=None, close=False):
        if text_data:
            try:
                payload = json.loads(text_data)
            except json.JSONDecodeError:
                self.log_trace_payload(direction="outgoing", raw_text=text_data)
            else:
                self.log_trace_payload(direction="outgoing", payload=payload)

        await super().send(text_data=text_data, bytes_data=bytes_data, close=close)

    async def connect(self):
        self.group_name = None
        self.user = self.scope.get("user")
        self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]
        self.connection_id = self.channel_name
        self._last_presence_persisted_at = None
        if not self.user or self.user.is_anonymous:
            logger.info(
                "chat.connect.rejected reason=unauthenticated conversation_id=%s connection_id=%s",
                self.conversation_id,
                self.connection_id,
            )
            await self.close(code=4401)
            return

        self.group_name = f"chat_{self.conversation_id}"

        is_allowed = await self.is_participant()
        if not is_allowed:
            logger.info(
                "chat.connect.rejected reason=forbidden conversation_id=%s user_id=%s connection_id=%s",
                self.conversation_id,
                self.user.id,
                self.connection_id,
            )
            await self.close(code=4403)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        last_seen_at = await self.persist_user_last_seen(force=True)
        presence_event = await self.mark_connected()
        presence_event["last_seen_at"] = last_seen_at
        await self.accept()
        logger.info(
            "chat.connect.accepted conversation_id=%s user_id=%s connection_id=%s",
            self.conversation_id,
            self.user.id,
            self.connection_id,
        )
        await self.send(
            text_data=json.dumps(
                {
                    "type": "conversation.state",
                    **(await self.get_runtime_state()),
                }
            )
        )
        await self.broadcast_user_presence(presence_event)
        for status_event in await self.mark_delivered():
            await self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "message_status",
                    **status_event,
                },
            )

    async def disconnect(self, close_code):
        if self.group_name:
            typing_payload = await self.update_typing(False)
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
            last_seen_at = await self.persist_user_last_seen(force=True)
            presence_event = await self.mark_disconnected()
            presence_event["last_seen_at"] = last_seen_at
            logger.info(
                "chat.disconnect conversation_id=%s user_id=%s connection_id=%s close_code=%s",
                self.conversation_id,
                self.user.id,
                self.connection_id,
                close_code,
            )
            await self.broadcast_user_presence(presence_event)
            if typing_payload["changed"]:
                await self.channel_layer.group_send(
                    self.group_name,
                    {
                        "type": "conversation_typing",
                        "conversation_id": typing_payload["conversation_id"],
                        "user_id": typing_payload["user_id"],
                        "is_typing": typing_payload["is_typing"],
                    },
                )

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return

        self.log_trace_payload(direction="incoming", raw_text=text_data)
        try:
            payload = json.loads(text_data)
        except json.JSONDecodeError:
            logger.info(
                "chat.receive.invalid_json conversation_id=%s user_id=%s connection_id=%s",
                self.conversation_id,
                getattr(self.user, "id", None),
                self.connection_id,
            )
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "error",
                        "detail": "Payload must be valid JSON.",
                    }
                )
            )
            return

        event_type = payload.get("type")
        logger.info(
            "chat.receive conversation_id=%s user_id=%s connection_id=%s event_type=%s",
            self.conversation_id,
            self.user.id,
            self.connection_id,
            event_type,
        )
        last_seen_at = await self.persist_user_last_seen(force=False)
        presence_event = await self.touch_presence()
        presence_event["last_seen_at"] = last_seen_at
        await self.broadcast_user_presence(presence_event)
        if event_type == "chat_message":
            await self.process_chat_message(payload)
        elif event_type in {"typing", "typing_start"}:
            await self.process_typing(is_typing=True)
        elif event_type == "typing_stop":
            await self.process_typing(is_typing=False)
        elif event_type == "seen":
            await self.process_seen()
        elif event_type == "ping":
            await self.process_ping()
        else:
            logger.info(
                "chat.receive.unsupported conversation_id=%s user_id=%s event_type=%s",
                self.conversation_id,
                self.user.id,
                event_type,
            )
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "error",
                        "detail": f"Unsupported event type: {event_type!r}.",
                    }
                )
            )

    async def process_ping(self):
        logger.info(
            "chat.ping conversation_id=%s user_id=%s connection_id=%s",
            self.conversation_id,
            self.user.id,
            self.connection_id,
        )
        await self.send(
            text_data=json.dumps(
                {
                    "type": "pong",
                    "conversation_id": int(self.conversation_id),
                    "server_timestamp": timezone.now().isoformat(),
                }
            )
        )

    async def process_chat_message(self, payload):
        text = payload.get("text", "")
        message_type = payload.get("message_type", "text")
        client_timestamp_raw = payload.get("client_timestamp")
        client_timestamp = parse_datetime(client_timestamp_raw) if client_timestamp_raw else None
        if client_timestamp and timezone.is_naive(client_timestamp):
            client_timestamp = timezone.make_aware(client_timestamp)

        if not client_timestamp:
            client_timestamp = timezone.now()

        logger.info(
            "chat.message.received conversation_id=%s user_id=%s message_type=%s product_id=%s reply_to_id=%s media_count=%s text_length=%s",
            self.conversation_id,
            self.user.id,
            message_type,
            payload.get("product_id"),
            payload.get("reply_to_id"),
            len(payload.get("media_files") or []),
            len(text or ""),
        )
        try:
            message = await self.create_message(
                text=text,
                message_type=message_type,
                client_timestamp=client_timestamp,
                product_id=payload.get("product_id"),
                reply_to_id=payload.get("reply_to_id"),
                media_files=payload.get("media_files"),
            )
        except ValueError as exc:
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "error",
                        "detail": str(exc),
                    }
                )
            )
            return
        if not message:
            logger.info(
                "chat.message.skipped conversation_id=%s user_id=%s",
                self.conversation_id,
                self.user.id,
            )
            return

        logger.info(
            "chat.message.created conversation_id=%s user_id=%s message_id=%s status_events=%s",
            self.conversation_id,
            self.user.id,
            message["message"]["id"],
            len(message["status_events"]),
        )
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "message_created",
                "message": message["message"],
            },
        )
        for status_event in message["status_events"]:
            await self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "message_status",
                    **status_event,
                },
            )

    async def process_typing(self, *, is_typing):
        typing_payload = await self.update_typing(is_typing)
        logger.info(
            "chat.typing conversation_id=%s user_id=%s is_typing=%s changed=%s",
            self.conversation_id,
            self.user.id,
            is_typing,
            typing_payload["changed"],
        )
        if not typing_payload["changed"]:
            return
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "conversation_typing",
                "conversation_id": typing_payload["conversation_id"],
                "user_id": typing_payload["user_id"],
                "is_typing": typing_payload["is_typing"],
            },
        )

    async def process_seen(self):
        status_events, seen_at = await self.update_seen()
        logger.info(
            "chat.seen conversation_id=%s user_id=%s status_events=%s seen_at=%s",
            self.conversation_id,
            self.user.id,
            len(status_events),
            seen_at,
        )
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "conversation_seen",
                "conversation_id": int(self.conversation_id),
                "user_id": self.user.id,
                "seen_at": seen_at,
            },
        )
        for status_event in status_events:
            await self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "message_status",
                    **status_event,
                },
            )

    async def message_created(self, event):
        payload = dict(event)
        payload.pop("type", None)
        await self.send(text_data=json.dumps({"type": "message.created", **payload}))

    async def conversation_typing(self, event):
        payload = dict(event)
        payload.pop("type", None)
        await self.send(text_data=json.dumps({"type": "conversation.typing", **payload}))

    async def conversation_seen(self, event):
        payload = dict(event)
        payload.pop("type", None)
        await self.send(text_data=json.dumps({"type": "conversation.seen", **payload}))

    async def message_status(self, event):
        payload = dict(event)
        payload.pop("type", None)
        await self.send(text_data=json.dumps({"type": "message.status", **payload}))

    async def user_presence(self, event):
        payload = dict(event)
        payload.pop("type", None)
        await self.send(text_data=json.dumps({"type": "user.presence", **payload}))

    async def broadcast_user_presence(self, presence_event):
        if not presence_event.get("changed"):
            return

        conversation_ids = await self.get_presence_target_conversation_ids()
        payload = {
            "type": "user_presence",
            "user_id": int(presence_event["user_id"]),
            "is_online": bool(presence_event["is_online"]),
            "last_seen_at": presence_event.get("last_seen_at"),
        }
        for conversation_id in conversation_ids:
            await self.channel_layer.group_send(f"chat_{conversation_id}", payload)

    @database_sync_to_async
    def is_participant(self):
        return ConversationParticipant.objects.filter(
            conversation_id=self.conversation_id, user=self.user
        ).exists()

    @database_sync_to_async
    def create_message(
        self,
        *,
        text,
        message_type,
        client_timestamp,
        product_id=None,
        reply_to_id=None,
        media_files=None,
    ):
        product = None
        reply_to = None
        if product_id:
            product = PartRequest.objects.filter(pk=product_id).first()
            if product is None:
                raise ValueError("product_id does not reference an existing part request.")
        if reply_to_id:
            reply_to = Message.objects.filter(
                pk=reply_to_id,
                conversation_id=self.conversation_id,
            ).select_related("sender", "product").first()
            if reply_to is None:
                raise ValueError("reply_to must belong to the same conversation.")

        delivered_user_ids = get_default_delivered_user_ids(self.conversation_id)
        delivered_user_ids = delivered_user_ids - {self.user.id}

        message_payload, status_events = create_message_with_statuses(
            conversation_id=self.conversation_id,
            sender=self.user,
            text=text,
            message_type=message_type,
            client_timestamp=client_timestamp,
            product=product,
            reply_to=reply_to,
            media_files=media_files,
            delivered_user_ids=delivered_user_ids,
        )
        return {
            "message": message_payload,
            "status_events": status_events,
        }

    @database_sync_to_async
    def mark_connected(self):
        add_connected_user(self.conversation_id, self.user.id, self.connection_id)
        return add_globally_connected_user(self.user.id, self.connection_id)

    @database_sync_to_async
    def get_runtime_state(self):
        runtime_state = get_conversation_runtime_state(self.conversation_id)
        participant_ids = list(
            ConversationParticipant.objects.filter(conversation_id=self.conversation_id)
            .values_list("user_id", flat=True)
            .distinct()
        )
        online_user_ids = get_globally_connected_user_ids() or set()
        last_seen_at_by_user_id = {
            str(user.id): user.chat_last_seen_at.isoformat() if user.chat_last_seen_at else None
            for user in ApiUser.objects.filter(id__in=participant_ids).only("id", "chat_last_seen_at")
        }
        runtime_state["online_user_ids"] = sorted(
            user_id for user_id in participant_ids if user_id in online_user_ids
        )
        runtime_state["presence_last_seen_at_by_user_id"] = last_seen_at_by_user_id
        return runtime_state

    @database_sync_to_async
    def mark_disconnected(self):
        remove_connected_user(self.conversation_id, self.user.id, self.connection_id)
        return remove_globally_connected_user(self.user.id, self.connection_id)

    @database_sync_to_async
    def mark_delivered(self):
        return mark_conversation_delivered(self.conversation_id, self.user)

    @database_sync_to_async
    def touch_presence(self):
        add_connected_user(self.conversation_id, self.user.id, self.connection_id)
        return add_globally_connected_user(self.user.id, self.connection_id)

    @database_sync_to_async
    def update_typing(self, is_typing):
        return set_typing_state(
            self.conversation_id,
            self.user.id,
            self.connection_id,
            is_typing,
        )

    @database_sync_to_async
    def update_seen(self):
        return mark_conversation_seen(self.conversation_id, self.user)

    @database_sync_to_async
    def get_presence_target_conversation_ids(self):
        return list(
            ConversationParticipant.objects.filter(user=self.user)
            .values_list("conversation_id", flat=True)
            .distinct()
        )

    @database_sync_to_async
    def _persist_user_last_seen(self, seen_at):
        ApiUser.objects.filter(pk=self.user.id).update(chat_last_seen_at=seen_at)

    async def persist_user_last_seen(self, *, force):
        seen_at = timezone.now()
        last_persisted_at = getattr(self, "_last_presence_persisted_at", None)
        if not force and last_persisted_at is not None:
            if (seen_at - last_persisted_at).total_seconds() < 60:
                return last_persisted_at.isoformat()

        await self._persist_user_last_seen(seen_at)
        self._last_presence_persisted_at = seen_at
        return seen_at.isoformat()
