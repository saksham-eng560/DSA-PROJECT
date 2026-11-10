"""Rate limiting: a sliding-window log and a token bucket.

``SlidingWindowLimiter`` keeps, per key, a double-ended queue (deque) of the timestamps of recent
requests. A new request first drops timestamps older than the window from the left end, then is
allowed if fewer than ``limit`` remain. Timestamps arrive in order, so the deque is always
sorted and each one is added once and removed once: O(1) amortised per request, O(limit) space
per key. Unlike a fixed window ("20 per minute, reset on the minute") it can't be gamed by
bunching requests around the reset.

``TokenBucket`` holds up to ``capacity`` tokens and refills at ``rate`` per second; a request
spends one. It allows short bursts while keeping the long-run average at ``rate``. O(1) time and
space.

Used by the chatbot and search endpoints (per user), in-process. The app's per-platform job
board limits stay in Redis (``services/rate_limiter.py``) because they must be shared between
the API and the workers.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Hashable


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic,
                 max_keys: int = 10_000) -> None:
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("limit and window_seconds must be positive")
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._events: dict[Hashable, deque[float]] = {}
        self._lock = threading.Lock()

    def _trim(self, events: deque[float], now: float) -> None:
        cutoff = now - self.window
        while events and events[0] <= cutoff:
            events.popleft()

    def allow(self, key: Hashable) -> bool:
        """Record a request for ``key`` if it is within the limit; returns whether it was allowed."""
        now = self._clock()
        with self._lock:
            events = self._events.get(key)
            if events is None:
                if len(self._events) >= self._max_keys:
                    self._sweep(now)
                events = self._events[key] = deque()
            self._trim(events, now)
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True

    def remaining(self, key: Hashable) -> int:
        now = self._clock()
        with self._lock:
            events = self._events.get(key)
            if events is None:
                return self.limit
            self._trim(events, now)
            return self.limit - len(events)

    def retry_after(self, key: Hashable) -> float:
        """Seconds until ``key``'s next request would be allowed (0 when it already is)."""
        now = self._clock()
        with self._lock:
            events = self._events.get(key)
            if events is None:
                return 0.0
            self._trim(events, now)
            if len(events) < self.limit:
                return 0.0
            return max(0.0, events[0] + self.window - now)

    def _sweep(self, now: float) -> None:
        """Drop keys with no recent requests (called when the table is full)."""
        for key in list(self._events):
            events = self._events[key]
            self._trim(events, now)
            if not events:
                del self._events[key]

    def reset(self, key: Hashable | None = None) -> None:
        with self._lock:
            if key is None:
                self._events.clear()
            else:
                self._events.pop(key, None)


class TokenBucket:
    def __init__(self, capacity: float, rate: float, clock: Callable[[], float] = time.monotonic) -> None:
        if capacity <= 0 or rate <= 0:
            raise ValueError("capacity and rate must be positive")
        self.capacity = capacity
        self.rate = rate
        self._clock = clock
        self._tokens = float(capacity)
        self._updated = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    def consume(self, tokens: float = 1.0) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    @property
    def tokens(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens
