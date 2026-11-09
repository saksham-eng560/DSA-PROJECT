"""Faster scans: sources and boards in parallel, a time limit per source, parallel LLM scoring,
already-saved postings not re-downloaded, and a live progress bar with a Stop button."""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.agent_run import AgentRun
from app.models.application import Application
from app.models.enums import ATSPlatform
from app.models.user import User
from app.scrapers import SCRAPERS, SearchQuery
from app.scrapers.base import ScrapedJob
from app.scrapers.greenhouse import GreenhouseScraper
from app.scrapers.linkedin import LinkedInScraper
from app.services import agent_orchestrator as orch


def _job(i: int, board: str = "board") -> ScrapedJob:
    return ScrapedJob(company_name=f"{board.title()} {i}", role_title="Software Engineer Intern", location="Delhi, India",
                      description="Build Python and FastAPI services with PostgreSQL and Docker. " * 3,
                      source_url=f"https://{board}.example/jobs/{i}", source_platform=ATSPlatform.GREENHOUSE).finalize()


class _Board:
    name = "board"
    count = 3
    delay = 0.0

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        time.sleep(self.delay)
        for i in range(self.count):
            query.report(i + 1, self.count)
        return [_job(i, self.name) for i in range(self.count)]


def _board(name: str, count: int = 3, delay: float = 0.0) -> type:
    return type(f"Board_{name}", (_Board,), {"name": name, "count": count, "delay": delay})


def _user(db: Any) -> User:
    return db.query(User).filter(User.email == "jane@example.com").one()


def _scan(platforms: list[str]) -> AgentRun:
    from app.core.database import session_scope

    with session_scope() as db:
        run = orch.run_scan(db, _user(db), platforms=platforms)
        db.expunge(run)
        return run


# ------------------------------------------------------------------ sources
def test_sources_are_searched_at_the_same_time(monkeypatch) -> None:
    for name in ("slow_a", "slow_b", "slow_c"):
        monkeypatch.setitem(SCRAPERS, name, _board(name, delay=0.6))
    started = time.monotonic()
    jobs = orch.discover_jobs(SearchQuery(), ["slow_a", "slow_b", "slow_c"])
    assert len(jobs) == 9
    assert time.monotonic() - started < 1.5  # three 0.6 s sources, not 1.8 s one after another


def test_a_slow_source_is_left_out_not_waited_for(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "SCAN_SOURCE_TIMEOUT_SECONDS", 1)
    monkeypatch.setitem(SCRAPERS, "quick", _board("quick"))
    monkeypatch.setitem(SCRAPERS, "stuck", _board("stuck", delay=5))
    started = time.monotonic()
    jobs = orch.discover_jobs(SearchQuery(), ["quick", "stuck"])
    assert time.monotonic() - started < 3.5
    assert {j.company_name for j in jobs} == {"Quick 0", "Quick 1", "Quick 2"}


@respx.mock
def test_company_boards_load_in_parallel_and_one_failure_skips_only_that_board() -> None:
    def board(token: str, delay: float = 0.3) -> Any:
        def respond(request: httpx.Request) -> httpx.Response:
            time.sleep(delay)
            return httpx.Response(200, json={"jobs": [{"id": 1, "title": "Software Engineer Intern", "content": "x",
                                                       "absolute_url": f"https://job-boards.greenhouse.io/{token}/jobs/1",
                                                       "location": {"name": "Remote"}}]})
        return respond

    for token in ("alpha", "beta", "gamma", "delta"):
        respx.get(f"https://boards-api.greenhouse.io/v1/boards/{token}").mock(return_value=httpx.Response(200, json={"name": token.title()}))
        respx.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs").mock(side_effect=board(token))
    respx.get("https://boards-api.greenhouse.io/v1/boards/broken").mock(return_value=httpx.Response(403))
    respx.get("https://boards-api.greenhouse.io/v1/boards/broken/jobs").mock(return_value=httpx.Response(403))

    steps: list[tuple[int, int]] = []
    query = SearchQuery(keywords=["Software Engineer"], sources={"greenhouse_boards": ["alpha", "broken", "beta", "gamma", "delta"]},
                        progress=lambda done, total: steps.append((done, total)))
    started = time.monotonic()
    jobs = GreenhouseScraper().search(query)
    assert time.monotonic() - started < 1.0  # four 0.3 s boards at once
    assert [j.company_name for j in jobs] == ["Alpha", "Beta", "Gamma", "Delta"]  # original order, broken one skipped
    assert steps[0] == (0, 5) and steps[-1] == (5, 5)


@respx.mock
def test_linkedin_does_not_download_saved_postings_again() -> None:
    card = ('<li><div class="base-card" data-entity-urn="urn:li:jobPosting:{id}"><h3 class="base-search-card__title">'
            'Software Engineer Intern</h3><h4 class="base-search-card__subtitle">Acme</h4>'
            '<span class="job-search-card__location">Delhi, India</span></div></li>')
    respx.get(url__startswith="https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search").mock(
        side_effect=[httpx.Response(200, text=card.format(id=111) + card.format(id=222)), httpx.Response(200, text="")])
    detail = respx.get(url__startswith="https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/").mock(
        return_value=httpx.Response(200, text='<div class="show-more-less-html__markup">Build things with Python.</div>'))
    known = frozenset({"https://www.linkedin.com/jobs/view/111/"})
    jobs = LinkedInScraper().search(SearchQuery(keywords=["Software Engineer"], locations=["Delhi"], limit=10, known_urls=known))
    assert sorted(j.source_url for j in jobs) == ["https://www.linkedin.com/jobs/view/111/", "https://www.linkedin.com/jobs/view/222/"]
    assert [str(c.request.url).rsplit("/", 1)[-1] for c in detail.calls] == ["222"]


