from urllib.parse import parse_qs

from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from asgiref.sync import sync_to_async
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from api.translation import normalize_language_code


@sync_to_async
def authenticate_token(raw_token):
    authentication = JWTAuthentication()
    try:
        validated_token = authentication.get_validated_token(raw_token)
        return authentication.get_user(validated_token), None
    except (InvalidToken, TokenError, AuthenticationFailed) as exc:
        return AnonymousUser(), {
            "code": exc.__class__.__name__,
            "detail": str(exc),
        }
    except Exception as exc:  # pragma: no cover - defensive fallback
        return AnonymousUser(), {
            "code": exc.__class__.__name__,
            "detail": str(exc),
        }


class JwtAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        query_string = parse_qs(scope["query_string"].decode())
        scope["auth_error"] = None
        scope["translation_language"] = normalize_language_code(
            (query_string.get("lang") or [None])[0]
        )

        token = query_string.get("token")
        if token:
            scope["user"], scope["auth_error"] = await authenticate_token(token[0])
        else:
            scope["user"] = AnonymousUser()
            scope["auth_error"] = {
                "code": "MissingToken",
                "detail": "JWT access token is required in the token query parameter.",
            }

        return await super().__call__(scope, receive, send)


def JwtAuthMiddlewareStack(inner):
    return JwtAuthMiddleware(inner)
