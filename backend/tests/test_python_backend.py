"""Python backend internships for a 2nd-year student, at most 2 months; a scan that never waits long on a slow AI;
and clearing old postings."""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.application import Application
from app.models.enums import ApplicationStatus, ATSPlatform, JobType
from app.models.job import Job
from app.models.user import User
from app.scrapers import SCRAPERS
from app.scrapers.base import ScrapedJob
from app.services import agent_orchestrator as orch
from app.services.duration_fit import drop_too_long, duration_months, duration_reasons
from app.services.job_matcher import filter_reasons
from app.services.presets import apply_preset
from app.services.role_focus import focus_reasons
from app.services.scan_progress import ScanProgress


def make_job(**kw: Any) -> Job:
    base = dict(company_name="Acme", role_title="Software Engineer Intern", description="Python role",
                source_url="https://x/1", source_platform=ATSPlatform.GREENHOUSE, job_type=JobType.INTERNSHIP,
                is_remote=False, location="Bengaluru, India")
    base.update(kw)
    return Job(**base)


LONG = " We are a small startup in Bengaluru building developer tools for thousands of teams. You will ship weekly." * 3
BACKEND = apply_preset({}, "ai-python")


def _job(title: str, desc: str = LONG, duration: str | None = None, url: str | None = None) -> ScrapedJob:
    slug = title.lower().replace(" ", "-")
    return ScrapedJob(company_name="Acme", role_title=title, description=desc, location="Bengaluru, India",
                      source_url=url or f"https://careers.example/{slug}/{abs(hash(desc)) % 10**8}",
                      source_platform=ATSPlatform.CUSTOM, job_type=JobType.INTERNSHIP,
                      raw={"duration": duration} if duration else {}).finalize()


# ------------------------------------------------------------------ internship length
@pytest.mark.parametrize(("desc", "duration", "months"), [
    ("", "2 Months", 2.0),
    ("", "6 Months", 6.0),
    ("", "8 Weeks", 1.8),
    ("This is a 6-month internship in Python.", None, 6.0),
    ("A three month internship building APIs.", None, 3.0),
    ("Internship duration: 2-3 months.", None, 2.0),  # the shortest it can be
    ("Duration of 12 weeks, starting May 2027.", None, 2.8),
    ("The internship runs for 10 weeks over the summer.", None, 2.3),
    ("Summer internship, May to July. Python and FastAPI.", None, None),  # no stated length: kept
    ("Requires 3 months of experience with Django.", None, None),  # not the internship's length
    ("Stipend is paid within 2 months of joining.", None, None),
])
def test_internship_length_is_read_from_the_posting(desc: str, duration: str | None, months: float | None) -> None:
    assert duration_months(_job("Python Developer Intern", desc, duration)) == months


def test_longer_internships_are_skipped_and_unstated_ones_kept() -> None:
    two, six, unstated = (_job("Python Intern", duration="2 Months"), _job("Python Intern", duration="6 Months"),
                          _job("Python Intern", "Python and FastAPI. " + LONG))
    kept, dropped = drop_too_long([two, six, unstated], {"max_internship_months": 2})
    assert kept == [two, unstated] and dropped == 1
    assert duration_reasons(six, {"max_internship_months": 2}) == ["6 months internship (you want at most 2 months)"]
    assert drop_too_long([six], {}) == ([six], 0)  # no limit set: every length


def test_a_saved_six_month_posting_is_a_hard_reason() -> None:
    job = make_job(role_title="Python Developer Intern", description="A 6-month internship. Python, FastAPI." + LONG,
                   job_type=JobType.INTERNSHIP)
    hard, _ = filter_reasons(job, BACKEND)
    assert "6 months internship (you want at most 2 months)" in hard


# ------------------------------------------------------------------ the preset
def test_the_preset_is_set_for_2nd_year_2_months() -> None:
    assert BACKEND["max_internship_months"] == 2 and BACKEND["year_of_study"] == 2 and BACKEND["internships_only"]
    assert "FastAPI Developer Intern" in BACKEND["target_roles"] and "Python" in BACKEND["focus_skills"]
    assert {"Java", "Node.js", "PHP"} <= set(BACKEND["avoid_skills"])
    kept = apply_preset({"location_focus": {"country": "India", "prime_cities": [], "country_share": 80},
                         "companies_to_avoid": ["Scam Co"]}, "ai-python")
    assert kept["location_focus"]["country_share"] == 100 and "Noida" in kept["location_focus"]["prime_cities"]
    assert kept["companies_to_avoid"] == ["Scam Co"]  # your own company lists stay


