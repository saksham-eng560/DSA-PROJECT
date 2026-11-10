"""One-page resume optimiser: which bullets to keep for this job, solved as a 0/1 knapsack.

Every bullet point is an *item*:

* **weight** - the lines it takes on the page (its length divided by the characters per line);
* **value** - how relevant it is to the job: the job's skills it mentions, shared keywords,
  whether it has a number in it (measurable results read better), plus a small bonus for the
  first bullet of each entry so no role is left empty.

The page is the *knapsack*, with a capacity in lines. Dynamic programming finds the set of
bullets with the highest total value that fits - an exact answer, where the obvious greedy
approach (take the best value-per-line first) can leave lines unused. Both are returned so the
difference is visible.

Nothing is rewritten and nothing is invented: the optimiser only chooses among your own bullets.
A line diff (longest common subsequence) shows what would be dropped.
"""

from __future__ import annotations

import math
import re
from typing import Any

from app.dsa.dp import greedy_knapsack, knapsack
from app.dsa.strings import diff
from app.models.job import Job
from app.schemas.resume_content import ResumeContent
from app.services.job_matcher import job_skills, job_text
from app.services.text_utils import display_skill, extract_skills, tokenize

CHARS_PER_LINE = 95
DEFAULT_LINES = 28  # bullet lines left on a one-page resume after the header, education and skills
_NUMBER = re.compile(r"\d")


def _bullets(resume: ResumeContent) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for entry in resume.experience:
        for position, text in enumerate(entry.bullets):
            items.append({"section": "experience", "entry": f"{entry.title} · {entry.company}".strip(" ·"),
                          "text": text, "first": position == 0})
    for project in resume.projects:
        parts = re.split(r"(?<=[.!?])\s+|\n+", project.description or "")
        for position, text in enumerate(p.strip() for p in parts if p.strip()):
            items.append({"section": "projects", "entry": project.name, "text": text, "first": position == 0,
                          "technologies": project.technologies})
    return items


def optimize(resume_content: dict[str, Any], job: Job, max_lines: int = DEFAULT_LINES) -> dict[str, Any]:
    resume = ResumeContent.model_validate(resume_content)
    wanted = set(job_skills(job))
    job_words = set(tokenize(job_text(job)))
    items = _bullets(resume)
    weights: list[int] = []
    values: list[float] = []
    for item in items:
        text = item["text"]
        mentioned = set(extract_skills(text, item.get("technologies", ()))) & wanted
        words = set(tokenize(text))
        overlap = len(words & job_words) / len(words) if words else 0.0
        value = 3.0 * len(mentioned) + 4.0 * overlap + (1.0 if _NUMBER.search(text) else 0.0) + (1.5 if item["first"] else 0.0)
        item.update(lines=max(1, math.ceil(len(text) / CHARS_PER_LINE)), value=round(value, 2),
                    skills=sorted(display_skill(s) for s in mentioned))
        weights.append(item["lines"])
        values.append(value)

    max_lines = max(1, min(max_lines, 80))
    best_value, chosen = knapsack(weights, values, max_lines)
    greedy_value, greedy_chosen = greedy_knapsack(weights, values, max_lines)
    keep = set(chosen)
    for index, item in enumerate(items):
        item["keep"] = index in keep
    before = [item["text"] for item in items]
    after = [item["text"] for item in items if item["keep"]]
    covered = sorted({skill for item in items if item["keep"] for skill in item["skills"]})
    return {
        "max_lines": max_lines,
        "bullets": items,
        "kept": len(chosen), "dropped": len(items) - len(chosen),
        "lines_used": sum(weights[i] for i in chosen), "lines_total": sum(weights),
        "value": round(best_value, 2), "value_total": round(sum(values), 2),
        "greedy": {"value": round(greedy_value, 2), "kept": len(greedy_chosen),
                   "lines_used": sum(weights[i] for i in greedy_chosen)},
        "skills_covered": covered,
        "skills_wanted": sorted(display_skill(s) for s in wanted),
        "diff": [{"op": op, "text": text} for op, text in diff(before, after)],
        "fits_already": sum(weights) <= max_lines,
        "algorithm": {"name": "0/1 knapsack (dynamic programming)", "time": "O(bullets * lines)",
                      "table_cells": (len(items) + 1) * (max_lines + 1)},
    }
