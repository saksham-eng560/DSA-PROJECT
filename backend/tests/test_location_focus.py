"""Internship focus: Summer 2027, ~90% India with Delhi NCR first, Internshala and the India preset."""

from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.application import Application
from app.models.enums import ApplicationStatus, ATSPlatform, JobType
from app.models.job import Job
from app.models.user import User, default_preferences
from app.scrapers import SCRAPERS, SearchQuery
from app.scrapers.base import ScrapedJob
from app.scrapers.indeed import base_for
from app.scrapers.internshala import MAX_PAGES, InternshalaScraper, card_to_job, categories_for, parse_cards, search_urls
from app.services.job_matcher import _location_score, filter_reasons, prefilter
from app.services.location_focus import (
    TIER_ABROAD,
    TIER_COUNTRY,
    TIER_PRIME,
    TIER_REMOTE,
    balance_by_location,
    get_focus,
    get_season,
    location_tier,
    season_status,
)
from app.services.presets import apply_preset
from tests.conftest import FIXTURES

INTERNSHALA = (FIXTURES / "scrapers" / "internshala_search.html").read_text()
FOCUS = get_focus(default_preferences())
SEASON = get_season(default_preferences())


def job(**kw) -> Job:  # type: ignore[no-untyped-def]
    base = dict(company_name="Acme", role_title="Software Engineer Intern", description="Python role",
                source_url="https://x/1", source_platform=ATSPlatform.GREENHOUSE, job_type=JobType.INTERNSHIP,
                is_remote=False, location="New Delhi, Delhi, India")
    base.update(kw)
    return Job(**base)


def scraped(location: str | None, remote: bool = False, n: int = 0) -> ScrapedJob:
    return ScrapedJob(company_name=f"Co {n}", role_title="SWE Intern", description="", source_url=f"https://x/{location}/{n}",
                      source_platform=ATSPlatform.CUSTOM, location=location, is_remote=remote)


def test_defaults_target_internships_in_india() -> None:
    prefs = default_preferences()
    assert prefs["job_types"] == ["internship"] and prefs["internship_season"] == "Summer 2027"
    assert prefs["location_focus"]["country"] == "India" and prefs["location_focus"]["country_share"] == 90
    assert "Delhi" in prefs["location_focus"]["prime_cities"] and "internshala" in prefs["platforms"]
    assert FOCUS is not None and SEASON is not None and SEASON.label == "Summer 2027"


def test_location_tiers() -> None:
    assert location_tier("New Delhi, Delhi, India", False, FOCUS) == TIER_PRIME
    assert location_tier("Gurugram, Haryana", False, FOCUS) == TIER_PRIME
    assert location_tier("Noida", False, FOCUS) == TIER_PRIME
    assert location_tier("Bengaluru, Karnataka", False, FOCUS) == TIER_COUNTRY
    assert location_tier("Remote in India", True, FOCUS) == TIER_COUNTRY
    assert location_tier("Remote", True, FOCUS) == TIER_REMOTE
    assert location_tier(None, False, FOCUS) == TIER_REMOTE
    assert location_tier("Indianapolis, Indiana", False, FOCUS) == TIER_ABROAD  # not India
    assert location_tier("San Francisco, CA", False, FOCUS) == TIER_ABROAD
    assert location_tier("San Francisco, CA", False, None) == TIER_COUNTRY  # no focus: everything is equal


def test_location_score_prefers_delhi() -> None:
    prefs = default_preferences()
    delhi, blr, sf = (_location_score(job(location=loc), prefs)[0] for loc in ("Delhi", "Bengaluru", "San Francisco, CA"))
    assert delhi > blr > sf


def test_scan_keeps_ninety_percent_in_india() -> None:
    home = [scraped("Delhi", n=i) for i in range(10)] + [scraped("Pune, India", n=i) for i in range(8)]
    abroad = [scraped("New York, NY", n=i) for i in range(30)] + [scraped("Remote", True, n=i) for i in range(5)]
    kept, dropped = balance_by_location(home + abroad, FOCUS)
    others = [j for j in kept if location_tier(j.location, j.is_remote, FOCUS) > TIER_COUNTRY]
    assert len(kept) - len(others) == 18 and len(others) == 2 and dropped == 33  # 18 : 2 = 90% : 10%
    assert all(j.is_remote for j in others)  # remote roles are preferred over on-site abroad
    # Nothing found in India: a few others are kept so the deck is never silently empty
    kept, _ = balance_by_location(abroad, FOCUS)
    assert len(kept) == 10
    assert balance_by_location(abroad, None) == (abroad, 0)


