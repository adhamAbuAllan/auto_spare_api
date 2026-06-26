from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction

from .models import CarMake, CarModel
from .mock_catalog_data import MOCK_CAR_CATALOG, MOCK_CAR_IMAGE_URL

logger = logging.getLogger(__name__)
DEFAULT_CAR_IMAGES_API_BASE_URL = "https://carimagesapi.com/api/v1"

_CACHE_LOCK = threading.RLock()
_MEMORY_CACHE: dict[str, Any] = {
    "makes": None,
    "models": {},
    "model_details": {},
}


class CarImagesApiError(RuntimeError):
    """Raised when the external car catalog API cannot be used safely."""


def _clean_base_url(value: Any) -> str:
    return str(value or "").strip().rstrip("/")


def _as_base_url_list(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates = value.split(",")
    else:
        candidates = value or []
    urls = []
    for item in candidates:
        url = _clean_base_url(item)
        if url:
            urls.append(url)
    return urls


def _unique_base_urls(values: list[str]) -> list[str]:
    urls = []
    seen = set()
    for value in values:
        url = _clean_base_url(value)
        if url and url not in seen:
            urls.append(url)
            seen.add(url)
    return urls


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


def build_signed_model_image_url(car_model: CarModel) -> str:
    api_key = str(getattr(settings, "CAR_IMAGES_API_KEY", "") or "").strip()
    api_secret = str(getattr(settings, "CAR_IMAGES_API_SECRET", "") or "").strip()
    if not api_key or not api_secret:
        return str(car_model.image_url or "").strip()

    ttl_seconds = int(getattr(settings, "CAR_IMAGES_SIGNED_IMAGE_TTL_SECONDS", 300))
    width = str(getattr(settings, "CAR_IMAGES_IMAGE_WIDTH", "800") or "800").strip()
    image_format = str(
        getattr(settings, "CAR_IMAGES_IMAGE_FORMAT", "webp") or "webp"
    ).strip()
    params = {
        "api_key": api_key,
        "expires": str(int(time.time()) + max(ttl_seconds, 60)),
        "format": image_format,
        "make": car_model.make.name,
        "model": car_model.name,
        "width": width,
    }
    canonical = urlencode(sorted(params.items()), quote_via=quote)
    params["sig"] = hmac.new(
        api_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"https://carimagesapi.com/image?{urlencode(params, quote_via=quote)}"


class CarImagesApiClient:
    def __init__(self) -> None:
        configured_base_url = _clean_base_url(
            getattr(
                settings,
                "CAR_IMAGES_API_BASE_URL",
                DEFAULT_CAR_IMAGES_API_BASE_URL,
            )
        )
        configured_fallback_urls = _as_base_url_list(
            getattr(settings, "CAR_IMAGES_API_FALLBACK_BASE_URLS", ())
        )
        self.base_urls = _unique_base_urls(
            [
                configured_base_url,
                *configured_fallback_urls,
                DEFAULT_CAR_IMAGES_API_BASE_URL,
            ]
        )
        self.base_url = self.base_urls[0]
        self.timeout_seconds = int(
            getattr(settings, "CAR_IMAGES_API_TIMEOUT_SECONDS", 20)
        )
        self.cache_ttl_seconds = int(
            getattr(settings, "CAR_IMAGES_MEMORY_CACHE_TTL_SECONDS", 12 * 60 * 60)
        )

    @property
    def is_mock_mode(self) -> bool:
        return getattr(settings, "CAR_IMAGES_API_MOCK", not getattr(settings, "CAR_IMAGES_API_KEY", "").strip())

    def _request_json(self, path: str) -> dict[str, Any]:
        errors = []
        for index, base_url in enumerate(self.base_urls):
            url = f"{base_url}{path}"
            try:
                return self._request_json_from_url(url, base_url)
            except CarImagesApiError as exc:
                errors.append(str(exc))
                if index < len(self.base_urls) - 1:
                    logger.warning(
                        "Car images API request failed through %s; trying fallback: %s",
                        base_url,
                        exc,
                    )

        raise CarImagesApiError(
            "Car images API request failed for all configured base URLs. "
            + " | ".join(errors)
        )

    def _request_json_pages(self, path: str) -> list[dict[str, Any]]:
        errors = []
        for index, base_url in enumerate(self.base_urls):
            url = f"{base_url}{path}"
            try:
                return self._request_json_pages_from_url(url, base_url)
            except CarImagesApiError as exc:
                errors.append(str(exc))
                if index < len(self.base_urls) - 1:
                    logger.warning(
                        "Paginated car images API request failed through %s; "
                        "trying fallback: %s",
                        base_url,
                        exc,
                    )

        raise CarImagesApiError(
            "Car images API paginated request failed for all configured base URLs. "
            + " | ".join(errors)
        )

    def _request_json_pages_from_url(self, url: str, base_url: str) -> list[dict[str, Any]]:
        items = []
        next_url = url
        visited_urls = set()

        while next_url:
            if next_url in visited_urls:
                raise CarImagesApiError(
                    f"Car images API pagination loop detected for {next_url}."
                )
            visited_urls.add(next_url)

            payload = self._request_json_from_url(next_url, base_url)
            page_items = payload.get("data") or []
            if not isinstance(page_items, list):
                raise CarImagesApiError(
                    f"Car images API returned invalid paginated data for {next_url}."
                )
            items.extend(page_items)

            next_url = self._extract_next_page_url(payload, current_url=next_url)
            if next_url:
                next_url = self._normalize_next_page_url(next_url, base_url)

        return items

    def _request_json_from_url(self, url: str, base_url: str) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "auto-spare-api/1.0",
        }
        proxy_token = str(getattr(settings, "CAR_IMAGES_API_PROXY_TOKEN", "")).strip()
        if proxy_token and self._should_send_proxy_token(base_url):
            headers["X-Car-Images-Proxy-Token"] = proxy_token

        api_key = str(getattr(settings, "CAR_IMAGES_API_KEY", "") or "").strip()
        if api_key and not self._should_send_proxy_token(base_url):
            headers["Authorization"] = f"Bearer {api_key}"

        request = Request(
            url,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read().decode("utf-8")
        except HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", errors="replace")[:500]
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

    def _extract_next_page_url(self, payload: dict[str, Any], *, current_url: str) -> str:
        next_value = payload.get("next")
        links = payload.get("links")
        pagination = payload.get("pagination") or payload.get("meta")

        if not next_value and isinstance(links, dict):
            next_value = links.get("next")
        if not next_value and isinstance(pagination, dict):
            next_value = pagination.get("next") or pagination.get("next_page_url")

        if isinstance(next_value, dict):
            next_value = next_value.get("url") or next_value.get("href")
        if next_value is True and isinstance(pagination, dict):
            next_value = pagination.get("next_url")

        next_url = str(next_value or "").strip()
        if not next_url:
            return ""
        return urljoin(current_url, next_url)

    def _normalize_next_page_url(self, next_url: str, base_url: str) -> str:
        parsed_next = urlparse(next_url)
        parsed_base = urlparse(base_url)
        if not parsed_next.netloc or parsed_next.netloc == parsed_base.netloc:
            return next_url

        base_path = parsed_base.path.rstrip("/")
        if parsed_next.path == base_path or parsed_next.path.startswith(f"{base_path}/"):
            suffix = parsed_next.path[len(base_path):]
            normalized_url = f"{base_url}{suffix}"
            if parsed_next.query:
                normalized_url = f"{normalized_url}?{parsed_next.query}"
            return normalized_url

        return next_url

    def _should_send_proxy_token(self, base_url: str) -> bool:
        base_hostname = (urlparse(base_url).hostname or "").lower()
        upstream_hostname = (
            urlparse(DEFAULT_CAR_IMAGES_API_BASE_URL).hostname or ""
        ).lower()
        return base_hostname != upstream_hostname

    def list_makes(self) -> list[dict[str, Any]]:
        cached = _cache_get("makes", None, self.cache_ttl_seconds)
        if cached is not None:
            return cached

        if self.is_mock_mode:
            from django.utils.text import slugify
            makes = [{"name": name, "slug": slugify(name)} for name in MOCK_CAR_CATALOG.keys()]
        else:
            makes = self._request_json_pages("/makes")
        return _cache_set("makes", None, makes, self.cache_ttl_seconds)

    def list_models(self, make_slug: str) -> list[dict[str, Any]]:
        normalized_make_slug = str(make_slug or "").strip().lower()
        cache_key = normalized_make_slug
        cached = _cache_get("models", cache_key, self.cache_ttl_seconds)
        if cached is not None:
            return cached

        if self.is_mock_mode:
            from django.utils.text import slugify
            models = []
            for make_name, model_names in MOCK_CAR_CATALOG.items():
                if slugify(make_name) == normalized_make_slug:
                    models = [{"name": name, "slug": slugify(name)} for name in model_names]
                    break
        else:
            models = self._request_json_pages(f"/makes/{quote(normalized_make_slug)}/models")
        return _cache_set("models", cache_key, models, self.cache_ttl_seconds)

    def get_model_details(self, make_slug: str, model_slug: str) -> dict[str, Any]:
        normalized_make_slug = str(make_slug or "").strip().lower()
        normalized_model_slug = str(model_slug or "").strip().lower()
        cache_key = f"{normalized_make_slug}:{normalized_model_slug}"
        cached = _cache_get("model_details", cache_key, self.cache_ttl_seconds)
        if cached is not None:
            return cached

        if self.is_mock_mode:
            from django.utils.text import slugify
            make_name_matched = None
            model_name_matched = None
            for make_name, model_names in MOCK_CAR_CATALOG.items():
                if slugify(make_name) == normalized_make_slug:
                    make_name_matched = make_name
                    for m in model_names:
                        if slugify(m) == normalized_model_slug:
                            model_name_matched = m
                            break
                if make_name_matched:
                    break

            if not make_name_matched or not model_name_matched:
                raise CarImagesApiError(f"Model not found: {make_slug}/{model_slug}")

            payload = {
                "generations": [
                    {
                        "year_start": 2020,
                        "year_end": 2024,
                        "images": {
                            "sizes": {
                                "800": {
                                    "webp": MOCK_CAR_IMAGE_URL
                                }
                            }
                        }
                    }
                ]
            }
        else:
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
