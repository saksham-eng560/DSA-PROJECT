"""API integration tests (FastAPI TestClient + SQLite)."""

from __future__ import annotations

import io

import httpx
import respx
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.application import Application
from app.models.enums import ApplicationStatus, ATSPlatform
from app.models.job import Job
from app.models.user import User
from app.services.pdf_generator import render_resume_pdf
from app.services.resume_parser import heuristic_parse
from app.worker.dispatch import run_inline
from tests.conftest import FIXTURES, SAMPLE_RESUME_TEXT


def test_auth_flow(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401
    r = client.post("/api/v1/auth/register", json={"email": "A@Example.com", "password": "short", "full_name": "A"})
    assert r.status_code == 422
    r = client.post("/api/v1/auth/register", json={"email": "A@Example.com", "password": "longenough1", "full_name": "A"})
    assert r.status_code == 201 and r.json()["user"]["email"] == "a@example.com"
    assert client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "longenough1", "full_name": "A"}).status_code == 409
    client.post("/api/v1/auth/logout")
    client.cookies.clear()
    assert client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "wrong-pass"}).status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "longenough1"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    client.cookies.clear()
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    ext = client.post("/api/v1/auth/extension-token", headers={"Authorization": f"Bearer {token}"}).json()["token"]
    # Extension tokens only work on extension endpoints
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {ext}"}).status_code == 401
    r = client.post("/api/v1/users/me/integrations/linkedin-cookie", json={"li_at": "AQEDAx" * 10},
                    headers={"Authorization": f"Bearer {ext}"})
    assert r.status_code == 200
    assert client.get("/api/v1/auth/config").json()["google_enabled"] is False
    assert client.get("/api/v1/auth/ws-token", headers={"Authorization": f"Bearer {token}"}).json()["token"]


def test_preferences_validation(auth_client: TestClient) -> None:
    r = auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"bogus": 1}})
    assert r.status_code == 422
    r = auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"auto_apply_threshold": 150}})
    assert r.status_code == 422
    r = auth_client.put("/api/v1/users/me/preferences", json={"preferences": {
        "target_roles": ["Backend Engineer"], "sources": {"greenhouse_boards": ["stripe"]}}})
    prefs = r.json()
    assert prefs["target_roles"] == ["Backend Engineer"]
    assert prefs["sources"]["greenhouse_boards"] == ["stripe"] and "lever_companies" in prefs["sources"]


def test_notification_popups_can_be_muted(auth_client: TestClient) -> None:
    """Muting pop-ups is one saved switch; notifications themselves keep being recorded for the bell."""
    url = "/api/v1/users/me/preferences"
    assert auth_client.get("/api/v1/auth/me").json()["preferences"]["notification_popups"] is True  # on by default
    assert auth_client.put(url, json={"preferences": {"notification_popups": "no"}}).status_code == 422
    assert auth_client.put(url, json={"preferences": {"notification_popups": False}}).json()["notification_popups"] is False
    assert auth_client.get("/api/v1/auth/me").json()["preferences"]["notification_popups"] is False
    assert auth_client.put(url, json={"preferences": {"notification_popups": True}}).json()["notification_popups"] is True


def test_resume_upload_pdf_and_edit(auth_client: TestClient) -> None:
    pdf = render_resume_pdf(heuristic_parse(SAMPLE_RESUME_TEXT))
    r = auth_client.post("/api/v1/resumes/upload", files={"file": ("resume.pdf", io.BytesIO(pdf), "application/pdf")})
    assert r.status_code == 201, r.text
    resume = r.json()
    assert resume["is_master"] and resume["parsed_content"]["personal_info"]["email"] == "jane.doe@example.com"
    original = auth_client.get(resume["original_file_url"])
    assert original.status_code == 200 and original.content.startswith(b"%PDF")
    content = resume["parsed_content"]
    content["summary"] = "Updated summary"
    r = auth_client.put(f"/api/v1/resumes/{resume['id']}", json={"parsed_content": content})
    assert r.json()["version"] == 2 and r.json()["parsed_content"]["summary"] == "Updated summary"
    r = auth_client.get(f"/api/v1/resumes/{resume['id']}/pdf?template=modern")
    assert r.headers["content-type"] == "application/pdf"
    assert auth_client.delete(f"/api/v1/resumes/{resume['id']}").status_code == 400  # can't delete master
    bad = auth_client.post("/api/v1/resumes/upload", files={"file": ("x.exe", io.BytesIO(b"MZ"), "application/octet-stream")})
    assert bad.status_code == 422


