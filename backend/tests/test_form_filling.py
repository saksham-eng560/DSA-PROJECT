"""Form filling in a real browser: an Indian internship form with the fields that trip up naive fillers
(country / dial-code dropdowns, date and number inputs, length limits, a masked phone input, a follow-up
question that only appears after an answer), plus the pure helpers behind it."""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest

from app.services.question_answerer import answer_questions, choose_option
from app.services.text_utils import clip
from app.submitters.base import BaseSubmitter, CandidatePacket
from app.submitters.form_engine import FormField, coerce_value, parse_when, value_stuck
from tests.test_e2e_pipeline import _chromium_available, parse_form

RESUME = {
    "personal_info": {"name": "Aarav Sharma", "email": "aarav@example.com", "phone": "+91 98100 12345",
                      "location": "New Delhi, Delhi, India", "linkedin": "https://linkedin.com/in/aarav",
                      "github": "https://github.com/aarav"},
    "summary": "Computer science student who builds Python backends.",
    "education": [{"institution": "Indian Institute of Technology Delhi", "degree": "B.Tech Computer Science",
                   "field": "Computer Science", "gpa": "8.9", "start_date": "2024", "end_date": "2028"}],
    "experience": [{"company": "Zomato", "title": "Software Engineering Intern", "start_date": "May 2026", "end_date": "Jul 2026"}],
    "skills": {"technical": ["Python", "FastAPI", "PostgreSQL"]},
}

FORM = """<!doctype html><html><body><h1>Summer 2027 Internship: Apply</h1>
<form method="post" action="/submit">
<label for="name">Full Name *</label><input id="name" name="name" required>
<label for="email">Email ID *</label><input id="email" name="email" type="email" required>
<label for="cc">Country code *</label><select id="cc" name="cc" required><option value="">Select</option>
  <option>+1 (United States)</option><option>+44 (United Kingdom)</option><option>+91 (India)</option></select>
<label for="mobile">Mobile Number *</label>
<input id="mobile" name="mobile" type="tel" required
  oninput="const d=this.value.replace(/\\D/g,'').slice(-10); this.value=d.length>5 ? d.slice(0,5)+' '+d.slice(5) : d">
<label for="country">Country *</label><select id="country" name="country" required><option value="">Select</option>
  <option>Afghanistan</option><option>British Indian Ocean Territory</option><option>India</option><option>Indonesia</option></select>
<label for="city">City *</label><input id="city" name="city" required>
<label for="start">Earliest start date *</label><input id="start" name="start" type="date" required>
<label for="stipend">Expected stipend (per month) *</label><input id="stipend" name="stipend" type="number" required>
<label for="college">College / University *</label><input id="college" name="college" maxlength="30" required>
<label for="degree">Degree *</label><select id="degree" name="degree" required><option value="">Select</option>
  <option>Diploma</option><option>B.Sc / B.A.</option><option>B.Tech / B.E.</option><option>M.Tech</option></select>
<label for="grad">Graduation year *</label><select id="grad" name="grad" required><option value="">Select</option>
  <option>2026</option><option>2027</option><option>2028</option><option>2029</option></select>
<fieldset><legend>Do you have a LinkedIn profile? *</legend>
  <label><input type="radio" name="has_li" value="yes" required
    onchange="document.getElementById('li-box').style.display='block'"> Yes</label>
  <label><input type="radio" name="has_li" value="no"> No</label></fieldset>
<div id="li-box" style="display:none"><label for="li">LinkedIn URL</label><input id="li" name="li"></div>
<label for="why">Why do you want this internship? *</label><textarea id="why" name="why" maxlength="160" required></textarea>
<label for="dob">Date of birth</label><input id="dob" name="dob" placeholder="DD/MM/YYYY">
<button type="submit">Submit application</button>
</form></body></html>"""


class _Site:
    def __init__(self) -> None:
        self.submissions: list[dict] = []
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, body: str) -> None:
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                self._send(FORM)

            def do_POST(self) -> None:
                site.submissions.append(parse_form(self))
                self._send("<html><body><h1>Thank you for applying!</h1><p>Application submitted.</p></body></html>")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/apply"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def site() -> Iterator[_Site]:
    s = _Site()
    yield s
    s.server.shutdown()


@pytest.mark.e2e
@pytest.mark.skipif(not _chromium_available(), reason="Chromium not available")
def test_indian_internship_form_is_filled_correctly(site: _Site) -> None:
    prefs = {"salary_min": 900000, "salary_currency": "INR"}  # a yearly range never answers a monthly stipend
    packet = CandidatePacket(
        first_name="Aarav", last_name="Sharma", email="aarav@example.com", phone="+91 98100 12345",
        location="New Delhi, Delhi, India", linkedin="https://linkedin.com/in/aarav", github="https://github.com/aarav",
        application_url=site.url, company_name="Acme", role_title="Software Engineering Intern",
        resolve_answers=lambda qs: answer_questions(qs, RESUME, prefs, {"expected_stipend": "15000"}, use_llm=False),
    )
    result = BaseSubmitter().submit(packet)
    assert result.stage == "submitted", (result.error, [f for f in result.fields if f["status"] != "filled"])
    sent = site.submissions[0]
    assert sent["name"] == "Aarav Sharma" and sent["email"] == "aarav@example.com"
    assert sent["cc"] == "+91 (India)"
    assert sent["mobile"] == "98100 12345"  # the page reformatted it; still counted as filled
    assert sent["country"] == "India"  # not "British Indian Ocean Territory"
    assert sent["city"] == "New Delhi"
    assert date.fromisoformat(sent["start"]) == date.today() + timedelta(days=14)  # "2 weeks" as a real date
    assert sent["stipend"] == "15000"
    assert len(sent["college"]) <= 30 and sent["college"].startswith("Indian Institute of")
    assert sent["degree"] == "B.Tech / B.E." and sent["grad"] == "2028"
    assert sent["has_li"] == "yes" and sent["li"] == "https://linkedin.com/in/aarav"  # revealed, then filled
    assert 0 < len(sent["why"]) <= 160
    assert sent.get("dob", "") == ""  # never guessed
    labels = {f["label"]: f["status"] for f in result.fields}
    assert labels["LinkedIn URL"] == "filled"


