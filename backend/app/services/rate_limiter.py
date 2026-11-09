"""Per-platform rate limiting & cooldowns (PLAN.md §14), Redis-backed with in-memory fallback."""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.redis import get_redis


@dataclass(frozen=True)
class PlatformLimit:
    requests_per_hour: int
    applications_per_day: int
    cooldown_seconds: tuple[int, int]  # randomized min/max between applications


PLATFORM_LIMITS: dict[str, PlatformLimit] = {
    "linkedin": PlatformLimit(100, 25, (120, 300)),
    "indeed": PlatformLimit(150, 30, (60, 180)),
    "greenhouse": PlatformLimit(200, 40, (30, 60)),
    "lever": PlatformLimit(200, 40, (30, 60)),
    "ashby": PlatformLimit(200, 40, (30, 60)),
    "workday": PlatformLimit(50, 10, (300, 600)),
    "glassdoor": PlatformLimit(100, 20, (120, 240)),
    "wellfound": PlatformLimit(100, 20, (60, 180)),
    "internshala": PlatformLimit(120, 25, (60, 180)),  # discovery + the opt-in apply bot (also internshala_daily_limit)
    # Discovery only (you apply on Naukri / Unstop yourself; remote-board jobs go to the company's own site).
    "naukri": PlatformLimit(80, 20, (60, 180)),
    "unstop": PlatformLimit(80, 20, (60, 180)),
    "remotive": PlatformLimit(12, 20, (60, 180)),  # Remotive asks for a few requests a day; responses are cached
    "jobicy": PlatformLimit(12, 20, (60, 180)),
    "himalayas": PlatformLimit(30, 20, (60, 180)),
}
DEFAULT_LIMIT = PlatformLimit(100, 20, (60, 180))
RATE_LIMIT_COOLDOWN_SECONDS = 15 * 60  # pause a platform for 15 min after HTTP 429


def limit_for(platform: str) -> PlatformLimit:
    return PLATFORM_LIMITS.get(platform, DEFAULT_LIMIT)


class RateLimiter:
    def __init__(self) -> None:
        self._mem: dict[str, tuple[float, float]] = {}  # key -> (value, expires_at)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ primitives
    def _incr(self, key: str, ttl: int) -> int:
        client = get_redis()
        if client is not None:
            value = int(client.incr(key))
            if value == 1:
                client.expire(key, ttl)
            return value
        with self._lock:
            now = time.time()
            value, expires = self._mem.get(key, (0, now + ttl))
            if expires < now:
                value, expires = 0, now + ttl
            value += 1
            self._mem[key] = (value, expires)
            return int(value)

    def _get(self, key: str) -> float:
        client = get_redis()
        if client is not None:
            value = client.get(key)
            return float(value) if value else 0.0
        with self._lock:
            value, expires = self._mem.get(key, (0, 0))
            return value if expires >= time.time() else 0.0

    def _set(self, key: str, value: float, ttl: int) -> None:
        client = get_redis()
        if client is not None:
            client.set(key, value, ex=ttl)
            return
        with self._lock:
            self._mem[key] = (value, time.time() + ttl)

    # ------------------------------------------------------------------ page loads / API calls
    def allow_request(self, platform: str) -> bool:
        if self.is_paused(platform):
            return False
        hour = datetime.now(UTC).strftime("%Y%m%d%H")
        count = self._incr(f"rl:req:{platform}:{hour}", 3700)
        return count <= limit_for(platform).requests_per_hour

    def pause_platform(self, platform: str, seconds: int = RATE_LIMIT_COOLDOWN_SECONDS) -> None:
        self._set(f"rl:pause:{platform}", time.time() + seconds, seconds)

    def is_paused(self, platform: str) -> bool:
        return self._get(f"rl:pause:{platform}") > time.time()

    # ------------------------------------------------------------------ applications
    def _app_key(self, user_id: str, platform: str | None = None) -> str:
        day = datetime.now(UTC).strftime("%Y%m%d")
        return f"rl:apps:{user_id}:{platform or 'all'}:{day}"

    def applications_today(self, user_id: str, platform: str | None = None) -> int:
        return int(self._get(self._app_key(user_id, platform)))

    def can_apply(self, user_id: str, platform: str, user_daily_max: int) -> tuple[bool, str | None]:
        if self.is_paused(platform):
            return False, f"{platform} is cooling down after rate limiting"
        if self.applications_today(user_id) >= user_daily_max:
            return False, f"Daily application limit reached ({user_daily_max})"
        if self.applications_today(user_id, platform) >= limit_for(platform).applications_per_day:
            return False, f"Daily {platform} limit reached ({limit_for(platform).applications_per_day})"
        last = self._get(f"rl:last:{user_id}:{platform}")
        wait = self._get(f"rl:wait:{user_id}:{platform}")
        if last and time.time() - last < wait:
            return False, f"Cooling down between {platform} applications ({int(wait - (time.time() - last))}s left)"
        return True, None

    def record_application(self, user_id: str, platform: str) -> None:
        self._incr(self._app_key(user_id), 90000)
        self._incr(self._app_key(user_id, platform), 90000)
        lo, hi = limit_for(platform).cooldown_seconds
        self._set(f"rl:last:{user_id}:{platform}", time.time(), 86400)
        self._set(f"rl:wait:{user_id}:{platform}", random.uniform(lo, hi), 86400)

    def cooldown_seconds(self, platform: str) -> float:
        lo, hi = limit_for(platform).cooldown_seconds
        return random.uniform(lo, hi)


rate_limiter = RateLimiter()