@pytest.mark.parametrize(("title", "desc", "out"), [
    ("Backend Developer Intern", "Build REST APIs with Python, FastAPI and PostgreSQL." + LONG, False),
    ("Python Developer Intern", "Django and Celery on AWS." + LONG, False),
    ("Backend Developer Intern", "Node.js, Express and MongoDB." + LONG, True),
    ("Backend Developer Intern", "Java, Spring Boot, MySQL." + LONG, True),
    ("Laravel Developer Intern", "PHP and Laravel." + LONG, True),
    ("Frontend Developer Intern", "React, TypeScript and Python tooling." + LONG, True),  # excluded title
    ("Android Developer Intern", "Kotlin; our backend is Python." + LONG, True),
    ("Data Analyst Intern", "Python, pandas and SQL." + LONG, True),
])
def test_only_python_backend_roles_pass(title: str, desc: str, out: bool) -> None:
    job = make_job(role_title=title, description=desc, job_type=JobType.INTERNSHIP)
    hard, _ = filter_reasons(job, BACKEND)
    assert bool(hard) is out, hard
    if title.startswith("Backend") and out:
        assert focus_reasons(job, BACKEND)


def test_the_preset_endpoint(auth_client: TestClient) -> None:
    prefs = auth_client.post("/api/v1/users/me/preferences/preset/ai-python").json()
    assert prefs["max_internship_months"] == 2 and "Python" in prefs["focus_skills"]
    bad = auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"max_internship_months": 40}})
    assert bad.status_code == 422
    ok = auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"max_internship_months": None}})
    assert ok.status_code == 200 and ok.json()["max_internship_months"] is None


# ------------------------------------------------------------------ a slow AI never holds the scan
class _Board:
    name = "slowai"

    def search(self, query: Any) -> list[ScrapedJob]:
        return [_job(f"Python Developer Intern {i}", "Python, FastAPI and PostgreSQL APIs." + LONG,
                     url=f"https://careers.example/slowai/{i}") for i in range(4)]


def test_scan_stops_waiting_for_a_slow_ai(auth_client: TestClient, master_resume: dict, fake_llm, monkeypatch) -> None:
    from app.config import settings
    from app.services import job_matcher

    monkeypatch.setattr(settings, "SCAN_LLM_BUDGET_SECONDS", 1)
    monkeypatch.setattr(settings, "SCAN_LLM_CONCURRENCY", 1)
    monkeypatch.setitem(SCRAPERS, "slowai", _Board)
    fake_llm({"JOB MATCH EVALUATION": {"evaluation": {
        "skills_match": 18, "experience_match": 16, "industry_match": 14, "location_match": 20, "compensation_match": 15,
        "reasoning": "Strong Python fit.", "missing_skills": [], "strong_matches": ["Python"], "proceed_with_application": True}}})
    real = job_matcher.llm_evaluation

    def slow(*args: Any, **kwargs: Any) -> dict:
        time.sleep(0.7)  # a laptop model: one answer at a time, slow
        return real(*args, **kwargs)

    monkeypatch.setattr(job_matcher, "llm_evaluation", slow)
    started = time.monotonic()
    with SessionLocal() as db:
        run = orch.run_scan(db, db.query(User).filter(User.email == "jane@example.com").one(), platforms=["slowai"])
        db.commit()
        status, log = run.status, [e["message"] for e in run.log]
    assert status == "completed" and time.monotonic() - started < 6  # 4 x 0.7 s one by one would be ~3 s + waiting
    assert any("get an instant score" in m for m in log), log
    with SessionLocal() as db:
        apps = db.query(Application).all()
        assert len(apps) == 4 and all(a.status == ApplicationStatus.MATCHED for a in apps)
        methods = sorted(a.match_details["method"] for a in apps)
        assert "llm" in methods and "heuristic" in methods  # the AI scored some, the rest got an instant score


