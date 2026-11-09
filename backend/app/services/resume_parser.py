"""Master resume ingestion: PDF/DOCX/TXT -> text -> structured JSON (LLM, with heuristic fallback)."""

from __future__ import annotations

import io
import logging
import re
from itertools import pairwise
from typing import Any

from app.schemas.resume_content import ResumeContent, normalize_resume
from app.services import llm_schemas
from app.services.llm import LLMError, get_llm, render_prompt
from app.services.text_utils import SOFT_SKILLS, extract_skills

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".txt", ".md")


class ResumeParseError(Exception):
    pass


# --------------------------------------------------------------------------- text extraction
def extract_text(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf") or data[:4] == b"%PDF":
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n".join(pages)
            if _words_per_line(text) < 2.5:
                # Canva / some Docs & LaTeX exports store every word as its own text object, so the plain
                # reader yields one word per line: rebuild the real lines from word positions instead.
                rebuilt = "\n".join(_positioned_lines(page) for page in reader.pages)
                if _words_per_line(rebuilt) > _words_per_line(text):
                    text = rebuilt
            links = _link_uris(reader)
            if links:  # "LinkedIn" / "GitHub" are often just clickable words: keep their URLs next to the name
                first, _, rest = text.partition("\n")
                text = f"{first}\n{' | '.join(links)}\n{rest}"
        except Exception as exc:
            raise ResumeParseError(f"Could not read PDF: {exc}") from exc
    elif name.endswith(".docx"):
        import docx

        try:
            document = docx.Document(io.BytesIO(data))
        except Exception as exc:
            raise ResumeParseError(f"Could not read DOCX: {exc}") from exc
        lines = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                lines.append(" | ".join(cell.text for cell in row.cells))
        text = "\n".join(lines)
    elif name.endswith((".txt", ".md")) or not name:
        text = data.decode("utf-8", errors="replace")
    else:
        raise ResumeParseError(f"Unsupported file type. Upload one of: {', '.join(SUPPORTED_EXTENSIONS)}")
    text = text.replace("\x00", "")
    # Bullet glyphs from various PDF producers (Symbol/Wingdings private-use, ReportLab std fonts)
    text = re.sub(r"[\x7f\uf0b7\uf0a7\uf076\uf0d8\u25cf\u25aa\u25e6\u2023\u2043]", "•", text)
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", "", text)
    # A bullet glyph alone on its line belongs to the next line
    text = re.sub(r"(?m)^[ \t]*•[ \t]*\n(?=\S)", "• ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 50:
        raise ResumeParseError("Could not extract text from the resume (is it a scanned image?)")
    return text


def _link_uris(reader: Any) -> list[str]:
    uris: list[str] = []
    for page in reader.pages:
        for annot in page.get("/Annots") or []:
            try:
                uri = str(annot.get_object().get("/A", {}).get("/URI") or "").strip()
            except Exception:  # noqa: BLE001 - malformed annotations are just skipped
                continue
            if uri.startswith("http") and uri not in uris and "mailto:" not in uri:
                uris.append(uri)
    return uris[:10]


def _words_per_line(text: str) -> float:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return sum(len(ln.split()) for ln in lines) / len(lines) if lines else 0.0


def _positioned_lines(page: Any) -> str:
    """Group a PDF page's text runs into lines by baseline, left to right, spacing words by their gaps."""
    runs: list[tuple[float, float, float, str]] = []  # (y, x, font_size, text)

    def visit(text: str, cm: Any, tm: Any, _font: Any, size: float) -> None:
        if not text or not text.strip(" \n"):
            if text and " " in text:  # Google Docs writes the space between words as its own run
                text = " "
            else:
                return
        # text-space origin -> user space: tm (text matrix) then cm (current transformation matrix)
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        scale = abs(tm[3] * cm[3]) or 1.0
        runs.append((y, x, (size or 10) * scale, text.replace("\n", " ")))

    page.extract_text(visitor_text=visit)
    lines: list[list[tuple[float, float, float, str]]] = []
    for run in sorted(runs, key=lambda r: (-r[0], r[1])):
        if lines and abs(lines[-1][0][0] - run[0]) <= max(2.0, run[2] * 0.35):
            lines[-1].append(run)
        else:
            lines.append([run])
    out = []
    for line in lines:
        line.sort(key=lambda r: r[1])
        text = line[0][3]
        for prev, cur in pairwise(line):
            # a gap of more than ~a fifth of the font size between runs is a space
            est_end = prev[1] + len(prev[3]) * prev[2] * 0.5
            gap = cur[1] - est_end
            joiner = "" if (text.endswith((" ", "-")) or cur[3].startswith(" ") or gap < prev[2] * 0.15) else " "
            text += joiner + cur[3]
        out.append(re.sub(r"\s+", " ", text).strip())
    return "\n".join(ln for ln in out if ln)


# --------------------------------------------------------------------------- parsing
def parse_resume_text(text: str) -> tuple[dict[str, Any], str]:
    """Return (structured resume, method) where method is 'llm' or 'heuristic'."""
    llm = get_llm()
    if llm.available:
        try:
            data = llm.complete_json(
                render_prompt("resume_parser", resume_text=text),
                schema=llm_schemas.RESUME_SCHEMA,
                effort="low",
                task="resume_parse",
            )
            parsed = normalize_resume(data.get("resume", data))
            if parsed["experience"] or parsed["education"] or parsed["skills"]["technical"]:
                return parsed, "llm"
        except LLMError as exc:
            logger.warning("LLM resume parsing failed, using heuristic parser: %s", exc)
    return heuristic_parse(text), "heuristic"


_SECTION_ALIASES = {
    "summary": ("summary", "professional summary", "profile", "about me", "objective", "career objective"),
    "experience": (
        "experience", "work experience", "professional experience", "employment", "employment history",
        "work history", "relevant experience", "internships", "internship experience",
    ),
    "education": ("education", "academic background", "academics", "education and training"),
    "projects": ("projects", "personal projects", "academic projects", "selected projects", "key projects"),
    "skills": ("skills", "technical skills", "core competencies", "technologies", "skills and tools", "tech stack"),
    "certifications": ("certifications", "certificates", "licenses", "licenses and certifications"),
    "awards": ("awards", "honors", "achievements", "honors and awards", "accomplishments"),
}

_DATE_RANGE = re.compile(
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{4}|\d{1,2}/\d{4}|\d{4})"
    r"\s*(?:-|–|—|to)\s*"
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{4}|\d{1,2}/\d{4}|\d{4}|present|current|now)",
    re.IGNORECASE,
)
_BULLET = re.compile(r"^\s*(?:[•\-\*·▪◦●‣]|\d+\.)\s+")


