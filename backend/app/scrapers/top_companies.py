"""Internships at renowned companies: big tech, product-based companies, top Indian and global startups and AI
companies (services/company_catalog.py).

Companies with their own Greenhouse / Lever / Ashby board or Workday site are read straight from it; the
ones that hire through their own career site (Google, Microsoft, Amazon, Flipkart...) are found with a
LinkedIn search for "<company> intern", keeping only that company's postings. Only internships are kept,
filtered by your target roles. All of it runs in parallel within the scan's time limit; a board that has
moved is skipped.
"""

from __future__ import annotations

import dataclasses
import logging

from app.models.enums import ATSPlatform
from app.scrapers.ashby import AshbyScraper
from app.scrapers.base import BaseScraper, ScrapedJob, SearchQuery
from app.scrapers.greenhouse import GreenhouseScraper
from app.scrapers.lever import LeverScraper
from app.scrapers.linkedin import LinkedInScraper
from app.scrapers.workday import WorkdayScraper
from app.services.company_catalog import CATALOG, Company, match_company
from app.services.intern_level import is_internship

logger = logging.getLogger(__name__)

LINKEDIN_PER_COMPANY = 8  # one results page per company: enough, and kind to LinkedIn's rate limits


def tasks() -> list[str]:
    """Every board / search to run, as "kind|token|company"."""
    out = []
    for company in CATALOG:
        for kind in ("greenhouse", "lever", "ashby", "workday"):
            token = getattr(company, kind)
            if token:
                out.append(f"{kind}|{token}|{company.name}")
        if company.linkedin:
            out.append(f"linkedin||{company.name}")
    return out


class TopCompaniesScraper(BaseScraper):
    platform = ATSPlatform.CUSTOM
    rate_key = "top_companies"

    def _keep(self, jobs: list[ScrapedJob], query: SearchQuery, company: Company | None) -> list[ScrapedJob]:
        out = []
        for job in self.filter(jobs, query):
            if not is_internship(job.role_title):  # an intern title, whatever the site calls the job type
                continue
            if company is not None:
                job.raw = {**(job.raw or {}), "top_company": company.name, "company_tier": company.tier}
            out.append(job)
        return out

    def _one(self, task: str, query: SearchQuery) -> list[ScrapedJob]:
        kind, token, name = task.split("|", 2)
        company = next((c for c in CATALOG if c.name == name), None)
        quiet = dataclasses.replace(query, progress=None)
        if kind == "greenhouse":
            return self._keep(GreenhouseScraper().list_board(token), quiet, company)
        if kind == "lever":
            return self._keep(LeverScraper().list_company(token), quiet, company)
        if kind == "ashby":
            return self._keep(AshbyScraper().list_board(token), quiet, company)
        if kind == "workday":
            return self._keep(WorkdayScraper().search_site(token, dataclasses.replace(quiet, keywords=["intern"])),
                              quiet, company)
        linkedin = LinkedInScraper()
        # Only that company's results are opened: LinkedIn's hourly request budget is shared with the LinkedIn
        # source, so none of it goes on other companies' postings.
        linkedin.accept = lambda summary: match_company(summary.get("company")) is company
        # One search across your focus country (it includes your prime city) instead of one per place.
        locations = [query.focus.country.title()] if query.focus is not None else query.locations
        found = linkedin.search(dataclasses.replace(
            quiet, search_terms=[f"{name} intern"], limit=LINKEDIN_PER_COMPANY, job_types=["internship"],
            locations=locations))
        return self._keep([j for j in found if match_company(j.company_name) is company], quiet, company)

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        jobs = self.map_sources(tasks(), lambda task: self._one(task, query), query, "company")
        return jobs[: max(query.limit, 150)]

    def fetch_job(self, url: str) -> ScrapedJob | None:
        return None  # postings are imported through their own board's scraper
