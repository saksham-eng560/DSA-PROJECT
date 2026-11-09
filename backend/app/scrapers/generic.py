"""Generic career-page scraper: schema.org JobPosting (JSON-LD) + delegation to known ATS boards."""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.models.enums import ATSPlatform, JobType
from app.scrapers.ats_detect import detect_ats_platform, parse_ats_url
from app.scrapers.base import BaseScraper, ScrapedJob, ScraperError, SearchQuery, parse_date

logger = logging.getLogger(__name__)

EMPLOYMENT = {"FULL_TIME": JobType.FULL_TIME, "PART_TIME": JobType.PART_TIME, "INTERN": JobType.INTERNSHIP,
              "CONTRACTOR": JobType.CONTRACT, "TEMPORARY": JobType.CONTRACT}


def _iter_jsonld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if node.get("@graph"):
                    stack.extend(node["@graph"])
                types = node.get("@type")
                types = types if isinstance(types, list) else [types]
                if "JobPosting" in types:
                    items.append(node)
                elif "ItemList" in types:
                    stack.extend(el.get("item", el) for el in node.get("itemListElement") or [] if isinstance(el, dict))
            elif isinstance(node, list):
                stack.extend(node)
    return items


def _location(posting: dict[str, Any]) -> tuple[str | None, bool]:
    remote = str(posting.get("jobLocationType") or "").upper() == "TELECOMMUTE"
    locs = posting.get("jobLocation") or []
    locs = locs if isinstance(locs, list) else [locs]
    names = []
    for loc in locs:
        addr = (loc or {}).get("address") or {}
        if isinstance(addr, str):
            names.append(addr)
            continue
        parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
        parts = [p if isinstance(p, str) else (p or {}).get("name") for p in parts]
        name = ", ".join(p for p in parts if p)
        if name:
            names.append(name)
    return ("; ".join(names) or ("Remote" if remote else None)), remote


_HOST_PREFIXES = {"www", "careers", "career", "jobs", "job", "apply", "boards", "hire", "recruiting", "talent", "work"}


def company_from_host(url: str) -> str:
    """``careers.acme-robotics.com`` -> ``Acme Robotics`` (IP addresses / localhost are returned as-is)."""
    host = (urlparse(url).hostname or "").lower()
    if not host or host == "localhost" or re.fullmatch(r"[\d.]+|[0-9a-f]*:[0-9a-f:]*", host):
        return host or "Unknown"
    labels = [x for x in host.split(".") if x not in _HOST_PREFIXES]
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in {"co", "com", "org", "net", "ac", "gov", "edu"}:
        labels = labels[:-1]  # acme.co.uk
    name = labels[-2] if len(labels) >= 2 else (labels[0] if labels else host)
    return re.sub(r"[-_]+", " ", name).title()


def company_from_page(soup: BeautifulSoup, url: str) -> str:
    """Company name from page metadata (og:site_name / application-name), else from the host name."""
    for attrs in ({"property": "og:site_name"}, {"name": "application-name"}):
        tag = soup.find("meta", attrs=attrs)
        content = (tag.get("content") or "").strip() if tag else ""
        if content:
            return content
    return company_from_host(url)


def jobposting_to_job(posting: dict[str, Any], page_url: str, fallback_company: str | None = None) -> ScrapedJob | None:
    title = posting.get("title") or posting.get("name")
    if not title:
        return None
    org = posting.get("hiringOrganization") or {}
    org = org[0] if isinstance(org, list) and org else org
    company = org.get("name") if isinstance(org, dict) else str(org or "")
    location, remote = _location(posting)
    salary = posting.get("baseSalary") or {}
    value = salary.get("value") if isinstance(salary, dict) else None
    smin = smax = None
    if isinstance(value, dict) and str(value.get("unitText", "YEAR")).upper() in ("YEAR", "ANNUAL"):
        smin, smax = value.get("minValue") or value.get("value"), value.get("maxValue") or value.get("value")
    emp = posting.get("employmentType")
    emp = emp[0] if isinstance(emp, list) and emp else emp
    url = posting.get("url") or page_url
    return ScrapedJob(
        company_name=company or fallback_company or company_from_host(page_url),
        role_title=title,
        description=posting.get("description") or "",
        source_url=url,
        application_url=posting.get("directApplyUrl") or url,
        source_platform=detect_ats_platform(url) if detect_ats_platform(url) != ATSPlatform.UNKNOWN else ATSPlatform.CUSTOM,
        location=location,
        is_remote=remote,
        job_type=EMPLOYMENT.get(str(emp or "").upper()),
        salary_min=int(float(smin)) if smin else None,
        salary_max=int(float(smax)) if smax else None,
        salary_currency=salary.get("currency") if isinstance(salary, dict) else None,
        posted_date=parse_date(posting.get("datePosted")),
        deadline_date=parse_date(posting.get("validThrough")),
        external_id=str((posting.get("identifier") or {}).get("value") or "") or None if isinstance(posting.get("identifier"), dict) else None,
        company_logo_url=org.get("logo") if isinstance(org, dict) and isinstance(org.get("logo"), str) else None,
        company_domain=urlparse(org.get("sameAs") or page_url).hostname if isinstance(org, dict) else None,
    ).finalize()


