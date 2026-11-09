"""More job portals (Naukri, Unstop, the remote boards) and existing sources returning more of what's out there
(reported: "a scan finds only ~4 jobs"). Deterministic: every request is mocked with fixtures that mirror each
site's real response shape."""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from app.models.enums import ATSPlatform, JobType
from app.models.user import default_preferences
from app.scrapers import SCRAPERS, SearchQuery
from app.scrapers import remote_boards as rb
from app.scrapers import top_companies as tc
from app.scrapers.base import BaseScraper, Blocked, ScrapedJob, ScraperError, role_search_terms
from app.scrapers.indeed import IndeedScraper, fromage, place_for
from app.scrapers.internships import InternshipListScraper
from app.scrapers.linkedin import LinkedInScraper
from app.scrapers.naukri import NaukriScraper, job_age, search_places
from app.scrapers.unstop import UnstopScraper
from app.scrapers.wellfound import place_pages, role_pages
from app.scrapers.workday import clearly_abroad
from app.services.job_matcher import _role_matches
from app.services.location_focus import TIER_COUNTRY, TIER_PRIME, TIER_REMOTE, get_focus, location_tier
from tests.conftest import FIXTURES

FX = FIXTURES / "scrapers"
ROLES = ["AI Engineer Intern", "Generative AI Intern", "Python Developer Intern", "Backend Developer Intern",
         "Software Engineer Intern"]
PREFS = {**default_preferences(), "target_roles": ROLES, "internships_only": True, "job_types": ["internship"],
         "posted_within_days": 30, "target_locations": ["Delhi, India", "India"],
         "location_focus": {**default_preferences()["location_focus"], "country_share": 100}}
FOCUS = get_focus(PREFS)


def fxj(name: str) -> Any:
    return json.loads((FX / name).read_text())


def query(**overrides: Any) -> SearchQuery:
    return SearchQuery(**{**SearchQuery.from_preferences(PREFS).__dict__, **overrides})


def params(call: Any) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(str(call.request.url)).query).items()}


@pytest.fixture(autouse=True)
def _fresh_board_cache() -> None:
    rb._cache.clear()


# ------------------------------------------------------------------ titles: the same role in other words
def test_related_titles_are_kept_unrelated_ones_are_not() -> None:
    q = SearchQuery(keywords=ROLES, internships_only=True)
    assert not _role_matches("ai engineer intern", "machine learning intern")  # what used to drop them
    for title in ("Machine Learning Intern", "GenAI Intern", "AI/ML Intern", "LLM Intern", "SDE Intern", "Django Intern",
                  "Server-side Intern", "NLP Research Intern"):
        assert q.matches_title(title), title
    for title in ("Graphic Design Intern", "Sales Intern", "Account Executive", "HR Intern"):
        assert not q.matches_title(title), title
    plain = SearchQuery(keywords=["AI Engineer Intern"])  # not internships-only: an intern target finds intern titles
    assert plain.matches_title("ML Intern") and not plain.matches_title("Senior ML Engineer")


def test_role_search_terms() -> None:
    q = SearchQuery(keywords=["Python Developer Intern", "Backend Developer", "python developer intern"])
    assert role_search_terms(q, intern=True) == ["Python Developer Intern", "Backend Developer intern"]
    assert role_search_terms(q, intern=False) == ["Python Developer", "Backend Developer"]
    assert role_search_terms(SearchQuery(keywords=["X"], search_terms=["Google intern"])) == ["Google intern"]


# ------------------------------------------------------------------ the shared pager
class _Pager(BaseScraper):
    rate_key = "pager"

    def search(self, query: SearchQuery) -> list[ScrapedJob]:  # pragma: no cover - not used
        return []


def _job(n: int | str, title: str = "Python Developer Intern") -> ScrapedJob:
    return ScrapedJob(company_name="Acme", role_title=title, description="Python", source_url=f"https://x.example/{n}",
                      source_platform=ATSPlatform.CUSTOM, location="Delhi, India").finalize()


