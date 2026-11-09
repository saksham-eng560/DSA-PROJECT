"""Swipe Review, internship lists and the mass-apply presets."""

from __future__ import annotations

import uuid

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.application import Application
from app.models.enums import ApplicationStatus, ATSPlatform, ExperienceLevel, JobType
from app.models.job import Job
from app.models.user import User
from app.scrapers import SCRAPERS, SearchQuery
from app.scrapers.internships import LISTS, InternshipListScraper, _cache, clean_url
from app.services.agent import preparation, scoring
from app.services.job_matcher import filter_reasons, prefilter
from app.services.presets import apply_preset
from tests.conftest import FIXTURES

LISTING = (FIXTURES / "scrapers" / "internship_listings.json").read_text()


def make_job(**kw) -> Job:  # type: ignore[no-untyped-def]
    base = dict(company_name="Acme", role_title="Software Engineer Intern", description="Python role",
                source_url="https://x/1", source_platform=ATSPlatform.GREENHOUSE, job_type=JobType.INTERNSHIP,
                is_remote=False, location="Austin, TX")
    base.update(kw)
    return Job(**base)


# ------------------------------------------------------------------ internship lists
@respx.mock
def test_internship_list_scraper() -> None:
    _cache.clear()
    respx.get(LISTS["simplify-internships"]).mock(return_value=httpx.Response(200, text=LISTING))
    assert "internships" in SCRAPERS
    query = SearchQuery(keywords=["Software Engineer", "Machine Learning"], posted_within_days=3650, limit=50,
                        sources={"internship_lists": ["simplify-internships"]})
    jobs = InternshipListScraper().search(query)
    titles = [j.role_title for j in jobs]
    # inactive, hidden, broken and off-target (design) listings are dropped; newest first
    assert titles == ["Software Engineer Intern", "Machine Learning Engineering Intern", "Software Engineer - Summer 2027"]
    figma = jobs[0]
    assert figma.source_url == "https://job-boards.greenhouse.io/figma/jobs/6131089004"
    assert figma.source_platform == ATSPlatform.GREENHOUSE and figma.job_type == JobType.INTERNSHIP
    assert figma.experience_level == ExperienceLevel.INTERNSHIP
    assert figma.raw["listing_source"] == "simplify-internships" and figma.raw["sponsorship"] == "Offers Sponsorship"
    assert "San Francisco, CA" in figma.location and "Summer 2027" in figma.description
    ramp = jobs[1]
    assert ramp.is_remote and ramp.source_platform == ATSPlatform.ASHBY
    # A title without the word "intern" is still an internship when it comes from an internship list
    assert jobs[2].job_type == JobType.INTERNSHIP
    # cached: a second scan doesn't download 10 MB again
    InternshipListScraper().search(query)
    assert respx.calls.call_count == 1


def test_clean_url_keeps_real_params() -> None:
    assert clean_url("https://x.io/apply?gh_jid=12&utm_source=Simplify&ref=Simplify") == "https://x.io/apply?gh_jid=12"


# ------------------------------------------------------------------ filters
def test_hard_and_soft_filters() -> None:
    prefs = {"target_roles": ["Data Scientist"], "remote_preference": "remote", "job_types": ["internship"],
             "companies_to_avoid": ["Evil"], "exclude_no_sponsorship": True}
    hard, soft = filter_reasons(make_job(), prefs)
    assert hard == [] and "Not a remote role" in soft and "Title does not match your target roles" in soft
    assert prefilter(make_job(), prefs, strict=False) == (True, None)  # swipe mode: you decide
    assert prefilter(make_job(), prefs)[0] is False  # auto mode keeps the old behaviour
    assert not prefilter(make_job(company_name="Evil Inc"), prefs, strict=False)[0]
    assert prefilter(make_job(role_title="Software Engineer", job_type=JobType.FULL_TIME), prefs, strict=False) == (
        False, "Not an internship (full-time role)")
    # LinkedIn lists many internships as "Full-time": an intern title is enough
    assert prefilter(make_job(job_type=JobType.FULL_TIME), prefs, strict=False)[0]
    no_visa = make_job(raw_data={"sponsorship": "Does Not Offer Sponsorship"})
    assert prefilter(no_visa, prefs, strict=False) == (False, "The posting says it does not sponsor visas")
    assert prefilter(no_visa, {**prefs, "exclude_no_sponsorship": False}, strict=False)[0]