def test_search_query_uses_focus() -> None:
    query = SearchQuery.from_preferences(default_preferences())
    assert query.locations == ["Delhi, India", "India"] and query.focus is not None
    assert query.matches_location("Bengaluru, Karnataka", False)  # India cities that don't say "India"
    sorted_jobs = SCRAPERS["generic"]().filter([scraped("New York, NY"), scraped("Pune"), scraped("Noida")],
                                               SearchQuery(focus=FOCUS, limit=2, posted_within_days=3650))
    assert [j.location for j in sorted_jobs] == ["Noida", "Pune"]  # prime city first, then India, within the limit
    focus_off = {**default_preferences(), "location_focus": {"enabled": False}, "target_locations": []}
    assert SearchQuery.from_preferences(focus_off).focus is None


def test_season_rules() -> None:
    assert season_status("SWE Intern - Summer 2027", "", None, SEASON)[0] == "match"
    assert season_status("Data Intern", "", ["Summer 2027"], SEASON)[0] == "match"
    assert season_status("Intern 2027", "", None, SEASON)[0] == "match"
    assert season_status("SWE Intern (Summer 2026)", "", None, SEASON) == ("conflict", "Posting is for Summer 2026, not Summer 2027")
    assert season_status("SWE Intern, Fall '26", "", None, SEASON)[0] == "conflict"
    assert season_status("ML Intern", "Our Summer 2026 interns loved it", None, SEASON)[0] == "other_mentioned"
    assert season_status("Python Intern", "Starts immediately. 3 months", None, SEASON)[0] == "immediate"
    assert season_status("Intern", "Work 20 hrs/week this summer", None, SEASON)[0] == "unknown"
    assert season_status("SWE Intern (Summer 2026)", "", None, None)[0] == "unknown"


def test_season_filter_in_matching() -> None:
    prefs = default_preferences()
    assert prefilter(job(role_title="SWE Intern (Summer 2026)"), prefs, strict=False) == (
        False, "Posting is for Summer 2026, not Summer 2027")
    assert prefilter(job(role_title="SWE Intern - Summer 2027"), prefs, strict=False) == (True, None)
    hard, soft = filter_reasons(job(description="Starts immediately"), prefs)
    assert not hard and soft == ["Starts immediately (you're targeting Summer 2027)"]
    # Only internships are held to the season; full-time roles you opt into are not
    assert prefilter(job(role_title="Engineer 2026", job_type=JobType.FULL_TIME),
                     {**prefs, "job_types": ["full-time"], "internships_only": False}, strict=False)[0]


def test_review_queue_delhi_first_then_india(auth_client: TestClient) -> None:
    email = auth_client.get("/api/v1/auth/me").json()["email"]
    rows = [("San Francisco, CA", False, 95, "SWE Intern"), ("Bengaluru, Karnataka", False, 90, "SWE Intern"),
            ("Remote", True, 99, "SWE Intern"), ("New Delhi, India", False, 60, "SWE Intern"),
            ("Gurugram, Haryana", False, 70, "SWE Intern - Summer 2027"), ("Mumbai, India", False, 50, "Intern 2027")]
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        for i, (loc, remote, score, title) in enumerate(rows):
            j = job(location=loc, is_remote=remote, role_title=title, source_url=f"https://q/{i}", dedupe_key=f"q-{i}")
            db.add(j)
            db.flush()
            db.add(Application(user_id=user.id, job_id=j.id, status=ApplicationStatus.MATCHED, match_score=score))
        db.commit()
    deck = auth_client.get("/api/v1/review/queue").json()["items"]
    order = [c["job"]["location"] for c in deck]
    # Delhi NCR (Summer 2027 first), then India (Summer 2027 first), then remote, then abroad
    assert order == ["Gurugram, Haryana", "New Delhi, India", "Mumbai, India", "Bengaluru, Karnataka", "Remote",
                     "San Francisco, CA"]
    assert deck[0]["focus"] == {"location_tier": 0, "season": "match", "season_label": "Summer 2027", "country": "India"}


