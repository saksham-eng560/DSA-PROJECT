"""Indeed search via a stealth browser (parses the embedded mosaic job-card JSON)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from app.automation.browser import BrowserUnavailable
from app.models.enums import ATSPlatform
from app.scrapers.base import (
    Blocked,
    RateLimited,
    ScrapedJob,
    ScraperError,
    SearchQuery,
    interleave,
    parse_date,
    parse_salary,
)
from app.scrapers.browser_scraper import BrowserScraper, extract_assigned_json
from app.services.location_focus import is_indian_location

logger = logging.getLogger(__name__)

BASE = "https://www.indeed.com"
INDIA_BASE = "https://in.indeed.com"


PAGE_SIZE = 10  # cards per Indeed results page (``&start=10`` is page 2)
MAX_PAGES = 2
FRESHNESS = (1, 3, 7, 14)  # the "date posted" filter values Indeed accepts


def base_for(location: str) -> str:
    """Indeed runs one site per country; Indian locations are searched on in.indeed.com."""
    return INDIA_BASE if is_indian_location(location) else BASE


def place_for(location: str) -> str:
    """What to type in Indeed's "where" box: on in.indeed.com "Delhi" rather than "Delhi, India", and nothing
    for "India" (the whole site is India)."""
    if not is_indian_location(location):
        return location
    city = location.split(",")[0].strip()
    return "" if city.lower() == "india" else city


def fromage(days: int | None) -> int:
    days = days or 14
    return next((d for d in FRESHNESS if d >= days), FRESHNESS[-1])


def parse_job_cards(html: str) -> list[dict[str, Any]]:
    data = extract_assigned_json(html, 'window.mosaic.providerData["mosaic-provider-jobcards"]')
    if not data:
        return []
    model = (data.get("metaData") or {}).get("mosaicProviderJobCardsModel") or {}
    return model.get("results") or []


def parse_description(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    node = soup.select_one("#jobDescriptionText, [data-testid='jobsearch-JobComponent-description']")
    return node.decode_contents() if node else ""


class IndeedScraper(BrowserScraper):
    platform = ATSPlatform.INDEED
    rate_key = "indeed"

    def card_to_job(self, card: dict[str, Any], description: str, base: str = BASE) -> ScrapedJob:
        salary = (card.get("salarySnippet") or {}).get("text") or ""
        parsed_salary = parse_salary(salary)
        jk = card.get("jobkey")
        url = f"{base}/viewjob?jk={jk}"
        third_party = card.get("thirdPartyApplyUrl")
        return ScrapedJob(
            company_name=card.get("company") or "Unknown",
            role_title=card.get("displayTitle") or card.get("title") or "",
            description=description or card.get("snippet") or "",
            source_url=url,
            application_url=third_party or url,
            source_platform=ATSPlatform.INDEED,
            location=card.get("formattedLocation"),
            is_remote=bool(card.get("remoteLocation")),
            salary_min=parsed_salary[0] if parsed_salary else None,
            salary_max=parsed_salary[1] if parsed_salary else None,
            salary_currency=parsed_salary[2] if parsed_salary else None,
            posted_date=parse_date(card.get("pubDate")),
            external_id=jk,
            easy_apply=bool(card.get("indeedApplyEnabled")),
            raw={"third_party_apply_url": third_party},
        ).finalize()

    def search_url(self, keyword: str, location: str, page: int, query: SearchQuery) -> tuple[str, str]:
        base = base_for(location)
        url = f"{base}/jobs?q={quote_plus(keyword)}&l={quote_plus(place_for(location))}&fromage={fromage(query.posted_within_days)}"
        if query.remote:
            url += "&sc=0kf%3Aattr%28DSQF7%29%3B"
        if page > 1:
            url += f"&start={(page - 1) * PAGE_SIZE}"
        return base, url

    def _cards(self, session: Any, query: SearchQuery, errors: list[str]) -> list[tuple[dict[str, Any], str]]:
        """Result cards of every role x place search (page 1 of each, then page 2), one from each in turn."""
        searches = list(dict.fromkeys((k, loc) for k in (query.keywords or [""])
                                      for loc in (query.locations or ["remote" if query.remote else ""])))
        per_search: dict[tuple[str, str], list[tuple[dict[str, Any], str]]] = {s: [] for s in searches}
        active = list(searches)
        seen: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            for search in list(active):
                if query.time_up():
                    return interleave(per_search.values())
                base, url = self.search_url(*search, page, query)
                try:
                    cards = parse_job_cards(self.browser_get(session, url, "#mosaic-provider-jobcards"))
                except (RateLimited, Blocked) as exc:  # no point asking again this scan
                    errors.append(str(exc))
                    return interleave(per_search.values())
                except ScraperError as exc:
                    errors.append(str(exc))
                    active.remove(search)
                    continue
                if len(cards) < PAGE_SIZE:
                    active.remove(search)
                for card in cards:
                    key = card.get("jobkey")
                    if not key or key in seen or not query.matches_title(card.get("displayTitle") or ""):
                        continue
                    seen.add(key)
                    per_search[search].append((card, base))
            if not active or sum(len(c) for c in per_search.values()) >= query.limit * 2:
                break
        return interleave(per_search.values())

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        jobs: list[ScrapedJob] = []
        errors: list[str] = []
        try:
            with self.open_session() as session:
                details = True
                for card, base in self._cards(session, query, errors):
                    if len(self.filter(jobs, query)) >= query.limit:
                        break
                    detail_url = f"{base}/viewjob?jk={card.get('jobkey')}"
                    description = ""
                    # Out of time (or budget): the rest keep the card's snippet; the full text comes later.
                    if details and not query.time_up() and not query.is_known(detail_url):
                        try:
                            description = parse_description(self.browser_get(session, detail_url, "#jobDescriptionText"))
                        except (RateLimited, Blocked):
                            details = False
                        except ScraperError:
                            description = ""
                    jobs.append(self.card_to_job(card, description, base))
        except BrowserUnavailable as exc:
            raise ScraperError(str(exc)) from exc
        if not jobs and errors:
            raise ScraperError(errors[0])
        return self.filter(jobs, query)