def test_files_are_user_scoped(auth_client: TestClient, master_resume: dict) -> None:
    assert auth_client.get("/api/v1/files/users/someone-else/uploads/x.pdf").status_code == 404
    assert auth_client.get("/api/v1/files/../../etc/passwd").status_code == 404


@respx.mock
def test_job_import_and_prepare_without_browser(auth_client: TestClient, master_resume: dict, monkeypatch) -> None:
    """Import by URL -> match -> prepare (tailor, PDF, cover letter) with staging stubbed out."""
    respx.get("https://vandelay.example/careers/platform-engineer").mock(
        return_value=httpx.Response(200, text=(FIXTURES / "scrapers" / "careers_jsonld.html").read_text()))
    from app.submitters import SubmissionResult
    from app.submitters.generic_submit import GenericSubmitter

    monkeypatch.setattr(GenericSubmitter, "stage", lambda self, packet: SubmissionResult(
        True, "staged", screenshot=b"\x89PNG fake", fields=[{"label": "First Name", "status": "filled", "kind": "first_name",
                                                             "value": packet.first_name, "required": True, "type": "text"}],
        answers=[{"question": "Why us?", "answer": "Because", "confidence": 0.5, "needs_user_review": True}]))
    r = auth_client.post("/api/v1/jobs/import", json={"url": "https://vandelay.example/careers/platform-engineer"})
    assert r.status_code == 201, r.text
    app_summary = r.json()
    assert app_summary["job"]["company_name"] == "Vandelay Industries"
    assert app_summary["match_score"] is not None
    with run_inline():
        r = auth_client.post(f"/api/v1/jobs/{app_summary['job']['id']}/prepare")
        assert r.status_code == 202
    detail = auth_client.get(f"/api/v1/applications/{app_summary['id']}").json()
    assert detail["status"] == "pending_approval"
    assert detail["cover_letter"].startswith("Dear Hiring Team")
    assert detail["tailored_resume"]["parsed_content"]["personal_info"]["email"] == "jane.doe@example.com"
    assert auth_client.get(detail["tailored_resume_pdf_url"]).content.startswith(b"%PDF")
    assert auth_client.get(detail["form_screenshot_url"]).status_code == 200
    assert detail["custom_answers"][0]["answer"] == "Because"
    assert [h["new_status"] for h in detail["history"]][-2:] == ["preparing", "pending_approval"]
    notes = auth_client.get("/api/v1/notifications").json()
    assert any(n["event_type"] == "application_ready" for n in notes["items"])


def _seed_app(email: str, status: ApplicationStatus) -> str:
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        job = Job(company_name="Initech", role_title="Engineer", description="Python", source_url=f"https://i/{status.value}",
                  source_platform=ATSPlatform.GREENHOUSE)
        db.add(job)
        db.flush()
        app = Application(user_id=user.id, job_id=job.id, status=status, ats_platform=ATSPlatform.GREENHOUSE)
        db.add(app)
        db.commit()
        return str(app.id)


