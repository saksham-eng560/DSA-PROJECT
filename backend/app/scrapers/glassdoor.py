"""Glassdoor search via a stealth browser (job cards + detail pane). Often redirects to company ATS."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from app.automation.browser import BrowserUnavailable
from app.models.enums import ATSPlatform
from app.scrapers.base import ScrapedJob, ScraperError, SearchQuery, parse_date, parse_salary
from app.scrapers.browser_scraper import BrowserScraper
from app.services.location_focus import is_indian_location

logger = logging.getLogger(__name__)

BASE = "https://www.glassdoor.com"
INDIA_BASE = "https://www.glassdoor.co.in"


def parse_listings(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for card in soup.select("li[data-test='jobListing'], li[data-jobid]"):
        link = card.select_one("a[data-test='job-link'], a[data-test='job-title'], a[href*='job-listing']")
        if not link:
            continue
        title = link.get_text(strip=True)
        company = card.select_one("[data-test='emp-name'], [class*='EmployerProfile_compactEmployerName'], .employer-name")
        location = card.select_one("[data-test='emp-location'], [data-test='location']")
        salary = card.select_one("[data-test='detailSalary']")
        age = card.select_one("[data-test='job-age']")
        job_id = card.get("data-jobid") or (re.search(r"jobListingId=(\d+)", link.get("href", "")) or [None, None])[1]
        out.append(
            {
                "id": job_id,
                "title": title,
                "company": re.sub(r"\s*\d\.\d\s*★?$", "", company.get_text(strip=True)) if company else "",
                "location": location.get_text(strip=True) if location else "",
                "salary": salary.get_text(strip=True) if salary else "",
                "age": age.get_text(strip=True) if age else "",
                "url": urljoin(BASE, link.get("href", "")),
            }
        )
    return out


def parse_detail(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    node = soup.select_one("[class*='JobDetails_jobDescription'], #JobDescriptionContainer, .jobDescriptionContent")
    return node.decode_contents() if node else ""


class GlassdoorScraper(BrowserScraper):
    platform = ATSPlatform.GLASSDOOR
    rate_key = "glassdoor"

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        jobs: list[ScrapedJob] = []
        try:
            with self.open_session() as session:
                for keyword in query.keywords or [""]:
                    for location in query.locations or [""]:
                        if len(jobs) >= query.limit or query.time_up():
                            break  # don't open more search pages once the limit or the scan's time is reached
                        age = min(query.posted_within_days or 14, 30)
                        base = INDIA_BASE if is_indian_location(location) else BASE
                        url = (f"{base}/Job/jobs.htm?sc.keyword={quote_plus(keyword)}"
                               f"&locKeyword={quote_plus(location)}&fromAge={age}")
                        if query.remote:
                            url += "&remoteWorkType=1"
                        for item in parse_listings(self.browser_get(session, url, "li[data-test='jobListing']")):
                            if len(jobs) >= query.limit or query.time_up():
                                break
                            if not query.matches_title(item["title"]):
                                continue
                            if query.is_known(item["url"]):  # already saved: no need to open it again
                                description = ""
                            else:
                                try:
                                    description = parse_detail(self.browser_get(session, item["url"], "[class*='JobDetails_jobDescription']"))
                                except ScraperError:
                                    description = ""
                            sal = parse_salary(item["salary"])
                            jobs.append(
                                ScrapedJob(
                                    company_name=item["company"] or "Unknown",
                                    role_title=item["title"],
                                    description=description or item["title"],
                                    source_url=item["url"],
                                    application_url=item["url"],
                                    source_platform=ATSPlatform.GLASSDOOR,
                                    location=item["location"],
                                    salary_min=sal[0] if sal else None,
                                    salary_max=sal[1] if sal else None,
                                    salary_currency=sal[2] if sal else None,
                                    posted_date=parse_date(item["age"]),
                                    external_id=item["id"],
                                ).finalize()
                            )
        except BrowserUnavailable as exc:
            raise ScraperError(str(exc)) from exc
        return self.filter(jobs, query)
