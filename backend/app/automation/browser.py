"""Playwright browser sessions with stealth, fingerprint randomisation and proxy support."""

from __future__ import annotations

import logging
import random
import threading
import time
from types import TracebackType
from typing import Any

from app.automation.proxy import ProxyManager, proxy_manager
from app.config import settings

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
]
VIEWPORTS = [(1920, 1080), (1680, 1050), (1536, 864), (1440, 900), (1366, 768), (1600, 900)]

# Minimal stealth patches (navigator.webdriver, plugins, languages, WebGL vendor, chrome runtime).
STEALTH_JS = """
(() => {
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
  Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5].map(() => ({ name: 'Chrome PDF Plugin' })) });
  Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => %(cores)d });
  window.chrome = window.chrome || { runtime: {} };
  const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
  if (originalQuery) {
    window.navigator.permissions.query = (parameters) => (
      parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters)
    );
  }
  const getParameter = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function (parameter) {
    if (parameter === 37445) return '%(vendor)s';
    if (parameter === 37446) return '%(renderer)s';
    return getParameter.call(this, parameter);
  };
})();
"""
WEBGL = [
    ("Intel Inc.", "Intel Iris OpenGL Engine"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Apple Inc.", "Apple M1"),
]


class BrowserUnavailable(Exception):
    pass


CHROMIUM_MISSING = ("Chromium, the browser the agent fills application forms in, isn't installed on this computer. "
                    "Install it with: python -m playwright install chromium (run it in backend/.venv; ./start.sh does "
                    "this when it starts), then try again. Meanwhile, apply on the site yourself and click “I Applied”.")
PLAYWRIGHT_MISSING = "Playwright isn't installed, so the agent can't open a browser: reinstall the backend requirements."


def launch_error(exc: BaseException) -> str:
    """A short reason Chromium didn't start (Playwright's own message is a multi-line banner)."""
    text = str(exc)
    if "Executable doesn't exist" in text or "playwright install" in text:
        return CHROMIUM_MISSING
    first = next((line.strip() for line in text.splitlines() if line.strip()), type(exc).__name__)
    return f"Could not launch Chromium: {first[:300]}"


_UNUSABLE = ("Executable doesn't exist", "playwright install", "error while loading shared libraries",
             "missing dependencies")


def chromium_unusable(exc: BaseException) -> bool:
    """True when Chromium can't run on this computer at all (not installed, system libraries missing): every launch
    fails until you fix it. A bad proxy or running out of memory is a one-off and says nothing about the next one."""
    text = str(exc)
    return any(marker in text for marker in _UNUSABLE)


def _launch_options(headless: bool, proxy_url: str | None = None) -> dict[str, Any]:
    options: dict[str, Any] = {
        "headless": headless,
        "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
    }
    if settings.PLAYWRIGHT_CHROMIUM_EXECUTABLE:
        options["executable_path"] = settings.PLAYWRIGHT_CHROMIUM_EXECUTABLE
    proxy = ProxyManager.to_playwright(proxy_url)
    if proxy:
        options["proxy"] = proxy
    return options


# What the last launch in this process found, so a Submit click can say right away that the browser
# can't start instead of failing minutes later in the background. Only a Chromium that can't run at all is
# remembered as a problem (``chromium_unusable``), never a one-off failure, and never a scraper's launch.
HEALTHY_FOR = 30 * 60.0  # seconds a successful launch is trusted
BROKEN_FOR = 60.0  # a failed launch is tried again after this (e.g. once you've installed Chromium)
_health: dict[str, Any] = {"problem": None, "at": 0.0}
_health_lock = threading.Lock()


def _record(problem: str | None) -> None:
    _health.update(problem=problem, at=time.monotonic())


def _cached() -> tuple[bool, str | None]:
    """(still fresh, the problem found) from the last launch."""
    fresh = bool(_health["at"]) and time.monotonic() - _health["at"] < (BROKEN_FOR if _health["problem"] else HEALTHY_FOR)
    return fresh, _health["problem"]


def _probe_in_background() -> None:
    if _health_lock.locked():  # a check is running already
        return
    threading.Thread(target=browser_problem, name="browser-health", daemon=True).start()


def browser_problem(probe: bool = True) -> str | None:
    """Why the form-filling browser can't start on this computer, or None when it can.

    ``probe=False`` (pages that only list things): never launches Chromium in the request. It answers from the
    last check, and when that is out of date a fresh one runs in the background for the next time you look."""
    if not probe:
        fresh, problem = _cached()
        if not fresh:
            _probe_in_background()
        return problem if fresh else None
    with _health_lock:
        fresh, problem = _cached()
        if fresh:
            return problem
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:  # pragma: no cover
            _record(PLAYWRIGHT_MISSING)
            return _health["problem"]
        try:
            with sync_playwright() as pw:
                pw.chromium.launch(**_launch_options(settings.BROWSER_HEADLESS)).close()
            _record(None)
        except Exception as exc:  # noqa: BLE001
            if not chromium_unusable(exc):  # e.g. out of memory right now: let the real launch try
                logger.warning("Browser check failed (not remembered): %s", launch_error(exc))
                return None
            _record(launch_error(exc))
        return _health["problem"]


class BrowserSession:
    """Context manager wrapping a sync Playwright browser + context.

    Each session is isolated (fresh context, no shared state between users) and ephemeral.
    """

    def __init__(
        self,
        *,
        cookies: list[dict[str, Any]] | None = None,
        storage_state: dict[str, Any] | None = None,
        use_proxy: bool = True,
        headless: bool | None = None,
        proxies: ProxyManager | None = None,
        timezone_id: str | None = None,
        user_agent: str | None = None,
        report_health: bool = True,
    ) -> None:
        self.cookies = cookies
        # Whether this launch tells ``browser_problem`` how Chromium is doing (scrapers' sessions don't)
        self.report_health = report_health
        self.user_agent = user_agent  # present as this browser (e.g. the one a synced login belongs to)
        self.storage_state = storage_state
        self.use_proxy = use_proxy
        self.headless = settings.BROWSER_HEADLESS if headless is None else headless
        self.proxies = proxies or proxy_manager
        self.timezone_id = timezone_id
        self.proxy_url: str | None = None
        self._pw: Any = None
        self.browser: Any = None
        self.context: Any = None
        self.page: Any = None

    def __enter__(self) -> BrowserSession:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            _record(PLAYWRIGHT_MISSING)
            raise BrowserUnavailable(PLAYWRIGHT_MISSING) from exc
        try:
            self._pw = sync_playwright().start()
        except Exception as exc:
            raise BrowserUnavailable(launch_error(exc)) from exc
        self.proxy_url = self.proxies.pick() if self.use_proxy else None
        reports = self.report_health and self.headless == settings.BROWSER_HEADLESS
        try:
            self.browser = self._pw.chromium.launch(**_launch_options(self.headless, self.proxy_url))
        except Exception as exc:
            self._pw.stop()
            problem = launch_error(exc)
            if reports and chromium_unusable(exc):  # not a bad proxy or a busy computer: Chromium itself
                _record(problem)
            raise BrowserUnavailable(problem) from exc
        if reports:
            _record(None)
        width, height = random.choice(VIEWPORTS)
        vendor, renderer = random.choice(WEBGL)
        context_kwargs: dict[str, Any] = {
            "user_agent": self.user_agent or random.choice(USER_AGENTS),
            "viewport": {"width": width, "height": height},
            "locale": "en-US",
            "timezone_id": self.timezone_id or "America/New_York",
            "java_script_enabled": True,
            "accept_downloads": False,
        }
        if self.storage_state:
            context_kwargs["storage_state"] = self.storage_state
        self.context = self.browser.new_context(**context_kwargs)
        self.context.set_default_timeout(settings.BROWSER_TIMEOUT_MS)
        self.context.add_init_script(
            STEALTH_JS % {"cores": random.choice([4, 8, 12, 16]), "vendor": vendor, "renderer": renderer}
        )
        if self.cookies:
            self.context.add_cookies(self.cookies)
        self.page = self.context.new_page()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        for closer in (
            lambda: self.context and self.context.close(),
            lambda: self.browser and self.browser.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass

    def screenshot(self, full_page: bool | None = None) -> bytes:
        return self.page.screenshot(full_page=settings.SCREENSHOT_FULL_PAGE if full_page is None else full_page, type="png")

    def html(self) -> str:
        return self.page.content()


def linkedin_cookies(li_at: str) -> list[dict[str, Any]]:
    return [
        {"name": "li_at", "value": li_at, "domain": ".linkedin.com", "path": "/", "httpOnly": True, "secure": True,
         "sameSite": "None"},
    ]
