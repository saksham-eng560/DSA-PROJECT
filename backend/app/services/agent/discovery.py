"""Discovery: search every job source at once, then the funnel that picks which of the postings found make the
deck. The funnel (``select_postings``) is pure: postings and your settings in, postings and a note per step out."""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any

from app.config import settings
from app.scrapers import SCRAPERS, ScrapedJob, ScraperError, SearchQuery
from app.services.agent.review import deck_target, is_swipe_mode
from app.services.agent.runs import RunLog
from app.services.duration_fit import duration_reasons, max_months
from app.services.intern_level import drop_ineligible, student, with_resume
from app.services.location_focus import LocationFocus, balance_by_location, location_tier
from app.services.role_focus import avoid_reasons, off_focus_reasons
from app.services.scan_progress import ScanProgress
from app.services.source_mix import balance_sources, cap_followers, cap_internshala, source_of

logger = logging.getLogger(__name__)


def scan_platforms(prefs: dict[str, Any], requested: list[str] | None = None) -> list[str]:
    """The sources a scan searches: the ones you asked for, or your saved ones plus the top companies."""
    chosen = list(requested or prefs.get("platforms") or list(SCRAPERS))
    if not requested and prefs.get("scan_top_companies", True) and "top_companies" in SCRAPERS \
            and "top_companies" not in chosen:
        chosen.insert(0, "top_companies")
    return chosen


def discover_jobs(query: SearchQuery, platforms: list[str], run: RunLog | None = None,
                  progress: ScanProgress | None = None, tick: Callable[[], None] | None = None) -> list[ScrapedJob]:
    """Search every platform at once (up to SCAN_SOURCE_CONCURRENCY). Sources are asked to wrap up
    with what they have at 80% of SCAN_SOURCE_TIMEOUT_SECONDS; one still running at the limit is left
    out of this scan so a single slow site never holds up the rest."""
    results: list[ScrapedJob] = []
    usable = [p for p in platforms if p in SCRAPERS]
    if not usable:
        return []

    started = time.monotonic()
    # Sources wrap up at 80% of the limit and return what they have; the hard limit is the backstop.
    query = dataclasses.replace(query, deadline=started + settings.SCAN_SOURCE_TIMEOUT_SECONDS * 0.8)

    def search(platform: str) -> list[ScrapedJob]:
        if progress is None:
            return SCRAPERS[platform]().search(query)
        progress.source_started(platform)
        scoped = dataclasses.replace(query, progress=lambda done, total: progress.source_step(platform, done, total))
        return SCRAPERS[platform]().search(scoped)

    pool = ThreadPoolExecutor(max_workers=max(1, min(settings.SCAN_SOURCE_CONCURRENCY, len(usable))),
                              thread_name_prefix="scan-source")
    futures = {pool.submit(search, p): p for p in usable}
    pending = set(futures)
    deadline = started + settings.SCAN_SOURCE_TIMEOUT_SECONDS
    try:
        while pending:
            done, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
            for future in done:
                platform = futures[future]
                try:
                    found = future.result()
                    for job in found:
                        job.raw = {**(job.raw or {}), "scan_source": platform}
                    results.extend(found)
                    if run:
                        run.log(f"{platform}: found {len(found)} jobs")
                    if progress:
                        progress.source_finished(platform, len(found))
                except ScraperError as exc:
                    if run:
                        run.log(f"{platform}: {exc}", level="error")
                    if progress:
                        progress.source_finished(platform, 0, "failed", str(exc))
                except Exception as exc:
                    logger.exception("Scraper %s crashed", platform)
                    if run:
                        run.log(f"{platform}: unexpected error {type(exc).__name__}: {exc}", level="error")
                    if progress:
                        progress.source_finished(platform, 0, "failed", f"{type(exc).__name__}: {exc}")
            if progress:
                progress.found = len({j.source_url for j in results})
            if tick:
                tick()
            if pending and time.monotonic() > deadline:
                for future in pending:
                    platform = futures[future]
                    if run:
                        run.log(f"{platform}: still searching after {settings.SCAN_SOURCE_TIMEOUT_SECONDS}s, "
                                "left out of this scan", level="warning")
                    if progress:
                        progress.source_finished(platform, 0, "timeout")
                break
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # a timed-out source finishes on its own
    # Deduplicate by URL
    unique: dict[str, ScrapedJob] = {}
    for job in results:
        unique.setdefault(job.source_url, job)
    return list(unique.values())


# --------------------------------------------------------------------------- the funnel
def student_label(prefs: dict[str, Any]) -> str:
    year = student(prefs).year
    return f"a {('1st', '2nd', '3rd', '4th', '5th')[year - 1]}-year student" if year else "a student"