# ------------------------------------------------------------------ presets
def test_the_one_preset() -> None:
    prefs = apply_preset({"target_roles": ["Software Engineer"], "max_applications_per_day": 10,
                          "sources": {"greenhouse_boards": ["mycompany"]}}, "ai-python")
    assert prefs["target_roles"][0] == "AI Engineer Intern" and prefs["job_types"] == ["internship"]
    assert prefs["review_mode"] == "swipe" and prefs["auto_submit_kept"] is True
    assert prefs["max_applications_per_day"] == 10 and prefs["sources"]["greenhouse_boards"] == []
    assert "internshala" in prefs["platforms"] and "internships" not in prefs["platforms"]
    with pytest.raises(ValueError):
        apply_preset({}, "internships")


def test_preset_endpoint(auth_client: TestClient) -> None:
    r = auth_client.post("/api/v1/users/me/preferences/preset/ai-python")
    assert r.status_code == 200 and r.json()["job_types"] == ["internship"]
    assert auth_client.post("/api/v1/users/me/preferences/preset/internships").status_code == 422
    bad = auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"review_mode": "yolo"}})
    assert bad.status_code == 422
    assert auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"auto_keep_min_score": 70}}).status_code == 200


# ------------------------------------------------------------------ review API
def _seed_queue(email: str, n: int = 4) -> list[str]:
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        ids = []
        for i in range(n):
            job = make_job(company_name=f"Startup {i}", role_title=f"Software Engineer Intern {i}",
                           source_url=f"https://jobs.example/{email}/{i}", dedupe_key=f"{email}-{i}",
                           is_remote=i % 2 == 0, company_verdict="verified")  # the company check passed
            db.add(job)
            db.flush()
            app = Application(user_id=user.id, job_id=job.id, status=ApplicationStatus.MATCHED, match_score=40 + i * 15,
                              match_details={"strong_matches": ["Python"], "heads_up": ["Not a remote role"]})
            db.add(app)
            db.flush()
            ids.append(str(app.id))
        db.commit()
        return ids


def test_review_queue_decisions(auth_client: TestClient, master_resume: dict, monkeypatch) -> None:
    queued: list[tuple] = []
    monkeypatch.setattr("app.worker.dispatch.enqueue", lambda *a, **k: queued.append(a))
    email = auth_client.get("/api/v1/auth/me").json()["email"]
    ids = _seed_queue(email)
    # Score order on its own (the India / Delhi location focus is tested in test_location_focus.py)
    auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"location_focus": {"enabled": False}}})

    deck = auth_client.get("/api/v1/review/queue").json()
    assert [c["match_score"] for c in deck["items"]] == [85, 70, 55, 40]  # best first
    assert deck["stats"]["remaining"] == 4 and deck["settings"]["auto_submit_kept"] is True
    assert deck["items"][0]["heads_up"] == ["Not a remote role"] and deck["has_master_resume"]
    assert auth_client.get("/api/v1/review/queue?min_score=60").json()["stats"]["remaining"] == 4
    assert auth_client.get("/api/v1/review/queue?min_score=60").json()["matching"] == 2
    assert len(auth_client.get("/api/v1/review/queue?min_score=60").json()["items"]) == 2
    assert len(auth_client.get("/api/v1/review/queue?remote=true").json()["items"]) == 2

    top = deck["items"][0]["application_id"]
    r = auth_client.post(f"/api/v1/review/{top}", json={"decision": "keep"})
    assert r.status_code == 200 and r.json()["application"]["status"] == "preparing"
    assert queued and queued[-1][0] == "prepare_application"
    assert r.json()["stats"]["kept_today"] == 1 and r.json()["stats"]["remaining"] == 3
    assert auth_client.post(f"/api/v1/review/{top}", json={"decision": "skip"}).status_code == 409

    # Undo a keep before preparation starts: the prepare task becomes a no-op
    assert auth_client.post(f"/api/v1/review/{top}/undo").status_code == 200
    from app.core.database import session_scope
    from app.services import agent_orchestrator as orch

    with session_scope() as db:
        assert orch.prepare_application(db, top).status == ApplicationStatus.MATCHED

    r = auth_client.post("/api/v1/review/bulk", json={"decision": "skip", "min_score": 0, "application_ids": ids[:2]})
    assert r.json()["count"] == 2
    r = auth_client.post("/api/v1/review/bulk", json={"decision": "keep", "min_score": 50})
    assert r.json()["count"] == 2 and r.json()["stats"]["remaining"] == 0
    assert auth_client.get("/api/v1/agent/status").json()["to_review"] == 0
    # Kept and skipped jobs are out of the deck; skipped ones don't clutter Applications
    listed = {a["id"] for a in auth_client.get("/api/v1/applications").json()["items"]}
    assert ids[0] not in listed and ids[3] in listed