def test_search_pages_takes_turns_and_survives_one_bad_search() -> None:
    calls: list[tuple[str, int]] = []
    steps: list[tuple[int, int]] = []

    def fetch(search: str, page: int) -> tuple[list[ScrapedJob], bool]:
        calls.append((search, page))
        if search == "broken":
            raise ScraperError("HTTP 500")
        return [_job(f"{search}{page}")], search == "deep" and page < 3

    q = SearchQuery(limit=50, progress=lambda done, total: steps.append((done, total)))
    jobs = _Pager().search_pages(["deep", "broken", "flat"], fetch, q, max_pages=3)
    assert calls == [("deep", 1), ("broken", 1), ("flat", 1), ("deep", 2), ("deep", 3)]  # page 1 of all first
    assert [j.source_url.rsplit("/", 1)[-1] for j in jobs] == ["deep1", "flat1", "deep2", "deep3"]
    assert steps[0] == (0, 9) and steps[-1] == (9, 9)
    # enough jobs: no second pages
    calls.clear()
    _Pager().search_pages(["deep", "flat"], fetch, SearchQuery(limit=2), max_pages=3)
    assert calls == [("deep", 1), ("flat", 1)]


def test_search_pages_stops_at_a_bot_wall_and_explains_an_empty_result() -> None:
    calls: list[str] = []

    def walled(search: str, page: int) -> tuple[list[ScrapedJob], bool]:
        calls.append(search)
        raise Blocked("captcha")

    with pytest.raises(Blocked):
        _Pager().search_pages(["a", "b", "c"], walled, SearchQuery(), max_pages=2)
    assert calls == ["a"]  # not asked again this scan
    calls.clear()
    late = SearchQuery(deadline=time.monotonic() - 1)  # the scan's time is up: nothing is fetched
    assert _Pager().search_pages(["a"], walled, late) == [] and calls == []


# ------------------------------------------------------------------ Naukri
def _naukri_fixture() -> dict[str, Any]:
    data = fxj("naukri_search.json")
    data["jobDetails"][0]["createdDate"] = int((datetime.now(UTC) - timedelta(days=2)).timestamp() * 1000)
    return data


@respx.mock
def test_naukri_search() -> None:
    route = respx.get("https://www.naukri.com/jobapi/v3/search").mock(return_value=httpx.Response(200, json=_naukri_fixture()))
    jobs = NaukriScraper().search(query())
    first = route.calls[0].request
    assert first.headers["appid"] == "109" and first.headers["systemid"] == "Naukri"
    p = params(route.calls[0])
    assert p["keyword"] == "AI Engineer Intern" and p["location"] == "delhi / ncr" and p["jobAge"] == "30"
    assert p["experience"] == "0" and p["pageNo"] == "1" and p["seoKey"] == "ai-engineer-intern-jobs-in-delhi-ncr"
    assert "location" not in params(route.calls[1]) and params(route.calls[1])["urlType"] == "search_by_keyword"  # all India
    assert len(route.calls) == len(ROLES) * 2  # every role in Delhi NCR and in all of India; 4 results = no page 2

    assert [j.role_title for j in jobs] == ["Python Developer Intern", "AI/ML Intern"]  # senior job and link-less one out
    py, ai = jobs
    assert py.location == "Noida, Delhi / NCR, India" and location_tier(py.location, py.is_remote, FOCUS) == TIER_PRIME
    assert py.source_url == ("https://www.naukri.com/job-listings-python-developer-intern-acme-analytics-noida-0-to-1-"
                             "years-251020500111")
    assert py.source_platform == ATSPlatform.CUSTOM and py.job_type == JobType.INTERNSHIP
    assert py.raw["listing_source"] == "naukri" and py.raw["apply_on_site"] == "Naukri" and "FastAPI" in py.raw["skills"]
    assert "Build REST APIs in Python" in py.description and "<" not in py.description
    assert py.posted_date == (datetime.now(UTC) - timedelta(days=2)).date() and py.external_id == "naukri-251020500111"
    assert ai.is_remote and ai.location == "Remote, India" and ai.raw["salary"] == "10,000-15,000 /month"
    assert location_tier(ai.location, ai.is_remote, FOCUS) == TIER_COUNTRY


