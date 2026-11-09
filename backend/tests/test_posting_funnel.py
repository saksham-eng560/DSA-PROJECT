"""The scan's posting funnel on its own, no database: which postings are saved and the note each step leaves."""

from __future__ import annotations

from app.models.enums import JobType
from app.services.agent.discovery import select_postings
from tests.test_role_focus import FOCUS, LONG, _job

FITS = _job("Python Backend Intern", "Python3, FastAPI and Postgres." + LONG)
JAVA = _job("Java Development Intern", "Java, Spring Boot." + LONG)
OFF_FOCUS = _job("Software Engineering Intern", "Build services in Go and TypeScript." + LONG)
FULL_TIME = _job("Senior Python Engineer", "Python3, FastAPI. 5+ years of experience." + LONG)
FULL_TIME.job_type = JobType.FULL_TIME


def test_hard_rules_leave_postings_out_and_near_misses_wait_at_the_back_of_the_deck() -> None:
    prefs = {**FOCUS, "internships_only": True}
    kept, notes = select_postings([OFF_FOCUS, JAVA, FULL_TIME, FITS], prefs)
    assert kept == [FITS, OFF_FOCUS]  # the near miss follows everything that fits
    assert [n.split(":")[0] for n in notes] == ["Interns only", "Tech focus", "Near misses", "This scan"]
    assert notes[0].startswith("Interns only: left out 1 posting ")
    assert notes[1] == "Tech focus: left out 1 posting in Java, Spring Boot"
    assert notes[2].endswith("shown only if Swipe Review would be thin")


def test_near_misses_are_left_out_outside_swipe_review_or_with_no_deck_to_fill() -> None:
    kept, notes = select_postings([OFF_FOCUS, FITS], {**FOCUS, "review_mode": "auto"})
    assert kept == [FITS] and notes[0].endswith(", left out")
    kept, _notes = select_postings([OFF_FOCUS, FITS], {**FOCUS, "fill_deck_to": 0})
    assert kept == [FITS]


def test_nothing_to_say_when_everything_fits() -> None:
    assert select_postings([FITS], FOCUS) == ([FITS], ["This scan: custom 1"])
    assert select_postings([], FOCUS) == ([], [])


def _many(n: int, title: str, desc: str, source: str) -> list:
    jobs = []
    for i in range(n):
        job = _job(title, desc, internshala=source == "internshala", company=f"{source}-co-{title[:6]}-{i}")
        if source != "internshala":
            job.raw = {**(job.raw or {}), "scan_source": source}
        jobs.append(job)
    return jobs


def test_near_misses_count_against_internshalas_limit() -> None:
    """With 10 Internshala postings a scan, 40 Internshala near misses still got in and could fill a thin deck."""
    fits = _many(10, "Python Backend Intern", FITS.description, "internshala") + _many(2, "Python Backend Intern",
                                                                                         FITS.description, "linkedin")
    near = _many(40, "Software Engineering Intern", OFF_FOCUS.description, "internshala") \
        + _many(3, "Software Engineering Intern", OFF_FOCUS.description, "linkedin")
    prefs = {**FOCUS, "internshala_per_scan": 10, "internshala_share": 100}
    kept, notes = select_postings(fits + near, prefs)
    assert kept[:12] == fits  # everything that fits first
    assert kept[12:] == near[40:]  # Internshala's 10 are already taken by postings that fit
    assert notes[-1] == "Near misses: 40 postings left out to keep Internshala and every site within their share"
    # Room left in Internshala's share goes to its best near misses
    kept, _notes = select_postings(fits[3:] + near, prefs)
    assert sum(1 for j in kept[9:] if "internshala.com/" in j.source_url) == 3


def test_near_misses_count_against_each_sites_share() -> None:
    fits = (_many(25, "Python Backend Intern", FITS.description, "linkedin")
            + _many(25, "Python Backend Intern", FITS.description, "naukri")
            + _many(20, "Python Backend Intern", FITS.description, "unstop"))
    near = _many(60, "Software Engineering Intern", OFF_FOCUS.description, "linkedin")
    kept, _notes = select_postings(fits + near, {**FOCUS, "source_share": 40})
    assert kept[:70] == fits
    assert len(kept) == 75  # LinkedIn may have 40 % (30 of 75): 5 near misses, not the deck's 40