def test_review_requires_resume_and_scopes_users(auth_client: TestClient, client: TestClient) -> None:
    email = auth_client.get("/api/v1/auth/me").json()["email"]
    ids = _seed_queue(email, 1)
    assert auth_client.post(f"/api/v1/review/{ids[0]}", json={"decision": "keep"}).status_code == 400
    client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "password-1234",
                                               "full_name": "Other"})
    assert client.post(f"/api/v1/review/{ids[0]}", json={"decision": "skip"}).status_code == 404
    assert client.get("/api/v1/review/queue").json()["stats"]["remaining"] == 0


def test_sqlite_upgrade_adds_new_columns() -> None:
    """Local SQLite installs from an older version gain the Swipe Review columns on start-up."""
    import pytest
    from sqlalchemy import inspect, text

    from app.core.database import create_all, engine

    if engine.dialect.name != "sqlite":
        pytest.skip("SQLite only")
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE applications DROP COLUMN auto_submit"))
        conn.execute(text("ALTER TABLE applications DROP COLUMN review_decision"))
    create_all()
    columns = {c["name"] for c in inspect(engine).get_columns("applications")}
    assert {"auto_submit", "review_decision", "reviewed_at"} <= columns


# ------------------------------------------------------------------ races & stale tasks
def _capture_enqueue(monkeypatch) -> list[tuple]:  # type: ignore[no-untyped-def]
    queued: list[tuple] = []
    monkeypatch.setattr("app.worker.dispatch.enqueue", lambda *a, **k: queued.append(a))
    return queued


def _prepare(app_id: str) -> None:
    from app.core.database import session_scope
    from app.services import agent_orchestrator as orch

    with session_scope() as db:
        orch.prepare_application(db, app_id, stage=False)


def test_undo_skip_only_while_skipped(auth_client: TestClient, master_resume: dict, monkeypatch) -> None:
    _capture_enqueue(monkeypatch)
    app_id = _seed_queue(auth_client.get("/api/v1/auth/me").json()["email"], 1)[0]
    assert auth_client.post(f"/api/v1/review/{app_id}", json={"decision": "skip"}).status_code == 200
    # ...then applied to by hand from the Jobs / Applications pages: undo must not pull it back to the deck
    assert auth_client.post(f"/api/v1/applications/{app_id}/mark-applied").status_code == 200
    assert auth_client.post(f"/api/v1/review/{app_id}/undo").status_code == 409
    assert auth_client.get(f"/api/v1/applications/{app_id}").json()["status"] == "applied"


def test_stale_prepare_tasks_never_submit_twice(auth_client: TestClient, master_resume: dict, monkeypatch) -> None:
    queued = _capture_enqueue(monkeypatch)
    first, second = _seed_queue(auth_client.get("/api/v1/auth/me").json()["email"], 2)
    # keep -> undo -> keep before the worker picks anything up: two prepare tasks for one job
    assert auth_client.post(f"/api/v1/review/{first}", json={"decision": "keep"}).status_code == 200
    assert auth_client.post(f"/api/v1/review/{first}/undo").status_code == 200
    assert auth_client.post(f"/api/v1/review/{first}", json={"decision": "keep"}).status_code == 200
    # kept, then skipped from Applications before its task ran
    assert auth_client.post(f"/api/v1/review/{second}", json={"decision": "keep"}).status_code == 200
    assert auth_client.post(f"/api/v1/applications/{second}/skip").status_code == 200

    tasks = [a[1] for a in queued if a[0] == "prepare_application"]
    assert tasks == [first, first, second]
    for app_id in tasks:
        _prepare(app_id)
    assert [a for a in queued if a[0] == "submit_application"] == [("submit_application", first)]
    assert auth_client.get(f"/api/v1/applications/{second}").json()["status"] == "skipped"


def test_undo_during_preparation_is_respected(auth_client: TestClient, master_resume: dict, monkeypatch) -> None:

    queued = _capture_enqueue(monkeypatch)
    app_id = _seed_queue(auth_client.get("/api/v1/auth/me").json()["email"], 1)[0]
    auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"resume_strategy": "full"}})
    assert auth_client.post(f"/api/v1/review/{app_id}", json={"decision": "keep"}).status_code == 200
    real_tailor = preparation.tailor_resume

    def tailor_then_undo(*args, **kwargs):  # type: ignore[no-untyped-def]
        result = real_tailor(*args, **kwargs)
        assert auth_client.post(f"/api/v1/review/{app_id}/undo").status_code == 200  # swiped back mid-way
        return result

    monkeypatch.setattr(preparation, "tailor_resume", tailor_then_undo)
    _prepare(app_id)
    assert [a for a in queued if a[0] == "submit_application"] == []
    detail = auth_client.get(f"/api/v1/applications/{app_id}").json()
    assert detail["status"] == "matched"
    assert [c["application_id"] for c in auth_client.get("/api/v1/review/queue").json()["items"]] == [app_id]