def test_india_preset_and_validation(auth_client: TestClient) -> None:
    prefs = apply_preset({"target_roles": ["Software Engineer"], "job_types": ["full-time"]}, "ai-python")
    assert prefs["job_types"] == ["internship"] and prefs["platforms"][0] == "internshala"
    assert prefs["location_focus"]["country_share"] == 100 and "Gurugram" in prefs["location_focus"]["prime_cities"]
    r = auth_client.post("/api/v1/users/me/preferences/preset/ai-python")
    assert r.status_code == 200 and r.json()["internship_season"] == "Summer 2027"
    bad = [{"location_focus": {"country": "India", "country_share": 140}}, {"internship_season": "someday"},
           {"progress_digest": "hourly"}]
    for body in bad:
        assert auth_client.put("/api/v1/users/me/preferences", json={"preferences": body}).status_code == 422
    ok = auth_client.put("/api/v1/users/me/preferences", json={"preferences": {
        "location_focus": {"enabled": True, "country": "India", "prime_cities": ["Delhi"], "country_share": 80},
        "internship_season": "Summer 2027", "progress_digest": "weekly"}})
    assert ok.status_code == 200 and ok.json()["location_focus"]["country_share"] == 80


def test_india_job_board_domains() -> None:
    assert base_for("Delhi, India") == "https://in.indeed.com"
    assert base_for("Bengaluru") == "https://in.indeed.com"
    assert base_for("Austin, TX") == "https://www.indeed.com"


# ------------------------------------------------------------------ Internshala
def test_internshala_parser() -> None:
    cards = parse_cards(INTERNSHALA)
    assert [c["company"] for c in cards] == ["Acme Labs Private Limited", "ByteWorks", "Nova Softwares"]
    first, wfh, old = cards
    assert first["url"].startswith("https://internshala.com/internship/detail/software-development-internship-in-delhi")
    assert first["locations"] == ["Delhi"] and first["stipend"] == "₹ 15,000 /month" and first["duration"] == "6 Months"
    assert wfh["immediate"] and old["locations"] == ["Gurgaon"] and old["id"] == "3012347"
    j = card_to_job(first)
    assert j.role_title == "Software Development Intern" and j.location == "Delhi, India"
    assert j.job_type == JobType.INTERNSHIP and j.raw["apply_on_site"] == "Internshala"
    assert location_tier(j.location, j.is_remote, FOCUS) == TIER_PRIME
    remote = card_to_job(wfh)
    assert remote.is_remote and remote.location == "Work from home, India" and "Starts immediately" in remote.description
    assert location_tier(remote.location, remote.is_remote, FOCUS) == TIER_COUNTRY


def test_internshala_search_urls() -> None:
    assert categories_for(["Machine Learning Intern"]) == ["machine-learning", "artificial-intelligence-ai"]
    assert categories_for([]) == ["software-development", "computer-science"]
    query = SearchQuery.from_preferences({**default_preferences(), "target_roles": ["Software Engineer Intern"],
                                          "sources": {"internshala_urls": ["https://internshala.com/internships/my-search/"]}})
    urls = search_urls(query)
    assert urls[0] == "https://internshala.com/internships/my-search/"
    assert "https://internshala.com/internships/software-development-internship-in-delhi/" in urls
    assert any("work-from-home-software-development" in u for u in urls) and len(urls) <= MAX_PAGES
    assert "https://internshala.com/internships/software-development-internship-in-noida/" in urls  # all of NCR