def test_eta_counts_the_ai_calls_not_the_instant_ones() -> None:
    progress = ScanProgress(None, None, [])  # type: ignore[arg-type]
    progress.phase, progress.to_score, progress.scored = "scoring", 8, 6  # six were filtered out instantly
    progress._scoring_t0 = time.monotonic() - 60
    progress.llm_total, progress.llm_done, progress.llm_concurrency = 2, 0, 1
    progress.llm_deadline = time.monotonic() + 120
    assert 110 <= progress.eta_seconds() <= 120  # no AI answer yet: bounded by the time budget, not "10 s left"
    progress.llm_done = 1  # one AI answer took a minute
    assert 60 <= progress.eta_seconds() <= 63


# ------------------------------------------------------------------ clearing old postings
def _clear_module() -> Any:
    path = Path(__file__).resolve().parents[2] / "scripts" / "clear_jobs.py"
    spec = importlib.util.spec_from_file_location("clear_jobs", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_clear_jobs_keeps_what_you_kept_or_applied_to(auth_client: TestClient, capsys: pytest.CaptureFixture[str]) -> None:
    statuses = {"waiting": ApplicationStatus.MATCHED, "new": ApplicationStatus.DISCOVERED, "skipped": ApplicationStatus.SKIPPED,
                "applied": ApplicationStatus.APPLIED, "interview": ApplicationStatus.INTERVIEW,
                "approval": ApplicationStatus.PENDING_APPROVAL, "kept": ApplicationStatus.MATCHED}
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "jane@example.com").one()
        for name, status in statuses.items():
            job = make_job(company_name=name, source_url=f"https://jobs.example/clear/{name}", dedupe_key=f"clear-{name}")
            db.add(job)
            db.flush()
            db.add(Application(user_id=user.id, job_id=job.id, status=status,
                               review_decision="keep" if name == "kept" else None))
        db.commit()
    clear = _clear_module().clear
    assert clear("jane@example.com", apply=False) == 0
    assert "3 job postings to clear" in capsys.readouterr().out
    with SessionLocal() as db:
        assert db.query(Application).count() == 7  # a dry run changes nothing
    assert clear("jane@example.com", apply=True) == 0
    with SessionLocal() as db:
        left = {a.job.company_name for a in db.query(Application).all()}
        assert left == {"applied", "interview", "approval", "kept"}
        assert {j.company_name for j in db.query(Job).all()} == left  # the cleared postings are gone too
    assert clear("nobody@example.com", apply=True) == 1


# ------------------------------------------------------------------ AI + Python · 2nd year
AI_PY = apply_preset({}, "ai-python")


def test_the_ai_python_preset() -> None:
    assert AI_PY["max_internship_months"] == 2 and AI_PY["year_of_study"] == 2 and AI_PY["verified_companies_only"]
    assert AI_PY["target_roles"][0] == "AI Engineer Intern" and "FastAPI Developer Intern" in AI_PY["target_roles"]
    assert {"AI", "LLMs", "Python", "FastAPI"} <= set(AI_PY["focus_skills"]) and "wellfound" in AI_PY["platforms"]
    assert AI_PY["scan_top_companies"] and AI_PY["skip_suspicious_companies"]


@pytest.mark.parametrize(("title", "desc", "verdict", "tier", "out"), [
    ("AI Engineer Intern", "Build LLM agents with Python and FastAPI." + LONG, "verified", None, False),
    ("AI Engineer Intern", "Build LLM agents with Python and FastAPI." + LONG, "verified", "startup_india", False),
    ("Python Developer Intern", "FastAPI and PostgreSQL." + LONG, "unverified", None, True),  # unknown company
    ("Python Developer Intern", "FastAPI and PostgreSQL." + LONG, "unverified", "ai", False),  # catalog company
    ("Python Developer Intern", "FastAPI and PostgreSQL." + LONG, None, None, False),  # not checked yet: kept
    ("Backend Developer Intern", "Node.js and Express." + LONG, "verified", None, True),  # off-stack
    ("Generative AI Intern", "A 6-month internship building RAG apps in Python." + LONG, "verified", None, True),
])
def test_ai_python_keeps_good_companies_and_short_ai_python_roles(title: str, desc: str, verdict: str | None,
                                                                  tier: str | None, out: bool) -> None:
    job = make_job(role_title=title, description=desc, company_verdict=verdict, company_tier=tier)
    hard, _ = filter_reasons(job, AI_PY)
    assert bool(hard) is out, hard