@respx.mock
def test_naukri_captcha_is_a_clear_error_not_a_crash() -> None:
    route = respx.get("https://www.naukri.com/jobapi/v3/search").mock(
        return_value=httpx.Response(406, json={"message": "recaptcha required", "statusCode": 406}))
    with pytest.raises(Blocked, match="captcha"):
        NaukriScraper().search(query())
    assert len(route.calls) == 1  # stops asking at once
    respx.get("https://www.naukri.com/jobapi/v3/search").mock(
        return_value=httpx.Response(200, text="<html><body>Please verify: captcha</body></html>"))
    with pytest.raises(Blocked):
        NaukriScraper().search(query())


@respx.mock
def test_naukri_follows_pages_in_turns() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        q = parse_qs(urlparse(str(request.url)).query)
        term, page = q["keyword"][0], int(q["pageNo"][0])
        count = 20 if page == 1 else 5
        items = [{"title": f"{term} {page}-{i}", "jobId": f"{term[:3]}{page}{i}", "companyName": "Acme",
                  "jdURL": f"/job-listings-{term[:3].lower()}-{page}-{i}", "placeholders": [{"type": "location", "label": "Pune"}]}
                 for i in range(count)]
        return httpx.Response(200, json={"noOfJobs": 25, "jobDetails": items})

    route = respx.get("https://www.naukri.com/jobapi/v3/search").mock(side_effect=respond)
    q = SearchQuery(keywords=["Python Developer Intern", "Backend Developer Intern"], locations=["India"],
                    internships_only=True, limit=200, posted_within_days=30)
    jobs = NaukriScraper().search(q)
    assert [(params(c)["keyword"], params(c)["pageNo"]) for c in route.calls] == [
        ("Python Developer Intern", "1"), ("Backend Developer Intern", "1"),
        ("Python Developer Intern", "2"), ("Backend Developer Intern", "2")]
    assert len(jobs) == 50 and {j.location for j in jobs} == {"Pune, India"}
    assert job_age(14) == 15 and job_age(45) == 30 and job_age(None) is None
    assert search_places(query()) == ["delhi / ncr", ""]


# ------------------------------------------------------------------ Unstop
@respx.mock
def test_unstop_search() -> None:
    route = respx.get("https://unstop.com/api/public/opportunity/search-result").mock(
        return_value=httpx.Response(200, json=fxj("unstop_search.json")))
    jobs = UnstopScraper().search(query(posted_within_days=3650))
    p = params(route.calls[0])
    assert p["opportunity"] == "internships" and p["searchTerm"] == "AI Engineer" and p["oppstatus"] == "open"
    assert len(route.calls) == len(ROLES)  # one page each (last_page 1)
    by_company = {j.company_name: j for j in jobs}
    assert set(by_company) == {"Acme Analytics", "NeuralNest", "Kite Systems"}  # the closed one is left out
    acme = by_company["Acme Analytics"]
    assert acme.location == "Gurugram, Delhi, India" and location_tier(acme.location, acme.is_remote, FOCUS) == TIER_PRIME
    assert acme.raw["stipend"] == "₹10,000 - ₹15,000 /month" and acme.raw["apply_on_site"] == "Unstop"
    assert acme.deadline_date == date(2099, 12, 31) and acme.source_url == (
        "https://unstop.com/internships/python-developer-intern-acme-analytics-1200001")
    assert "FastAPI" in acme.description and acme.external_id == "unstop-1200001"
    gen = by_company["NeuralNest"]
    assert gen.role_title == "Generative AI Intern" and gen.is_remote and gen.location == "Work from home, India"
    assert gen.source_url == "https://unstop.com/internships/generative-ai-neuralnest-1200002" and gen.raw["stipend"] == "Unpaid"
    assert by_company["Kite Systems"].location == "Bengaluru, India" and by_company["Kite Systems"].raw["stipend"] == "₹8,000 /month"
    assert jobs[0].company_name == "Acme Analytics"  # Delhi NCR first


