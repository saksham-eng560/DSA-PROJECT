"""Ashby public job-board API: https://developers.ashbyhq.com/docs/public-job-posting-api"""

from __future__ import annotations

import logging
from typing import Any

from app.models.enums import ATSPlatform, JobType
from app.scrapers.ats_detect import parse_ats_url
from app.scrapers.base import BaseScraper, ScrapedJob, SearchQuery, parse_date

logger = logging.getLogger(__name__)

API = "https://api.ashbyhq.com/posting-api/job-board"
EMPLOYMENT = {"FullTime": JobType.FULL_TIME, "PartTime": JobType.PART_TIME, "Intern": JobType.INTERNSHIP,
              "Contract": JobType.CONTRACT, "Temporary": JobType.CONTRACT}


class AshbyScraper(BaseScraper):
    platform = ATSPlatform.ASHBY
    rate_key = "ashby"

    def _to_job(self, board: str, item: dict[str, Any]) -> ScrapedJob:
        comp = item.get("compensation") or {}
        salary_min = salary_max = None
        currency = None
        for component in comp.get("summaryComponents") or []:
            if component.get("compensationType") == "Salary" and component.get("interval") in (None, "1 YEAR"):
                salary_min, salary_max = component.get("minValue"), component.get("maxValue")
                currency = component.get("currencyCode")
        locations = [item.get("location")] + [s.get("location") for s in item.get("secondaryLocations") or []]
        url = item.get("jobUrl") or f"https://jobs.ashbyhq.com/{board}/{item['id']}"
        return ScrapedJob(
            company_name=item.get("organizationName") or board.replace("-", " ").title(),
            role_title=item.get("title") or "",
            description=item.get("descriptionHtml") or item.get("descriptionPlain") or "",
            source_url=url,
            application_url=item.get("applyUrl") or f"{url}/application",
            source_platform=ATSPlatform.ASHBY,
            location=", ".join(loc for loc in locations if loc) or None,
            is_remote=bool(item.get("isRemote")) or item.get("workplaceType") == "Remote",
            job_type=EMPLOYMENT.get(item.get("employmentType") or ""),
            salary_min=int(salary_min) if salary_min else None,
            salary_max=int(salary_max) if salary_max else None,
            salary_currency=currency,
            posted_date=parse_date(item.get("publishedAt")),
            external_id=item.get("id"),
            raw={"board": board, "department": item.get("department"), "team": item.get("team")},
        ).finalize()

    def list_board(self, board: str) -> list[ScrapedJob]:
        data = self.get_json(f"{API}/{board}", params={"includeCompensation": "true"})
        return [self._to_job(board, item) for item in data.get("jobs", []) if item.get("isListed", True)]

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        jobs = self.map_sources(query.sources.get("ashby_boards") or [],
                                lambda board: self.filter(self.list_board(board), query), query, "Ashby board")
        return jobs[: query.limit]

    def fetch_job(self, url: str) -> ScrapedJob | None:
        ref = parse_ats_url(url)
        if not ref.board or not ref.job_id:
            return None
        for job in self.list_board(ref.board):
            if job.external_id == ref.job_id:
                return job
        return None
