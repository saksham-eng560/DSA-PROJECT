"""Render resume JSON into a clean, ATS-parseable PDF (single column, no tables/graphics)."""

from __future__ import annotations

import io
import os
from typing import Any
from xml.sax.saxutils import escape

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer

from app.schemas.resume_content import ResumeContent

TEMPLATES = ("classic", "modern")
ACCENTS = {"classic": colors.HexColor("#111827"), "modern": colors.HexColor("#1d4ed8")}

# Embedded TrueType (Bitstream Vera, bundled with ReportLab) so every character — bullets, dashes,
# accents — extracts as real Unicode text. The 14 standard PDF fonts mangle "•" for ATS parsers.
FONT, FONT_BOLD = "AAVera", "AAVera-Bold"
_fonts_registered = False


def _register_fonts() -> None:
    global _fonts_registered
    if _fonts_registered:
        return
    font_dir = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
    pdfmetrics.registerFont(TTFont(FONT, os.path.join(font_dir, "Vera.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, os.path.join(font_dir, "VeraBd.ttf")))
    pdfmetrics.registerFont(TTFont("AAVera-Italic", os.path.join(font_dir, "VeraIt.ttf")))
    pdfmetrics.registerFont(TTFont("AAVera-BoldItalic", os.path.join(font_dir, "VeraBI.ttf")))
    pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT_BOLD, italic="AAVera-Italic", boldItalic="AAVera-BoldItalic")
    _fonts_registered = True


def _styles(template: str) -> dict[str, ParagraphStyle]:
    _register_fonts()
    base = getSampleStyleSheet()
    accent = ACCENTS.get(template, ACCENTS["classic"])
    font, bold = FONT, FONT_BOLD
    return {
        "name": ParagraphStyle("name", parent=base["Title"], fontName=bold, fontSize=20, leading=24,
                               alignment=TA_CENTER, textColor=accent, spaceAfter=2),
        "contact": ParagraphStyle("contact", parent=base["Normal"], fontName=font, fontSize=9.5, leading=12,
                                  alignment=TA_CENTER, textColor=colors.HexColor("#374151")),
        "section": ParagraphStyle("section", parent=base["Heading2"], fontName=bold, fontSize=11.5, leading=14,
                                  textColor=accent, spaceBefore=9, spaceAfter=2),
        "entry": ParagraphStyle("entry", parent=base["Normal"], fontName=bold, fontSize=10.5, leading=13),
        "meta": ParagraphStyle("meta", parent=base["Normal"], fontName=font, fontSize=9.5, leading=12,
                               textColor=colors.HexColor("#4b5563")),
        "body": ParagraphStyle("body", parent=base["Normal"], fontName=font, fontSize=10, leading=13),
        "bullet": ParagraphStyle("bullet", parent=base["Normal"], fontName=font, fontSize=9.5, leading=12.5,
                                 leftIndent=14, bulletIndent=3, bulletFontName=font, spaceAfter=1),
    }


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text or ""), style)


def _dates(start: str, end: str) -> str:
    if start and end:
        return f"{start} – {end}"
    return start or end or ""


def render_resume_pdf(content: dict[str, Any], template: str = "classic", page_size: str = "letter") -> bytes:
    resume = ResumeContent.model_validate(content or {})
    st = _styles(template)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4 if page_size.lower() == "a4" else LETTER,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
        title=f"{resume.personal_info.name or 'Resume'} - Resume",
        author=resume.personal_info.name or "",
    )
    story: list[Any] = []
    info = resume.personal_info
    story.append(_p(info.name or "Resume", st["name"]))
    contact = [v for v in (info.location, info.email, info.phone, info.linkedin, info.github, info.portfolio) if v]
    if contact:
        story.append(_p("  |  ".join(contact), st["contact"]))

    def section(title: str) -> None:
        story.append(_p(title.upper(), st["section"]))
        story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#9ca3af"), spaceAfter=4))

    def bullets(items: list[str]) -> list[Paragraph]:
        return [Paragraph(escape(b), st["bullet"], bulletText="•") for b in items]

    if resume.summary:
        section("Summary")
        story.append(_p(resume.summary, st["body"]))

    if resume.experience:
        section("Experience")
        for exp in resume.experience:
            head = " — ".join(v for v in (exp.title, exp.company) if v)
            meta = "  |  ".join(v for v in (_dates(exp.start_date, exp.end_date), exp.location) if v)
            header: list[Any] = [_p(head, st["entry"])]
            if meta:
                header.append(_p(meta, st["meta"]))
            items = bullets(exp.bullets)
            # Keep the heading with its first bullet; the rest may flow onto the next page.
            story.append(KeepTogether(header + items[:1]))
            story.extend(items[1:])
            story.append(Spacer(1, 4))

    if resume.projects:
        section("Projects")
        for proj in resume.projects:
            head = proj.name + (f" ({proj.url})" if proj.url else "")
            block = [_p(head, st["entry"])]
            if proj.description:
                block.append(_p(proj.description, st["body"]))
            if proj.technologies:
                block.append(_p("Technologies: " + ", ".join(proj.technologies), st["meta"]))
            block.append(Spacer(1, 4))
            story.append(KeepTogether(block))

    if resume.education:
        section("Education")
        for edu in resume.education:
            degree = ", ".join(v for v in (edu.degree, edu.field) if v)
            head = " — ".join(v for v in (edu.institution, degree) if v)
            meta = "  |  ".join(v for v in (_dates(edu.start_date, edu.end_date), f"GPA: {edu.gpa}" if edu.gpa else "") if v)
            block = [_p(head, st["entry"])]
            if meta:
                block.append(_p(meta, st["meta"]))
            highlights = [h for h in edu.highlights if not h.lower().startswith("gpa")]
            if highlights:
                block.extend(bullets(highlights))
            block.append(Spacer(1, 4))
            story.append(KeepTogether(block))

    skills = resume.skills
    skill_rows = [
        ("Technical", skills.technical),
        ("Tools", skills.tools),
        ("Languages", skills.languages),
        ("Soft skills", skills.soft_skills),
    ]
    if any(values for _, values in skill_rows):
        section("Skills")
        for label, values in skill_rows:
            if values:
                story.append(Paragraph(f"<b>{escape(label)}:</b> {escape(', '.join(values))}", st["body"]))

    if resume.certifications:
        section("Certifications")
        story.extend(bullets([
            " — ".join(v for v in (c.name, c.issuer, c.date) if v) for c in resume.certifications
        ]))

    if resume.awards:
        section("Awards")
        story.extend(bullets(resume.awards))

    doc.build(story)
    return buf.getvalue()


def render_cover_letter_pdf(text: str, candidate: dict[str, Any] | None = None) -> bytes:
    st = _styles("classic")
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=inch, rightMargin=inch, topMargin=inch, bottomMargin=inch,
                            title="Cover Letter")
    story: list[Any] = []
    if candidate and candidate.get("name"):
        story.append(_p(candidate["name"], st["entry"]))
        contact = [candidate.get(k) for k in ("email", "phone", "location") if candidate.get(k)]
        if contact:
            story.append(_p("  |  ".join(contact), st["meta"]))
        story.append(Spacer(1, 16))
    for para in (text or "").split("\n\n"):
        story.append(Paragraph(escape(para).replace("\n", "<br/>"), st["body"]))
        story.append(Spacer(1, 10))
    doc.build(story)
    return buf.getvalue()
