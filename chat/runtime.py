import logging
import os
import threading
import time
from collections import defaultdict

from django.conf import settings

try:
    import redis
except Exception:  # pragma: no cover - import failure fallback
    redis = None


logger = logging.getLogger(__name__)

_REDIS_RETRY_DELAY_SECONDS = 3
_redis_client = None
_redis_next_retry_at = 0.0
_memory_presence = defaultdict(dict)
_memory_typing = defaultdict(dict)
_memory_lock = threading.Lock()


def reset_runtime_state():
    global _redis_client, _redis_next_retry_at
    _redis_client = None
    _redis_next_retry_at = 0.0
    with _memory_lock:
        _memory_presence.clear()
        _memory_typing.clear()


def _runtime_backend():
    return str(getattr(settings, "CHANNEL_LAYER_BACKEND", "redis")).strip().lower()


def _memory_enabled():
    return _runtime_backend() in {"memory", "inmemory"}


def _presence_ttl_seconds():
    return int(getattr(settings, "CHAT_PRESENCE_TTL_SECONDS", 75))


def _typing_ttl_seconds():
    return int(getattr(settings, "CHAT_TYPING_TTL_SECONDS", 8))


def _now_ts(now=None):
    return time.time() if now is None else float(now)


def _member(user_id, connection_id):
    return f"{int(user_id)}:{str(connection_id)}"


def _parse_member(raw_member):
    try:
        raw_member = str(raw_member)
        user_id, connection_id = raw_member.split(":", 1)
        return int(user_id), connection_id
    except (TypeError, ValueError):
        return None


def _presence_key(conversation_id):
    return f"chat:presence:{int(conversation_id)}"


def _typing_key(conversation_id):
    return f"chat:typing:{int(conversation_id)}"


def _mark_redis_failure(exc, *, now=None):
    global _redis_client, _redis_next_retry_at

    current = _now_ts(now)
    if _redis_client is not None:
        try:
            _redis_client.close()
        except Exception:
            pass
    _redis_client = None
    _redis_next_retry_at = current + _REDIS_RETRY_DELAY_SECONDS
    logger.warning("Chat runtime Redis unavailable: %s", exc)


