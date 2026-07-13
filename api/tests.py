from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from chat.runtime import add_globally_connected_user, reset_runtime_state
from chat.push_notifications import _NOTIFICATION_TEXT, _build_message_preview

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
    UserReport,
)
from .firebase import FirebaseTokenVerificationError
from .translation import TranslationValue, localize_part_request_status_label


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
            "name": f"User {suffix}",
            "phone": f"+15550000{suffix:03d}",
            "city": "Riyadh",
            "role": "user",
            "password": "Secur3Pass!2026",
        }
        payload.update(overrides)
        password = payload.pop("password")
        payload.pop("username", None)
        payload.pop("email", None)
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


class HomePageTests(ApiTestCase):
    def test_home_page_serves_multilingual_shell(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "MTA Auto Spare")
        self.assertContains(response, 'data-lang="he"')
        self.assertContains(response, 'data-lang="ar"')


class PrivacyPolicyPageTests(ApiTestCase):
    def test_privacy_policy_page_serves_bundled_html(self):
        response = self.client.get("/privacy-policy/?lang=en")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")
        self.assertContains(response, "MTA Auto Spare Privacy Policy")
        self.assertContains(response, 'data-lang="en"')


@override_settings(APP_UPDATE_STORE_SYNC_ENABLED=False)
class AppUpdateApiTests(ApiTestCase):
    @override_settings(
        APP_UPDATE_LATEST_ANDROID_VERSION="3.1.0",
        APP_UPDATE_LATEST_ANDROID_BUILD=3,
        APP_UPDATE_MIN_ANDROID_VERSION="",
        APP_UPDATE_MIN_ANDROID_BUILD=None,
        APP_UPDATE_ANDROID_STORE_URL="https://play.google.com/store/apps/details?id=com.mta_spare_auto",
        APP_UPDATE_TITLE="New update available",
        APP_UPDATE_MESSAGE="Update MTA to get the latest features.",
        APP_UPDATE_RELEASE_NOTES="Better notifications.",
    )
    def test_app_update_returns_available_android_update(self):
        response = self.client.get(
            "/api/app-update/",
            {
                "platform": "android",
                "version": "3.0.0",
                "build": "2",
                "package": "com.mta_spare_auto",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["update_available"])
        self.assertFalse(payload["update_required"])
        self.assertEqual(payload["latest_version"], "3.1.0")
        self.assertEqual(payload["latest_build_number"], 3)
        self.assertEqual(
            payload["android_store_url"],
            "https://play.google.com/store/apps/details?id=com.mta_spare_auto",
        )
        self.assertEqual(payload["message"], "Update MTA to get the latest features.")

    @override_settings(
        APP_UPDATE_LATEST_ANDROID_VERSION="3.1.0",
        APP_UPDATE_LATEST_ANDROID_BUILD=3,
        APP_UPDATE_MIN_ANDROID_VERSION="3.0.0",
        APP_UPDATE_MIN_ANDROID_BUILD=2,
        APP_UPDATE_ANDROID_STORE_URL="https://play.google.com/store/apps/details?id=com.mta_spare_auto",
        APP_UPDATE_TITLE="",
        APP_UPDATE_MESSAGE="",
        APP_UPDATE_RELEASE_NOTES="",
    )
    def test_app_update_returns_required_when_below_minimum_build(self):
        response = self.client.get(
            "/api/app-update/",
            {
                "platform": "android",
                "version": "3.0.0",
                "build": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["update_available"])
        self.assertTrue(payload["update_required"])
        self.assertEqual(payload["minimum_supported_version"], "3.0.0")
        self.assertEqual(payload["minimum_supported_build_number"], 2)

    @override_settings(
        APP_UPDATE_LATEST_IOS_VERSION="3.1.0",
        APP_UPDATE_LATEST_IOS_BUILD=3,
        APP_UPDATE_MIN_IOS_VERSION="",
        APP_UPDATE_MIN_IOS_BUILD=None,
        APP_UPDATE_IOS_STORE_URL="https://apps.apple.com/us/app/mta-%D7%A9%D7%95%D7%A7-%D7%97%D7%9C%D7%A7%D7%99-%D7%97%D7%99%D7%9C%D7%95%D7%A3-%D7%9C%D7%A8%D7%9B%D7%91/id6776418788",
        APP_UPDATE_TITLE="",
        APP_UPDATE_MESSAGE="",
        APP_UPDATE_RELEASE_NOTES="",
    )
    def test_app_update_returns_available_ios_update(self):
        response = self.client.get(
            "/api/app-update/",
            {
                "platform": "ios",
                "version": "3.0.0",
                "build": "2",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["update_available"])
        self.assertFalse(payload["update_required"])
        self.assertEqual(
            payload["ios_store_url"],
            "https://apps.apple.com/us/app/mta-%D7%A9%D7%95%D7%A7-%D7%97%D7%9C%D7%A7%D7%99-%D7%97%D7%99%D7%9C%D7%95%D7%A3-%D7%9C%D7%A8%D7%9B%D7%91/id6776418788",
        )

    @override_settings(
        APP_UPDATE_LATEST_ANDROID_VERSION="3.0.0",
        APP_UPDATE_LATEST_ANDROID_BUILD=2,
        APP_UPDATE_MIN_ANDROID_VERSION="",
        APP_UPDATE_MIN_ANDROID_BUILD=None,
        APP_UPDATE_ANDROID_STORE_URL="",
        APP_UPDATE_TITLE="",
        APP_UPDATE_MESSAGE="",
        APP_UPDATE_RELEASE_NOTES="",
    )
    def test_app_update_returns_no_update_when_current_version_matches(self):
        response = self.client.get(
            "/api/app-update/",
            {
                "platform": "android",
                "version": "3.0.0",
                "build": "2",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["update_available"])
        self.assertFalse(payload["update_required"])


@override_settings(
    APP_UPDATE_STORE_SYNC_ENABLED=True,
    APP_UPDATE_STORE_CACHE_SECONDS=3600,
    APP_UPDATE_ANDROID_PACKAGE_NAME="com.mta_spare_auto",
    APP_UPDATE_IOS_APP_ID="6776418788",
)
class AppStoreVersionTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

    @patch("api.app_store_versions._fetch")
    def test_android_uses_the_published_play_store_version(self, fetch):
        fetch.return_value = '... "141":[[["3.4.6"]] ...'

        response = self.client.get(
            "/api/app-update/",
            {"platform": "android", "version": "3.4.5", "build": "11"},
        )

        self.assertTrue(response.json()["update_available"])
        self.assertEqual(response.json()["latest_version"], "3.4.6")
        fetch.assert_called_once()

    @patch("api.app_store_versions._fetch")
    def test_ios_uses_the_published_app_store_version(self, fetch):
        fetch.return_value = '{"resultCount": 1, "results": [{"version": "3.4.6"}]}'

        response = self.client.get(
            "/api/app-update/",
            {"platform": "ios", "version": "3.4.5", "build": "11"},
        )

        self.assertTrue(response.json()["update_available"])
        self.assertEqual(response.json()["latest_version"], "3.4.6")

    @override_settings(APP_UPDATE_LATEST_ANDROID_VERSION="3.4.5")
    @patch("api.app_store_versions._fetch", side_effect=OSError("offline"))
    def test_store_failure_uses_the_configured_fallback(self, fetch):
        response = self.client.get(
            "/api/app-update/",
            {"platform": "android", "version": "3.4.5", "build": "11"},
        )

        self.assertFalse(response.json()["update_available"])
        self.assertEqual(response.json()["latest_version"], "3.4.5")


class PushNotificationLanguageTests(ApiTestCase):
    def test_message_preview_uses_the_recipient_language(self):
        preview = _build_message_preview(
            {"message_type": "media"},
            _NOTIFICATION_TEXT["ar"],
        )

        self.assertEqual(preview, "أرسل مرفقًا.")


class UsersApiTests(ApiTestCase):
    def test_user_manager_creates_phone_user(self):
        user = ApiUser.objects.create_user(
            phone="+966555000100",
            name="Manager User",
            password="test1234",
        )

        self.assertEqual(user.phone, "+966555000100")
        self.assertEqual(user.name, "Manager User")
        self.assertTrue(user.check_password("test1234"))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_user_manager_creates_superuser(self):
        user = ApiUser.objects.create_superuser(
            phone="+966555000101",
            password="test1234",
        )

        self.assertEqual(user.phone, "+966555000101")
        self.assertEqual(user.name, "+966555000101")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    @patch("api.serializers.verify_firebase_id_token")
    def test_register_user_with_verified_firebase_phone(self, verify_token):
        verify_token.return_value = {
            "uid": "firebase-user-1",
            "phone_number": "+966555000111",
        }

        response = self.client.post(
            "/api/register/",
            data={
                "firebase_id_token": "valid-firebase-token",
                "name": "Alice",
                "phone": "+966555000111",
                "city": "Riyadh",
                "role": "user",
                "rating": "4.50",
                "password": "Secur3Pass!2026",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(ApiUser.objects.count(), 1)
        user = ApiUser.objects.first()
        payload = response.json()
        self.assertEqual(user.name, "Alice")
        self.assertEqual(user.phone, "+966555000111")
        self.assertEqual(user.firebase_uid, "firebase-user-1")
        self.assertIsNotNone(user.phone_verified_at)
        self.assertEqual(user.role, "user")
        self.assertIn("access", payload)
        self.assertIn("refresh", payload)
        self.assertEqual(payload["user"]["phone"], "+966555000111")
        self.assertIn("chat_push_enabled", payload["user"])
        self.assertIn("chat_message_preview_enabled", payload["user"])

    @patch("api.serializers.verify_firebase_id_token")
    def test_register_supplier_creates_supported_car_model_links(self, verify_token):
        camry = self.create_car_model(make_name="Toyota", model_name="Camry")
        elantra = self.create_car_model(make_name="Hyundai", model_name="Elantra")
        verify_token.return_value = {
            "uid": "firebase-supplier-1",
            "phone_number": "+966555000112",
        }

        response = self.client.post(
            "/api/register/",
            data={
                "firebase_id_token": "valid-firebase-token",
                "name": "Garage Owner",
                "phone": "+966555000112",
                "role": "supplier",
                "password": "Secur3Pass!2026",
                "supported_car_model_ids": [camry.id, elantra.id, camry.id],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        created_user = ApiUser.objects.get(phone="+966555000112")
        self.assertEqual(
            set(
                UserCarModel.objects.filter(user=created_user).values_list(
                    "car_model_id", flat=True
                )
            ),
            {camry.id, elantra.id},
        )

    @patch("api.serializers.verify_firebase_id_token")
    def test_register_user_returns_clear_message_when_phone_exists(self, verify_token):
        self.create_user(phone="+966555000112")
        verify_token.return_value = {
            "uid": "firebase-user-duplicate",
            "phone_number": "+966555000112",
        }

        response = self.client.post(
            "/api/register/",
            data={
                "name": "Alice 2",
                "phone": "+966555000112",
                "city": "Riyadh",
                "role": "user",
                "password": "Secur3Pass!2026",
                "firebase_id_token": "valid-firebase-token",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["message"], "A user with this phone number already exists.")
        self.assertEqual(payload["status_code"], 400)
        self.assertIn("phone", payload)

    @patch("api.serializers.verify_firebase_id_token")
    def test_register_user_rejects_firebase_phone_mismatch(self, verify_token):
        verify_token.return_value = {
            "uid": "firebase-user-mismatch",
            "phone_number": "+966555000114",
        }

        response = self.client.post(
            "/api/register/",
            data={
                "name": "Alice 2",
                "phone": "+966555000113",
                "city": "Riyadh",
                "role": "user",
                "password": "Secur3Pass!2026",
                "firebase_id_token": "valid-firebase-token",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["message"], "Firebase verified phone number does not match.")
        self.assertEqual(payload["status_code"], 400)
        self.assertIn("phone", payload)

    @patch("api.serializers.verify_firebase_id_token")
    def test_register_user_rejects_invalid_firebase_token(self, verify_token):
        verify_token.side_effect = FirebaseTokenVerificationError("invalid")

        response = self.client.post(
            "/api/register/",
            data={
                "name": "Alice 2",
                "phone": "+966555000113",
                "city": "Riyadh",
                "role": "user",
                "password": "Secur3Pass!2026",
                "firebase_id_token": "invalid-firebase-token",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["message"], "Firebase ID token is invalid.")
        self.assertEqual(payload["status_code"], 400)
        self.assertIn("firebase_id_token", payload)

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

    def test_retrieve_supplier_profile_includes_phone_and_supported_car_models(self):
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
                "phone": "+966555999999",
                "password": "Secur3Pass!2026",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertEqual(payload["detail"], "No user found with this phone number.")
        self.assertEqual(payload["message"], "No user found with this phone number.")
        self.assertEqual(payload["status_code"], 401)
        self.assertEqual(payload["code"], "user_not_found")

    def test_login_returns_clear_message_when_password_is_incorrect(self):
        user = self.create_user()

        response = self.client.post(
            "/api/token/",
            data={
                "phone": user.phone,
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

    @patch("api.serializers.verify_firebase_id_token")
    def test_password_reset_updates_password_after_firebase_phone_verification(
        self, verify_token
    ):
        user = self.create_user(phone="+966555000115", password="OldPass123!")
        verify_token.return_value = {
            "uid": "firebase-reset-uid",
            "phone_number": user.phone,
        }

        response = self.client.post(
            "/api/password-reset/",
            data={
                "firebase_id_token": "firebase-token",
                "phone": user.phone,
                "password": "NewPass123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password("NewPass123!"))
        self.assertIsNotNone(user.phone_verified_at)

    @patch("api.serializers.verify_firebase_id_token")
    def test_password_reset_rejects_firebase_phone_mismatch(self, verify_token):
        user = self.create_user(phone="+966555000116")
        verify_token.return_value = {
            "uid": "firebase-reset-uid",
            "phone_number": "+966555000117",
        }

        response = self.client.post(
            "/api/password-reset/",
            data={
                "firebase_id_token": "firebase-token",
                "phone": user.phone,
                "password": "NewPass123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["message"], "Firebase verified phone number does not match.")
        self.assertIn("phone", payload)

    def test_password_reset_rejects_unknown_phone(self):
        response = self.client.post(
            "/api/password-reset/",
            data={
                "firebase_id_token": "firebase-token",
                "phone": "+966555000118",
                "password": "NewPass123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["message"], "No user found with this phone number.")
        self.assertIn("phone", payload)

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

    @override_settings(CAR_IMAGES_API_KEY="", CAR_IMAGES_API_SECRET="")
    def test_car_catalog_returns_unpaginated_active_models(self):
        alpha_make = CarMake.objects.get(name="Brand Alpha")
        alpha_model = CarModel.objects.get(make=alpha_make, name="Model One")
        alpha_model.image_url = "https://images.example/brand-alpha-model-one.webp"
        alpha_model.save(update_fields=["image_url"])
        self.create_car_model(
            make_name="Brand Alpha",
            model_name="Inactive Model",
            is_active=False,
        )
        self.client.force_authenticate(user=None)

        response = self.client.get("/api/car-catalog/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsInstance(payload, list)
        makes_by_name = {item["name"]: item for item in payload}
        self.assertIn("Brand Alpha", makes_by_name)
        alpha_models = {
            item["name"]: item for item in makes_by_name["Brand Alpha"]["models"]
        }
        self.assertIn("Model One", alpha_models)
        self.assertNotIn("Inactive Model", alpha_models)
        self.assertEqual(
            alpha_models["Model One"]["image_url"],
            "https://images.example/brand-alpha-model-one.webp",
        )

    def test_search_car_models_filters_by_query_and_make(self):
        alpha_make = CarMake.objects.get(name="Brand Alpha")
        self.create_car_model(make_name="Brand Alpha", model_name="RS Seven")
        self.create_car_model(make_name="Brand Beta", model_name="RS Eight")

        response = self.client.get(
            f"/api/car-models/?search=rs&make_id={alpha_make.id}"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["display_name"], "Brand Alpha RS Seven")


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

    def test_expired_part_request_is_hidden_from_list_and_detail(self):
        part_request = PartRequest.objects.create(
            requester=self.user,
            title="Old bumper request",
            description="This should no longer be visible",
            status=self.status,
            city="Riyadh",
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        list_response = self.client.get("/api/part-requests/")
        detail_response = self.client.get(f"/api/part-requests/{part_request.id}/")

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["count"], 0)
        self.assertEqual(detail_response.status_code, 404)
        self.assertTrue(PartRequest.objects.filter(pk=part_request.pk).exists())

    def test_new_part_request_expires_in_48_hours(self):
        before_creation = timezone.now()
        part_request = PartRequest.objects.create(
            requester=self.user,
            title="New bumper request",
            description="Still visible",
            status=self.status,
            city="Riyadh",
        )

        self.assertGreaterEqual(
            part_request.expires_at,
            before_creation + timedelta(hours=48),
        )
        self.assertLessEqual(
            part_request.expires_at,
            timezone.now() + timedelta(hours=48, seconds=1),
        )

    def test_list_hides_granted_request_from_other_suppliers(self):
        owner = self.create_user(username="owner", email="owner@example.com")
        granted_supplier = self.create_user(
            username="granted-supplier",
            email="granted-supplier@example.com",
            role="supplier",
        )
        other_supplier = self.create_user(
            username="other-supplier",
            email="other-supplier@example.com",
            role="supplier",
        )
        part_request = PartRequest.objects.create(
            requester=owner,
            title="Need alternator",
            description="Original preferred",
            status=self.status,
            city="Riyadh",
        )
        PartRequestAccess.objects.create(
            part_request=part_request,
            user=granted_supplier,
            status=PartRequestAccess.STATUS_ACCEPTED,
            resolved_by=owner,
            resolved_at=timezone.now(),
        )

        self.client.force_authenticate(user=other_supplier)
        other_supplier_response = self.client.get("/api/part-requests/")
        self.assertEqual(other_supplier_response.status_code, 200)
        self.assertEqual(other_supplier_response.json()["count"], 0)

        self.client.force_authenticate(user=granted_supplier)
        granted_supplier_response = self.client.get("/api/part-requests/")
        self.assertEqual(granted_supplier_response.status_code, 200)
        self.assertEqual(granted_supplier_response.json()["count"], 1)
        self.assertEqual(
            granted_supplier_response.json()["results"][0]["id"],
            part_request.id,
        )

        self.client.force_authenticate(user=owner)
        owner_response = self.client.get("/api/part-requests/")
        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(owner_response.json()["count"], 1)
        self.assertEqual(owner_response.json()["results"][0]["id"], part_request.id)

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
        car_model = self.create_car_model()
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
                "car_model": str(car_model.id),
                "city": "",
                "images": [image],
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(len(payload["images"]), 1)
        self.assertTrue(payload["images"][0]["image"].startswith("http://testserver/media/"))
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
                "notification_language": "ar",
                "is_active": True,
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(MobileDevice.objects.count(), 1)
        self.assertEqual(MobileDevice.objects.first().push_token, "token-v1")
        self.assertEqual(MobileDevice.objects.first().notification_language, "ar")

        update_response = self.client.post(
            "/api/mobile-devices/",
            data={
                "device_id": "android-001",
                "platform": "android",
                "push_token": "token-v2",
                "device_name": "Pixel 9",
                "app_version": "1.0.1",
                "notification_language": "he-IL",
                "is_active": True,
            },
            format="json",
        )

        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(MobileDevice.objects.count(), 1)
        device = MobileDevice.objects.get()
        self.assertEqual(device.push_token, "token-v2")
        self.assertEqual(device.app_version, "1.0.1")
        self.assertEqual(device.notification_language, "he")

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


class LocalizationFallbackTests(ApiTestCase):
    def setUp(self):
        self.user = self.create_user(username="translator", email="translator@example.com")
        self.client.force_authenticate(user=self.user)
        self.awaiting_status = PartRequestStatus.objects.create(
            code="awaiting",
            label="Awaiting",
            is_terminal=False,
        )

    def test_part_request_statuses_are_localized_without_external_provider(self):
        response = self.client.get(
            "/api/part-request-statuses/",
            HTTP_ACCEPT_LANGUAGE="ar",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["results"][0]["label"],
            localize_part_request_status_label(
                code="awaiting",
                label="Awaiting",
                target_language="ar",
            ),
        )

    def test_known_system_chat_messages_get_deterministic_translation(self):
        owner = self.create_user(username="owner-ar", email="owner-ar@example.com")
        supplier = self.create_user(
            username="supplier-ar",
            email="supplier-ar@example.com",
            role="supplier",
        )
        conversation = Conversation.objects.create(title="Localized system chat")
        ConversationParticipant.objects.create(conversation=conversation, user=owner)
        ConversationParticipant.objects.create(conversation=conversation, user=supplier)
        Message.objects.create(
            conversation=conversation,
            sender=supplier,
            message_type="text",
            text='Requested access to manage the status of "Need bumper".',
            client_timestamp=timezone.now(),
        )

        self.client.force_authenticate(user=owner)
        response = self.client.get(
            f"/api/messages/?conversation_id={conversation.id}",
            HTTP_ACCEPT_LANGUAGE="ar",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        results = payload["results"] if isinstance(payload, dict) else payload
        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0]["translated_text"],
            '\u0637\u0644\u0628 \u0627\u0644\u0648\u0635\u0648\u0644 \u0644\u0625\u062f\u0627\u0631\u0629 \u062d\u0627\u0644\u0629 "Need bumper".',
        )


class UserModerationApiTests(ApiTestCase):
    def setUp(self):
        self.admin = self.create_user(username="admin", email="admin@example.com")
        self.admin.is_staff = True
        self.admin.save(update_fields=["is_staff"])
        self.reporter = self.create_user(username="reporter", email="reporter@example.com")
        self.reported_user = self.create_user(
            username="reported",
            email="reported@example.com",
        )

    def test_user_report_lifecycle_for_reporter_and_admin(self):
        self.client.force_authenticate(user=self.reporter)
        create_response = self.client.post(
            "/api/user-reports/",
            data={
                "reported_user": self.reported_user.id,
                "reason": "Spam",
                "details": "Repeated unwanted messages.",
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201)
        report = UserReport.objects.get()
        self.assertEqual(report.reporter_id, self.reporter.id)
        self.assertEqual(report.reported_user_id, self.reported_user.id)
        self.assertEqual(report.status, UserReport.STATUS_OPEN)

        reporter_list_response = self.client.get("/api/user-reports/")
        self.assertEqual(reporter_list_response.status_code, 200)
        self.assertEqual(reporter_list_response.json()["count"], 1)

        self.client.force_authenticate(user=self.admin)
        admin_list_response = self.client.get("/api/user-reports/")
        self.assertEqual(admin_list_response.status_code, 200)
        self.assertEqual(admin_list_response.json()["count"], 1)

        update_response = self.client.patch(
            f"/api/user-reports/{report.id}/",
            data={
                "status": UserReport.STATUS_ACTIONED,
                "admin_notes": "User warned and reviewed.",
            },
            format="json",
        )

        self.assertEqual(update_response.status_code, 200)
        report.refresh_from_db()
        self.assertEqual(report.status, UserReport.STATUS_ACTIONED)
        self.assertEqual(report.reviewed_by_id, self.admin.id)
        self.assertEqual(report.admin_notes, "User warned and reviewed.")
        self.assertIsNotNone(report.reviewed_at)

    def test_admin_can_block_and_unblock_user_and_blocked_user_cannot_login(self):
        blocked_user = self.create_user(
            username="blocked-user",
            email="blocked-user@example.com",
            password="secret123",
        )

        self.client.force_authenticate(user=self.admin)
        block_response = self.client.post(
            f"/api/users/{blocked_user.id}/block/",
            data={"reason": "Fraud investigation"},
            format="json",
        )

        self.assertEqual(block_response.status_code, 200)
        blocked_user.refresh_from_db()
        self.assertFalse(blocked_user.is_active)
        self.assertEqual(blocked_user.blocked_reason, "Fraud investigation")
        self.assertEqual(blocked_user.blocked_by_id, self.admin.id)
        self.assertIsNotNone(blocked_user.blocked_at)

        self.client.force_authenticate(user=None)
        blocked_login_response = self.client.post(
            "/api/token/",
            data={"phone": blocked_user.phone, "password": "secret123"},
            format="json",
        )

        self.assertEqual(blocked_login_response.status_code, 401)
        self.assertEqual(
            blocked_login_response.json()["code"],
            "blocked_account",
        )

        self.client.force_authenticate(user=self.admin)
        unblock_response = self.client.post(
            f"/api/users/{blocked_user.id}/unblock/",
            format="json",
        )

        self.assertEqual(unblock_response.status_code, 200)
        blocked_user.refresh_from_db()
        self.assertTrue(blocked_user.is_active)
        self.assertIsNone(blocked_user.blocked_at)
        self.assertEqual(blocked_user.blocked_reason, "")
        self.assertIsNone(blocked_user.blocked_by)

        self.client.force_authenticate(user=None)
        login_response = self.client.post(
            "/api/token/",
            data={"phone": blocked_user.phone, "password": "secret123"},
            format="json",
        )

        self.assertEqual(login_response.status_code, 200)
        self.assertIn("access", login_response.json())

    def test_admin_can_delete_user_by_phone(self):
        target_user = self.create_user(
            phone="+966555000201",
            username="delete-target",
            email="delete-target@example.com",
        )

        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/users/delete-by-phone/",
            data={"phone": target_user.phone},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deleted_user_id"], target_user.id)
        self.assertEqual(response.json()["phone"], target_user.phone)
        self.assertFalse(ApiUser.objects.filter(pk=target_user.pk).exists())

    def test_non_admin_cannot_delete_user_by_phone(self):
        target_user = self.create_user(
            phone="+966555000202",
            username="delete-target",
            email="delete-target@example.com",
        )

        self.client.force_authenticate(user=self.reporter)
        response = self.client.post(
            "/api/users/delete-by-phone/",
            data={"phone": target_user.phone},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(ApiUser.objects.filter(pk=target_user.pk).exists())

    def test_admin_delete_by_phone_rejects_unknown_phone(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/users/delete-by-phone/",
            data={"phone": "+966555000203"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("phone", response.json())

    def test_admin_delete_by_phone_rejects_own_account(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/users/delete-by-phone/",
            data={"phone": self.admin.phone},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(ApiUser.objects.filter(pk=self.admin.pk).exists())
