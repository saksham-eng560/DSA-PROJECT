"""Where and when you want to work: a location focus (e.g. India, Delhi NCR first) and an internship season.

Location focus
    ``{"country": "India", "prime_cities": ["Delhi", "Gurugram", ...], "country_share": 90}``
    Every job gets a tier: 0 = a prime city, 1 = elsewhere in the country, 2 = remote / unknown,
    3 = abroad. Swipe Review shows lower tiers first, and each scan keeps roughly
    ``country_share`` % of new postings in the country (the rest may come from anywhere).

Internship season
    ``"Summer 2027"``: postings clearly for another term are skipped, postings that name the season
    are ranked first, and postings that don't say are kept (most Indian listings don't).

The same substring rules are used in Python and in SQL (``location_tier_sql``) so both agree.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, case, func, not_, or_

from app.services.text_utils import normalize_text

# Places that identify a country when a listing doesn't name it ("Bengaluru, Karnataka").
COUNTRY_PLACES: dict[str, list[str]] = {
    "india": [
        "india", "delhi", "gurugram", "gurgaon", "noida", "faridabad", "ghaziabad", "bengaluru", "bangalore",
        "mumbai", "navi mumbai", "thane", "pune", "hyderabad", "secunderabad", "chennai", "kolkata", "ahmedabad",
        "gandhinagar", "jaipur", "chandigarh", "mohali", "panchkula", "kochi", "cochin", "thiruvananthapuram",
        "trivandrum", "coimbatore", "indore", "bhopal", "lucknow", "kanpur", "nagpur", "nashik", "surat",
        "vadodara", "bhubaneswar", "visakhapatnam", "vizag", "mysuru", "mysore", "mangaluru", "mangalore",
        "dehradun", "patna", "ranchi", "raipur", "guwahati", "goa", "karnataka", "maharashtra", "telangana",
        "tamil nadu", "haryana", "uttar pradesh", "west bengal", "gujarat", "rajasthan", "kerala", "punjab",
    ],
}
# Substrings that contain a country place but are somewhere else.
FALSE_FRIENDS: dict[str, list[str]] = {"india": ["indiana", "indianapolis"]}

DEFAULT_PRIME_CITIES = ["Delhi", "New Delhi", "Delhi NCR", "Gurugram", "Gurgaon", "Noida", "Greater Noida",
                        "Faridabad", "Ghaziabad"]

SEASONS = {"summer": "summer", "fall": "fall", "autumn": "fall", "winter": "winter", "spring": "spring"}
_SEASON_YEAR = re.compile(r"\b(summer|fall|autumn|winter|spring)\s*(?:of\s*)?(?:20(\d{2})|'(\d{2}))\b")
_YEAR_SEASON = re.compile(r"\b20(\d{2})\s*(summer|fall|autumn|winter|spring)\b")
_YEAR = re.compile(r"\b20(2\d|3\d)\b")
_IMMEDIATE = re.compile(r"\bstart(?:s|ing)? immediately\b|\bimmediate (?:joining|joiner|start)\b|\bjoin immediately\b")

# A "remote" role that names another country is abroad ("Remote, US", "Remote - United States", "Remote (EMEA)").
_FOREIGN = re.compile(r"\b(usa|u\.s\.a?\.?|united states|us|canada|uk|united kingdom|europe|emea|latam|americas|"
                      r"north america|new york|ny|california|san francisco|seattle|texas|boston|chicago|london|germany|"
                      r"berlin|netherlands|amsterdam|singapore|australia|japan|dubai|uae)\b")

TIER_PRIME, TIER_COUNTRY, TIER_REMOTE, TIER_ABROAD = 0, 1, 2, 3
TIER_LABELS = {TIER_PRIME: "prime location", TIER_COUNTRY: "in your focus country", TIER_REMOTE: "remote / unspecified",
               TIER_ABROAD: "outside your focus country"}


@dataclass(frozen=True)
class LocationFocus:
    country: str
    prime_terms: tuple[str, ...]
    country_terms: tuple[str, ...]
    false_friends: tuple[str, ...]
    share: int  # % of each scan's new postings to keep in the country

    @property
    def other_share(self) -> int:
        return 100 - self.share


def get_focus(prefs: dict[str, Any]) -> LocationFocus | None:
    raw = prefs.get("location_focus") or {}
    if not isinstance(raw, dict) or not raw.get("enabled", True):
        return None
    country = normalize_text(str(raw.get("country") or ""))
    if not country:
        return None
    prime = tuple(t for t in (normalize_text(str(c)) for c in raw.get("prime_cities") or []) if t)
    places = COUNTRY_PLACES.get(country, [country])
    try:
        share = int(raw.get("country_share", 90))
    except (TypeError, ValueError):
        share = 90
    return LocationFocus(country=country, prime_terms=prime, country_terms=tuple(dict.fromkeys([country, *places, *prime])),
                         false_friends=tuple(FALSE_FRIENDS.get(country, [])), share=max(0, min(100, share)))


def _mentions(text: str, terms: tuple[str, ...], false_friends: tuple[str, ...]) -> bool:
    if not text:
        return False
    for bad in false_friends:
        text = text.replace(bad, " ")
    return any(term in text for term in terms)


def location_tier(location: str | None, is_remote: bool, focus: LocationFocus | None) -> int:
    if focus is None:
        return TIER_COUNTRY
    loc = normalize_text(location or "")
    if _mentions(loc, focus.prime_terms, focus.false_friends):
        return TIER_PRIME
    if _mentions(loc, focus.country_terms, focus.false_friends):
        return TIER_COUNTRY
    if _FOREIGN.search(loc):
        return TIER_ABROAD
    if is_remote or not loc or re.fullmatch(r"(remote|anywhere|work from home|wfh)( .*)?", loc):
        return TIER_REMOTE
    return TIER_ABROAD


def only_home(focus: LocationFocus | None) -> bool:
    """country_share 100: only postings in the focus country (or remote without another country) are kept."""
    return focus is not None and focus.share >= 100


def is_indian_location(location: str | None) -> bool:
    return _mentions(normalize_text(location or ""), tuple(COUNTRY_PLACES["india"]), tuple(FALSE_FRIENDS["india"]))


def location_tier_sql(location_col: Any, remote_col: Any, focus: LocationFocus):  # type: ignore[no-untyped-def]
    """The same tiers as ``location_tier`` as a SQL CASE expression (for ORDER BY)."""
    loc = func.lower(func.coalesce(location_col, ""))

    def any_of(terms: tuple[str, ...]):  # type: ignore[no-untyped-def]
        hits = or_(*[loc.like(f"%{t}%") for t in terms]) if terms else or_(False)
        if focus.false_friends:  # "Indianapolis, Indiana" is not India
            hits = and_(hits, not_(or_(*[loc.like(f"%{f}%") for f in focus.false_friends])))
        return hits

    return case(
        (any_of(focus.prime_terms), TIER_PRIME),
        (any_of(focus.country_terms), TIER_COUNTRY),
        (or_(remote_col.is_(True), loc == ""), TIER_REMOTE),
        else_=TIER_ABROAD,
    )


def balance_by_location(jobs: list[Any], focus: LocationFocus | None) -> tuple[list[Any], int]:
    """Keep about ``focus.share`` % of postings in the focus country; drop the surplus from elsewhere.

    ``jobs`` are ScrapedJob-like objects (``location``, ``is_remote``, ``posted_date``). Returns
    (kept, dropped_count). If a scan finds nothing in the country, up to 10 others are kept so the
    deck is never silently empty.
    """
    if focus is None or not jobs or focus.share <= 0:
        return jobs, 0
    if only_home(focus):  # nothing from abroad; remote roles that don't name another country stay
        kept = [j for j in jobs if location_tier(j.location, j.is_remote, focus) <= TIER_REMOTE]
        return kept, len(jobs) - len(kept)
    home = [j for j in jobs if location_tier(j.location, j.is_remote, focus) <= TIER_COUNTRY]
    other = [j for j in jobs if location_tier(j.location, j.is_remote, focus) > TIER_COUNTRY]
    if focus.share >= 100:
        cap = 0 if home else 10
    elif home:
        cap = math.ceil(len(home) * focus.other_share / focus.share)
    else:
        cap = 10
    if len(other) <= cap:
        return jobs, 0
    # Prefer the freshest postings from elsewhere, remote ones before on-site abroad.
    other.sort(key=lambda j: (location_tier(j.location, j.is_remote, focus), -(j.posted_date.toordinal() if j.posted_date else 0)))
    kept_other = {id(j) for j in other[:cap]}
    kept = [j for j in jobs if id(j) in kept_other or location_tier(j.location, j.is_remote, focus) <= TIER_COUNTRY]
    return kept, len(jobs) - len(kept)


# --------------------------------------------------------------------------- internship season
@dataclass(frozen=True)
class Season:
    name: str   # "summer"
    year: int   # 2027

    @property
    def label(self) -> str:
        return f"{self.name.title()} {self.year}"


def get_season(prefs: dict[str, Any]) -> Season | None:
    raw = normalize_text(str(prefs.get("internship_season") or ""))
    m = re.search(r"(summer|fall|autumn|winter|spring)\s*(?:20)?(\d{2})", raw) or re.search(r"20(\d{2})\s*(summer|fall|autumn|winter|spring)", raw)
    if not m:
        return None
    season, year = (m.group(1), m.group(2)) if m.group(1).isalpha() else (m.group(2), m.group(1))
    return Season(SEASONS[season], 2000 + int(year))


def _terms_in(text: str) -> set[tuple[str, int]]:
    found = {(SEASONS[s], 2000 + int(y or y2)) for s, y, y2 in _SEASON_YEAR.findall(text)}
    found |= {(SEASONS[s], 2000 + int(y)) for y, s in _YEAR_SEASON.findall(text)}
    return found


def season_status(title: str, description: str, terms: list[str] | None, season: Season | None) -> tuple[str, str | None]:
    """Return (status, note): "match", "conflict" (hard skip), "other_mentioned" / "immediate" (heads-ups) or "unknown"."""
    if season is None:
        return "unknown", None
    target = (season.name, season.year)
    head = normalize_text(f"{title} {' '.join(terms or [])}")
    body = normalize_text((description or "")[:4000])
    head_terms, body_terms = _terms_in(head), _terms_in(body)
    title_years = {2000 + int(y) for y in _YEAR.findall(normalize_text(title))}
    if target in head_terms or target in body_terms or (season.year in title_years and not (head_terms - {target})):
        return "match", f"{season.label} internship"
    if head_terms or (title_years and season.year not in title_years):
        other = sorted(head_terms)[0] if head_terms else ("", sorted(title_years)[0])
        label = f"{other[0].title()} {other[1]}".strip()
        return "conflict", f"Posting is for {label}, not {season.label}"
    if body_terms:
        other = sorted(body_terms)[0]
        return "other_mentioned", f"Description mentions {other[0].title()} {other[1]}, not {season.label}"
    if _IMMEDIATE.search(body) or _IMMEDIATE.search(head):
        return "immediate", f"Starts immediately (you're targeting {season.label})"
    return "unknown", None


def season_rank_sql(title_col: Any, description_col: Any, season: Season):  # type: ignore[no-untyped-def]
    """0 when the posting names the target season (or the year in its title), else 1."""
    title = func.lower(func.coalesce(title_col, ""))
    desc = func.lower(func.coalesce(description_col, ""))
    label = f"{season.name} {season.year}"
    return case(
        (or_(title.like(f"%{season.year}%"), desc.like(f"%{label}%"), desc.like(f"%{season.year} {season.name}%")), 0),
        else_=1,
    )