def find_ats_boards(html: str, base_url: str) -> dict[str, set[str]]:
    """Find embedded / linked ATS boards on a careers page."""
    boards: dict[str, set[str]] = {"greenhouse_boards": set(), "lever_companies": set(), "ashby_boards": set(), "workday_sites": set()}
    for m in re.finditer(r"boards(?:-api)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=|v1/boards/)?([a-z0-9_-]+)", html, re.I):
        if m.group(1).lower() not in ("embed", "v1", "jobs"):
            boards["greenhouse_boards"].add(m.group(1))
    for m in re.finditer(r"job-boards\.greenhouse\.io/([a-z0-9_-]+)", html, re.I):
        boards["greenhouse_boards"].add(m.group(1))
    for m in re.finditer(r"jobs\.lever\.co/([a-z0-9_-]+)", html, re.I):
        boards["lever_companies"].add(m.group(1))
    for m in re.finditer(r"jobs\.ashbyhq\.com/([a-z0-9_.-]+)", html, re.I):
        boards["ashby_boards"].add(m.group(1))
    for m in re.finditer(r"https://[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?[A-Za-z0-9_-]+", html):
        boards["workday_sites"].add(m.group(0))
    return boards


class GenericScraper(BaseScraper):
    platform = ATSPlatform.CUSTOM
    rate_key = "generic"

    def fetch_html(self, url: str) -> str:
        resp = self.request("GET", url)
        if resp.status_code >= 400:
            raise ScraperError(f"HTTP {resp.status_code} for {url}")
        return resp.text

    def scrape_page(self, url: str, query: SearchQuery, depth: int = 0) -> list[ScrapedJob]:
        html = self.fetch_html(url)
        soup = BeautifulSoup(html, "lxml")
        site = company_from_page(soup, url)
        jobs = [j for j in (jobposting_to_job(p, url, site) for p in _iter_jsonld(soup)) if j]
        if jobs or depth > 0:
            return jobs
        # Delegate to ATS boards found on the page
        boards = find_ats_boards(html, url)
        if any(boards.values()):
            from app.scrapers import get_scraper

            sub = SearchQuery(**{**query.__dict__, "sources": {k: sorted(v) for k, v in boards.items()}})
            for key, platform in (("greenhouse_boards", "greenhouse"), ("lever_companies", "lever"),
                                  ("ashby_boards", "ashby"), ("workday_sites", "workday")):
                if boards[key]:
                    try:
                        jobs.extend(get_scraper(platform).search(sub))
                    except ScraperError as exc:
                        logger.warning("Delegated %s scrape failed: %s", platform, exc)
            return jobs
        # Follow job-looking links (limited) and read their JSON-LD
        links: list[str] = []
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"])
            text = a.get_text(" ", strip=True)
            if re.search(r"/(jobs?|careers?|positions?|openings?)/[^/?#]+", href) and query.matches_title(text or href):
                links.append(href.split("#")[0])
        for link in list(dict.fromkeys(links))[:15]:
            try:
                jobs.extend(self.scrape_page(link, query, depth=1))
            except ScraperError:
                continue
        return jobs

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        jobs = self.map_sources(query.sources.get("career_pages") or [],
                                lambda url: self.scrape_page(url, query), query, "Career page")
        return self.filter(jobs, query)

    def fetch_job(self, url: str) -> ScrapedJob | None:
        html = self.fetch_html(url)
        soup = BeautifulSoup(html, "lxml")
        for posting in _iter_jsonld(soup):
            job = jobposting_to_job(posting, url, company_from_page(soup, url))
            if job:
                job.source_url = url
                return job
        # Last resort: title + main text of the page
        title = (soup.find("h1") or soup.find("title"))
        main = soup.find("main") or soup.body
        if not title or not main:
            return None
        ref = parse_ats_url(url)
        return ScrapedJob(
            company_name=company_from_page(soup, url),
            role_title=title.get_text(strip=True),
            description=main.get_text("\n", strip=True)[:20000],
            source_url=url,
            application_url=url,
            source_platform=ref.platform if ref.platform != ATSPlatform.UNKNOWN else ATSPlatform.CUSTOM,
        ).finalize()
