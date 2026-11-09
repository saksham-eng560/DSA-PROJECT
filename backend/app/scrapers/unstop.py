"""Unstop (unstop.com, formerly Dare2Compete): internships and jobs for Indian students, via the public
opportunity-search JSON its own website loads (no login needed to search).

``GET https://unstop.com/api/public/opportunity/search-result?opportunity=internships&searchTerm=...&page=N``
returns a Laravel-style page: ``{"data": {"data": [...], "current_page": 1, "last_page": 9, ...}}``. Each
opportunity has an ``id``, ``title``, ``organisation.name``, ``public_url`` / ``seo_url``, a ``region``
("online" = work from home), ``end_date`` (the application deadline) and a ``jobDetail`` block (locations,
stipend). Applying needs your own Unstop login, so these jobs are marked "apply on Unstop".

Not verified live from the development sandbox (its network blocks job sites): the parameter names and fields
follow what Unstop's web app requests and receives, and the parser reads every field defensively (a missing
one is simply left empty).
"""

from __future__ import annotations

import html
import logging
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

from app.models.enums import ATSPlatform, ExperienceLevel, JobType
from app.scrapers.base import BaseScraper, Blocked, ScrapedJob, ScraperError, SearchQuery, parse_date, role_search_terms
from app.services.location_focus import is_indian_location

logger = logging.getLogger(__name__)

BASE = "https://unstop.com"
API = f"{BASE}/api/public/opportunity/search-result"
SITE = "Unstop"
PAGE_SIZE = 18
MAX_PAGES = 3
MAX_TERMS = 8  # every target role of the AI + Python preset (7)
_WFH = re.compile(r"\b(work from home|wfh|remote|online)\b", re.I)
_CURRENCY = {"fa-rupee": "₹", "inr": "₹", "rupee": "₹", "fa-dollar": "$", "usd": "$"}


def _names(value: Any, *keys: str) -> list[str]:
    """Strings from a list of strings / dicts (``[{"city": "Delhi"}, "Noida"]``) or one string."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            item = next((item.get(k) for k in keys if item.get(k)), None)
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if text and text not in out:
            out.append(text)
    return out


def opportunity_url(item: dict[str, Any]) -> str | None:
    for key in ("seo_url", "public_url"):
        value = str(item.get(key) or "").strip()
        if value:
            return urljoin(f"{BASE}/", value).split("?")[0]
    return None


def _amount(value: Any) -> int | None:
    try:
        number = int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


_PER = {"monthly": "month", "month": "month", "weekly": "week", "week": "week", "yearly": "year", "annually": "year",
        "year": "year", "hourly": "hour", "lumpsum": "lump sum", "lump sum": "lump sum"}


def stipend(detail: dict[str, Any]) -> str | None:
    """ "₹10,000 - ₹15,000 /month", "Unpaid", or None when Unstop doesn't show it."""
    if str(detail.get("paid_unpaid") or "").lower() == "unpaid":
        return "Unpaid"
    if str(detail.get("show_salary", 1)).lower() in ("0", "false"):
        return None
    low, high = _amount(detail.get("min_salary")), _amount(detail.get("max_salary"))
    if not low and not high:
        return None
    sign = _CURRENCY.get(str(detail.get("currency") or "").lower(), "₹")
    amount = " - ".join(f"{sign}{n:,}" for n in dict.fromkeys(n for n in (low, high) if n))
    per = _PER.get(str(detail.get("pay_in") or "").strip().lower())
    return f"{amount} /{per}" if per else amount


