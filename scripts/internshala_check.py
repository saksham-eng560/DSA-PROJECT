#!/usr/bin/env python3
"""Why won't Internshala accept the bot's login? Opens Internshala the way the bot does and reports what happens.

    backend/.venv/bin/python scripts/internshala_check.py                  # the account with a synced login
    backend/.venv/bin/python scripts/internshala_check.py you@example.com

It tries your synced Internshala login twice: once as a plain request (no browser), once in the bot's own
browser (a window opens for a few seconds). For each it shows where Internshala sent it and which login
cookies Internshala changed or deleted. Cookie NAMES only, never their values: the output is safe to share.
It changes nothing in the app. Run it right after "Sync Internshala session" in the extension.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parent.parent
# The same database ./start.sh uses (LOCAL_DATABASE_URL overrides it there too)
os.environ.setdefault("DATABASE_URL", os.environ.get("LOCAL_DATABASE_URL") or f"sqlite:///{ROOT}/backend/data/autoapply.db")
sys.path.insert(0, str(ROOT / "backend"))

BASE = os.environ.get("INTERNSHALA_CHECK_BASE", "https://internshala.com")  # (a test server in the tests)
PROBE_PATH = "/student/dashboard"  # logged in: stays here; logged out: sent to the login page
LOGIN_COOKIES = ("PHPSESSID", "l", "is_logged_in", "sessionToken", "persistentSession")


def _set_cookies(headers: list[tuple[str, str]]) -> list[str]:
    """Set-Cookie headers -> "name (new value)" / "name (deleted)", names only."""
    out = []
    for name, value in headers:
        if name.lower() != "set-cookie":
            continue
        cookie = value.split(";", 1)[0]
        key, _, val = cookie.partition("=")
        low = value.lower()
        deleted = val.strip() in ("", "deleted") or "max-age=0" in low or "01 jan 1970" in low or "01-jan-1970" in low
        if key.strip() in LOGIN_COOKIES or deleted:
            out.append(f"{key.strip()} ({'deleted' if deleted else 'new value'})")
    return out


def _logged_out(path: str) -> bool:
    from app.submitters.internshala_apply import _on_login_page

    return _on_login_page(f"{BASE}{path}")


def describe(user: Any) -> list[dict]:
    synced = user.internshala_session_updated_at
    age = f"{int((datetime.now(UTC) - synced).total_seconds() // 60)} min ago" if synced else "never"
    print(f"Internshala login check for {user.email}")
    print(f"  Synced: {age} · the app marks it {'working' if user.internshala_session_valid else 'NOT working'}")
    print(f"  Browser the login came from: {user.internshala_user_agent or 'not sent'}")
    if not user.internshala_user_agent:
        print("    -> the extension didn't send it: reload the extension (version 1.1.1 or later) and sync again")
    cookies = user.internshala_session or []
    now = time.time()
    names = []
    for c in cookies:
        exp = c.get("expirationDate")
        left = float(exp) - now if exp else 0
        kind = "session" if not exp else f"{int(left // 86400)} d" if left >= 86400 else f"{max(0, int(left // 3600))} h"
        host = c.get("domain", "")
        names.append(f"{c['name']} [{kind}{'' if host.lstrip('.') == 'internshala.com' else ', ' + host}]")
    print(f"  Cookies ({len(cookies)}): {', '.join(names) or 'none'}")
    missing = [n for n in ("PHPSESSID", "l", "is_logged_in") if n not in {c['name'] for c in cookies}]
    if missing:
        print(f"    (no {', '.join(missing)})")
    return cookies


def plain_request(cookies: list[dict], user_agent: str | None) -> bool:
    import httpx

    print("\n1) Plain request with your cookies (no browser)")
    jar = "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("domain", "").lstrip(".") == "internshala.com")
    headers = {"User-Agent": user_agent or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
               "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
               "Accept-Language": "en-US,en;q=0.9", "Cookie": jar}
    url = f"{BASE}{PROBE_PATH}"
    try:
        with httpx.Client(follow_redirects=False, timeout=20, headers=headers) as client:
            for _ in range(6):
                r = client.get(url)
                changed = _set_cookies(list(r.headers.multi_items()))
                print(f"  GET {urlparse(url).path} -> {r.status_code}" + (f" · Internshala changed: {', '.join(changed)}" if changed else ""))
                if r.status_code not in (301, 302, 303, 307, 308):
                    break
                url = urljoin(url, r.headers.get("location", "/"))
                if urlparse(url).netloc != urlparse(BASE).netloc:  # never send your cookies anywhere else
                    client.headers.pop("Cookie", None)
    except httpx.HTTPError as exc:
        print(f"  Could not reach Internshala: {exc}")
        return False
    path = urlparse(url).path
    ok = PROBE_PATH in path and not _logged_out(path)
    print(f"  -> {'LOGGED IN' if ok else 'logged OUT'} (ended on {path})")
    return ok


def bot_browser(cookies: list[dict], user_agent: str | None) -> bool:
    from app.automation.browser import BrowserSession
    from app.submitters import internshala_apply as ia

    print("\n2) The bot's own browser (a window opens for a few seconds)")
    steps: list[str] = []

    def on_response(response: Any) -> None:
        if response.request.resource_type != "document" or urlparse(response.url).netloc != urlparse(BASE).netloc:
            return
        changed = _set_cookies([(h["name"], h["value"]) for h in response.headers_array()])
        steps.append(f"  {urlparse(response.url).path} -> {response.status}" + (f" · Internshala changed: {', '.join(changed)}" if changed else ""))

    try:
        with BrowserSession(**ia.browser_kwargs(cookies, user_agent), headless=False) as session:
            page = session.page
            page.on("response", on_response)
            page.goto(f"{BASE}{PROBE_PATH}", wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:  # noqa: BLE001 - pages that keep polling never go idle
                pass
            path = urlparse(page.url).path
            signup = ia._first_visible(page, ia.SELECTORS["login_modal"]) is not None
            title = page.title()
            page.wait_for_timeout(4000)  # so you can see what the bot sees
    except Exception as exc:  # noqa: BLE001
        print(f"  Could not open the browser: {exc}")
        return False
    print("\n".join(steps) or "  (no page loaded)")
    ok = PROBE_PATH in path and not _logged_out(path) and not signup
    print(f"  -> {'LOGGED IN' if ok else 'logged OUT'} (ended on {path}, page title: {title[:60]!r})")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("email", nargs="?")
    args = parser.parse_args()

    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.user import User

    with SessionLocal() as db:
        query = select(User).where(User.internshala_session.is_not(None))
        users = [u for u in db.scalars(query) if not args.email or u.email.lower() == args.email.lower()]
        if not users:
            sys.exit("No synced Internshala login in this copy's database: open the extension and click "
                     "“Sync Internshala session” (and run this from the folder you start the app from).")
        if len(users) > 1:
            sys.exit("More than one account has a synced login; pass your e-mail: " + ", ".join(u.email for u in users))
        user = users[0]
        cookies = describe(user)
        user_agent = user.internshala_user_agent

    plain = plain_request(cookies, user_agent)
    browser = bot_browser(cookies, user_agent)
    print("\nVerdict:")
    if plain and browser:
        print("  Internshala accepts this login, in the bot's browser too. Use “Apply with the bot” now.")
    elif plain:
        print("  Internshala accepts your cookies, but not inside the bot's browser: it checks the browser itself.")
    elif browser:
        print("  The bot's browser is logged in (only the plain request isn't): the bot should work.")
    else:
        print("  Internshala refuses these cookies even without a browser, so the login itself isn't accepted.\n"
              "  If it changed or deleted PHPSESSID / l above, it ended that session.")
    print("\nPaste everything above (it contains no passwords or cookie values) to get help with the next step.")


if __name__ == "__main__":
    main()