def test_a_company_you_trust_is_always_in() -> None:
    job = make_job(company_name="Tiny Labs", role_title="AI Engineer Intern", description="LLM apps in Python." + LONG,
                   company_verdict="unverified")
    assert any("isn't a verified company" in r for r in filter_reasons(job, AI_PY)[0])
    assert not filter_reasons(job, {**AI_PY, "trusted_companies": ["Tiny Labs"]})[0]
    assert not filter_reasons(job, {**AI_PY, "verified_companies_only": False})[0]


def test_ai_roles_come_first_in_swipe_review(auth_client: TestClient) -> None:
    from app.services.role_focus import is_ai_role

    assert is_ai_role("Generative AI Intern") and is_ai_role("AI/ML Engineer Intern") and is_ai_role("LLM Agents Intern")
    assert not is_ai_role("Python Developer Intern") and not is_ai_role("Email Marketing Intern")
    auth_client.post("/api/v1/users/me/preferences/preset/ai-python")
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "jane@example.com").one()
        titles = ["Python Developer Intern", "Backend Developer Intern", "AI Engineer Intern", "Generative AI Intern"]
        for i, title in enumerate(titles):
            job = make_job(role_title=title, description="Python and FastAPI. " + LONG, company_verdict="verified",
                           source_url=f"https://jobs.example/ai-first/{i}", dedupe_key=f"ai-first-{i}")
            db.add(job)
            db.flush()
            db.add(Application(user_id=user.id, job_id=job.id, status=ApplicationStatus.MATCHED, match_score=90 - i * 10))
        db.commit()
    deck = [c["job"]["role_title"] for c in auth_client.get("/api/v1/review/queue").json()["items"]]
    assert deck[:2] == ["AI Engineer Intern", "Generative AI Intern"]  # AI first, then by score
    assert deck[2:] == ["Python Developer Intern", "Backend Developer Intern"]


# ------------------------------------------------------------------ Delhi NCR first, nothing from abroad
def test_us_and_new_york_postings_are_left_out_delhi_first() -> None:
    from app.services.location_focus import TIER_ABROAD, TIER_PRIME, TIER_REMOTE, balance_by_location, get_focus, location_tier

    focus = get_focus(AI_PY)
    assert location_tier("Gurugram, Haryana", False, focus) == TIER_PRIME
    assert location_tier("Remote", True, focus) == TIER_REMOTE
    for abroad in ("New York, NY", "Remote, US", "Remote - United States", "San Francisco, CA", "Remote (Canada)"):
        assert location_tier(abroad, abroad.startswith("Remote"), focus) == TIER_ABROAD, abroad
    jobs = [_job("AI Engineer Intern", url=f"https://careers.example/loc/{i}") for i in range(4)]
    for job, (loc, remote) in zip(jobs, [("Noida, India", False), ("Remote", True), ("New York, NY", False),
                                         ("Remote, US", True)], strict=True):
        job.location, job.is_remote = loc, remote
    kept, dropped = balance_by_location(jobs, focus)
    assert [j.location for j in kept] == ["Noida, India", "Remote"] and dropped == 2
    saved = make_job(role_title="AI Engineer Intern", description="LLM agents in Python." + LONG, location="New York, NY")
    assert any(r.startswith("Outside India") for r in filter_reasons(saved, AI_PY)[0])


# ------------------------------------------------------------------ a scan the app was stopped in
def test_a_scan_left_running_by_a_stopped_app_is_closed(auth_client: TestClient) -> None:
    from datetime import UTC, datetime, timedelta

    from app.models.agent_run import AgentRun

    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "jane@example.com").one()
        old = datetime.now(UTC) - timedelta(days=21)
        db.add(AgentRun(user_id=user.id, run_type="scan", trigger="user", status="running", started_at=old, log=[],
                        progress={"phase": "saving", "percent": 55, "updated_at": old.isoformat()}))
        db.commit()
    status = auth_client.get("/api/v1/agent/status").json()
    assert not any(r["run_type"] == "scan" for r in status["running_runs"])  # no frozen bar counting from weeks ago
    with SessionLocal() as db:
        run = db.query(AgentRun).one()
        assert run.status == "failed" and "Interrupted" in run.log[-1]["message"]
