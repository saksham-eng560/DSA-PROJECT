"""Scraper snapshot tests (PLAN.md §16): deterministic, no network, one per platform."""

import json
import re

import httpx
import pytest
import respx

from app.models.enums import ATSPlatform, ExperienceLevel, JobType
from app.scrapers import SearchQuery, fetch_job_from_url
from app.scrapers.ashby import AshbyScraper
from app.scrapers.base import parse_date, parse_salary
from app.scrapers.browser_scraper import extract_assigned_json, next_data
from app.scrapers.generic import GenericScraper, company_from_host, find_ats_boards
from app.scrapers.glassdoor import parse_listings
from app.scrapers.greenhouse import GreenhouseScraper
from app.scrapers.indeed import IndeedScraper, parse_description, parse_job_cards
from app.scrapers.lever import LeverScraper
from app.scrapers.linkedin import LinkedInScraper, parse_job_detail, parse_search_results
from app.scrapers.wellfound import parse_apollo_jobs
from app.scrapers.workday import WorkdayScraper
from tests.conftest import FIXTURES

FX = FIXTURES / "scrapers"


def fx(name: str) -> str:
    return (FX / name).read_text()


def fxj(name: str) -> object:
    return json.loads(fx(name))


QUERY = SearchQuery(keywords=["Software Engineer", "Backend Engineer"], posted_within_days=3650, limit=20)


@respx.mock
def test_greenhouse_board() -> None:
    respx.get("https://boards-api.greenhouse.io/v1/boards/stripe/jobs").mock(return_value=httpx.Response(200, json=fxj("greenhouse_jobs.json")))
    respx.get("https://boards-api.greenhouse.io/v1/boards/stripe").mock(return_value=httpx.Response(200, json=fxj("greenhouse_board.json")))
    jobs = GreenhouseScraper().search(SearchQuery(**{**QUERY.__dict__, "sources": {"greenhouse_boards": ["stripe"]}}))
    titles = [j.role_title for j in jobs]
    assert "Backend Engineer, Payments" in titles and "Account Executive" not in titles
    backend = next(j for j in jobs if j.external_id == "7001")
    assert backend.company_name == "Stripe" and backend.source_platform == ATSPlatform.GREENHOUSE
    assert "Python" in backend.description and "<" not in backend.description
    assert (backend.salary_min, backend.salary_max) == (150000, 210000)
    assert backend.job_type == JobType.FULL_TIME
    intern = next(j for j in jobs if j.external_id == "7003")
    assert intern.job_type == JobType.INTERNSHIP and intern.is_remote


@respx.mock
def test_greenhouse_questions_and_fetch() -> None:
    respx.get("https://boards-api.greenhouse.io/v1/boards/stripe/jobs/7001").mock(
        return_value=httpx.Response(200, json=fxj("greenhouse_job_questions.json")))
    respx.get("https://boards-api.greenhouse.io/v1/boards/stripe").mock(return_value=httpx.Response(200, json=fxj("greenhouse_board.json")))
    questions = GreenhouseScraper().application_questions("stripe", "7001")
    auth = next(q for q in questions if "authorized" in q["question"])
    assert auth["field_type"] == "select" and auth["options"] == ["Yes", "No"] and auth["required"]
    job = fetch_job_from_url("https://job-boards.greenhouse.io/stripe/jobs/7001?gh_src=abc")
    assert job is not None and job.role_title == "Backend Engineer, Payments"


@respx.mock
def test_lever_company() -> None:
    respx.get("https://api.lever.co/v0/postings/acme").mock(return_value=httpx.Response(200, json=fxj("lever_postings.json")))
    jobs = LeverScraper().search(SearchQuery(**{**QUERY.__dict__, "sources": {"lever_companies": ["acme"]}}))
    assert len(jobs) == 1
    job = jobs[0]
    assert job.role_title == "Senior Software Engineer, Platform"
    assert job.is_remote and job.job_type == JobType.FULL_TIME
    assert job.experience_level == ExperienceLevel.SENIOR
    assert (job.salary_min, job.salary_max) == (180000, 220000)
    assert job.application_url.endswith("/apply")
    assert "5+ years" in job.description


