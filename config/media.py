import mimetypes
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.utils.http import http_date


def _media_file(path):
    root = Path(settings.MEDIA_ROOT).resolve()
    requested = (root / path).resolve()
    if requested == root or root not in requested.parents or not requested.is_file():
        raise Http404
    return requested


def _parse_range(value, size):
    if not value or not value.startswith("bytes=") or "," in value:
        return None

    value = value[6:].strip()
    if "-" not in value:
        return None
    start_text, end_text = value.split("-", 1)

    try:
        if not start_text:
            suffix_length = int(end_text)
            if suffix_length <= 0:
                return None
            start = max(size - suffix_length, 0)
            end = size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
            if start < 0 or start >= size or end < start:
                return None
            end = min(end, size - 1)
    except ValueError:
        return None

    return start, end


def serve_media(request, path):
    media_file = _media_file(path)
    size = media_file.stat().st_size
    content_type = mimetypes.guess_type(media_file.name)[0] or "application/octet-stream"
    range_header = request.headers.get("Range")
    byte_range = _parse_range(range_header, size) if range_header else None

    if range_header and byte_range is None:
        response = HttpResponse(status=416)
        response["Content-Range"] = f"bytes */{size}"
        response["Accept-Ranges"] = "bytes"
        return response

    if byte_range:
        start, end = byte_range
        length = end - start + 1
        if request.method == "HEAD":
            response = HttpResponse(status=206)
        else:
            file_handle = media_file.open("rb")
            file_handle.seek(start)
            response = FileResponse(file_handle, status=206, content_type=content_type)
        response["Content-Length"] = str(length)
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
    else:
        if request.method == "HEAD":
            response = HttpResponse(status=200)
        else:
            response = FileResponse(media_file.open("rb"), content_type=content_type)
        response["Content-Length"] = str(size)

    response["Accept-Ranges"] = "bytes"
    response["Last-Modified"] = http_date(media_file.stat().st_mtime)
    response["Content-Disposition"] = f'inline; filename="{media_file.name}"'
    return response