@respx.mock
def test_unstop_without_internships_only_also_searches_jobs() -> None:
    route = respx.get("https://unstop.com/api/public/opportunity/search-result").mock(
        return_value=httpx.Response(200, json={"data": {"data": [], "last_page": 1}}))
    UnstopScraper().search(SearchQuery(keywords=["Python Developer"]))
    assert [params(c)["opportunity"] for c in route.calls] == ["internships", "jobs"]
    respx.get("https://unstop.com/api/public/opportunity/search-result").mock(return_value=httpx.Response(200, text="oops"))
    with pytest.raises(ScraperError):
        UnstopScraper().search(SearchQuery(keywords=["Python Developer"]))


# ------------------------------------------------------------------ remote boards
def _mock_boards() -> dict[str, Any]:
    return {
        "remotive": respx.get("https://remotive.com/api/remote-jobs").mock(return_value=httpx.Response(200, json=fxj("remotive_jobs.json"))),
        "jobicy": respx.get("https://jobicy.com/api/v2/remote-jobs").mock(return_value=httpx.Response(200, json=fxj("jobicy_jobs.json"))),
        "himalayas": respx.get("https://himalayas.app/jobs/api/search").mock(return_value=httpx.Response(200, json=fxj("himalayas_jobs.json"))),
    }


@respx.mock
def test_remote_boards_keep_intern_roles_open_to_india() -> None:
    routes = _mock_boards()
    jobs = SCRAPERS["remote_boards"]().search(query(posted_within_days=3650))
    by_title = {j.role_title: j for j in jobs}
    assert set(by_title) == {"Machine Learning Intern", "Backend Intern", "AI Engineer Intern", "Software Engineer Intern"}
    # left out: senior roles, "USA Only", Canada, Germany, a mid-level role, and a junior full-time job (interns only)
    assert all(j.is_remote for j in jobs)
    assert by_title["Backend Intern"].location == "Remote (India)"
    assert by_title["Machine Learning Intern"].location == "Remote (Worldwide)"
    assert by_title["AI Engineer Intern"].location == "Remote (Anywhere)"
    swe = by_title["Software Engineer Intern"]
    assert swe.location == "Remote (India, Singapore)" and swe.application_url.startswith("https://jobs.lever.co/cloudnest/")
    assert swe.source_url == "https://himalayas.app/companies/cloudnest/jobs/software-engineer-intern"
    assert location_tier(swe.location, True, FOCUS) == TIER_COUNTRY
    assert location_tier(by_title["Machine Learning Intern"].location, True, FOCUS) == TIER_REMOTE
    assert [j.raw["listing_source"] for j in jobs[:2]] and {j.raw["listing_source"] for j in jobs} == {"remotive", "jobicy", "himalayas"}
    assert {j.role_title for j in jobs[:2]} == {"Backend Intern", "Software Engineer Intern"}  # open to India first
    assert params(routes["remotive"].calls[0]) == {"search": "intern"}
    assert params(routes["himalayas"].calls[0]) == {"q": "intern", "page": "1"}
    # the boards ask not to be polled often: a second scan within hours is served from the cache
    before = sum(len(r.calls) for r in routes.values())
    SCRAPERS["remote_boards"]().search(query(posted_within_days=3650))
    assert sum(len(r.calls) for r in routes.values()) == before


