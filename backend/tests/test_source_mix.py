"""A full, balanced Swipe Review: every job site in proportion, and near misses of your narrower wishes (tech focus,
internship length, verified companies only) filling a deck your settings would leave thin."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.application import Application
from app.models.enums import ApplicationStatus, ATSPlatform, JobType
from app.models.job import Job
from app.models.user import User
from app.scrapers import SCRAPERS
from app.scrapers.base import ScrapedJob, SearchQuery
from app.services import agent_orchestrator as orch
from app.services.source_mix import balance_sources, source_caps, source_of

LONG = " We are a fast growing product company in Delhi building AI tools for millions of users. You will ship weekly." * 3


def _job(title: str, n: int = 0, *, source: str = "board", desc: str = LONG, location: str = "Delhi, India",
         raw: dict | None = None) -> ScrapedJob:
    url = f"https://careers.example/{source}/{title.lower().replace(' ', '-')}-{n}"
    return ScrapedJob(company_name=f"Acme {n}", role_title=title, description=desc, source_url=url, application_url=url,
                      source_platform=ATSPlatform.CUSTOM, job_type=JobType.INTERNSHIP, location=location,
                      raw={"scan_source": source, **(raw or {})}).finalize()


# ------------------------------------------------------------------ proportions
def test_no_source_crowds_out_the_rest_when_there_is_plenty() -> None:
    caps = source_caps({"internshala": 300, "linkedin": 80, "top_companies": 40, "naukri": 30}, 40, floor=60)
    total = sum(caps.values())
    assert total > 60 and all(cap <= total * 40 // 100 + 1 for cap in caps.values())
    assert caps["top_companies"] == 40 and caps["naukri"] == 30  # small sources keep everything
    assert caps == {"internshala": 100, "linkedin": 80, "top_companies": 40, "naukri": 30}  # Internshala: 40 % of 250


def test_a_thin_scan_is_never_cut_for_proportions() -> None:
    # 40 % of what the small sources can match would keep ~13; the floor keeps 60 (or everything, if fewer)
    assert sum(source_caps({"internshala": 200, "linkedin": 5, "naukri": 3}, 40, floor=60).values()) == 60
    assert source_caps({"internshala": 20, "linkedin": 2}, 40, floor=60) == {"internshala": 20, "linkedin": 2}
    assert source_caps({"internshala": 500}, 40, floor=60) == {"internshala": 500}  # one source: nothing to balance


def test_balance_keeps_each_sources_best_and_known_postings() -> None:
    jobs = [_job("AI Intern", i, source="internshala", location="Mumbai, India") for i in range(50)]
    jobs[49].location = "Noida, India"  # the prime city is kept first
    jobs += [_job("AI Intern", i, source="linkedin") for i in range(10)]
    known = {jobs[0].source_url}
    kept, dropped = balance_sources(jobs, 40, _focus(), known=known, floor=20)
    assert jobs[0] in kept and jobs[49] in kept  # already yours: kept and not counted; prime city first
    assert dropped == {"internshala": 49 - sum(1 for j in kept if source_of(j) == "internshala" and j is not jobs[0])}
    assert sum(1 for j in kept if source_of(j) == "linkedin") == 10
    assert [j for j in jobs if j in kept] == kept  # original order


def _focus() -> Any:
    from app.services.location_focus import get_focus

    return get_focus({"location_focus": {"country": "India", "prime_cities": ["Noida"], "country_share": 100}})


# ------------------------------------------------------------------ a thin deck is filled
class _Board:
    jobs: list[ScrapedJob] = []

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        return list(self.jobs)


def _user(db: Any, client: TestClient) -> User:
    return db.query(User).filter(User.email == client.get("/api/v1/auth/me").json()["email"]).one()


def _scan(client: TestClient, monkeypatch: Any, jobs: list[ScrapedJob]) -> Any:
    monkeypatch.setitem(SCRAPERS, "board", type("Board", (_Board,), {"jobs": jobs}))
    with SessionLocal() as db:
        run = orch.run_scan(db, _user(db, client), platforms=["board"])
        db.commit()
        return run


def test_near_misses_fill_a_thin_deck_and_stay_there(auth_client: TestClient, master_resume: dict, monkeypatch: Any) -> None:
    c = auth_client
    assert c.post("/api/v1/users/me/preferences/preset/ai-python").status_code == 200
    c.put("/api/v1/users/me/preferences", json={"preferences": {"target_roles": [], "fill_deck_to": 5,
                                                                "verified_companies_only": False}})
    six_months = "Duration: 6 Months. Build LLM agents in Python." + LONG
    jobs = [_job("AI Engineer Intern", 1, desc="A 2-month internship building LLM agents in Python." + LONG,
                 raw={"top_company": "Acme"}),
            *[_job("Generative AI Intern", i, desc=six_months) for i in range(2, 10)],  # longer than you want
            _job("Java Developer Intern", 10)]  # a stack you skip: never
    run = _scan(c, monkeypatch, jobs)
    assert run.status == "completed"
    deck = c.get("/api/v1/review/queue").json()["items"]
    titles = [i["job"]["role_title"] for i in deck]
    assert len(deck) == 5 and "Java Developer Intern" not in titles  # 1 that fits + 4 near misses fill up to 5
    near = [i for i in deck if any(h.startswith("Outside your settings: 6 months internship") for h in i["heads_up"])]
    assert len(near) == 4
    log = " ".join(e["message"] for e in run.log)
    assert "showing 4 near misses too" in log and "This scan: board" in log
    with SessionLocal() as db:
        skipped = db.query(Application).filter(Application.status == ApplicationStatus.SKIPPED).all()
        assert {a.job.role_title for a in skipped} == {"Generative AI Intern"}  # the rest: skipped with the reason
        assert all("6 months internship" in (a.match_reasoning or "") for a in skipped)
        assert not db.query(Job).filter(Job.role_title == "Java Developer Intern").count()

    # A full deck: the next scan's near misses are left out
    run = _scan(c, monkeypatch, [_job("Generative AI Intern", i, desc=six_months) for i in range(20, 25)])
    assert len(c.get("/api/v1/review/queue").json()["items"]) == 5
    assert "near miss" not in " ".join(e["message"] for e in run.log)


def test_near_misses_the_full_deck_had_no_room_for_get_their_turn_later(auth_client: TestClient, master_resume: dict,
                                                                         monkeypatch: Any) -> None:
    """A full deck: a scan saved and skipped its near misses, and later scans finding them again never looked at
    them (they weren't new), so "shown only if Swipe Review would be thin" never happened for them."""
    c = auth_client
    c.post("/api/v1/users/me/preferences/preset/ai-python")
    c.put("/api/v1/users/me/preferences", json={"preferences": {"target_roles": [], "fill_deck_to": 3,
                                                                "verified_companies_only": False}})
    six_months = "Duration: 6 Months. Build LLM agents in Python." + LONG
    fits = [_job("AI Engineer Intern", i, desc="A 2-month internship building LLM agents in Python." + LONG,
                 raw={"top_company": "Acme"}) for i in range(1, 4)]
    near = [_job("Generative AI Intern", i, desc=six_months) for i in range(10, 14)]
    _scan(c, monkeypatch, fits + near)  # the deck is full with the 3 that fit: every near miss held back
    deck = c.get("/api/v1/review/queue").json()["items"]
    assert {i["job"]["role_title"] for i in deck} == {"AI Engineer Intern"} and len(deck) == 3

    for card in deck:  # you swipe the deck down to nothing
        assert c.post(f"/api/v1/review/{card['application_id']}", json={"decision": "skip"}).status_code == 200
    run = _scan(c, monkeypatch, fits + near)  # found again: the deck is thin now
    deck = c.get("/api/v1/review/queue").json()["items"]
    assert [i["job"]["role_title"] for i in deck] == ["Generative AI Intern"] * 3  # up to fill_deck_to
    assert all(any(h.startswith("Outside your settings: 6 months") for h in i["heads_up"]) for i in deck)
    assert "get their turn" in " ".join(e["message"] for e in run.log)
    with SessionLocal() as db:  # the ones you skipped stay skipped
        assert all(a.status == ApplicationStatus.SKIPPED and a.review_decision == "skip"
                   for a in db.query(Application).join(Job).filter(Job.role_title == "AI Engineer Intern"))


def test_unverified_companies_are_near_misses_too(auth_client: TestClient, master_resume: dict, monkeypatch: Any) -> None:
    c = auth_client
    c.post("/api/v1/users/me/preferences/preset/ai-python")
    c.put("/api/v1/users/me/preferences", json={"preferences": {"target_roles": [], "location_focus": {"enabled": False}}})
    jobs = [_job("AI Engineer Intern", i, source="internshala",
                 raw={"listing_source": "internshala"}) for i in range(3)]
    _scan(c, monkeypatch, jobs)
    deck = c.get("/api/v1/review/queue").json()["items"]
    assert len(deck) == 3  # small unknown companies wait for your swipe instead of vanishing
    with SessionLocal() as db:
        for app in db.query(Application).all():
            assert app.status == ApplicationStatus.MATCHED
            if app.job.company_verdict == "unverified":
                assert app.match_details["near_miss"]
                assert any("isn't a verified company" in h for h in app.match_details["heads_up"])


def test_the_presets_earlier_sources_are_upgraded_once_and_a_choice_of_yours_stays(auth_client: TestClient) -> None:
    """Every scan swapped a saved list equal to the preset's earlier 4 sources for today's 7: you unchecked Naukri,
    Unstop and Remote boards, Settings showed 4, and every scan searched 7."""
    from app.services.presets import EARLIER_PLATFORMS, PLATFORMS, upgrade_earlier_platforms

    assert {"naukri", "unstop", "remote_boards"} <= set(PLATFORMS) <= set(SCRAPERS)
    earlier = list(EARLIER_PLATFORMS[0])
    assert orch.scan_platforms({"platforms": earlier, "scan_top_companies": True}) == ["top_companies", *earlier]
    assert orch.scan_platforms({"platforms": earlier}, ["indeed"]) == ["indeed"]

    # Saved before the new sources existed (no marker): upgraded once, at start-up
    with SessionLocal() as db:
        user = db.query(User).filter_by(email="jane@example.com").one()
        user.preferences = {k: v for k, v in user.preferences.items() if k != "platforms_upgraded"} | {"platforms": earlier}
        db.commit()
    from app.main import app as api

    with TestClient(api):
        pass
    assert auth_client.get("/api/v1/users/me/preferences").json()["platforms"] == PLATFORMS

    # You choose those same 4 yourself: they stay yours, at every scan and every restart
    r = auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"platforms": earlier}})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        assert upgrade_earlier_platforms(db) == 0
        user = db.query(User).filter_by(email="jane@example.com").one()
        assert user.preferences["platforms"] == earlier
        assert orch.scan_platforms(user.prefs) == ["top_companies", *earlier]


