from unittest.mock import patch

from django.utils import timezone
from django.test import TestCase

from api.models import (
    ApiUser,
    CarMake,
    CarModel,
    Conversation,
    ConversationParticipant,
    Message,
    MessageStatus,
    MobileDevice,
    PartRequest,
    PartRequestStatus,
    UserCarModel,
)

from .push_notifications import (
    _send_fcm_message,
    send_chat_message_push_notifications,
    send_message_status_push_notifications,
    send_request_created_push_notifications,
    send_test_request_notification,
    send_typing_push_notifications,
)


class ChatPushNotificationTests(TestCase):
    def setUp(self):
        self.sender = ApiUser.objects.create_user(
            username="sender",
            email="sender@example.com",
            name="Sender User",
            role=ApiUser.ROLE_SUPPLIER,
            password="test1234",
        )
        self.preview_enabled_recipient = ApiUser.objects.create_user(
            username="preview-on",
            email="preview-on@example.com",
            name="Preview Enabled",
            role=ApiUser.ROLE_SUPPLIER,
            password="test1234",
        )
        self.preview_disabled_recipient = ApiUser.objects.create_user(
            username="preview-off",
            email="preview-off@example.com",
            name="Preview Disabled",
            role=ApiUser.ROLE_SUPPLIER,
            password="test1234",
            chat_message_preview_enabled=False,
        )
        self.push_disabled_recipient = ApiUser.objects.create_user(
            username="push-off",
            email="push-off@example.com",
            name="Push Disabled",
            role=ApiUser.ROLE_SUPPLIER,
            password="test1234",
            chat_push_enabled=False,
        )
        self.conversation = Conversation.objects.create(title="Push Tests")
        ConversationParticipant.objects.create(
            conversation=self.conversation,
            user=self.sender,
        )
        ConversationParticipant.objects.create(
            conversation=self.conversation,
            user=self.preview_enabled_recipient,
        )
        ConversationParticipant.objects.create(
            conversation=self.conversation,
            user=self.preview_disabled_recipient,
        )
        ConversationParticipant.objects.create(
            conversation=self.conversation,
            user=self.push_disabled_recipient,
        )

        self.sender_device = MobileDevice.objects.create(
            user=self.sender,
            device_id="sender-android-1",
            platform=MobileDevice.PLATFORM_ANDROID,
            push_token="sender-token",
            is_active=True,
        )
        self.preview_enabled_device = MobileDevice.objects.create(
            user=self.preview_enabled_recipient,
            device_id="preview-on-android-1",
            platform=MobileDevice.PLATFORM_ANDROID,
            push_token="preview-on-token",
            is_active=True,
        )
        self.preview_disabled_device = MobileDevice.objects.create(
            user=self.preview_disabled_recipient,
            device_id="preview-off-android-1",
            platform=MobileDevice.PLATFORM_ANDROID,
            push_token="preview-off-token",
            is_active=True,
        )
        MobileDevice.objects.create(
            user=self.preview_enabled_recipient,
            device_id="preview-on-web-1",
            platform=MobileDevice.PLATFORM_WEB,
            push_token="web-token",
            is_active=True,
        )
        MobileDevice.objects.create(
            user=self.preview_enabled_recipient,
            device_id="preview-on-android-inactive",
            platform=MobileDevice.PLATFORM_ANDROID,
            push_token="inactive-token",
            is_active=False,
        )
        MobileDevice.objects.create(
            user=self.push_disabled_recipient,
            device_id="push-off-android-1",
            platform=MobileDevice.PLATFORM_ANDROID,
            push_token="push-off-token",
            is_active=True,
        )

        self.message = Message.objects.create(
            conversation=self.conversation,
            sender=self.sender,
            message_type="text",
            text="Fresh front bumper available in stock",
            client_timestamp=timezone.now(),
        )
        self.make = CarMake.objects.create(name="Test Make", slug="test-make")
        self.matching_model = CarModel.objects.create(
            make=self.make,
            name="Model Match",
            slug="model-match",
            image_url="https://placehold.co/600x400/png?text=Test+Make+Model+Match",
        )
        self.other_model = CarModel.objects.create(
            make=self.make,
            name="Model Other",
            slug="model-other",
            image_url="https://placehold.co/600x400/png?text=Test+Make+Model+Other",
        )
        UserCarModel.objects.create(
            user=self.preview_enabled_recipient,
            car_model=self.matching_model,
        )
        UserCarModel.objects.create(
            user=self.preview_disabled_recipient,
            car_model=self.matching_model,
        )
        UserCarModel.objects.create(
            user=self.push_disabled_recipient,
            car_model=self.matching_model,
        )
        self.request_status, _ = PartRequestStatus.objects.get_or_create(
            code="awaiting",
            defaults={
                "label": "Awaiting",
                "is_terminal": False,
            },
        )
        self.part_request = PartRequest.objects.create(
            requester=self.sender,
            title="Front bumper for Toyota Camry",
            description="OEM preferred and ready for pickup",
            status=self.request_status,
            car_model=self.matching_model,
            city="Riyadh",
        )

    def test_message_push_filters_devices_and_respects_preview_preference(self):
        with patch(
            "chat.push_notifications._dispatch_notification",
            return_value=True,
        ) as dispatch_mock:
            send_count = send_chat_message_push_notifications(
                {
                    "id": self.message.id,
                    "conversation_id": self.conversation.id,
                    "sender": {
                        "id": self.sender.id,
                        "name": self.sender.name,
                    },
                    "message_type": "text",
                    "text": self.message.text,
                }
            )

        self.assertEqual(send_count, 2)
        self.assertEqual(dispatch_mock.call_count, 2)
        payloads_by_device = {
            call.kwargs["device"].device_id: call.kwargs for call in dispatch_mock.call_args_list
        }
        self.assertEqual(
            payloads_by_device["preview-on-android-1"]["body"],
            "Fresh front bumper available in stock",
        )
        self.assertEqual(
            payloads_by_device["preview-off-android-1"]["body"],
            "Sent you a new message.",
        )
        for kwargs in payloads_by_device.values():
            self.assertEqual(kwargs["data"]["event_type"], "chat_message")
            self.assertEqual(kwargs["data"]["conversation_id"], self.conversation.id)
            self.assertEqual(kwargs["data"]["message_id"], self.message.id)
            self.assertEqual(kwargs["data"]["actor_user_id"], self.sender.id)
            self.assertEqual(kwargs["data"]["app_name"], "MTA Auto Spare")
            self.assertEqual(kwargs["data"]["sender_name"], "Sender User")
            self.assertEqual(kwargs["data"]["chat_message_type"], "text")

    def test_typing_push_notifications_are_disabled(self):
        with patch(
            "chat.push_notifications._dispatch_notification",
            return_value=True,
        ) as dispatch_mock:
            send_count = send_typing_push_notifications(
                conversation_id=self.conversation.id,
                actor_user_id=self.sender.id,
                is_typing=False,
            )

        self.assertEqual(send_count, 0)
        dispatch_mock.assert_not_called()

    def test_status_push_notifications_are_disabled(self):
        with patch(
            "chat.push_notifications._dispatch_notification",
            return_value=True,
        ) as dispatch_mock:
            send_count = send_message_status_push_notifications(
                [
                    {
                        "conversation_id": self.conversation.id,
                        "message_id": self.message.id,
                        "user_id": self.sender.id,
                        "status": MessageStatus.STATUS_SENT,
                    },
                    {
                        "conversation_id": self.conversation.id,
                        "message_id": self.message.id,
                        "user_id": self.preview_enabled_recipient.id,
                        "status": MessageStatus.STATUS_DELIVERED,
                    },
                    {
                        "conversation_id": self.conversation.id,
                        "message_id": self.message.id,
                        "user_id": self.preview_disabled_recipient.id,
                        "status": MessageStatus.STATUS_SEEN,
                    },
                ]
            )

        self.assertEqual(send_count, 0)
        dispatch_mock.assert_not_called()

    def test_inactive_devices_are_skipped(self):
        self.preview_enabled_device.is_active = False
        self.preview_enabled_device.save(update_fields=["is_active"])
        self.preview_disabled_device.is_active = False
        self.preview_disabled_device.save(update_fields=["is_active"])

        with patch(
            "chat.push_notifications._dispatch_notification",
            return_value=True,
        ) as dispatch_mock:
            send_chat_message_push_notifications(
                {
                    "id": self.message.id,
                    "conversation_id": self.conversation.id,
                    "sender": {
                        "id": self.sender.id,
                        "name": self.sender.name,
                    },
                    "message_type": "text",
                    "text": self.message.text,
                }
            )

        dispatch_mock.assert_not_called()

    def test_request_created_push_filters_supplier_devices_and_respects_preview_preference(self):
        with patch(
            "chat.push_notifications._dispatch_notification",
            return_value={"status": "sent"},
        ) as dispatch_mock:
            send_count = send_request_created_push_notifications(self.part_request)

        self.assertEqual(send_count, 2)
        self.assertEqual(dispatch_mock.call_count, 2)
        payloads_by_device = {
            call.kwargs["device"].device_id: call.kwargs for call in dispatch_mock.call_args_list
        }
        self.assertEqual(
            payloads_by_device["preview-on-android-1"]["data"]["event_type"],
            "request_created",
        )
        self.assertEqual(
            payloads_by_device["preview-on-android-1"]["data"]["request_id"],
            self.part_request.id,
        )
        self.assertEqual(
            payloads_by_device["preview-on-android-1"]["body"],
            "OEM preferred and ready for pickup",
        )
        self.assertEqual(
            payloads_by_device["preview-off-android-1"]["body"],
            "A supplier posted a new request.",
        )
        for kwargs in payloads_by_device.values():
            self.assertEqual(kwargs["channel_id"], "chat_activity")
            self.assertEqual(kwargs["data"]["requester_id"], self.sender.id)

    def test_request_created_push_only_targets_suppliers_with_matching_car_model(self):
        UserCarModel.objects.filter(
            user=self.preview_disabled_recipient,
            car_model=self.matching_model,
        ).delete()
        UserCarModel.objects.create(
            user=self.preview_disabled_recipient,
            car_model=self.other_model,
        )

        with patch(
            "chat.push_notifications._dispatch_notification",
            return_value={"status": "sent"},
        ) as dispatch_mock:
            send_count = send_request_created_push_notifications(self.part_request)

        self.assertEqual(send_count, 1)
        self.assertEqual(dispatch_mock.call_count, 1)
        self.assertEqual(
            dispatch_mock.call_args.kwargs["device"].device_id,
            "preview-on-android-1",
        )

    def test_test_request_notification_returns_structured_result(self):
        with patch(
            "chat.push_notifications._dispatch_notification",
            return_value={
                "status": "sent",
                "firebase_message_id": "projects/demo/messages/123",
            },
        ) as dispatch_mock:
            result = send_test_request_notification(
                device=self.preview_enabled_device,
                request_id=self.part_request.id,
                requester_id=self.sender.id,
                request_title="Test seller request",
                request_description="Testing push delivery",
                seller_name="Sender User",
            )

        self.assertEqual(result["status"], "sent")
        self.assertEqual(
            result["firebase_message_id"],
            "projects/demo/messages/123",
        )
        dispatch_mock.assert_called_once()

    def test_fcm_android_message_is_sent_as_data_only_high_priority(self):
        with (
            patch(
                "chat.push_notifications.messaging.AndroidConfig",
                side_effect=lambda **kwargs: kwargs,
            ) as android_config_mock,
            patch(
                "chat.push_notifications.messaging.Message",
                side_effect=lambda **kwargs: kwargs,
            ) as message_mock,
            patch(
                "chat.push_notifications.messaging.send",
                return_value="message-id-1",
            ) as send_mock,
        ):
            result = _send_fcm_message(
                token="push-token",
                title="Seller User",
                body="Sent you a new message.",
                data={
                    "conversation_id": 7,
                    "event_type": "chat_message",
                    "chat_message_type": "text",
                    "title": "Seller User",
                    "body": "Sent you a new message.",
                },
                channel_id="chat_messages",
                app=object(),
            )

        self.assertEqual(result, "message-id-1")
        android_config_mock.assert_called_once()
        self.assertEqual(android_config_mock.call_args.kwargs["priority"], "high")
        message_mock.assert_called_once()
        self.assertNotIn("notification", message_mock.call_args.kwargs)
        self.assertEqual(
            message_mock.call_args.kwargs["data"],
            {
                "conversation_id": "7",
                "event_type": "chat_message",
                "chat_message_type": "text",
                "title": "Seller User",
                "body": "Sent you a new message.",
            },
        )
        send_mock.assert_called_once()

    def test_chat_push_payload_avoids_reserved_fcm_message_type_key(self):
        with patch(
            "chat.push_notifications._dispatch_notification",
            return_value={"status": "sent"},
        ) as dispatch_mock:
            send_chat_message_push_notifications(
                {
                    "id": self.message.id,
                    "conversation_id": self.conversation.id,
                    "sender": {
                        "id": self.sender.id,
                        "name": self.sender.name,
                    },
                    "message_type": "media",
                    "text": self.message.text,
                }
            )

        sent_payload = dispatch_mock.call_args.kwargs["data"]
        self.assertEqual(sent_payload["chat_message_type"], "media")
        self.assertNotIn("message_type", sent_payload)
