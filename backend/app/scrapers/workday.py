"""Workday career sites via the public CXS JSON endpoints used by every *.myworkdayjobs.com site."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from app.models.enums import ATSPlatform, JobType
from app.scrapers.ats_detect import parse_ats_url
from app.scrapers.base import BaseScraper, ScrapedJob, ScraperError, SearchQuery, parse_date
from app.services.location_focus import TIER_ABROAD, location_tier, only_home

logger = logging.getLogger(__name__)
_SEVERAL_PLACES = re.compile(r"^\s*\d+\s+locations?\s*$", re.I)


def clearly_abroad(locations_text: str | None, query: SearchQuery) -> bool:
    """You only want jobs in your country and the search result already names a place elsewhere ("US, CA, Santa
    Clara"): no need to download it (Workday sites allow few requests an hour). "3 Locations" isn't judged."""
    text = (locations_text or "").strip()
    if not text or _SEVERAL_PLACES.match(text) or not only_home(query.focus):
        return False
    return location_tier(text, False, query.focus) == TIER_ABROAD


def site_parts(site_url: str) -> tuple[str, str, str]:
    """Return (host, tenant, site) from e.g. https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"""
    ref = parse_ats_url(site_url)
    if not ref.host or not ref.site:
        raise ScraperError(f"Not a Workday site URL: {site_url}")
    return ref.host, ref.board or ref.host.split(".")[0], ref.site


class WorkdayScraper(BaseScraper):
    platform = ATSPlatform.WORKDAY
    rate_key = "workday"

    def _api(self, host: str, tenant: str, site: str) -> str:
        return f"https://{host}/wday/cxs/{tenant}/{site}"

    def _detail(self, host: str, tenant: str, site: str, external_path: str) -> dict[str, Any]:
        return self.get_json(f"{self._api(host, tenant, site)}{external_path}", headers={"Accept": "application/json"})

    def _to_job(self, host: str, tenant: str, site: str, detail: dict[str, Any], summary: dict[str, Any] | None = None) -> ScrapedJob:
        info = detail.get("jobPostingInfo") or {}
        org = (detail.get("hiringOrganization") or {}).get("name") or tenant.replace("-", " ").title()
        external_path = (summary or {}).get("externalPath") or ""
        url = info.get("externalUrl") or f"https://{host}/{site}{external_path}"
        time_type = (info.get("timeType") or "").lower()
        job_type = JobType.PART_TIME if "part" in time_type else (JobType.FULL_TIME if "full" in time_type else None)
        if "intern" in (info.get("title") or "").lower():
            job_type = JobType.INTERNSHIP
        return ScrapedJob(
            company_name=org,
            role_title=info.get("title") or (summary or {}).get("title") or "",
            description=info.get("jobDescription") or "",
            source_url=url,
            application_url=url.rstrip("/") + "/apply" if "/apply" not in url else url,
            source_platform=ATSPlatform.WORKDAY,
            location=info.get("location") or (summary or {}).get("locationsText"),
            is_remote="remote" in (info.get("remoteType") or info.get("location") or "").lower(),
            job_type=job_type,
            posted_date=parse_date(info.get("startDate") or info.get("postedOn") or (summary or {}).get("postedOn")),
            deadline_date=parse_date(info.get("endDate")) if info.get("endDate") else None,
            external_id=info.get("jobReqId") or info.get("id"),
            raw={"host": host, "tenant": tenant, "site": site, "externalPath": external_path},
        ).finalize()

    def search_site(self, site_url: str, query: SearchQuery) -> list[ScrapedJob]:
        host, tenant, site = site_parts(site_url)
        search_terms = query.keywords or [""]
        jobs: list[ScrapedJob] = []
        seen: set[str] = set()
        for term in search_terms:
            offset = 0
            while offset < query.limit:
                resp = self.request(
                    "POST", f"{self._api(host, tenant, site)}/jobs",
                    json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term},
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                )
                if resp.status_code >= 400:
                    raise ScraperError(f"Workday search failed ({resp.status_code}) for {site_url}")
                data = resp.json()
                postings = data.get("jobPostings") or []
                for posting in postings:
                    if query.time_up():
                        return self.filter(jobs, query)
                    path = posting.get("externalPath")
                    if not path or path in seen:
                        continue
                    seen.add(path)
                    if not query.matches_title(posting.get("title") or ""):
                        continue
                    if clearly_abroad(posting.get("locationsText"), query):
                        continue
                    posted = parse_date(posting.get("postedOn"))
                    try:
                        detail = self._detail(host, tenant, site, path)
                        jobs.append(self._to_job(host, tenant, site, detail, posting))
                    except ScraperError as exc:
                        logger.debug("Workday detail failed for %s: %s", path, exc)
                    if posted is None:
                        continue
                if len(postings) < 20:
                    break
                offset += 20
        return self.filter(jobs, query)

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        jobs = self.map_sources(query.sources.get("workday_sites") or [],
                                lambda site_url: self.search_site(site_url, query), query, "Workday site")
        return jobs[: query.limit]

    def fetch_job(self, url: str) -> ScrapedJob | None:
        ref = parse_ats_url(url)
        if not ref.host or not ref.site or not ref.job_id:
            return None
        path = ref.job_id.removesuffix("/apply")
        detail = self._detail(ref.host, ref.board or "", ref.site, path)
        host = urlparse(url).hostname or ref.host
        return self._to_job(host, ref.board or "", ref.site, detail, {"externalPath": path})
