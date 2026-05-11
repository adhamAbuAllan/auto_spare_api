from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction

from .models import CarMake, CarModel

logger = logging.getLogger(__name__)

_CACHE_LOCK = threading.RLock()
_MEMORY_CACHE: dict[str, Any] = {
    "makes": None,
    "models": {},
    "model_details": {},
}


class CarImagesApiError(RuntimeError):
    """Raised when the external car catalog API cannot be used safely."""


def _normalize_name(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _cache_get(bucket: str, key: str | None, ttl_seconds: int) -> Any:
    with _CACHE_LOCK:
        if bucket == "makes":
            entry = _MEMORY_CACHE["makes"]
        else:
            entry = _MEMORY_CACHE[bucket].get(key)

        if not entry:
            return None

        expires_at = entry["expires_at"]
        if expires_at <= time.time():
            if bucket == "makes":
                _MEMORY_CACHE["makes"] = None
            else:
                _MEMORY_CACHE[bucket].pop(key, None)
            return None

        return entry["value"]


def _cache_set(bucket: str, key: str | None, value: Any, ttl_seconds: int) -> Any:
    entry = {
        "value": value,
        "expires_at": time.time() + ttl_seconds,
    }
    with _CACHE_LOCK:
        if bucket == "makes":
            _MEMORY_CACHE["makes"] = entry
        else:
            _MEMORY_CACHE[bucket][key] = entry
    return value


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _is_placeholder_image_url(url: Any) -> bool:
    normalized = str(url or "").strip().lower()
    if not normalized:
        return True
    return "placehold.co/" in normalized or normalized.startswith("https://placehold.co")


def _pick_generation_image_url(generation: dict[str, Any]) -> str:
    images = generation.get("images") or {}
    sizes = images.get("sizes") or {}

    for size in ("800", "400", "1200"):
        size_payload = sizes.get(size) or {}
        for image_format in ("webp", "jpg", "png"):
            url = _first_non_empty(size_payload.get(image_format))
            if url:
                return url

    for image_format in ("webp", "jpg", "png"):
        url = _first_non_empty(images.get(image_format))
        if url:
            return url

    return ""


def select_preferred_model_image_url(model_payload: dict[str, Any]) -> str:
    generations = model_payload.get("generations") or []
    ranked_generations = sorted(
        generations,
        key=lambda item: (
            item.get("year_end") or 9999,
            item.get("year_start") or 0,
        ),
        reverse=True,
    )
    for generation in ranked_generations:
        url = _pick_generation_image_url(generation)
        if url:
            return url
    return ""


class CarImagesApiClient:
    def __init__(self) -> None:
        self.base_url = str(
            getattr(
                settings,
                "CAR_IMAGES_API_BASE_URL",
                "https://carimagesapi.com/api/v1",
            )
        ).rstrip("/")
        self.timeout_seconds = int(
            getattr(settings, "CAR_IMAGES_API_TIMEOUT_SECONDS", 20)
        )
        self.cache_ttl_seconds = int(
            getattr(settings, "CAR_IMAGES_MEMORY_CACHE_TTL_SECONDS", 12 * 60 * 60)
        )

    def _request_json(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "auto-spare-api/1.0",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read().decode("utf-8")
        except HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise CarImagesApiError(
                f"Car images API returned HTTP {exc.code} for {url}. {error_body}".strip()
            ) from exc
        except URLError as exc:
            raise CarImagesApiError(
                f"Car images API request failed for {url}: {exc}"
            ) from exc

        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise CarImagesApiError(
                f"Car images API returned invalid JSON for {url}."
            ) from exc

        if not isinstance(data, dict):
            raise CarImagesApiError(
                f"Car images API returned an unexpected payload for {url}."
            )
        return data

    def list_makes(self) -> list[dict[str, Any]]:
        cached = _cache_get("makes", None, self.cache_ttl_seconds)
        if cached is not None:
            return cached

        payload = self._request_json("/makes")
        makes = payload.get("data") or []
        if not isinstance(makes, list):
            raise CarImagesApiError("Car images API returned invalid makes data.")
        return _cache_set("makes", None, makes, self.cache_ttl_seconds)

    def list_models(self, make_slug: str) -> list[dict[str, Any]]:
        normalized_make_slug = str(make_slug or "").strip().lower()
        cache_key = normalized_make_slug
        cached = _cache_get("models", cache_key, self.cache_ttl_seconds)
        if cached is not None:
            return cached

        payload = self._request_json(f"/makes/{quote(normalized_make_slug)}/models")
        models = payload.get("data") or []
        if not isinstance(models, list):
            raise CarImagesApiError(
                f"Car images API returned invalid models data for make {make_slug}."
            )
        return _cache_set("models", cache_key, models, self.cache_ttl_seconds)

    def get_model_details(self, make_slug: str, model_slug: str) -> dict[str, Any]:
        normalized_make_slug = str(make_slug or "").strip().lower()
        normalized_model_slug = str(model_slug or "").strip().lower()
        cache_key = f"{normalized_make_slug}:{normalized_model_slug}"
        cached = _cache_get("model_details", cache_key, self.cache_ttl_seconds)
        if cached is not None:
            return cached

        payload = self._request_json(
            f"/makes/{quote(normalized_make_slug)}/models/{quote(normalized_model_slug)}"
        )
        return _cache_set("model_details", cache_key, payload, self.cache_ttl_seconds)


class CarCatalogSyncService:
    def __init__(self, *, client: CarImagesApiClient | None = None) -> None:
        self.client = client or CarImagesApiClient()

    def sync_catalog(
        self,
        *,
        with_images: bool = False,
        refresh_images: bool = False,
        make_slugs: list[str] | None = None,
    ) -> dict[str, int]:
        target_make_slugs = {
            str(item or "").strip().lower()
            for item in (make_slugs or [])
            if str(item or "").strip()
        }
        stats = {
            "makes_created": 0,
            "makes_updated": 0,
            "models_created": 0,
            "models_updated": 0,
            "images_updated": 0,
        }

        for make_payload in self.client.list_makes():
            remote_make_slug = str(make_payload.get("slug") or "").strip().lower()
            if target_make_slugs and remote_make_slug not in target_make_slugs:
                continue

            make, was_created, was_updated = self._upsert_make(make_payload)
            stats["makes_created"] += int(was_created)
            stats["makes_updated"] += int(was_updated)

            for model_payload in self.client.list_models(make.slug):
                model, model_created, model_updated = self._upsert_model(
                    make=make,
                    model_payload=model_payload,
                )
                stats["models_created"] += int(model_created)
                stats["models_updated"] += int(model_updated)

                if with_images:
                    image_was_updated = self._populate_model_image(
                        model,
                        force_refresh=refresh_images,
                    )
                    stats["images_updated"] += int(image_was_updated)

        return stats

    def ensure_model_image(self, car_model: CarModel) -> str:
        if not _is_placeholder_image_url(car_model.image_url):
            return car_model.image_url

        self._populate_model_image(car_model, force_refresh=False)
        return str(car_model.image_url or "").strip()

    def _upsert_make(self, make_payload: dict[str, Any]) -> tuple[CarMake, bool, bool]:
        remote_name = str(make_payload.get("name") or "").strip()
        remote_slug = str(make_payload.get("slug") or "").strip().lower()
        if not remote_name or not remote_slug:
            raise CarImagesApiError("Car images API returned an invalid make record.")

        make = (
            CarMake.objects.filter(slug=remote_slug).first()
            or CarMake.objects.filter(name__iexact=remote_name).first()
        )

        if make is None:
            make = CarMake.objects.create(name=remote_name, slug=remote_slug)
            return make, True, False

        changed_fields = []
        if make.name != remote_name:
            make.name = remote_name
            changed_fields.append("name")
        if make.slug != remote_slug:
            make.slug = remote_slug
            changed_fields.append("slug")

        if changed_fields:
            make.save(update_fields=changed_fields)
            return make, False, True

        return make, False, False

    def _upsert_model(
        self,
        *,
        make: CarMake,
        model_payload: dict[str, Any],
    ) -> tuple[CarModel, bool, bool]:
        remote_name = str(model_payload.get("name") or "").strip()
        remote_slug = str(model_payload.get("slug") or "").strip().lower()
        if not remote_name or not remote_slug:
            raise CarImagesApiError(
                f"Car images API returned an invalid model record for {make.name}."
            )

        model = (
            CarModel.objects.filter(make=make, slug=remote_slug).first()
            or CarModel.objects.filter(make=make, name__iexact=remote_name).first()
        )

        if model is None:
            model = CarModel.objects.create(
                make=make,
                name=remote_name,
                slug=remote_slug,
                is_active=True,
            )
            return model, True, False

        changed_fields = []
        if model.name != remote_name:
            model.name = remote_name
            changed_fields.append("name")
        if model.slug != remote_slug:
            model.slug = remote_slug
            changed_fields.append("slug")
        if not model.is_active:
            model.is_active = True
            changed_fields.append("is_active")

        if changed_fields:
            model.save(update_fields=changed_fields)
            return model, False, True

        return model, False, False

    def _populate_model_image(self, car_model: CarModel, *, force_refresh: bool) -> bool:
        current_image_url = str(car_model.image_url or "").strip()
        if current_image_url and not _is_placeholder_image_url(current_image_url) and not force_refresh:
            return False

        remote_make_slug = self._ensure_remote_make_slug(car_model.make)
        remote_model_slug = self._ensure_remote_model_slug(car_model, remote_make_slug)
        details = self.client.get_model_details(remote_make_slug, remote_model_slug)
        image_url = select_preferred_model_image_url(details)
        if not image_url or image_url == current_image_url:
            return False

        changed_fields = []
        if car_model.image_url != image_url:
            car_model.image_url = image_url
            changed_fields.append("image_url")
        if car_model.slug != remote_model_slug:
            car_model.slug = remote_model_slug
            changed_fields.append("slug")

        if changed_fields:
            car_model.save(update_fields=changed_fields)
            return True

        return False

    def _ensure_remote_make_slug(self, car_make: CarMake) -> str:
        makes = self.client.list_makes()
        normalized_name = _normalize_name(car_make.name)

        for make_payload in makes:
            remote_slug = str(make_payload.get("slug") or "").strip().lower()
            remote_name = str(make_payload.get("name") or "").strip()
            if car_make.slug == remote_slug or normalized_name == _normalize_name(remote_name):
                changed_fields = []
                if car_make.slug != remote_slug:
                    car_make.slug = remote_slug
                    changed_fields.append("slug")
                if car_make.name != remote_name:
                    car_make.name = remote_name
                    changed_fields.append("name")
                if changed_fields:
                    car_make.save(update_fields=changed_fields)
                return remote_slug

        raise CarImagesApiError(
            f'Unable to match local make "{car_make.name}" with the external catalog.'
        )

    def _ensure_remote_model_slug(self, car_model: CarModel, make_slug: str) -> str:
        remote_models = self.client.list_models(make_slug)
        normalized_name = _normalize_name(car_model.name)

        for model_payload in remote_models:
            remote_slug = str(model_payload.get("slug") or "").strip().lower()
            remote_name = str(model_payload.get("name") or "").strip()
            if car_model.slug == remote_slug or normalized_name == _normalize_name(remote_name):
                changed_fields = []
                if car_model.slug != remote_slug:
                    car_model.slug = remote_slug
                    changed_fields.append("slug")
                if car_model.name != remote_name:
                    car_model.name = remote_name
                    changed_fields.append("name")
                if changed_fields:
                    car_model.save(update_fields=changed_fields)
                return remote_slug

        raise CarImagesApiError(
            f'Unable to match local model "{car_model.name}" under make "{car_model.make.name}" '
            "with the external catalog."
        )


def sync_car_catalog(
    *,
    with_images: bool = False,
    refresh_images: bool = False,
    make_slugs: list[str] | None = None,
) -> dict[str, int]:
    service = CarCatalogSyncService()
    with transaction.atomic():
        return service.sync_catalog(
            with_images=with_images,
            refresh_images=refresh_images,
            make_slugs=make_slugs,
        )