class _FakeBoard:
    """Three internship postings, offline."""

    def search(self, query: SearchQuery) -> list:
        from app.scrapers.base import ScrapedJob

        return [ScrapedJob(company_name=f"Board {i}", role_title="Software Engineer Intern", location="Austin, TX",
                           description="Build Python and FastAPI services with PostgreSQL and Docker. " * 3,
                           source_url=f"https://board.example/jobs/{i}", source_platform=ATSPlatform.GREENHOUSE).finalize()
                for i in range(3)]


def _scan(email: str) -> None:
    from app.core.database import session_scope
    from app.services import agent_orchestrator as orch

    with session_scope() as db:
        user = db.query(User).filter(User.email == email).one()
        orch.run_scan(db, user, platforms=["fakeboard"])


def _app_for_job(job_id) -> Application:  # type: ignore[no-untyped-def]
    with SessionLocal() as db:
        return db.query(Application).filter(Application.job_id == job_id).one()


def test_scan_respects_swipes_made_while_it_runs(auth_client: TestClient, master_resume: dict, monkeypatch) -> None:
    """The scan commits as it goes, so you can swipe cards before it is done with them."""

    queued = _capture_enqueue(monkeypatch)
    monkeypatch.setitem(SCRAPERS, "fakeboard", _FakeBoard)
    assert auth_client.put("/api/v1/users/me/preferences", json={"preferences": {
        "target_roles": ["Software Engineer"], "auto_keep_min_score": 0}}).status_code == 200
    real_evaluate = scoring.evaluate_match
    evaluated: list = []

    def evaluate_and_swipe(resume, job, *args, **kwargs):  # type: ignore[no-untyped-def]
        evaluated.append(job.id)
        if len(evaluated) == 1:  # skip a card the scan hasn't scored yet
            with SessionLocal() as db:
                other = db.query(Application).filter(Application.job_id != job.id).first()
            assert auth_client.post(f"/api/v1/review/{other.id}", json={"decision": "skip"}).status_code == 200
        if len(evaluated) == 2:  # skip the card it has just scored, before auto-keep runs
            assert auth_client.post(f"/api/v1/review/{_app_for_job(evaluated[0]).id}",
                                    json={"decision": "skip"}).status_code == 200
        return real_evaluate(resume, job, *args, **kwargs)

    monkeypatch.setattr(scoring, "evaluate_match", evaluate_and_swipe)
    _scan(auth_client.get("/api/v1/auth/me").json()["email"])

    assert len(evaluated) == 2  # the card skipped mid-scan is not scored (and un-skipped) afterwards
    with SessionLocal() as db:
        apps = db.query(Application).all()
        decisions = sorted((a.status.value, a.review_decision) for a in apps)
    assert decisions == [("preparing", "keep"), ("skipped", "skip"), ("skipped", "skip")]
    assert [a[0] for a in queued] == ["prepare_application"]


def test_no_write_lock_held_through_llm_calls(auth_client: TestClient, master_resume: dict, monkeypatch) -> None:
    """SQLite (local mode): scans and preparations never hold the write lock while waiting on an LLM,
    so swipes and other writes don't fail with "database is locked" meanwhile."""
    import sqlite3

    import pytest

    from app.core.database import engine
    from app.scrapers.base import ScrapedJob

    if engine.dialect.name != "sqlite":
        pytest.skip("SQLite only")

    def can_write() -> bool:
        conn = sqlite3.connect(engine.url.database, timeout=0.05)
        try:
            conn.execute("UPDATE users SET full_name = full_name")
            conn.commit()
            return True
        except sqlite3.OperationalError:
            return False
        finally:
            conn.close()

    probes: list[tuple[str, bool]] = []

    def probing(name, fn):  # type: ignore[no-untyped-def]
        def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
            if kwargs.get("use_llm", True):
                probes.append((name, can_write()))
            return fn(*args, **kwargs)
        return wrapper

    queued = _capture_enqueue(monkeypatch)
    monkeypatch.setitem(SCRAPERS, "fakeboard", _FakeBoard)
    for module, name in ((scoring, "evaluate_match"), (preparation, "tailor_resume"), (preparation, "generate_cover_letter")):
        monkeypatch.setattr(module, name, probing(name, getattr(module, name)))
    monkeypatch.setattr("app.services.agent.jobs.fetch_job_from_url", lambda url: ScrapedJob(
        company_name="Board 0", role_title="Software Engineer Intern", description="Full description. " * 60,
        source_url=url, source_platform=ATSPlatform.GREENHOUSE))
    auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"target_roles": ["Software Engineer"],
                                                                          "resume_strategy": "full"}})
    _scan(auth_client.get("/api/v1/auth/me").json()["email"])
    assert [p for p in probes if p[0] == "evaluate_match"] == [("evaluate_match", True)] * 3

    app_id = auth_client.get("/api/v1/review/queue").json()["items"][0]["application_id"]
    with SessionLocal() as db:  # a curated-list posting: preparation first fetches the full description
        db.get(Application, uuid.UUID(app_id)).job.raw_data = {"listing_source": "simplify-internships"}
        db.commit()
    assert auth_client.post(f"/api/v1/review/{app_id}", json={"decision": "keep"}).status_code == 200
    probes.clear()
    _prepare(app_id)
    assert probes == [("tailor_resume", True), ("generate_cover_letter", True)]
    assert queued[-1] == ("submit_application", app_id)


