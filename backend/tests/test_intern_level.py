"""Intern-level roles only (and only ones open to your year of study), and at most 10 new Internshala postings a scan."""

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
from app.services import agent_orchestrator as orch
from app.services.intern_level import (
    Student,
    academic_year_end,
    drop_ineligible,
    eligibility_reasons,
    graduation_year_from_resume,
    is_internship,
    student,
    with_resume,
)
from app.services.source_mix import cap_internshala, internshala_allowance

GRAD = academic_year_end() + 2  # a 2nd-year student on a 4-year degree, whenever the tests run
YOU = Student(2, GRAD, "you")
LONG = " We build products used by millions of people across India. You'll work with our engineering team." * 3


def _job(company: str, title: str = "Software Engineer Intern", *, desc: str = LONG, internshala: bool = False,
         job_type: JobType | None = JobType.INTERNSHIP, url: str | None = None) -> ScrapedJob:
    slug = f"{company}-{title}".lower().replace(" ", "-")
    url = url or (f"https://internshala.com/internship/detail/{slug}" if internshala else f"https://careers.example/{slug}")
    return ScrapedJob(company_name=company, role_title=title, description=desc, source_url=url, application_url=url,
                      source_platform=ATSPlatform.CUSTOM, location="Delhi, India", job_type=job_type,
                      raw={"listing_source": "internshala"} if internshala else {}).finalize()


# ------------------------------------------------------------------ is it an internship?
@pytest.mark.parametrize(("title", "expected"), [
    ("Software Engineer Intern", True),
    ("Product Manager Intern", True),        # "manager" in an intern title is still an internship
    ("Campus Recruiting Intern", True),
    ("Software Engineering Internship Program 2027", True),
    ("Student Researcher", True),
    ("Summer Analyst", True),
    ("Intern Program Manager", False),       # full-time jobs about interns
    ("University Recruiter, Internships", False),
    ("Recruiter - Early Careers & Interns", False),
    ("Senior Software Engineer", False),
    ("Graduate Engineer Trainee", False),
    ("Internal Tools Engineer", False),
])
def test_only_intern_titles_count(title: str, expected: bool) -> None:
    assert is_internship(title, JobType.FULL_TIME) is expected


def test_an_internship_job_type_counts_without_intern_in_the_title() -> None:
    assert is_internship("Software Engineer - Summer 2027", JobType.INTERNSHIP)
    assert not is_internship("Intern Program Manager", JobType.INTERNSHIP)


# ------------------------------------------------------------------ is it for a 2nd-year student?
@pytest.mark.parametrize(("title", "text", "reason"), [
    ("Software Engineer Intern", "Students pursuing a Bachelor's or Master's in CS.", None),
    ("Research Intern", "Must be currently enrolled in a PhD program in Machine Learning.", "PhD / Master's / MBA program"),
    ("Research Intern", "Candidates must be enrolled in a PhD program.", "PhD / Master's / MBA program"),
    ("MBA Intern", "", "For PhD / Master's / MBA students"),
    ("Data Science Intern (BS/MS)", "", None),
    ("SDE Intern", "Open to final year students.", "Only for final year students (you're in 2nd year)"),
    ("SDE Intern", "Eligibility: pre-final year B.Tech students.", "Only for pre-final year students"),
    ("SDE Intern", "Only for 3rd and 4th year students.", "Only for 3rd and 4th year students"),
    ("SDE Intern", "We are looking for rising seniors.", "Only for rising seniors (you're in 2nd year)"),
    ("SDE Intern", "Open to 2nd and 3rd year students.", None),
    ("SDE Intern", "Open to 1st to 3rd year students.", None),
    ("STEP Intern", "This program is for first and second year students.", None),
    ("SDE Intern", "Open to rising juniors and seniors.", None),  # the summer after your 2nd year
    ("SDE Intern", "Interns will be mentored for the first year of the program.", None),
    ("SDE Intern", "Open to all students irrespective of year.", None),
    ("SDE Intern", "Recent graduates who have completed their degree.", "For graduates, not current students"),
    ("SDE Intern", "Open to students and recent graduates.", None),
    ("SDE Intern", "Requires 2+ years of experience in Java.", "Asks for 2+ years of work experience"),
    ("SDE Intern", "Must have completed 2 years of study and have experience with Python.", None),
    ("SDE Intern", "0-1 years of experience.", None),
])
def test_eligibility_for_a_second_year_student(title: str, text: str, reason: str | None) -> None:
    reasons = eligibility_reasons(title, f"{title}\n{text}", YOU)
    if reason is None:
        assert reasons == []
    else:
        assert any(reason in r for r in reasons), reasons


