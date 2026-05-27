from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET


def _proxy_error(message: str, status: int) -> JsonResponse:
    return JsonResponse({"detail": message}, status=status)


@require_GET
def car_images_api_proxy(request, path: str) -> HttpResponse:
    if not getattr(settings, "CAR_IMAGES_API_PROXY_ENABLED", False):
        return _proxy_error("Car images API proxy is disabled.", 404)

    configured_token = getattr(settings, "CAR_IMAGES_API_PROXY_TOKEN", "")
    if configured_token:
        supplied_token = request.headers.get("X-Car-Images-Proxy-Token", "")
        if supplied_token != configured_token:
            return _proxy_error("Invalid car images API proxy token.", 403)

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

    upstream_request = Request(
        target_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "auto-spare-api-ngrok-proxy/1.0",
        },
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
