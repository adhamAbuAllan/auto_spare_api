import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from api.models import ConversationParticipant, Message


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get("user")
        if not self.user or self.user.is_anonymous:
            await self.close()
            return

        self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]
        self.group_name = f"chat_{self.conversation_id}"

        is_allowed = await self.is_participant()
        if not is_allowed:
            await self.close()
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return

        try:
            payload = json.loads(text_data)
        except json.JSONDecodeError:
            return

        event_type = payload.get("type")
        if event_type == "chat_message":
            await self.process_chat_message(payload)
        elif event_type == "typing":
            await self.process_typing()
        elif event_type == "seen":
            await self.process_seen()

    async def process_chat_message(self, payload):
        text = payload.get("text", "")
        message_type = payload.get("message_type", "text")
        client_timestamp_raw = payload.get("client_timestamp")
        client_timestamp = parse_datetime(client_timestamp_raw) if client_timestamp_raw else None
        if client_timestamp and timezone.is_naive(client_timestamp):
            client_timestamp = timezone.make_aware(client_timestamp)

        if not client_timestamp:
            return

        message = await self.create_message(text, message_type, client_timestamp)
        if not message:
            return

        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "handle.chat_message",
                "id": message.id,
                "conversation_id": int(self.conversation_id),
                "sender_id": self.user.id,
                "text": message.text,
                "client_timestamp": message.client_timestamp.isoformat(),
                "server_timestamp": message.server_timestamp.isoformat(),
            },
        )

    async def process_typing(self):
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "handle.typing",
                "user_id": self.user.id,
            },
        )

    async def process_seen(self):
        await self.update_seen()
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "handle.seen",
                "user_id": self.user.id,
            },
        )

    async def handle_chat_message(self, event):
        await self.send(text_data=json.dumps(event))

    async def handle_typing(self, event):
        await self.send(text_data=json.dumps(event))

    async def handle_seen(self, event):
        await self.send(text_data=json.dumps(event))

    @database_sync_to_async
    def is_participant(self):
        return ConversationParticipant.objects.filter(
            conversation_id=self.conversation_id, user=self.user
        ).exists()

    @database_sync_to_async
    def create_message(self, text, message_type, client_timestamp):
        return Message.objects.create(
            conversation_id=self.conversation_id,
            sender=self.user,
            text=text,
            message_type=message_type,
            client_timestamp=client_timestamp,
        )

    @database_sync_to_async
    def update_seen(self):
        ConversationParticipant.objects.filter(
            conversation_id=self.conversation_id, user=self.user
        ).update(last_read_at=timezone.now())
