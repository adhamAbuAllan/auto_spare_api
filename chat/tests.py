import base64
from unittest import mock

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
    PartRequest,
    PartRequestStatus,
)
from api.translation import TranslationValue
from config.asgi import application
from .runtime import reset_runtime_state


TEST_CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}


class FakeTranslationProvider:
    provider_name = "google"

    def translate_texts(self, *, texts, target_language, source_language=None):
        return [
            TranslationValue(
                translated_text=f"{target_language}:{text}",
                source_language=source_language or "en",
                provider=self.provider_name,
            )
            for text in texts
        ]


@override_settings(CHANNEL_LAYERS=TEST_CHANNEL_LAYERS, CHANNEL_LAYER_BACKEND="memory")
class ChatConsumerTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        reset_runtime_state()
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
        self.status, _ = PartRequestStatus.objects.get_or_create(
            code="awaiting",
            defaults={
                "label": "Awaiting",
                "is_terminal": False,
            },
        )
        self.product = PartRequest.objects.create(
            requester=self.buyer,
            title="Front bumper",
            description="OEM preferred",
            min_price="100.00",
            max_price="250.00",
            status=self.status,
            city="Riyadh",
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

    def tearDown(self):
        reset_runtime_state()
        super().tearDown()

    def _build_path(self, user=None, token=None, conversation_id=None, lang=None):
        if token is None and user is not None:
            token = str(AccessToken.for_user(user))
        conversation_id = conversation_id or self.conversation.id
        query_params = []
        if token is not None:
            query_params.append(f"token={token}")
        if lang:
            query_params.append(f"lang={lang}")
        suffix = f"?{'&'.join(query_params)}" if query_params else ""
        return f"/ws/chat/{conversation_id}/{suffix}"

    def _build_inbox_path(self, user=None, token=None, lang=None):
        if token is None and user is not None:
            token = str(AccessToken.for_user(user))
        query_params = []
        if token is not None:
            query_params.append(f"token={token}")
        if lang:
            query_params.append(f"lang={lang}")
        suffix = f"?{'&'.join(query_params)}" if query_params else ""
        return f"/ws/inbox/{suffix}"

    async def _connect(self, path):
        communicator = WebsocketCommunicator(application, path)
        connected, _ = await communicator.connect()
        initial_state = None
        if connected:
            initial_state = await communicator.receive_json_from()
        return communicator, connected, initial_state

    async def _connect_inbox(self, path):
        communicator = WebsocketCommunicator(application, path)
        connected, _ = await communicator.connect()
        return communicator, connected

    async def _receive_many(self, communicator, count, *, ignore_event_types=None):
        ignore_event_types = set(ignore_event_types or {"user.presence"})
        events = []
        while len(events) < count:
            event = await communicator.receive_json_from()
            if event.get("type") in ignore_event_types:
                continue
            events.append(event)
        return events

    async def _receive_next(self, communicator, *, ignore_event_types=None):
        return (
            await self._receive_many(
                communicator,
                1,
                ignore_event_types=ignore_event_types,
            )
        )[0]

    def test_participant_can_connect(self):
        async def scenario():
            communicator, connected, initial_state = await self._connect(
                self._build_path(user=self.buyer)
            )
            self.assertTrue(connected)
            self.assertEqual(initial_state["type"], "conversation.state")
            self.assertEqual(initial_state["conversation_id"], self.conversation.id)
            self.assertEqual(initial_state["connected_user_ids"], [self.buyer.id])
            self.assertEqual(initial_state["typing_user_ids"], [])
            self.assertEqual(initial_state["online_user_ids"], [self.buyer.id])
            await communicator.disconnect()

        async_to_sync(scenario)()

    def test_global_user_presence_broadcasts_across_shared_conversations(self):
        second_conversation = Conversation.objects.create(title="Supplier Direct")
        ConversationParticipant.objects.create(
            conversation=second_conversation,
            user=self.seller,
        )
        ConversationParticipant.objects.create(
            conversation=second_conversation,
            user=self.outsider,
        )

        async def scenario():
            buyer_communicator, buyer_connected, buyer_state = await self._connect(
                self._build_path(user=self.buyer)
            )
            self.assertTrue(buyer_connected)
            self.assertEqual(buyer_state["online_user_ids"], [self.buyer.id])
            self.assertIn(str(self.seller.id), buyer_state["presence_last_seen_at_by_user_id"])

            seller_communicator, seller_connected, _ = await self._connect(
                self._build_path(user=self.seller, conversation_id=second_conversation.id)
            )
            self.assertTrue(seller_connected)

            try:
                online_event = None
                for _ in range(3):
                    candidate = await buyer_communicator.receive_json_from()
                    if candidate.get("type") == "user.presence" and candidate.get("user_id") == self.seller.id:
                        online_event = candidate
                        break
            finally:
                await seller_communicator.disconnect()

            offline_event = await buyer_communicator.receive_json_from()
            await buyer_communicator.disconnect()
            return online_event, offline_event

        online_event, offline_event = async_to_sync(scenario)()
        self.assertIsNotNone(online_event)
        self.assertEqual(online_event["type"], "user.presence")
        self.assertEqual(online_event["user_id"], self.seller.id)
        self.assertTrue(online_event["is_online"])
        self.assertIsNotNone(online_event["last_seen_at"])

        self.assertEqual(offline_event["type"], "user.presence")
        self.assertEqual(offline_event["user_id"], self.seller.id)
        self.assertFalse(offline_event["is_online"])
        self.assertIsNotNone(offline_event["last_seen_at"])

    def test_non_participant_and_invalid_token_are_rejected(self):
        async def scenario():
            outsider_communicator, outsider_connected, _ = await self._connect(
                self._build_path(user=self.outsider)
            )
            self.assertFalse(outsider_connected)
            await outsider_communicator.wait()

            invalid_communicator, invalid_connected, _ = await self._connect(
                self._build_path(token="not-a-valid-token")
            )
            self.assertFalse(invalid_connected)
            await invalid_communicator.wait()

        async_to_sync(scenario)()

    def test_invalid_json_and_unsupported_event_return_error(self):
        async def scenario():
            communicator, connected, _ = await self._connect(self._build_path(user=self.buyer))
            self.assertTrue(connected)
            try:
                await communicator.send_to(text_data="not-json")
                invalid_json_event = await self._receive_next(communicator)
                await communicator.send_json_to({"type": "unknown_event"})
                unsupported_event = await self._receive_next(communicator)
                return invalid_json_event, unsupported_event
            finally:
                await communicator.disconnect()

        invalid_json_event, unsupported_event = async_to_sync(scenario)()
        self.assertEqual(invalid_json_event["type"], "error")
        self.assertIn("valid JSON", invalid_json_event["detail"])
        self.assertEqual(unsupported_event["type"], "error")
        self.assertIn("Unsupported event type", unsupported_event["detail"])

    def test_invalid_product_id_and_invalid_media_payload_return_error(self):
        async def scenario():
            communicator, connected, _ = await self._connect(self._build_path(user=self.buyer))
            self.assertTrue(connected)
            try:
                await communicator.send_json_to(
                    {
                        "type": "chat_message",
                        "message_type": "product",
                        "product_id": 999999,
                        "client_timestamp": "2026-03-23T10:00:00Z",
                    }
                )
                invalid_product_event = await self._receive_next(communicator)
                await communicator.send_json_to(
                    {
                        "type": "chat_message",
                        "message_type": "media",
                        "client_timestamp": "2026-03-23T10:03:00Z",
                        "media_files": [
                            {
                                "name": "bad.bin",
                                "content_type": "application/octet-stream",
                                "data_base64": "%%%not-base64%%%",
                            }
                        ],
                    }
                )
                invalid_media_event = await self._receive_next(communicator)
                return invalid_product_event, invalid_media_event
            finally:
                await communicator.disconnect()

        invalid_product_event, invalid_media_event = async_to_sync(scenario)()
        self.assertEqual(invalid_product_event["type"], "error")
        self.assertIn("product_id", invalid_product_event["detail"])
        self.assertEqual(invalid_media_event["type"], "error")
        self.assertIn("valid base64", invalid_media_event["detail"])

    def test_chat_message_broadcasts_normalized_events_and_persists(self):
        async def scenario():
            buyer_communicator, buyer_connected, buyer_state = await self._connect(
                self._build_path(user=self.buyer)
            )
            seller_communicator, seller_connected, seller_state = await self._connect(
                self._build_path(user=self.seller)
            )
            self.assertTrue(buyer_connected)
            self.assertTrue(seller_connected)
            self.assertEqual(buyer_state["type"], "conversation.state")
            self.assertEqual(seller_state["type"], "conversation.state")
            self.assertEqual(buyer_state["connected_user_ids"], [self.buyer.id])
            self.assertEqual(seller_state["connected_user_ids"], [self.buyer.id, self.seller.id])

            try:
                payload = {
                    "type": "chat_message",
                    "text": "Hello over websocket",
                    "message_type": "text",
                    "client_timestamp": "2026-03-23T10:00:00Z",
                }
                await buyer_communicator.send_json_to(payload)
                buyer_events = await self._receive_many(buyer_communicator, 3)
                seller_events = await self._receive_many(seller_communicator, 3)
                return buyer_events, seller_events
            finally:
                await buyer_communicator.disconnect()
                await seller_communicator.disconnect()

        buyer_events, seller_events = async_to_sync(scenario)()
        buyer_event = buyer_events[0]
        seller_event = seller_events[0]

        self.assertEqual(buyer_event["type"], "message.created")
        self.assertEqual(seller_event["type"], "message.created")
        self.assertEqual(buyer_event["message"]["text"], "Hello over websocket")
        self.assertEqual(seller_event["message"]["text"], "Hello over websocket")
        self.assertEqual(buyer_event["message"]["sender"]["id"], self.buyer.id)
        self.assertEqual(seller_event["message"]["sender"]["id"], self.buyer.id)
        self.assertEqual(buyer_event["message"]["conversation_id"], self.conversation.id)
        self.assertEqual(seller_event["message"]["conversation_id"], self.conversation.id)

        message = Message.objects.get(pk=buyer_event["message"]["id"])
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
            {event["type"] for event in buyer_statuses},
            {"message.status"},
        )
        self.assertEqual(
            {event["user_id"] for event in buyer_statuses},
            {self.buyer.id, self.seller.id},
        )
        self.assertEqual(
            {event["type"] for event in seller_statuses},
            {"message.status"},
        )

    def test_chat_message_broadcasts_inbox_events_to_participants(self):
        async def scenario():
            buyer_chat_communicator, buyer_connected, buyer_state = await self._connect(
                self._build_path(user=self.buyer)
            )
            buyer_inbox_communicator, buyer_inbox_connected = await self._connect_inbox(
                self._build_inbox_path(user=self.buyer)
            )
            seller_inbox_communicator, seller_inbox_connected = await self._connect_inbox(
                self._build_inbox_path(user=self.seller)
            )
            self.assertTrue(buyer_connected)
            self.assertTrue(buyer_inbox_connected)
            self.assertTrue(seller_inbox_connected)
            self.assertEqual(buyer_state["type"], "conversation.state")

            try:
                await buyer_chat_communicator.send_json_to(
                    {
                        "type": "chat_message",
                        "text": "Inbox should update immediately",
                        "message_type": "text",
                        "client_timestamp": "2026-04-05T18:00:00Z",
                    }
                )
                await self._receive_many(buyer_chat_communicator, 2)
                buyer_inbox_event = await buyer_inbox_communicator.receive_json_from()
                seller_inbox_event = await seller_inbox_communicator.receive_json_from()
                return buyer_inbox_event, seller_inbox_event
            finally:
                await buyer_chat_communicator.disconnect()
                await buyer_inbox_communicator.disconnect()
                await seller_inbox_communicator.disconnect()

        buyer_inbox_event, seller_inbox_event = async_to_sync(scenario)()

        self.assertEqual(buyer_inbox_event["type"], "inbox.message")
        self.assertEqual(seller_inbox_event["type"], "inbox.message")
        self.assertEqual(
            buyer_inbox_event["message"]["conversation_id"],
            self.conversation.id,
        )
        self.assertEqual(
            seller_inbox_event["message"]["conversation_id"],
            self.conversation.id,
        )
        self.assertEqual(
            buyer_inbox_event["message"]["text"],
            "Inbox should update immediately",
        )
        self.assertEqual(
            seller_inbox_event["message"]["text"],
            "Inbox should update immediately",
        )

    @override_settings(TRANSLATION_ENABLED=True)
    def test_chat_message_events_are_localized_per_connection_language(self):
        async def scenario():
            buyer_communicator, buyer_connected, _ = await self._connect(
                self._build_path(user=self.buyer, lang="ar")
            )
            seller_communicator, seller_connected, _ = await self._connect(
                self._build_path(user=self.seller, lang="he")
            )
            self.assertTrue(buyer_connected)
            self.assertTrue(seller_connected)

            try:
                with mock.patch(
                    "api.translation.get_translation_provider",
                    return_value=FakeTranslationProvider(),
                ):
                    await buyer_communicator.send_json_to(
                        {
                            "type": "chat_message",
                            "text": "Hello over websocket",
                            "message_type": "text",
                            "client_timestamp": "2026-03-23T10:00:00Z",
                        }
                    )
                    buyer_events = await self._receive_many(buyer_communicator, 3)
                    seller_events = await self._receive_many(seller_communicator, 3)
                    return buyer_events[0], seller_events[0]
            finally:
                await buyer_communicator.disconnect()
                await seller_communicator.disconnect()

        buyer_event, seller_event = async_to_sync(scenario)()

        self.assertEqual(buyer_event["type"], "message.created")
        self.assertEqual(seller_event["type"], "message.created")
        self.assertEqual(buyer_event["message"]["translated_text"], "ar:Hello over websocket")
        self.assertEqual(seller_event["message"]["translated_text"], "he:Hello over websocket")
        self.assertEqual(buyer_event["message"]["translation_target_language"], "ar")
        self.assertEqual(seller_event["message"]["translation_target_language"], "he")

    def test_product_reply_and_media_payloads_are_accepted(self):
        async def scenario():
            buyer_communicator, buyer_connected, buyer_state = await self._connect(
                self._build_path(user=self.buyer)
            )
            seller_communicator, seller_connected, seller_state = await self._connect(
                self._build_path(user=self.seller)
            )
            self.assertTrue(buyer_connected)
            self.assertTrue(seller_connected)
            self.assertEqual(buyer_state["type"], "conversation.state")
            self.assertEqual(seller_state["type"], "conversation.state")

            try:
                await seller_communicator.send_json_to(
                    {
                        "type": "chat_message",
                        "message_type": "text",
                        "text": "Original message",
                        "client_timestamp": "2026-03-23T09:55:00Z",
                    }
                )
                reply_source_events = await self._receive_many(seller_communicator, 3)
                await self._receive_many(buyer_communicator, 3)
                reply_source_id = reply_source_events[0]["message"]["id"]

                await buyer_communicator.send_json_to(
                    {
                        "type": "chat_message",
                        "message_type": "product",
                        "product_id": self.product.id,
                        "client_timestamp": "2026-03-23T10:01:00Z",
                    }
                )
                product_events = await self._receive_many(buyer_communicator, 3)
                await self._receive_many(seller_communicator, 3)

                await buyer_communicator.send_json_to(
                    {
                        "type": "chat_message",
                        "message_type": "text",
                        "text": "Replying now",
                        "reply_to_id": reply_source_id,
                        "client_timestamp": "2026-03-23T10:02:00Z",
                    }
                )
                reply_events = await self._receive_many(buyer_communicator, 3)
                await self._receive_many(seller_communicator, 3)

                await buyer_communicator.send_json_to(
                    {
                        "type": "chat_message",
                        "message_type": "media",
                        "client_timestamp": "2026-03-23T10:03:00Z",
                        "media_files": [
                            {
                                "name": "voice-note.m4a",
                                "content_type": "audio/mp4",
                                "data_base64": base64.b64encode(
                                    b"fake websocket voice payload"
                                ).decode(),
                            }
                        ],
                    }
                )
                media_events = await self._receive_many(buyer_communicator, 3)
                await self._receive_many(seller_communicator, 3)
                return product_events[0], reply_events[0], media_events[0]
            finally:
                await buyer_communicator.disconnect()
                await seller_communicator.disconnect()

        product_event, reply_event, media_event = async_to_sync(scenario)()

        self.assertEqual(product_event["type"], "message.created")
        self.assertEqual(product_event["message"]["message_type"], "product")
        self.assertEqual(product_event["message"]["product"]["id"], self.product.id)
        self.assertIsNone(product_event["message"]["reply_to"])

        self.assertEqual(reply_event["type"], "message.created")
        self.assertEqual(reply_event["message"]["reply_to"]["text"], "Original message")
        self.assertEqual(reply_event["message"]["reply_to"]["sender"]["id"], self.seller.id)

        self.assertEqual(media_event["type"], "message.created")
        self.assertEqual(media_event["message"]["message_type"], "media")
        self.assertEqual(len(media_event["message"]["media"]), 1)
        self.assertEqual(media_event["message"]["media"][0]["content_type"], "audio/mp4")

    def test_reply_payload_includes_nested_product(self):
        async def scenario():
            buyer_communicator, buyer_connected, buyer_state = await self._connect(
                self._build_path(user=self.buyer)
            )
            seller_communicator, seller_connected, seller_state = await self._connect(
                self._build_path(user=self.seller)
            )
            self.assertTrue(buyer_connected)
            self.assertTrue(seller_connected)
            self.assertEqual(buyer_state["type"], "conversation.state")
            self.assertEqual(seller_state["type"], "conversation.state")

            try:
                await buyer_communicator.send_json_to(
                    {
                        "type": "chat_message",
                        "message_type": "product",
                        "product_id": self.product.id,
                        "client_timestamp": "2026-03-23T10:20:00Z",
                    }
                )
                product_events = await self._receive_many(buyer_communicator, 3)
                await self._receive_many(seller_communicator, 3)
                product_message_id = product_events[0]["message"]["id"]

                await seller_communicator.send_json_to(
                    {
                        "type": "chat_message",
                        "message_type": "text",
                        "text": "I can supply this one.",
                        "reply_to_id": product_message_id,
                        "client_timestamp": "2026-03-23T10:21:00Z",
                    }
                )
                seller_events = await self._receive_many(seller_communicator, 3)
                await self._receive_many(buyer_communicator, 3)
                return seller_events[0]
            finally:
                await buyer_communicator.disconnect()
                await seller_communicator.disconnect()

        reply_event = async_to_sync(scenario)()
        self.assertEqual(reply_event["type"], "message.created")
        self.assertEqual(reply_event["message"]["reply_to"]["product"]["id"], self.product.id)
        self.assertEqual(
            reply_event["message"]["reply_to"]["product"]["title"], self.product.title
        )

    def test_typing_start_and_stop_broadcast(self):
        async def scenario():
            buyer_communicator, buyer_connected, buyer_state = await self._connect(
                self._build_path(user=self.buyer)
            )
            seller_communicator, seller_connected, seller_state = await self._connect(
                self._build_path(user=self.seller)
            )
            self.assertTrue(buyer_connected)
            self.assertTrue(seller_connected)
            self.assertEqual(buyer_state["type"], "conversation.state")
            self.assertEqual(seller_state["type"], "conversation.state")

            try:
                await seller_communicator.send_json_to({"type": "typing_start"})
                start_events = [
                    await self._receive_next(buyer_communicator),
                    await self._receive_next(seller_communicator),
                ]
                await seller_communicator.send_json_to({"type": "typing_stop"})
                stop_events = [
                    await self._receive_next(buyer_communicator),
                    await self._receive_next(seller_communicator),
                ]
                return start_events, stop_events
            finally:
                await buyer_communicator.disconnect()
                await seller_communicator.disconnect()

        start_events, stop_events = async_to_sync(scenario)()

        self.assertEqual(start_events[0]["type"], "conversation.typing")
        self.assertEqual(start_events[0]["user_id"], self.seller.id)
        self.assertTrue(start_events[0]["is_typing"])
        self.assertEqual(start_events[1]["type"], "conversation.typing")
        self.assertEqual(stop_events[0]["type"], "conversation.typing")
        self.assertFalse(stop_events[0]["is_typing"])
        self.assertEqual(stop_events[1]["type"], "conversation.typing")

    def test_websocket_chat_events_trigger_push_notifications(self):
        async def scenario():
            buyer_communicator, buyer_connected, buyer_state = await self._connect(
                self._build_path(user=self.buyer)
            )
            seller_communicator, seller_connected, seller_state = await self._connect(
                self._build_path(user=self.seller)
            )
            self.assertTrue(buyer_connected)
            self.assertTrue(seller_connected)
            self.assertEqual(buyer_state["type"], "conversation.state")
            self.assertEqual(seller_state["type"], "conversation.state")

            with (
                mock.patch("chat.consumers.send_chat_message_push_notifications") as message_push_mock,
            ):
                try:
                    await buyer_communicator.send_json_to(
                        {
                            "type": "chat_message",
                            "text": "Push notifications please",
                            "message_type": "text",
                            "client_timestamp": "2026-03-23T10:00:00Z",
                        }
                    )
                    await self._receive_many(buyer_communicator, 3)
                    await self._receive_many(seller_communicator, 3)

                    await seller_communicator.send_json_to({"type": "typing_start"})
                    await self._receive_next(buyer_communicator)
                    await self._receive_next(seller_communicator)

                    await seller_communicator.send_json_to({"type": "seen"})
                    await self._receive_next(buyer_communicator)
                    await self._receive_next(buyer_communicator)
                    await self._receive_next(seller_communicator)
                    await self._receive_next(seller_communicator)
                finally:
                    await buyer_communicator.disconnect()
                    await seller_communicator.disconnect()

                return (
                    message_push_mock.call_args_list,
                )

        (message_push_calls,) = async_to_sync(scenario)()

        self.assertEqual(len(message_push_calls), 1)
        self.assertEqual(
            message_push_calls[0].args[0]["text"],
            "Push notifications please",
        )

    def test_disconnect_clears_typing_state(self):
        async def scenario():
            buyer_communicator, buyer_connected, buyer_state = await self._connect(
                self._build_path(user=self.buyer)
            )
            seller_communicator, seller_connected, seller_state = await self._connect(
                self._build_path(user=self.seller)
            )
            self.assertTrue(buyer_connected)
            self.assertTrue(seller_connected)
            self.assertEqual(buyer_state["type"], "conversation.state")
            self.assertEqual(seller_state["type"], "conversation.state")

            try:
                await seller_communicator.send_json_to({"type": "typing_start"})
                await self._receive_next(buyer_communicator)
                await self._receive_next(seller_communicator)
                await seller_communicator.disconnect()
                return await self._receive_next(buyer_communicator)
            finally:
                await buyer_communicator.disconnect()

        disconnect_event = async_to_sync(scenario)()
        self.assertEqual(disconnect_event["type"], "conversation.typing")
        self.assertEqual(disconnect_event["user_id"], self.seller.id)
        self.assertFalse(disconnect_event["is_typing"])

    def test_one_disconnect_does_not_remove_multi_tab_presence(self):
        async def scenario():
            buyer_communicator, buyer_connected, buyer_state = await self._connect(
                self._build_path(user=self.buyer)
            )
            seller_communicator_one, seller_one_connected, seller_one_state = await self._connect(
                self._build_path(user=self.seller)
            )
            seller_communicator_two, seller_two_connected, seller_two_state = await self._connect(
                self._build_path(user=self.seller)
            )
            self.assertTrue(buyer_connected)
            self.assertTrue(seller_one_connected)
            self.assertTrue(seller_two_connected)
            self.assertEqual(buyer_state["type"], "conversation.state")
            self.assertEqual(seller_one_state["type"], "conversation.state")
            self.assertEqual(seller_two_state["type"], "conversation.state")

            try:
                await seller_communicator_one.disconnect()
                await buyer_communicator.send_json_to(
                    {
                        "type": "chat_message",
                        "text": "Still delivered after one tab closes",
                        "message_type": "text",
                        "client_timestamp": "2026-03-23T11:00:00Z",
                    }
                )
                buyer_events = await self._receive_many(buyer_communicator, 3)
                seller_events = await self._receive_many(seller_communicator_two, 3)
                return buyer_events, seller_events
            finally:
                await buyer_communicator.disconnect()
                await seller_communicator_two.disconnect()

        buyer_events, seller_events = async_to_sync(scenario)()
        self.assertEqual(buyer_events[0]["type"], "message.created")
        self.assertEqual(seller_events[0]["type"], "message.created")
        self.assertEqual(
            {event["status"] for event in buyer_events[1:]},
            {MessageStatus.STATUS_SENT, MessageStatus.STATUS_DELIVERED},
        )
        self.assertEqual(
            {event["status"] for event in seller_events[1:]},
            {MessageStatus.STATUS_SENT, MessageStatus.STATUS_DELIVERED},
        )

    def test_seen_updates_last_read_and_broadcasts_seen_receipts(self):
        message = Message.objects.create(
            conversation=self.conversation,
            sender=self.buyer,
            message_type="text",
            text="Mark this as seen",
            client_timestamp="2026-03-23T10:00:00Z",
        )
        MessageStatus.objects.create(
            message=message,
            user=self.buyer,
            status=MessageStatus.STATUS_SENT,
        )

        async def scenario():
            buyer_communicator, buyer_connected, buyer_state = await self._connect(
                self._build_path(user=self.buyer)
            )
            seller_communicator, seller_connected, seller_state = await self._connect(
                self._build_path(user=self.seller)
            )
            self.assertTrue(buyer_connected)
            self.assertTrue(seller_connected)
            self.assertEqual(buyer_state["type"], "conversation.state")
            self.assertEqual(seller_state["type"], "conversation.state")

            try:
                connect_events = [
                    await self._receive_next(buyer_communicator),
                    await self._receive_next(seller_communicator),
                ]
                await seller_communicator.send_json_to({"type": "seen"})
                seen_events = [
                    await self._receive_next(buyer_communicator),
                    await self._receive_next(buyer_communicator),
                    await self._receive_next(seller_communicator),
                    await self._receive_next(seller_communicator),
                ]
                return connect_events, seen_events
            finally:
                await buyer_communicator.disconnect()
                await seller_communicator.disconnect()

        connect_events, seen_events = async_to_sync(scenario)()
        self.assertEqual(connect_events[0]["type"], "message.status")
        self.assertEqual(connect_events[0]["status"], MessageStatus.STATUS_DELIVERED)
        self.assertEqual(connect_events[1]["type"], "message.status")

        self.assertEqual(seen_events[0]["type"], "conversation.seen")
        self.assertEqual(seen_events[0]["user_id"], self.seller.id)
        self.assertEqual(seen_events[1]["type"], "message.status")
        self.assertEqual(seen_events[2]["type"], "conversation.seen")
        self.assertEqual(seen_events[3]["type"], "message.status")
        self.assertEqual(seen_events[1]["message_id"], message.id)
        self.assertEqual(seen_events[3]["message_id"], message.id)
        self.assertEqual(seen_events[1]["user_id"], self.seller.id)
        self.assertEqual(seen_events[3]["user_id"], self.seller.id)
        self.assertEqual(seen_events[1]["status"], MessageStatus.STATUS_SEEN)
        self.assertEqual(seen_events[3]["status"], MessageStatus.STATUS_SEEN)

        participant = ConversationParticipant.objects.get(
            conversation=self.conversation,
            user=self.seller,
        )
        self.assertIsNotNone(participant.last_read_at)
        message_status = MessageStatus.objects.get(message=message, user=self.seller)
        self.assertEqual(message_status.status, MessageStatus.STATUS_SEEN)

    def test_connect_returns_current_typing_state_snapshot(self):
        async def scenario():
            third_communicator = None
            buyer_communicator, buyer_connected, buyer_state = await self._connect(
                self._build_path(user=self.buyer)
            )
            seller_communicator, seller_connected, seller_state = await self._connect(
                self._build_path(user=self.seller)
            )
            self.assertTrue(buyer_connected)
            self.assertTrue(seller_connected)

            try:
                await seller_communicator.send_json_to({"type": "typing_start"})
                await self._receive_next(buyer_communicator)
                await self._receive_next(seller_communicator)

                third_communicator, third_connected, third_state = await self._connect(
                    self._build_path(user=self.buyer)
                )
                self.assertTrue(third_connected)
                return buyer_state, seller_state, third_state
            finally:
                try:
                    if third_communicator is not None:
                        await third_communicator.disconnect()
                except Exception:
                    pass
                await buyer_communicator.disconnect()
                await seller_communicator.disconnect()

        buyer_state, seller_state, third_state = async_to_sync(scenario)()
        self.assertEqual(buyer_state["typing_user_ids"], [])
        self.assertEqual(seller_state["typing_user_ids"], [])
        self.assertEqual(third_state["connected_user_ids"], [self.buyer.id, self.seller.id])
        self.assertEqual(third_state["typing_user_ids"], [self.seller.id])

    def test_ping_returns_pong_and_keeps_presence_active(self):
        clock = {"now": 0.0}

        with mock.patch("chat.runtime.time.time", side_effect=lambda: clock["now"]):
            async def scenario():
                buyer_communicator, buyer_connected, buyer_state = await self._connect(
                    self._build_path(user=self.buyer)
                )
                self.assertTrue(buyer_connected)
                self.assertEqual(buyer_state["type"], "conversation.state")

                try:
                    clock["now"] = 40.0
                    await buyer_communicator.send_json_to({"type": "ping"})
                    pong = await self._receive_next(buyer_communicator)

                    clock["now"] = 74.0
                    seller_communicator, seller_connected, seller_state = await self._connect(
                        self._build_path(user=self.seller)
                    )
                    self.assertTrue(seller_connected)
                    await seller_communicator.disconnect()
                    return pong, seller_state
                finally:
                    await buyer_communicator.disconnect()

            pong, seller_state = async_to_sync(scenario)()

        self.assertEqual(pong["type"], "pong")
        self.assertEqual(pong["conversation_id"], self.conversation.id)
        self.assertEqual(seller_state["connected_user_ids"], [self.buyer.id, self.seller.id])

    def test_idle_connection_expires_without_ping(self):
        clock = {"now": 0.0}

        with mock.patch("chat.runtime.time.time", side_effect=lambda: clock["now"]):
            async def scenario():
                buyer_communicator, buyer_connected, buyer_state = await self._connect(
                    self._build_path(user=self.buyer)
                )
                self.assertTrue(buyer_connected)
                self.assertEqual(buyer_state["type"], "conversation.state")

                try:
                    clock["now"] = 76.0
                    seller_communicator, seller_connected, seller_state = await self._connect(
                        self._build_path(user=self.seller)
                    )
                    self.assertTrue(seller_connected)
                    await seller_communicator.disconnect()
                    return seller_state
                finally:
                    await buyer_communicator.disconnect()

            seller_state = async_to_sync(scenario)()

        self.assertEqual(seller_state["connected_user_ids"], [self.seller.id])

    def test_typing_expires_without_disconnect(self):
        clock = {"now": 0.0}

        with mock.patch("chat.runtime.time.time", side_effect=lambda: clock["now"]):
            async def scenario():
                third_communicator = None
                buyer_communicator, buyer_connected, buyer_state = await self._connect(
                    self._build_path(user=self.buyer)
                )
                seller_communicator, seller_connected, seller_state = await self._connect(
                    self._build_path(user=self.seller)
                )
                self.assertTrue(buyer_connected)
                self.assertTrue(seller_connected)
                self.assertEqual(buyer_state["type"], "conversation.state")
                self.assertEqual(seller_state["type"], "conversation.state")

                try:
                    await seller_communicator.send_json_to({"type": "typing_start"})
                    await self._receive_next(buyer_communicator)
                    await self._receive_next(seller_communicator)

                    clock["now"] = 9.0
                    third_communicator, third_connected, third_state = await self._connect(
                        self._build_path(user=self.buyer)
                    )
                    self.assertTrue(third_connected)
                    return third_state
                finally:
                    try:
                        if third_communicator is not None:
                            await third_communicator.disconnect()
                    except Exception:
                        pass
                    await buyer_communicator.disconnect()
                    await seller_communicator.disconnect()

            third_state = async_to_sync(scenario)()

        self.assertEqual(third_state["typing_user_ids"], [])
        self.assertEqual(third_state["connected_user_ids"], [self.buyer.id, self.seller.id])

    def test_message_delivery_skips_expired_presence(self):
        clock = {"now": 0.0}

        with mock.patch("chat.runtime.time.time", side_effect=lambda: clock["now"]):
            async def scenario():
                buyer_communicator, buyer_connected, buyer_state = await self._connect(
                    self._build_path(user=self.buyer)
                )
                seller_communicator, seller_connected, seller_state = await self._connect(
                    self._build_path(user=self.seller)
                )
                self.assertTrue(buyer_connected)
                self.assertTrue(seller_connected)
                self.assertEqual(buyer_state["type"], "conversation.state")
                self.assertEqual(seller_state["type"], "conversation.state")

                try:
                    clock["now"] = 76.0
                    await buyer_communicator.send_json_to(
                        {
                            "type": "chat_message",
                            "text": "Only sent because seller lease expired",
                            "message_type": "text",
                            "client_timestamp": "2026-03-23T12:00:00Z",
                        }
                    )
                    buyer_events = await self._receive_many(buyer_communicator, 2)
                    seller_events = await self._receive_many(seller_communicator, 2)
                    return buyer_events, seller_events
                finally:
                    await buyer_communicator.disconnect()
                    await seller_communicator.disconnect()

            buyer_events, seller_events = async_to_sync(scenario)()

        self.assertEqual(buyer_events[0]["type"], "message.created")
        self.assertEqual(seller_events[0]["type"], "message.created")
        self.assertEqual(buyer_events[1]["type"], "message.status")
        self.assertEqual(seller_events[1]["type"], "message.status")
        self.assertEqual(buyer_events[1]["status"], MessageStatus.STATUS_SENT)
        self.assertEqual(seller_events[1]["status"], MessageStatus.STATUS_SENT)
        self.assertEqual(
            Message.objects.latest("id").statuses.values_list("user_id", "status").get(),
            (self.buyer.id, MessageStatus.STATUS_SENT),
        )
