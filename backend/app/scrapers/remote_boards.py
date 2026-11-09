"""Remote job boards with public JSON APIs: Remotive, Jobicy and Himalayas (no login, no key).

Only internships and entry-level roles are kept (junior / graduate / trainee titles, or the board's own
"entry level" tag; never senior, lead or manager roles), and only ones open to candidates where you are: a
posting restricted to the US or Europe is left out, one open worldwide, in your country or in your region
(India: Asia / APAC) stays. Every posting is remote; its location reads "Remote (<who can apply>)", so the
scan's location logic ranks "Remote (India)" with India and "Remote (Worldwide)" with the remote roles.

The boards ask API users not to poll often and to link back to them: responses are cached for a few hours
per URL, and every job links to its page on the board.

* Remotive  ``GET https://remotive.com/api/remote-jobs?category=software-dev`` / ``?search=intern``
  (documented: https://github.com/remotive-com/remote-jobs-api).
* Jobicy    ``GET https://jobicy.com/api/v2/remote-jobs?count=50&tag=intern`` (documented at
  https://jobicy.com/jobs-rss-feed).
* Himalayas ``GET https://himalayas.app/jobs/api/search?q=intern&page=1``, falling back to the plain
  ``/jobs/api?limit=20&offset=0`` listing (documented at https://himalayas.app/api).

Not verified live from the development sandbox (its network blocks job sites); the field names follow the
boards' published API documentation, and each board is read defensively and independently: one failing never
affects the others.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import threading
import time
from typing import Any

from app.models.enums import ATSPlatform, JobType
from app.scrapers.base import BaseScraper, ScrapedJob, ScraperError, SearchQuery, parse_date, parse_salary
from app.services.intern_level import is_internship
from app.services.location_focus import TIER_COUNTRY, TIER_PRIME, location_tier
from app.services.text_utils import normalize_text

logger = logging.getLogger(__name__)

# Regions that include a focus country: a posting "open to APAC" is open to you in India.
REGIONS: dict[str, tuple[str, ...]] = {
    "india": ("asia", "apac", "asia pacific", "asia-pacific", "south asia", "southern asia", "indian subcontinent"),
}
_WORLDWIDE = re.compile(r"\b(worldwide|world ?wide|anywhere|global(ly)?|international|all countries|any country|"
                        r"any location|everywhere|remote)\b")
_FILLER = re.compile(r"\b(in|the|world|only|from|of|all|any|candidates?|locations?|countries)\b|[^a-z]+")
# A title that says entry level; and the boards' own level tags ("Entry", "Entry-level", "Junior").
_ENTRY_TITLE = re.compile(r"\b(junior|jr|entry[- ]level|graduate|new grad|trainee|fresher|apprentice|early career)\b", re.I)
_ENTRY_TAG = re.compile(r"\b(entry|entry[- ]level|junior|intern|internship|graduate|trainee)\b", re.I)
_SENIOR = re.compile(r"\b(senior|sr|staff|principal|lead|head|manager|director|vp|vice president|architect|chief|"
                     r"expert|mid[- ]level|midweight|experienced)\b", re.I)

_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def entry_level(title: str, job_type: JobType | None = None, levels: list[str] | None = None) -> bool:
    """An internship, or an entry-level role by its title or the board's own level tag (never a senior one)."""
    if is_internship(title, job_type):
        return True
    if _SENIOR.search(title or ""):
        return False
    tags = " ".join(levels or [])
    return bool(_ENTRY_TITLE.search(title or "")) or (bool(_ENTRY_TAG.search(tags)) and not _SENIOR.search(tags))


def _worldwide(place: str) -> bool:
    """ "Worldwide", "Anywhere in the world", "Remote": nothing but open-to-all words (not "Remote - US",
    not "Worldwide (UTC-5 to UTC+3)")."""
    return bool(_WORLDWIDE.search(place)) and not _FILLER.sub(" ", _WORLDWIDE.sub(" ", place)).strip()


