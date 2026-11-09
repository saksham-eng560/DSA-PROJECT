""""Ready to submit": the review sheet of every paused application, the one-click submit that saves your
corrections, and the submitter typing those corrections instead of what the agent worked out."""

from __future__ import annotations

import http.server
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.application import Application
from app.models.enums import ApplicationStatus, ATSPlatform, JobType
from app.models.job import Job
from app.models.user import User
from app.services import agent_orchestrator as orch
from app.services.review_sheet import build_rows
from app.submitters.base import BaseSubmitter, CandidatePacket, override_key
from tests.test_e2e_pipeline import _chromium_available, parse_form

FORM_FIELDS = [
    {"label": "First name", "kind": "first_name", "type": "text", "value": "Jane", "required": True, "status": "filled"},
    {"label": "Email", "kind": "email", "type": "email", "value": "jane@example.com", "required": True, "status": "filled"},
    {"label": "Resume/CV", "kind": "resume", "type": "file", "value": "Jane_Doe_Resume.pdf", "required": True, "status": "filled"},
    {"label": "Why do you want to join us?", "kind": "question", "type": "textarea", "value": "I like the product.",
     "required": True, "status": "filled", "confidence": 0.5, "needs_user_review": True},
    {"label": "How did you hear about us?", "kind": "question", "type": "select", "value": "LinkedIn", "required": False,
     "status": "filled", "options": ["LinkedIn", "Referral", "Other"], "confidence": 0.9},
    {"label": "Date of birth", "kind": "question", "type": "text", "value": "", "required": True, "status": "unmapped"},
    {"label": "Confirm email", "kind": "email", "type": "email", "value": "jane@example.com", "required": False, "status": "filled"},
]
CUSTOM_ANSWERS = [
    {"question": "Why do you want to join us?", "answer": "I like the product.", "field_type": "textarea",
     "confidence": 0.5, "needs_user_review": True, "required": True, "source": "llm"},
    {"question": "How did you hear about us?", "answer": "LinkedIn", "field_type": "select",
     "options": ["LinkedIn", "Referral", "Other"], "confidence": 0.9, "needs_user_review": False, "source": "fallback"},
    {"question": "Are you legally authorized to work in India?", "answer": "Yes", "field_type": "radio",
     "options": ["Yes", "No"], "confidence": 0.95, "needs_user_review": False, "required": True, "source": "mapping"},
]


def _user_id(email: str) -> uuid.UUID:
    with SessionLocal() as db:
        return db.query(User).filter(User.email == email).one().id


def _seed(user_id: uuid.UUID, company: str = "Acme", score: int | None = 80, staged_minutes_ago: int = 10,
          status: ApplicationStatus = ApplicationStatus.PENDING_APPROVAL, raw: dict | None = None,
          form_fields: list | None = None, custom_answers: list | None = None) -> str:
    with SessionLocal() as db:
        job = Job(company_name=company, role_title="Software Engineering Intern", description="Python",
                  source_url=f"https://boards.greenhouse.io/{company.lower()}/jobs/{uuid.uuid4().hex[:8]}",
                  source_platform=ATSPlatform.GREENHOUSE, job_type=JobType.INTERNSHIP, location="Bengaluru, India",
                  dedupe_key=f"{company}-{uuid.uuid4().hex[:6]}", raw_data=raw)
        db.add(job)
        db.flush()
        app = Application(
            user_id=user_id, job_id=job.id, status=status, match_score=score, ats_platform=ATSPlatform.GREENHOUSE,
            staged_at=datetime.now(UTC) - timedelta(minutes=staged_minutes_ago),
            form_fields=FORM_FIELDS if form_fields is None else form_fields,
            custom_answers=CUSTOM_ANSWERS if custom_answers is None else custom_answers,
            cover_letter="Dear Acme team, ...", tailored_resume_pdf_url="users/x/resumes/r.pdf",
            form_screenshot_url="users/x/screenshots/s.png",
        )
        db.add(app)
        db.commit()
        return str(app.id)


def _capture_enqueue(monkeypatch) -> list[tuple]:  # type: ignore[no-untyped-def]
    queued: list[tuple] = []
    monkeypatch.setattr("app.worker.dispatch.enqueue", lambda *a, **k: queued.append(a))
    return queued