def _section_of(line: str) -> str | None:
    clean = re.sub(r"[^a-z &]", "", line.lower()).strip()
    if not clean or len(clean) > 40:
        return None
    for section, aliases in _SECTION_ALIASES.items():
        if clean in aliases:
            return section
    return None


def heuristic_parse(text: str) -> dict[str, Any]:
    lines = [ln.strip() for ln in text.splitlines()]
    info: dict[str, str] = {}
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    phone = re.search(r"(\+?\d[\d\s().-]{8,}\d)", text)
    linkedin = re.search(r"(https?://)?(www\.)?linkedin\.com/in/[\w\-%]+/?", text, re.IGNORECASE)
    github = re.search(r"(https?://)?(www\.)?github\.com/[\w\-]+/?", text, re.IGNORECASE)
    if email:
        info["email"] = email.group(0)
    if phone:
        info["phone"] = phone.group(1).strip()
    if linkedin:
        info["linkedin"] = linkedin.group(0) if linkedin.group(0).startswith("http") else "https://" + linkedin.group(0)
    if github:
        info["github"] = github.group(0) if github.group(0).startswith("http") else "https://" + github.group(0)
    for line in lines[:5]:
        if line and not re.search(r"@|\d{3}|http|linkedin|github", line, re.IGNORECASE) and len(line.split()) <= 5:
            info["name"] = line.title() if line.isupper() else line
            break

    sections: dict[str, list[str]] = {k: [] for k in _SECTION_ALIASES}
    current: str | None = None
    for line in lines:
        section = _section_of(line)
        if section:
            current = section
            continue
        if current and line:
            sections[current].append(line)

    for key in sections:
        sections[key] = _join_wrapped(sections[key])
    experience = _parse_entries(sections["experience"], kind="experience")
    education = _education_rows(sections["education"]) or _parse_entries(sections["education"], kind="education")
    projects = []
    for entry in _parse_entries(sections["projects"], kind="experience"):
        description = " ".join(entry["bullets"])
        # "Name | Python · Kotlin · Flutter | Link": the header lists the stack
        stack = [t.strip() for t in re.split(r"[·,/•]|\s\|\s", entry["company"]) if 1 < len(t.strip()) <= 30]
        projects.append(
            {
                "name": entry["title"],
                "description": description,
                "technologies": list(dict.fromkeys(
                    stack + extract_skills(" ".join([description, entry["company"], entry["location"]])))),
                "url": "",
            }
        )
    for key in ("awards", "certifications"):
        if any(_BULLET.match(line) for line in sections[key]):  # drop footers that trail a bulleted list
            sections[key] = [line for line in sections[key] if _BULLET.match(line)]

    skills_text = " ".join(sections["skills"])
    listed = [s.strip() for s in re.split(r"[,;|•\n]|\s{2,}", " , ".join(sections["skills"])) if s.strip()]
    listed = [re.sub(r"^[A-Za-z &/]+:\s*", "", s) for s in listed]
    technical = [s for s in listed if 1 < len(s) <= 40]
    vocab_found = extract_skills(skills_text or text)
    soft = [s for s in vocab_found if s in SOFT_SKILLS]
    if not technical:
        technical = [s for s in vocab_found if s not in soft]

    return normalize_resume(
        {
            "personal_info": info,
            "summary": " ".join(sections["summary"])[:1500],
            "experience": experience,
            "education": education,
            "projects": projects,
            "skills": {"technical": technical, "languages": [], "tools": [], "soft_skills": soft},
            "certifications": [{"name": _BULLET.sub("", c)} for c in sections["certifications"]][:20],
            "awards": [_BULLET.sub("", a) for a in sections["awards"]][:20],
        }
    )


