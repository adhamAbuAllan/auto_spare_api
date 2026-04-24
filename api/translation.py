import logging
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from typing import Iterable
from urllib.parse import parse_qs

from django.conf import settings
from django.db import transaction


logger = logging.getLogger(__name__)

SUPPORTED_TRANSLATION_LANGUAGES = tuple(
    dict.fromkeys(
        language
        for language in getattr(settings, "TRANSLATION_SUPPORTED_LANGUAGES", ())
        if language
    )
)
_ARABIC_BLOCKS = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)
_HEBREW_BLOCKS = ((0x0590, 0x05FF),)


@dataclass(frozen=True)
class TextTranslationItem:
    entity_type: str
    entity_id: int
    field_name: str
    source_text: str
    source_language: str | None = None

    @property
    def cache_key(self):
        return (self.entity_type, self.entity_id, self.field_name)


@dataclass(frozen=True)
class TranslationValue:
    translated_text: str
    source_language: str | None = None
    provider: str | None = None


def normalize_language_code(value):
    if value is None:
        return None

    normalized = str(value).strip().lower().replace("_", "-")
    if not normalized:
        return None

    normalized = normalized.split(";", 1)[0].strip()
    if normalized in SUPPORTED_TRANSLATION_LANGUAGES:
        return normalized

    base_language = normalized.split("-", 1)[0]
    if base_language in SUPPORTED_TRANSLATION_LANGUAGES:
        return base_language

    return None


def resolve_requested_translation_language(request=None, *, header_value=None):
    header = header_value
    if request is not None:
        header = request.headers.get("Accept-Language", "")

    if not header:
        return None

    for candidate in str(header).split(","):
        language_code = normalize_language_code(candidate)
        if language_code:
            return language_code

    return None


def resolve_requested_translation_language_from_scope(scope):
    query_string = parse_qs((scope.get("query_string") or b"").decode())
    return normalize_language_code((query_string.get("lang") or [None])[0])


def _count_characters_in_blocks(text, blocks):
    total = 0
    for char in text:
        codepoint = ord(char)
        for start, end in blocks:
            if start <= codepoint <= end:
                total += 1
                break
    return total


def _heuristic_detect_language(text):
    if not text:
        return None

    arabic_count = _count_characters_in_blocks(text, _ARABIC_BLOCKS)
    hebrew_count = _count_characters_in_blocks(text, _HEBREW_BLOCKS)
    latin_count = sum(1 for char in text if char.isascii() and char.isalpha())

    counts = {
        "ar": arabic_count,
        "he": hebrew_count,
        "en": latin_count,
    }
    language, count = max(counts.items(), key=lambda item: item[1])
    if count <= 0:
        return None
    return language


def detect_source_language(text):
    normalized_text = str(text or "").strip()
    if not normalized_text:
        return None
    return _heuristic_detect_language(normalized_text)


def stamp_part_request_languages(part_request):
    part_request.title_language = detect_source_language(part_request.title) or ""
    part_request.description_language = (
        detect_source_language(part_request.description) or ""
    )
    return part_request


def stamp_message_language(message):
    message.text_language = detect_source_language(message.text) or ""
    return message


def build_source_hash(source_text):
    return sha256((source_text or "").encode("utf-8")).hexdigest()


class GoogleTranslationProvider:
    provider_name = "google"

    def __init__(self):
        try:
            from google.cloud import translate_v3
        except ImportError as exc:  # pragma: no cover - import depends on env
            raise RuntimeError("google-cloud-translate is not installed.") from exc

        project_id = getattr(settings, "GOOGLE_CLOUD_PROJECT", "").strip()
        if not project_id:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for translation.")

        self._client = translate_v3.TranslationServiceClient()
        self._parent = (
            f"projects/{project_id}/locations/"
            f"{getattr(settings, 'TRANSLATION_GOOGLE_LOCATION', 'global')}"
        )

    def translate_texts(self, *, texts, target_language, source_language=None):
        if not texts:
            return []

        request = {
            "contents": list(texts),
            "parent": self._parent,
            "mime_type": "text/plain",
            "target_language_code": target_language,
        }
        if source_language:
            request["source_language_code"] = source_language

        response = self._client.translate_text(request=request)
        translations = []
        for translation in response.translations:
            translations.append(
                TranslationValue(
                    translated_text=translation.translated_text,
                    source_language=normalize_language_code(
                        getattr(translation, "detected_language_code", None)
                    )
                    or source_language,
                    provider=self.provider_name,
                )
            )
        return translations