# ------------------------------------------------------------------ the review sheet
def test_rows_put_what_needs_you_first_without_duplicates() -> None:
    rows = build_rows(FORM_FIELDS, CUSTOM_ANSWERS, {}, "Dear Acme team, ...", "/api/v1/files/r.pdf")
    keys = [r["key"] for r in rows]
    assert len(keys) == len(set(keys))  # "Why..." is in the form and the answers; Email is in the form twice
    # Flagged (low confidence) and required-and-empty first, then the form's own order, then the rest
    assert keys == [
        override_key("Why do you want to join us?"), override_key("Date of birth"),
        "first_name", "email", "resume", override_key("How did you hear about us?"),
        override_key("Are you legally authorized to work in India?"), "cover_letter",
    ]
    by_key = {r["key"]: r for r in rows}
    why = by_key[override_key("Why do you want to join us?")]
    assert why["flagged"] and why["kind"] == "question" and why["type"] == "textarea" and "50%" in why["note"]
    dob = by_key[override_key("Date of birth")]
    assert dob["required"] and not dob["filled"] and dob["value"] == "" and dob["flagged"]
    assert by_key["email"] == {**by_key["email"], "kind": "profile", "value": "jane@example.com", "filled": True,
                               "flagged": False, "source": "profile"}
    assert by_key["resume"]["url"] == "/api/v1/files/r.pdf" and by_key["resume"]["value"] == "Jane_Doe_Resume.pdf"
    hear = by_key[override_key("How did you hear about us?")]
    assert hear["options"] == ["LinkedIn", "Referral", "Other"] and not hear["flagged"]
    assert by_key["cover_letter"]["type"] == "textarea" and by_key["cover_letter"]["value"].startswith("Dear Acme")

    # Your corrections show up as the value, and a corrected row no longer needs you
    fixed = build_rows(FORM_FIELDS, CUSTOM_ANSWERS, {"email": "jane.doe@work.com", override_key("Date of birth"): "01/02/2004",
                                                    override_key("Why do you want to join us?"): "Mission."}, None)
    by_key = {r["key"]: r for r in fixed}
    assert by_key["email"]["value"] == "jane.doe@work.com" and by_key["email"]["source"] == "user"
    assert by_key[override_key("Date of birth")]["value"] == "01/02/2004" and by_key[override_key("Date of birth")]["filled"]
    assert not any(r["flagged"] for r in fixed)
    assert "cover_letter" not in by_key and "resume" in by_key  # no letter; the resume upload is still listed


def test_queue_lists_your_pending_applications_best_match_first(auth_client: TestClient) -> None:
    me = _user_id("jane@example.com")
    low = _seed(me, "LowCo", score=55)
    older = _seed(me, "OldCo", score=90, staged_minutes_ago=60)
    newer = _seed(me, "NewCo", score=90, staged_minutes_ago=5)
    board = _seed(me, "BoardCo", score=70, raw={"apply_on_site": "Internshala"}, form_fields=[], custom_answers=[])
    _seed(me, "Matched", status=ApplicationStatus.MATCHED)  # not filled yet: not in the queue
    with SessionLocal() as db:
        other = User(email="other@example.com", full_name="Other", preferences={})
        db.add(other)
        db.commit()
        other_id = other.id
    _seed(other_id, "NotYours", score=99)

    r = auth_client.get("/api/v1/applications/review-queue")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 4
    assert [i["id"] for i in body["items"]] == [older, newer, board, low]  # score desc, then the longest waiting
    first = body["items"][0]
    assert first["job"]["company_name"] == "OldCo" and first["status"] == "pending_approval"
    assert first["form_screenshot_url"] == "/api/v1/files/users/x/screenshots/s.png"
    assert first["tailored_resume_pdf_url"] == "/api/v1/files/users/x/resumes/r.pdf"
    assert first["apply_url"].startswith("https://boards.greenhouse.io/oldco/")
    assert first["blocker"] is None and first["attention"] == 2 and len(first["rows"]) == 8
    blocked = body["items"][2]
    assert "Internshala" in blocked["blocker"] and "I Applied" in blocked["blocker"]
    assert [r["key"] for r in blocked["rows"]] == ["cover_letter", "resume"]
    assert auth_client.get("/api/v1/applications/review-queue?limit=1").json()["total"] == 4


# ------------------------------------------------------------------ one-click submit
def _confirm_all(item: dict[str, Any], **edits: str) -> list[dict[str, str]]:
    return [{"key": r["key"], "value": edits.get(r["key"], r["value"])} for r in item["rows"]]


