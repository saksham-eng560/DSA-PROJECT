"""Shared helpers for scrapers that need a real browser (bot-protected boards)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.automation.browser import BrowserSession, BrowserUnavailable
from app.automation.human import human_scroll, pause
from app.scrapers.base import BaseScraper, Blocked, RateLimited, ScraperError
from app.services.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)

BLOCK_MARKERS = ("cf-challenge", "challenge-platform", "Just a moment...", "px-captcha", "Access Denied",
                 "verify you are human", "unusual traffic")


def extract_assigned_json(html: str, variable: str) -> Any:
    """Extract ``<variable> = {...};`` JSON blobs embedded in inline scripts."""
    idx = html.find(variable)
    if idx == -1:
        return None
    start = html.find("{", idx)
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for pos in range(start, len(html)):
        ch = html[pos]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start : pos + 1])
                except json.JSONDecodeError:
                    return None
    return None


def next_data(html: str) -> Any:
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


class BrowserScraper(BaseScraper):
    requires_browser = True

    def browser_get(self, session: BrowserSession, url: str, wait_selector: str | None = None) -> str:
        if not rate_limiter.allow_request(self.rate_key):
            raise RateLimited(f"{self.rate_key} request budget exhausted or cooling down")
        response = session.page.goto(url, wait_until="domcontentloaded")
        if response is not None and response.status == 429:
            rate_limiter.pause_platform(self.rate_key)
            raise RateLimited(f"{self.rate_key} returned HTTP 429")
        if wait_selector:
            try:
                session.page.wait_for_selector(wait_selector, timeout=15000)
            except Exception:  # noqa: BLE001
                pass
        pause(1.5, 4)
        human_scroll(session.page)
        html = session.html()
        if any(marker in html[:20000] for marker in BLOCK_MARKERS) and len(html) < 200000:
            raise Blocked(f"{self.rate_key}: blocked by bot protection (configure PROXY_URLS)")
        return html

    def open_session(self) -> BrowserSession:
        try:
            return BrowserSession(report_health=False)  # a proxy or a busy site says nothing about form filling
        except BrowserUnavailable as exc:  # pragma: no cover
            raise ScraperError(str(exc)) from exc