def test_approval_gate(auth_client: TestClient, master_resume: dict, monkeypatch) -> None:
    """Nothing is submitted without approval; approval triggers exactly one submission."""
    from app.submitters import SubmissionResult
    from app.submitters.greenhouse_submit import GreenhouseSubmitter

    submitted: list[str] = []
    monkeypatch.setattr(GreenhouseSubmitter, "submit", lambda self, packet: submitted.append(packet.email) or SubmissionResult(
        True, "submitted", screenshot=b"png", confirmation_number="GH-1"))
    app_id = _seed_app("jane@example.com", ApplicationStatus.MATCHED)
    # Direct submission of an unapproved application is refused
    from app.services.agent_orchestrator import submit_application

    with SessionLocal() as db:
        submit_application(db, app_id)
        db.commit()
    assert submitted == []
    with run_inline():
        r = auth_client.post(f"/api/v1/applications/{app_id}/approve", json={
            "cover_letter": "Edited letter", "custom_answers": [{"question": "Why?", "answer": "Edited"}]})
    assert r.status_code == 202, r.text
    detail = auth_client.get(f"/api/v1/applications/{app_id}").json()
    assert submitted == ["jane.doe@example.com"]
    assert detail["status"] == "applied" and detail["confirmation_number"] == "GH-1"
    assert detail["cover_letter"] == "Edited letter"
    assert detail["custom_answers"][0]["needs_user_review"] is False
    # Approving again is a conflict
    assert auth_client.post(f"/api/v1/applications/{app_id}/approve", json={}).status_code == 409


def test_approval_requires_answers_and_learns_them(auth_client: TestClient, master_resume: dict, monkeypatch) -> None:
    from app.submitters import SubmissionResult
    from app.submitters.greenhouse_submit import GreenhouseSubmitter

    monkeypatch.setattr(GreenhouseSubmitter, "submit", lambda self, packet: SubmissionResult(True, "submitted", screenshot=b"png"))
    app_id = _seed_app("jane@example.com", ApplicationStatus.PENDING_APPROVAL)
    question = {"question": "Will you now or in the future require visa sponsorship?", "options": ["Yes", "No"], "required": True}
    r = auth_client.post(f"/api/v1/applications/{app_id}/approve", json={"custom_answers": [{**question, "answer": ""}]})
    assert r.status_code == 422 and "sponsorship" in r.json()["detail"]
    with run_inline():
        r = auth_client.post(f"/api/v1/applications/{app_id}/approve", json={"custom_answers": [{**question, "answer": "No"}]})
    assert r.status_code == 202, r.text
    mappings = {m["field_name"]: m["field_value"] for m in auth_client.get("/api/v1/users/me/field-mappings").json()["mappings"]}
    assert mappings["requires_sponsorship"] == "No"  # answered automatically next time


def test_import_without_resume_does_not_prepare(auth_client: TestClient, monkeypatch) -> None:
    from app.scrapers.base import ScrapedJob
    from app.services.agent import scan

    monkeypatch.setattr(scan, "fetch_job_from_url", lambda url: ScrapedJob(
        company_name="Initech", role_title="Data Engineer", description="Python", source_url=url, application_url=url,
        source_platform=ATSPlatform.CUSTOM).finalize())
    auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"job_types": ["full-time", "internship"], "internships_only": False}})
    r = auth_client.post("/api/v1/jobs/import", json={"url": "https://initech.example/jobs/1", "prepare": True})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "discovered" and "master resume" in r.json()["match_reasoning"]


def test_other_users_cannot_access(auth_client: TestClient, client: TestClient) -> None:
    app_id = _seed_app("jane@example.com", ApplicationStatus.PENDING_APPROVAL)
    client.cookies.clear()
    client.post("/api/v1/auth/register", json={"email": "mallory@example.com", "password": "longenough1", "full_name": "M"})
    assert client.get(f"/api/v1/applications/{app_id}").status_code == 404
    assert client.post(f"/api/v1/applications/{app_id}/approve", json={}).status_code == 404
    assert client.get("/api/v1/applications/not-a-uuid").status_code == 404