# ------------------------------------------------------------------ helpers
def test_choose_option_matches_the_way_forms_word_things() -> None:
    countries = ["Afghanistan", "British Indian Ocean Territory (+246)", "India (+91)", "Indonesia"]
    assert choose_option("India", countries) == "India (+91)"
    assert choose_option("+91", ["+1", "+44", "+91"]) == "+91"
    assert choose_option("B.Tech", ["High School", "Bachelor's Degree", "Master's Degree"]) == "Bachelor's Degree"
    assert choose_option("Bachelor of Technology", ["Diploma", "B.Tech / B.E.", "MBA"]) == "B.Tech / B.E."
    assert choose_option("No", ["Yes, I will need sponsorship", "No, I will not need sponsorship"]) == "No, I will not need sponsorship"
    assert choose_option("USA", ["Canada", "United States", "United Kingdom"]) == "United States"
    assert choose_option("Bangalore", ["Bengaluru", "Delhi NCR"]) == "Bengaluru"
    assert choose_option("2 weeks", ["Immediately", "Within 2 weeks", "1 month"]) == "Within 2 weeks"
    assert choose_option("PhD", ["Bachelor's", "Master's", "Doctorate"]) == "Doctorate"
    # No good match: the answer comes back unchanged, so the field is left for you instead of guessed
    assert choose_option("Job board", ["LinkedIn", "Referral", "Company website"]) == "Job board"


def test_dates_numbers_and_limits() -> None:
    today = date(2026, 9, 30)
    assert parse_when("2 weeks", today) == date(2026, 10, 14)
    assert parse_when("Immediately", today) == today
    assert parse_when("15/06/2027", today) == date(2027, 6, 15)
    assert parse_when("June 2027", today) == date(2027, 6, 1)
    field = lambda **kw: FormField(handle="aa-0", tag="input", label=kw.pop("label", "x"), **kw)  # noqa: E731
    assert coerce_value(field(type="number"), "₹15,000 per month") == "15000"
    assert coerce_value(field(type="number"), "two") is None
    assert coerce_value(field(type="month"), "2027-06-01") == "2027-06"
    assert coerce_value(field(type="text", label="Date of joining", placeholder="DD/MM/YYYY"), "2027-06-01") == "01/06/2027"
    assert len(coerce_value(field(type="text", max_length=20), "A very long answer that keeps going on") or "") <= 20
    assert clip("First sentence here. Second one is long.", 30) == "First sentence here."
    tel = field(type="tel", label="Mobile")
    assert value_stuck(tel, "98100 12345", "+91 98100 12345")
    assert not value_stuck(field(type="text"), "", "hello")


def test_resume_facts_answer_education_and_location_questions() -> None:
    questions = [{"question": q} for q in (
        "University name", "Degree", "Major / field of study", "Graduation year", "CGPA", "City", "Country",
        "Country code", "Date of birth")]
    answers = {a["question"]: a for a in answer_questions(questions, RESUME, {}, {}, use_llm=False)}
    assert answers["University name"]["answer"] == "Indian Institute of Technology Delhi"
    assert answers["Degree"]["answer"] == "B.Tech Computer Science"
    assert answers["Major / field of study"]["answer"] == "Computer Science"
    assert answers["Graduation year"]["answer"] == "2028"
    assert answers["CGPA"]["answer"] == "8.9"
    assert answers["City"]["answer"] == "New Delhi" and answers["Country"]["answer"] == "India"
    assert answers["Country code"]["answer"] == "+91"
    assert answers["Date of birth"]["answer"] == "" and answers["Date of birth"]["needs_user_review"]
    # A yes/no question about a resume fact is not answered with the fact itself
    yes_no = answer_questions([{"question": "Do you have a degree in Computer Science?", "options": ["Yes", "No"]}],
                              RESUME, {}, {}, use_llm=False)[0]
    assert yes_no["answer"] in ("Yes", "No")


def test_ambiguous_dates_follow_the_box_format() -> None:
    today = date(2026, 10, 1)
    assert parse_when("01/02/2004", today) == date(2004, 2, 1)  # day first by default (India, UK, most forms)
    assert parse_when("01/02/2004", today, day_first=False) == date(2004, 1, 2)
    assert parse_when("06/15/2027", today) == date(2027, 6, 15)  # can only be month first
    box = lambda placeholder: FormField(handle="aa-0", tag="input", type="text", label="Date of birth",  # noqa: E731
                                        placeholder=placeholder)
    assert coerce_value(box("DD/MM/YYYY"), "01/02/2004") == "01/02/2004"  # already in the box's format: typed as is
    assert coerce_value(box("DD/MM/YYYY"), "2004-02-01") == "01/02/2004"
    assert coerce_value(box("MM/DD/YYYY"), "2004-02-01") == "02/01/2004"
    assert coerce_value(box("YYYY-MM-DD"), "01/02/2004") == "2004-02-01"