@respx.mock
def test_remote_boards_entry_level_when_not_internships_only() -> None:
    _mock_boards()
    q = SearchQuery(keywords=["Python Developer"], locations=["India"], posted_within_days=3650)
    titles = {j.role_title for j in SCRAPERS["remote_boards"]().search(q)}
    assert "Junior Python Developer" in titles  # APAC includes India
    assert not titles & {"Python Developer", "Senior Backend Engineer"}  # mid-level and senior are never kept


@respx.mock
def test_one_remote_board_failing_does_not_stop_the_others() -> None:
    respx.get("https://remotive.com/api/remote-jobs").mock(return_value=httpx.Response(200, json=fxj("remotive_jobs.json")))
    respx.get("https://jobicy.com/api/v2/remote-jobs").mock(return_value=httpx.Response(403))
    respx.get("https://himalayas.app/jobs/api/search").mock(return_value=httpx.Response(404))
    listing = respx.get("https://himalayas.app/jobs/api").mock(return_value=httpx.Response(200, json=fxj("himalayas_jobs.json")))
    titles = {j.role_title for j in SCRAPERS["remote_boards"]().search(query(posted_within_days=3650))}
    assert titles == {"Machine Learning Intern", "Backend Intern", "Software Engineer Intern"}
    assert params(listing.calls[0]) == {"limit": "20", "offset": "0"}  # no search endpoint: the latest listings


@respx.mock
def test_every_remote_board_failing_says_why() -> None:
    for url in ("https://remotive.com/api/remote-jobs", "https://jobicy.com/api/v2/remote-jobs",
                "https://himalayas.app/jobs/api/search", "https://himalayas.app/jobs/api"):
        respx.get(url).mock(return_value=httpx.Response(403))
    with pytest.raises(ScraperError, match="Remotive"):
        SCRAPERS["remote_boards"]().search(query())


def test_remote_geo_rules() -> None:
    q = query()
    assert rb.open_to_you([], q) and rb.open_to_you(["Worldwide"], q) and rb.open_to_you(["APAC"], q)
    assert rb.open_to_you(["USA", "India"], q) and rb.open_to_you(["Anywhere in the World"], q)
    assert not rb.open_to_you(["USA Only"], q) and not rb.open_to_you(["Remote - US"], q)
    assert not rb.open_to_you(["Brazil"], q) and not rb.open_to_you(["Worldwide (UTC-5 to UTC+3)"], q)
    assert rb.open_to_you(["Brazil"], SearchQuery())  # nowhere in particular: every remote role is open
    assert rb.entry_level("Graduate Software Engineer") and rb.entry_level("Python Developer", levels=["Entry-level"])
    assert not rb.entry_level("Senior Engineer", levels=["Entry"]) and not rb.entry_level("Lead Generation Specialist")


# ------------------------------------------------------------------ LinkedIn: every role and place gets its turn
def _cards(prefix: str, n: int, company: str = "Acme") -> str:
    return "".join(f'<li><div class="base-card" data-entity-urn="urn:li:jobPosting:{prefix}{i}"><h3 class="base-search-card__title">'
                   f'Python Developer Intern</h3><h4 class="base-search-card__subtitle">{company}</h4>'
                   '<span class="job-search-card__location">Delhi, India</span></div></li>' for i in range(n))