def test_submit_saves_your_corrections_and_submits(auth_client: TestClient, monkeypatch) -> None:
    queued = _capture_enqueue(monkeypatch)
    me = _user_id("jane@example.com")
    first = _seed(me, "First", score=90)
    second = _seed(me, "Second", score=80)
    item = auth_client.get("/api/v1/applications/review-queue").json()["items"][0]
    assert item["id"] == first

    rows = _confirm_all(item, **{
        "email": "jane.doe@work.com",
        override_key("Date of birth"): "01/02/2004",
        override_key("Why do you want to join us?"): "Your mission matches mine.",
    })
    r = auth_client.post(f"/api/v1/applications/{first}/submit", json={"rows": rows, "cover_letter": "Dear First, hello."})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "approved" and body["next_id"] == second
    assert queued == [("submit_application", first)]
    assert body["cover_letter"] == "Dear First, hello."
    assert body["field_overrides"] == {
        "email": "jane.doe@work.com", override_key("Date of birth"): "01/02/2004",
        override_key("Why do you want to join us?"): "Your mission matches mine.",
    }
    answers = {a["question"]: a for a in body["custom_answers"]}
    assert answers["Why do you want to join us?"]["answer"] == "Your mission matches mine."
    assert answers["Why do you want to join us?"]["source"] == "user"
    assert answers["Date of birth"]["answer"] == "01/02/2004"  # a question with no answer yet becomes yours
    assert not any(a["needs_user_review"] for a in body["custom_answers"])  # you checked every row
    assert answers["How did you hear about us?"]["answer"] == "LinkedIn"  # confirmed as is
    assert "Submitted from Ready to submit" in body["history"][-1]["notes"]
    assert "Email" in body["history"][-1]["notes"] and "Cover letter" in body["history"][-1]["notes"]

    # Standard answers you approved are learned for next time
    mappings = {m["field_name"]: m["field_value"] for m in auth_client.get("/api/v1/users/me/field-mappings").json()["mappings"]}
    assert mappings.get("work_authorization") == "Yes"

    # The submit run gets your corrections, and the queue moved on
    with SessionLocal() as db:
        app = db.get(Application, uuid.UUID(first))
        packet = orch.build_packet(db, db.get(User, me), app, None, None)
    assert packet.profile_value("email") == "jane.doe@work.com"
    assert packet.override_for("Date  of Birth") == "01/02/2004"  # labels match however they're spaced or cased
    assert [i["id"] for i in auth_client.get("/api/v1/applications/review-queue").json()["items"]] == [second]
    last = auth_client.get("/api/v1/applications/review-queue").json()["items"][0]
    r = auth_client.post(f"/api/v1/applications/{second}/submit",
                         json={"rows": _confirm_all(last, **{override_key("Date of birth"): "01/02/2004"})})
    assert r.status_code == 202 and r.json()["next_id"] is None


def test_submit_refuses_what_it_cannot_send(auth_client: TestClient, monkeypatch) -> None:
    queued = _capture_enqueue(monkeypatch)
    me = _user_id("jane@example.com")
    app_id = _seed(me)
    item = auth_client.get("/api/v1/applications/review-queue").json()["items"][0]

    # A required field left empty: nothing is saved or submitted
    r = auth_client.post(f"/api/v1/applications/{app_id}/submit", json={"rows": _confirm_all(item, email="x@y.com")})
    assert r.status_code == 422 and "Date of birth" in r.json()["detail"]
    r = auth_client.post(f"/api/v1/applications/{app_id}/submit", json={"rows": _confirm_all(item, **{
        override_key("Date of birth"): "01/02/2004", override_key("Why do you want to join us?"): "  "})})
    assert r.status_code == 422 and "Why do you want to join us?" in r.json()["detail"]
    assert auth_client.post(f"/api/v1/applications/{app_id}/submit",
                            json={"rows": [{"key": "password", "value": "x"}]}).status_code == 422
    detail = auth_client.get(f"/api/v1/applications/{app_id}").json()
    assert detail["status"] == "pending_approval" and detail["field_overrides"] == {}

    # Postings that only take applications from your own account: apply there, then "I Applied"
    board = _seed(me, "Board", raw={"apply_on_site": "Internshala"}, form_fields=[], custom_answers=[])
    r = auth_client.post(f"/api/v1/applications/{board}/submit", json={"rows": []})
    assert r.status_code == 409 and "Internshala" in r.json()["detail"]

    # Already on its way / someone else's
    done = _seed(me, "Done", status=ApplicationStatus.APPLIED)
    r = auth_client.post(f"/api/v1/applications/{done}/submit", json={"rows": []})
    assert r.status_code == 409 and "applied" in r.json()["detail"]
    with SessionLocal() as db:
        other = User(email="other@example.com", full_name="Other", preferences={})
        db.add(other)
        db.commit()
        other_id = other.id
    theirs = _seed(other_id, "Theirs")
    assert auth_client.post(f"/api/v1/applications/{theirs}/submit", json={"rows": []}).status_code == 404
    assert queued == []

    # A failed submission can be fixed and sent again from the same sheet
    failed = _seed(me, "Retry", status=ApplicationStatus.FAILED, form_fields=FORM_FIELDS[:3], custom_answers=[])
    r = auth_client.post(f"/api/v1/applications/{failed}/submit", json={"rows": [{"key": "first_name", "value": "Janet"}]})
    assert r.status_code == 202, r.text
    assert r.json()["field_overrides"] == {"first_name": "Janet"} and queued == [("submit_application", failed)]

    # The classic approve flow is untouched
    r = auth_client.post(f"/api/v1/applications/{app_id}/approve", json={"custom_answers": [
        {**a, "answer": a.get("answer") or "Yes"} for a in CUSTOM_ANSWERS]})
    assert r.status_code == 202 and r.json()["history"][-1]["notes"] == "Approved by user"


