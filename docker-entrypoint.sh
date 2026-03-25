#!/bin/sh
set -e

python - <<'PY'
import os
import socket
import sys
import time


def wait_for_service(name, host, port, timeout=60):
    deadline = time.time() + timeout
    while True:
        try:
            with socket.create_connection((host, port), timeout=2):
                print(f"{name} is reachable at {host}:{port}")
                return
        except OSError as exc:
            if time.time() >= deadline:
                print(f"Timed out waiting for {name} at {host}:{port}: {exc}", file=sys.stderr)
                sys.exit(1)
            print(f"Waiting for {name} at {host}:{port}...")
            time.sleep(2)


db_engine = os.getenv("DB_ENGINE", "sqlite").strip().lower()
if db_engine in {"postgres", "postgresql"}:
    wait_for_service(
        "PostgreSQL",
        os.getenv("DB_HOST", "db"),
        int(os.getenv("DB_PORT", "5432")),
    )

channel_backend = os.getenv("CHANNEL_LAYER_BACKEND", "redis").strip().lower()
if channel_backend not in {"memory", "inmemory"}:
    wait_for_service(
        "Redis",
        os.getenv("REDIS_HOST", "redis"),
        int(os.getenv("REDIS_PORT", "6379")),
    )
PY

python manage.py migrate --noinput
exec daphne -b 0.0.0.0 -p 8000 config.asgi:application
