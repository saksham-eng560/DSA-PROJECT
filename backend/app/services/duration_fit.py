"""How long an internship lasts, and whether that fits the length you want.

``max_internship_months`` (Settings › Search preferences, e.g. 2 for a summer break) skips internships whose
posting states a longer duration: Internshala's "Duration: 6 Months" field, or "a 6-month internship",
"for 12 weeks", "duration of 3 months" in the description. Postings that don't state a length are kept
(most don't), and so is anything when the setting is empty.
"""

from __future__ import annotations

import re
from typing import Any

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8, "ten": 10,
                 "twelve": 12, "a": 1}
_NUM = r"(\d{1,2}(?:\.\d)?|one|two|three|four|five|six|eight|ten|twelve)"
_UNIT = r"(months?|mos?\b|weeks?|wks?\b)"
# A length that is the internship's: "Duration: 3 Months", "6-month internship", "internship for 8 weeks",
# "duration of 2-3 months". Not "3 months of experience" or "paid within 2 months".
_FIELD = re.compile(rf"\bduration\b\s*(?:of|:|-|is)?\s*(?:about\s+|up to\s+)?{_NUM}(?:\s*(?:-|–|to)\s*{_NUM})?\s*{_UNIT}", re.I)
_BEFORE = re.compile(rf"\b{_NUM}(?:\s*(?:-|–|to)\s*{_NUM})?[\s-]*{_UNIT}[\s-]+(?:long\s+|paid\s+|full[\s-]time\s+|remote\s+)*"
                     r"(?:internship|intern\b|program|programme|engagement)", re.I)
_AFTER = re.compile(rf"\b(?:internship|intern\b|program|programme)\b[^.\n;]{{0,40}}?\b(?:for|of|lasting|spanning)\s+"
                    rf"(?:a period of\s+|about\s+|up to\s+)?{_NUM}(?:\s*(?:-|–|to)\s*{_NUM})?\s*{_UNIT}", re.I)


def _months(number: str, unit: str) -> float:
    value = float(_NUMBER_WORDS.get(number.lower(), 0) or number)
    return round(value / 4.345, 1) if unit.lower().startswith("w") else value


def _from_match(m: re.Match[str]) -> float:
    numbers = [g for g in m.groups()[:-1] if g]
    unit = m.groups()[-1]
    return min(_months(n, unit) for n in numbers)  # "2-3 months": the shortest it can be


def duration_months(job: Any) -> float | None:
    """The internship's length in months as the posting states it (the shortest option), else None."""
    raw = getattr(job, "raw_data", None) or getattr(job, "raw", None) or {}
    field = str(raw.get("duration") or "")
    m = re.search(rf"{_NUM}\s*{_UNIT}", field, re.I)
    if m:
        return _months(m.group(1), m.group(2))
    text = "\n".join(filter(None, [getattr(job, "role_title", "") or "", (getattr(job, "description", "") or "")[:6000]]))
    for pattern in (_FIELD, _BEFORE, _AFTER):
        m = pattern.search(text)
        if m:
            return _from_match(m)
    return None


def max_months(prefs: dict[str, Any]) -> float | None:
    try:
        value = float(prefs.get("max_internship_months") or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _fmt(months: float) -> str:
    return f"{months:g} month{'s' if months != 1 else ''}"


def duration_reasons(job: Any, prefs: dict[str, Any]) -> list[str]:
    """Hard reason a job is longer than the internship length you want (empty: it fits or doesn't say)."""
    limit = max_months(prefs)
    if limit is None:
        return []
    months = duration_months(job)
    if months is not None and months > limit:
        return [f"{_fmt(months)} internship (you want at most {_fmt(limit)})"]
    return []


def drop_too_long(jobs: list[Any], prefs: dict[str, Any]) -> tuple[list[Any], int]:
    """(the postings that fit your internship length, how many were left out)."""
    if max_months(prefs) is None:
        return jobs, 0
    kept = [j for j in jobs if not duration_reasons(j, prefs)]
    return kept, len(jobs) - len(kept)
