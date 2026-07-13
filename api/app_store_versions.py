"""Read the publicly published version of the mobile applications.

The values are cached because app-update checks are made by every installed
application.  A lookup failure deliberately falls back to the configured
version, so a temporary store or network problem never breaks the API.
"""

import json
import logging
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache


logger = logging.getLogger(__name__)

_APPLE_LOOKUP_URL = "https://itunes.apple.com/lookup"
_PLAY_STORE_URL = "https://play.google.com/store/apps/details"
_PLAY_VERSION_PATTERN = re.compile(r'"141":\[\[\["([^"\\]+)"\]\]')


def _fetch(url):
    request = Request(url, headers={"User-Agent": "MTA-App-Update-Checker/1.0"})
    with urlopen(request, timeout=settings.APP_UPDATE_STORE_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8")


def _apple_version():
    app_id = settings.APP_UPDATE_IOS_APP_ID
    if not app_id:
        return None

    query = urlencode({"id": app_id, "country": settings.APP_UPDATE_IOS_STORE_COUNTRY})
    payload = json.loads(_fetch(f"{_APPLE_LOOKUP_URL}?{query}"))
    results = payload.get("results", [])
    if not results:
        return None
    version = str(results[0].get("version", "")).strip()
    return version or None


def _android_version():
    package_name = settings.APP_UPDATE_ANDROID_PACKAGE_NAME
    if not package_name:
        return None

    query = urlencode(
        {
            "id": package_name,
            "hl": settings.APP_UPDATE_ANDROID_STORE_LANGUAGE,
            "gl": settings.APP_UPDATE_ANDROID_STORE_COUNTRY,
        }
    )
    match = _PLAY_VERSION_PATTERN.search(_fetch(f"{_PLAY_STORE_URL}?{query}"))
    return match.group(1).strip() if match else None


def _cached_version(platform, fallback):
    if not settings.APP_UPDATE_STORE_SYNC_ENABLED:
        return fallback

    cache_key = f"app-update:store-version:{platform}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    loader = _apple_version if platform == "ios" else _android_version
    try:
        version = loader()
    except Exception:  # Store lookups must never make the update endpoint fail.
        logger.warning("Could not fetch the %s store version.", platform)
        return fallback

    if version:
        cache.set(cache_key, version, settings.APP_UPDATE_STORE_CACHE_SECONDS)
        return version
    return fallback


def latest_store_version(platform, fallback=""):
    """Return the published store version, falling back to the env setting."""
    return _cached_version(platform, fallback)