@lru_cache(maxsize=1)
def get_translation_provider():
    if not getattr(settings, "TRANSLATION_ENABLED", False):
        return None

    provider_name = getattr(settings, "TRANSLATION_PROVIDER", "google").strip().lower()
    if provider_name != "google":
        logger.warning("Unsupported translation provider configured: %s", provider_name)
        return None

    try:
        return GoogleTranslationProvider()
    except Exception as exc:  # pragma: no cover - depends on credentials/runtime
        logger.warning("Translation provider is unavailable: %s", exc)
        return None


def _cache_model():
    from .models import TranslationCache

    return TranslationCache


def translate_text_items(items: Iterable[TextTranslationItem], target_language):
    normalized_target_language = normalize_language_code(target_language)
    prepared_items = []
    for item in items:
        if not item.source_text:
            continue

        normalized_source_language = normalize_language_code(item.source_language) or (
            detect_source_language(item.source_text)
        )
        prepared_items.append(
            TextTranslationItem(
                entity_type=item.entity_type,
                entity_id=int(item.entity_id),
                field_name=item.field_name,
                source_text=item.source_text,
                source_language=normalized_source_language,
            )
        )

    if not prepared_items or not normalized_target_language:
        return {}

    entity_types = {item.entity_type for item in prepared_items}
    entity_ids = {item.entity_id for item in prepared_items}
    field_names = {item.field_name for item in prepared_items}
    source_hashes = {
        item.cache_key: build_source_hash(item.source_text) for item in prepared_items
    }

    cache_rows = _cache_model().objects.filter(
        target_language=normalized_target_language,
        entity_type__in=entity_types,
        entity_id__in=entity_ids,
        field_name__in=field_names,
    )
    cache_by_key = {
        (row.entity_type, row.entity_id, row.field_name): row for row in cache_rows
    }

    results = {}
    misses = []
    for item in prepared_items:
        if item.source_language == normalized_target_language:
            continue

        cache_key = item.cache_key
        cache_row = cache_by_key.get(cache_key)
        if cache_row and cache_row.source_hash == source_hashes[cache_key]:
            results[cache_key] = TranslationValue(
                translated_text=cache_row.translated_text,
                source_language=cache_row.source_language or item.source_language,
                provider=cache_row.provider,
            )
            continue

        misses.append(item)

    if not misses:
        return results

    provider = get_translation_provider()
    if provider is None:
        return results

    misses_by_language = {}
    for item in misses:
        misses_by_language.setdefault(item.source_language or "", []).append(item)

    updates = []
    for source_language, grouped_items in misses_by_language.items():
        try:
            translated_values = provider.translate_texts(
                texts=[item.source_text for item in grouped_items],
                source_language=source_language or None,
                target_language=normalized_target_language,
            )
        except Exception as exc:  # pragma: no cover - depends on provider runtime
            logger.warning(
                "Translation request failed for %s items to %s: %s",
                len(grouped_items),
                normalized_target_language,
                exc,
            )
            continue

        for item, translated_value in zip(grouped_items, translated_values):
            cache_key = item.cache_key
            results[cache_key] = translated_value
            updates.append(
                {
                    "item": item,
                    "source_hash": source_hashes[cache_key],
                    "translated_value": translated_value,
                }
            )

    if not updates:
        return results

    with transaction.atomic():
        for update in updates:
            item = update["item"]
            translated_value = update["translated_value"]
            _cache_model().objects.update_or_create(
                entity_type=item.entity_type,
                entity_id=item.entity_id,
                field_name=item.field_name,
                target_language=normalized_target_language,
                defaults={
                    "source_language": translated_value.source_language or "",
                    "source_hash": update["source_hash"],
                    "translated_text": translated_value.translated_text,
                    "provider": translated_value.provider
                    or getattr(settings, "TRANSLATION_PROVIDER", "google"),
                },
            )

    return results


def _translation_from_map(translations, *, entity_type, entity_id, field_name):
    return translations.get((entity_type, int(entity_id), field_name))


