"""Wellfound (AngelList Talent) startup jobs via a stealth browser (__NEXT_DATA__ Apollo state)."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.automation.browser import BrowserUnavailable
from app.models.enums import ATSPlatform, JobType
from app.scrapers.base import Blocked, RateLimited, ScrapedJob, ScraperError, SearchQuery, parse_date, parse_salary
from app.scrapers.browser_scraper import BrowserScraper, next_data

logger = logging.getLogger(__name__)

BASE = "https://wellfound.com"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# Wellfound has a page per role ("/role/l/backend-engineer/india"), not per job title: "AI Engineer Intern"
# lives under machine-learning-engineer. Roles not listed here use their own name without "intern".
ROLE_PAGES: list[tuple[str, str]] = [
    (r"\b(ai|ml|machine learning|deep learning|llm|nlp|gen ?ai|generative)\b", "machine-learning-engineer"),
    (r"data scien", "data-scientist"),
    (r"data engineer", "data-engineer"),
    (r"full[\s-]?stack", "full-stack-engineer"),
    (r"front[\s-]?end|react", "frontend-engineer"),
    (r"back[\s-]?end|python|django|flask|fast ?api", "backend-engineer"),
    (r"devops|sre|cloud", "devops-engineer"),
    (r"android|ios|mobile|flutter", "mobile-engineer"),
    (r"software|developer|engineer|sde|swe|programm", "software-engineer"),
]
MAX_ROLE_PAGES = 4


def role_pages(keywords: list[str]) -> list[str]:
    out: list[str] = []
    for keyword in keywords or ["software engineer"]:
        text = keyword.lower()
        page = next((p for pattern, p in ROLE_PAGES if re.search(pattern, text)), None)
        page = page or _slug(re.sub(r"\b(intern|interns|internship|internships|trainee)\b", " ", text))
        if page and page not in out:
            out.append(page)
    return out[:MAX_ROLE_PAGES]


def place_pages(query: SearchQuery) -> list[str]:
    """Location slugs: your focus country (its page includes your prime city), else your target places, else
    "remote" when you only want remote roles (``/role/l/<role>/remote``)."""
    if query.focus is not None:
        return [_slug(query.focus.country)]
    places = [_slug(loc.split(",")[0]) for loc in query.locations if loc.strip()]
    return list(dict.fromkeys(p for p in places if p)) or ["remote" if query.remote else ""]


def parse_apollo_jobs(data: Any) -> list[dict[str, Any]]:
    """Walk the Apollo cache and join JobListing nodes with their Startup."""
    state = (((data or {}).get("props") or {}).get("pageProps") or {}).get("apolloState") or {}
    graph = state.get("data") or state
    jobs = []
    for key, node in graph.items():
        if not isinstance(node, dict) or "JobListing" not in str(node.get("__typename") or key):
            continue
        startup_ref = (node.get("startup") or {}).get("__ref") if isinstance(node.get("startup"), dict) else None
        startup = graph.get(startup_ref, {}) if startup_ref else {}
        jobs.append(
            {
                "id": node.get("id"),
                "title": node.get("title") or "",
                "slug": node.get("slug") or _slug(node.get("title") or ""),
                "description": node.get("description") or node.get("descriptionSnippet") or "",
                "locations": node.get("locationNames") or [],
                "remote": bool(node.get("remote")),
                "compensation": node.get("compensation") or "",
                "job_type": node.get("jobType") or "",
                "posted": node.get("liveStartAt") or node.get("postedAt"),
                "company": startup.get("name") or "",
                "company_slug": startup.get("slug") or "",
                "logo": startup.get("logoUrl"),
            }
        )
    return jobs


class WellfoundScraper(BrowserScraper):
    platform = ATSPlatform.WELLFOUND
    rate_key = "wellfound"

    def item_to_job(self, item: dict[str, Any]) -> ScrapedJob:
        sal = parse_salary(item["compensation"])
        job_url = f"{BASE}/jobs/{item['id']}-{item['slug']}"
        return ScrapedJob(
            company_name=item["company"] or "Startup",
            role_title=item["title"],
            description=item["description"] or item["title"],
            source_url=job_url,
            application_url=job_url,
            source_platform=ATSPlatform.WELLFOUND,
            location=", ".join(item["locations"]) or None,
            is_remote=item["remote"],
            job_type=JobType.INTERNSHIP if "intern" in item["job_type"].lower() else None,
            salary_min=sal[0] if sal else None,
            salary_max=sal[1] if sal else None,
            salary_currency=sal[2] if sal else None,
            posted_date=parse_date(item["posted"]),
            external_id=str(item["id"]),
            company_logo_url=item["logo"],
            easy_apply=True,
        ).finalize()

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        jobs: list[ScrapedJob] = []
        seen: set[str] = set()
        errors: list[str] = []
        pages = [(role, place) for role in role_pages(query.keywords) for place in place_pages(query)]
        try:
            with self.open_session() as session:
                for done, (role, place) in enumerate(pages):
                    query.report(done, len(pages))
                    if query.time_up() or len(self.filter(jobs, query)) >= query.limit:
                        break
                    url = f"{BASE}/role/l/{role}/{place}" if place else f"{BASE}/role/r/{role}"
                    try:
                        html = self.browser_get(session, url, "[data-test='StartupResult']")
                    except (RateLimited, Blocked) as exc:
                        errors.append(str(exc))
                        break
                    except ScraperError as exc:  # e.g. no page for that role: try the next one
                        errors.append(str(exc))
                        continue
                    for item in parse_apollo_jobs(next_data(html)):
                        key = str(item["id"])
                        if key in seen or not item["title"] or not query.matches_title(item["title"]):
                            continue
                        seen.add(key)
                        jobs.append(self.item_to_job(item))
                query.report(len(pages), len(pages))
        except BrowserUnavailable as exc:
            raise ScraperError(str(exc)) from exc
        if not jobs and errors:
            raise ScraperError(errors[0])
        return self.filter(jobs, query)
