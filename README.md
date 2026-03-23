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
  - `typing_start`
  - `typing_stop`
  - `seen`
- Outgoing socket events:
  - `message.created`
  - `message.status`
  - `conversation.typing`
  - `conversation.seen`
- Message ordering is `(client_timestamp, server_timestamp, id)`.
- Local development can use `CHANNEL_LAYER_BACKEND=memory`; production should use Redis.

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

## Tests

```bash
python manage.py test api chat
```
