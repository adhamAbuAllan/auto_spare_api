from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import (
    ApiUser,
    Conversation,
    ConversationParticipant,
    Message,
    MessageStatus,
    MobileDevice,
    PartRequest,
    PartRequestStatus,
    SparePart,
)


class ApiTestCase(APITestCase):
    def create_user(self, **overrides):
        suffix = ApiUser.objects.count() + 1
        payload = {
            "username": f"user{suffix}",
            "email": f"user{suffix}@example.com",
            "name": f"User {suffix}",
            "phone": f"+15550000{suffix:03d}",
            "city": "Riyadh",
            "role": "user",
            "password": "test1234",
        }
        payload.update(overrides)
        password = payload.pop("password")
        return ApiUser.objects.create_user(password=password, **payload)


class UsersApiTests(ApiTestCase):
    def test_create_user(self):
        response = self.client.post(
            "/api/users/",
            data={
                "email": "alice@example.com",
                "username": "alice",
                "name": "Alice",
                "phone": "+966555000111",
                "city": "Riyadh",
                "role": "user",
                "rating": "4.50",
                "password": "test1234",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(ApiUser.objects.count(), 1)
        self.assertEqual(ApiUser.objects.first().name, "Alice")
        self.assertEqual(ApiUser.objects.first().role, "user")

    def test_list_users_returns_paginated_results_for_authenticated_user(self):
        viewer = self.create_user(username="viewer", email="viewer@example.com")
        self.create_user(username="alice", email="alice@example.com", name="Alice")
        self.create_user(
            username="bob",
            email="bob@example.com",
            name="Bob",
            city="Jeddah",
            role="supplier",
        )

        self.client.force_authenticate(user=viewer)
        response = self.client.get("/api/users/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 3)
        self.assertEqual(len(payload["results"]), 3)
        self.assertEqual(payload["results"][1]["name"], "Alice")
        self.assertEqual(payload["results"][2]["role"], "supplier")


class SparePartApiTests(ApiTestCase):
    def setUp(self):
        self.user = self.create_user()
        self.client.force_authenticate(user=self.user)

    def test_create_spare_part(self):
        response = self.client.post(
            "/api/spare-parts/",
            data={
                "name": "Brake Pad",
                "description": "Front wheel brake pad",
                "price": "149.99",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(SparePart.objects.count(), 1)
        self.assertEqual(SparePart.objects.first().name, "Brake Pad")

    def test_list_spare_parts(self):
        SparePart.objects.create(name="Oil Filter", description="", price="45.00")
        SparePart.objects.create(name="Air Filter", description="", price="65.50")

        response = self.client.get("/api/spare-parts/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["results"][0]["name"], "Oil Filter")
        self.assertEqual(payload["results"][1]["name"], "Air Filter")


class PartRequestApiTests(ApiTestCase):
    def setUp(self):
        self.user = self.create_user()
        self.client.force_authenticate(user=self.user)
        self.status = PartRequestStatus.objects.create(
            code="open", label="Open", is_terminal=False
        )

    def test_create_and_list_part_request(self):
        create_response = self.client.post(
            "/api/part-requests/",
            data={
                "requester": self.user.id,
                "title": "Need bumper",
                "description": "Original preferred",
                "min_price": "100.00",
                "max_price": "250.00",
                "status": self.status.id,
                "city": "Riyadh",
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(PartRequest.objects.count(), 1)

        list_response = self.client.get("/api/part-requests/")
        payload = list_response.json()

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["min_price"], "100.00")
        self.assertEqual(payload["results"][0]["max_price"], "250.00")

    def test_upload_part_image(self):
        part_request = PartRequest.objects.create(
            requester=self.user,
            title="Need bumper",
            description="Original preferred",
            min_price="100.00",
            max_price="250.00",
            status=self.status,
            city="Riyadh",
        )
        image_bytes = (Path(__file__).resolve().parent.parent / "fixtures" / "sample_part.jpg").read_bytes()
        image = SimpleUploadedFile(
            "sample_part.jpg",
            image_bytes,
            content_type="image/jpeg",
        )

        response = self.client.post(
            "/api/part-images/",
            data={"part_request": part_request.id, "image": image},
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["part_request"], part_request.id)


class ConversationApiTests(ApiTestCase):
    def setUp(self):
        self.buyer = self.create_user(username="buyer", email="buyer@example.com")
        self.seller = self.create_user(
            username="seller",
            email="seller@example.com",
            role="supplier",
        )
        self.client.force_authenticate(user=self.buyer)
        create_response = self.client.post(
            "/api/conversations/",
            data={"title": "Bumper Request Chat"},
            format="json",
        )
        self.conversation = Conversation.objects.get(pk=create_response.json()["id"])
        ConversationParticipant.objects.create(
            conversation=self.conversation,
            user=self.seller,
        )
        self.buyer_message = Message.objects.create(
            conversation=self.conversation,
            sender=self.buyer,
            message_type="text",
            text="Hi, I need the front bumper.",
            client_timestamp=timezone.now(),
        )
        self.seller_message = Message.objects.create(
            conversation=self.conversation,
            sender=self.seller,
            message_type="text",
            text="I can help with that.",
            client_timestamp=timezone.now(),
        )

    def test_list_conversations_returns_last_message(self):
        response = self.client.get("/api/conversations/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["last_message"]["id"], self.seller_message.id)
        self.assertEqual(payload["results"][0]["unread_count"], 1)

    def test_list_messages_returns_paginated_results(self):
        response = self.client.get(
            f"/api/messages/?conversation_id={self.conversation.id}"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["results"]), 2)
        self.assertEqual(payload["results"][0]["text"], "Hi, I need the front bumper.")
        self.assertEqual(payload["results"][1]["text"], "I can help with that.")
        self.assertEqual(payload["results"][0]["conversation_id"], self.conversation.id)
        self.assertEqual(payload["results"][0]["statuses"], [])

    def test_http_message_create_initializes_receipt_statuses(self):
        response = self.client.post(
            "/api/messages/",
            data={
                "conversation": self.conversation.id,
                "message_type": "text",
                "text": "Created through HTTP",
                "client_timestamp": "2026-03-23T10:05:00Z",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["conversation_id"], self.conversation.id)
        self.assertEqual(payload["sender"]["id"], self.buyer.id)
        self.assertEqual(payload["message_type"], "text")
        self.assertEqual(len(payload["statuses"]), 1)

        message = Message.objects.get(pk=payload["id"])
        self.assertEqual(
            set(message.statuses.values_list("user_id", "status")),
            {
                (self.buyer.id, MessageStatus.STATUS_SENT),
            },
        )

    def test_http_product_message_returns_product_payload(self):
        product_status = PartRequestStatus.objects.create(
            code="available",
            label="Available",
            is_terminal=False,
        )
        product = PartRequest.objects.create(
            requester=self.seller,
            title="OEM grille",
            description="Clean condition",
            min_price="200.00",
            max_price="350.00",
            status=product_status,
            city="Riyadh",
        )

        response = self.client.post(
            "/api/messages/",
            data={
                "conversation": self.conversation.id,
                "message_type": "product",
                "product": product.id,
                "client_timestamp": "2026-03-23T10:10:00Z",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["product"]["id"], product.id)
        self.assertEqual(response.json()["product"]["title"], "OEM grille")

    def test_http_reply_message_rejects_cross_conversation_reply_to(self):
        other_conversation = Conversation.objects.create(title="Other")
        ConversationParticipant.objects.create(conversation=other_conversation, user=self.buyer)
        other_message = Message.objects.create(
            conversation=other_conversation,
            sender=self.buyer,
            message_type="text",
            text="Elsewhere",
            client_timestamp=timezone.now(),
        )

        response = self.client.post(
            "/api/messages/",
            data={
                "conversation": self.conversation.id,
                "message_type": "text",
                "text": "Wrong reply",
                "reply_to": other_message.id,
                "client_timestamp": "2026-03-23T10:11:00Z",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("reply_to", response.json())

    def test_http_media_message_requires_file_and_returns_media(self):
        missing_file_response = self.client.post(
            "/api/messages/",
            data={
                "conversation": self.conversation.id,
                "message_type": "media",
                "client_timestamp": "2026-03-23T10:12:00Z",
            },
            format="multipart",
        )
        self.assertEqual(missing_file_response.status_code, 400)
        self.assertIn("files", missing_file_response.json())

        upload = SimpleUploadedFile(
            "chat-note.txt",
            b"socket fallback media",
            content_type="text/plain",
        )
        response = self.client.post(
            "/api/messages/",
            data={
                "conversation": self.conversation.id,
                "message_type": "media",
                "client_timestamp": "2026-03-23T10:13:00Z",
                "files": [upload],
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["message_type"], "media")
        self.assertEqual(len(response.json()["media"]), 1)
        self.assertEqual(response.json()["media"][0]["content_type"], "text/plain")

    def test_conversation_participants_are_scoped_to_request_user(self):
        outsider = self.create_user(username="outsider", email="outsider@example.com")
        other_conversation = Conversation.objects.create(title="Private")
        ConversationParticipant.objects.create(conversation=other_conversation, user=outsider)

        response = self.client.get("/api/conversation-participants/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 2)
        self.assertTrue(
            all(item["conversation"] == self.conversation.id for item in payload["results"])
        )

    def test_conversation_participant_create_requires_membership(self):
        outsider = self.create_user(username="outsider2", email="outsider2@example.com")
        intruder = self.create_user(username="intruder", email="intruder@example.com")
        self.client.force_authenticate(user=intruder)

        response = self.client.post(
            "/api/conversation-participants/",
            data={
                "conversation": self.conversation.id,
                "user": outsider.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_message_statuses_are_scoped_to_user_conversations(self):
        MessageStatus.objects.create(
            message=self.seller_message,
            user=self.buyer,
            status=MessageStatus.STATUS_DELIVERED,
        )
        outsider = self.create_user(username="outsider3", email="outsider3@example.com")
        private_conversation = Conversation.objects.create(title="Private")
        ConversationParticipant.objects.create(conversation=private_conversation, user=outsider)
        private_message = Message.objects.create(
            conversation=private_conversation,
            sender=outsider,
            message_type="text",
            text="Private message",
            client_timestamp=timezone.now(),
        )
        MessageStatus.objects.create(
            message=private_message,
            user=outsider,
            status=MessageStatus.STATUS_SENT,
        )

        response = self.client.get("/api/message-statuses/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["message"], self.seller_message.id)

    def test_message_status_create_requires_membership(self):
        outsider = self.create_user(username="outsider4", email="outsider4@example.com")
        private_conversation = Conversation.objects.create(title="Private")
        ConversationParticipant.objects.create(conversation=private_conversation, user=outsider)
        private_message = Message.objects.create(
            conversation=private_conversation,
            sender=outsider,
            message_type="text",
            text="Private message",
            client_timestamp=timezone.now(),
        )

        response = self.client.post(
            "/api/message-statuses/",
            data={
                "message": private_message.id,
                "status": MessageStatus.STATUS_DELIVERED,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)


class MobileApiTests(ApiTestCase):
    def setUp(self):
        self.user = self.create_user(username="mobile-user", email="mobile@example.com")
        self.client.force_authenticate(user=self.user)

    def test_patch_me_updates_chat_notification_preferences(self):
        response = self.client.patch(
            "/api/me/",
            data={
                "chat_push_enabled": False,
                "chat_message_preview_enabled": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.chat_push_enabled)
        self.assertFalse(self.user.chat_message_preview_enabled)

    def test_mobile_device_registration_upserts_by_device_id(self):
        create_response = self.client.post(
            "/api/mobile-devices/",
            data={
                "device_id": "android-001",
                "platform": "android",
                "push_token": "token-v1",
                "device_name": "Pixel 9",
                "app_version": "1.0.0",
                "is_active": True,
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(MobileDevice.objects.count(), 1)
        self.assertEqual(MobileDevice.objects.first().push_token, "token-v1")

        update_response = self.client.post(
            "/api/mobile-devices/",
            data={
                "device_id": "android-001",
                "platform": "android",
                "push_token": "token-v2",
                "device_name": "Pixel 9",
                "app_version": "1.0.1",
                "is_active": True,
            },
            format="json",
        )

        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(MobileDevice.objects.count(), 1)
        device = MobileDevice.objects.get()
        self.assertEqual(device.push_token, "token-v2")
        self.assertEqual(device.app_version, "1.0.1")

    def test_list_mobile_devices_returns_only_current_user_devices(self):
        MobileDevice.objects.create(
            user=self.user,
            device_id="ios-001",
            platform="ios",
            push_token="token-ios",
            device_name="iPhone",
        )
        other_user = self.create_user(username="other-mobile", email="other-mobile@example.com")
        MobileDevice.objects.create(
            user=other_user,
            device_id="android-999",
            platform="android",
            push_token="token-other",
            device_name="Other phone",
        )

        response = self.client.get("/api/mobile-devices/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["device_id"], "ios-001")
