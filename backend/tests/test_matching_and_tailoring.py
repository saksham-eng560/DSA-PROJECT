from datetime import UTC, datetime, timedelta

from app.models.enums import ATSPlatform, JobType
from app.models.job import Job
from app.schemas.resume_content import ResumeContent
from app.services.cover_letter import generate_cover_letter
from app.services.job_matcher import estimate_years_experience, evaluate_match, prefilter, priority_key
from app.services.resume_parser import heuristic_parse
from app.services.resume_tailor import enforce_truthfulness, heuristic_tailor, is_skill_supported, tailor_resume
from tests.conftest import SAMPLE_RESUME_TEXT

MASTER = heuristic_parse(SAMPLE_RESUME_TEXT)
PREFS = {"target_roles": ["Backend Engineer", "Software Engineer"], "target_locations": ["San Francisco"],
         "remote_preference": "hybrid", "salary_min": 120000, "job_types": ["full-time"], "internships_only": False, "companies_to_avoid": ["Evil Corp"],
         "auto_apply_threshold": 60}


def make_job(**kw) -> Job:  # type: ignore[no-untyped-def]
    defaults = dict(company_name="Stripe", role_title="Backend Software Engineer", source_url="https://x/1",
                    source_platform=ATSPlatform.GREENHOUSE, job_type=JobType.FULL_TIME, location="San Francisco, CA",
                    description="We are looking for a backend engineer with 3+ years of experience in Python, FastAPI, "
                                "PostgreSQL, Docker and Kubernetes. Experience with AWS is a plus. Our mission is to "
                                "grow the GDP of the internet.",
                    salary_min=150000, salary_max=200000)
    defaults.update(kw)
    return Job(**defaults)


def test_prefilter_rules() -> None:
    assert prefilter(make_job(), PREFS) == (True, None)
    assert not prefilter(make_job(company_name="Evil Corp Inc"), PREFS)[0]
    assert not prefilter(make_job(role_title="Marketing Manager"), PREFS)[0]
    assert not prefilter(make_job(job_type=JobType.CONTRACT), PREFS)[0]
    old = datetime.now(UTC).date() - timedelta(days=60)
    assert not prefilter(make_job(posted_date=old), {**PREFS, "posted_within_days": 14})[0]
    assert not prefilter(make_job(is_remote=False, location="NYC"), {**PREFS, "remote_preference": "remote"})[0]


def test_heuristic_evaluation_scores_good_match_high() -> None:
    good = evaluate_match(MASTER, make_job(), PREFS, 60)
    assert good["method"] == "heuristic"
    assert good["match_score"] == sum(good[k] for k in ("skills_match", "experience_match", "industry_match",
                                                         "location_match", "compensation_match"))
    assert good["match_score"] >= 60 and good["proceed_with_application"]
    assert "Python" in good["strong_matches"]
    bad = evaluate_match(MASTER, make_job(role_title="Senior iOS Engineer",
                                          description="10+ years of Swift, Objective-C, iOS and SwiftUI required.",
                                          salary_max=90000, location="Tokyo, Japan"), PREFS, 60)
    assert bad["match_score"] < good["match_score"]
    assert "Swift" in bad["missing_skills"]


def test_llm_evaluation_clamps_and_sums(fake_llm) -> None:
    fake_llm({"JOB MATCH EVALUATION": {"evaluation": {
        "match_score": 999, "skills_match": 25, "experience_match": 18, "industry_match": -3, "location_match": "20",
        "compensation_match": 15, "proceed_with_application": True, "reasoning": "Strong fit.",
        "missing_skills": ["go"], "strong_matches": ["python"]}}})
    result = evaluate_match(MASTER, make_job(), PREFS, 60)
    assert result["method"] == "llm"
    assert result["skills_match"] == 20 and result["industry_match"] == 0
    assert result["match_score"] == 20 + 18 + 0 + 20 + 15


