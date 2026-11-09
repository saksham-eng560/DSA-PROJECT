"""Scraper registry."""

from __future__ import annotations

from app.models.enums import ATSPlatform
from app.scrapers.ashby import AshbyScraper
from app.scrapers.ats_detect import detect_ats_platform, parse_ats_url
from app.scrapers.base import BaseScraper, RateLimited, ScrapedJob, ScraperError, SearchQuery
from app.scrapers.generic import GenericScraper
from app.scrapers.glassdoor import GlassdoorScraper
from app.scrapers.greenhouse import GreenhouseScraper
from app.scrapers.indeed import IndeedScraper
from app.scrapers.internshala import InternshalaScraper
from app.scrapers.internships import InternshipListScraper
from app.scrapers.lever import LeverScraper
from app.scrapers.linkedin import LinkedInScraper
from app.scrapers.naukri import NaukriScraper
from app.scrapers.remote_boards import RemoteBoardsScraper
from app.scrapers.top_companies import TopCompaniesScraper
from app.scrapers.unstop import UnstopScraper
from app.scrapers.wellfound import WellfoundScraper
from app.scrapers.workday import WorkdayScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    "top_companies": TopCompaniesScraper,
    "internshala": InternshalaScraper,
    "internships": InternshipListScraper,
    "naukri": NaukriScraper,  # Naukri.com search API (India)
    "unstop": UnstopScraper,  # Unstop internships (India)
    "remote_boards": RemoteBoardsScraper,  # Remotive, Jobicy, Himalayas: remote intern / entry-level roles open to you
    "greenhouse": GreenhouseScraper,
    "lever": LeverScraper,
    "ashby": AshbyScraper,
    "workday": WorkdayScraper,
    "linkedin": LinkedInScraper,
    "indeed": IndeedScraper,
    "glassdoor": GlassdoorScraper,
    "wellfound": WellfoundScraper,
    "generic": GenericScraper,
}

PLATFORM_TO_SCRAPER = {
    ATSPlatform.GREENHOUSE: "greenhouse",
    ATSPlatform.LEVER: "lever",
    ATSPlatform.ASHBY: "ashby",
    ATSPlatform.WORKDAY: "workday",
    ATSPlatform.LINKEDIN: "linkedin",
}


def get_scraper(name: str) -> BaseScraper:
    return SCRAPERS[name]()


def fetch_job_from_url(url: str) -> ScrapedJob | None:
    """Import a single posting from any supported URL (used by "Add job by URL")."""
    platform = detect_ats_platform(url)
    name = PLATFORM_TO_SCRAPER.get(platform, "generic")
    if "internshala.com/" in url:
        name = "internshala"
    job = get_scraper(name).fetch_job(url)
    if job is None and name != "generic":
        job = get_scraper("generic").fetch_job(url)
    return job


__all__ = [
    "SCRAPERS",
    "BaseScraper",
    "RateLimited",
    "ScrapedJob",
    "ScraperError",
    "SearchQuery",
    "detect_ats_platform",
    "fetch_job_from_url",
    "get_scraper",
    "parse_ats_url",
]
