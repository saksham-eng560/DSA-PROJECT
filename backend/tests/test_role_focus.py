"""Tech focus: AI / Python roles in, Java and data-science roles out (the "AI engineer · Python" preset)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.application import Application
from app.models.enums import ApplicationStatus, ATSPlatform, JobType
from app.models.job import Job
from app.models.user import User
from app.scrapers import SCRAPERS
from app.scrapers.base import ScrapedJob, SearchQuery
from app.scrapers.internshala import categories_for
from app.services import agent_orchestrator as orch
from app.services.job_matcher import evaluate_match, filter_reasons
from app.services.presets import AI_PYTHON_ROLES, apply_preset
from app.services.role_focus import drop_off_focus, focus_reasons

LONG = " We are a fast growing company in Delhi building products for millions of users. You will ship weekly." * 3
FOCUS = {"focus_skills": ["AI", "LLMs", "Generative AI", "AI agents", "RAG", "Machine Learning", "Python", "FastAPI",
                         "Django", "Flask", "PyTorch", "Hugging Face", "LangChain"],
         "avoid_skills": ["Java", "Spring Boot"],
         "keywords_exclude": ["Java", "Spring Boot", "Data Science", "Data Scientist", "Data Analyst", "Data Analytics",
                              "Business Analyst", "Business Analytics"]}


def _job(title: str, desc: str = LONG, *, internshala: bool = False, company: str = "Acme") -> ScrapedJob:
    slug = f"{company}-{title}".lower().replace(" ", "-")
    url = f"https://internshala.com/internship/detail/{slug}" if internshala else f"https://careers.example/{slug}"
    return ScrapedJob(company_name=company, role_title=title, description=desc, source_url=url, application_url=url,
                      source_platform=ATSPlatform.CUSTOM, job_type=JobType.INTERNSHIP, location="Delhi, India",
                      raw={"listing_source": "internshala"} if internshala else {}).finalize()


@pytest.mark.parametrize(("title", "desc", "internshala", "reason"), [
    ("Java Development Intern", "Java Development internship at Acme (via Internshala).", True, "Java role (you skip Java)"),
    ("Spring Boot Developer Intern", LONG, False, "Spring Boot role"),
    ("Backend Developer Intern", "Requirements: Java, Spring Boot, MySQL." + LONG, False, "Asks for Java, not Python / FastAPI"),
    ("Backend Developer Intern", "Our AI-powered platform. Requirements: Java." + LONG, False, "Asks for Java"),  # "AI" alone
    ("Backend Developer Intern", "Requirements: Java or Python; FastAPI a plus." + LONG, False, None),
    ("Software Engineering Intern", "Use Java, C++ or Python to build systems." + LONG, False, None),  # big tech SWE
    ("Software Engineering Intern", "", False, None),  # no description to judge: kept
    ("Software Engineering Intern", "Build services in Go and TypeScript." + LONG, False, "Not an AI / Python role"),
    ("Web Development Intern", "Web Development internship at Acme (via Internshala).", True, "Not an AI / Python role"),
    ("Python Development Intern", "Python Development internship at Acme (via Internshala).", True, None),
    ("Artificial Intelligence (AI) Intern", "AI internship at Acme (via Internshala).", True, None),
    ("GenAI Developer Intern", "Build LLM apps with LangChain and RAG." + LONG, False, None),
    ("JavaScript Developer Intern", "React and Node." + LONG, False, "Not an AI / Python role"),  # JavaScript isn't Java
    ("Backend Intern", "Python3, FastAPI and Postgres." + LONG, False, None),
])
def test_focus_for_an_ai_engineer(title: str, desc: str, internshala: bool, reason: str | None) -> None:
    reasons = focus_reasons(_job(title, desc, internshala=internshala), FOCUS)
    assert reasons == [] if reason is None else reasons and reason in reasons[0], reasons


def test_no_focus_no_filter() -> None:
    jobs = [_job("Java Development Intern"), _job("Data Science Intern")]
    assert drop_off_focus(jobs, {}) == (jobs, 0)


def test_the_preset_replaces_your_search_filters() -> None:
    mine = {"target_roles": ["Java Development Intern", "Web Developer Intern"], "max_applications_per_day": 25,
            "keywords_exclude": ["Senior"], "internships_only": False, "trusted_companies": ["Tiny Labs"],
            "sources": {"internshala_urls": ["https://internshala.com/internships/java-internship/",
                                             "https://internshala.com/internships/data-science-internship/",
                                             "https://internshala.com/internships/javascript-development-internship/"],
                        "greenhouse_boards": ["stripe"], "internship_lists": ["simplify-internships"]}}
    prefs = apply_preset(mine, "ai-python")
    assert prefs["target_roles"] == AI_PYTHON_ROLES and "Java Development Intern" not in prefs["target_roles"]
    assert prefs["sources"]["internshala_urls"] == ["https://internshala.com/internships/javascript-development-internship/"]
    assert "Senior" not in prefs["keywords_exclude"] and {"Java", "Data Science", "Frontend"} <= set(prefs["keywords_exclude"])
    assert "FastAPI" in prefs["focus_skills"] and "Java" in prefs["avoid_skills"]
    assert prefs["internships_only"] is True and prefs["job_types"] == ["internship"]
    # US startup boards and US internship lists are gone; your own limits and trusted companies stay
    assert prefs["sources"]["greenhouse_boards"] == [] and prefs["sources"]["internship_lists"] == []
    assert prefs["max_applications_per_day"] == 25 and prefs["trusted_companies"] == ["Tiny Labs"]
    # Internshala is searched in its AI, machine-learning, Python and backend categories
    assert categories_for(prefs["target_roles"])[:4] == ["machine-learning", "artificial-intelligence-ai",
                                                         "python-django", "backend-development"]


def test_data_science_titles_are_skipped() -> None:
    job = Job(company_name="Acme", role_title="Data Science Intern", description="Python and AI." + LONG,
              source_url="https://x/ds", source_platform=ATSPlatform.LEVER, job_type=JobType.INTERNSHIP)
    hard, _ = filter_reasons(job, FOCUS)
    assert hard == ["Title contains excluded keyword 'Data Science'"]


def test_the_ai_scoring_sees_your_focus(fake_llm: Any) -> None:
    provider = fake_llm({"JOB MATCH EVALUATION": {"evaluation": {
        "skills_match": 18, "experience_match": 16, "industry_match": 14, "location_match": 18, "compensation_match": 15,
        "proceed_with_application": True, "reasoning": "AI fit.", "missing_skills": [], "strong_matches": ["python"]}}})
    job = Job(company_name="Acme", role_title="AI Engineer Intern", description="LLM apps in Python." + LONG,
              source_url="https://x/ai", source_platform=ATSPlatform.LEVER, job_type=JobType.INTERNSHIP)
    evaluate_match({"skills": {"languages": ["Python"]}}, job, FOCUS, 60)
    prompt = provider.calls[0]["prompt"]
    assert '"focus_skills"' in prompt and "FastAPI" in prompt and '"avoid_skills"' in prompt and "Java" in prompt


# ------------------------------------------------------------------ in the app
class _Board:
    jobs: list[ScrapedJob] = []

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        return list(self.jobs)


def _user(db: Any, client: TestClient) -> User:
    return db.query(User).filter(User.email == client.get("/api/v1/auth/me").json()["email"]).one()


def test_scan_with_the_preset_keeps_ai_and_python_roles(auth_client: TestClient, master_resume: dict,
                                                        monkeypatch: Any) -> None:
    c = auth_client
    assert c.post("/api/v1/users/me/preferences/preset/ai-python").status_code == 200
    c.put("/api/v1/users/me/preferences", json={"preferences": {"target_roles": [], "location_focus": {"enabled": False},
                                                                "verified_companies_only": False}})
    jobs = [_job("Java Development Intern", "Java Development internship (via Internshala).", internshala=True),
            _job("Backend Developer Intern", "Requirements: Java, Spring Boot, MySQL." + LONG),
            _job("Web Development Intern", "Web Development internship (via Internshala).", internshala=True),
            _job("AI Engineer Intern", "Build LLM agents with Python and FastAPI." + LONG),
            _job("Python Development Intern", "Python Development internship (via Internshala).", internshala=True)]
    monkeypatch.setitem(SCRAPERS, "board", type("Board", (_Board,), {"jobs": jobs}))
    with SessionLocal() as db:
        run = orch.run_scan(db, _user(db, c), platforms=["board"])
        db.commit()
        assert run.status == "completed"
        # Java roles never; a web development card only as a marked near miss, because the deck would be thin
        assert sorted(j.role_title for j in db.query(Job).all()) == ["AI Engineer Intern", "Python Development Intern",
                                                                     "Web Development Intern"]
        log = " ".join(e["message"] for e in run.log)
        assert "Tech focus: left out 2 postings in Java" in log and "Near misses: 1 posting outside AI, LLMs" in log
        web = db.query(Application).join(Job).filter(Job.role_title == "Web Development Intern").one()
        assert web.status == ApplicationStatus.MATCHED and web.match_details["near_miss"]
        assert web.match_details["heads_up"][0] == "Outside your settings: Not an AI / Python role"
        fits = db.query(Application).join(Job).filter(Job.role_title == "AI Engineer Intern").one()
        assert fits.status == ApplicationStatus.MATCHED and not fits.match_details["near_miss"]
    # A full deck (or fill_deck_to 0) leaves near misses out, as before
    deck = [i["job"]["role_title"] for i in c.get("/api/v1/review/queue").json()["items"]]
    assert "Web Development Intern" in deck  # a near miss shown on purpose stays in the deck
    c.put("/api/v1/users/me/preferences", json={"preferences": {"fill_deck_to": 0}})
    monkeypatch.setitem(SCRAPERS, "board", type("Board", (_Board,), {"jobs": [
        _job("Content Writing Ops Intern", "Ops internship (via Internshala).", internshala=True)]}))
    with SessionLocal() as db:
        orch.run_scan(db, _user(db, c), platforms=["board"])
        db.commit()
        assert not db.query(Job).filter(Job.role_title == "Content Writing Ops Intern").count()


def test_auto_keep_never_keeps_a_near_miss(auth_client: TestClient, master_resume: dict, monkeypatch: Any) -> None:
    """A near miss is only on the deck because it was thin, marked with the setting it misses: you decide, never the
    agent (kept with "Submit automatically" on, it would be sent without you ever seeing it)."""
    c = auth_client
    queued: list[tuple] = []
    monkeypatch.setattr("app.worker.dispatch.enqueue", lambda *a, **k: queued.append(a))
    assert c.post("/api/v1/users/me/preferences/preset/ai-python").status_code == 200
    c.put("/api/v1/users/me/preferences", json={"preferences": {"target_roles": [], "location_focus": {"enabled": False},
                                                                "verified_companies_only": False,
                                                                "auto_keep_min_score": 0, "auto_submit_kept": True}})
    jobs = [_job("AI Engineer Intern", "Build LLM agents with Python and FastAPI." + LONG),
            _job("Web Development Intern", "Web Development internship (via Internshala).", internshala=True)]
    monkeypatch.setitem(SCRAPERS, "board", type("Board", (_Board,), {"jobs": jobs}))
    with SessionLocal() as db:
        run = orch.run_scan(db, _user(db, c), platforms=["board"])
        db.commit()
        assert run.status == "completed"
        apps = {a.job.role_title: a for a in db.query(Application).all()}
        near = apps["Web Development Intern"]
        assert near.match_details["near_miss"] and near.review_decision is None
        assert near.status == ApplicationStatus.MATCHED  # still waiting for your swipe
        assert apps["AI Engineer Intern"].review_decision == "keep"  # what fits your settings is kept as before
    assert queued == [("prepare_application", str(apps["AI Engineer Intern"].id))]
    assert [i["job"]["role_title"] for i in c.get("/api/v1/review/queue").json()["items"]] == ["Web Development Intern"]


def test_java_cards_already_waiting_are_skipped(auth_client: TestClient) -> None:
    with SessionLocal() as db:
        user = _user(db, auth_client)
        for title in ("Java Development Intern", "Generative AI Intern"):
            job = Job(company_name="Acme", role_title=title, description="Build things." + LONG, job_type=JobType.INTERNSHIP,
                      source_url=f"https://internshala.com/internship/detail/{title}", source_platform=ATSPlatform.CUSTOM,
                      raw_data={"listing_source": "internshala"})
            db.add(job)
            db.flush()
            db.add(Application(user_id=user.id, job_id=job.id, status=ApplicationStatus.MATCHED, match_score=70))
        db.commit()
    assert len(auth_client.get("/api/v1/review/queue").json()["items"]) == 2  # no focus yet: both wait
    auth_client.post("/api/v1/users/me/preferences/preset/ai-python")
    auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"verified_companies_only": False}})
    deck = auth_client.get("/api/v1/review/queue").json()["items"]
    assert [i["job"]["role_title"] for i in deck] == ["Generative AI Intern"]
    with SessionLocal() as db:
        skipped = db.query(Application).filter(Application.status == ApplicationStatus.SKIPPED).one()
        assert skipped.job.role_title == "Java Development Intern"
        assert skipped.match_reasoning == "Title contains excluded keyword 'Java'"


def test_focus_settings_are_validated(auth_client: TestClient) -> None:
    url = "/api/v1/users/me/preferences"
    for bad in ({"focus_skills": "Python"}, {"avoid_skills": [""]}, {"focus_skills": ["x" * 61]}, {"avoid_skills": [3]}):
        assert auth_client.put(url, json={"preferences": bad}).status_code == 422, bad
    ok = auth_client.put(url, json={"preferences": {"focus_skills": ["Python"], "avoid_skills": ["Java"]}})
    assert ok.status_code == 200 and ok.json()["avoid_skills"] == ["Java"]