def open_to_you(restrictions: list[str], query: SearchQuery) -> bool:
    """Someone where you are may apply: no restriction, worldwide, your country or a region that includes it."""
    places = [p for p in (normalize_text(str(r)).strip() for r in restrictions or []) if p]
    if not places:
        return True
    focus = query.focus
    if focus is not None:
        regions = REGIONS.get(focus.country, ())
        return any(_worldwide(p) or location_tier(p, True, focus) in (TIER_PRIME, TIER_COUNTRY)
                   or any(re.search(rf"\b{re.escape(r)}\b", p) for r in regions) for p in places)
    homes = [h for loc in query.locations for h in (normalize_text(x).strip() for x in str(loc).split(","))
             if h and h not in ("remote", "anywhere")]
    if not homes:
        return True  # no idea where you are: every remote role is open to you
    regions = tuple(r for country, rs in REGIONS.items() if country in homes for r in rs)
    return any(_worldwide(p) or any(h in p for h in homes) or any(re.search(rf"\b{re.escape(r)}\b", p) for r in regions)
               for p in places)


def remote_location(restrictions: list[str]) -> str:
    given = ", ".join(dict.fromkeys(r.strip() for r in restrictions if r and r.strip()))
    return f"Remote ({given})"[:255] if given else "Remote"


def split_places(text: str | None) -> list[str]:
    return [p.strip() for p in re.split(r",|;|/|\|", text or "") if p.strip()]


class RemoteBoard(BaseScraper):
    """One board. ``fetch`` returns its intern / entry-level jobs open to you (title and age not yet filtered)."""

    platform = ATSPlatform.CUSTOM
    name = "board"
    label = "Board"
    cache_seconds = 3 * 3600

    def cached_json(self, url: str, params: dict[str, Any]) -> Any:
        key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        with _cache_lock:
            hit = _cache.get(key)
            if hit and time.time() - hit[0] < self.cache_seconds:
                return hit[1]
        response = self.request("GET", url, params=params, headers={"Accept": "application/json"})
        if response.status_code == 404:
            raise ScraperError(f"{self.label}: not found ({url})")
        if response.status_code >= 400:
            raise ScraperError(f"{self.label} API failed (HTTP {response.status_code})")
        try:
            data = response.json()
        except ValueError as exc:
            raise ScraperError(f"{self.label} returned something that isn't JSON") from exc
        with _cache_lock:
            _cache[key] = (time.time(), data)
        return data

    def make_job(self, *, title: str, company: str, url: str, restrictions: list[str], description: str,
                 job_type: JobType | None, posted: Any, external_id: str, extra: dict[str, Any],
                 application_url: str | None = None, logo: str | None = None, salary: tuple[int, int, str] | None = None,
                 deadline: Any = None) -> ScrapedJob:
        return ScrapedJob(
            company_name=company,
            role_title=title,
            description=description or f"{title} at {company} (remote, via {self.label}).",
            source_url=url,
            application_url=application_url or url,
            source_platform=ATSPlatform.CUSTOM,
            location=remote_location(restrictions),
            is_remote=True,
            job_type=job_type,
            salary_min=salary[0] if salary else None,
            salary_max=salary[1] if salary else None,
            salary_currency=salary[2] if salary else None,
            posted_date=parse_date(posted),
            deadline_date=parse_date(deadline),
            external_id=f"{self.name}-{external_id}",
            company_logo_url=logo or None,
            raw={"listing_source": self.name, "board": self.label, "open_to": restrictions, **extra},
        ).finalize()

    def fetch(self, query: SearchQuery) -> list[ScrapedJob]:  # pragma: no cover - each board has its own
        raise NotImplementedError

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        return self.filter(self.fetch(query), query)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _salary(low: Any, high: Any, currency: Any) -> tuple[int, int, str] | None:
    try:
        lo, hi = int(float(low)), int(float(high or low))
    except (TypeError, ValueError):
        return None
    return (lo, hi, str(currency or "USD").upper()[:3]) if 10000 <= lo <= hi else None


# --------------------------------------------------------------------------- Remotive
REMOTIVE_API = "https://remotive.com/api/remote-jobs"
REMOTIVE_TYPES = {"internship": JobType.INTERNSHIP, "full_time": JobType.FULL_TIME, "part_time": JobType.PART_TIME,
                  "contract": JobType.CONTRACT, "freelance": JobType.FREELANCE}
_DATA_ROLE = re.compile(r"\b(ai|ml|machine learning|data|llm|nlp|genai|generative)\b", re.I)


