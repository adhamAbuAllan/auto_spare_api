from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from chat.runtime import add_globally_connected_user, reset_runtime_state

from .models import (
    ApiUser,
    CarMake,
    CarModel,
    Conversation,
    ConversationParticipant,
    Message,
    MessageStatus,
    MobileDevice,
    PartImage,
    PartRequest,
    PartRequestAccess,
    PartRequestStatus,
    SparePart,
    TranslationCache,
    UserCarModel,
)
from .translation import TranslationValue


class FakeTranslationProvider:
    provider_name = "google"

    def __init__(self):
        self.calls = []

    def translate_texts(self, *, texts, target_language, source_language=None):
        self.calls.append(
            {
                "texts": list(texts),
                "target_language": target_language,
                "source_language": source_language,
            }
        )
        return [
            TranslationValue(
                translated_text=f"{target_language}:{text}",
                source_language=source_language or "en",
                provider=self.provider_name,
            )
            for text in texts
        ]


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

    def create_car_model(self, *, make_name="Toyota", model_name="Camry", **overrides):
        make_defaults = {
            "slug": make_name.lower().replace(" ", "-"),
        }
        make, _ = CarMake.objects.get_or_create(
            name=make_name,
            defaults=make_defaults,
        )
        payload = {
            "make": make,
            "name": model_name,
            "slug": model_name.lower().replace(" ", "-"),
            "image_url": f"https://placehold.co/600x400/png?text={make_name}+{model_name}",
            "is_active": True,
        }
        payload.update(overrides)
        car_model, _ = CarModel.objects.get_or_create(
            make=make,
            slug=payload["slug"],
            defaults={
                "name": payload["name"],
                "image_url": payload["image_url"],
                "is_active": payload["is_active"],
            },
        )
        return car_model


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

    def test_create_user_creates_supported_car_model_links(self):
        camry = self.create_car_model(make_name="Toyota", model_name="Camry")
        elantra = self.create_car_model(make_name="Hyundai", model_name="Elantra")

        response = self.client.post(
            "/api/users/",
            data={
                "email": "garage@example.com",
                "username": "garage-owner",
                "name": "Garage Owner",
                "role": "supplier",
                "password": "test1234",
                "supported_car_model_ids": [camry.id, elantra.id, camry.id],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        created_user = ApiUser.objects.get(username="garage-owner")
        self.assertEqual(
            set(
                UserCarModel.objects.filter(user=created_user).values_list(
                    "car_model_id", flat=True
                )
            ),
            {camry.id, elantra.id},
        )

    def test_create_user_returns_clear_message_when_email_exists(self):
        self.create_user(username="alice", email="alice@example.com")

        response = self.client.post(
            "/api/users/",
            data={
                "email": "alice@example.com",
                "username": "alice_2",
                "name": "Alice 2",
                "phone": "+966555000112",
                "city": "Riyadh",
                "role": "user",
                "password": "test1234",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["message"], "A user with this email already exists.")
        self.assertEqual(payload["status_code"], 400)
        self.assertIn("email", payload)

    def test_create_user_returns_clear_message_when_username_exists(self):
        self.create_user(username="alice", email="alice@example.com")

        response = self.client.post(
            "/api/users/",
            data={
                "email": "alice-2@example.com",
                "username": "alice",
                "name": "Alice 2",
                "phone": "+966555000113",
                "city": "Riyadh",
                "role": "user",
                "password": "test1234",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["message"], "A user with this username already exists.")
        self.assertEqual(payload["status_code"], 400)
        self.assertIn("username", payload)

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

    def test_retrieve_supplier_profile_includes_email_phone_and_supported_car_models(self):
        viewer = self.create_user(username="viewer", email="viewer@example.com")
        audi_a4 = self.create_car_model(make_name="Audi", model_name="A4")
        bmw_x5 = self.create_car_model(make_name="BMW", model_name="X5")
        supplier = self.create_user(
            username="supplier",
            email="supplier@example.com",
            name="Supplier Garage",
            phone="+201001112233",
            city="Cairo",
            role="supplier",
        )
        UserCarModel.objects.create(user=supplier, car_model=audi_a4)
        UserCarModel.objects.create(user=supplier, car_model=bmw_x5)

        self.client.force_authenticate(user=viewer)
        response = self.client.get(f"/api/users/{supplier.id}/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["email"], supplier.email)
        self.assertEqual(payload["phone"], supplier.phone)
        self.assertEqual(payload["role"], "supplier")
        self.assertEqual(
            [item["make_name"] for item in payload["supported_car_models"]],
            ["Audi", "BMW"],
        )

    def test_login_returns_clear_message_when_user_is_not_found(self):
        response = self.client.post(
            "/api/token/",
            data={
                "username": "missing-user",
                "password": "test1234",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertEqual(payload["detail"], "No user found with this username.")
        self.assertEqual(payload["message"], "No user found with this username.")
        self.assertEqual(payload["status_code"], 401)
        self.assertEqual(payload["code"], "user_not_found")

    def test_login_returns_clear_message_when_password_is_incorrect(self):
        self.create_user(username="known-user", email="known@example.com")

        response = self.client.post(
            "/api/token/",
            data={
                "username": "known-user",
                "password": "wrong-password",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertEqual(payload["detail"], "The password you entered is incorrect.")
        self.assertEqual(payload["message"], "The password you entered is incorrect.")
        self.assertEqual(payload["status_code"], 401)
        self.assertEqual(payload["code"], "invalid_password")

    def test_protected_endpoints_return_clear_unauthorized_message(self):
        response = self.client.get("/api/me/")

        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertEqual(payload["message"], "You need to sign in to continue.")
        self.assertEqual(payload["status_code"], 401)


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


class CarCatalogApiTests(ApiTestCase):
    def setUp(self):
        self.user = self.create_user()
        self.client.force_authenticate(user=self.user)
        self.create_car_model(make_name="Brand Alpha", model_name="Model One")
        self.create_car_model(make_name="Brand Beta", model_name="Model Two")

    def test_list_car_makes_is_available_without_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.get("/api/car-makes/")

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.json()["count"], 2)

    def test_list_car_makes_returns_nested_models(self):
        response = self.client.get("/api/car-makes/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreaterEqual(payload["count"], 2)
        makes_by_name = {item["name"]: item for item in payload["results"]}
        self.assertIn("Brand Alpha", makes_by_name)
        self.assertIn("Brand Beta", makes_by_name)
        self.assertEqual(
            makes_by_name["Brand Alpha"]["models"][0]["display_name"],
            "Brand Alpha Model One",
        )
        self.assertEqual(
            makes_by_name["Brand Beta"]["models"][0]["display_name"],
            "Brand Beta Model Two",
        )


class PartRequestApiTests(ApiTestCase):
    def setUp(self):
        self.user = self.create_user()
        self.client.force_authenticate(user=self.user)
        self.status, _ = PartRequestStatus.objects.get_or_create(
            code="awaiting",
            defaults={
                "label": "Awaiting",
                "is_terminal": False,
            },
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

    def test_retrieve_part_request_returns_single_request(self):
        part_request = PartRequest.objects.create(
            requester=self.user,
            title="Need bumper",
            description="Original preferred",
            min_price="100.00",
            max_price="250.00",
            status=self.status,
            city="Riyadh",
        )

        response = self.client.get(f"/api/part-requests/{part_request.id}/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["id"], part_request.id)
        self.assertEqual(payload["title"], "Need bumper")

    def test_create_part_request_accepts_null_city(self):
        response = self.client.post(
            "/api/part-requests/",
            data={
                "requester": self.user.id,
                "title": "Need mirror",
                "description": "Side mirror needed",
                "status": self.status.id,
                "city": None,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertIsNone(payload["city"])
        self.assertEqual(payload["images"], [])

    def test_create_part_request_with_images_returns_uploaded_images(self):
        image_bytes = (Path(__file__).resolve().parent.parent / "fixtures" / "sample_part.jpg").read_bytes()
        image = SimpleUploadedFile(
            "sample_part.jpg",
            image_bytes,
            content_type="image/jpeg",
        )

        response = self.client.post(
            "/api/part-requests/",
            data={
                "requester": str(self.user.id),
                "title": "Need headlight",
                "description": "Front right headlight",
                "status": str(self.status.id),
                "city": "",
                "images": [image],
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(len(payload["images"]), 1)
        self.assertIn("sample_part", payload["images"][0]["image"])

        request = PartRequest.objects.get(pk=payload["id"])
        self.assertIsNone(request.city)
        self.assertEqual(request.images.count(), 1)

    def test_create_part_request_triggers_request_created_push_notifications(self):
        with patch("api.views.send_request_created_push_notifications") as push_mock:
            response = self.client.post(
                "/api/part-requests/",
                data={
                    "title": "Need headlight",
                    "description": "Front right headlight",
                    "status": self.status.id,
                    "city": "Riyadh",
                },
                format="json",
            )

        self.assertEqual(response.status_code, 201)
        push_mock.assert_called_once()
        created_request = PartRequest.objects.get(pk=response.json()["id"])
        self.assertEqual(push_mock.call_args.args[0].id, created_request.id)

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

    def test_patch_part_request_updates_fields_and_images_for_owner(self):
        part_request = PartRequest.objects.create(
            requester=self.user,
            title="Need bumper",
            description="Original preferred",
            min_price="100.00",
            max_price="250.00",
            status=self.status,
            city="Riyadh",
        )
        existing_image_bytes = (
            Path(__file__).resolve().parent.parent / "fixtures" / "sample_part.jpg"
        ).read_bytes()
        kept_image = PartImage.objects.create(
            part_request=part_request,
            image=SimpleUploadedFile(
                "kept_sample.jpg",
                existing_image_bytes,
                content_type="image/jpeg",
            ),
        )
        removed_image = PartImage.objects.create(
            part_request=part_request,
            image=SimpleUploadedFile(
                "removed_sample.jpg",
                existing_image_bytes,
                content_type="image/jpeg",
            ),
        )
        new_upload = SimpleUploadedFile(
            "new_sample.jpg",
            existing_image_bytes,
            content_type="image/jpeg",
        )

        response = self.client.patch(
            f"/api/part-requests/{part_request.id}/",
            data={
                "title": "Updated bumper",
                "description": "Updated description",
                "city": "Jeddah",
                "status": str(self.status.id),
                "keep_image_ids": [str(kept_image.id)],
                "sync_images": "true",
                "images": [new_upload],
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["title"], "Updated bumper")
        self.assertEqual(payload["city"], "Jeddah")
        self.assertEqual(len(payload["images"]), 2)

        part_request.refresh_from_db()
        self.assertEqual(part_request.title, "Updated bumper")
        self.assertEqual(part_request.city, "Jeddah")
        self.assertTrue(part_request.images.filter(pk=kept_image.id).exists())
        self.assertFalse(part_request.images.filter(pk=removed_image.id).exists())
        self.assertEqual(part_request.images.count(), 2)

    def test_patch_part_request_rejects_non_owner(self):
        owner = self.create_user(username="owner", email="owner@example.com")
        outsider = self.create_user(username="outsider", email="outsider@example.com")
        part_request = PartRequest.objects.create(
            requester=owner,
            title="Need bumper",
            description="Original preferred",
            status=self.status,
            city="Riyadh",
        )
        self.client.force_authenticate(user=outsider)

        response = self.client.patch(
            f"/api/part-requests/{part_request.id}/",
            data={"title": "Intruder edit"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertEqual(payload["message"], "You can only modify your own requests.")
        part_request.refresh_from_db()
        self.assertEqual(part_request.title, "Need bumper")

    def test_delete_part_request_removes_owned_request(self):
        part_request = PartRequest.objects.create(
            requester=self.user,
            title="Need bumper",
            description="Original preferred",
            status=self.status,
            city="Riyadh",
        )
        PartImage.objects.create(
            part_request=part_request,
            image=SimpleUploadedFile(
                "delete_sample.jpg",
                (Path(__file__).resolve().parent.parent / "fixtures" / "sample_part.jpg").read_bytes(),
                content_type="image/jpeg",
            ),
        )

        response = self.client.delete(f"/api/part-requests/{part_request.id}/")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(PartRequest.objects.filter(pk=part_request.id).exists())
        self.assertFalse(PartImage.objects.filter(part_request_id=part_request.id).exists())

    @override_settings(TRANSLATION_ENABLED=True)
    def test_part_request_retrieve_returns_translated_fields_and_caches_them(self):
        provider = FakeTranslationProvider()
        part_request = PartRequest.objects.create(
            requester=self.user,
            title="Need bumper",
            title_language="en",
            description="Original preferred",
            description_language="en",
            status=self.status,
            city="Riyadh",
        )

        with patch("api.translation.get_translation_provider", return_value=provider):
            response = self.client.get(
                f"/api/part-requests/{part_request.id}/",
                HTTP_ACCEPT_LANGUAGE="ar",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["translated_title"], "ar:Need bumper")
        self.assertEqual(payload["translated_description"], "ar:Original preferred")
        self.assertEqual(payload["translation_target_language"], "ar")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(TranslationCache.objects.count(), 2)

        cached_provider = FakeTranslationProvider()
        with patch("api.translation.get_translation_provider", return_value=cached_provider):
            cached_response = self.client.get(
                f"/api/part-requests/{part_request.id}/",
                HTTP_ACCEPT_LANGUAGE="ar",
            )

        self.assertEqual(cached_response.status_code, 200)
        self.assertEqual(cached_response.json()["translated_title"], "ar:Need bumper")
        self.assertEqual(cached_provider.calls, [])

    @override_settings(TRANSLATION_ENABLED=True)
    def test_part_request_translation_cache_refreshes_after_patch(self):
        provider = FakeTranslationProvider()
        part_request = PartRequest.objects.create(
            requester=self.user,
            title="Need bumper",
            title_language="en",
            description="Original preferred",
            description_language="en",
            status=self.status,
            city="Riyadh",
        )

        with patch("api.translation.get_translation_provider", return_value=provider):
            initial_response = self.client.get(
                f"/api/part-requests/{part_request.id}/",
                HTTP_ACCEPT_LANGUAGE="he",
            )

        self.assertEqual(initial_response.status_code, 200)
        cache_entry = TranslationCache.objects.get(
            entity_type="part_request",
            entity_id=part_request.id,
            field_name="title",
            target_language="he",
        )
        original_source_hash = cache_entry.source_hash

        updated_provider = FakeTranslationProvider()
        with patch("api.translation.get_translation_provider", return_value=updated_provider):
            patch_response = self.client.patch(
                f"/api/part-requests/{part_request.id}/",
                data={
                    "title": "Need rear bumper",
                    "description": "Updated description",
                },
                format="json",
                HTTP_ACCEPT_LANGUAGE="he",
            )

        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["translated_title"], "he:Need rear bumper")
        cache_entry.refresh_from_db()
        self.assertNotEqual(cache_entry.source_hash, original_source_hash)
        self.assertEqual(cache_entry.translated_text, "he:Need rear bumper")

    @override_settings(TRANSLATION_ENABLED=True)
    def test_part_request_translation_skips_same_language_targets(self):
        provider = FakeTranslationProvider()
        part_request = PartRequest.objects.create(
            requester=self.user,
            title="Need bumper",
            title_language="en",
            description="Original preferred",
            description_language="en",
            status=self.status,
            city="Riyadh",
        )

        with patch("api.translation.get_translation_provider", return_value=provider):
            response = self.client.get(
                f"/api/part-requests/{part_request.id}/",
                HTTP_ACCEPT_LANGUAGE="en",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["translated_title"])
        self.assertIsNone(payload["translated_description"])
        self.assertEqual(payload["translation_target_language"], "en")
        self.assertEqual(provider.calls, [])


class ConversationApiTests(ApiTestCase):
    def setUp(self):
        self.buyer = self.create_user(username="buyer", email="buyer@example.com")
        self.seller = self.create_user(
            username="seller",
            email="seller@example.com",
            role="supplier",
        )
        self.status, _ = PartRequestStatus.objects.get_or_create(
            code="awaiting",
            defaults={
                "label": "Awaiting",
                "is_terminal": False,
            },
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

    def test_list_conversations_orders_by_latest_message_activity(self):
        second_conversation = Conversation.objects.create(title="Older activity")
        ConversationParticipant.objects.create(
            conversation=second_conversation,
            user=self.buyer,
        )
        ConversationParticipant.objects.create(
            conversation=second_conversation,
            user=self.seller,
        )

        Message.objects.create(
            conversation=self.conversation,
            sender=self.buyer,
            message_type="text",
            text="Most recent message",
            client_timestamp=timezone.now() + timedelta(minutes=1),
        )

        response = self.client.get("/api/conversations/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["results"][0]["id"], self.conversation.id)
        self.assertEqual(payload["results"][1]["id"], second_conversation.id)

    def test_list_conversations_uses_latest_server_message_for_order_and_preview(self):
        second_conversation = Conversation.objects.create(title="Second chat")
        ConversationParticipant.objects.create(
            conversation=second_conversation,
            user=self.buyer,
        )
        ConversationParticipant.objects.create(
            conversation=second_conversation,
            user=self.seller,
        )
        Message.objects.create(
            conversation=second_conversation,
            sender=self.seller,
            message_type="text",
            text="Second conversation recent",
            client_timestamp=timezone.now(),
        )
        skewed_latest_message = Message.objects.create(
            conversation=self.conversation,
            sender=self.buyer,
            message_type="text",
            text="Clock skew latest",
            client_timestamp=timezone.now() - timedelta(days=30),
        )

        response = self.client.get("/api/conversations/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["results"][0]["id"], self.conversation.id)
        self.assertEqual(
            payload["results"][0]["last_message"]["id"],
            skewed_latest_message.id,
        )
        self.assertEqual(
            payload["results"][0]["last_message"]["text"],
            "Clock skew latest",
        )

    def test_list_conversations_includes_last_message_statuses(self):
        MessageStatus.objects.create(
            message=self.seller_message,
            user=self.seller,
            status=MessageStatus.STATUS_SENT,
        )
        MessageStatus.objects.create(
            message=self.seller_message,
            user=self.buyer,
            status=MessageStatus.STATUS_DELIVERED,
        )

        response = self.client.get("/api/conversations/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        statuses = payload["results"][0]["last_message"]["statuses"]
        self.assertEqual(len(statuses), 2)
        self.assertEqual(statuses[0]["message_id"], self.seller_message.id)
        self.assertEqual(statuses[0]["conversation_id"], self.conversation.id)
        self.assertEqual(statuses[0]["status"], MessageStatus.STATUS_DELIVERED)
        self.assertEqual(statuses[1]["status"], MessageStatus.STATUS_SENT)

    @override_settings(TRANSLATION_ENABLED=True)
    def test_list_conversations_returns_translated_last_message_preview(self):
        self.seller_message.text_language = "en"
        self.seller_message.save(update_fields=["text_language"])
        provider = FakeTranslationProvider()

        with patch("api.translation.get_translation_provider", return_value=provider):
            response = self.client.get("/api/conversations/", HTTP_ACCEPT_LANGUAGE="ar")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        last_message = payload["results"][0]["last_message"]
        self.assertEqual(last_message["message_type"], "text")
        self.assertEqual(last_message["translated_text"], "ar:I can help with that.")
        self.assertEqual(last_message["text_language"], "en")
        self.assertEqual(last_message["translation_target_language"], "ar")

    @override_settings(CHANNEL_LAYER_BACKEND="memory")
    def test_list_conversations_returns_participant_presence(self):
        reset_runtime_state()
        self.addCleanup(reset_runtime_state)
        self.seller.chat_last_seen_at = timezone.now() - timedelta(hours=2)
        self.seller.save(update_fields=["chat_last_seen_at"])
        add_globally_connected_user(self.seller.id, "seller-mobile")

        response = self.client.get("/api/conversations/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        participants = payload["results"][0]["participants"]
        seller_participant = next(
            item for item in participants if item["user"]["id"] == self.seller.id
        )
        self.assertTrue(seller_participant["user"]["is_online"])
        self.assertIsNotNone(seller_participant["user"]["last_seen_at"])

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

    def test_http_message_create_broadcasts_websocket_events(self):
        with patch("api.views.broadcast_created_message") as broadcast_mock:
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
        broadcast_mock.assert_called_once()

        payload, status_events = broadcast_mock.call_args.args
        self.assertEqual(payload["conversation_id"], self.conversation.id)
        self.assertEqual(payload["text"], "Created through HTTP")
        self.assertEqual(len(status_events), 1)
        self.assertEqual(status_events[0]["status"], MessageStatus.STATUS_SENT)

    def test_http_message_create_broadcasts_inbox_events(self):
        with patch("api.views.broadcast_inbox_message") as inbox_broadcast_mock:
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
        inbox_broadcast_mock.assert_called_once()
        self.assertEqual(
            inbox_broadcast_mock.call_args.args[0]["conversation_id"],
            self.conversation.id,
        )
        self.assertEqual(
            inbox_broadcast_mock.call_args.args[0]["text"],
            "Created through HTTP",
        )

    def test_http_message_create_triggers_push_notifications(self):
        with patch("api.views.send_chat_message_push_notifications") as message_push_mock:
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
        message_push_mock.assert_called_once()
        self.assertEqual(
            message_push_mock.call_args.args[0]["conversation_id"],
            self.conversation.id,
        )

    def test_http_message_edit_updates_text_and_marks_message_edited(self):
        response = self.client.patch(
            f"/api/messages/{self.buyer_message.id}/",
            data={"text": "Updated bumper details"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["id"], self.buyer_message.id)
        self.assertEqual(payload["text"], "Updated bumper details")
        self.assertIsNotNone(payload["edited_at"])
        self.assertFalse(payload["is_deleted"])

        self.buyer_message.refresh_from_db()
        self.assertEqual(self.buyer_message.text, "Updated bumper details")
        self.assertIsNotNone(self.buyer_message.edited_at)

    def test_http_message_edit_rejects_other_users_message(self):
        response = self.client.patch(
            f"/api/messages/{self.seller_message.id}/",
            data={"text": "Intruder edit"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["message"], "You can only edit your own messages.")

    def test_http_message_delete_for_everyone_marks_message_deleted(self):
        latest_message = Message.objects.create(
            conversation=self.conversation,
            sender=self.buyer,
            message_type="text",
            text="Delete me for everyone",
            client_timestamp=timezone.now() + timedelta(minutes=2),
        )

        response = self.client.delete(f"/api/messages/{latest_message.id}/?scope=all")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["scope"], "all")
        self.assertEqual(payload["message"]["id"], latest_message.id)
        self.assertTrue(payload["message"]["is_deleted"])
        self.assertEqual(payload["message"]["text"], "")

        latest_message.refresh_from_db()
        self.assertTrue(latest_message.is_deleted)
        self.assertEqual(latest_message.text, "")

        conversations_response = self.client.get("/api/conversations/")
        self.assertEqual(conversations_response.status_code, 200)
        conversation_payload = conversations_response.json()["results"][0]["last_message"]
        self.assertEqual(conversation_payload["id"], latest_message.id)
        self.assertTrue(conversation_payload["is_deleted"])

    def test_http_message_delete_for_me_hides_message_from_requester_only(self):
        response = self.client.delete(f"/api/messages/{self.seller_message.id}/?scope=me")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["scope"], "me")
        self.assertEqual(payload["message_id"], self.seller_message.id)

        messages_response = self.client.get(
            f"/api/messages/?conversation_id={self.conversation.id}"
        )
        self.assertEqual(messages_response.status_code, 200)
        visible_ids = [item["id"] for item in messages_response.json()["results"]]
        self.assertNotIn(self.seller_message.id, visible_ids)

        conversations_response = self.client.get("/api/conversations/")
        self.assertEqual(conversations_response.status_code, 200)
        self.assertEqual(
            conversations_response.json()["results"][0]["last_message"]["id"],
            self.buyer_message.id,
        )
        self.assertEqual(conversations_response.json()["results"][0]["unread_count"], 0)

        self.client.force_authenticate(user=self.seller)
        seller_messages_response = self.client.get(
            f"/api/messages/?conversation_id={self.conversation.id}"
        )
        self.assertEqual(seller_messages_response.status_code, 200)
        seller_visible_ids = [
            item["id"] for item in seller_messages_response.json()["results"]
        ]
        self.assertIn(self.seller_message.id, seller_visible_ids)

    def test_http_message_delete_for_everyone_rejects_other_users_message(self):
        response = self.client.delete(f"/api/messages/{self.seller_message.id}/?scope=all")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["message"],
            "You can only delete your own messages for everyone.",
        )

    def test_http_product_message_returns_product_payload(self):
        product = PartRequest.objects.create(
            requester=self.seller,
            title="OEM grille",
            description="Clean condition",
            min_price="200.00",
            max_price="350.00",
            status=self.status,
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

    def test_http_voice_message_accepts_m4a_upload(self):
        upload = SimpleUploadedFile(
            "voice-note.m4a",
            b"fake m4a bytes for validation coverage",
            content_type="audio/mp4",
        )

        response = self.client.post(
            "/api/messages/",
            data={
                "conversation": self.conversation.id,
                "message_type": "media",
                "client_timestamp": "2026-03-23T10:14:00Z",
                "files": [upload],
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["message_type"], "media")
        self.assertEqual(len(response.json()["media"]), 1)
        self.assertEqual(response.json()["media"][0]["content_type"], "audio/mp4")

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
        payload = response.json()
        self.assertEqual(payload["message"], "You are not a participant in this conversation.")
        self.assertEqual(payload["status_code"], 403)

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

    def test_message_status_create_does_not_trigger_push_notifications(self):
        response = self.client.post(
            "/api/message-statuses/",
            data={
                "message": self.seller_message.id,
                "status": MessageStatus.STATUS_SEEN,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)


class PartRequestAccessApiTests(ApiTestCase):
    def setUp(self):
        self.owner = self.create_user(username="owner", email="owner@example.com")
        self.supplier = self.create_user(
            username="supplier",
            email="supplier@example.com",
            role="supplier",
        )
        self.awaiting_status, _ = PartRequestStatus.objects.get_or_create(
            code="awaiting",
            defaults={
                "label": "Awaiting",
                "is_terminal": False,
            },
        )
        self.in_progress_status, _ = PartRequestStatus.objects.get_or_create(
            code="in_progress",
            defaults={
                "label": "In Progress",
                "is_terminal": False,
            },
        )
        self.part_request = PartRequest.objects.create(
            requester=self.owner,
            title="Need a front bumper",
            description="Original or clean aftermarket",
            status=self.awaiting_status,
            city="Cairo",
        )
        self.conversation = Conversation.objects.create(title="Owner and supplier")
        ConversationParticipant.objects.create(
            conversation=self.conversation,
            user=self.owner,
        )
        ConversationParticipant.objects.create(
            conversation=self.conversation,
            user=self.supplier,
        )

    def _request_access(self):
        self.client.force_authenticate(user=self.supplier)
        return self.client.post(
            "/api/part-request-accesses/",
            data={
                "part_request": self.part_request.id,
                "conversation": self.conversation.id,
            },
            format="json",
        )

    def test_create_access_request_creates_pending_access_and_chat_message(self):
        response = self._request_access()

        self.assertEqual(response.status_code, 201)
        access = PartRequestAccess.objects.get(
            part_request=self.part_request,
            user=self.supplier,
        )
        self.assertEqual(access.status, PartRequestAccess.STATUS_PENDING)
        latest_message = Message.objects.filter(
            conversation=self.conversation
        ).order_by("-id").first()
        self.assertIsNotNone(latest_message)
        self.assertEqual(latest_message.sender_id, self.supplier.id)
        self.assertIn("Requested access to manage the status", latest_message.text)

    def test_owner_can_approve_access_and_supplier_can_only_update_status(self):
        create_response = self._request_access()
        access_id = create_response.json()["id"]

        self.client.force_authenticate(user=self.owner)
        approve_response = self.client.post(
            f"/api/part-request-accesses/{access_id}/approve/",
            format="json",
        )
        self.assertEqual(approve_response.status_code, 200)

        access = PartRequestAccess.objects.get(pk=access_id)
        self.assertEqual(access.status, PartRequestAccess.STATUS_ACCEPTED)

        self.client.force_authenticate(user=self.supplier)
        status_update_response = self.client.patch(
            f"/api/part-requests/{self.part_request.id}/",
            data={"status": self.in_progress_status.id},
            format="json",
        )
        self.assertEqual(status_update_response.status_code, 200)
        self.part_request.refresh_from_db()
        self.assertEqual(self.part_request.status_id, self.in_progress_status.id)

        forbidden_response = self.client.patch(
            f"/api/part-requests/{self.part_request.id}/",
            data={
                "status": self.awaiting_status.id,
                "title": "Updated by supplier",
            },
            format="json",
        )
        self.assertEqual(forbidden_response.status_code, 403)
        self.assertEqual(
            forbidden_response.json()["message"],
            "You can only update the request status after access is approved.",
        )

    def test_approve_access_sends_push_notification_via_system_chat_message(self):
        create_response = self._request_access()
        access_id = create_response.json()["id"]

        self.client.force_authenticate(user=self.owner)
        with patch("api.views.send_chat_message_push_notifications") as push_mock:
            response = self.client.post(
                f"/api/part-request-accesses/{access_id}/approve/",
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        push_mock.assert_called_once()
        self.assertEqual(
            push_mock.call_args.args[0]["conversation_id"],
            self.conversation.id,
        )
        self.assertIn(
            "Approved access to manage the status",
            push_mock.call_args.args[0]["text"],
        )

    def test_supplier_status_update_sends_push_notification_to_request_owner(self):
        create_response = self._request_access()
        access_id = create_response.json()["id"]

        self.client.force_authenticate(user=self.owner)
        approve_response = self.client.post(
            f"/api/part-request-accesses/{access_id}/approve/",
            format="json",
        )
        self.assertEqual(approve_response.status_code, 200)

        self.client.force_authenticate(user=self.supplier)
        with patch("api.views.send_chat_message_push_notifications") as push_mock:
            response = self.client.patch(
                f"/api/part-requests/{self.part_request.id}/",
                data={"status": self.in_progress_status.id},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        push_mock.assert_called_once()
        self.assertEqual(
            push_mock.call_args.args[0]["conversation_id"],
            self.conversation.id,
        )
        self.assertIn(
            'Updated the status of "Need a front bumper"',
            push_mock.call_args.args[0]["text"],
        )

        latest_message = Message.objects.filter(
            conversation=self.conversation
        ).order_by("-id").first()
        self.assertIsNotNone(latest_message)
        self.assertEqual(latest_message.sender_id, self.supplier.id)
        self.assertIn('from "Awaiting" to "In Progress"', latest_message.text)

    def test_part_request_retrieve_includes_access_flags_for_owner_and_supplier(self):
        self._request_access()
        access = PartRequestAccess.objects.get(
            part_request=self.part_request,
            user=self.supplier,
        )
        access.status = PartRequestAccess.STATUS_ACCEPTED
        access.resolved_by = self.owner
        access.resolved_at = timezone.now()
        access.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])

        self.client.force_authenticate(user=self.supplier)
        supplier_response = self.client.get(
            f"/api/part-requests/{self.part_request.id}/"
        )
        self.assertEqual(supplier_response.status_code, 200)
        supplier_payload = supplier_response.json()
        self.assertFalse(supplier_payload["is_owner"])
        self.assertTrue(supplier_payload["can_update_status"])
        self.assertEqual(
            supplier_payload["my_access_status"],
            PartRequestAccess.STATUS_ACCEPTED,
        )
        self.assertEqual(
            supplier_payload["granted_user"]["id"],
            self.supplier.id,
        )
        self.assertEqual(
            supplier_payload["status_details"]["code"],
            self.awaiting_status.code,
        )

        self.client.force_authenticate(user=self.owner)
        owner_response = self.client.get(f"/api/part-requests/{self.part_request.id}/")
        self.assertEqual(owner_response.status_code, 200)
        owner_payload = owner_response.json()
        self.assertTrue(owner_payload["is_owner"])
        self.assertTrue(owner_payload["can_update_status"])
        self.assertIsNone(owner_payload["my_access_status"])
        self.assertEqual(owner_payload["granted_user"]["id"], self.supplier.id)


class MobileApiTests(ApiTestCase):
    def setUp(self):
        self.user = self.create_user(
            username="mobile-user",
            email="mobile@example.com",
            role="supplier",
        )
        self.client.force_authenticate(user=self.user)
        self.camry = self.create_car_model(make_name="Toyota", model_name="Camry")
        self.elantra = self.create_car_model(make_name="Hyundai", model_name="Elantra")

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

    def test_patch_me_updates_profile_fields(self):
        response = self.client.patch(
            "/api/me/",
            data={
                "name": "Updated Mobile User",
                "phone": "+15551234567",
                "city": "Alexandria",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.name, "Updated Mobile User")
        self.assertEqual(self.user.phone, "+15551234567")
        self.assertEqual(self.user.city, "Alexandria")

    def test_patch_me_updates_supported_car_models(self):
        response = self.client.patch(
            "/api/me/",
            data={
                "supported_car_model_ids": [self.camry.id, self.elantra.id],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(UserCarModel.objects.filter(user=self.user).values_list("car_model_id", flat=True)),
            {self.camry.id, self.elantra.id},
        )
        payload = response.json()
        self.assertEqual(len(payload["supported_car_models"]), 2)

    def test_delete_me_removes_current_user_account_and_owned_records(self):
        request_status = PartRequestStatus.objects.create(
            code="delete-account-open",
            label="Delete Account Open",
            is_terminal=False,
        )
        part_request = PartRequest.objects.create(
            requester=self.user,
            title="Delete my request",
            description="Owned by the deleting user",
            status=request_status,
            city="Cairo",
        )
        MobileDevice.objects.create(
            user=self.user,
            device_id="delete-account-device",
            platform="android",
            push_token="delete-token",
            is_active=True,
        )

        response = self.client.delete("/api/me/")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(ApiUser.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(PartRequest.objects.filter(pk=part_request.pk).exists())
        self.assertFalse(MobileDevice.objects.filter(user_id=self.user.id).exists())

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

    def test_mobile_device_validation_returns_clear_message(self):
        response = self.client.post(
            "/api/mobile-devices/",
            data={
                "device_id": "android-002",
                "platform": "android",
                "device_name": "Pixel 9",
                "app_version": "1.0.0",
                "is_active": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertIn("push_token", payload)
        self.assertEqual(
            payload["message"],
            "Push token is required for active mobile devices.",
        )
        self.assertEqual(payload["status_code"], 400)

    def test_mobile_device_registration_accepts_null_optional_metadata(self):
        response = self.client.post(
            "/api/mobile-devices/",
            data={
                "device_id": "android-003",
                "platform": "android",
                "push_token": "token-v3",
                "device_name": None,
                "app_version": None,
                "is_active": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        device = MobileDevice.objects.get(device_id="android-003")
        self.assertEqual(device.device_name, "")
        self.assertEqual(device.app_version, "")

    def test_mobile_device_deactivation_accepts_null_push_token(self):
        MobileDevice.objects.create(
            user=self.user,
            device_id="android-004",
            platform="android",
            push_token="token-v4",
            device_name="Pixel 9",
            app_version="1.0.0",
            is_active=True,
        )

        response = self.client.post(
            "/api/mobile-devices/",
            data={
                "device_id": "android-004",
                "platform": "android",
                "push_token": None,
                "is_active": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        device = MobileDevice.objects.get(device_id="android-004")
        self.assertEqual(device.push_token, "")
        self.assertFalse(device.is_active)

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

    def test_test_request_notification_returns_send_status_for_selected_device(self):
        device = MobileDevice.objects.create(
            user=self.user,
            device_id="android-test-device",
            platform="android",
            push_token="token-test-device",
            is_active=True,
        )
        request_status = PartRequestStatus.objects.create(
            code="test-open",
            label="Test Open",
            is_terminal=False,
        )
        part_request = PartRequest.objects.create(
            requester=self.user,
            title="Test notification request",
            description="Used to verify push delivery",
            status=request_status,
            city="Cairo",
        )

        with patch(
            "api.views.send_test_request_notification",
            return_value={
                "status": "sent",
                "firebase_message_id": "projects/demo/messages/abc",
                "device_id": device.device_id,
                "device_model_id": device.id,
                "user_id": self.user.id,
                "platform": device.platform,
                "channel_id": "chat_activity",
                "push_token_preview": "token-te...vice",
            },
        ) as send_mock:
            response = self.client.post(
                "/api/mobile-devices/test-request-notification/",
                data={
                    "mobile_device_id": device.id,
                    "request_id": part_request.id,
                    "request_title": "Manual notification check",
                    "request_description": "Please verify the push message arrives.",
                    "seller_name": "QA Seller",
                },
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["overall_status"], "sent")
        self.assertEqual(payload["device"]["device_id"], device.device_id)
        self.assertEqual(payload["request_id"], part_request.id)
        self.assertEqual(
            payload["result"]["firebase_message_id"],
            "projects/demo/messages/abc",
        )
        send_mock.assert_called_once()
