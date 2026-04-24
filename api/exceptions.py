from collections.abc import Mapping, Sequence

from rest_framework.settings import api_settings
from rest_framework.views import exception_handler


DEFAULT_STATUS_MESSAGES = {
    400: "Please check the submitted data and try again.",
    401: "Authentication failed. Please sign in and try again.",
    403: "You do not have permission to perform this action.",
    404: "The requested resource was not found.",
    405: "This action is not allowed for this endpoint.",
    415: "The uploaded content type is not supported.",
    429: "Too many requests. Please try again later.",
    500: "Something went wrong on the server. Please try again later.",
}

DETAIL_MESSAGE_MAP = {
    "authentication credentials were not provided.": "You need to sign in to continue.",
    "you do not have permission to perform this action.": "You do not have permission to perform this action.",
    "not found.": "The requested resource was not found.",
}


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return response

    message = _build_error_message(response.data, response.status_code)

    if isinstance(response.data, Mapping):
        normalized = dict(response.data)
        detail = normalized.get("detail")
        detail_code = getattr(detail, "code", None)
        if detail_code and "code" not in normalized:
            normalized["code"] = detail_code
        normalized["status_code"] = response.status_code
        normalized["message"] = message
        response.data = normalized
        return response

    response.data = {
        "status_code": response.status_code,
        "message": message,
        "errors": response.data,
    }
    return response


def _build_error_message(data, status_code):
    if isinstance(data, Mapping):
        detail = data.get("detail")
        if detail is not None:
            message = _normalize_message(detail)
            if message:
                return _humanize_detail_message(message, status_code)

        field_message = _first_field_error_message(data)
        if field_message:
            return field_message
    else:
        message = _normalize_message(data)
        if message:
            return _humanize_detail_message(message, status_code)

    return DEFAULT_STATUS_MESSAGES.get(status_code, "Request failed.")


def _first_field_error_message(data):
    ignored_keys = {"message", "status_code", "code"}

    for field_name, value in data.items():
        if field_name in ignored_keys:
            continue

        message = _normalize_message(value)
        if not message:
            continue

        if field_name == api_settings.NON_FIELD_ERRORS_KEY:
            return message

        return _humanize_field_message(field_name, message)

    return None


def _humanize_field_message(field_name, message):
    clean_message = " ".join(str(message).split())
    field_label = field_name.replace("_", " ").strip()
    field_label_cap = field_label.capitalize()

    if clean_message.lower() == f"{field_name.lower()} is required.":
        return f"{field_label_cap} is required."

    if clean_message.lower().startswith(f"{field_name.lower()} "):
        return f"{field_label_cap}{clean_message[len(field_name):]}"

    if clean_message.lower().startswith(
        ("this field", "ensure", "enter", "a valid", "invalid")
    ):
        return f"{field_label_cap}: {clean_message}"

    return clean_message


def _humanize_detail_message(message, status_code):
    clean_message = " ".join(str(message).split())
    lower_message = clean_message.lower()

    if lower_message in DETAIL_MESSAGE_MAP:
        return DETAIL_MESSAGE_MAP[lower_message]

    if status_code == 401:
        if "token is invalid or expired" in lower_message:
            return "Your session has expired. Please sign in again."
        if "given token not valid" in lower_message:
            return "Your session is no longer valid. Please sign in again."
        if "token not valid" in lower_message:
            return "Your session is no longer valid. Please sign in again."

    return clean_message or DEFAULT_STATUS_MESSAGES.get(status_code, "Request failed.")


def _normalize_message(value):
    if isinstance(value, Mapping):
        for nested_value in value.values():
            message = _normalize_message(nested_value)
            if message:
                return message
        return ""

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        messages = [part for item in value if (part := _normalize_message(item))]
        return "; ".join(messages)

    return str(value).strip()