def test_internshala_searches_every_role_category_before_a_second_city() -> None:
    """The AI + Python roles span six categories; the old 12-page budget only ever reached the first three."""
    roles = ["AI Engineer Intern", "Generative AI Intern", "Python Developer Intern", "Backend Developer Intern",
             "Software Engineer Intern"]
    urls = search_urls(SearchQuery.from_preferences({**default_preferences(), "target_roles": roles}))
    for category in ("machine-learning", "artificial-intelligence-ai", "python-django", "backend-development",
                     "software-development", "computer-science"):
        assert f"https://internshala.com/internships/{category}-internship-in-delhi/" in urls
        assert f"https://internshala.com/internships/{category}-internship/" in urls
    assert urls.index("https://internshala.com/internships/computer-science-internship-in-delhi/") < urls.index(
        "https://internshala.com/internships/machine-learning-internship/")  # every category in Delhi first
    assert len(urls) == MAX_PAGES


@respx.mock
def test_internshala_shares_the_limit_between_categories_and_reads_page_two_when_short() -> None:
    def card(slug: str, i: int, city: str = "Delhi") -> str:
        return (f'<div class="individual_internship" internshipid="{slug}{i}"><h3 class="job-internship-name">'
                f'<a class="job-title-href" href="/internship/detail/{slug}-internship-at-co{i}">{slug.title()}</a></h3>'
                f'<p class="company-name">Co {slug} {i}</p><div class="locations"><a>{city}</a></div>'
                '<div class="status-success"><span>1 day ago</span></div></div>')

    def page(slug: str, n: int, offset: int = 0) -> str:
        return "<html><body>" + "".join(card(slug, offset + i) for i in range(n)) + "</body></html>"

    pages = {"machine-learning-internship-in-delhi/": page("ml", 20), "python-django-internship-in-delhi/": page("py", 3),
             "machine-learning-internship-in-delhi/page-2/": page("ml", 5, offset=100)}

    def respond(request: httpx.Request) -> httpx.Response:
        key = str(request.url).removeprefix("https://internshala.com/internships/")
        return httpx.Response(200, text=pages.get(key, "<html><body></body></html>"))

    route = respx.get(url__startswith="https://internshala.com/").mock(side_effect=respond)
    query = SearchQuery.from_preferences({**default_preferences(), "target_roles": ["Machine Learning Intern", "Python Developer Intern"]},
                                         limit=15)
    jobs = InternshalaScraper().search(query)
    assert len(jobs) == 15
    assert sum("Co py" in j.company_name for j in jobs) == 3  # all three, not fifteen machine-learning cards first
    fetched = [str(c.request.url) for c in route.calls]
    # 23 internships on the first pages is less than twice the limit: page 2 of the full page, and only of that one
    assert [u for u in fetched if u.endswith("/page-2/")] == ["https://internshala.com/internships/machine-learning-internship-in-delhi/page-2/"]
    assert any("Co ml 100" == j.company_name for j in InternshalaScraper().search(SearchQuery(**{**query.__dict__, "limit": 50})))



@respx.mock
def test_internshala_search_end_to_end() -> None:
    respx.get(url__startswith="https://internshala.com/").mock(return_value=httpx.Response(200, text=INTERNSHALA))
    query = SearchQuery.from_preferences({**default_preferences(), "target_roles": ["Software Engineer Intern"]}, limit=10)
    jobs = InternshalaScraper().search(query)
    assert {j.company_name for j in jobs} == {"Acme Labs Private Limited", "ByteWorks", "Nova Softwares"}
    assert jobs[0].location in ("Delhi, India", "Gurgaon, India")  # prime-city postings first


def test_internshala_jobs_ask_you_to_apply_yourself(auth_client: TestClient, master_resume: dict) -> None:
    from app.services import agent_orchestrator as orch

    email = auth_client.get("/api/v1/auth/me").json()["email"]
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        j = job(source_url="https://internshala.com/internship/detail/x", raw_data={"apply_on_site": "Internshala"},
                source_platform=ATSPlatform.CUSTOM)
        db.add(j)
        db.flush()
        app = Application(user_id=user.id, job_id=j.id, status=ApplicationStatus.PREPARING, auto_submit=True,
                          review_decision="keep")
        db.add(app)
        db.flush()
        orch.stage_application(db, str(app.id))  # no browser is opened
        assert app.status == ApplicationStatus.PENDING_APPROVAL and app.auto_submit is False
        assert app.needs_manual_review and "I Applied" in app.manual_review_reason
