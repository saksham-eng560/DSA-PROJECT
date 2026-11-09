"""Curated internship / new-grad lists published as JSON on GitHub (updated several times a day).

The SimplifyJobs and vanshb03 lists track thousands of open internships at startups and large
companies alike, each with a direct application link (Greenhouse, Lever, Ashby, Workday, ...).
A listing carries no description; ``agent.jobs.enrich_job`` fetches it from the posting
before the resume is tailored.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from app.models.enums import ATSPlatform, ExperienceLevel, JobType
from app.scrapers.ats_detect import detect_ats_platform
from app.scrapers.base import BaseScraper, ScrapedJob, ScraperError, SearchQuery, detect_remote

logger = logging.getLogger(__name__)

RAW = "https://raw.githubusercontent.com"
LISTS: dict[str, str] = {
    "simplify-internships": f"{RAW}/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
    "vanshb03-internships": f"{RAW}/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json",
    "simplify-new-grad": f"{RAW}/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
}
DEFAULT_LISTS = ["simplify-internships", "vanshb03-internships"]
CACHE_SECONDS = 30 * 60

_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_cache_lock = threading.Lock()


def list_url(name_or_url: str) -> str:
    return LISTS.get(name_or_url.strip(), name_or_url.strip())


def clean_url(url: str) -> str:
    """Drop tracking parameters (utm_*, ref=Simplify) so the same posting dedupes across sources."""
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and not (k.lower() == "ref" and v.lower() == "simplify")]
    return urlunparse(parts._replace(query=urlencode(query)))


class InternshipListScraper(BaseScraper):
    platform = ATSPlatform.CUSTOM
    rate_key = "github"

    def load(self, name_or_url: str) -> list[dict[str, Any]]:
        url = list_url(name_or_url)
        if not url.startswith("https://"):
            raise ScraperError(f"Unknown internship list '{name_or_url}'")
        with _cache_lock:
            cached = _cache.get(url)
            if cached and time.time() - cached[0] < CACHE_SECONDS:
                return cached[1]
        data = self.get_json(url)
        if not isinstance(data, list):
            raise ScraperError(f"Unexpected listing format at {url}")
        with _cache_lock:
            _cache[url] = (time.time(), data)
        return data

    @staticmethod
    def to_job(item: dict[str, Any], source: str) -> ScrapedJob | None:
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        company = str(item.get("company_name") or "").strip()
        if not url.startswith("http") or not title or not company:
            return None
        url = clean_url(url)
        new_grad = "new-grad" in source or "new_grad" in source.lower()
        locations = [str(loc) for loc in item.get("locations") or [] if loc]
        location = "; ".join(locations)[:255] or None
        terms = [str(t) for t in item.get("terms") or ([item["season"]] if item.get("season") else [])]
        sponsorship = str(item.get("sponsorship") or "")
        degrees = [str(d) for d in item.get("degrees") or []]
        lines = [f"{title} at {company}."]
        if locations:
            lines.append(f"Locations: {', '.join(locations)}.")
        if terms:
            lines.append(f"Term: {', '.join(terms)}.")
        if item.get("category"):
            lines.append(f"Category: {item['category']}.")
        if degrees:
            lines.append(f"Degrees: {', '.join(degrees)}.")
        if sponsorship and sponsorship.lower() != "other":
            lines.append(f"Sponsorship: {sponsorship}.")
        posted = item.get("date_posted")
        platform = detect_ats_platform(url)
        return ScrapedJob(
            company_name=company,
            role_title=title,
            description=" ".join(lines),
            source_url=url,
            application_url=url,
            source_platform=platform if platform != ATSPlatform.UNKNOWN else ATSPlatform.CUSTOM,
            location=location,
            is_remote=detect_remote(location),
            job_type=JobType.FULL_TIME if new_grad else JobType.INTERNSHIP,
            experience_level=ExperienceLevel.ENTRY if new_grad else ExperienceLevel.INTERNSHIP,
            posted_date=datetime.fromtimestamp(int(posted), tz=UTC).date() if isinstance(posted, (int, float)) else None,
            external_id=str(item.get("id") or "") or None,
            raw={"listing_source": source, "sponsorship": sponsorship or None, "terms": terms, "degrees": degrees,
                 "category": item.get("category"), "company_url": item.get("company_url") or None},
        ).finalize()

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        names = query.sources.get("internship_lists")
        if names is None:
            names = DEFAULT_LISTS
        jobs: list[ScrapedJob] = []
        seen: set[str] = set()
        errors: list[str] = []
        for name in names:
            if query.time_up():
                break
            try:
                items = self.load(name)
            except (ScraperError, httpx.HTTPError, ValueError) as exc:  # e.g. HTTP 403, or not JSON
                logger.warning("Internship list %s failed: %s", name, exc)
                errors.append(f"Internship list {name}: {exc}")
                continue
            live = [i for i in items if i.get("active", True) and i.get("is_visible", True)]
            live.sort(key=lambda i: i.get("date_posted") or 0, reverse=True)
            for item in live:
                job = self.to_job(item, name)
                if job is None or job.source_url in seen:
                    continue
                seen.add(job.source_url)
                jobs.append(job)
        if not jobs and errors:  # every list failed: say why instead of "found 0 jobs"
            raise ScraperError(errors[0])
        return self.filter(jobs, query)