@respx.mock
def test_linkedin_pages_by_what_it_returns_and_takes_turns() -> None:
    pages = {("Python Developer Intern", "0"): _cards("1", 10), ("Python Developer Intern", "10"): _cards("2", 3),
             ("Backend Developer Intern", "0"): _cards("3", 2)}

    def respond(request: httpx.Request) -> httpx.Response:
        q = parse_qs(urlparse(str(request.url)).query)
        return httpx.Response(200, text=pages.get((q["keywords"][0], q["start"][0]), ""))

    search = respx.get(url__startswith="https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search").mock(side_effect=respond)
    detail = respx.get(url__startswith="https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/").mock(
        return_value=httpx.Response(200, text='<div class="show-more-less-html__markup">Build APIs with Python.</div>'))
    q = SearchQuery(keywords=["Python Developer Intern", "Backend Developer Intern"], locations=["Delhi, India"],
                    job_types=["internship"], internships_only=True, posted_within_days=30, limit=50)
    jobs = LinkedInScraper().search(q)
    assert [(params(c)["keywords"], params(c)["start"]) for c in search.calls] == [
        ("Python Developer Intern", "0"), ("Backend Developer Intern", "0"), ("Python Developer Intern", "10")]
    assert len(jobs) == 15 and params(search.calls[0])["f_JT"] == "I"
    # a small limit is shared: one result from each search in turn
    detail.calls.reset()
    small = LinkedInScraper().search(SearchQuery(**{**q.__dict__, "limit": 4}))
    assert len(small) == 4 and {j.external_id[0] for j in small} == {"1", "3"}


@respx.mock
def test_linkedin_keeps_what_it_found_when_its_request_budget_runs_out() -> None:
    """The shared LinkedIn budget (top companies spend it too) ran out mid-round: the results of the searches
    already made were dropped and LinkedIn said "found 0 jobs" with no reason."""
    def respond(request: httpx.Request) -> httpx.Response:
        keyword = parse_qs(urlparse(str(request.url)).query)["keywords"][0]
        return httpx.Response(200, text=_cards("1", 10)) if keyword == "Python Developer Intern" else httpx.Response(429)

    respx.get(url__startswith="https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search").mock(side_effect=respond)
    detail = respx.get(url__startswith="https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/").mock(
        return_value=httpx.Response(200, text='<div class="show-more-less-html__markup">Build APIs.</div>'))
    q = SearchQuery(keywords=["Python Developer Intern", "Backend Developer Intern"], locations=["Delhi, India"],
                    posted_within_days=30, limit=50)
    jobs = LinkedInScraper().search(q)
    assert len(jobs) == 10 and not detail.called  # kept as listed; the description is read when you keep one
    assert {j.role_title for j in jobs} == {"Python Developer Intern"}
    assert all(j.raw["listing_source"] == "linkedin" for j in jobs)  # preparation fetches the full posting


@respx.mock
def test_linkedin_says_why_when_its_budget_ran_out_before_anything_was_found() -> None:
    respx.get(url__startswith="https://www.linkedin.com/jobs-guest/").mock(return_value=httpx.Response(429))
    with pytest.raises(ScraperError, match="429"):
        LinkedInScraper().search(SearchQuery(keywords=["Python Developer Intern"], locations=["India"]))


@respx.mock
def test_linkedin_says_when_it_refuses_every_search() -> None:
    respx.get(url__startswith="https://www.linkedin.com/jobs-guest/").mock(return_value=httpx.Response(999, text="blocked"))
    with pytest.raises(ScraperError, match="HTTP 999"):
        LinkedInScraper().search(SearchQuery(keywords=["Python Developer Intern"], locations=["India"]))


@respx.mock
def test_top_companies_linkedin_opens_only_that_companys_postings(monkeypatch: Any) -> None:
    monkeypatch.setattr(tc, "tasks", lambda: ["linkedin||Google"])
    search = respx.get(url__startswith="https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search").mock(
        return_value=httpx.Response(200, text=_cards("7", 1, "Google") + _cards("8", 1, "Googly Solutions")))
    detail = respx.get(url__startswith="https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/").mock(
        return_value=httpx.Response(200, text='<div class="show-more-less-html__markup">Python internship.</div>'))
    jobs = tc.TopCompaniesScraper().search(SearchQuery(limit=50, focus=FOCUS, locations=["Delhi, India", "India"]))
    assert [j.company_name for j in jobs] == ["Google"]
    assert [str(c.request.url).rsplit("/", 1)[-1] for c in detail.calls] == ["70"]  # Googly's posting never opened
    assert [params(c)["location"] for c in search.calls] == ["India"]  # one country-wide search