# ------------------------------------------------------------------ progress + stop
def test_scan_reports_progress_until_done(auth_client: TestClient, master_resume: dict, monkeypatch) -> None:
    from app.services import scan_progress

    pushed: list[dict] = []
    monkeypatch.setattr(scan_progress, "push_update", lambda uid, event, data: pushed.append(data["progress"]))
    monkeypatch.setitem(SCRAPERS, "board_a", _board("board_a"))
    monkeypatch.setitem(SCRAPERS, "board_b", _board("board_b", count=2))
    run = _scan(["board_a", "board_b"])

    assert run.status == "completed"
    assert run.progress["phase"] == "done" and run.progress["percent"] == 100
    assert {s["name"]: (s["status"], s["found"]) for s in run.progress["sources"]} == {
        "board_a": ("done", 3), "board_b": ("done", 2)}
    assert run.progress["new"] == 5 and run.progress["scored"] == 5 == run.progress["to_score"]
    phases = [p["phase"] for p in pushed]
    assert phases.index("discovering") < phases.index("saving") < phases.index("scoring") < phases.index("finishing")
    percents = [p["percent"] for p in pushed]
    assert percents == sorted(percents)  # the bar never goes backwards

    listed = auth_client.get("/api/v1/agent/runs").json()["items"][0]
    assert listed["progress"]["percent"] == 100


def test_stop_ends_a_running_scan(auth_client: TestClient, master_resume: dict, monkeypatch) -> None:
    gate = threading.Event()

    class Waiting(_Board):
        def search(self, query: SearchQuery) -> list[ScrapedJob]:
            gate.wait(10)
            return [_job(1, "waiting")]

    monkeypatch.setitem(SCRAPERS, "waiting", Waiting)
    result: dict[str, AgentRun] = {}
    worker = threading.Thread(target=lambda: result.setdefault("run", _scan(["waiting"])))
    worker.start()
    try:
        run_id = None
        for _ in range(100):
            running = auth_client.get("/api/v1/agent/status").json()["running_runs"]
            if running and running[0]["progress"]:
                run_id = running[0]["id"]
                break
            time.sleep(0.05)
        assert run_id, "the scan never reported progress"
        assert running[0]["progress"]["phase"] == "discovering"
        assert auth_client.post(f"/api/v1/agent/runs/{run_id}/cancel").status_code == 200
        worker.join(10)
    finally:
        gate.set()
        worker.join(10)
    assert result["run"].status == "cancelled"
    with SessionLocal() as db:
        assert db.query(Application).count() == 0  # nothing half-saved


# ------------------------------------------------------------------ parallel LLM scoring
def test_llm_scores_several_jobs_at_once(auth_client: TestClient, master_resume: dict, fake_llm, monkeypatch) -> None:
    from app.config import settings
    from app.services import job_matcher

    monkeypatch.setattr(settings, "SCAN_LLM_CONCURRENCY", 4)
    monkeypatch.setitem(SCRAPERS, "many", _board("many", count=8))
    fake_llm({"JOB MATCH EVALUATION": {"evaluation": {
        "skills_match": 18, "experience_match": 16, "industry_match": 14, "location_match": 20, "compensation_match": 15,
        "reasoning": "Strong Python fit.", "missing_skills": [], "strong_matches": ["Python"], "proceed_with_application": True}}})
    real = job_matcher.llm_evaluation
    active = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def slow_llm(*args: Any, **kwargs: Any) -> dict:
        with lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        time.sleep(0.25)
        try:
            return real(*args, **kwargs)
        finally:
            with lock:
                active["now"] -= 1

    monkeypatch.setattr(job_matcher, "llm_evaluation", slow_llm)
    started = time.monotonic()
    run = _scan(["many"])
    assert run.status == "completed"
    assert active["peak"] >= 3 and time.monotonic() - started < 1.8  # 8 x 0.25 s would take 2 s one by one
    with SessionLocal() as db:
        apps = db.query(Application).all()
        assert len(apps) == 8
        assert {a.match_score for a in apps} == {83} and {a.match_details["method"] for a in apps} == {"llm"}
        assert {a.status.value for a in apps} == {"matched"}


def test_sources_wrap_up_with_what_they_have_before_the_limit(monkeypatch) -> None:
    from app.config import settings

    calls: list[str] = []

    class Paced(_Board):
        """Works through five boards (0.3 s each) and stops starting new ones once its time is up."""

        def search(self, query: SearchQuery) -> list[ScrapedJob]:
            def board(name: str) -> list[ScrapedJob]:
                calls.append(name)
                time.sleep(0.3)
                return [_job(len(calls), "paced")]
            return self.map_sources(["a", "b", "c", "d", "e"], board, query, "board")

        map_sources = GreenhouseScraper.map_sources
        rate_key = "paced"

    monkeypatch.setattr(settings, "SCRAPER_BOARD_CONCURRENCY", 1)
    monkeypatch.setattr(settings, "SCAN_SOURCE_TIMEOUT_SECONDS", 1)  # wrap up after 0.8 s, give up after 1 s
    monkeypatch.setitem(SCRAPERS, "paced", Paced)
    jobs = orch.discover_jobs(SearchQuery(), ["paced"])
    assert 1 <= len(jobs) < 5 and len(calls) == len(jobs)  # partial results kept, remaining boards skipped
