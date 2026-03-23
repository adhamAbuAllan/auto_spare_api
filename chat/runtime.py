import os
import threading
from collections import defaultdict

from django.conf import settings

try:
    import redis
except Exception:  # pragma: no cover - import failure fallback
    redis = None


_redis_client = None
_redis_failed = False
_memory_presence = defaultdict(lambda: defaultdict(set))
_memory_typing = defaultdict(lambda: defaultdict(set))
_memory_lock = threading.Lock()


def _redis_enabled():
    backend = getattr(settings, "CHANNEL_LAYER_BACKEND", "redis")
    return str(backend).strip().lower() not in {"memory", "inmemory"}


def _get_redis_client():
    global _redis_client, _redis_failed

    if _redis_client is not None:
        return _redis_client
    if _redis_failed or redis is None or not _redis_enabled():
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
        return _redis_client
    except Exception:
        _redis_failed = True
        return None


def _presence_key(conversation_id):
    return f"chat:presence:{conversation_id}"


def _presence_user_key(conversation_id, user_id):
    return f"{_presence_key(conversation_id)}:user:{user_id}"


def _typing_key(conversation_id):
    return f"chat:typing:{conversation_id}"


def _typing_user_key(conversation_id, user_id):
    return f"{_typing_key(conversation_id)}:user:{user_id}"


def add_connected_user(conversation_id, user_id, connection_id):
    conversation_id = int(conversation_id)
    user_id = int(user_id)
    connection_id = str(connection_id)
    client = _get_redis_client()
    if client:
        user_key = _presence_user_key(conversation_id, user_id)
        client.sadd(user_key, connection_id)
        client.sadd(_presence_key(conversation_id), user_id)
        return

    with _memory_lock:
        _memory_presence[conversation_id][user_id].add(connection_id)


def remove_connected_user(conversation_id, user_id, connection_id):
    conversation_id = int(conversation_id)
    user_id = int(user_id)
    connection_id = str(connection_id)
    client = _get_redis_client()
    if client:
        user_key = _presence_user_key(conversation_id, user_id)
        client.srem(user_key, connection_id)
        if client.scard(user_key) == 0:
            client.delete(user_key)
            client.srem(_presence_key(conversation_id), user_id)
        return

    with _memory_lock:
        conversation_presence = _memory_presence.get(conversation_id)
        if not conversation_presence:
            return
        user_connections = conversation_presence.get(user_id)
        if not user_connections:
            return
        user_connections.discard(connection_id)
        if not user_connections:
            conversation_presence.pop(user_id, None)
        if not conversation_presence:
            _memory_presence.pop(conversation_id, None)


def get_connected_user_ids(conversation_id):
    conversation_id = int(conversation_id)
    client = _get_redis_client()
    if client:
        connected_users = set()
        for raw_user_id in client.smembers(_presence_key(conversation_id)):
            if not str(raw_user_id).strip():
                continue
            user_id = int(raw_user_id)
            if client.scard(_presence_user_key(conversation_id, user_id)) > 0:
                connected_users.add(user_id)
            else:
                client.srem(_presence_key(conversation_id), user_id)
        return connected_users

    with _memory_lock:
        return {
            user_id
            for user_id, connections in _memory_presence.get(conversation_id, {}).items()
            if connections
        }


def get_typing_user_ids(conversation_id):
    conversation_id = int(conversation_id)
    client = _get_redis_client()
    if client:
        typing_users = set()
        for raw_user_id in client.smembers(_typing_key(conversation_id)):
            if not str(raw_user_id).strip():
                continue
            user_id = int(raw_user_id)
            if client.scard(_typing_user_key(conversation_id, user_id)) > 0:
                typing_users.add(user_id)
            else:
                client.srem(_typing_key(conversation_id), user_id)
                client.delete(_typing_user_key(conversation_id, user_id))
        return typing_users

    with _memory_lock:
        return {
            user_id
            for user_id, connections in _memory_typing.get(conversation_id, {}).items()
            if connections
        }


def get_conversation_runtime_state(conversation_id):
    conversation_id = int(conversation_id)
    return {
        "conversation_id": conversation_id,
        "connected_user_ids": sorted(get_connected_user_ids(conversation_id)),
        "typing_user_ids": sorted(get_typing_user_ids(conversation_id)),
    }


def set_typing_state(conversation_id, user_id, connection_id, is_typing):
    conversation_id = int(conversation_id)
    user_id = int(user_id)
    connection_id = str(connection_id)
    client = _get_redis_client()
    if client:
        key = _typing_user_key(conversation_id, user_id)
        was_typing = client.scard(key) > 0
        if is_typing:
            client.sadd(key, connection_id)
        else:
            client.srem(key, connection_id)
        is_typing_now = client.scard(key) > 0
        if is_typing_now:
            client.sadd(_typing_key(conversation_id), user_id)
        else:
            client.delete(key)
            client.srem(_typing_key(conversation_id), user_id)
    else:
        with _memory_lock:
            conversation_typing = _memory_typing[conversation_id]
            user_connections = conversation_typing[user_id]
            was_typing = bool(user_connections)
            if is_typing:
                user_connections.add(connection_id)
            else:
                user_connections.discard(connection_id)
            is_typing_now = bool(user_connections)
            if not user_connections:
                conversation_typing.pop(user_id, None)
            if not conversation_typing:
                _memory_typing.pop(conversation_id, None)

    return {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "is_typing": is_typing_now,
        "changed": was_typing != is_typing_now,
    }