# ------------------------------------------------------------------ smaller fixes
def test_indeed_wellfound_and_workday_search_the_right_things() -> None:
    assert place_for("Delhi, India") == "Delhi" and place_for("India") == "" and place_for("San Francisco, CA") == "San Francisco, CA"
    assert fromage(30) == 14 and fromage(5) == 7
    base, url = IndeedScraper().search_url("Python Developer Intern", "India", 2, query())
    assert base == "https://in.indeed.com" and "&l=&" in url and url.endswith("&start=10")
    assert role_pages(ROLES) == ["machine-learning-engineer", "backend-engineer", "software-engineer"]
    assert role_pages(["Growth Hacker Intern"]) == ["growth-hacker"]
    assert place_pages(query()) == ["india"] and place_pages(SearchQuery()) == [""]
    assert clearly_abroad("US, CA, Santa Clara", query()) and not clearly_abroad("India, Bengaluru", query())
    assert not clearly_abroad("3 Locations", query()) and not clearly_abroad("US, CA, Santa Clara", SearchQuery())


def test_wellfound_remote_only_search_opens_the_remote_listing() -> None:
    assert place_pages(SearchQuery(remote=True)) == ["remote"]
    assert place_pages(SearchQuery(remote=True, locations=["Delhi, India"])) == ["delhi"]


def test_internshala_next_page_keeps_the_query_and_counts_on() -> None:
    from app.scrapers.internshala import next_page

    base = "https://internshala.com/internships/python-internship"
    assert next_page(base + "/") == base + "/page-2/"
    assert next_page(base) == base + "/page-2/"
    assert next_page(base + "/?ref=x") == base + "/page-2/?ref=x"
    assert next_page(base + "/page-2/") == base + "/page-3/"
    assert next_page(base + "/page-2/?ref=x") == base + "/page-3/?ref=x"


def test_every_source_shares_one_interleave() -> None:
    from app.scrapers import base, indeed, internshala, linkedin

    assert base.interleave([["a1", "a2"], ["b1"], []]) == ["a1", "b1", "a2"]
    assert base.interleave(iter([[1], [2, 3]])) == [1, 2, 3] and base.interleave([]) == []
    assert linkedin.interleave is indeed.interleave is internshala.interleave is base.interleave


@respx.mock
def test_internship_lists_say_why_when_every_list_fails() -> None:
    respx.get(url__regex=re.escape("https://raw.githubusercontent.com/") + ".*").mock(return_value=httpx.Response(403))
    with pytest.raises(ScraperError):
        InternshipListScraper().search(SearchQuery(sources={"internship_lists": ["simplify-internships"]}))


def test_new_portals_are_registered() -> None:
    assert {"naukri", "unstop", "remote_boards"} <= set(SCRAPERS)


def test_unstop_parses_its_live_api_response() -> None:
    """Two postings from Unstop's live search API (October 2026, trimmed): the fields the parser reads are all there."""
    import json
    from pathlib import Path

    from app.scrapers.unstop import to_job

    data = json.loads((Path(__file__).parent / "fixtures" / "scrapers" / "unstop_live_search.json").read_text())
    for item in data["data"]["data"]:  # still open whenever the test runs (a closed one is dropped, by design)
        item["end_date"] = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    jobs = [to_job(item) for item in data["data"]["data"]]
    assert all(jobs) and len(jobs) == 2
    by_company = {j.company_name: j for j in jobs if j}
    ml = by_company["Nexvia Technologies"]
    assert ml.role_title == "AI & Machine Learning Internship" and ml.location.startswith("Hyderabad")
    assert ml.raw["stipend"] == "₹20,000 - ₹35,000 /month" and ml.posted_date is not None
    assert by_company["Zenotalent"].is_remote and by_company["Zenotalent"].source_url.startswith("https://unstop.com/")
