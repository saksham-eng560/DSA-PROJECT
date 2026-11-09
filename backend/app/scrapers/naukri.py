"""Naukri.com (India's largest job site) through the JSON search API its own website calls.

Searching needs no login: ``GET https://www.naukri.com/jobapi/v3/search`` with the ``appid: 109`` /
``systemid: Naukri`` headers its web app sends, a keyword, a location and ``jobAge`` (days). Naukri has no
internship filter, so internship searches put "intern" in the keyword and ask for postings open to 0 years of
experience; every result still has to have an internship title (``SearchQuery.internships_only``).

Applying needs your own Naukri login, so these jobs are marked "apply on Naukri" (like Internshala): the agent
prepares your resume and answers, you apply there and click "I Applied".

Not verified live from the development sandbox (its network blocks job sites): the request parameters and the
response fields (``jobDetails[].title / companyName / jobId / jdURL / placeholders / tagsAndSkills /
jobDescription / createdDate / footerPlaceholderLabel``, ``noOfJobs``) follow what Naukri's web app sends and
receives. Naukri increasingly asks scripted clients for a captcha or a signed ``nkparam`` header; when it does,
this source fails with a clear message (``Blocked``) instead of crashing or silently returning nothing.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.models.enums import ATSPlatform
from app.scrapers.base import BaseScraper, Blocked, ScrapedJob, ScraperError, SearchQuery, parse_date, role_search_terms
from app.services.location_focus import COUNTRY_PLACES, is_indian_location
from app.services.text_utils import normalize_text

logger = logging.getLogger(__name__)

BASE = "https://www.naukri.com"
API = f"{BASE}/jobapi/v3/search"
SITE = "Naukri"
PAGE_SIZE = 20
MAX_PAGES = 3  # per search (role x place); most searches stop after page 1 once enough jobs are found
MAX_TERMS = 8  # every target role of the AI + Python preset (7)
JOB_AGES = (1, 3, 7, 15, 30)  # Naukri's "freshness" filter
HEADERS = {
    "appid": "109",
    "systemid": "Naukri",
    "clientid": "d3skt0p",
    "gid": "LOCATION,INDUSTRY,EDUCATION,FAREA_ROLE",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Referer": f"{BASE}/",
}
# How Naukri names a place when that differs from the usual name ("Delhi / NCR" covers Delhi, Gurugram, Noida...).
PLACES = {"delhi": "delhi / ncr", "new delhi": "delhi / ncr", "delhi ncr": "delhi / ncr", "ncr": "delhi / ncr",
          "gurgaon": "gurugram", "bangalore": "bengaluru"}
_REMOTE = re.compile(r"\b(remote|work from home|wfh)\b", re.I)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize_text(text)).strip("-")


def job_age(days: int | None) -> int | None:
    """The smallest freshness filter Naukri offers that covers ``days`` (None: no filter)."""
    if not days or days <= 0:
        return None
    return next((age for age in JOB_AGES if age >= days), JOB_AGES[-1])


def search_places(query: SearchQuery) -> list[str]:
    """Where to search: your prime city first, then your other target cities, then "" (all of India)."""
    focus = query.focus
    country = focus.country if focus else None
    names = [*(focus.prime_terms[:1] if focus else ()), *(query.locations or [])]
    out: list[str] = []
    for name in names:
        city = normalize_text(str(name).split(",")[0])
        if not city or city in ("remote", "anywhere", "work from home") or city == country or city in COUNTRY_PLACES:
            place = ""  # the whole country: Naukri is Indian, so no location means all of India
        else:
            place = PLACES.get(city, city)
        if place not in out:
            out.append(place)
    return out[:3] or [""]


def check_response(response: Any) -> dict[str, Any]:
    """The search results, or ``Blocked`` / ``ScraperError`` with a reason the scan log can show."""
    head = (response.text or "")[:4000].lower()
    walled = "captcha" in head or "access denied" in head or "nkparam" in head
    if response.status_code in (403, 406) or (response.status_code != 200 and walled):
        raise Blocked(f"Naukri refused the automated search (HTTP {response.status_code}, it asks for a captcha / "
                      "bot check). Search naukri.com yourself, or try again later.")
    if response.status_code >= 400:
        raise ScraperError(f"Naukri search failed (HTTP {response.status_code})")
    try:
        data = response.json()
    except ValueError:
        if walled or "<html" in head:
            raise Blocked("Naukri answered with a web page (a captcha / bot check) instead of search results. "
                          "Search naukri.com yourself, or try again later.") from None
        raise ScraperError("Naukri returned something that isn't search results") from None
    if not isinstance(data, dict):
        raise ScraperError("Unexpected Naukri response")
    if "jobDetails" not in data:
        message = str(data.get("message") or data.get("error") or "")
        if "captcha" in message.lower() or "denied" in message.lower():
            raise Blocked(f"Naukri refused the automated search ({message}). Search naukri.com yourself, or try again later.")
        if data.get("noOfJobs") in (0, "0") or not message:
            return {"jobDetails": [], "noOfJobs": 0}
        raise ScraperError(f"Naukri search failed: {message[:200]}")
    return data


def job_url(item: dict[str, Any]) -> str | None:
    href = str(item.get("jdURL") or "").strip()
    if not href:
        return None
    parts = urlsplit(urljoin(f"{BASE}/", href))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))  # drop ?src=...&sid=... tracking


def place_label(label: str) -> str | None:
    """Naukri's location text, with ", India" when it names an Indian place or remote work (Naukri lists jobs in
    India; a "Remote" job there is one you can do from India)."""
    label = re.sub(r"\s+", " ", label or "").strip(" ,")
    if not label:
        return None
    if "india" in label.lower():
        return label
    if is_indian_location(label) or _REMOTE.search(label):
        return f"{label}, India"
    return label  # abroad (Naukri lists a few Gulf jobs): the location logic judges it


def to_job(item: dict[str, Any]) -> ScrapedJob | None:
    title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
    company = re.sub(r"\s+", " ", str(item.get("companyName") or "")).strip()
    job_id = str(item.get("jobId") or "").strip()
    url = job_url(item)
    if not title or not company or not job_id or not url:
        return None
    details = {str(p.get("type")): str(p.get("label") or "").strip()
               for p in item.get("placeholders") or [] if isinstance(p, dict) and p.get("type")}
    where = details.get("location", "")
    experience = details.get("experience") or None
    salary = details.get("salary") or None
    skills = [s.strip() for s in str(item.get("tagsAndSkills") or "").split(",") if s.strip()]
    lines = [f"{title} at {company} (via Naukri)."]
    if where:
        lines.append(f"Location: {where}.")
    if experience:
        lines.append(f"Experience: {experience}.")
    if salary and "not disclosed" not in salary.lower():
        lines.append(f"Salary: {salary}.")
    if skills:
        lines.append(f"Skills: {', '.join(skills)}.")
    snippet = str(item.get("jobDescription") or "").strip()
    return ScrapedJob(
        company_name=company,
        role_title=title,
        description=" ".join(lines) + (f"\n\n{snippet}" if snippet else ""),
        source_url=url,
        application_url=url,
        source_platform=ATSPlatform.CUSTOM,
        location=place_label(where),
        is_remote=bool(_REMOTE.search(where)),
        posted_date=parse_date(item.get("createdDate")) or parse_date(item.get("footerPlaceholderLabel")),
        external_id=f"naukri-{job_id}",
        company_logo_url=item.get("logoPathV3") or item.get("logoPath") or None,
        raw={"listing_source": "naukri", "apply_on_site": SITE, "experience": experience,
             "salary": salary if salary and "not disclosed" not in salary.lower() else None,
             "skills": skills, "naukri_company_id": item.get("companyId")},
    ).finalize()


class NaukriScraper(BaseScraper):
    platform = ATSPlatform.CUSTOM
    rate_key = "naukri"

    def params(self, term: str, place: str, page: int, query: SearchQuery) -> dict[str, Any]:
        seo = f"{slug(term)}-jobs" + (f"-in-{slug(place)}" if place else "")
        params: dict[str, Any] = {"noOfResults": PAGE_SIZE, "urlType": "search_by_key_loc" if place else "search_by_keyword",
                                  "searchType": "adv", "keyword": term, "k": term, "pageNo": page, "seoKey": seo,
                                  "src": "jobsearchDesk", "latLong": ""}
        if place:
            params.update(location=place, l=place)
        age = job_age(query.posted_within_days)
        if age:
            params["jobAge"] = age
        if query.internships_only:
            params["experience"] = 0  # postings open to someone with no work experience yet
        if query.remote:
            params["wfhType"] = 2  # Naukri's "remote" work-mode filter
        return params

    def page(self, term: str, place: str, page: int, query: SearchQuery) -> tuple[list[ScrapedJob], bool]:
        response = self.request("GET", API, params=self.params(term, place, page, query), headers=HEADERS)
        data = check_response(response)
        items = [i for i in data.get("jobDetails") or [] if isinstance(i, dict)]
        jobs = [job for job in (to_job(i) for i in items) if job is not None]
        try:
            total = int(data.get("noOfJobs") or 0)
        except (TypeError, ValueError):
            total = 0
        more = len(items) >= PAGE_SIZE and (not total or page * PAGE_SIZE < total)
        return jobs, more

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        terms = role_search_terms(query, intern=True if query.internships_only else None, limit=MAX_TERMS)
        if not terms:
            terms = ["intern"] if query.internships_only else ["software developer"]
        searches = [(term, place) for term in terms for place in search_places(query)]
        jobs = self.search_pages(searches, lambda s, page: self.page(s[0], s[1], page, query), query,
                                 max_pages=MAX_PAGES, label="Naukri search")
        return self.filter(jobs, query)
