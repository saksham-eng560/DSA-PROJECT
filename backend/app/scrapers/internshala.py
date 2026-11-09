"""Internshala (internshala.com): India's largest internship board.

Search pages are plain HTML (one card per internship). The scraper builds category + city URLs from
your target roles and location focus (e.g. ``/internships/software-development-internship-in-delhi/``),
and you can add any Internshala search URL you like under ``sources.internshala_urls``.

Applying on Internshala needs your own Internshala login, so these jobs are marked "apply on
Internshala": the agent prepares your resume and answers, you apply there and click "I Applied". With the
opt-in Internshala bot (Settings › Integrations, login synced by the browser extension) the agent fills the
application itself and sends it when you click Submit (``app/submitters/internshala_apply.py``).

Internshala changes its markup from time to time; the parser tries the current and older class names.
Check it with ``python scripts/test_scraper.py internshala``.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from app.automation.browser import BrowserUnavailable
from app.models.enums import ATSPlatform, ExperienceLevel, JobType
from app.scrapers.base import RateLimited, ScrapedJob, ScraperError, SearchQuery, interleave, parse_date
from app.scrapers.browser_scraper import BLOCK_MARKERS, BrowserScraper
from app.services.text_utils import normalize_text

logger = logging.getLogger(__name__)

BASE = "https://internshala.com"
SITE = "Internshala"

# Target-role keywords -> Internshala categories (URL slugs).
CATEGORY_RULES: list[tuple[str, list[str]]] = [
    (r"full[\s-]?stack", ["full-stack-development"]),
    (r"back[\s-]?end", ["backend-development"]),
    (r"front[\s-]?end|react|angular|ui developer", ["front-end-development"]),
    (r"android|ios|mobile|flutter", ["android-app-development", "mobile-app-development"]),
    (r"machine learning|\bml\b|deep learning|\bai\b|artificial intelligence|llm|nlp", ["machine-learning", "artificial-intelligence-ai"]),
    (r"data scien|data analy|analytics|business analy", ["data-science", "data-analytics"]),
    (r"data engineer", ["data-science"]),
    (r"python", ["python-django"]),
    (r"java\b", ["java"]),
    (r"web", ["web-development"]),
    (r"cloud|devops|sre", ["cloud-computing"]),
    (r"cyber|security", ["cyber-security"]),
    (r"software|developer|engineer|swe|sde|programm|coding", ["software-development", "computer-science", "web-development"]),
    (r"product manag", ["product-management"]),
    (r"design", ["ui-ux-design"]),
    (r"market", ["marketing"]),
    (r"finance", ["finance"]),
]
DEFAULT_CATEGORIES = ["software-development", "computer-science"]
MAX_PAGES = 18  # first search pages per scan (each lists ~40 internships)
MAX_MORE_PAGES = 6  # second pages of the fullest searches, only when the first pages found too few internships
MAX_CATEGORIES = 6
MAX_CITIES = 3
FULL_PAGE = 20  # a page with this many cards probably has a next page


def categories_for(roles: list[str]) -> list[str]:
    out: list[str] = []
    for role in roles or []:
        text = normalize_text(role)
        for pattern, cats in CATEGORY_RULES:
            if re.search(pattern, text):
                out.extend(cats)
                break
    return list(dict.fromkeys(out)) or list(DEFAULT_CATEGORIES)


def city_slug(city: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalize_text(city)).strip("-")
    return {"new-delhi": "delhi", "delhi-ncr": "delhi", "gurugram": "gurgaon", "bengaluru": "bangalore"}.get(slug, slug)


def search_urls(query: SearchQuery) -> list[str]:
    """Your own URLs first, then every category of your roles in your prime city, then all of India, then work
    from home, then your other prime cities (Gurugram, Noida...): each category gets its turn before any one gets
    a second search, so a list of seven roles isn't cut down to its first two categories."""
    urls = [u.strip() for u in query.sources.get("internshala_urls") or [] if str(u).strip().startswith("http")]
    cities: list[str] = []
    focus = query.focus
    for name in [*(focus.prime_terms if focus else ()), *(query.locations or [])]:
        slug = city_slug(str(name).split(",")[0])
        if slug and slug not in ("india", "remote") and slug not in cities:
            cities.append(slug)
    cities = cities[:MAX_CITIES] or ["delhi"]
    categories = categories_for(query.keywords)[:MAX_CATEGORIES]
    urls.extend(f"{BASE}/internships/{c}-internship-in-{cities[0]}/" for c in categories)
    urls.extend(f"{BASE}/internships/{c}-internship/" for c in categories)
    urls.extend(f"{BASE}/internships/work-from-home-{c}-internships/" for c in categories)
    urls.extend(f"{BASE}/internships/{c}-internship-in-{city}/" for city in cities[1:] for c in categories[:3])
    return list(dict.fromkeys(urls))[:MAX_PAGES]