def test_graduation_years_in_the_posting() -> None:
    def check(text: str) -> list[str]:
        return eligibility_reasons("SDE Intern", text, YOU)

    assert check(f"{GRAD - 2}/{GRAD - 1} batch only.") == [f"For students graduating in {GRAD - 2}–{GRAD - 1} "
                                                          f"(you graduate in {GRAD})"]
    assert check(f"Class of {GRAD - 1} graduates.")
    assert check(f"Must be graduating between December {GRAD - 1} and June {GRAD}.") == []
    assert check(f"Students graduating in {GRAD - 1} or later.") == []
    assert check(f"Open to the {GRAD} batch.") == []


def test_your_graduation_year_comes_from_you_then_your_resume_then_an_estimate() -> None:
    resume = {"education": [{"end_date": f"Expected May {GRAD + 1}"}, {"end_date": "2019"}]}
    assert graduation_year_from_resume(resume) == GRAD + 1
    assert student({"year_of_study": 2}) == Student(2, GRAD, "estimate")
    assert student({"year_of_study": 2}, resume) == Student(2, GRAD + 1, "resume")
    assert student({"year_of_study": 2, "graduation_year": 2030}, resume) == Student(2, 2030, "you")
    assert student({"year_of_study": None}) == Student(None, None, "")  # any year: the year isn't checked
    assert with_resume({"graduation_year": None}, resume)["graduation_year"] == GRAD + 1
    assert with_resume({"graduation_year": 2030}, resume)["graduation_year"] == 2030


def test_a_full_time_search_is_left_alone() -> None:
    jobs = [_job("Acme", "Senior Software Engineer", job_type=JobType.FULL_TIME)]
    assert drop_ineligible(jobs, {"internships_only": False}) == (jobs, 0)
    assert drop_ineligible(jobs, {"year_of_study": 2}) == ([], 1)


def test_scrapers_keep_internships_only() -> None:
    query = SearchQuery.from_preferences({"job_types": ["full-time", "internship"]})
    assert query.internships_only and query.job_types == ["internship"]
    jobs = [_job("Acme"), _job("Acme", "Backend Engineer", job_type=None), _job("Acme", "Intern Program Manager")]
    kept = SCRAPERS["generic"]().filter(jobs, query)
    assert [j.role_title for j in kept] == ["Software Engineer Intern"]
    wide = SearchQuery.from_preferences({"job_types": ["full-time"], "internships_only": False})
    assert len(SCRAPERS["generic"]().filter(jobs, wide)) == 3


# ------------------------------------------------------------------ 10 new Internshala postings a scan
def test_internshala_allowance() -> None:
    assert internshala_allowance(400) == 10  # plenty from elsewhere: still 10 at most
    assert internshala_allowance(240, per_scan=50) == 50 and internshala_allowance(120, per_scan=50) == 40  # a quarter
    assert internshala_allowance(12) == 10  # a thin scan: not held to a quarter (4), filled up to the 10 a scan
    assert internshala_allowance(0) == 10  # only Internshala answered: its best 10
    assert internshala_allowance(400, share=100, per_scan=25) == 25
    assert internshala_allowance(400, per_scan=0) == 0 and internshala_allowance(400, share=0) == 0


def test_postings_you_already_have_dont_count() -> None:
    others = [_job(f"Startup {i}", f"Backend Intern {i}") for i in range(100)]
    ours = [_job(f"Small Co {i}", f"Intern {i}", internshala=True) for i in range(30)]
    known = {j.source_url for j in ours[:10]}
    kept, dropped = cap_internshala(others + ours, 25, per_scan=10, known=known)
    kept_ours = [j for j in kept if "internshala" in j.source_url]
    assert len(kept_ours) == 20 and dropped == 10  # the 10 you have (refreshed) + 10 new
    assert all(j.source_url in {k.source_url for k in kept_ours} for j in ours[:10])


# ------------------------------------------------------------------ a whole scan
class _Board:
    jobs: list[ScrapedJob] = []

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        return list(self.jobs)


def _user(db: Any, client: TestClient) -> User:
    return db.query(User).filter(User.email == client.get("/api/v1/auth/me").json()["email"]).one()