def near_miss_reasons(job: Any, prefs: dict[str, Any]) -> list[str]:
    """What a just-scraped posting misses of your tech focus and internship length."""
    return off_focus_reasons(job, prefs) + duration_reasons(job, prefs)


def split_near_misses(jobs: list[ScrapedJob], prefs: dict[str, Any]) -> tuple[list[ScrapedJob], list[ScrapedJob], int]:
    """(postings in your tech focus and length, near misses, how many are in a technology you skip: left out)."""
    fits: list[ScrapedJob] = []
    near: list[ScrapedJob] = []
    avoided = 0
    for job in jobs:
        if avoid_reasons(job, prefs):
            avoided += 1
        else:
            (near if near_miss_reasons(job, prefs) else fits).append(job)
    return fits, near, avoided


def _plural(n: int, word: str = "posting") -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def select_postings(scraped: list[ScrapedJob], prefs: dict[str, Any], resume: dict[str, Any] | None = None,
                    focus: LocationFocus | None = None, have_internshala: frozenset[str] = frozenset(),
                    have: frozenset[str] = frozenset()) -> tuple[list[ScrapedJob], list[str]]:
    """Which of a scan's postings are saved, and a note for the run log on each step that left some out.

    Hard rules first (not an internship for you, a technology you skip), then the mix: the location focus,
    Internshala's share, every other site's share. Near misses (outside your tech focus or internship length)
    are kept aside; in Swipe Review they follow the rest, to be shown only when the deck would be thin.
    ``resume``: your master resume's content (your graduation year). ``have_internshala`` / ``have``: source
    URLs of the postings you already have, so the shares count only what's new."""
    notes: list[str] = []
    scraped, not_for_you = drop_ineligible(scraped, with_resume(prefs, resume))
    if not_for_you:
        notes.append(f"Interns only: left out {_plural(not_for_you)} that aren't internships for {student_label(prefs)} "
                     "(full-time roles, PhD / MBA-only, final-year-only...)")
    scraped, near, avoided = split_near_misses(scraped, prefs)
    if avoided:
        notes.append(f"Tech focus: left out {_plural(avoided)} in {', '.join(prefs.get('avoid_skills') or [])}")
    if near:
        skills = prefs.get("focus_skills") or []
        notes.append(f"Near misses: {_plural(len(near))} outside "
                     f"{', '.join(skills[:3]) + (', ...' if len(skills) > 3 else '') or 'your focus'}"
                     + (f" or longer than {max_months(prefs):g} months" if max_months(prefs) else "")
                     + (", shown only if Swipe Review would be thin" if is_swipe_mode(prefs) else ", left out"))
    if focus is not None:
        scraped, dropped = balance_by_location(scraped, focus)
        near, dropped_near = balance_by_location(near, focus)
        if dropped or dropped_near:
            notes.append(f"Location focus: kept ~{focus.share}% of postings in {focus.country.title()} "
                         f"({dropped + dropped_near} from elsewhere left out this scan)")
    scraped, capped = cap_internshala(scraped, prefs.get("internshala_share"), focus,
                                      prefs.get("internshala_per_scan"), have_internshala)
    if capped:
        notes.append(f"Internshala: kept the best {prefs.get('internshala_per_scan', 10)} new postings at most "
                     f"(and at most {prefs.get('internshala_share', 25)}% of the scan); {capped} more left out")
    scraped, crowded = balance_sources(scraped, prefs.get("source_share"), focus, have)
    if crowded:
        notes.append("Source mix: no site fills more than its share of a scan; left out "
                     + ", ".join(f"{n} from {src}" for src, n in sorted(crowded.items(), key=lambda kv: -kv[1])))
    mix: dict[str, int] = {}
    for sj in scraped:
        mix[source_of(sj)] = mix.get(source_of(sj), 0) + 1
    if mix:
        notes.append("This scan: " + ", ".join(f"{src} {n}" for src, n in sorted(mix.items(), key=lambda kv: -kv[1])))
    if near and is_swipe_mode(prefs):  # kept aside: shown only if your settings leave Swipe Review thin
        near = sorted(near, key=lambda j: (len(near_miss_reasons(j, prefs)), location_tier(j.location, j.is_remote, focus)))
        # The same Internshala and per-site shares, counting the postings that fit
        near, over_share = cap_followers(scraped, near, internshala_share=prefs.get("internshala_share"),
                                         internshala_per_scan=prefs.get("internshala_per_scan"),
                                         source_share=prefs.get("source_share"), known_internshala=have_internshala,
                                         known=have)
        if over_share:
            notes.append(f"Near misses: {_plural(over_share)} left out to keep Internshala and every site "
                         "within their share")
        scraped = scraped + near[:deck_target(prefs)]
    return scraped, notes
