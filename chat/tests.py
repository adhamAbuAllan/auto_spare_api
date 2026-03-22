from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from api.models import (
    ApiUser,
    Conversation,
    ConversationParticipant,
    Message,
    MessageStatus,
)
from config.asgi import application


TEST_CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}


@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
class ChatConsumerTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.buyer = ApiUser.objects.create_user(
            username="buyer",
            email="buyer@example.com",
            name="Buyer",
            password="test1234",
        )
        self.seller = ApiUser.objects.create_user(
            username="seller",
            email="seller@example.com",
            name="Seller",
            role="supplier",
            password="test1234",
        )
        self.outsider = ApiUser.objects.create_user(
            username="outsider",
            email="outsider@example.com",
            name="Outsider",
            password="test1234",
        )
        self.conversation = Conversation.objects.create(title="Socket Test")
        ConversationParticipant.objects.create(
            conversation=self.conversation,
            user=self.buyer,
        )
        ConversationParticipant.objects.create(
            conversation=self.conversation,
            user=self.seller,
        )

    def _build_path(self, user=None, token=None, conversation_id=None):
        if token is None and user is not None:
            token = str(AccessToken.for_user(user))
        conversation_id = conversation_id or self.conversation.id
        suffix = f"?token={token}" if token is not None else ""
        return f"/ws/chat/{conversation_id}/{suffix}"

    async def _connect(self, path):
        communicator = WebsocketCommunicator(application, path)
        connected, _ = await communicator.connect()
        return communicator, connected

    def test_participant_can_connect(self):
        async def scenario():
            communicator, connected = await self._connect(self._build_path(user=self.buyer))
            self.assertTrue(connected)
            await communicator.disconnect()

        async_to_sync(scenario)()

    def test_non_participant_and_invalid_token_are_rejected(self):
        async def scenario():
            outsider_communicator, outsider_connected = await self._connect(
                self._build_path(user=self.outsider)
            )
            self.assertFalse(outsider_connected)
            await outsider_communicator.wait()

            invalid_communicator, invalid_connected = await self._connect(
                self._build_path(token="not-a-valid-token")
            )
            self.assertFalse(invalid_connected)
            await invalid_communicator.wait()

        async_to_sync(scenario)()

    def test_chat_message_broadcasts_and_persists(self):
        async def scenario():
            buyer_communicator, buyer_connected = await self._connect(
                self._build_path(user=self.buyer)
            )
            seller_communicator, seller_connected = await self._connect(
                self._build_path(user=self.seller)
            )
            self.assertTrue(buyer_connected)
            self.assertTrue(seller_connected)

            try:
                payload = {
                    "type": "chat_message",
                    "text": "Hello over websocket",
                    "message_type": "text",
                    "client_timestamp": "2026-03-23T10:00:00Z",
                }
                await buyer_communicator.send_json_to(payload)
                buyer_events = [
                    await buyer_communicator.receive_json_from(),
                    await buyer_communicator.receive_json_from(),
                    await buyer_communicator.receive_json_from(),
                ]
                seller_events = [
                    await seller_communicator.receive_json_from(),
                    await seller_communicator.receive_json_from(),
                    await seller_communicator.receive_json_from(),
                ]
                return buyer_events, seller_events
            finally:
                await buyer_communicator.disconnect()
                await seller_communicator.disconnect()

        buyer_events, seller_events = async_to_sync(scenario)()
        buyer_event = buyer_events[0]
        seller_event = seller_events[0]

        self.assertEqual(buyer_event["type"], "handle.chat_message")
        self.assertEqual(seller_event["type"], "handle.chat_message")
        self.assertEqual(buyer_event["text"], "Hello over websocket")
        self.assertEqual(seller_event["text"], "Hello over websocket")
        self.assertEqual(buyer_event["sender_id"], self.buyer.id)
        self.assertEqual(seller_event["sender_id"], self.buyer.id)
        self.assertEqual(buyer_event["conversation_id"], self.conversation.id)
        self.assertEqual(seller_event["conversation_id"], self.conversation.id)

        message = Message.objects.get(pk=buyer_event["id"])
        self.assertEqual(message.sender_id, self.buyer.id)
        self.assertEqual(message.conversation_id, self.conversation.id)
        self.assertEqual(message.text, "Hello over websocket")
        self.assertEqual(message.statuses.count(), 2)
        self.assertEqual(
            set(message.statuses.values_list("user_id", "status")),
            {
                (self.buyer.id, MessageStatus.STATUS_SENT),
                (self.seller.id, MessageStatus.STATUS_DELIVERED),
            },
        )

        buyer_statuses = buyer_events[1:]
        seller_statuses = seller_events[1:]
        self.assertEqual(
            {event["status"] for event in buyer_statuses},
            {MessageStatus.STATUS_SENT, MessageStatus.STATUS_DELIVERED},
        )
        self.assertEqual(
            {event["status"] for event in seller_statuses},
            {MessageStatus.STATUS_SENT, MessageStatus.STATUS_DELIVERED},
        )
        self.assertEqual(
            {event["user_id"] for event in buyer_statuses},
            {self.buyer.id, self.seller.id},
        )
        self.assertEqual(
            {event["type"] for event in buyer_statuses},
            {"handle.message_status"},
        )

    def test_seen_updates_last_read_and_broadcasts_read_receipts(self):
        message = Message.objects.create(
            conversation=self.conversation,
            sender=self.buyer,
            message_type="text",
            text="Mark this as read",
            client_timestamp="2026-03-23T10:00:00Z",
        )
        MessageStatus.objects.create(
            message=message,
            user=self.buyer,
            status=MessageStatus.STATUS_SENT,
        )
        MessageStatus.objects.create(
            message=message,
            user=self.seller,
            status=MessageStatus.STATUS_DELIVERED,
        )

        async def scenario():
            buyer_communicator, buyer_connected = await self._connect(
                self._build_path(user=self.buyer)
            )
            seller_communicator, seller_connected = await self._connect(
                self._build_path(user=self.seller)
            )
            self.assertTrue(buyer_connected)
            self.assertTrue(seller_connected)

            try:
                await seller_communicator.send_json_to({"type": "seen"})
                buyer_events = [
                    await buyer_communicator.receive_json_from(),
                    await buyer_communicator.receive_json_from(),
                ]
                seller_events = [
                    await seller_communicator.receive_json_from(),
                    await seller_communicator.receive_json_from(),
                ]
                return buyer_events, seller_events
            finally:
                await buyer_communicator.disconnect()
                await seller_communicator.disconnect()

        buyer_events, seller_events = async_to_sync(scenario)()

        self.assertEqual(buyer_events[0], {"type": "handle.seen", "user_id": self.seller.id})
        self.assertEqual(seller_events[0], {"type": "handle.seen", "user_id": self.seller.id})
        self.assertEqual(buyer_events[1]["type"], "handle.message_status")
        self.assertEqual(seller_events[1]["type"], "handle.message_status")
        self.assertEqual(buyer_events[1]["message_id"], message.id)
        self.assertEqual(seller_events[1]["message_id"], message.id)
        self.assertEqual(buyer_events[1]["user_id"], self.seller.id)
        self.assertEqual(seller_events[1]["user_id"], self.seller.id)
        self.assertEqual(buyer_events[1]["status"], MessageStatus.STATUS_READ)
        self.assertEqual(seller_events[1]["status"], MessageStatus.STATUS_READ)

        participant = ConversationParticipant.objects.get(
            conversation=self.conversation,
            user=self.seller,
        )
        self.assertIsNotNone(participant.last_read_at)
        message_status = MessageStatus.objects.get(message=message, user=self.seller)
        self.assertEqual(message_status.status, MessageStatus.STATUS_READ)