def test_scan_keeps_ten_internshala_and_interns_only(auth_client: TestClient, master_resume: dict,
                                                     monkeypatch: Any) -> None:
    internshala = [_job(f"Small Co {i}", f"Web Development Intern {i}", internshala=True) for i in range(25)]
    others = [_job(f"Startup {i}", f"Backend Intern {i}") for i in range(60)]
    not_for_you = [
        _job("Bigco", "Senior Backend Engineer", job_type=JobType.FULL_TIME),
        _job("Bigco", "Research Intern", desc="Must be currently enrolled in a PhD program." + LONG),
        _job("Bigco", "SDE Intern", desc="Open to final year students only." + LONG),
        _job("Bigco", "Platform Intern", desc=f"For the {GRAD - 2} batch." + LONG),
    ]
    step = _job("Google", "STEP Intern", desc="For first and second year undergraduate students." + LONG)
    monkeypatch.setitem(SCRAPERS, "board_i", type("BoardI", (_Board,), {"jobs": internshala}))
    monkeypatch.setitem(SCRAPERS, "board_o", type("BoardO", (_Board,), {"jobs": [*others, *not_for_you, step]}))
    c = auth_client
    c.put("/api/v1/users/me/preferences", json={"preferences": {"target_roles": [], "location_focus": {"enabled": False}}})
    with SessionLocal() as db:
        run = orch.run_scan(db, _user(db, c), platforms=["board_i", "board_o"])
        db.commit()
        assert run.status == "completed"
        titles = {j.role_title for j in db.query(Job).all()}
        assert sum(1 for t in titles if t.startswith("Web Development Intern")) == 10
        assert "STEP Intern" in titles and not titles & {"Senior Backend Engineer", "Research Intern", "SDE Intern",
                                                          "Platform Intern"}
        logs = " ".join(e["message"] for e in run.log)
        assert "left out 4 postings that aren't internships for a 2nd-year student" in logs
        assert "kept the best 10 new postings at most" in logs

        # The next scan brings 10 more from Internshala, not the same 10 again and not 25
        run = orch.run_scan(db, _user(db, c), platforms=["board_i", "board_o"])
        db.commit()
        assert sum(1 for j in db.query(Job).all() if "internshala.com" in j.source_url) == 20

    card = next(i for i in c.get("/api/v1/review/queue").json()["items"] if i["job"]["company_name"] == "Google")
    assert card["job"]["year_fit"] == "Open to 2nd-year students"


def test_full_time_cards_already_in_your_deck_are_skipped(auth_client: TestClient) -> None:
    with SessionLocal() as db:
        user = _user(db, auth_client)
        for title, job_type in (("Senior Backend Engineer", JobType.FULL_TIME), ("Backend Intern", JobType.INTERNSHIP)):
            job = Job(company_name="Razorpay", role_title=title, source_url=f"https://x.example/{title}",
                      source_platform=ATSPlatform.LEVER, description=LONG, job_type=job_type, company_tier="startup_india")
            db.add(job)
            db.flush()
            db.add(Application(user_id=user.id, job_id=job.id, status=ApplicationStatus.MATCHED, match_score=80))
        db.commit()
    top = auth_client.get("/api/v1/jobs/top-companies").json()
    assert [i["role_title"] for i in top["items"]] == ["Backend Intern"] and top["total"] == 1
    deck = auth_client.get("/api/v1/review/queue").json()["items"]
    assert [i["job"]["role_title"] for i in deck] == ["Backend Intern"]
    with SessionLocal() as db:
        skipped = db.query(Application).filter(Application.status == ApplicationStatus.SKIPPED).one()
        assert skipped.job.role_title == "Senior Backend Engineer"
        assert skipped.match_reasoning == "Not an internship (full-time role)"


def test_student_settings(auth_client: TestClient) -> None:
    c = auth_client
    me = c.get("/api/v1/users/me/student").json()
    assert me == {"internships_only": True, "year_of_study": 2, "graduation_year": GRAD, "graduation_year_source": "estimate"}
    url = "/api/v1/users/me/preferences"
    assert c.put(url, json={"preferences": {"graduation_year": 2030, "year_of_study": 3}}).status_code == 200
    assert c.get("/api/v1/users/me/student").json()["graduation_year_source"] == "you"
    for bad in ({"year_of_study": 7}, {"graduation_year": "soon"}, {"internships_only": "yes"},
                {"internshala_per_scan": 99}, {"internshala_per_scan": True}):
        assert c.put(url, json={"preferences": bad}).status_code == 422, bad
    assert c.put(url, json={"preferences": {"year_of_study": None, "internshala_per_scan": 5}}).json()["internshala_per_scan"] == 5


def test_the_preset_is_internships_only(auth_client: TestClient) -> None:
    prefs = auth_client.post("/api/v1/users/me/preferences/preset/ai-python").json()
    assert prefs["internships_only"] and prefs["job_types"] == ["internship"] and prefs["year_of_study"] == 2