class RemotiveBoard(RemoteBoard):
    rate_key = "remotive"
    name = "remotive"
    label = "Remotive"
    cache_seconds = 6 * 3600  # Remotive asks for a few requests a day at most

    def requests(self, query: SearchQuery) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"search": "intern"}, {"category": "software-dev"}]
        if any(_DATA_ROLE.search(k) for k in query.keywords):
            out.append({"category": "data"})
        if not query.internships_only:
            out.append({"search": "junior"})
        return out

    def to_job(self, item: dict[str, Any], query: SearchQuery) -> ScrapedJob | None:
        title, company, url = _text(item.get("title")), _text(item.get("company_name")), _text(item.get("url"))
        if not title or not company or not url.startswith("http"):
            return None
        restrictions = split_places(item.get("candidate_required_location"))
        job_type = REMOTIVE_TYPES.get(str(item.get("job_type") or "").lower())
        if not entry_level(title, job_type) or not open_to_you(restrictions, query):
            return None
        return self.make_job(
            title=title, company=company, url=url, restrictions=restrictions,
            description=str(item.get("description") or ""), job_type=job_type,
            posted=item.get("publication_date"), external_id=str(item.get("id") or url.rsplit("-", 1)[-1]),
            logo=item.get("company_logo"), salary=parse_salary(str(item.get("salary") or "")),
            extra={"category": item.get("category"), "tags": item.get("tags") or []})

    def fetch(self, query: SearchQuery) -> list[ScrapedJob]:
        jobs: list[ScrapedJob] = []
        for params in self.requests(query):
            if query.time_up():
                break
            data = self.cached_json(REMOTIVE_API, params)
            for item in (data.get("jobs") if isinstance(data, dict) else None) or []:
                job = self.to_job(item, query) if isinstance(item, dict) else None
                if job is not None:
                    jobs.append(job)
        return jobs


# --------------------------------------------------------------------------- Jobicy
JOBICY_API = "https://jobicy.com/api/v2/remote-jobs"


def _types(value: Any) -> list[str]:
    return [str(v).lower() for v in (value if isinstance(value, list) else [value]) if v]


class JobicyBoard(RemoteBoard):
    rate_key = "jobicy"
    name = "jobicy"
    label = "Jobicy"
    cache_seconds = 6 * 3600  # Jobicy's feed changes a few times a day; it asks not to be polled often

    def requests(self, query: SearchQuery) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"count": 50, "tag": "intern"}, {"count": 100}]
        if not query.internships_only:
            out.insert(1, {"count": 50, "tag": "junior"})
        return out

    def to_job(self, item: dict[str, Any], query: SearchQuery) -> ScrapedJob | None:
        title, company, url = _text(item.get("jobTitle")), _text(item.get("companyName")), _text(item.get("url"))
        if not title or not company or not url.startswith("http"):
            return None
        kinds = _types(item.get("jobType"))
        job_type = (JobType.INTERNSHIP if any("intern" in k for k in kinds) else JobType.PART_TIME if any("part" in k for k in kinds)
                    else JobType.CONTRACT if any("contract" in k for k in kinds) else JobType.FULL_TIME if kinds else None)
        levels = [_text(item.get("jobLevel"))]
        restrictions = split_places(item.get("jobGeo"))
        if not entry_level(title, job_type, levels) or not open_to_you(restrictions, query):
            return None
        return self.make_job(
            title=title, company=company, url=url, restrictions=restrictions,
            description=str(item.get("jobDescription") or item.get("jobExcerpt") or ""), job_type=job_type,
            posted=item.get("pubDate"), external_id=str(item.get("id") or url.rstrip("/").rsplit("/", 1)[-1]),
            logo=item.get("companyLogo"),
            salary=_salary(item.get("annualSalaryMin"), item.get("annualSalaryMax"), item.get("salaryCurrency")),
            extra={"level": levels[0] or None, "industry": item.get("jobIndustry")})

    def fetch(self, query: SearchQuery) -> list[ScrapedJob]:
        jobs: list[ScrapedJob] = []
        for params in self.requests(query):
            if query.time_up():
                break
            data = self.cached_json(JOBICY_API, params)
            for item in (data.get("jobs") if isinstance(data, dict) else None) or []:
                job = self.to_job(item, query) if isinstance(item, dict) else None
                if job is not None:
                    jobs.append(job)
        return jobs


# --------------------------------------------------------------------------- Himalayas
HIMALAYAS_SEARCH = "https://himalayas.app/jobs/api/search"
HIMALAYAS_LIST = "https://himalayas.app/jobs/api"
HIMALAYAS_PAGES = 2
HIMALAYAS_TYPES = {"intern": JobType.INTERNSHIP, "internship": JobType.INTERNSHIP, "full time": JobType.FULL_TIME,
                   "full-time": JobType.FULL_TIME, "part time": JobType.PART_TIME, "part-time": JobType.PART_TIME,
                   "contractor": JobType.CONTRACT, "contract": JobType.CONTRACT, "temporary": JobType.CONTRACT}


