import io

import pytest

from app.schemas.resume_content import ResumeContent, normalize_resume
from app.services.pdf_generator import render_cover_letter_pdf, render_resume_pdf
from app.services.resume_parser import ResumeParseError, extract_text, heuristic_parse, parse_resume_text
from tests.conftest import SAMPLE_RESUME_TEXT


def test_heuristic_parse_extracts_sections() -> None:
    data = heuristic_parse(SAMPLE_RESUME_TEXT)
    info = data["personal_info"]
    assert info["name"] == "Jane Doe"
    assert info["email"] == "jane.doe@example.com"
    assert info["linkedin"].endswith("linkedin.com/in/janedoe")
    assert [e["company"] for e in data["experience"]] == ["Acme Corp", "Beta Labs"]
    acme = data["experience"][0]
    assert acme["start_date"] == "Jan 2022" and acme["end_date"] == "Present"
    assert acme["location"] == "San Francisco, CA"
    assert len(acme["bullets"]) == 3
    assert data["education"][0]["institution"] == "University of California, Berkeley"
    assert data["education"][0]["gpa"] == "3.8"
    assert data["projects"][0]["name"] == "JobBot"
    assert "Python" in data["skills"]["technical"]


def test_pdf_roundtrip_is_ats_parseable() -> None:
    """Our generated PDFs must re-parse losslessly: clean Unicode bullets, same structure."""
    parsed = heuristic_parse(SAMPLE_RESUME_TEXT)
    for template in ("classic", "modern"):
        pdf = render_resume_pdf(parsed, template=template)
        assert pdf.startswith(b"%PDF")
        text = extract_text("resume.pdf", pdf)
        assert "\x7f" not in text and "• Built REST APIs" in text
        reparsed = heuristic_parse(text)
        assert reparsed["personal_info"]["email"] == "jane.doe@example.com"
        key = lambda r: [(e["title"], e["company"], e["start_date"], e["end_date"], e["location"], e["bullets"])  # noqa: E731
                         for e in r["experience"]]
        assert key(reparsed) == key(parsed)
        assert reparsed["education"][0]["institution"] == "University of California, Berkeley"
        assert reparsed["education"][0]["degree"] == "B.S. Computer Science"


def test_cover_letter_pdf() -> None:
    assert render_cover_letter_pdf("Dear Hiring Team,\n\nHello.\n\nSincerely,\nJane", {"name": "Jane"}).startswith(b"%PDF")


def test_docx_extraction() -> None:
    import docx

    document = docx.Document()
    for line in SAMPLE_RESUME_TEXT.splitlines():
        document.add_paragraph(line)
    buf = io.BytesIO()
    document.save(buf)
    text = extract_text("resume.docx", buf.getvalue())
    assert "Acme Corp" in text


def test_extract_text_errors() -> None:
    with pytest.raises(ResumeParseError):
        extract_text("resume.exe", b"MZ....")
    with pytest.raises(ResumeParseError):
        extract_text("resume.txt", b"too short")


def test_parse_resume_uses_llm_when_available(fake_llm) -> None:
    fake_llm({"RESUME PARSING": {"personal_info": {"name": "Jane Doe", "email": "j@x.com"}, "summary": "s",
                                 "experience": [{"company": "Acme", "title": "SWE", "bullets": ["Did things"]}],
                                 "education": [], "projects": [], "skills": {"technical": ["Python"]},
                                 "certifications": ["AWS SAA"], "awards": [{"name": "Hackathon winner"}]}})
    parsed, method = parse_resume_text(SAMPLE_RESUME_TEXT)
    assert method == "llm"
    assert parsed["certifications"][0]["name"] == "AWS SAA"
    assert parsed["awards"] == ["Hackathon winner"]


def test_normalize_resume_cleans_input() -> None:
    data = normalize_resume({"skills": ["Python", "python", " SQL "], "experience": [{"company": "A", "bullets": "• one\n• two"}]})
    assert data["skills"]["technical"] == ["Python", "SQL"]
    assert data["experience"][0]["bullets"] == ["one", "two"]
    assert ResumeContent.model_validate(data).skills_text().startswith("Python")


def test_pdf_with_one_text_object_per_word_is_rebuilt_into_lines() -> None:
    """Canva / some Docs & LaTeX exports: the plain PDF reader returns one word per line."""
    import io
    import random

    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    from app.services.resume_parser import extract_text, heuristic_parse

    lines = ["Saksham Verma", "Noida | sakshamverma56000@gmail.com | +91 9310312915", "EXPERIENCE",
             "Software Engineering Intern | AxisIQ | Remote Jan 2025 - Mar 2025",
             "• Architected a full-stack async logistics platform using FastAPI and Leaflet",
             "• Engineered a multi-criteria routing engine with weighted disruption penalties",
             "SKILLS", "Python, FastAPI, Docker, Flutter"]
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=letter)
    rnd, y = random.Random(1), 740
    for line in lines:
        x = 50.0
        for word in line.split(" "):
            pdf.drawString(x, y + rnd.choice([-0.6, 0, 0.6, 1.2]), word)  # every word its own object
            x += pdf.stringWidth(word + " ", "Helvetica", 12)
        y -= 18
    pdf.save()

    text = extract_text("resume.pdf", buf.getvalue())
    assert "Software Engineering Intern | AxisIQ | Remote Jan 2025 - Mar 2025" in text
    assert "• Engineered a multi-criteria routing engine with weighted disruption penalties" in text
    parsed = heuristic_parse(text)
    assert parsed["personal_info"]["name"] == "Saksham Verma"
    assert len(parsed["experience"]) == 1 and len(parsed["experience"][0]["bullets"]) == 2


def test_wrapped_bullets_education_table_and_project_stack() -> None:
    from app.services.resume_parser import heuristic_parse

    text = """ALEX KIM
+91 9000000000 | alex@example.com | LinkedIn | GitHub
EDUCATION
Degree / Course Year Institution Score
B.Tech (Computer Science) 2024-Present Example Institute of Technology 9.1 CGPA
CBSE, Class XII 2024 Example Public School, Delhi 92.4%
PROJECTS
Route Planner | Python · FastAPI · Docker | Link
• Architected a routing engine with weighted penalties from real-time telemetry and traffic
feeds to recommend safer routes.
• Built a dashboard for live monitoring.
Crop Doctor | Flutter · TensorFlow Lite
• Deployed an on-device model that classifies leaf diseases for farmers in rural
communities with no connectivity.
ACHIEVEMENTS
• Top 5 — Example Hackathon
• Solved 300+ problems on LeetCode
Example Institute of Technology · Delhi, India
"""
    parsed = heuristic_parse(text)
    assert [p["name"] for p in parsed["projects"]] == ["Route Planner", "Crop Doctor"]
    assert parsed["projects"][0]["technologies"][:3] == ["Python", "FastAPI", "Docker"]
    assert "traffic feeds to recommend safer routes." in parsed["projects"][0]["description"]
    edu = parsed["education"]
    assert [e["degree"] for e in edu] == ["B.Tech (Computer Science)", "CBSE, Class XII"]
    assert edu[0]["institution"] == "Example Institute of Technology" and edu[0]["gpa"] == "9.1 CGPA"
    assert (edu[0]["start_date"], edu[0]["end_date"], edu[1]["end_date"]) == ("2024", "Present", "2024")
    assert parsed["awards"] == ["Top 5 — Example Hackathon", "Solved 300+ problems on LeetCode"]