def next_page(url: str) -> str:
    """The results page after ``url``: ``.../python-django-internship/page-2/`` (then ``page-3/``...).
    A query string (``?ref=...``) stays at the end, where Internshala reads it."""
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    current = re.fullmatch(r"page-(\d+)", segments[-1]) if segments else None
    if current:
        segments[-1] = f"page-{int(current.group(1)) + 1}"
    else:
        segments.append("page-2")
    return urlunsplit(parts._replace(path="/" + "/".join(segments) + "/"))


def _text(node: Tag | None) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() if node else ""


def _first(card: Tag, *selectors: str) -> Tag | None:
    for selector in selectors:
        node = card.select_one(selector)
        if node is not None and _text(node):
            return node
    return None


def parse_cards(html: str) -> list[dict[str, Any]]:
    """Parse an Internshala search page into plain dicts."""
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in soup.select("div.individual_internship, div.internship_meta"):
        link = _first(card, "a.job-title-href", "h3.job-internship-name a", ".profile a", "h3 a", "a[href*='/internship/detail/']")
        href = card.get("data-href") or (link.get("href") if link else None)
        title = _text(_first(card, "h3.job-internship-name", ".job-internship-name", ".profile", "h3") or link)
        company = _text(_first(card, "p.company-name", ".company-name", ".company_name", ".heading_6", ".company a"))
        if not href or not title or not company or "/internship/detail/" not in str(href):
            continue
        url = urljoin(BASE, str(href).split("?")[0])
        if url in seen:
            continue
        seen.add(url)
        locations = [_text(a) for a in card.select(".locations a, .location_link, #location_names a, .locations span")]
        locations = list(dict.fromkeys(loc for loc in locations if loc))
        stipend = _text(_first(card, "span.stipend", ".stipend"))
        items = [_text(i) for i in card.select(".row-1-item, .other_detail_item, .item_body")]
        # "3 Months" (the item with the calendar icon), never the stipend: "₹ 8,000 - 10,000 /month"
        calendar = card.select_one(".row-1-item:has(i[class*='calendar'])")
        duration = _text(calendar) if calendar is not None else next(
            (i for i in items if re.search(r"\d\s*(week|month)s?\b", i, re.I) and not re.search(r"₹|/\s*(week|month)", i, re.I)), "")
        status = _text(_first(card, ".status-success", ".status-info", ".status-inactive", ".status", ".posted_by_container"))
        immediate = bool(re.search(r"start(s)? immediately", card.get_text(" ", strip=True), re.I))
        internship_id = card.get("internshipid") or re.sub(r"\D", "", str(card.get("id") or "")) or None
        out.append({"id": internship_id, "url": url, "title": title, "company": company, "locations": locations,
                    "stipend": stipend, "duration": duration, "posted": status, "immediate": immediate})
    return out


def card_to_job(card: dict[str, Any]) -> ScrapedJob:
    locations = card.get("locations") or []
    wfh = any(re.search(r"work from home|remote", loc, re.I) for loc in locations) or not locations
    places = [("Work from home" if re.search(r"work from home", loc, re.I) else loc) for loc in locations] or ["Work from home"]
    location = ", ".join(places) + ", India"
    lines = [f"{card['title']} internship at {card['company']} (via Internshala).", f"Location: {location}."]
    if card.get("stipend"):
        lines.append(f"Stipend: {card['stipend']}.")
    if card.get("duration"):
        lines.append(f"Duration: {card['duration']}.")
    if card.get("immediate"):
        lines.append("Starts immediately.")
    return ScrapedJob(
        company_name=card["company"],
        role_title=card["title"] if re.search(r"\bintern", card["title"], re.I) else f"{card['title']} Intern",
        description=" ".join(lines),
        source_url=card["url"],
        application_url=card["url"],
        source_platform=ATSPlatform.CUSTOM,
        location=location[:255],
        is_remote=wfh,
        job_type=JobType.INTERNSHIP,
        experience_level=ExperienceLevel.INTERNSHIP,
        posted_date=parse_date(card.get("posted")) if card.get("posted") else None,
        external_id=f"internshala-{card['id']}" if card.get("id") else None,
        raw={"listing_source": "internshala", "apply_on_site": SITE, "stipend": card.get("stipend") or None,
             "duration": card.get("duration") or None, "starts_immediately": bool(card.get("immediate"))},
    ).finalize()


