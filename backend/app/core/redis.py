"""Redis connection helpers with graceful degradation when Redis is unavailable."""

from __future__ import annotations

import logging
import threading
import time

import redis

from app.config import settings

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None
_last_failure: float = 0.0
_lock = threading.Lock()
_RETRY_AFTER_SECONDS = 30.0


def get_redis() -> redis.Redis | None:
    """Return a connected Redis client, or ``None`` when Redis is not configured/reachable."""
    global _client, _last_failure
    if not settings.REDIS_URL:
        return None
    if _client is not None:
        return _client
    if _last_failure and time.monotonic() - _last_failure < _RETRY_AFTER_SECONDS:
        return None
    with _lock:
        if _client is not None:
            return _client
        try:
            client = redis.Redis.from_url(
                settings.REDIS_URL, decode_responses=True, socket_connect_timeout=2, socket_timeout=5
            )
            client.ping()
            _client = client
            return _client
        except redis.RedisError as exc:
            _last_failure = time.monotonic()
            logger.warning("Redis unavailable (%s); falling back to in-process state", exc)
            return None


def reset_redis() -> None:
    global _client, _last_failure
    _client = None
    _last_failure = 0.0
