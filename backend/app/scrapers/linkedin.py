"""LinkedIn Jobs via the public guest endpoints (no login needed for discovery).

Easy Apply submissions use the user's synced ``li_at`` session cookie (see submitters/linkedin_easy_apply.py).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from app.models.enums import ATSPlatform, ExperienceLevel, JobType
from app.scrapers.ats_detect import parse_ats_url
from app.scrapers.base import BaseScraper, RateLimited, ScrapedJob, ScraperError, SearchQuery, interleave, parse_date

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
EMPLOYMENT = {"full-time": JobType.FULL_TIME, "part-time": JobType.PART_TIME, "internship": JobType.INTERNSHIP,
              "contract": JobType.CONTRACT, "temporary": JobType.CONTRACT}
SENIORITY = {"internship": ExperienceLevel.INTERNSHIP, "entry level": ExperienceLevel.ENTRY,
             "associate": ExperienceLevel.ENTRY, "mid-senior level": ExperienceLevel.SENIOR,
             "director": ExperienceLevel.LEAD, "executive": ExperienceLevel.EXECUTIVE}
JOB_TYPE_FILTER = {"full-time": "F", "part-time": "P", "contract": "C", "internship": "I", "freelance": "T"}
MIN_PAGE = 10  # LinkedIn's guest search returns 10 cards a page (25 in older versions): fewer = the last page
MAX_PAGES = 4  # per role x place search


def parse_search_results(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    results = []
    for card in soup.select("div.base-card, div.job-search-card, li > div[data-entity-urn]"):
        urn = card.get("data-entity-urn") or ""
        m = re.search(r"(\d+)$", urn)
        link = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
        href = (link.get("href") if link else "") or ""
        if not m:
            m = re.search(r"/jobs/view/(?:[^/?]*-)?(\d+)", href)
        if not m:
            continue
        title = card.select_one(".base-search-card__title, h3")
        company = card.select_one(".base-search-card__subtitle, h4")
        location = card.select_one(".job-search-card__location")
        posted = card.select_one("time")
        logo = card.select_one("img[data-delayed-url], img.artdeco-entity-image")
        results.append(
            {
                "id": m.group(1),
                "title": title.get_text(strip=True) if title else "",
                "company": company.get_text(strip=True) if company else "",
                "location": location.get_text(strip=True) if location else "",
                "posted": (posted.get("datetime") or posted.get_text(strip=True)) if posted else None,
                "url": href.split("?")[0] if href else f"https://www.linkedin.com/jobs/view/{m.group(1)}/",
                "logo": (logo.get("data-delayed-url") or logo.get("src")) if logo else None,
            }
        )
    return results


def parse_job_detail(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    desc = soup.select_one(".show-more-less-html__markup, .description__text")
    criteria: dict[str, str] = {}
    for item in soup.select(".description__job-criteria-item"):
        header = item.select_one(".description__job-criteria-subheader")
        value = item.select_one(".description__job-criteria-text")
        if header and value:
            criteria[header.get_text(strip=True).lower()] = value.get_text(strip=True).lower()
    apply_url = None
    code = soup.select_one("code#applyUrl")
    if code:
        text = code.decode_contents()
        m = re.search(r'"(https?://[^"]+)"', text)
        if m:
            apply_url = m.group(1).replace("\\u0026", "&")
            m2 = re.search(r"[?&]url=([^&]+)", apply_url)
            if m2 and "linkedin.com" in apply_url:
                from urllib.parse import unquote

                apply_url = unquote(m2.group(1))
    easy_apply = apply_url is None and bool(
        soup.select_one("[data-tracking-control-name*='apply'], button.sign-up-modal__outlet, .apply-button")
    )
    title = soup.select_one(".top-card-layout__title, h2")
    company = soup.select_one(".topcard__org-name-link, .top-card-layout__second-subline a")
    location = soup.select_one(".topcard__flavor--bullet")
    return {
        "description": desc.decode_contents() if desc else "",
        "criteria": criteria,
        "apply_url": apply_url,
        "easy_apply": easy_apply,
        "title": title.get_text(strip=True) if title else None,
        "company": company.get_text(strip=True) if company else None,
        "location": location.get_text(strip=True) if location else None,
    }


class LinkedInScraper(BaseScraper):
    platform = ATSPlatform.LINKEDIN
    rate_key = "linkedin"
    # Optional: only open results this accepts (the top-companies source keeps one company's postings, so it
    # doesn't spend LinkedIn's request budget on everyone else's).
    accept: Callable[[dict[str, Any]], bool] | None = None

    def _build_job(self, summary: dict[str, Any], detail: dict[str, Any]) -> ScrapedJob:
        criteria = detail.get("criteria") or {}
        employment = criteria.get("employment type", "")
        seniority = criteria.get("seniority level", "")
        apply_url = detail.get("apply_url")
        source_url = f"https://www.linkedin.com/jobs/view/{summary['id']}/"
        return ScrapedJob(
            company_name=summary.get("company") or detail.get("company") or "Unknown",
            role_title=summary.get("title") or detail.get("title") or "",
            description=detail.get("description") or "",
            source_url=source_url,
            application_url=apply_url or source_url,
            source_platform=ATSPlatform.LINKEDIN,
            location=summary.get("location") or detail.get("location"),
            job_type=next((v for k, v in EMPLOYMENT.items() if k in employment), None),
            experience_level=next((v for k, v in SENIORITY.items() if k in seniority), None),
            posted_date=parse_date(summary.get("posted")),
            external_id=summary["id"],
            easy_apply=bool(detail.get("easy_apply")),
            company_logo_url=summary.get("logo"),
            raw={"criteria": criteria, "external_apply_url": apply_url,
                 "external_platform": parse_ats_url(apply_url).platform.value if apply_url else None},
        ).finalize()

    def _params(self, keyword: str, location: str, query: SearchQuery) -> str:
        days = max(1, min(query.posted_within_days or 7, 30))
        params = f"keywords={quote_plus(keyword)}&location={quote_plus(location)}&f_TPR=r{days * 86400}"
        if query.remote:
            params += "&f_WT=2"
        types = [JOB_TYPE_FILTER[t] for t in query.job_types if t in JOB_TYPE_FILTER]
        if types:
            params += "&f_JT=" + "%2C".join(types)
        return params

    def _fresh(self, summaries: list[dict[str, Any]], seen: set[str], query: SearchQuery) -> list[dict[str, Any]]:
        """New results worth opening: your roles (and, for the top companies, that company's postings)."""
        out = []
        for summary in summaries:
            if summary["id"] in seen or not query.matches_title(summary["title"]):
                continue
            if self.accept is not None and not self.accept(summary):
                continue
            seen.add(summary["id"])
            out.append(summary)
        return out

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        """Page 1 of every role x place search, then their next pages, until ``query.limit`` jobs pass the filter.

        Each round's results are opened in turn (one from each search), so every role and place gets its share
        of the limit; pages are followed by the number of cards they actually hold (10 today, 25 in the past).
        When LinkedIn stops answering (its request budget, a refusal, the scan's time), the results already
        listed are kept as LinkedIn showed them (the full description is read when you keep one), and the reason
        goes in ``errors``.
        """
        jobs: list[ScrapedJob] = []
        seen: set[str] = set()
        errors: list[str] = []
        keywords = query.search_terms or query.keywords or [""]
        locations = query.locations or ([""] if not query.remote else ["United States"])
        searches = list(dict.fromkeys((k, loc) for k in keywords for loc in locations))
        start = dict.fromkeys(searches, 0)
        active = list(searches)
        total = max(1, len(searches) * MAX_PAGES)
        done = 0
        halted = False  # no more requests to LinkedIn this scan

        def halt(reason: str) -> None:
            nonlocal halted
            if not halted:
                errors.append(reason)
                logger.warning("LinkedIn: %s", reason)
            halted = True

        query.report(0, total)
        for _round in range(MAX_PAGES):
            found: list[list[dict[str, Any]]] = []
            for search in list(active):
                if query.time_up():
                    halt("the scan's time ran out; kept the results already found")
                    break
                try:
                    resp = self.request("GET", f"{SEARCH_URL}?{self._params(*search, query)}&start={start[search]}")
                except RateLimited as exc:
                    halt(str(exc))
                    break
                except ScraperError as exc:
                    errors.append(str(exc))
                    active.remove(search)
                    continue
                finally:
                    done += 1
                    query.report(min(done, total), total)
                if resp.status_code in (401, 403, 999):  # LinkedIn is refusing this client: don't keep asking
                    halt(f"LinkedIn refused the search (HTTP {resp.status_code})")
                    break
                if resp.status_code != 200 or not resp.text.strip():
                    if resp.status_code >= 500:
                        errors.append(f"LinkedIn search failed (HTTP {resp.status_code})")
                    active.remove(search)  # no more results
                    continue
                summaries = parse_search_results(resp.text)
                start[search] += len(summaries)
                if len(summaries) < MIN_PAGE:
                    active.remove(search)  # that was the last page
                found.append(self._fresh(summaries, seen, query))
            pending = interleave(found)
            while pending:
                room = query.limit - len(self.filter(jobs, query))
                if room <= 0:
                    break
                batch, pending = pending[:room], pending[room:]
                if not halted and query.time_up():
                    halt("the scan's time ran out; kept the results already found")
                details: dict[str, dict[str, Any]] = {}
                if not halted:
                    details, limited = self._details(batch, query)
                    if limited:
                        halt(f"{self.rate_key} request budget exhausted or cooling down; kept the results already found")
                for summary in batch:
                    detail = details.get(summary["id"])
                    if detail is None:  # not opened (LinkedIn stopped answering): keep it as listed
                        job = self._build_job(summary, {})
                        job.raw = {**job.raw, "listing_source": "linkedin"}  # the description is read when you keep it
                        jobs.append(job)
                    elif detail.get("description") or detail.get("known"):
                        jobs.append(self._build_job(summary, detail))
            if halted or not active or len(self.filter(jobs, query)) >= query.limit:
                break
        query.report(total, total)
        if not jobs and errors:
            raise ScraperError(errors[0])
        return self.filter(jobs, query)

    def _details(self, summaries: list[dict[str, Any]], query: SearchQuery) -> tuple[dict[str, dict[str, Any]], bool]:
        """Job details for a page of results, three at a time. Postings already saved aren't downloaded again."""
        details: dict[str, dict[str, Any]] = {}
        todo = []
        for summary in summaries:
            if query.is_known(f"https://www.linkedin.com/jobs/view/{summary['id']}/"):
                details[summary["id"]] = {"known": True}
            else:
                todo.append(summary)
        limited = False

        def fetch(job_id: str) -> dict[str, Any]:
            resp = self.request("GET", DETAIL_URL.format(job_id=job_id))
            return parse_job_detail(resp.text) if resp.status_code == 200 else {}

        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="scrape-linkedin") as pool:
            futures = {pool.submit(fetch, s["id"]): s["id"] for s in todo}
            for future in as_completed(futures):
                try:
                    details[futures[future]] = future.result()
                except RateLimited:
                    limited = True
                except ScraperError:
                    details[futures[future]] = {}
        return details, limited

    def fetch_job(self, url: str) -> ScrapedJob | None:
        ref = parse_ats_url(url)
        if not ref.job_id:
            return None
        resp = self.request("GET", DETAIL_URL.format(job_id=ref.job_id))
        if resp.status_code != 200:
            return None
        detail = parse_job_detail(resp.text)
        return self._build_job({"id": ref.job_id, "title": detail.get("title"), "company": detail.get("company"),
                                "location": detail.get("location")}, detail)
