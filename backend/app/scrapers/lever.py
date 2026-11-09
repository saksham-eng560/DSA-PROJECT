"""Lever Postings API (public): https://github.com/lever/postings-api"""

from __future__ import annotations

import logging
from typing import Any

from app.models.enums import ATSPlatform, JobType
from app.scrapers.ats_detect import parse_ats_url
from app.scrapers.base import BaseScraper, ScrapedJob, ScraperError, SearchQuery, parse_date

logger = logging.getLogger(__name__)

API = "https://api.lever.co/v0/postings"
COMMITMENT = {"full-time": JobType.FULL_TIME, "full time": JobType.FULL_TIME, "part-time": JobType.PART_TIME,
              "part time": JobType.PART_TIME, "intern": JobType.INTERNSHIP, "internship": JobType.INTERNSHIP,
              "contract": JobType.CONTRACT, "contractor": JobType.CONTRACT, "temporary": JobType.CONTRACT}


class LeverScraper(BaseScraper):
    platform = ATSPlatform.LEVER
    rate_key = "lever"

    def _to_job(self, company_slug: str, item: dict[str, Any], company_name: str | None = None) -> ScrapedJob:
        cats = item.get("categories") or {}
        sections = []
        for lst in item.get("lists") or []:
            sections.append(f"<h3>{lst.get('text', '')}</h3>{lst.get('content', '')}")
        description = (item.get("description") or "") + "".join(sections) + (item.get("additional") or "")
        commitment = (cats.get("commitment") or "").lower()
        salary = item.get("salaryRange") or {}
        job_type = next((jt for key, jt in COMMITMENT.items() if key in commitment), None)
        location = cats.get("location") or ", ".join(cats.get("allLocations") or [])
        return ScrapedJob(
            company_name=company_name or company_slug.replace("-", " ").title(),
            role_title=item.get("text") or "",
            description=description,
            source_url=item.get("hostedUrl") or f"https://jobs.lever.co/{company_slug}/{item['id']}",
            application_url=item.get("applyUrl") or f"https://jobs.lever.co/{company_slug}/{item['id']}/apply",
            source_platform=ATSPlatform.LEVER,
            location=location or None,
            is_remote=(item.get("workplaceType") == "remote"),
            job_type=job_type,
            salary_min=salary.get("min") if salary.get("interval", "per-year-salary") == "per-year-salary" else None,
            salary_max=salary.get("max") if salary.get("interval", "per-year-salary") == "per-year-salary" else None,
            salary_currency=salary.get("currency"),
            posted_date=parse_date(item.get("createdAt")),
            external_id=item.get("id"),
            raw={"company": company_slug, "team": cats.get("team"), "department": cats.get("department"),
                 "workplaceType": item.get("workplaceType")},
        ).finalize()

    def list_company(self, slug: str) -> list[ScrapedJob]:
        data = self.get_json(f"{API}/{slug}", params={"mode": "json"})
        if not isinstance(data, list):
            raise ScraperError(f"Unexpected Lever response for {slug}")
        return [self._to_job(slug, item) for item in data]

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        jobs = self.map_sources(query.sources.get("lever_companies") or [],
                                lambda slug: self.filter(self.list_company(slug), query), query, "Lever company")
        return jobs[: query.limit]

    def fetch_job(self, url: str) -> ScrapedJob | None:
        ref = parse_ats_url(url)
        if not ref.board or not ref.job_id:
            return None
        return self._to_job(ref.board, self.get_json(f"{API}/{ref.board}/{ref.job_id}", params={"mode": "json"}))
