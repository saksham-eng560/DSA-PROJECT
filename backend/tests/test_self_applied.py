""""I Applied": jobs you applied to yourself, their own section, and progress tracking on every channel."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.application import Application
from app.models.enums import ApplicationStatus, ATSPlatform, JobType
from app.models.interview import Interview
from app.models.job import Job
from app.models.user import Notification, User
from app.services import notifier
from app.services.gmail_service import parse_gmail_message, process_message
from app.services.progress import build_digest, send_due_digests
from tests.test_questions_email_notify import _gmail_raw


def _seed(email: str, status: ApplicationStatus = ApplicationStatus.MATCHED, company: str = "Acme Labs",
          location: str = "New Delhi, India", **app_kw) -> str:  # type: ignore[no-untyped-def]
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        job = Job(company_name=company, role_title="Software Development Intern", description="Python",
                  source_url=f"https://jobs.example/{company}/{status.value}", source_platform=ATSPlatform.GREENHOUSE,
                  job_type=JobType.INTERNSHIP, location=location, dedupe_key=f"{company}-{status.value}")
        db.add(job)
        db.flush()
        app = Application(user_id=user.id, job_id=job.id, status=status, **app_kw)
        db.add(app)
        db.commit()
        return str(app.id)


def _email(client: TestClient) -> str:
    return client.get("/api/v1/auth/me").json()["email"]


def test_i_applied_moves_job_to_applied_and_tracks_it(auth_client: TestClient, monkeypatch) -> None:
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(notifier, "send_email", lambda to, subject, body, db=None, user=None: sent.append((subject, body)) or True)
    app_id = _seed(_email(auth_client), auto_submit=True, needs_manual_review=True, manual_review_reason="x")

    r = auth_client.post(f"/api/v1/applications/{app_id}/mark-applied", json={"applied_on": "2026-09-25", "notes": "Via referral"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "applied" and body["self_applied"] is True and body["needs_manual_review"] is False
    assert body["submitted_at"].startswith("2026-09-25") and body["notes"] == "Via referral"
    assert body["history"][-1]["changed_by"] == "user"
    # The tracking confirmation reached Gmail (and the dashboard)
    assert sent and sent[0][0].startswith("[AutoApply AI] 📌 Tracking: Software Development Intern @ Acme Labs")
    with SessionLocal() as db:
        assert db.query(Notification).filter(Notification.event_type == "self_applied").count() == 1
        assert db.get(Application, __import__("uuid").UUID(app_id)).auto_submit is False  # the agent stands down
    # A bare POST (the old "Mark as applied" button) still works, and nothing ever moves backwards
    assert auth_client.post(f"/api/v1/applications/{app_id}/mark-applied").json()["status"] == "applied"
    interview = _seed(_email(auth_client), ApplicationStatus.INTERVIEW, company="Nova")
    assert auth_client.post(f"/api/v1/applications/{interview}/mark-applied").json()["status"] == "interview"


def test_self_applied_section_filters(auth_client: TestClient) -> None:
    email = _email(auth_client)
    mine = _seed(email, company="ByteWorks")
    _seed(email, ApplicationStatus.APPLIED, company="AgentCo")  # submitted by the agent
    auth_client.post(f"/api/v1/applications/{mine}/mark-applied")

    me = auth_client.get("/api/v1/applications?applied_by=me").json()
    assert [i["job"]["company_name"] for i in me["items"]] == ["ByteWorks"] and me["self_applied_total"] == 1
    assert me["counts"] == {"applied": 1}
    agent = auth_client.get("/api/v1/applications?applied_by=agent").json()
    assert [i["job"]["company_name"] for i in agent["items"]] == ["AgentCo"]
    everything = auth_client.get("/api/v1/applications").json()["items"]
    assert {i["job"]["company_name"]: i["self_applied"] for i in everything} == {"ByteWorks": True, "AgentCo": False}
    assert auth_client.get("/api/v1/applications?applied_by=someone").status_code == 422


def test_log_an_application_made_anywhere(auth_client: TestClient) -> None:
    r = auth_client.post("/api/v1/applications/manual", json={
        "company_name": "Zomato", "role_title": "SDE Intern", "url": "https://www.zomato.com/careers/123",
        "location": "Gurugram, Haryana", "applied_on": str(date.today() - timedelta(days=2)), "notes": "Applied on their site"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "applied" and body["self_applied"] and body["job"]["company_name"] == "Zomato"
    assert body["job"]["job_type"] == "internship" and body["job"]["application_url"] == "https://www.zomato.com/careers/123"
    assert auth_client.get("/api/v1/applications?applied_by=me").json()["total"] == 1
    no_link = auth_client.post("/api/v1/applications/manual", json={"company_name": "Swiggy", "role_title": "Data Intern"})
    assert no_link.status_code == 201
    assert no_link.json()["job"]["application_url"] is None and no_link.json()["job"]["source_url"] is None
    assert auth_client.post("/api/v1/applications/manual", json={
        "company_name": "X", "role_title": "Y", "url": "javascript:alert(1)"}).status_code == 422
    assert auth_client.post("/api/v1/applications/manual", json={
        "company_name": "X", "role_title": "Y", "job_type": "gig"}).status_code == 422


def test_progress_updates_reach_every_channel(db, monkeypatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(notifier, "send_email", lambda to, subject, body, db=None, user=None: sent.append(subject) or True)
    user = User(email="p@example.com", full_name="P", preferences={})
    db.add(user)
    db.flush()
    notifier.notify(db, user, "application_submitted", "Applied: SWE Intern @ Acme")  # was dashboard + chat only
    notifier.notify(db, user, "scan_completed", "Scan done")  # not a progress event: dashboard only
    assert sent == ["[AutoApply AI] Applied: SWE Intern @ Acme"]
    user.preferences = {"progress_updates_everywhere": False}
    notifier.notify(db, user, "application_submitted", "Applied again")
    assert sent == ["[AutoApply AI] Applied: SWE Intern @ Acme"]  # back to the original channels


def test_recruiter_update_says_what_changed_and_own_emails_are_ignored(db, monkeypatch) -> None:
    from tests.test_questions_email_notify import _seed_application

    user, app = _seed_application(db)
    rejected = parse_gmail_message(_gmail_raw("careers@stripe.com", "Your application to Stripe",
                                              "Unfortunately we have decided not to move forward with your application.", "r1"))
    process_message(db, user, rejected, [{"id": str(app.id), "company_name": "Stripe", "role_title": "Software Engineer"}])
    note = db.query(Notification).filter(Notification.event_type == "recruiter_email").one()
    assert "Application status: applied → rejected" in note.body
    ours = parse_gmail_message(_gmail_raw("no-reply@example.com", "[AutoApply AI] Interview scheduled: Stripe",
                                          "Interview with Stripe tomorrow", "n1"))
    assert process_message(db, user, ours, [{"id": str(app.id), "company_name": "Stripe", "role_title": "Software Engineer"}]) is None


def test_progress_digest(auth_client: TestClient, monkeypatch) -> None:
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(notifier, "send_email", lambda to, subject, body, db=None, user=None: sent.append((subject, body)) or True)
    email = _email(auth_client)
    # 20:20 in India (the default time zone), tomorrow: always after the real clock, whatever time the tests run
    now = (datetime.now(UTC) + timedelta(days=1)).replace(hour=14, minute=50, second=0, microsecond=0)
    old = _seed(email, ApplicationStatus.APPLIED, company="Old Co", submitted_at=now - timedelta(days=10))
    fresh = _seed(email, company="Fresh Co")
    auth_client.post(f"/api/v1/applications/{fresh}/mark-applied")
    with SessionLocal() as db:
        db.add(Interview(application_id=__import__("uuid").UUID(old), scheduled_at=now + timedelta(days=2), timezone="Asia/Kolkata"))
        db.commit()
        user = db.query(User).filter(User.email == email).one()
        title, body, tracked = build_digest(db, user, days=1, now=datetime.now(UTC))
    assert tracked == 2 and "moved today" in title
    assert "Fresh Co — Software Development Intern: matched → applied (you applied yourself)" in body
    assert "Old Co — Software Development Intern (applied 10 days ago)" not in body  # 10 days relative to `now` only
    with SessionLocal() as db:
        title, body, _ = build_digest(db, db.query(User).filter(User.email == email).one(), days=1, now=now)
    assert "Old Co — Software Development Intern (applied 10 days ago)" in body and "Interviews this week" in body

    # Hourly job: sends at 20:00 local time only, once
    assert send_due_digests(now=now.replace(hour=9)) == 0
    assert send_due_digests(now=now) == 1 and send_due_digests(now=now + timedelta(minutes=30)) == 0
    assert sent[-1][0].startswith("[AutoApply AI] Progress:")
    auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"progress_digest": "off"}})
    assert send_due_digests(now=now + timedelta(days=1)) == 0
    # On demand from the dashboard
    assert auth_client.post("/api/v1/users/me/progress-report").json() == {"sent": True}


def test_agent_stands_down_when_you_applied_mid_fill(auth_client: TestClient, master_resume: dict, monkeypatch) -> None:
    from app.services import agent_orchestrator as orch
    from app.services.agent import submission
    from app.submitters import SubmissionResult

    app_id = _seed(_email(auth_client), ApplicationStatus.PREPARING, company="Race Co", auto_submit=True,
                   review_decision="keep")

    def fill_while_you_click(db, user, app, submit):  # type: ignore[no-untyped-def]
        with SessionLocal() as other:  # you click "I Applied" in the dashboard while the browser is busy
            row = other.get(Application, app.id)
            row.status = ApplicationStatus.APPLIED
            other.commit()
        return SubmissionResult(True, "filled", fields=[])

    monkeypatch.setattr(submission, "_run_submitter", fill_while_you_click)
    with SessionLocal() as db:
        app = orch.stage_application(db, app_id)
        assert app.status == ApplicationStatus.APPLIED  # not pulled back to "pending approval"
