import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from api.models import ConversationParticipant, PartRequest, Message
from .runtime import (
    add_connected_user,
    get_conversation_runtime_state,
    remove_connected_user,
    set_typing_state,
)
from .services import (
    create_message_with_statuses,
    get_default_delivered_user_ids,
    mark_conversation_delivered,
    mark_conversation_seen,
)


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.group_name = None
        self.user = self.scope.get("user")
        self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]
        self.connection_id = self.channel_name
        if not self.user or self.user.is_anonymous:
            await self.close(code=4401)
            return

        self.group_name = f"chat_{self.conversation_id}"

        is_allowed = await self.is_participant()
        if not is_allowed:
            await self.close(code=4403)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.mark_connected()
        await self.accept()
        await self.send(
            text_data=json.dumps(
                {
                    "type": "conversation.state",
                    **(await self.get_runtime_state()),
                }
            )
        )
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
            await self.mark_disconnected()
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

        try:
            payload = json.loads(text_data)
        except json.JSONDecodeError:
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
        await self.touch_presence()
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
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "error",
                        "detail": f"Unsupported event type: {event_type!r}.",
                    }
                )
            )

    async def process_ping(self):
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
            return

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

    @database_sync_to_async
    def get_runtime_state(self):
        return get_conversation_runtime_state(self.conversation_id)

    @database_sync_to_async
    def mark_disconnected(self):
        remove_connected_user(self.conversation_id, self.user.id, self.connection_id)

    @database_sync_to_async
    def mark_delivered(self):
        return mark_conversation_delivered(self.conversation_id, self.user)

    @database_sync_to_async
    def touch_presence(self):
        add_connected_user(self.conversation_id, self.user.id, self.connection_id)

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