def localize_part_request_payloads(payloads, *, target_language):
    normalized_target_language = normalize_language_code(target_language)
    valid_payloads = [payload for payload in payloads if isinstance(payload, dict)]
    items = []

    for payload in valid_payloads:
        payload["translation_target_language"] = normalized_target_language
        entity_id = payload.get("id")
        if entity_id in (None, ""):
            payload.setdefault("translated_title", None)
            if "description" in payload:
                payload.setdefault("translated_description", None)
            continue

        title = str(payload.get("title", "") or "")
        payload["title_language"] = normalize_language_code(payload.get("title_language")) or (
            detect_source_language(title) or payload.get("title_language") or None
        )
        items.append(
            TextTranslationItem(
                entity_type="part_request",
                entity_id=int(entity_id),
                field_name="title",
                source_text=title,
                source_language=payload.get("title_language"),
            )
        )

        if "description" in payload:
            description = str(payload.get("description", "") or "")
            payload["description_language"] = normalize_language_code(
                payload.get("description_language")
            ) or (detect_source_language(description) or payload.get("description_language") or None)
            items.append(
                TextTranslationItem(
                    entity_type="part_request",
                    entity_id=int(entity_id),
                    field_name="description",
                    source_text=description,
                    source_language=payload.get("description_language"),
                )
            )

    translations = translate_text_items(items, normalized_target_language)

    for payload in valid_payloads:
        entity_id = payload.get("id")
        if entity_id in (None, ""):
            continue

        title_translation = _translation_from_map(
            translations,
            entity_type="part_request",
            entity_id=entity_id,
            field_name="title",
        )
        payload["translated_title"] = (
            title_translation.translated_text if title_translation else None
        )

        if "description" in payload:
            description_translation = _translation_from_map(
                translations,
                entity_type="part_request",
                entity_id=entity_id,
                field_name="description",
            )
            payload["translated_description"] = (
                description_translation.translated_text
                if description_translation
                else None
            )

    return valid_payloads


def localize_message_payloads(payloads, *, target_language):
    normalized_target_language = normalize_language_code(target_language)
    valid_payloads = [payload for payload in payloads if isinstance(payload, dict)]
    items = []
    product_payloads = []
    reply_payloads = []

    for payload in valid_payloads:
        payload["translation_target_language"] = normalized_target_language
        entity_id = payload.get("id")
        if entity_id not in (None, ""):
            text = str(payload.get("text", "") or "")
            payload["text_language"] = normalize_language_code(payload.get("text_language")) or (
                detect_source_language(text) or payload.get("text_language") or None
            )
            items.append(
                TextTranslationItem(
                    entity_type="message",
                    entity_id=int(entity_id),
                    field_name="text",
                    source_text=text,
                    source_language=payload.get("text_language"),
                )
            )

        product = payload.get("product")
        if isinstance(product, dict):
            product_payloads.append(product)

        reply_to = payload.get("reply_to")
        if isinstance(reply_to, dict):
            reply_payloads.append(reply_to)

    translations = translate_text_items(items, normalized_target_language)

    for payload in valid_payloads:
        entity_id = payload.get("id")
        if entity_id in (None, ""):
            payload.setdefault("translated_text", None)
            continue

        text_translation = _translation_from_map(
            translations,
            entity_type="message",
            entity_id=entity_id,
            field_name="text",
        )
        payload["translated_text"] = (
            text_translation.translated_text if text_translation else None
        )

    if product_payloads:
        localize_part_request_payloads(product_payloads, target_language=normalized_target_language)
    if reply_payloads:
        localize_message_payloads(reply_payloads, target_language=normalized_target_language)

    return valid_payloads


def localize_part_request_response_data(data, *, target_language):
    payloads = _extract_payloads(data)
    if not payloads:
        return data
    localize_part_request_payloads(payloads, target_language=target_language)
    return data


def localize_message_response_data(data, *, target_language):
    payloads = _extract_payloads(data)
    if not payloads:
        return data
    localize_message_payloads(payloads, target_language=target_language)
    return data


def localize_conversation_response_data(data, *, target_language):
    payloads = _extract_payloads(data)
    last_messages = []
    for payload in payloads:
        last_message = payload.get("last_message")
        if isinstance(last_message, dict):
            last_messages.append(last_message)
    if last_messages:
        localize_message_payloads(last_messages, target_language=target_language)
    return data


def _extract_payloads(data):
    if isinstance(data, dict):
        results = data.get("results")
        if isinstance(results, list):
            return [item for item in results if isinstance(item, dict)]
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []
