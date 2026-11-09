"""End-to-end: scan a careers page -> match -> tailor -> fill the real form in Chromium ->
pause for approval -> approve -> submit, against a local mock company site + ATS."""

from __future__ import annotations

import http.server
import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from fastapi.testclient import TestClient

from app.worker.dispatch import run_inline
from tests.conftest import FIXTURES


def parse_form(handler: http.server.BaseHTTPRequestHandler) -> dict:
    """Parse a urlencoded or multipart/form-data body (stdlib only; ``cgi`` is gone in Python 3.13)."""
    body = handler.rfile.read(int(handler.headers.get("Content-Length") or 0))
    ctype = handler.headers.get("Content-Type", "")
    if not ctype.startswith("multipart/form-data"):
        return {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
    message = BytesParser(policy=email_policy).parsebytes(f"Content-Type: {ctype}\r\n\r\n".encode() + body)
    fields: dict = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        fields[name] = {"filename": filename, "size": len(payload)} if filename else payload.decode("utf-8", "replace")
    return fields


pytestmark = pytest.mark.e2e

APPLY_HTML = (FIXTURES / "mock_ats" / "apply.html").read_text()


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


# Always recent: a fixed date ages past the default 14-day posted_within_days window and the scan drops it.
POSTED = (datetime.now(UTC).date() - timedelta(days=3)).isoformat()


class MockSite:
    def __init__(self) -> None:
        self.submissions: list[dict] = []
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):  # type: ignore[no-untyped-def]
                pass

            def _send(self, body: str, status: int = 200) -> None:
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                base = f"http://127.0.0.1:{site.port}"
                if self.path.startswith("/careers"):
                    posting = {
                        "@context": "https://schema.org", "@type": "JobPosting", "title": "Software Engineer",
                        "description": "<p>Backend engineer with Python, FastAPI, PostgreSQL, Docker and Kubernetes. "
                                       "2+ years of experience. We build developer tools for millions of users.</p>",
                        "datePosted": POSTED, "employmentType": "FULL_TIME",
                        "hiringOrganization": {"@type": "Organization", "name": "Acme Robotics"},
                        "jobLocation": {"@type": "Place", "address": {"addressLocality": "San Francisco", "addressRegion": "CA"}},
                        "url": f"{base}/jobs/1", "directApplyUrl": f"{base}/jobs/1/apply",
                    }
                    self._send(f'<html><head><script type="application/ld+json">{json.dumps(posting)}</script></head>'
                               f"<body><h1>Careers</h1></body></html>")
                elif self.path.startswith("/jobs/1"):
                    self._send(APPLY_HTML)
                else:
                    self._send("not found", 404)

            def do_POST(self) -> None:
                site.submissions.append(parse_form(self))
                self._send("<html><body><h1>Thank you for applying!</h1><p>Your application has been submitted. "
                           "Confirmation number: ACME-777</p></body></html>")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> MockSite:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:  # type: ignore[no-untyped-def]
        self.server.shutdown()


@pytest.fixture
def mock_site() -> Iterator[MockSite]:
    with MockSite() as site:
        yield site