@respx.mock
def test_ashby_board() -> None:
    respx.get("https://api.ashbyhq.com/posting-api/job-board/openai").mock(return_value=httpx.Response(200, json=fxj("ashby_board.json")))
    jobs = AshbyScraper().search(SearchQuery(**{**QUERY.__dict__, "sources": {"ashby_boards": ["openai"]}}))
    assert [j.role_title for j in jobs] == ["Software Engineer, Backend"]
    assert jobs[0].salary_max == 300000 and jobs[0].location == "San Francisco, New York"


@respx.mock
def test_workday_site() -> None:
    base = "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite"
    respx.post(f"{base}/jobs").mock(return_value=httpx.Response(200, json=fxj("workday_jobs.json")))
    respx.get(re.compile(re.escape(base) + r"/job/.*")).mock(return_value=httpx.Response(200, json=fxj("workday_detail.json")))
    q = SearchQuery(**{**QUERY.__dict__, "keywords": ["Software Engineer"],
                       "sources": {"workday_sites": ["https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"]}})
    jobs = WorkdayScraper().search(q)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.company_name == "NVIDIA" and job.external_id == "JR100"
    assert job.source_platform == ATSPlatform.WORKDAY and job.job_type == JobType.FULL_TIME
    assert "Kubernetes" in job.description


def test_linkedin_parsers() -> None:
    results = parse_search_results(fx("linkedin_search.html"))
    assert [r["id"] for r in results] == ["4012345678", "4012345679"]
    assert results[0]["company"] == "Globex" and results[0]["posted"] == "2026-09-27"
    external = parse_job_detail(fx("linkedin_detail_external.html"))
    assert external["apply_url"] == "https://job-boards.greenhouse.io/globex/jobs/555"
    assert not external["easy_apply"]
    easy = parse_job_detail(fx("linkedin_detail_easy.html"))
    assert easy["easy_apply"] and easy["apply_url"] is None


@respx.mock
def test_linkedin_search_end_to_end() -> None:
    respx.get(re.compile(r"https://www\.linkedin\.com/jobs-guest/jobs/api/seeMoreJobPostings/search.*start=0.*")).mock(
        return_value=httpx.Response(200, text=fx("linkedin_search.html")))
    respx.get(re.compile(r"https://www\.linkedin\.com/jobs-guest/jobs/api/seeMoreJobPostings/search.*start=25.*")).mock(
        return_value=httpx.Response(200, text=""))
    respx.get("https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4012345678").mock(
        return_value=httpx.Response(200, text=fx("linkedin_detail_external.html")))
    respx.get("https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4012345679").mock(
        return_value=httpx.Response(200, text=fx("linkedin_detail_easy.html")))
    jobs = LinkedInScraper().search(SearchQuery(keywords=["Engineer"], locations=["San Francisco"], posted_within_days=3650, limit=30))
    by_id = {j.external_id: j for j in jobs}
    assert by_id["4012345678"].raw["external_platform"] == "greenhouse"
    assert by_id["4012345678"].application_url == "https://job-boards.greenhouse.io/globex/jobs/555"
    assert by_id["4012345679"].easy_apply
    assert by_id["4012345679"].experience_level == ExperienceLevel.ENTRY


def test_indeed_parsers() -> None:
    cards = parse_job_cards(fx("indeed_search.html"))
    assert [c["jobkey"] for c in cards] == ["abc123", "def456"]
    job = IndeedScraper().card_to_job(cards[0], parse_description(fx("indeed_detail.html")))
    assert job.easy_apply and job.is_remote and job.salary_min == 120000
    assert "Django" in job.description


def test_glassdoor_and_wellfound_parsers() -> None:
    listings = parse_listings(fx("glassdoor_search.html"))
    assert listings[0]["company"] == "Hooli" and listings[0]["id"] == "9001"
    assert listings[0]["url"].startswith("https://www.glassdoor.com/job-listing/")
    assert parse_date(listings[0]["age"]) is not None
    wf = parse_apollo_jobs(next_data(fx("wellfound_next.html")))
    assert wf[0]["company"] == "Pied Piper" and wf[0]["remote"]
    assert parse_salary(wf[0]["compensation"]) == (150000, 190000, "USD")


