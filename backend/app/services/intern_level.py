"""Intern-level roles only, and only internships a student in your year can get.

Every source (big tech boards, LinkedIn, Internshala, the curated lists, careers pages) goes through the same
checks: at the scraper (cheap: is it an internship at all?), before a scan saves anything, and again before a
card reaches Swipe Review. A posting is left out when it is:

* not an internship: full-time, new-grad, contract and part-time jobs, and full-time jobs that only mention
  interns ("Intern Program Manager", "University Recruiter - Internships");
* for another degree: internships only for PhD, Master's or MBA students;
* for another year: "final-year students only", "rising seniors", "recent graduates", "2026 graduates" when
  you're in 2nd year and graduate in 2029;
* asking for real work experience: "2+ years of experience".

Your year of study (``year_of_study``, default 2nd year) and graduation year (``graduation_year``; read from your
resume's education when empty, else estimated from your year) are in Settings › Search preferences.
``internships_only`` (default on) turns all of it off for a full-time search.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

DEFAULT_YEAR = 2
COURSE_YEARS = 4  # B.Tech / B.E. / BS: used only to estimate a graduation year you haven't given

# A role whose title says it's an internship (job types from job sites are often wrong: LinkedIn lists many
# internships as "Full-time").
INTERN_TITLE = re.compile(
    r"\b(intern|interns|internship|internships|co-?op|summer (analyst|associate|engineer|student|intern)|"
    r"student (researcher|engineer|developer)|winter intern)\b", re.I)
# Full-time jobs about interns: a staff role word, interns in the plural or an "intern program", and no
# "... Intern" role of its own ("Campus Recruiting Intern" and "Product Manager Intern" are internships).
_STAFF_WORD = re.compile(r"\b(manager|recruiter|recruiting|coordinator|director|lead|partner|specialist|sourcer|"
                         r"advisor|head|administrator)\b", re.I)
_ABOUT_INTERNS = re.compile(r"\b(interns|internships|(intern|internship) program(me)?s?|early careers?)\b", re.I)
_OWN_INTERN = re.compile(r"\bintern\b(?!\s*program)|\binternship\b(?!s|\s*program)", re.I)

_GRAD_DEGREE = r"(ph\.?\s?d\.?|doctoral|doctorate|mba|master'?s|masters|m\.?\s?tech|post-?graduate|graduate students?)"
_GRAD_TITLE = re.compile(rf"\b{_GRAD_DEGREE}(?=\W|$)", re.I)
_GRAD_REQUIRED = re.compile(
    r"\b(pursuing|enrolled|currently (in|a|an)|candidates? for|working towards?|studying|students? (in|of|pursuing)|"
    rf"must be (a|an|in))\b[^.\n;]{{0,80}}?\b{_GRAD_DEGREE}(?=\W|$)", re.I)
_UNDERGRAD = re.compile(  # "B.E." / "BS" only in capitals: "must be enrolled" isn't a degree
    r"\b(bachelor'?s?|undergrad\w*|b\.?\s?tech|b\.?\s?sc|bca|bba|b\.\s?com|bcom|college students?|sophomores?|"
    r"freshm[ae]n|(first|second|1st|2nd)[- ]year)|(?-i:\bB\.?[ES](?:\.|\b))", re.I)

_US_YEAR = {1: "freshm[ae]n", 2: "sophomores?", 3: "juniors?", 4: "seniors?"}
# Years of study: "2nd-year", "final year", "pre-final year", "2nd and 3rd year", "1st to 3rd year".
_YEAR_WORD = r"(?:first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th|pre-?final|final|penultimate)"
_ORDINAL_LIST = rf"{_YEAR_WORD}(?:\s*(?:,|/|&|\band\b|\bor\b|\bto\b|-|–)\s*{_YEAR_WORD})*[- ]?years?"
_US_LIST = r"rising (?:sophomores?|juniors?|seniors?)|sophomores?|freshm[ae]n"
_YEAR_LISTS = re.compile(rf"\b(?:{_ORDINAL_LIST}|{_US_LIST})\b", re.I)
# "Open to final year students", "Eligibility: pre-final year B.Tech", "only rising seniors" (not "for the first
# year of the program": an ordinal year counts only before students / B.Tech / of study...).
_STUDENT_NOUN = (r"(?=\s*(?:students?|undergrad\w*|candidates?|engineering|b\.?\s?tech|b\.?\s?e\b|bachelor|college|"
                 r"batch|of (?:study|college|engineering|b\.?\s?tech|bachelor|university|graduation|your (?:degree|course))))")
_RESTRICTED_TO_YEAR = re.compile(
    r"\b(only|must|should|eligib\w*|open to|open for|for|who are|are in|currently in|available to|applicable to|"
    rf"meant for|targeted at|reserved for|limited to)\b[^.\n;]{{0,40}}?\b(?P<years>(?:{_ORDINAL_LIST}){_STUDENT_NOUN}|"
    rf"{_US_LIST})\b", re.I)
_YEAR_NUMBER = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
                "fifth": 5, "5th": 5}
_ALL_YEARS = re.compile(r"\b(any|all) (year|years|batch|batches)\b|students of all years|any year of study|"
                        r"irrespective of (the |your )?year|(all|any) (current )?students\b", re.I)
_GRADUATES = re.compile(r"\b(recent(ly)? graduat\w*|new grads?|fresh graduates?|already graduated|have graduated|"
                        r"must have graduated|graduates only|only graduates|degree holders? only|"
                        r"completed (your|their|a|the) (degree|graduation|bachelor'?s?))\b", re.I)
_STUDENT = re.compile(r"\b(students?|enrolled|pursuing|undergrad\w*|sophomores?|freshm[ae]n|juniors?|"
                      r"(first|second|third|1st|2nd|3rd)[- ]year)\b", re.I)

_GRAD_YEAR_AFTER = re.compile(
    r"\b(?:graduat\w*|batch(?:es)?|class(?:es)? of|pass(?:ing)?[- ]?outs?|passing out|convocation|degree completion|"
    r"grad(?:uation)? (?:date|year))\b[^.\n;]{0,50}?\b(?P<first>20[2-4]\d)\b(?:[^.\n;]{0,20}?\b(?P<last>20[2-4]\d)\b)?"
    r"(?P<later>\s*(?:or|and) (?:later|after|beyond)|\s*onwards?)?", re.I)
_GRAD_YEAR_BEFORE = re.compile(
    r"\b(?P<first>20[2-4]\d)\s*(?:(?:/|,|&|and|or|-|–)\s*(?P<last>20[2-4]\d)\s*)?(?:batch|graduates?|grads|pass-?outs?|passouts?)\b",
    re.I)
_EXPERIENCE = re.compile(  # "2+ years of experience", "3-5 yrs of relevant industry experience"; not "2 years of college"
    r"\b(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)\b(?:\s+of)?"
    r"(?:\s+(?!(?:and|or|with|study|studies|college|university|degree|school|coursework|education|academic)\b)[a-z/-]+){0,2}?"
    r"\s+experience", re.I)


@dataclass(frozen=True)
class Student:
    year: int | None  # 1-5: your year of study; None = don't check the year
    graduation_year: int | None
    source: str  # "you" | "resume" | "estimate" | "" (where graduation_year came from)

    def years_to_go(self, now: datetime | None = None) -> int | None:
        """0 = final year, 1 = pre-final year."""
        return None if self.graduation_year is None else self.graduation_year - academic_year_end(now)


def academic_year_end(now: datetime | None = None) -> int:
    """The calendar year this academic year ends in (it runs July-June): 2027 in October 2026."""
    now = now or datetime.now(UTC)
    return now.year + 1 if now.month >= 7 else now.year


def internships_only(prefs: dict[str, Any]) -> bool:
    return bool(prefs.get("internships_only", True))


def _int(value: Any, low: int, high: int) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def graduation_year_from_resume(content: Any) -> int | None:
    """The latest graduation year in your resume's education that isn't in the past ("2025 - 2029", "Expected May 2029")."""
    if not content:
        return None
    education = content.get("education") if isinstance(content, dict) else getattr(content, "education", None)
    this_year = datetime.now(UTC).year
    years = []
    for entry in education or []:
        end = entry.get("end_date") if isinstance(entry, dict) else getattr(entry, "end_date", "")
        found = [int(y) for y in re.findall(r"\b(20[2-4]\d)\b", str(end or ""))]
        years.extend(y for y in found if y >= this_year)
    return max(years) if years else None


def student(prefs: dict[str, Any], resume_content: Any = None, now: datetime | None = None) -> Student:
    year = _int(prefs.get("year_of_study"), 1, 5) if "year_of_study" in prefs else DEFAULT_YEAR
    chosen = _int(prefs.get("graduation_year"), 2000, 2100)
    if chosen is not None:
        return Student(year, chosen, "you")
    from_resume = graduation_year_from_resume(resume_content)
    if from_resume is not None:
        return Student(year, from_resume, "resume")
    if year is None:
        return Student(None, None, "")
    return Student(year, academic_year_end(now) + max(0, COURSE_YEARS - year), "estimate")


def with_resume(prefs: dict[str, Any], resume_content: Any) -> dict[str, Any]:
    """Your preferences with the graduation year from your resume filled in (when you haven't set one)."""
    if _int(prefs.get("graduation_year"), 2000, 2100) is not None or not resume_content:
        return prefs
    year = graduation_year_from_resume(resume_content)
    return {**prefs, "graduation_year": year} if year else prefs


def is_internship(title: str | None, job_type: Any = None) -> bool:
    title = title or ""
    if _STAFF_WORD.search(title) and _ABOUT_INTERNS.search(title) and not _OWN_INTERN.search(title):
        return False  # a full-time job about interns
    kind = getattr(job_type, "value", job_type)
    return kind == "internship" or bool(INTERN_TITLE.search(title))


def _year_labels(phrase: str) -> set[str]:
    """ "2nd and 3rd year" -> {"2", "3"}; "1st to 3rd year" -> {"1", "2", "3"}; "final year" -> {"final"}."""
    low = phrase.lower()
    if low.startswith("rising "):
        return {"rising " + low.split()[1].rstrip("s")}
    if low.startswith("sophomore"):
        return {"2"}
    if low.startswith("freshm"):
        return {"1"}
    labels: set[str] = set()
    numbers: list[int] = []
    for word in re.findall(_YEAR_WORD, low):
        if word in _YEAR_NUMBER:
            numbers.append(_YEAR_NUMBER[word])
        else:
            labels.add("prefinal" if word.startswith(("pre", "penultimate")) else "final")
    if len(numbers) == 2 and re.search(rf"{_YEAR_WORD}\s*(?:to|-|–)\s*{_YEAR_WORD}", low):
        numbers = list(range(min(numbers), max(numbers) + 1))
    return labels | {str(n) for n in numbers}


def _you(stud: Student) -> set[str]:
    """The year labels that include you (the summer after this year, US postings call you a rising junior)."""
    if stud.year is None:
        return set()
    you = {str(stud.year)}
    if stud.year + 1 in _US_YEAR:
        you.add("rising " + _US_YEAR[stud.year + 1].removesuffix("s?"))
    to_go = stud.years_to_go()
    if to_go == 0:
        you.add("final")
    elif to_go == 1:
        you.add("prefinal")
    return you


def names_your_year(text: str, stud: Student) -> bool:
    """The posting names your year ("open to first and second year students", "sophomores", "rising juniors")."""
    you = _you(stud)
    return bool(you) and any(_year_labels(m.group(0)) & you for m in _YEAR_LISTS.finditer(text))


def _graduation_years(text: str) -> tuple[int, int] | None:
    """The graduation years a posting asks for, as (earliest, latest); latest 9999 for "2027 or later"."""
    years: list[int] = []
    open_ended = False
    for pattern in (_GRAD_YEAR_AFTER, _GRAD_YEAR_BEFORE):
        for m in pattern.finditer(text):
            years.extend(int(y) for y in (m.group("first"), m.group("last")) if y)
            open_ended = open_ended or bool(m.groupdict().get("later"))
    if not years:
        return None
    return min(years), 9999 if open_ended else max(years)


def _ordinal(year: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(year, f"{year}th")


def eligibility_reasons(title: str, text: str, stud: Student) -> list[str]:
    """Why an internship isn't for you (empty: it is)."""
    reasons: list[str] = []
    if _GRAD_TITLE.search(title) and not _UNDERGRAD.search(title):
        reasons.append("For PhD / Master's / MBA students, not undergraduates")
    elif _GRAD_REQUIRED.search(text) and not _UNDERGRAD.search(text):
        reasons.append("Asks for students in a PhD / Master's / MBA program")

    if stud.year is not None and not _ALL_YEARS.search(text) and not names_your_year(text, stud):
        you = f"you're in {_ordinal(stud.year)} year"
        restricted = _RESTRICTED_TO_YEAR.search(text)
        if restricted:
            years = restricted.group("years").lower()
            who = years if re.search(r"(seniors?|juniors?|sophomores?|freshm[ae]n)$", years) else f"{years} students"
            reasons.append(f"Only for {who} ({you})")
        elif _GRADUATES.search(text) and not _STUDENT.search(text):
            reasons.append(f"For graduates, not current students ({you})")

    wanted = _graduation_years(text)
    if wanted and stud.graduation_year and not wanted[0] <= stud.graduation_year <= wanted[1]:
        asked = str(wanted[0]) if wanted[0] == wanted[1] else f"{wanted[0]}–{wanted[1]}"
        reasons.append(f"For students graduating in {asked} (you graduate in {stud.graduation_year})")

    years = [int(m.group(1)) for m in _EXPERIENCE.finditer(text)]
    if years and min(years) >= 2:
        reasons.append(f"Asks for {min(years)}+ years of work experience")
    return reasons


def posting_text(job: Any) -> str:
    return "\n".join(filter(None, [getattr(job, "role_title", ""), (getattr(job, "description", "") or "")[:8000],
                                   getattr(job, "requirements", None)]))


def intern_level_reasons(job: Any, prefs: dict[str, Any]) -> list[str]:
    """Hard reasons a job (a saved Job or a just-scraped ScrapedJob) isn't an internship for you."""
    if not internships_only(prefs):
        return []
    if not is_internship(job.role_title, getattr(job, "job_type", None)):
        kind = getattr(getattr(job, "job_type", None), "value", None)
        return [f"Not an internship ({kind or 'full-time'} role)" if kind != "internship"
                else "Not an internship (a job about the intern program)"]
    return eligibility_reasons(job.role_title or "", posting_text(job), student(prefs))


def drop_ineligible(jobs: list[Any], prefs: dict[str, Any]) -> tuple[list[Any], int]:
    """(the postings that are internships for you, how many were left out)."""
    if not internships_only(prefs):
        return jobs, 0
    kept = [j for j in jobs if not intern_level_reasons(j, prefs)]
    return kept, len(jobs) - len(kept)


def year_fit(job: Any, prefs: dict[str, Any] | None) -> str | None:
    """ "Open to 2nd-year students" when the posting names your year (Google STEP, "1st and 2nd year students")."""
    if not prefs or not internships_only(prefs):
        return None
    stud = student(prefs)
    if stud.year is None or not names_your_year(posting_text(job), stud):
        return None
    return f"Open to {_ordinal(stud.year)}-year students"
