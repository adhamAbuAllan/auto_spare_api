# Auto Spare API

Django backend for a car-parts marketplace with REST APIs and real-time chat.

## Requirements

- Python 3
- PostgreSQL for the primary database
- Redis for Django Channels in multi-process and production environments
- Environment variables in `.env`

## Core Chat Notes

- WebSocket endpoint: `/ws/chat/<conversation_id>/?token=<jwt_access_token>`
- Incoming socket events:
  - `chat_message`
  - `ping`
  - `typing_start`
  - `typing`
  - `typing_stop`
  - `seen`
- Outgoing socket events:
  - `conversation.state`
  - `message.created`
  - `message.status`
  - `pong`
  - `conversation.typing`
  - `conversation.seen`
- Message ordering is `(client_timestamp, server_timestamp, id)`.
- Local development can use `CHANNEL_LAYER_BACKEND=memory`; production should use Redis.
- In `redis` mode, runtime state does not silently fall back to in-process memory when Redis is down.
- Production presence and typing are lease-based:
  - `CHAT_PRESENCE_TTL_SECONDS` defaults to `75`
  - `CHAT_TYPING_TTL_SECONDS` defaults to `8`
  - clients should send `ping` every `CHAT_HEARTBEAT_INTERVAL_SECONDS` seconds, default `20`

## Example `.env`

```env
SECRET_KEY=change-me
DEBUG=True
TIME_ZONE=Asia/Riyadh
DB_ENGINE=postgres
DB_NAME=auto_spare_db
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=127.0.0.1
DB_PORT=5432
CHANNEL_LAYER_BACKEND=redis
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
CHAT_PRESENCE_TTL_SECONDS=75
CHAT_TYPING_TTL_SECONDS=8
CHAT_HEARTBEAT_INTERVAL_SECONDS=20
ALLOWED_HOSTS=127.0.0.1,localhost
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

## Docker Stack

For a production-style chat setup, use:

- PostgreSQL for persisted chat data
- Redis for Channels and realtime presence/typing state
- Daphne for the ASGI websocket server

The repository now includes:

- `docker-compose.yml`
- `Dockerfile`
- `docker-entrypoint.sh`

Run the stack with:

```bash
docker compose up --build
```

The container startup will:

1. Wait for PostgreSQL and Redis.
2. Run migrations.
3. Start Daphne on port `8000`.

The web service uses the health endpoint at `/api/health/`, and the websocket/chat runtime uses Redis when `CHANNEL_LAYER_BACKEND=redis`.

## Tests

```bash
python manage.py test api chat
```

## Manual Chat Testing

- The browser tester in `chat/testing/chat_test.html` now sends heartbeat `ping` events automatically while connected.
- Typing refresh is also automatic while the message box is active, so presence and typing stay realistic during manual tests.