@respx.mock
def test_generic_jsonld_and_ats_delegation() -> None:
    respx.get("https://vandelay.example/careers").mock(return_value=httpx.Response(200, text=fx("careers_jsonld.html")))
    jobs = GenericScraper().search(SearchQuery(keywords=["Platform Engineer"], posted_within_days=3650,
                                               sources={"career_pages": ["https://vandelay.example/careers"]}))
    assert len(jobs) == 1
    job = jobs[0]
    assert job.company_name == "Vandelay Industries" and job.location == "Seattle, WA, US"
    assert (job.salary_min, job.salary_max) == (140000, 180000)
    assert job.application_url.endswith("/apply") and str(job.deadline_date) == "2026-12-31"
    boards = find_ats_boards(fx("careers_ats_links.html"), "https://x")
    assert boards["greenhouse_boards"] == {"stripe"}


def test_helpers() -> None:
    assert parse_salary("Pay: $120k-$150k per year") == (120000, 150000, "USD")
    assert parse_salary("£45,000 - £55,000") == (45000, 55000, "GBP")
    assert parse_salary("$25 - $30 per hour") is None
    assert parse_date("Posted 3 Days Ago") is not None
    assert parse_date(1790000000000).year == 2026
    data = extract_assigned_json('x = 1; window.foo={"a": "}{", "b": [1, {"c": 2}]}; more', "window.foo")
    assert data == {"a": "}{", "b": [1, {"c": 2}]}


@respx.mock
def test_rate_limited_platform_pauses() -> None:
    from app.scrapers.base import RateLimited
    from app.services.rate_limiter import rate_limiter

    respx.get("https://api.lever.co/v0/postings/busy").mock(return_value=httpx.Response(429))
    with pytest.raises(RateLimited):
        LeverScraper().list_company("busy")
    assert rate_limiter.is_paused("lever")


def test_company_name_fallbacks() -> None:
    assert company_from_host("https://careers.acme-robotics.com/jobs/1") == "Acme Robotics"
    assert company_from_host("https://www.example.co.uk/careers") == "Example"
    assert company_from_host("http://127.0.0.1:8765/jobs/1") == "127.0.0.1"


@respx.mock
def test_job_page_without_jsonld_uses_site_name() -> None:
    respx.get("https://jobs.initech.example/openings/42").mock(return_value=httpx.Response(200, text=(
        '<html><head><title>Apply</title><meta property="og:site_name" content="Initech"></head>'
        "<body><main><h1>Data Engineer</h1><p>Build pipelines in Python and Spark.</p></main></body></html>")))
    job = fetch_job_from_url("https://jobs.initech.example/openings/42")
    assert job is not None and job.company_name == "Initech" and job.role_title == "Data Engineer"


def test_internshala_cards_as_the_site_serves_them_today() -> None:
    """Two cards from internshala.com's live search page (October 2026). The duration is the calendar item, never
    the stipend next to it ("₹ 8,000 - 10,000 /month"), so the internship-length setting can judge it."""
    from pathlib import Path

    from app.scrapers.internshala import card_to_job, parse_cards
    from app.services.duration_fit import duration_months

    html = (Path(__file__).parent / "fixtures" / "scrapers" / "internshala_live_cards.html").read_text()
    cards = parse_cards(html)
    assert len(cards) == 2
    first = cards[0]
    assert first["title"] == "AI – Automation & Systems" and first["company"] == "The Brandologist Co."
    assert first["stipend"] == "₹ 8,000 - 10,000 /month" and first["duration"] == "3 Months"
    job = card_to_job(first)
    assert job.role_title == "AI – Automation & Systems Intern" and "Duration: 3 Months." in job.description
    assert duration_months(job) == 3 and job.location.startswith("Delhi")
    assert all(c["duration"].endswith(("Months", "Month", "Weeks", "Week")) for c in cards)
