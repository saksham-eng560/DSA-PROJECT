"""Residential proxy rotation (BrightData / Oxylabs / any HTTP proxy URL list)."""

from __future__ import annotations

import random
import threading
import time
from urllib.parse import unquote, urlparse

from app.config import settings


class ProxyManager:
    def __init__(self, urls: list[str] | None = None) -> None:
        self.urls = list(urls if urls is not None else settings.proxy_urls)
        self._bad: dict[str, float] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.urls)

    def pick(self) -> str | None:
        if not self.urls:
            return None
        now = time.time()
        with self._lock:
            healthy = [u for u in self.urls if self._bad.get(u, 0) < now]
        return random.choice(healthy or self.urls)

    def mark_bad(self, url: str, cooldown: int = 600) -> None:
        with self._lock:
            self._bad[url] = time.time() + cooldown

    @staticmethod
    def to_playwright(url: str | None) -> dict[str, str] | None:
        if not url:
            return None
        parsed = urlparse(url)
        proxy = {"server": f"{parsed.scheme or 'http'}://{parsed.hostname}:{parsed.port or 80}"}
        if parsed.username:
            proxy["username"] = unquote(parsed.username)
        if parsed.password:
            proxy["password"] = unquote(parsed.password)
        return proxy


proxy_manager = ProxyManager()