def parse_detail(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    parts = [_text(node) for node in soup.select(".internship_details .text-container, .about_company_text_container, "
                                                 ".internship_details .round_tabs_container, .skills_heading + div")]
    return "\n\n".join(p for p in parts if p)


class InternshalaScraper(BrowserScraper):
    platform = ATSPlatform.CUSTOM
    rate_key = "internshala"
    requires_browser = False  # plain HTTP first; a browser only if Internshala challenges us

    def _http_page(self, url: str) -> str | None:
        """Plain HTTP. ``None`` when Internshala challenges the request and a real browser is needed."""
        response = self.request("GET", url)
        html = response.text
        if response.status_code in (403, 503) or any(m in html[:20000] for m in BLOCK_MARKERS):
            return None
        if response.status_code == 404:
            raise ScraperError(f"Not found: {url}")
        if response.status_code >= 400:
            raise ScraperError(f"HTTP {response.status_code} for {url}")
        return html

    def _get(self, url: str, state: dict[str, Any]) -> str:
        if state.get("session") is None:
            html = self._http_page(url)
            if html is not None:
                return html
            try:  # fall back to a real browser for the rest of this scan
                state["session"] = self.open_session().__enter__()
            except (ScraperError, BrowserUnavailable) as exc:
                raise ScraperError(f"Internshala blocked the request and no browser is available: {exc}") from exc
        return self.browser_get(state["session"], url, "div.individual_internship")

    def _second_pages(self, urls: list[str], parsed: dict[int, list[dict[str, Any]]], query: SearchQuery) -> None:
        """Too few internships on the first pages: read page 2 of the full ones (plain HTTP, one at a time)."""
        if len({c["url"] for cards in parsed.values() for c in cards}) >= query.limit * 2:
            return
        for idx in [i for i in parsed if len(parsed[i]) >= FULL_PAGE][:MAX_MORE_PAGES]:
            if query.time_up():
                return
            try:
                html = self._http_page(next_page(urls[idx]))
            except ScraperError as exc:  # e.g. RateLimited, or no page 2 after all
                logger.info("Internshala page 2 of %s skipped: %s", urls[idx], exc)
                if isinstance(exc, RateLimited):
                    return
                continue
            if html is None:  # challenged: keep what we have
                return
            parsed[idx] = parsed[idx] + parse_cards(html)

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        urls = search_urls(query)
        pages: dict[int, str] = {}
        blocked: list[int] = []
        errors: list[str] = []

        def fetch(url: str) -> list[ScrapedJob]:  # search pages over plain HTTP, several at once
            html = self._http_page(url)
            if html is None:
                blocked.append(urls.index(url))
            else:
                pages[urls.index(url)] = html
            return []

        self.map_sources(urls, fetch, query, "Internshala page")
        if blocked:  # Internshala challenged plain HTTP: one browser, one page at a time
            state: dict[str, Any] = {"session": None}
            try:
                state["session"] = self.open_session().__enter__()
                cards = 0
                for idx in sorted(blocked):
                    if cards >= query.limit * 2 or query.time_up():
                        break
                    try:
                        pages[idx] = self.browser_get(state["session"], urls[idx], "div.individual_internship")
                        cards += len(parse_cards(pages[idx]))
                    except ScraperError as exc:
                        errors.append(str(exc))
            except (ScraperError, BrowserUnavailable) as exc:
                errors.append(f"Internshala blocked the request and no browser is available: {exc}")
            finally:
                if state.get("session") is not None:
                    state["session"].__exit__(None, None, None)
        parsed = {idx: parse_cards(pages[idx]) for idx in sorted(pages)}
        if not blocked:
            self._second_pages(urls, parsed, query)
        jobs: list[ScrapedJob] = []
        seen: set[str] = set()
        # Pages are already scoped to your roles by category, so no title filter. One card from each page in turn:
        # the limit is shared by every category and city (the filter then puts your prime city first).
        for card in interleave(list(parsed.values())):
            if len(jobs) >= query.limit * 4:
                break
            if card["url"] in seen:
                continue
            seen.add(card["url"])
            jobs.append(card_to_job(card))
        if not jobs and errors:
            raise ScraperError(errors[0])
        if not jobs and not pages and urls:
            raise ScraperError("Internshala could not be reached")
        return self.filter(jobs, dataclasses.replace(query, keywords=[]))

    def fetch_job(self, url: str) -> ScrapedJob | None:
        html = self._get(url, {"session": None})
        description = parse_detail(html)
        soup = BeautifulSoup(html, "lxml")
        title = _text(soup.select_one(".profile_on_detail_page, .heading_4_5.profile, h1"))
        company = _text(soup.select_one(".company_name a, .company-name, .heading_6.company_name"))
        if not title or not company:
            return None
        card = {"id": None, "url": url.split("?")[0], "title": title, "company": company,
                "locations": [_text(a) for a in soup.select("#location_names a, .location_link, .locations a")],
                "stipend": _text(soup.select_one(".stipend")), "duration": "", "posted": "",
                "immediate": bool(re.search(r"start(s)? immediately", soup.get_text(" "), re.I))}
        job = card_to_job(card)
        if description:
            job.description = f"{job.description}\n\n{description}"
        return job
