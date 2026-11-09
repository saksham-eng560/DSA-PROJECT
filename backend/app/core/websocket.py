"""Real-time event fan-out to dashboard WebSocket clients.

Workers (Celery) publish events to Redis channel ``events:<user_id>``; every API
process runs a subscriber that forwards them to its locally connected sockets.
Without Redis, events published inside the API process are delivered directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "events:"


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscriber_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ lifecycle
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def start_subscriber(self) -> None:
        if self._subscriber_task is None and get_redis() is not None:
            self._subscriber_task = asyncio.create_task(self._redis_listener())

    async def stop_subscriber(self) -> None:
        if self._subscriber_task:
            self._subscriber_task.cancel()
            self._subscriber_task = None

    async def _redis_listener(self) -> None:
        client = get_redis()
        if client is None:
            return
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        pubsub.psubscribe(f"{CHANNEL_PREFIX}*")
        try:
            while True:
                message = await asyncio.to_thread(pubsub.get_message, timeout=1.0)
                if message and message.get("type") == "pmessage":
                    user_id = str(message["channel"])[len(CHANNEL_PREFIX):]
                    try:
                        payload = json.loads(message["data"])
                    except (TypeError, ValueError):
                        continue
                    await self.send_to_user(user_id, payload)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Redis event listener crashed")
        finally:
            try:
                pubsub.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ connections
    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[user_id].add(websocket)

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        self._connections[user_id].discard(websocket)
        if not self._connections[user_id]:
            self._connections.pop(user_id, None)

    def connection_count(self, user_id: str | None = None) -> int:
        if user_id:
            return len(self._connections.get(user_id, ()))
        return sum(len(v) for v in self._connections.values())

    async def send_to_user(self, user_id: str, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._connections.get(user_id, ())):
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(user_id, ws)

    def deliver_local(self, user_id: str, payload: dict[str, Any]) -> None:
        """Thread-safe delivery used when Redis is unavailable."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.send_to_user(user_id, payload), loop)
        except RuntimeError:
            pass


manager = ConnectionManager()


def publish_event(user_id: str, event_type: str, data: dict[str, Any] | None = None) -> None:
    """Publish a real-time event for ``user_id`` from any process/thread."""
    payload = {"type": event_type, "data": data or {}}
    client = get_redis()
    if client is not None:
        try:
            client.publish(f"{CHANNEL_PREFIX}{user_id}", json.dumps(payload, default=str))
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to publish event via Redis: %s", exc)
    manager.deliver_local(str(user_id), payload)