def to_job(item: dict[str, Any], internship: bool = True) -> ScrapedJob | None:
    title = re.sub(r"\s+", " ", html.unescape(str(item.get("title") or ""))).strip()  # "AI &amp; ML Internship"
    org = item.get("organisation") if isinstance(item.get("organisation"), dict) else {}
    company = re.sub(r"\s+", " ", html.unescape(str(org.get("name") or item.get("organisation_name") or ""))).strip()
    url = opportunity_url(item)
    if not title or not company or not url or not item.get("id"):
        return None
    deadline = parse_date(item.get("end_date") or item.get("regn_end_date"))
    if deadline and deadline < datetime.now(UTC).date():
        return None  # applications closed
    detail = item.get("jobDetail") if isinstance(item.get("jobDetail"), dict) else {}
    cities = _names(detail.get("locations"), "city", "name", "location") + _names(item.get("locations"), "city", "name")
    address = item.get("address_with_country_logo")
    if isinstance(address, dict) and address.get("city"):
        cities += [str(address["city"])]
    cities = list(dict.fromkeys(c for c in cities if c))
    wfh = str(item.get("region") or "").lower() == "online" or any(_WFH.search(c) for c in cities)
    places = [("Work from home" if _WFH.search(c) else c) for c in cities] or (["Work from home"] if wfh else [])
    location = ", ".join(dict.fromkeys(places)) or None
    if location and (wfh or is_indian_location(location)) and "india" not in location.lower():
        location += ", India"  # Unstop lists opportunities for students in India
    skills = _names(item.get("required_skills"), "skill", "name") or _names(item.get("skills"), "skill", "name")
    pay = stipend(detail)
    kind = "internship" if internship else "job"
    lines = [f"{title} {kind} at {company} (via Unstop)."]
    if location:
        lines.append(f"Location: {location}.")
    if pay:
        lines.append(f"Stipend: {pay}." if internship else f"Salary: {pay}.")
    if skills:
        lines.append(f"Skills: {', '.join(skills)}.")
    if deadline:
        lines.append(f"Apply by {deadline.isoformat()}.")
    body = str(item.get("details") or item.get("description") or "").strip()
    logo = org.get("logoUrl2") or org.get("logoUrl") or item.get("logoUrl2") or None
    return ScrapedJob(
        company_name=company,
        role_title=title if not internship or re.search(r"\bintern", title, re.I) else f"{title} Intern",
        description=" ".join(lines) + (f"\n\n{body}" if body else ""),
        source_url=url,
        application_url=url,
        source_platform=ATSPlatform.CUSTOM,
        location=location[:255] if location else None,
        is_remote=wfh,
        job_type=JobType.INTERNSHIP if internship else None,
        experience_level=ExperienceLevel.INTERNSHIP if internship else None,
        posted_date=parse_date(item.get("approved_date") or item.get("start_date") or item.get("updated_at")),
        deadline_date=deadline,
        external_id=f"unstop-{item['id']}",
        company_logo_url=logo,
        raw={"listing_source": "unstop", "apply_on_site": SITE, "stipend": pay, "skills": skills,
             "region": item.get("region")},
    ).finalize()


class UnstopScraper(BaseScraper):
    platform = ATSPlatform.CUSTOM
    rate_key = "unstop"

    def page(self, kind: str, term: str, page: int) -> tuple[list[ScrapedJob], bool]:
        params = {"opportunity": kind, "page": page, "per_page": PAGE_SIZE, "oppstatus": "open", "searchTerm": term}
        response = self.request("GET", API, params=params, headers={"Accept": "application/json", "Referer": f"{BASE}/{kind}"})
        if response.status_code in (403, 503) and "<html" in (response.text or "")[:2000].lower():
            raise Blocked(f"Unstop refused the automated search (HTTP {response.status_code}, a bot check)")
        if response.status_code >= 400:
            raise ScraperError(f"Unstop search failed (HTTP {response.status_code})")
        try:
            data = response.json()
        except ValueError as exc:
            raise ScraperError("Unstop returned something that isn't search results") from exc
        block = data.get("data") if isinstance(data, dict) else None
        if isinstance(block, dict):
            items, last = block.get("data") or [], block.get("last_page")
        elif isinstance(block, list):
            items, last = block, None
        else:
            raise ScraperError("Unexpected Unstop response")
        jobs = [j for j in (to_job(i, kind == "internships") for i in items if isinstance(i, dict)) if j is not None]
        try:
            more = page < int(last) if last is not None else len(items) >= PAGE_SIZE
        except (TypeError, ValueError):
            more = len(items) >= PAGE_SIZE
        return jobs, more and bool(items)

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        # Unstop's own internship filter does the "intern" part: search the role itself ("Python Developer").
        terms = role_search_terms(query, intern=False, limit=MAX_TERMS) or [""]
        kinds = ["internships"] if query.internships_only else ["internships", "jobs"]
        searches = [(kind, term) for term in terms for kind in kinds]
        jobs = self.search_pages(searches, lambda s, page: self.page(s[0], s[1], page), query,
                                 max_pages=MAX_PAGES, label="Unstop search")
        return self.filter(jobs, query)
