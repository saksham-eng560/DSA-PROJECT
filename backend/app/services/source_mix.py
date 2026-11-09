"""Keep Internshala from crowding out everything else.

Internshala returns far more postings than any other source, and many come from small or unknown companies,
so left alone they fill the whole deck. Each scan adds at most ``internshala_per_scan`` (default 10) new
Internshala postings, and no more than ``internshala_share`` % (default 25 %) of its new postings: one for
every three from elsewhere, though never so few that a thin scan keeps under ``SCAN_FLOOR`` postings. The ones kept
are the best: renowned companies first, then postings with no warning signs, then the prime city. Postings you already
have don't count (they're only refreshed).
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from app.scrapers.base import ScrapedJob
from app.services.company_verifier import SUSPICIOUS, VERIFIED, check_job
from app.services.location_focus import location_tier

DEFAULT_SHARE = 25
DEFAULT_PER_SCAN = 10
MIN_KEPT = 3  # a scan where only Internshala answered still shows a few
SCAN_FLOOR = 60  # a thin scan keeps at least this many new postings whatever the proportions


def is_internshala(job: ScrapedJob) -> bool:
    return (job.raw or {}).get("listing_source") == "internshala" or "internshala.com/" in (job.source_url or "")


def _setting(value: Any, default: int, high: int) -> int:
    try:
        return default if value is None else max(0, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def internshala_allowance(others: int, share: Any = None, per_scan: Any = None) -> int:
    """How many new Internshala postings a scan with ``others`` new postings from elsewhere may add. When the other
    sites have little, Internshala still fills the scan up to ``SCAN_FLOOR``: proportions never cost you good jobs."""
    share, per_scan = _setting(share, DEFAULT_SHARE, 100), _setting(per_scan, DEFAULT_PER_SCAN, 50)
    if share == 0 or per_scan == 0:
        return 0
    by_share = per_scan if share >= 100 else max(MIN_KEPT, others * share // (100 - share), SCAN_FLOOR - others)
    return min(per_scan, by_share)


def cap_internshala(jobs: list[ScrapedJob], share: Any = None, focus: Any = None, per_scan: Any = None,
                    known: Collection[str] = ()) -> tuple[list[ScrapedJob], int]:
    """Return (jobs to keep in their original order, how many new Internshala postings were left out).

    ``known``: source URLs you already have; those are kept and don't count toward the limit.
    """
    new = [j for j in jobs if is_internshala(j) and j.source_url not in known]
    if not new:
        return jobs, 0
    others = sum(1 for j in jobs if not is_internshala(j) and j.source_url not in known)
    allowed = internshala_allowance(others, share, per_scan)
    if len(new) <= allowed:
        return jobs, 0

    def rank(job: ScrapedJob) -> tuple[int, int, int]:
        check = check_job(job)
        trust = 0 if check.verdict == VERIFIED else 2 if check.verdict == SUSPICIOUS else 1
        place = location_tier(job.location, job.is_remote, focus) if focus is not None else 0
        return (trust, -check.score, place)

    keep = {id(j) for j in sorted(new, key=rank)[:allowed]}
    kept = [j for j in jobs if not is_internshala(j) or j.source_url in known or id(j) in keep]
    return kept, len(new) - allowed


# --------------------------------------------------------------------------- every source in proportion
# No single job source fills more than ``source_share`` % (default 40 %) of a scan's new postings while the others
# have postings to offer; the ones kept from a crowded source are its best (renowned, verified, prime city,
# freshest). A thin scan is never cut below ``SCAN_FLOOR`` postings just to keep the proportions.
DEFAULT_SOURCE_SHARE = 40


def source_of(job: ScrapedJob) -> str:
    """The job source (scraper) a posting came from: "internshala", "linkedin", "top_companies"..."""
    raw = job.raw or {}
    return str(raw.get("scan_source") or ("internshala" if is_internshala(job) else raw.get("listing_source"))
               or job.source_platform.value)


def _quality(job: ScrapedJob, focus: Any) -> tuple[int, int, int, int]:
    check = check_job(job)
    trust = 0 if (job.raw or {}).get("top_company") else 1 if check.verdict == VERIFIED else \
        3 if check.verdict == SUSPICIOUS else 2
    place = location_tier(job.location, job.is_remote, focus) if focus is not None else 0
    fresh = -(job.posted_date.toordinal() if job.posted_date else 0)
    return (trust, place, fresh, -check.score)


def source_caps(counts: dict[str, int], share: int, floor: int) -> dict[str, int]:
    """How many new postings each source may add: at most ``share`` % of the total kept, unless the total would
    fall under ``floor`` (then the biggest sources give the rest, in turn)."""
    total = sum(counts.values())
    if share >= 100 or len(counts) < 2 or total == 0:
        return dict(counts)
    share = max(share, -(-100 // len(counts)))  # two sources can't each be under 40 %: 50 % each then
    # The largest K with sum(min(n, share% of K)) >= K: every source at most share % of what is kept.
    lo, hi = 0, total
    while lo < hi:
        mid = (lo + hi + 1) // 2
        cap = mid * share // 100
        if sum(min(n, cap) for n in counts.values()) >= mid:
            lo = mid
        else:
            hi = mid - 1
    cap = max(1, lo * share // 100)
    caps = {s: min(n, cap) for s, n in counts.items()}
    short = min(floor, total) - sum(caps.values())
    while short > 0:  # a thin scan: fill up from the sources with postings left, one at a time
        for s in sorted(counts, key=lambda s: counts[s] - caps[s], reverse=True):
            if short <= 0:
                break
            if caps[s] < counts[s]:
                caps[s] += 1
                short -= 1
    return caps


def balance_sources(jobs: list[ScrapedJob], share: Any = None, focus: Any = None, known: Collection[str] = (),
                    floor: int = SCAN_FLOOR) -> tuple[list[ScrapedJob], dict[str, int]]:
    """Return (jobs to keep in their original order, {source: how many new postings were left out})."""
    share = _setting(share, DEFAULT_SOURCE_SHARE, 100)
    new = [j for j in jobs if j.source_url not in known]
    counts: dict[str, int] = {}
    for job in new:
        counts[source_of(job)] = counts.get(source_of(job), 0) + 1
    caps = source_caps(counts, share or 100, floor)
    keep: set[int] = set()
    dropped: dict[str, int] = {}
    for src, cap in caps.items():
        mine = sorted((j for j in new if source_of(j) == src), key=lambda j: _quality(j, focus))
        keep.update(id(j) for j in mine[:cap])
        if len(mine) > cap:
            dropped[src] = len(mine) - cap
    return [j for j in jobs if j.source_url in known or id(j) in keep], dropped



# --------------------------------------------------------------------------- near misses within the same shares
def cap_followers(kept: list[ScrapedJob], extra: list[ScrapedJob], *, internshala_share: Any = None,
                  internshala_per_scan: Any = None, source_share: Any = None, known_internshala: Collection[str] = (),
                  known: Collection[str] = ()) -> tuple[list[ScrapedJob], int]:
    """Return (the postings of ``extra``, best first, that still fit Internshala's and every site's share once the
    ``kept`` postings are counted, how many were left out). Near misses follow the postings that fit; without this,
    a thin deck could be filled mostly from Internshala, exactly what the shares prevent."""
    def new(job: ScrapedJob, seen: Collection[str]) -> bool:
        return job.source_url not in seen

    # Internshala: what's left of its allowance after the postings already kept
    others = sum(1 for j in kept + extra if not is_internshala(j) and new(j, known_internshala))
    room = internshala_allowance(others, internshala_share, internshala_per_scan) \
        - sum(1 for j in kept if is_internshala(j) and new(j, known_internshala))
    left: list[ScrapedJob] = []
    for job in extra:
        if is_internshala(job) and new(job, known_internshala):
            if room <= 0:
                continue
            room -= 1
        left.append(job)
    # Every site: its share of everything kept, near misses included, less what it already has
    share = _setting(source_share, DEFAULT_SOURCE_SHARE, 100)
    taken: dict[str, int] = {}
    counts: dict[str, int] = {}
    for job in kept:
        if new(job, known):
            taken[source_of(job)] = taken.get(source_of(job), 0) + 1
    for job in kept + left:
        if new(job, known):
            counts[source_of(job)] = counts.get(source_of(job), 0) + 1
    caps = source_caps(counts, share or 100, SCAN_FLOOR)
    out: list[ScrapedJob] = []
    for job in left:
        src = source_of(job)
        if new(job, known):
            if taken.get(src, 0) >= caps.get(src, 0):
                continue
            taken[src] = taken.get(src, 0) + 1
        out.append(job)
    return out, len(extra) - len(out)