class HimalayasBoard(RemoteBoard):
    rate_key = "himalayas"
    name = "himalayas"
    label = "Himalayas"
    cache_seconds = 2 * 3600

    def to_job(self, item: dict[str, Any], query: SearchQuery) -> ScrapedJob | None:
        title, company = _text(item.get("title")), _text(item.get("companyName"))
        url = _text(item.get("guid"))
        apply = _text(item.get("applicationLink"))
        if not url.startswith("http"):
            url = apply
        if not title or not company or not url.startswith("http"):
            return None
        job_type = HIMALAYAS_TYPES.get(_text(item.get("employmentType")).lower())
        levels = [_text(s) for s in (item.get("seniority") if isinstance(item.get("seniority"), list) else [item.get("seniority")]) if s]
        restrictions = [r if isinstance(r, str) else _text((r or {}).get("name")) for r in item.get("locationRestrictions") or []]
        restrictions = [r for r in restrictions if r]
        if not entry_level(title, job_type, levels) or not open_to_you(restrictions, query):
            return None
        return self.make_job(
            title=title, company=company, url=url, restrictions=restrictions,
            description=str(item.get("description") or item.get("excerpt") or ""), job_type=job_type,
            posted=item.get("pubDate"), deadline=item.get("expiryDate"),
            external_id=url.rstrip("/").rsplit("/", 1)[-1], application_url=apply if apply.startswith("http") else url,
            logo=item.get("companyLogo"),
            salary=_salary(item.get("minSalary"), item.get("maxSalary"), item.get("currency") or item.get("salaryCurrency")),
            extra={"seniority": levels, "categories": item.get("categories") or []})

    def _items(self, data: Any) -> list[dict[str, Any]]:
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise ScraperError("Unexpected Himalayas response")
        return [i for i in data["jobs"] if isinstance(i, dict)]

    def fetch(self, query: SearchQuery) -> list[ScrapedJob]:
        terms = ["intern"] if query.internships_only else ["intern", "junior", "entry level"]
        items: list[dict[str, Any]] = []
        try:
            for term in terms:
                for page in range(1, HIMALAYAS_PAGES + 1):
                    if query.time_up():
                        break
                    found = self._items(self.cached_json(HIMALAYAS_SEARCH, {"q": term, "page": page}))
                    items.extend(found)
                    if len(found) < 20:
                        break
        except ScraperError as exc:
            if items:
                logger.info("Himalayas search stopped early: %s", exc)
            else:  # no search endpoint (or a new shape): read the latest listings instead
                logger.info("Himalayas search unavailable (%s); reading the latest listings", exc)
                for offset in range(0, 100, 20):
                    if query.time_up():
                        break
                    found = self._items(self.cached_json(HIMALAYAS_LIST, {"limit": 20, "offset": offset}))
                    items.extend(found)
                    if len(found) < 20:
                        break
        jobs = [self.to_job(i, query) for i in items]
        return [j for j in jobs if j is not None]


BOARDS: dict[str, type[RemoteBoard]] = {"remotive": RemotiveBoard, "jobicy": JobicyBoard, "himalayas": HimalayasBoard}


class RemoteBoardsScraper(BaseScraper):
    """All the remote boards at once (``sources.remote_boards`` picks some; default: all of them)."""

    platform = ATSPlatform.CUSTOM
    rate_key = "remote_boards"

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        names = query.sources.get("remote_boards")
        if names is None:
            names = list(BOARDS)
        names = [n for n in names if n in BOARDS]
        quiet = dataclasses.replace(query, progress=None)
        errors: list[str] = []

        def fetch(name: str) -> list[ScrapedJob]:
            try:
                return BOARDS[name]().fetch(quiet)
            except Exception as exc:  # noted, then map_sources logs it and carries on with the other boards
                errors.append(f"{BOARDS[name].label}: {exc}")
                raise

        jobs = self.map_sources(names, fetch, query, "Remote board")
        unique = list({job.source_url: job for job in jobs}.values())
        if not unique and errors and len(errors) == len(names):
            raise ScraperError("; ".join(errors))
        return self.filter(unique, query)
