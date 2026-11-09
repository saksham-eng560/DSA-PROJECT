"""CAPTCHA detection and solving via 2Captcha / Anti-Captcha (createTask API)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

PROVIDERS = {
    "2captcha": "https://api.2captcha.com",
    "anticaptcha": "https://api.anti-captcha.com",
    "anti-captcha": "https://api.anti-captcha.com",
}
TASK_TYPES = {
    "recaptcha": "RecaptchaV2TaskProxyless",
    "hcaptcha": "HCaptchaTaskProxyless",
    "turnstile": "TurnstileTaskProxyless",
}


class CaptchaError(Exception):
    pass


@dataclass
class CaptchaInfo:
    kind: str  # recaptcha | hcaptcha | turnstile
    sitekey: str
    invisible: bool = False


DETECT_JS = """
() => {
  const pick = (sel, attr) => { const el = document.querySelector(sel); return el ? el.getAttribute(attr) : null; };
  let key = pick('.h-captcha[data-sitekey], [data-hcaptcha-sitekey]', 'data-sitekey') || pick('[data-hcaptcha-sitekey]', 'data-hcaptcha-sitekey');
  if (!key) { const f = document.querySelector('iframe[src*="hcaptcha.com"]'); if (f) { const m = f.src.match(/sitekey=([^&]+)/); if (m) key = m[1]; } }
  if (key) return { kind: 'hcaptcha', sitekey: key, invisible: !!document.querySelector('[data-size="invisible"]') };
  key = pick('.cf-turnstile[data-sitekey]', 'data-sitekey');
  if (key) return { kind: 'turnstile', sitekey: key, invisible: false };
  key = pick('.g-recaptcha[data-sitekey], [data-sitekey]', 'data-sitekey');
  if (!key) { const f = document.querySelector('iframe[src*="recaptcha/api2/anchor"], iframe[src*="recaptcha/enterprise/anchor"]'); if (f) { const m = f.src.match(/[?&]k=([^&]+)/); if (m) key = m[1]; } }
  if (key) return { kind: 'recaptcha', sitekey: key, invisible: !!document.querySelector('.g-recaptcha[data-size="invisible"]') };
  return null;
}
"""

INJECT_JS = """
([kind, token]) => {
  const setVal = (sel) => document.querySelectorAll(sel).forEach(el => { el.value = token; el.innerHTML = token; el.style.display = 'block'; });
  if (kind === 'recaptcha') setVal('textarea[name="g-recaptcha-response"], #g-recaptcha-response');
  if (kind === 'hcaptcha') { setVal('textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"]'); }
  if (kind === 'turnstile') setVal('input[name="cf-turnstile-response"]');
  // Fire registered callbacks (reCAPTCHA stores them in ___grecaptcha_cfg)
  try {
    const cfg = window.___grecaptcha_cfg && window.___grecaptcha_cfg.clients;
    if (cfg) {
      const walk = (obj, depth) => {
        if (!obj || depth > 4) return;
        for (const k of Object.keys(obj)) {
          const v = obj[k];
          if (v && typeof v === 'object') {
            if (typeof v.callback === 'function') { try { v.callback(token); } catch (e) {} }
            walk(v, depth + 1);
          }
        }
      };
      walk(cfg, 0);
    }
  } catch (e) {}
  const cbName = (document.querySelector('[data-callback]') || {}).getAttribute && document.querySelector('[data-callback]').getAttribute('data-callback');
  if (cbName && typeof window[cbName] === 'function') { try { window[cbName](token); } catch (e) {} }
  return true;
}
"""


def detect_captcha(page: Any) -> CaptchaInfo | None:
    try:
        found = page.evaluate(DETECT_JS)
    except Exception:  # noqa: BLE001
        return None
    if not found:
        return None
    return CaptchaInfo(kind=found["kind"], sitekey=found["sitekey"], invisible=bool(found.get("invisible")))


def solve(info: CaptchaInfo, page_url: str, timeout: int = 180) -> str:
    if not settings.CAPTCHA_API_KEY:
        raise CaptchaError("CAPTCHA detected but CAPTCHA_API_KEY is not configured")
    base = PROVIDERS.get(settings.CAPTCHA_PROVIDER.lower(), PROVIDERS["2captcha"])
    task: dict[str, Any] = {"type": TASK_TYPES[info.kind], "websiteURL": page_url, "websiteKey": info.sitekey}
    if info.kind == "recaptcha" and info.invisible:
        task["isInvisible"] = True
    created = httpx.post(f"{base}/createTask", json={"clientKey": settings.CAPTCHA_API_KEY, "task": task}, timeout=30).json()
    if created.get("errorId"):
        raise CaptchaError(f"createTask failed: {created.get('errorCode')} {created.get('errorDescription')}")
    task_id = created["taskId"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        result = httpx.post(f"{base}/getTaskResult", json={"clientKey": settings.CAPTCHA_API_KEY, "taskId": task_id}, timeout=30).json()
        if result.get("errorId"):
            raise CaptchaError(f"getTaskResult failed: {result.get('errorCode')}")
        if result.get("status") == "ready":
            solution = result.get("solution") or {}
            token = solution.get("gRecaptchaResponse") or solution.get("token") or solution.get("text")
            if not token:
                raise CaptchaError("Solver returned no token")
            return token
    raise CaptchaError("CAPTCHA solving timed out")


def solve_on_page(page: Any, retries: int = 3) -> bool:
    """Detect and solve a CAPTCHA on the current page. Returns True if one was solved.

    Retry policy (PLAN.md §13): up to 3 attempts, 5s apart.
    """
    info = detect_captcha(page)
    if info is None:
        return False
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            token = solve(info, page.url)
            page.evaluate(INJECT_JS, [info.kind, token])
            logger.info("Solved %s on %s (attempt %s)", info.kind, page.url, attempt)
            return True
        except CaptchaError as exc:
            last_error = exc
            if "not configured" in str(exc):
                break
            time.sleep(5)
    raise CaptchaError(str(last_error) if last_error else "CAPTCHA could not be solved")