def test_manual_status_and_interviews(auth_client: TestClient, master_resume: dict) -> None:
    app_id = _seed_app("jane@example.com", ApplicationStatus.APPLIED)
    r = auth_client.post(f"/api/v1/applications/{app_id}/status", json={"status": "approved"})
    assert r.status_code == 422  # agent-only statuses can't be set manually
    r = auth_client.post("/api/v1/interviews", json={"application_id": app_id, "scheduled_at": "2030-01-15T17:00:00Z",
                                                      "meeting_link": "https://zoom.us/j/1", "interview_type": "technical"})
    assert r.status_code == 201, r.text
    interview = r.json()
    assert interview["meeting_platform"] == "zoom" and interview["prep_notes"]
    assert auth_client.get(f"/api/v1/applications/{app_id}").json()["status"] == "interview"
    r = auth_client.patch(f"/api/v1/interviews/{interview['id']}", json={"outcome": "passed", "feedback": "Went well"})
    assert r.json()["outcome"] == "passed"
    assert len(auth_client.get("/api/v1/interviews?upcoming=true").json()["items"]) == 1
    overview = auth_client.get("/api/v1/analytics/overview").json()
    assert overview["totals"]["interviews"] == 1 and overview["rates"]["interview_rate"] == 100.0


def test_export_and_delete_account(auth_client: TestClient, master_resume: dict) -> None:
    _seed_app("jane@example.com", ApplicationStatus.APPLIED)
    auth_client.put("/api/v1/users/me/field-mappings", json={"mappings": [{"field_name": "workday_password", "field_value": "pw!"}]})
    export = auth_client.get("/api/v1/users/me/export")
    body = export.json()
    assert export.headers["content-disposition"].startswith("attachment")
    assert len(body["applications"]) == 1 and len(body["resumes"]) == 1
    assert "hashed_password" not in body["user"] and "pw!" not in export.text
    assert auth_client.request("DELETE", "/api/v1/users/me", json={"confirm": "nope"}).status_code == 400
    assert auth_client.request("DELETE", "/api/v1/users/me", json={"confirm": "DELETE"}).json() == {"deleted": True}
    with SessionLocal() as db:
        assert db.query(User).count() == 0 and db.query(Application).count() == 0
    assert auth_client.get("/api/v1/auth/me").status_code == 401


def test_gmail_webhook_and_health(client: TestClient) -> None:
    import base64
    import json

    data = base64.b64encode(json.dumps({"emailAddress": "nobody@example.com", "historyId": 1}).encode()).decode()
    with run_inline():
        assert client.post("/api/v1/webhooks/gmail", json={"message": {"data": data}}).status_code == 204
    assert client.post("/api/v1/webhooks/gmail", json={"bad": 1}).status_code == 400
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/health/ready").json()["checks"]["database"] == "ok"


def test_upload_is_master_unless_told_otherwise(auth_client: TestClient) -> None:
    pdf = render_resume_pdf(heuristic_parse(SAMPLE_RESUME_TEXT))

    def upload(**data: str) -> dict:
        r = auth_client.post("/api/v1/resumes/upload", data=data,
                             files={"file": ("resume.pdf", io.BytesIO(pdf), "application/pdf")})
        assert r.status_code == 201, r.text
        return r.json()

    first = upload()  # no is_master field: becomes the master
    assert first["is_master"] is True
    copy = upload(is_master="false")  # an extra copy: the master stays
    assert copy["is_master"] is False
    assert auth_client.get("/api/v1/resumes/master").json()["id"] == first["id"]
    assert upload(is_master="true")["is_master"] is True



def test_a_change_is_committed_before_the_response() -> None:
    """The dashboard refetches as soon as a click returns: it must already read the new state. (The test client
    waits for the whole request either way, so this checks the setting that makes it so.)"""
    from app.api.deps import DB
    from app.core.database import get_db

    dep = DB.__metadata__[0]
    assert dep.dependency is get_db and dep.scope == "function"