def _join_wrapped(lines: list[str]) -> list[str]:
    """Re-attach the continuation of a bullet that wrapped onto the next line."""
    out: list[str] = []
    for line in lines:
        prev = out[-1] if out else ""
        wrapped = (
            prev and _BULLET.match(prev) and not _BULLET.match(line) and " | " not in line
            and not _DATE_RANGE.search(line)
            and (line[:1].islower() or line[:1] in "(&" or (not re.search(r"[.!?:]$", prev) and line.endswith(".")))
        )
        if wrapped:
            out[-1] = f"{prev} {line}"
        else:
            out.append(line)
    return out


_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_SCORE = re.compile(r"(\d+(?:\.\d+)?\s*(?:/\s*\d+(?:\.\d+)?\s*)?(?:CGPA|GPA|CPI|SGPA|%|percent))\s*$", re.I)


def _education_rows(lines: list[str]) -> list[dict[str, Any]]:
    """Table-style education: "B.Tech (CS) 2025-Present Delhi Technological University 8.8 CGPA" per row."""
    rows = [ln for ln in lines if not (re.search(r"institution|university|school|college", ln, re.I)
                                      and re.search(r"\b(degree|course|year|score)\b", ln, re.I)
                                      and not _YEAR.search(ln))]
    if not rows or any(_BULLET.match(r) or not _YEAR.search(r) for r in rows):
        return []
    entries = []
    for row in rows:
        dates = _DATE_RANGE.search(row)
        match = dates or _YEAR.search(row)
        before, after = row[: match.start()].strip(" |,-–—"), row[match.end():].strip(" |,-–—")
        score = _SCORE.search(after)
        institution = after[: score.start()].strip(" |,-–—") if score else after
        entries.append({
            "institution": institution,
            "degree": before,
            "field": "",
            "gpa": score.group(1).strip() if score else "",
            "start_date": dates.group(1) if dates else "",
            "end_date": dates.group(2) if dates else match.group(0),
            "highlights": [],
        })
    return entries


def _parse_entries(lines: list[str], kind: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        is_bullet = bool(_BULLET.match(line))
        dates = _DATE_RANGE.search(line)
        if (not is_bullet and dates and current is not None and not current["start_date"] and not current["bullets"]):
            # "Jan 2022 – Present | San Francisco, CA" directly under a "Title — Company" header
            rest = _DATE_RANGE.sub("", line).strip(" |,-–—")
            current["start_date"], current["end_date"] = dates.group(1), dates.group(2)
            if rest and not current["location"]:
                current["location"] = rest
            continue
        if not is_bullet and (dates or current is None or (current and current.get("bullets"))):
            header = _DATE_RANGE.sub("", line).strip(" |,-–—")
            if "|" in header:
                pieces = header.split("|")
            elif re.search(r"\s[@–—-]\s| at |\s{2,}", header):
                pieces = re.split(r"\s+[@–—-]\s+| at |\s{2,}", header)
            else:
                pieces = re.split(r",\s(?=[A-Z])", header, maxsplit=1)
            parts = [p.strip() for p in pieces if p.strip()]
            current = {
                "title": parts[0] if parts else header,
                "company": parts[1] if len(parts) > 1 else "",
                "location": parts[2] if len(parts) > 2 else "",
                "start_date": dates.group(1) if dates else "",
                "end_date": dates.group(2) if dates else "",
                "bullets": [],
            }
            entries.append(current)
        elif current is not None:
            if not is_bullet and not current["bullets"] and not current["company"]:
                current["company"] = line
            else:
                current["bullets"].append(_BULLET.sub("", line))
    if kind == "education":
        return [
            {
                "institution": e["title"],
                "degree": e["company"],
                "field": "",
                "gpa": next((re.search(r"gpa[:\s]*([\d.]+)", b, re.I).group(1) for b in e["bullets"] if re.search(r"gpa[:\s]*[\d.]+", b, re.I)), ""),
                "start_date": e["start_date"],
                "end_date": e["end_date"],
                "highlights": e["bullets"],
            }
            for e in entries
        ]
    return entries


def resume_to_text(content: dict[str, Any]) -> str:
    return ResumeContent.model_validate(content).full_text()