def test_priority_ordering() -> None:
    soon = datetime.now(UTC).date() + timedelta(days=2)
    later = soon + timedelta(days=20)
    items = [(80, later), (90, None), (80, soon)]
    assert sorted(items, key=lambda x: priority_key(*x)) == [(90, None), (80, soon), (80, later)]


def test_years_of_experience_excludes_internships() -> None:
    years = estimate_years_experience(ResumeContent.model_validate(MASTER))
    assert 3.5 <= years <= 6


# --------------------------------------------------------------------------- truthfulness guard
def test_truthfulness_guard_blocks_fabrication() -> None:
    tailored = {
        "personal_info": {"name": "Jane Q. Hacker", "email": "fake@evil.com"},
        "summary": "Machine learning expert with 10 years of experience.",
        "experience": [
            {"company": "Acme Corp", "title": "Staff Engineer", "start_date": "2015", "end_date": "Present",
             "bullets": ["Built REST APIs in Python and FastAPI serving 2M requests/day",
                         "Led a team of 25 engineers", "Trained deep learning models with PyTorch"]},
        ],
        "education": [{"institution": "MIT", "degree": "PhD"}],
        "projects": [{"name": "Invented Project", "description": "x"}],
        "skills": {"technical": ["Python", "PyTorch", "REST API Design", "Rust"], "tools": [], "languages": [], "soft_skills": []},
        "certifications": [{"name": "Fake Cert"}],
    }
    fixed, violations = enforce_truthfulness(MASTER, tailored)
    assert fixed["personal_info"]["name"] == "Jane Doe"
    assert fixed["education"][0]["institution"] == "University of California, Berkeley"
    assert fixed["certifications"] == []
    acme = next(e for e in fixed["experience"] if e["company"] == "Acme Corp")
    assert acme["title"] == "Software Engineer" and acme["start_date"] == "Jan 2022"
    assert not any("25 engineers" in b for b in acme["bullets"])
    assert not any("PyTorch" in b for b in acme["bullets"])
    assert any("Beta Labs" == e["company"] for e in fixed["experience"])  # omitted job restored
    techs = fixed["skills"]["technical"]
    assert "PyTorch" not in techs and "Rust" not in techs
    assert "REST API Design" in techs  # implicit skill evidenced by "REST APIs"
    assert [p["name"] for p in fixed["projects"]] == ["JobBot"]
    assert fixed["summary"] == MASTER["summary"]  # reverted: unverified ML claim + "10 years"
    assert len(violations) >= 6


def test_skill_support_detection() -> None:
    text = "built rest apis in python; deployed on kubernetes"
    assert is_skill_supported("REST API Design", text, set())
    assert is_skill_supported("Kubernetes", text, set())
    assert not is_skill_supported("Machine Learning", text, set())


def test_heuristic_tailor_reorders_without_inventing() -> None:
    job = make_job(description="Kubernetes and Docker expert needed to run AWS infrastructure.")
    tailored, changes = heuristic_tailor(MASTER, job)
    assert "Kubernetes" in tailored["experience"][0]["bullets"][0]
    assert changes
    result = tailor_resume(MASTER, job)
    assert result["method"] == "heuristic"
    assert result["violations"] == []


def test_llm_tailoring_passes_through_guard(fake_llm) -> None:
    fake_llm({"RESUME TAILORING": {"tailored_resume": {**MASTER, "skills": {**MASTER["skills"], "technical": ["Golang", *MASTER["skills"]["technical"]]}},
                                   "changes_made": ["Moved Python first"]}})
    result = tailor_resume(MASTER, make_job())
    assert result["method"] == "llm"
    assert "Golang" not in result["tailored_resume"]["skills"]["technical"]
    assert any("Golang" in v for v in result["violations"])


def test_cover_letter_heuristic_is_grounded() -> None:
    letter = generate_cover_letter(MASTER, make_job())
    text = letter["cover_letter"]
    assert letter["method"] == "heuristic"
    assert text.startswith("Dear Hiring Team,") and text.rstrip().endswith("Jane Doe")
    assert "Stripe" in text and "Acme Corp" in text
    assert 60 <= len(text.split()) <= 350