@pytest.mark.skipif(not _chromium_available(), reason="Chromium not available")
def test_full_pipeline(auth_client: TestClient, master_resume: dict, mock_site: MockSite, tmp_path: Path) -> None:
    c = auth_client
    base = f"http://127.0.0.1:{mock_site.port}"
    r = c.put("/api/v1/users/me/preferences", json={"preferences": {
        "target_roles": ["Software Engineer"], "target_locations": ["San Francisco"], "auto_apply_threshold": 50,
        "platforms": ["generic"], "sources": {"career_pages": [f"{base}/careers"]}, "job_types": ["full-time"], "internships_only": False,
        "review_mode": "auto"}})
    assert r.status_code == 200
    c.put("/api/v1/users/me/field-mappings", json={"mappings": [
        {"field_name": "work_authorization", "field_value": "Yes"},
        {"field_name": "requires_sponsorship", "field_value": "No"}]})

    # 1-3. Discovery -> matching -> preparation -> staging (form filled, NOT submitted)
    with run_inline():
        r = c.post("/api/v1/agent/start-scan", json={"platforms": ["generic"]})
        assert r.status_code == 202, r.text
    run = c.get(f"/api/v1/agent/runs/{r.json()['id']}").json()
    assert run["status"] == "completed", run["log"]
    assert run["jobs_discovered"] == 1 and run["jobs_matched"] == 1

    pending = c.get("/api/v1/applications?status=pending_approval").json()
    assert pending["total"] == 1, c.get("/api/v1/applications").json()
    app_id = pending["items"][0]["id"]
    detail = c.get(f"/api/v1/applications/{app_id}").json()
    assert mock_site.submissions == []  # DIRECTIVE 2: nothing submitted yet
    filled = {f["label"]: f for f in detail["form_fields"]}
    assert filled["First Name"]["value"] == "Jane" and filled["First Name"]["status"] == "filled"
    assert filled["Resume/CV"]["status"] == "filled"
    assert filled["Are you legally authorized to work in the United States?"]["value"] == "Yes"
    screenshot = c.get(detail["form_screenshot_url"])
    assert screenshot.status_code == 200 and screenshot.content[:4] == b"\x89PNG"
    (tmp_path / "staged.png").write_bytes(screenshot.content)
    assert detail["tailored_resume"] and detail["cover_letter"]

    # 4. User edits an answer and approves -> 5. agent submits
    answers = detail["custom_answers"]
    for a in answers:
        if a["question"].startswith("Why do you want"):
            a["answer"] = "I love building developer tools, and Acme's mission resonates with me."
    with run_inline():
        r = c.post(f"/api/v1/applications/{app_id}/approve", json={"custom_answers": answers})
        assert r.status_code == 202, r.text
    final = c.get(f"/api/v1/applications/{app_id}").json()
    assert final["status"] == "applied", final["error_log"]
    assert final["confirmation_number"] == "ACME-777"
    assert len(mock_site.submissions) == 1
    sub = mock_site.submissions[0]
    assert sub["first_name"] == "Jane" and sub["last_name"] == "Doe" and sub["email"] == "jane.doe@example.com"
    assert sub["resume"]["filename"].endswith("_Resume.pdf") and sub["resume"]["size"] > 1000
    assert sub["auth"] == "yes" and sub["sponsor"] == "No" and sub["consent"] == "on"
    assert sub["why"].startswith("I love building developer tools")
    assert sub["gender"] == "Decline To Self Identify"
    history = [h["new_status"] for h in final["history"]]
    assert history == ["matched", "preparing", "pending_approval", "approved", "applied"]
    overview = c.get("/api/v1/analytics/overview").json()
    assert overview["totals"]["applied"] == 1


