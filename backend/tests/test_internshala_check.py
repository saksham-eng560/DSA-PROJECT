"""scripts/internshala_check.py: what Internshala does with the synced login, safe to paste anywhere."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.automation.browser import BrowserSession
from app.core.database import SessionLocal
from app.models.user import User
from app.submitters import internshala_apply as ia
from tests.test_e2e_pipeline import _chromium_available
from tests.test_internshala_apply import GOOD, MAC_CHROME, SESSION, MockInternshala, mock_site  # noqa: F401 - fixture

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "internshala_check.py"


def _script(monkeypatch: pytest.MonkeyPatch, site: MockInternshala) -> Any:
    monkeypatch.setenv("INTERNSHALA_CHECK_BASE", site.base)
    spec = importlib.util.spec_from_file_location("internshala_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    real = ia.browser_kwargs

    def local(cookies: Any, user_agent: Any) -> dict:  # the mock site is plain HTTP on 127.0.0.1
        kwargs = real(cookies, user_agent)
        kwargs["cookies"] = [{**c, "domain": "127.0.0.1", "secure": False, "sameSite": "Lax"} for c in kwargs["cookies"]]
        return kwargs

    monkeypatch.setattr(ia, "browser_kwargs", local)
    init = BrowserSession.__init__
    monkeypatch.setattr(BrowserSession, "__init__", lambda self, **kw: init(self, **{**kw, "headless": True}))
    return module


def _sync(session: list[dict], user_agent: str | None = MAC_CHROME) -> None:
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "jane@example.com").one()
        user.internshala_session = session
        user.internshala_user_agent = user_agent
        db.commit()


@pytest.mark.e2e
@pytest.mark.skipif(not _chromium_available(), reason="Chromium not available")
@pytest.mark.parametrize("accepted", [True, False])
def test_check_reports_what_internshala_does_with_the_login(auth_client: TestClient, mock_site: MockInternshala,  # noqa: F811
                                                           monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
                                                           accepted: bool) -> None:
    script = _script(monkeypatch, mock_site)
    _sync(SESSION if accepted else [{**c, "value": "stale"} if c["name"] == "PHPSESSID" else c for c in SESSION])
    monkeypatch.setattr("sys.argv", ["internshala_check.py"])
    script.main()
    out = capsys.readouterr().out
    assert "Internshala login check for jane@example.com" in out and MAC_CHROME in out
    assert "PHPSESSID [session]" in out and "l [399 d]" in out
    if accepted:
        assert out.count("-> LOGGED IN") == 2 and "accepts this login, in the bot's browser too" in out
    else:
        assert out.count("-> logged OUT") == 2 and "refuses these cookies even without a browser" in out
        assert "/login/student" in out
    assert out.count("GET /student/dashboard -> 302" if not accepted else "GET /student/dashboard -> 200") == 1
    assert ("/student/dashboard -> 302" if not accepted else "/student/dashboard -> 200") in out.split("2) The bot")[1]
    assert mock_site.agents and set(mock_site.agents) == {MAC_CHROME}  # both probes present your browser
    for secret in (GOOD, "remember-me-token", "csrf123"):
        assert secret not in out  # names only: safe to paste


def test_check_says_when_nothing_is_synced(auth_client: TestClient, mock_site: MockInternshala,  # noqa: F811
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    script = _script(monkeypatch, mock_site)
    monkeypatch.setattr("sys.argv", ["internshala_check.py"])
    with pytest.raises(SystemExit, match="Sync Internshala session"):
        script.main()
