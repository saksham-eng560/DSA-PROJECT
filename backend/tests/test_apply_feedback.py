"""Clicking Keep / Submit / Apply with the bot never ends in silence: work the app was stopped in the middle of
is picked up again, crashes and a missing Chromium are reported where you look, and the dashboard's live
updates arrive once the change is saved."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.automation import browser
from app.automation.browser import CHROMIUM_MISSING, BrowserUnavailable, launch_error
from app.core.database import SessionLocal, session_scope
from app.models.agent_run import AgentRun
from app.models.application import Application
from app.models.enums import ApplicationStatus, ATSPlatform, JobType
from app.models.job import Job
from app.services import agent_orchestrator as orch
from app.services.agent import submission
from app.services.application_service import set_status
from app.services.notifier import notify
from app.submitters.base import BaseSubmitter, CandidatePacket, SubmissionResult
from tests.test_review_queue import FORM_FIELDS, _capture_enqueue, _seed, _user_id

REAL_BROWSER_PROBLEM = browser.browser_problem  # conftest replaces it with "Chromium works" for every test


def _app(status: ApplicationStatus, company: str = "Acme", **fields: Any) -> str:
    """An application of jane@example.com's for a verified company's Greenhouse posting."""
    with SessionLocal() as db:
        job = Job(company_name=company, role_title="Software Engineering Intern", description="Python FastAPI. " * 20,
                  source_url=f"https://boards.greenhouse.io/{company.lower()}/jobs/{uuid.uuid4().hex[:8]}",
                  source_platform=ATSPlatform.GREENHOUSE, job_type=JobType.INTERNSHIP, location="Bengaluru, India",
                  dedupe_key=f"{company}-{uuid.uuid4().hex[:6]}", company_verdict="verified")
        db.add(job)
        db.flush()
        app = Application(user_id=_user_id("jane@example.com"), job_id=job.id, status=status, match_score=80,
                          **{"ats_platform": ATSPlatform.GREENHOUSE, **fields})
        db.add(app)
        db.commit()
        return str(app.id)


def _notes(client: TestClient) -> list[dict]:
    return client.get("/api/v1/notifications").json()["items"]


# ------------------------------------------------------------------ the app was stopped mid-way
def test_kept_and_approved_jobs_are_picked_up_again_after_a_restart(auth_client: TestClient, monkeypatch) -> None:
    """Without Redis the task queue lives in memory: Ctrl-C used to leave kept jobs "preparing" forever."""
    queued = _capture_enqueue(monkeypatch)
    kept = _app(ApplicationStatus.PREPARING)  # nothing prepared yet
    filling = _app(ApplicationStatus.PREPARING, "Filling", cover_letter="Dear team", tailored_resume_pdf_url="r.pdf",
                   match_details={"prepared_at": "2026-10-26T10:00:00+00:00"})
    halfway = _app(ApplicationStatus.PREPARING, "Halfway", tailored_resume_pdf_url="r.pdf")  # no cover letter yet
    approved = _app(ApplicationStatus.APPROVED, "Approved")
    _app(ApplicationStatus.PENDING_APPROVAL, "Waiting")  # waits for you: nothing to resume
    with SessionLocal() as db:
        user_id = _user_id("jane@example.com")
        db.add_all([AgentRun(user_id=user_id, run_type="prepare", status="running", log=[]),
                    AgentRun(user_id=user_id, run_type="scan", status="running", log=[])])
        db.commit()

    from app.main import app as api

    with TestClient(api):  # start-up in local mode (no Redis) does it
        pass
    assert sorted(queued) == sorted([("prepare_application", kept), ("stage_application", filling),
                                     ("prepare_application", halfway), ("submit_application", approved)])
    with SessionLocal() as db:
        runs = {r.run_type: r for r in db.query(AgentRun).all()}
        assert runs["prepare"].status == "failed" and "Interrupted" in runs["prepare"].log[-1]["message"]
        assert runs["scan"].status == "running"  # scans have their own clean-up

    queued.clear()
    with session_scope() as db:
        assert orch.resume_interrupted_work(db) == 4
    assert len(queued) == 4


class _Stopped(BaseException):
    """The app being stopped (Ctrl-C) mid-way: nothing catches it."""


def test_stopped_while_the_cover_letter_was_written_prepares_again(auth_client: TestClient, master_resume: dict,
                                                                   monkeypatch) -> None:
    """The resume is saved before the slow cover letter: a restart then went straight to the form, and the
    application could be sent without the cover letter you asked for."""
    from app.services.agent import preparation

    queued = _capture_enqueue(monkeypatch)
    app_id = _app(ApplicationStatus.PREPARING)

    def stopped(*args: Any, **kwargs: Any) -> Any:
        raise _Stopped

    write_letter = preparation.generate_cover_letter
    monkeypatch.setattr(preparation, "generate_cover_letter", stopped)
    with pytest.raises(_Stopped), SessionLocal() as db:
        preparation.prepare_application(db, app_id, stage=False)
    with SessionLocal() as db:
        app = db.get(Application, uuid.UUID(app_id))
        assert app.tailored_resume_pdf_url and not app.cover_letter  # the resume was saved first
    with session_scope() as db:
        orch.resume_interrupted_work(db)
    assert queued == [("prepare_application", app_id)]

    queued.clear()
    monkeypatch.setattr(preparation, "generate_cover_letter", write_letter)
    monkeypatch.setattr(preparation, "ready_or_submit", lambda *a, **k: None)
    with session_scope() as db:
        preparation.prepare_application(db, app_id, stage=False)
    with session_scope() as db:
        app = db.get(Application, uuid.UUID(app_id))
        assert app.cover_letter and app.match_details["prepared_at"]
        orch.resume_interrupted_work(db)
    assert queued == [("stage_application", app_id)]  # finished: it was filling the form


def test_two_workers_never_send_the_same_application(auth_client: TestClient, monkeypatch) -> None:
    """A restart in local mode re-queues approved jobs while the scheduler process may be sending one of them, and
    two API workers each re-queue everything. Whoever marks it "being sent" first sends it; the other copy of the
    task only looks again once that attempt must be over."""
    queued: list[tuple] = []
    monkeypatch.setattr("app.worker.dispatch.enqueue", lambda *a, **k: queued.append((*a, k.get("countdown"))))
    app_id = _app(ApplicationStatus.APPROVED, tailored_resume_pdf_url="r.pdf")
    sent: list[uuid.UUID] = []

    def send(db: Any, user: Any, app: Application, submit: bool) -> SubmissionResult:
        sent.append(app.id)
        if len(sent) == 1:  # the other worker's copy of the task, while this one is filling the form
            with session_scope() as other:
                assert orch.submit_application(other, app_id).status == ApplicationStatus.APPROVED
        return SubmissionResult(True, "submitted", confirmation_number="C-1")

    monkeypatch.setattr(submission, "_run_submitter", send)
    with session_scope() as db:
        orch.submit_application(db, app_id)
    assert len(sent) == 1
    [(task, again_id, countdown)] = queued
    assert (task, again_id) == ("submit_application", app_id)
    assert submission.SUBMISSION_STALE_MINUTES * 60 < countdown <= submission.SUBMISSION_STALE_MINUTES * 60 + 30
    with session_scope() as db:
        app = db.get(Application, uuid.UUID(app_id))
        assert app.status == ApplicationStatus.APPLIED and app.submitting_at is None
        orch.submit_application(db, app_id)  # that later look: already applied, nothing is sent
    assert len(sent) == 1


def test_an_attempt_that_never_finished_is_held_not_sent_again(auth_client: TestClient, monkeypatch) -> None:
    """The app stopped while a form was being sent: it may have gone out, so it waits for you instead."""
    queued = _capture_enqueue(monkeypatch)
    app_id = _app(ApplicationStatus.APPROVED, tailored_resume_pdf_url="r.pdf",
                  submitting_at=datetime.now(UTC) - timedelta(minutes=submission.SUBMISSION_STALE_MINUTES + 5))
    sent: list[bool] = []
    monkeypatch.setattr(submission, "_run_submitter", lambda db, user, app, submit: sent.append(submit)
                        or SubmissionResult(True, "submitted"))
    with session_scope() as db:
        orch.submit_application(db, app_id)
    assert sent == [] and queued == []
    detail = auth_client.get(f"/api/v1/applications/{app_id}").json()
    assert detail["status"] == "pending_approval" and "may already have gone out" in detail["manual_review_reason"]
    assert _notes(auth_client)[0]["event_type"] == "application_held"
    # Your approval starts a new attempt
    assert auth_client.post(f"/api/v1/applications/{app_id}/approve", json={}).status_code == 202
    assert queued == [("submit_application", app_id)]
    with session_scope() as db:
        orch.submit_application(db, app_id)
    assert sent == [True]
    assert auth_client.get(f"/api/v1/applications/{app_id}").json()["status"] == "applied"


# ------------------------------------------------------------------ crashes fail loudly
def test_a_crash_while_filling_the_form_is_reported(auth_client: TestClient, monkeypatch) -> None:
    """"Re-fill form" / "Apply with the bot" run the stage task on its own: a crash there used to leave the
    application "preparing" with no word."""
    _capture_enqueue(monkeypatch)
    app_id = _app(ApplicationStatus.PREPARING, cover_letter="Dear team", tailored_resume_pdf_url="r.pdf")

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the resume file is missing")

    monkeypatch.setattr(submission, "_run_submitter", boom)
    with session_scope() as db:
        orch.stage_application(db, app_id)
    detail = auth_client.get(f"/api/v1/applications/{app_id}").json()
    assert detail["status"] == "failed" and "the resume file is missing" in detail["error_log"]
    assert detail["history"][-1]["notes"] == "Filling the form failed: the resume file is missing"
    note = _notes(auth_client)[0]
    assert note["event_type"] == "agent_error" and note["title"] == "Could not fill the Acme form"


def test_a_crash_while_submitting_is_a_failed_attempt_not_a_stuck_application(auth_client: TestClient,
                                                                               monkeypatch) -> None:
    queued = _capture_enqueue(monkeypatch)
    app_id = _app(ApplicationStatus.APPROVED, tailored_resume_pdf_url="r.pdf")
    monkeypatch.setattr(submission, "_run_submitter", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with session_scope() as db:
        orch.submit_application(db, app_id)
    assert queued == [("submit_application", app_id)]  # retried once (rule #10)
    assert auth_client.get(f"/api/v1/applications/{app_id}").json()["status"] == "approved"
    with session_scope() as db:
        orch.submit_application(db, app_id)
    detail = auth_client.get(f"/api/v1/applications/{app_id}").json()
    assert detail["status"] == "failed" and detail["history"][-1]["notes"] == "Submission failed: OSError: disk full"
    note = _notes(auth_client)[0]
    assert note["event_type"] == "application_failed" and note["body"].startswith("OSError: disk full. Open the")
    with SessionLocal() as db:
        assert {r.status for r in db.query(AgentRun).filter(AgentRun.run_type == "apply")} == {"failed"}


# ------------------------------------------------------------------ Chromium missing
PLAYWRIGHT_BANNER = (
    "BrowserType.launch: Executable doesn't exist at /pw/chromium_headless_shell-1248/chrome-headless-shell\n"
    "╔════════════════════════════════════════════════════════════╗\n"
    "║ Looks like Playwright was just installed or updated.       ║\n"
    "║     playwright install                                     ║\n"
    "╚════════════════════════════════════════════════════════════╝"
)


def test_launch_errors_are_short_and_say_what_to_do() -> None:
    assert launch_error(Exception(PLAYWRIGHT_BANNER)) == CHROMIUM_MISSING
    assert "playwright install chromium" in CHROMIUM_MISSING and "I Applied" in CHROMIUM_MISSING
    assert launch_error(Exception("Target page closed\n=== logs ===\nmore")) == "Could not launch Chromium: Target page closed"


def test_a_page_error_is_one_line_not_playwrights_call_log() -> None:
    class _Page:
        def goto(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://x\nCall log:\n  - navigating to https://x")

    class _Session:
        page = _Page()

        def __enter__(self) -> _Session:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def screenshot(self) -> bytes:
            return b""

    packet = CandidatePacket(first_name="Jane", last_name="Doe", email="jane@example.com", application_url="https://x")
    result = BaseSubmitter(session_factory=lambda **kw: _Session()).submit(packet)
    assert result.stage == "failed" and result.error == "RuntimeError: Page.goto: net::ERR_NAME_NOT_RESOLVED at https://x"


def test_no_browser_means_no_retry_and_a_readable_reason(auth_client: TestClient, monkeypatch) -> None:
    def no_chromium(**kwargs: Any) -> Any:
        raise BrowserUnavailable(CHROMIUM_MISSING)

    packet = CandidatePacket(first_name="Jane", last_name="Doe", email="jane@example.com", application_url="https://x")
    assert BaseSubmitter(session_factory=no_chromium).stage(packet).stage == "unavailable"

    _capture_enqueue(monkeypatch)
    app_id = _app(ApplicationStatus.PREPARING, cover_letter="Dear team", tailored_resume_pdf_url="r.pdf")
    attempts: list[bool] = []
    monkeypatch.setattr(submission, "_run_submitter", lambda db, user, app, submit: attempts.append(submit)
                        or SubmissionResult(False, "unavailable", error=CHROMIUM_MISSING))
    with session_scope() as db:
        orch.stage_application(db, app_id)
    detail = auth_client.get(f"/api/v1/applications/{app_id}").json()
    assert attempts == [False]  # launching again a second later can't work
    assert detail["status"] == "pending_approval" and detail["manual_review_reason"] == CHROMIUM_MISSING


def test_submit_says_right_away_when_chromium_is_missing(auth_client: TestClient, monkeypatch) -> None:
    queued = _capture_enqueue(monkeypatch)
    monkeypatch.setattr(browser, "browser_problem", lambda probe=True: CHROMIUM_MISSING)
    app_id = _seed(_user_id("jane@example.com"), form_fields=FORM_FIELDS[:3], custom_answers=[])
    item = auth_client.get("/api/v1/applications/review-queue").json()["items"][0]
    assert item["blocker"] == CHROMIUM_MISSING  # Ready to submit shows it, with everything ready to copy over
    r = auth_client.post(f"/api/v1/applications/{app_id}/submit", json={"rows": []})
    assert r.status_code == 409 and r.json()["detail"] == CHROMIUM_MISSING
    assert auth_client.post(f"/api/v1/applications/{app_id}/approve", json={}).status_code == 409
    assert queued == [] and auth_client.get(f"/api/v1/applications/{app_id}").json()["status"] == "pending_approval"

    # Approved before the browser broke: back to Ready to submit, and you're told
    approved = _app(ApplicationStatus.APPROVED, "Later", tailored_resume_pdf_url="r.pdf")
    with session_scope() as db:
        orch.submit_application(db, approved)
    detail = auth_client.get(f"/api/v1/applications/{approved}").json()
    assert detail["status"] == "pending_approval" and detail["manual_review_reason"] == CHROMIUM_MISSING
    note = _notes(auth_client)[0]
    assert note["event_type"] == "application_held" and note["link"] == f"/dashboard/submit?id={approved}"


class _FakePlaywright:
    """Playwright whose Chromium launch fails with ``error`` (or works), counting the launches."""

    def __init__(self, error: str | None = None) -> None:
        self.error, self.launches = error, 0
        self.chromium = self

    def launch(self, **options: Any) -> Any:
        self.launches += 1
        if self.error:
            raise RuntimeError(self.error)
        return self

    def close(self) -> None: ...
    def stop(self) -> None: ...
    def start(self) -> Any: return self
    def __enter__(self) -> Any: return self
    def __exit__(self, *exc: Any) -> None: ...


def _fake_playwright(monkeypatch, error: str | None = None) -> _FakePlaywright:  # type: ignore[no-untyped-def]
    import playwright.sync_api

    fake = _FakePlaywright(error)
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: fake)
    monkeypatch.setattr(browser, "browser_problem", REAL_BROWSER_PROBLEM)
    monkeypatch.setattr(browser, "_health", {"problem": None, "at": 0.0})
    return fake


def test_a_scraper_or_one_off_launch_failure_never_holds_your_submissions(auth_client: TestClient, monkeypatch) -> None:
    """A bad proxy (or one Chromium too many) during a scan was remembered for 60 s as "Chromium is broken": an
    approved application sent in that window went back to Ready to submit, and your approval was lost."""
    fake = _fake_playwright(monkeypatch, "Browser.launch: net::ERR_PROXY_CONNECTION_FAILED")
    with pytest.raises(BrowserUnavailable):
        browser.BrowserSession().__enter__()  # a one-off failure, even in a form-filling session
    fake.error = "Executable doesn't exist at /ms-playwright/chromium/chrome"
    with pytest.raises(BrowserUnavailable):
        browser.BrowserSession(report_health=False).__enter__()  # a scraper's session
    assert browser._health["at"] == 0.0  # nothing remembered

    fake.error = None
    _capture_enqueue(monkeypatch)
    monkeypatch.setattr(submission, "_run_submitter", lambda db, user, app, submit: SubmissionResult(True, "submitted"))
    approved = _app(ApplicationStatus.APPROVED, "Approved", tailored_resume_pdf_url="r.pdf")
    with session_scope() as db:
        orch.submit_application(db, approved)
    assert auth_client.get(f"/api/v1/applications/{approved}").json()["status"] == "applied"

    # Chromium that can't run at all is remembered, so Submit says so right away
    monkeypatch.setattr(browser, "_health", {"problem": None, "at": 0.0})
    fake.error = "Executable doesn't exist at /ms-playwright/chromium/chrome"
    with pytest.raises(BrowserUnavailable):
        browser.BrowserSession().__enter__()
    assert browser.browser_problem() == CHROMIUM_MISSING
    fake.error = "Browser.launch: Target page, context or browser has been closed (out of memory)"
    monkeypatch.setattr(browser, "_health", {"problem": None, "at": 0.0})
    assert browser.browser_problem() is None and browser._health["at"] == 0.0  # a one-off check isn't kept


def test_ready_to_submit_never_launches_chromium_while_loading(auth_client: TestClient, monkeypatch) -> None:
    """Loading Ready to submit launched Chromium inside the request (holding the lock Submit waits on) whenever the
    last check was out of date; with BROWSER_HEADLESS=false a window popped up."""
    fake = _fake_playwright(monkeypatch, "Executable doesn't exist at /ms-playwright/chromium/chrome")
    checks: list[bool] = []
    monkeypatch.setattr(browser, "_probe_in_background", lambda: checks.append(True))
    _seed(_user_id("jane@example.com"), form_fields=FORM_FIELDS[:3], custom_answers=[])
    item = auth_client.get("/api/v1/applications/review-queue").json()["items"][0]
    assert fake.launches == 0 and item["blocker"] is None and checks == [True]  # checked in the background
    assert browser.browser_problem() == CHROMIUM_MISSING and fake.launches == 1  # e.g. that background check
    item = auth_client.get("/api/v1/applications/review-queue").json()["items"][0]
    assert item["blocker"] == CHROMIUM_MISSING and fake.launches == 1 and checks == [True]
    with browser._health_lock:  # a check is running: the page doesn't wait for it
        monkeypatch.setattr(browser, "_health", {"problem": None, "at": 0.0})
        assert browser.browser_problem(probe=False) is None


def test_apply_with_the_bot_says_when_chromium_is_missing(auth_client: TestClient, monkeypatch) -> None:
    queued = _capture_enqueue(monkeypatch)
    url = "https://internshala.com/internship/detail/python-intern-at-acme"
    app_id = _app(ApplicationStatus.PENDING_APPROVAL)
    with SessionLocal() as db:
        app = db.get(Application, uuid.UUID(app_id))
        app.job.source_url = app.job.application_url = url
        app.user.preferences = {**(app.user.preferences or {}), "internshala_bot_enabled": True}
        app.user.internshala_session = [{"name": "PHPSESSID", "value": "x", "domain": ".internshala.com", "path": "/"}]
        app.user.internshala_session_valid = True
        db.commit()
    monkeypatch.setattr(browser, "browser_problem", lambda probe=True: CHROMIUM_MISSING)
    r = auth_client.post(f"/api/v1/applications/{app_id}/bot-apply")
    assert r.status_code == 409 and r.json()["detail"] == CHROMIUM_MISSING and queued == []


# ------------------------------------------------------------------ live updates and where things went
def test_live_updates_go_out_once_the_change_is_saved(auth_client: TestClient, monkeypatch) -> None:
    """The dashboard refetches as soon as it hears an update: sent before the commit, the refetch read the old
    state, and a kept job only showed up in Ready to submit on the next poll."""
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr("app.services.notifier.publish_event", lambda uid, kind, data=None: sent.append((kind, data)))
    app_id = _app(ApplicationStatus.MATCHED)
    with SessionLocal() as db:
        app = db.get(Application, uuid.UUID(app_id))
        set_status(db, app, ApplicationStatus.PENDING_APPROVAL, "agent")
        notify(db, app.user, "application_ready", "Review application")
        assert sent == []
        db.commit()
    assert [kind for kind, _ in sent] == ["application_updated", "notification"]
    assert sent[0][1]["status"] == "pending_approval"


def test_live_updates_keep_one_hook_per_session_however_many_changes(auth_client: TestClient, monkeypatch) -> None:
    """A scan that changes thousands of applications used to keep a listener (and its payload) for each one."""
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr("app.services.notifier.publish_event", lambda uid, kind, data=None: sent.append((kind, data)))
    app_id = _app(ApplicationStatus.MATCHED)
    with SessionLocal() as db:
        app = db.get(Application, uuid.UUID(app_id))
        hooks = len(db.dispatch.after_commit)
        for status in [ApplicationStatus.PREPARING, ApplicationStatus.PENDING_APPROVAL] * 50:
            set_status(db, app, status, "agent")
        assert len(db.dispatch.after_commit) <= hooks + 1
        db.commit()
        assert len(sent) == 100
        set_status(db, app, ApplicationStatus.SKIPPED, "agent")
        db.commit()
        assert len(sent) == 101  # earlier events aren't sent again at the next commit
        assert len(db.dispatch.after_commit) <= hooks + 1


def test_rolled_back_changes_send_no_live_update(auth_client: TestClient, monkeypatch) -> None:
    """After a rollback (a scan's error handler), events for the statuses that were never saved stay unsent."""
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr("app.services.notifier.publish_event", lambda uid, kind, data=None: sent.append((kind, data)))
    app_id = _app(ApplicationStatus.MATCHED)
    with SessionLocal() as db:
        app = db.get(Application, uuid.UUID(app_id))
        set_status(db, app, ApplicationStatus.SKIPPED, "agent")
        db.rollback()
        app = db.get(Application, uuid.UUID(app_id))
        notify(db, app.user, "agent_error", "Job scan failed")
        db.commit()
    assert [kind for kind, _ in sent] == ["notification"]


def test_status_counts_and_prepare_from_jobs_waits_for_you(auth_client: TestClient, master_resume: dict,
                                                           monkeypatch) -> None:
    queued = _capture_enqueue(monkeypatch)
    monkeypatch.setattr("app.api.jobs.enqueue", lambda *a, **k: queued.append(a))
    _app(ApplicationStatus.FAILED, "Broken")
    assert auth_client.get("/api/v1/agent/status").json()["failed"] == 1
    # Kept with "Apply automatically", failed, then "Prepare application" on the Jobs page: it waits for you now
    app_id = _app(ApplicationStatus.FAILED, "Again", review_decision="keep", auto_submit=True,
                  ats_platform=ATSPlatform.LEVER)  # (Greenhouse would fetch its questions over the network)
    with SessionLocal() as db:
        job_id = db.get(Application, uuid.UUID(app_id)).job_id
    assert auth_client.post(f"/api/v1/jobs/{job_id}/prepare").status_code == 202
    assert queued == [("prepare_application", app_id)]
    with session_scope() as db:
        orch.prepare_application(db, app_id, stage=False)
    assert auth_client.get(f"/api/v1/applications/{app_id}").json()["status"] == "pending_approval"
    assert [q for q in queued if q[0] == "submit_application"] == []


@pytest.mark.parametrize("reply", [{"tailored_resume": "I rewrote it for you", "changes_made": []},
                                   {"tailored_resume": {"experience": ["did things"]}, "changes_made": "lots"}])
def test_a_garbled_ai_resume_falls_back_to_the_heuristic(auth_client: TestClient, master_resume: dict, fake_llm,
                                                         reply: dict) -> None:
    from app.services.resume_tailor import tailor_resume

    fake_llm({"RESUME TAILORING": reply})
    job = Job(id=uuid.uuid4(), company_name="Acme", role_title="Backend Intern",
              description="Python, FastAPI and PostgreSQL. " * 5, source_url="https://x/1",
              source_platform=ATSPlatform.GREENHOUSE)
    result = tailor_resume(master_resume["parsed_content"], job)
    assert result["method"] == "heuristic" and result["tailored_resume"]["experience"]