@pytest.mark.skipif(not _chromium_available(), reason="Chromium not available")
def test_swipe_keep_then_auto_submit(auth_client: TestClient, master_resume: dict, mock_site: MockSite) -> None:
    """Swipe Review: a low score never skips the job; keeping it prepares, fills and submits it."""
    c = auth_client
    base = f"http://127.0.0.1:{mock_site.port}"
    r = c.put("/api/v1/users/me/preferences", json={"preferences": {
        "target_roles": ["Software Engineer"], "auto_apply_threshold": 100, "platforms": ["generic"],
        "sources": {"career_pages": [f"{base}/careers"]}, "job_types": ["full-time"], "internships_only": False, "auto_submit_kept": True}})
    assert r.status_code == 200 and r.json()["review_mode"] == "swipe"
    c.put("/api/v1/users/me/field-mappings", json={"mappings": [
        {"field_name": "work_authorization", "field_value": "Yes"},
        {"field_name": "requires_sponsorship", "field_value": "No"},
        {"field_name": "why_company", "field_value": "I love building developer tools."}]})

    with run_inline():
        r = c.post("/api/v1/agent/start-scan", json={"platforms": ["generic"]})
    run = c.get(f"/api/v1/agent/runs/{r.json()['id']}").json()
    assert run["status"] == "completed", run["log"]

    # Threshold 100 would have skipped it in "auto" mode; in swipe mode it waits for you.
    deck = c.get("/api/v1/review/queue").json()
    assert deck["stats"]["remaining"] == 1 and len(deck["items"]) == 1
    card = deck["items"][0]
    assert card["job"]["company_name"] == "Acme Robotics" and card["match_score"] is not None
    assert c.get("/api/v1/agent/status").json()["to_review"] == 1
    assert mock_site.submissions == []

    # Skip, undo, then keep
    app_id = card["application_id"]
    assert c.post(f"/api/v1/review/{app_id}", json={"decision": "skip"}).json()["application"]["status"] == "skipped"
    assert c.get("/api/v1/review/queue").json()["stats"]["remaining"] == 0
    assert c.post(f"/api/v1/review/{app_id}/undo").status_code == 200
    assert c.get("/api/v1/review/queue").json()["stats"]["remaining"] == 1
    with run_inline():
        r = c.post(f"/api/v1/review/{app_id}", json={"decision": "keep"})
        assert r.status_code == 200, r.text
    # Acme Robotics is no company the agent can verify (a careers page it doesn't know): filled, but held
    held = c.get(f"/api/v1/applications/{app_id}").json()
    assert held["status"] == "pending_approval" and held["manual_review_reason"].startswith("Not sent automatically")
    assert held["job"]["company"]["verdict"] == "unverified" and mock_site.submissions == []
    with run_inline():  # you check it and mark it legit: what was held only for that goes out
        r = c.post("/api/v1/jobs/company-trust", json={"company": "Acme Robotics", "trusted": True})
        assert r.json()["applications_updated"] == 1, r.text
    final = c.get(f"/api/v1/applications/{app_id}").json()
    history = [h["new_status"] for h in final["history"]]
    assert final["status"] == "applied", (final["status"], final["error_log"], final["custom_answers"])
    assert final["job"]["company"]["verdict"] == "verified"
    assert history[-4:] == ["preparing", "pending_approval", "approved", "applied"]
    assert len(mock_site.submissions) == 1 and mock_site.submissions[0]["sponsor"] == "No"
    assert mock_site.submissions[0]["why"].startswith("I'm interested in Acme Robotics")
    assert c.get("/api/v1/review/queue").json()["stats"]["kept_total"] == 1


@pytest.mark.skipif(not _chromium_available(), reason="Chromium not available")
def test_swipe_keep_stops_for_eligibility_questions(auth_client: TestClient, master_resume: dict,
                                                    mock_site: MockSite) -> None:
    """Without a saved visa answer the kept job is filled but waits for you: eligibility is never guessed."""
    c = auth_client
    base = f"http://127.0.0.1:{mock_site.port}"
    c.put("/api/v1/users/me/preferences", json={"preferences": {
        "target_roles": ["Software Engineer"], "platforms": ["generic"], "job_types": ["full-time"], "internships_only": False,
        "sources": {"career_pages": [f"{base}/careers"]}}})
    with run_inline():
        c.post("/api/v1/agent/start-scan", json={"platforms": ["generic"]})
        app_id = c.get("/api/v1/review/queue").json()["items"][0]["application_id"]
        assert c.post(f"/api/v1/review/{app_id}", json={"decision": "keep"}).status_code == 200
    final = c.get(f"/api/v1/applications/{app_id}").json()
    assert final["status"] == "pending_approval"
    assert mock_site.submissions == []
    assert c.post(f"/api/v1/review/{app_id}/undo").status_code == 409  # already prepared