def test_a_related_title_is_not_flagged_as_off_target() -> None:
    from types import SimpleNamespace

    from app.services.job_matcher import filter_reasons
    from app.services.presets import apply_preset

    prefs = apply_preset({})

    def flagged(title: str) -> bool:
        job = SimpleNamespace(company_name="X", role_title=title, description="Python and LLM work. " * 30, job_type=None,
                              deadline_date=None, raw_data={}, location="Delhi, India", is_remote=False, posted_date=None,
                              requirements=None, source_url="https://x.example/1", company_verdict="verified",
                              company_tier=None, company_check={})
        return "Title does not match your target roles" in filter_reasons(job, prefs)[1]

    assert not flagged("AI/ML Intern") and not flagged("Machine Learning Intern")  # the scan kept them as your roles
    assert flagged("Marketing Intern")


def test_internshala_is_not_starved_when_the_other_sites_have_little() -> None:
    from app.services.source_mix import internshala_allowance

    # Seen in a scan of the live sites: 13 Internshala postings that fit, 2 from Unstop. The share alone kept 3.
    assert internshala_allowance(2, share=50, per_scan=40) == 40
    assert internshala_allowance(200, share=50, per_scan=40) == 40  # plenty elsewhere: the share decides
    assert internshala_allowance(30, share=25, per_scan=50) == 30  # 60 in all: the floor, not 25 % of 30 (10)
    assert internshala_allowance(2, share=0, per_scan=40) == 0  # you turned Internshala off