def _get_redis_client(now=None):
    global _redis_client, _redis_next_retry_at

    if _memory_enabled():
        return None
    if redis is None:
        return None

    current = _now_ts(now)
    if _redis_client is not None:
        return _redis_client
    if current < _redis_next_retry_at:
        return None

    try:
        _redis_client = redis.Redis(
            host=getattr(settings, "REDIS_HOST", "127.0.0.1"),
            port=int(getattr(settings, "REDIS_PORT", 6379)),
            db=int(os.getenv("REDIS_DB", "0")),
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        _redis_client.ping()
        _redis_next_retry_at = 0.0
        return _redis_client
    except Exception as exc:
        _mark_redis_failure(exc, now=current)
        return None


def _touch_key_expiry(client, key, ttl_seconds):
    client.expire(key, max(int(ttl_seconds) * 2, int(ttl_seconds) + 5))


def _get_active_redis_members(client, key, *, now=None):
    current = _now_ts(now)
    client.zremrangebyscore(key, "-inf", current)
    return client.zrange(key, 0, -1)


def _prune_memory_bucket(bucket, conversation_id, *, now=None):
    current = _now_ts(now)
    conversation_bucket = bucket.get(int(conversation_id), {})
    expired_ids = [
        connection_id
        for connection_id, (_, expires_at) in conversation_bucket.items()
        if expires_at <= current
    ]
    for connection_id in expired_ids:
        conversation_bucket.pop(connection_id, None)
    if not conversation_bucket:
        bucket.pop(int(conversation_id), None)
    return conversation_bucket


def _active_memory_user_ids(bucket, conversation_id, *, now=None):
    conversation_bucket = _prune_memory_bucket(bucket, conversation_id, now=now)
    return {user_id for user_id, _ in conversation_bucket.values()}


def add_connected_user(conversation_id, user_id, connection_id, *, now=None):
    conversation_id = int(conversation_id)
    user_id = int(user_id)
    connection_id = str(connection_id)
    current = _now_ts(now)
    expires_at = current + _presence_ttl_seconds()

    if _memory_enabled():
        with _memory_lock:
            _memory_presence[conversation_id][connection_id] = (user_id, expires_at)
        return True

    client = _get_redis_client(now=current)
    if not client:
        return False

    try:
        key = _presence_key(conversation_id)
        client.zadd(key, {_member(user_id, connection_id): expires_at})
        _touch_key_expiry(client, key, _presence_ttl_seconds())
        return True
    except Exception as exc:
        _mark_redis_failure(exc, now=current)
        return False


def remove_connected_user(conversation_id, user_id, connection_id):
    conversation_id = int(conversation_id)
    connection_id = str(connection_id)
    member = _member(user_id, connection_id)

    if _memory_enabled():
        with _memory_lock:
            conversation_presence = _memory_presence.get(conversation_id)
            if conversation_presence is not None:
                conversation_presence.pop(connection_id, None)
                if not conversation_presence:
                    _memory_presence.pop(conversation_id, None)

            conversation_typing = _memory_typing.get(conversation_id)
            if conversation_typing is not None:
                conversation_typing.pop(connection_id, None)
                if not conversation_typing:
                    _memory_typing.pop(conversation_id, None)
        return True

    client = _get_redis_client()
    if not client:
        return False

    try:
        client.zrem(_presence_key(conversation_id), member)
        client.zrem(_typing_key(conversation_id), member)
        return True
    except Exception as exc:
        _mark_redis_failure(exc)
        return False


def get_connected_user_ids(conversation_id, *, now=None):
    conversation_id = int(conversation_id)

    if _memory_enabled():
        with _memory_lock:
            return _active_memory_user_ids(_memory_presence, conversation_id, now=now)

    client = _get_redis_client(now=now)
    if not client:
        return None

    try:
        members = _get_active_redis_members(client, _presence_key(conversation_id), now=now)
        return {
            parsed[0]
            for parsed in (_parse_member(member) for member in members)
            if parsed is not None
        }
    except Exception as exc:
        _mark_redis_failure(exc, now=now)
        return None


def get_typing_user_ids(conversation_id, *, now=None):
    conversation_id = int(conversation_id)

    if _memory_enabled():
        with _memory_lock:
            return _active_memory_user_ids(_memory_typing, conversation_id, now=now)

    client = _get_redis_client(now=now)
    if not client:
        return None

    try:
        members = _get_active_redis_members(client, _typing_key(conversation_id), now=now)
        return {
            parsed[0]
            for parsed in (_parse_member(member) for member in members)
            if parsed is not None
        }
    except Exception as exc:
        _mark_redis_failure(exc, now=now)
        return None


def get_conversation_runtime_state(conversation_id, *, now=None):
    conversation_id = int(conversation_id)
    connected_user_ids = get_connected_user_ids(conversation_id, now=now) or set()
    typing_user_ids = get_typing_user_ids(conversation_id, now=now) or set()
    return {
        "conversation_id": conversation_id,
        "connected_user_ids": sorted(connected_user_ids),
        "typing_user_ids": sorted(typing_user_ids),
    }


def set_typing_state(conversation_id, user_id, connection_id, is_typing, *, now=None):
    conversation_id = int(conversation_id)
    user_id = int(user_id)
    connection_id = str(connection_id)
    current = _now_ts(now)
    member = _member(user_id, connection_id)

    if _memory_enabled():
        with _memory_lock:
            before_users = _active_memory_user_ids(_memory_typing, conversation_id, now=current)
            conversation_typing = _memory_typing[conversation_id]
            if is_typing:
                conversation_typing[connection_id] = (
                    user_id,
                    current + _typing_ttl_seconds(),
                )
            else:
                conversation_typing.pop(connection_id, None)
                if not conversation_typing:
                    _memory_typing.pop(conversation_id, None)
            after_users = _active_memory_user_ids(_memory_typing, conversation_id, now=current)

        was_typing = user_id in before_users
        is_typing_now = user_id in after_users
        return {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "is_typing": is_typing_now,
            "changed": was_typing != is_typing_now,
        }

    client = _get_redis_client(now=current)
    if not client:
        return {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "is_typing": False,
            "changed": False,
        }

    try:
        key = _typing_key(conversation_id)
        before_users = {
            parsed[0]
            for parsed in (
                _parse_member(member_value)
                for member_value in _get_active_redis_members(client, key, now=current)
            )
            if parsed is not None
        }
        if is_typing:
            client.zadd(key, {member: current + _typing_ttl_seconds()})
            _touch_key_expiry(client, key, _typing_ttl_seconds())
        else:
            client.zrem(key, member)
        after_users = {
            parsed[0]
            for parsed in (
                _parse_member(member_value)
                for member_value in _get_active_redis_members(client, key, now=current)
            )
            if parsed is not None
        }
        was_typing = user_id in before_users
        is_typing_now = user_id in after_users
        return {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "is_typing": is_typing_now,
            "changed": was_typing != is_typing_now,
        }
    except Exception as exc:
        _mark_redis_failure(exc, now=current)
        return {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "is_typing": False,
            "changed": False,
        }
