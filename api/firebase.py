import json
import logging
from pathlib import Path

from django.conf import settings

logger = logging.getLogger("api.firebase")

try:
    import firebase_admin
    from firebase_admin import auth as firebase_auth
    from firebase_admin import credentials
except ImportError:  # pragma: no cover - dependency is optional at import time
    firebase_admin = None
    firebase_auth = None
    credentials = None


class FirebaseAuthUnavailable(RuntimeError):
    pass


class FirebaseTokenVerificationError(RuntimeError):
    pass


_FIREBASE_APP = None
_MISSING_SDK_LOGGED = False
_MISSING_SETTINGS_LOGGED = False
_MISSING_FILE_LOGGED = False


def _resolve_service_account_path():
    configured = str(getattr(settings, "FCM_SERVICE_ACCOUNT_FILE", "") or "").strip()
    if not configured:
        return None

    path = Path(configured)
    if not path.is_absolute():
        path = Path(settings.BASE_DIR) / path
    return path


def get_firebase_app():
    global _FIREBASE_APP, _MISSING_SDK_LOGGED, _MISSING_SETTINGS_LOGGED, _MISSING_FILE_LOGGED

    if _FIREBASE_APP is not None:
        return _FIREBASE_APP

    if firebase_admin is None or credentials is None:
        if not _MISSING_SDK_LOGGED:
            logger.info("Firebase Admin SDK is not installed.")
            _MISSING_SDK_LOGGED = True
        return None

    # Try raw JSON setting first
    raw_json = str(getattr(settings, "FCM_SERVICE_ACCOUNT_JSON", "") or "").strip()
    if raw_json:
        try:
            _FIREBASE_APP = firebase_admin.get_app()
        except ValueError:
            try:
                cert_dict = json.loads(raw_json)
                _FIREBASE_APP = firebase_admin.initialize_app(
                    credentials.Certificate(cert_dict)
                )
                logger.info("Firebase Admin SDK initialized using raw JSON string.")
            except Exception as exc:
                logger.error("Failed to initialize Firebase using FCM_SERVICE_ACCOUNT_JSON: %s", exc)
                return None
        return _FIREBASE_APP

    service_account_path = _resolve_service_account_path()
    if service_account_path is None:
        if not _MISSING_SETTINGS_LOGGED:
            logger.info("FCM_SERVICE_ACCOUNT_FILE is not configured.")
            _MISSING_SETTINGS_LOGGED = True
        return None

    if not service_account_path.exists():
        if not _MISSING_FILE_LOGGED:
            logger.warning("Firebase service account file was not found at %s.", service_account_path)
            _MISSING_FILE_LOGGED = True
        return None

    try:
        _FIREBASE_APP = firebase_admin.get_app()
    except ValueError:
        _FIREBASE_APP = firebase_admin.initialize_app(
            credentials.Certificate(str(service_account_path))
        )
        logger.info("Firebase Admin SDK initialized using %s.", service_account_path)

    return _FIREBASE_APP


def verify_firebase_id_token(id_token):
    normalized_token = str(id_token or "").strip()
    if not normalized_token:
        raise FirebaseTokenVerificationError("Firebase ID token is required.")

    app = get_firebase_app()
    if app is None or firebase_auth is None:
        raise FirebaseAuthUnavailable("Firebase Admin SDK is not configured.")

    try:
        return firebase_auth.verify_id_token(normalized_token, app=app)
    except Exception as exc:  # pragma: no cover - exact exceptions depend on Firebase runtime
        raise FirebaseTokenVerificationError("Firebase ID token is invalid.") from exc