# ------------------------------------------------------------------ the submitter honours overrides
FORM = """<!doctype html><html><body><h1>Apply</h1>
<form method="post" action="/submit">
<label for="fn">First name *</label><input id="fn" name="fn" required>
<label for="ln">Last name *</label><input id="ln" name="ln" required>
<label for="email">Email *</label><input id="email" name="email" type="email" required>
<label for="why">Why do you want to join us? *</label><textarea id="why" name="why" required></textarea>
<label for="hear">How did you hear about us?</label><select id="hear" name="hear"><option value="">Select</option>
  <option>LinkedIn</option><option>Referral</option><option>Other</option></select>
<label for="dob">Date of birth *</label><input id="dob" name="dob" placeholder="DD/MM/YYYY" required>
<button type="submit">Submit application</button>
</form></body></html>"""


class _Site:
    def __init__(self) -> None:
        self.submissions: list[dict] = []
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, body: str) -> None:
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                self._send(FORM)

            def do_POST(self) -> None:
                site.submissions.append(parse_form(self))
                self._send("<html><body><h1>Thank you for applying!</h1><p>Application submitted.</p></body></html>")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/apply"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def site() -> Iterator[_Site]:
    s = _Site()
    yield s
    s.server.shutdown()


@pytest.mark.e2e
@pytest.mark.skipif(not _chromium_available(), reason="Chromium not available")
def test_submitter_types_your_corrections(site: _Site) -> None:
    generated: list[str] = []

    def resolve(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        generated.extend(q["question"] for q in questions)
        return [{**q, "answer": "Referral" if q.get("options") else "Generated answer", "confidence": 0.9} for q in questions]

    packet = CandidatePacket(
        first_name="Jane", last_name="Doe", email="jane@example.com", application_url=site.url,
        answers=[{"question": "Why do you want to join us?", "answer": "Stored answer"}],
        resolve_answers=resolve,
        overrides={"email": "jane.doe@work.com", override_key("Why do you want to join us?"): "Your mission matches mine.",
                   override_key("Last  Name"): "Doe-Smith", override_key("Date of birth"): "01/02/2004"},
    )
    result = BaseSubmitter().submit(packet)
    assert result.stage == "submitted", (result.error, result.fields)
    sent = site.submissions[0]
    assert sent["fn"] == "Jane"  # no correction: the profile value
    assert sent["email"] == "jane.doe@work.com"  # profile correction beats the profile value
    assert sent["ln"] == "Doe-Smith"  # a label correction beats the profile value too
    assert sent["why"] == "Your mission matches mine."  # ...and the stored and generated answers
    assert sent["dob"] == "01/02/2004"  # exactly as you typed it (1 Feb), not re-read as 2 Jan
    assert sent["hear"] == "Referral"  # everything you didn't correct still comes from the agent
    assert generated == ["How did you hear about us?"]  # never asked about a field you answered
    report = {f["label"]: f for f in result.fields}
    assert report["Why do you want to join us?"]["source"] == "user" and report["Why do you want to join us?"]["status"] == "filled"


def test_clear_skips_everything_waiting_and_only_yours(auth_client: TestClient) -> None:
    me = _user_id("jane@example.com")
    waiting = [_seed(me, "A"), _seed(me, "B")]
    matched = _seed(me, "Matched", status=ApplicationStatus.MATCHED)
    with SessionLocal() as db:
        other = User(email="other@example.com", full_name="Other", preferences={})
        db.add(other)
        db.commit()
        other_id = other.id
    theirs = _seed(other_id, "NotYours")

    r = auth_client.post("/api/v1/applications/review-queue/clear")
    assert r.status_code == 200, r.text
    assert r.json() == {"cleared": 2}
    assert auth_client.get("/api/v1/applications/review-queue").json()["total"] == 0
    with SessionLocal() as db:
        status = {str(a.id): a.status for a in db.query(Application).all()}
    assert all(status[i] == ApplicationStatus.SKIPPED for i in waiting)
    assert status[matched] == ApplicationStatus.MATCHED and status[theirs] == ApplicationStatus.PENDING_APPROVAL
