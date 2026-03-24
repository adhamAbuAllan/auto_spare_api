from collections import defaultdict
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from . import runtime


class FakeRedisClient:
    def __init__(self, *, fail_ping=False):
        self.fail_ping = fail_ping
        self.sorted_sets = defaultdict(dict)
        self.expirations = {}

    def ping(self):
        if self.fail_ping:
            raise OSError("redis down")
        return True

    def close(self):
        return None

    def expire(self, key, ttl_seconds):
        self.expirations[key] = int(ttl_seconds)
        return True

    def zadd(self, key, mapping):
        bucket = self.sorted_sets[key]
        for member, score in mapping.items():
            bucket[str(member)] = float(score)
        return len(mapping)

    def zrem(self, key, *members):
        bucket = self.sorted_sets.get(key, {})
        removed = 0
        for member in members:
            if str(member) in bucket:
                removed += 1
                bucket.pop(str(member), None)
        return removed

    def zremrangebyscore(self, key, minimum, maximum):
        bucket = self.sorted_sets.get(key, {})
        if minimum == "-inf":
            minimum_value = float("-inf")
        else:
            minimum_value = float(minimum)
        maximum_value = float(maximum)
        expired = [
            member
            for member, score in bucket.items()
            if minimum_value <= float(score) <= maximum_value
        ]
        for member in expired:
            bucket.pop(member, None)
        return len(expired)

    def zrange(self, key, start, end):
        bucket = self.sorted_sets.get(key, {})
        ordered = [member for member, _ in sorted(bucket.items(), key=lambda item: (item[1], item[0]))]
        if end == -1:
            return ordered[start:]
        return ordered[start : end + 1]


class FakeRedisFactory:
    def __init__(self, clients):
        self.clients = list(clients)
        self.calls = 0

    def __call__(self, **kwargs):
        client = self.clients[min(self.calls, len(self.clients) - 1)]
        self.calls += 1
        return client


@override_settings(
    CHANNEL_LAYER_BACKEND="memory",
    CHAT_PRESENCE_TTL_SECONDS=75,
    CHAT_TYPING_TTL_SECONDS=8,
)
class RuntimeMemoryTests(SimpleTestCase):
    def setUp(self):
        runtime.reset_runtime_state()

    def tearDown(self):
        runtime.reset_runtime_state()
        super().tearDown()

    def test_presence_expires_without_disconnect(self):
        runtime.add_connected_user(1, 10, "conn-a", now=0)

        self.assertEqual(runtime.get_connected_user_ids(1, now=74), {10})
        self.assertEqual(runtime.get_connected_user_ids(1, now=76), set())

    def test_typing_expires_without_disconnect(self):
        runtime.set_typing_state(1, 10, "conn-a", True, now=0)

        self.assertEqual(runtime.get_typing_user_ids(1, now=7), {10})
        self.assertEqual(runtime.get_typing_user_ids(1, now=9), set())

    def test_multi_tab_presence_keeps_user_connected(self):
        runtime.add_connected_user(1, 10, "conn-a", now=0)
        runtime.add_connected_user(1, 10, "conn-b", now=10)

        self.assertEqual(runtime.get_connected_user_ids(1, now=74), {10})
        self.assertEqual(runtime.get_connected_user_ids(1, now=76), {10})
        self.assertEqual(runtime.get_connected_user_ids(1, now=86), set())


@override_settings(
    CHANNEL_LAYER_BACKEND="redis",
    REDIS_HOST="127.0.0.1",
    REDIS_PORT=6379,
    CHAT_PRESENCE_TTL_SECONDS=75,
    CHAT_TYPING_TTL_SECONDS=8,
)
class RuntimeRedisTests(SimpleTestCase):
    def setUp(self):
        runtime.reset_runtime_state()

    def tearDown(self):
        runtime.reset_runtime_state()
        super().tearDown()

    def test_redis_runtime_retries_after_failure_and_recovers(self):
        factory = FakeRedisFactory(
            [
                FakeRedisClient(fail_ping=True),
                FakeRedisClient(),
            ]
        )

        with mock.patch.object(runtime, "redis", SimpleNamespace(Redis=factory)):
            self.assertIsNone(runtime.get_connected_user_ids(1, now=0))
            self.assertEqual(factory.calls, 1)

            self.assertIsNone(runtime.get_connected_user_ids(1, now=1))
            self.assertEqual(factory.calls, 1)

            self.assertTrue(runtime.add_connected_user(1, 22, "conn-a", now=4))
            self.assertEqual(factory.calls, 2)
            self.assertEqual(runtime.get_connected_user_ids(1, now=4), {22})

    def test_unavailable_redis_does_not_fall_back_to_memory_state(self):
        factory = FakeRedisFactory([FakeRedisClient(fail_ping=True)])

        with mock.patch.object(runtime, "redis", SimpleNamespace(Redis=factory)):
            self.assertFalse(runtime.add_connected_user(1, 77, "conn-a", now=0))
            self.assertIsNone(runtime.get_connected_user_ids(1, now=0))
            self.assertEqual(
                runtime.get_conversation_runtime_state(1, now=0),
                {
                    "conversation_id": 1,
                    "connected_user_ids": [],
                    "typing_user_ids": [],
                },
            )
