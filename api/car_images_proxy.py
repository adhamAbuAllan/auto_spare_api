from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET

from .mock_catalog_data import MOCK_CAR_CATALOG, MOCK_CAR_IMAGE_URL


def _proxy_error(message: str, status: int) -> JsonResponse:
    return JsonResponse({"detail": message}, status=status)


def _serve_mock_proxy_response(path: str) -> HttpResponse:
    parts = [part.strip().lower() for part in path.split("/") if part.strip()]

    # 1. /makes
    if len(parts) == 1 and parts[0] == "makes":
        from django.utils.text import slugify
        data = [{"name": name, "slug": slugify(name)} for name in MOCK_CAR_CATALOG.keys()]
        return JsonResponse({"data": data})

    # 2. /makes/<make>/models
    if len(parts) == 3 and parts[0] == "makes" and parts[2] == "models":
        from django.utils.text import slugify
        make_slug = parts[1]
        for make_name, model_names in MOCK_CAR_CATALOG.items():
            if slugify(make_name) == make_slug:
                data = [{"name": name, "slug": slugify(name)} for name in model_names]
                return JsonResponse({"data": data})
        return JsonResponse({"data": []})

    # 3. /makes/<make>/models/<model>
    if len(parts) == 4 and parts[0] == "makes" and parts[2] == "models":
        from django.utils.text import slugify
        make_slug = parts[1]
        model_slug = parts[3]

        make_name_matched = None
        model_name_matched = None
        for make_name, model_names in MOCK_CAR_CATALOG.items():
            if slugify(make_name) == make_slug:
                make_name_matched = make_name
                for m in model_names:
                    if slugify(m) == model_slug:
                        model_name_matched = m
                        break
            if make_name_matched:
                break

        if not make_name_matched or not model_name_matched:
            return _proxy_error(f"Model not found: {make_slug}/{model_slug}", 404)

        return JsonResponse({
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
        })

    return _proxy_error("Not found", 404)


@require_GET
def car_images_api_proxy(request, path: str) -> HttpResponse:
    if not getattr(settings, "CAR_IMAGES_API_PROXY_ENABLED", False):
        return _proxy_error("Car images API proxy is disabled.", 404)

    configured_token = getattr(settings, "CAR_IMAGES_API_PROXY_TOKEN", "")
    if configured_token:
        supplied_token = request.headers.get("X-Car-Images-Proxy-Token", "")
        if supplied_token != configured_token:
            return _proxy_error("Invalid car images API proxy token.", 403)

    is_mock_mode = getattr(settings, "CAR_IMAGES_API_MOCK", not getattr(settings, "CAR_IMAGES_API_KEY", "").strip())
    if is_mock_mode:
        return _serve_mock_proxy_response(path)

    normalized_path = "/".join(
        quote(part.strip(), safe="")
        for part in str(path or "").strip("/").split("/")
        if part.strip()
    )
    if not normalized_path:
        return _proxy_error("Missing car images API path.", 400)

    target_base_url = getattr(
        settings,
        "CAR_IMAGES_API_PROXY_TARGET_BASE_URL",
        "https://carimagesapi.com/api/v1",
    ).rstrip("/")
    target_url = f"{target_base_url}/{normalized_path}"
    if request.META.get("QUERY_STRING"):
        target_url = f"{target_url}?{request.META['QUERY_STRING']}"

    upstream_headers = {
        "Accept": "application/json",
        "User-Agent": "auto-spare-api-ngrok-proxy/1.0",
    }
    api_key = str(getattr(settings, "CAR_IMAGES_API_KEY", "") or "").strip()
    if api_key:
        upstream_headers["Authorization"] = f"Bearer {api_key}"

    upstream_request = Request(
        target_url,
        headers=upstream_headers,
    )
    timeout_seconds = int(getattr(settings, "CAR_IMAGES_API_TIMEOUT_SECONDS", 20))

    try:
        with urlopen(upstream_request, timeout=timeout_seconds) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type", "application/json")
            return HttpResponse(
                body,
                status=response.status,
                content_type=content_type,
            )
    except HTTPError as exc:
        error_body = exc.read()
        content_type = exc.headers.get("Content-Type", "application/json")
        return HttpResponse(error_body, status=exc.code, content_type=content_type)
    except URLError as exc:
        return _proxy_error(f"Car images API proxy request failed: {exc}", 502)