def test_env_inline_comments_are_not_values(tmp_path) -> None:
    """`PROXY_URLS=   # comment` used to become a bogus proxy and break every browser session."""
    from app.config import Settings

    env = tmp_path / ".env"
    env.write_text("PROXY_URLS=    # comma-separated http://user:pass@host:port (A, B)\nGMAIL_PUBSUB_TOPIC=  # x\n")
    s = Settings(_env_file=str(env))
    assert s.proxy_urls == [] and s.GMAIL_PUBSUB_TOPIC is None
    # Newer python-dotenv hands over "" for `KEY=  # comment`: an optional setting still means "not set"
    s = Settings(_env_file=None, GMAIL_PUBSUB_TOPIC="", OLLAMA_API_KEY="  ", REDIS_URL="")
    assert s.GMAIL_PUBSUB_TOPIC is None and s.OLLAMA_API_KEY is None and s.REDIS_URL == ""
    # A DEBUG exported by another tool (e.g. `DEBUG=release` in a Mac shell) must not block startup
    assert Settings(_env_file=None, DEBUG="release").DEBUG is False
    assert Settings(_env_file=None, DEBUG="").DEBUG is False
    assert Settings(_env_file=None, DEBUG="true").DEBUG is True


# ------------------------------------------------------------------ which resume is sent
def test_resume_strategies(auth_client: TestClient, monkeypatch) -> None:
    import io

    from app.core.database import session_scope
    from app.core.storage import get_storage
    from app.services import agent_orchestrator as orch
    from app.services.pdf_generator import render_resume_pdf
    from app.services.resume_parser import heuristic_parse
    from tests.conftest import SAMPLE_RESUME_TEXT

    _capture_enqueue(monkeypatch)
    original = render_resume_pdf(heuristic_parse(SAMPLE_RESUME_TEXT)) + b"%ORIGINAL-MARKER"
    r = auth_client.post("/api/v1/resumes/upload", files={"file": ("my.pdf", io.BytesIO(original), "application/pdf")})
    assert r.status_code == 201
    master_content = r.json()["parsed_content"]
    email = auth_client.get("/api/v1/auth/me").json()["email"]
    first, second = _seed_queue(email, 2)
    assert auth_client.get("/api/v1/users/me/preferences").json()["resume_strategy"] == "original"

    # original (default): the uploaded file is sent byte for byte
    auth_client.post(f"/api/v1/review/{first}", json={"decision": "keep"})
    _prepare(first)
    with session_scope() as db:
        app = db.get(Application, uuid.UUID(first))
        assert app.tailored_resume_id is None
        assert get_storage().read(app.tailored_resume_pdf_url) == original

    # light: every sentence you wrote survives unchanged, only the order moves
    assert auth_client.put("/api/v1/users/me/preferences",
                           json={"preferences": {"resume_strategy": "light"}}).status_code == 200
    auth_client.post(f"/api/v1/review/{second}", json={"decision": "keep"})
    _prepare(second)
    detail = auth_client.get(f"/api/v1/applications/{second}").json()
    light = detail["tailored_resume"]["parsed_content"]
    assert light["summary"] == master_content["summary"]
    for before, after in zip(master_content["experience"], light["experience"], strict=True):
        assert sorted(before["bullets"]) == sorted(after["bullets"])
    assert sorted(light["skills"]["technical"]) == sorted(master_content["skills"]["technical"])
    assert auth_client.put("/api/v1/users/me/preferences",
                           json={"preferences": {"resume_strategy": "rewrite-it"}}).status_code == 422
    assert orch.RESUME_STRATEGIES == ("original", "light", "full")
