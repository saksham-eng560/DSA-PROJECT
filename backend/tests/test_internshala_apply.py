"""Internshala apply bot: a local mock of Internshala's pages (easy-apply modal with a Quill-style editor,
availability radios with hidden inputs, custom questions, the resume interstitial, external listings,
"Already Applied", closed listings, the login redirect and the profile gate), the session-sync API and the
orchestrator wiring (blocker, staging, auto-submit, session expiry, daily limit)."""

from __future__ import annotations

import http.server
import json
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from app.automation.browser import BrowserSession, BrowserUnavailable
from app.core.database import SessionLocal
from app.models.application import Application, ApplicationStatusHistory
from app.models.enums import ApplicationStatus, ATSPlatform, JobType
from app.models.job import Job
from app.models.user import Notification, User, UserFieldMapping
from app.services import agent_orchestrator as orch
from app.services.question_answerer import answer_questions
from app.submitters import CandidatePacket, InternshalaSubmitter, SubmissionResult
from app.submitters import internshala_apply as ia
from app.worker.dispatch import run_inline
from tests.test_e2e_pipeline import _chromium_available, parse_form

CHROMIUM = _chromium_available()
needs_browser = pytest.mark.skipif(not CHROMIUM, reason="Chromium not available")

RESUME = {
    "personal_info": {"name": "Aarav Sharma", "email": "aarav@example.com", "phone": "+91 98100 12345",
                      "location": "New Delhi, Delhi, India"},
    "summary": "Computer science student who builds Python backends with FastAPI.",
    "education": [{"institution": "IIT Delhi", "degree": "B.Tech Computer Science", "field": "Computer Science",
                   "gpa": "8.9", "start_date": "2024", "end_date": "2028"}],
    "skills": {"technical": ["Python", "FastAPI", "PostgreSQL"]},
}
COVER_LETTER = ("Dear Hiring Manager,\n\nI am excited to apply for the Python Development internship at Acme Labs.\n"
                "I have built FastAPI services and enjoy backend work.\n\nI would love to contribute to your team.\n\n"
                "Sincerely,\nAarav Sharma")
GOOD = "good-session"
# Your Chrome's navigator.userAgent (not one of the bot's own browser identities)
MAC_CHROME = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
SESSION = [  # as the extension sends them (chrome.cookies.getAll)
    {"name": "PHPSESSID", "value": GOOD, "domain": "internshala.com", "path": "/", "secure": True, "httpOnly": True,
     "sameSite": "unspecified", "hostOnly": True},
    {"name": "l", "value": "remember-me-token", "domain": "internshala.com", "path": "/", "secure": True, "httpOnly": True,
     "sameSite": "lax", "hostOnly": True, "expirationDate": time.time() + 400 * 86400},
    {"name": "csrf_cookie_name", "value": "csrf123", "domain": "internshala.com", "path": "/", "secure": True,
     "httpOnly": True, "sameSite": "strict", "hostOnly": True, "expirationDate": time.time() + 86400},
    {"name": "is_logged_in", "value": "1", "domain": ".internshala.com", "path": "/", "secure": True, "httpOnly": False,
     "sameSite": "no_restriction", "hostOnly": False, "expirationDate": time.time() + 86400},
]

# ------------------------------------------------------------------------------------------- mock pages
STYLE = """<style>
.modal{display:none;position:fixed;inset:0;background:#fff;overflow:auto;padding:16px}
.modal.show{display:block}
.hidden-input{display:none}
.ql-editor{min-height:120px;border:1px solid #ccc}
.ql-clipboard{position:absolute;left:-100000px;height:1px;overflow-y:hidden}
.chosen-drop{position:absolute;left:-9999px}
.error{display:none;color:red}
</style>"""
HEADER = '<header><div class="profile_icon_right">AS</div></header>'


def _form(action: str, *, questions: bool = True, modal: bool = True) -> str:
    extra = """
    <div class="form-group additional_question">
      <div class="assessment_question"><label for="custom_question_text_101">Describe a Python project you built and your role in it.</label></div>
      <textarea id="custom_question_text_101" class="custom-question-answer" name="custom_question_text_101" maxlength="300"></textarea>
    </div>
    <div class="form-group additional_question">
      <div class="assessment_question"><label>Do you have a laptop with a working internet connection?</label></div>
      <div class="custom_question_boolean_container radio_group">
        <div class="radio"><input type="radio" class="hidden-input" id="cq_2_yes" name="custom_question_radio_2" value="yes"><label for="cq_2_yes">Yes</label></div>
        <div class="radio"><input type="radio" class="hidden-input" id="cq_2_no" name="custom_question_radio_2" value="no"><label for="cq_2_no">No</label></div>
      </div>
    </div>
    <div class="form-group additional_question">
      <div class="assessment_question"><label for="cq_3">What is your expected stipend per month (in INR)?</label></div>
      <input type="number" id="cq_3" name="custom_question_number_3">
    </div>
    <div class="form-group additional_question">
      <div class="assessment_question"><label>Rate your proficiency in Python</label></div>
      <select class="custom_question_range" name="custom_question_range_4" id="cq_4" style="display:none">
        <option value="">Select</option><option value="1">1</option><option value="2">2</option><option value="3">3</option>
        <option value="4">4</option><option value="5">5</option></select>
      <div class="chosen-container"><a class="chosen-single"><span>Select</span></a>
        <div class="chosen-drop"><div class="chosen-search"><input type="text" autocomplete="off"></div></div></div>
    </div>
    <div class="form-group">
      <input type="checkbox" class="hidden-input" id="location_single" name="location_single" value="yes">
      <label for="location_single">I am willing to relocate to Delhi for this internship</label>
    </div>""" if questions else ""
    required = (["cover_letter", "confirm_availability", "custom_question_text_101", "custom_question_radio_2",
                 "custom_question_number_3", "custom_question_range_4"] if questions else ["cover_letter", "confirm_availability"])
    submit_js = (f"""await fetch('{action}', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(data)}});
        form.style.display = 'none';
        document.getElementById('continue_container').style.display = 'block';""" if modal else "HTMLFormElement.prototype.submit.call(form);")
    return f"""
<form id="application-form" {'' if modal else f'method="post" action="{action}"'}>
  <div class="form-group">
    <h4>Cover letter</h4>
    <label>Why should you be hired for this role?</label>
    <div id="cover_letter_holder"><div class="ql-toolbar"></div>
      <div class="ql-container"><div class="ql-editor" contenteditable="true"><p><br></p></div>
      <div class="ql-clipboard" contenteditable="true" tabindex="-1"></div></div></div>
    <textarea id="cover_letter" name="cover_letter" style="display:none"></textarea>
  </div>
  <div id="confirm_availability_container">
    <div class="availability_heading">Confirm your availability</div>
    <div class="radio"><input type="radio" class="hidden-input" id="radio1" name="confirm_availability" value="yes" checked><label for="radio1">Yes, I am available to join immediately</label></div>
    <div class="radio"><input type="radio" class="hidden-input" id="radio2" name="confirm_availability" value="notice_current"><label for="radio2">No, I am currently on notice period</label></div>
    <div class="radio"><input type="radio" class="hidden-input" id="radio3" name="confirm_availability" value="notice_serve"><label for="radio3">No, I will have to serve notice period</label></div>
    <div class="radio"><input type="radio" class="hidden-input" id="radio4" name="confirm_availability" value="other"><label for="radio4">Other (Please specify your availability)</label></div>
    <textarea id="confirm_availability_textarea" name="availability_text" style="display:none"></textarea>
  </div>{extra}
  <div class="custom-resume-container"><span>Your Internshala resume will be attached</span>
    <input type="file" id="custom_resume" name="custom_resume" style="display:none"></div>
  <p class="error" id="required_error">This field is required</p>
  <div class="easy_apply_footer"><input type="submit" id="submit" value="Submit" class="btn btn-primary"></div>
</form>
<div id="continue_container" style="display:none"><h3>Application submitted successfully</h3><button>Continue</button></div>
<script>
  const editor = document.querySelector('.ql-editor');
  const sync = () => {{ document.getElementById('cover_letter').value = editor.innerText.trim(); }};
  editor.addEventListener('input', sync);
  new MutationObserver(sync).observe(editor, {{childList: true, subtree: true, characterData: true}});
  document.querySelectorAll('input[name=confirm_availability]').forEach((r) => r.addEventListener('change', () => {{
    document.getElementById('confirm_availability_textarea').style.display = document.getElementById('radio4').checked ? 'block' : 'none';
  }}));
  const range = document.getElementById('cq_4');
  if (range) range.addEventListener('change', (e) => {{
    document.querySelector('.chosen-single span').textContent = e.target.options[e.target.selectedIndex].text; }});
  document.getElementById('application-form').addEventListener('submit', async (ev) => {{
    ev.preventDefault();
    const form = document.getElementById('application-form');
    const data = Object.fromEntries(new FormData(form).entries());
    delete data.custom_resume;
    const missing = {json.dumps(required)}.filter((k) => !data[k] || !String(data[k]).trim());
    if (data.confirm_availability === 'other' && !(data.availability_text || '').trim()) missing.push('availability_text');
    if (missing.length) {{ document.getElementById('required_error').style.display = 'block'; return; }}
    {submit_js}
  }});
</script>"""


def _detail(body: str, title: str = "Python Development") -> str:
    return (f"<!doctype html><html><head><title>{title}</title>{STYLE}</head><body>{HEADER}"
            f"<h1 class='profile_on_detail_page'>{title}</h1><div class='company_name'><a>Acme Labs</a></div>{body}</body></html>")


def easy_detail(slug: str) -> str:
    return _detail(f"""
<div class="top_apply_now_cta"><button id="top_easy_apply_button" class="btn btn-primary"
  onclick="document.getElementById('easy_apply_modal').classList.add('show')">Apply now</button></div>
<div id="easy_apply_modal" class="modal"><button id="easy_apply_modal_close">×</button>
{_form(f'/application/submit/{slug}')}</div>""")


PAGES: dict[str, str] = {
    "/internship/detail/redirect-1": _detail(
        '<a class="top_apply_now_cta btn" href="/student/resume?detail_source=resume_intermediate&id=redirect-1">Apply now</a>'),
    "/student/resume": (f"<!doctype html><html><head>{STYLE}</head><body>{HEADER}<h2>Review your resume</h2>"
                        "<div id='layout_table'><div class='proceed-btn-container'><button class='proceed-btn' "
                        "onclick=\"location.href='/application/form/redirect-1'\">Proceed to application</button></div></div></body></html>"),
    "/application/form/redirect-1": (f"<!doctype html><html><head>{STYLE}</head><body>{HEADER}<h2>Application</h2>"
                                     f"{_form('/application/submit/redirect-1', questions=False, modal=False)}</body></html>"),
    "/student/applications": f"<!doctype html><html><body>{HEADER}<h2>My applications</h2><p>Python Development — Applied</p></body></html>",
    "/internship/detail/external-1": _detail("""
<button id="easy_apply_button" class="btn" onclick="document.getElementById('ext').classList.add('show')">Apply now</button>
<div id="ext" class="modal"><div class="modal-content"><button class="close" onclick="document.getElementById('ext').classList.remove('show')">×</button>
<p>You will be redirected to another website</p><a class="proceed-cta" href="https://careers.example.com/apply/123" target="_blank">Proceed</a></div></div>"""),
    "/internship/detail/applied-1": _detail('<button class="btn apply_now_btn" disabled>Already Applied</button>'),
    "/internship/detail/closed-1": _detail('<div class="closed_message">Applications are closed for this internship</div>'),
    "/internship/detail/gate-1": _detail('<a class="top_apply_now_cta btn" href="/student/personal_details?next=gate-1">Apply now</a>'),
    "/student/personal_details": f"<!doctype html><html><body>{HEADER}<h2>Complete your profile to apply</h2><form><input name='city'></form></body></html>",
    "/student/dashboard": f"<!doctype html><html><body>{HEADER}<h2>Dashboard</h2></body></html>",
    "/login/student": "<!doctype html><html><body><form id='login-form'><input name='email'><input name='password' type='password'></form></body></html>",
    "/registration/student_details": ("<!doctype html><html><body><h1>Complete your profile</h1><form><input name='college'>"
                                      "</form></body></html>"),
    # What a logged-out visitor's "Apply now" opens (the page in the bug report)
    "/registration/student": ("<!doctype html><html><body><h1>Sign-up and apply for free</h1><p>3,00,000+ companies hiring on "
                              "Internshala</p><div class='card'><h4>Candidate sign up</h4><button>Sign up with Google</button>"
                              "<button>Sign up with Email</button><p>Already registered? <a href='/login/student'>Login</a></p>"
                              "</div></body></html>"),
}


class MockInternshala:
    def __init__(self) -> None:
        self.submissions: list[tuple[str, dict]] = []
        self.requests: list[str] = []
        self.agents: list[str] = []  # the User-Agent of every page request
        self.bound_ua: str | None = None  # set: the login only works from this browser (like a UA-bound session)
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, body: str, status: int = 200, headers: dict[str, str] | None = None) -> None:
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def _logged_in(self) -> bool:
                cookies = dict(p.strip().split("=", 1) for p in (self.headers.get("Cookie") or "").split(";") if "=" in p)
                ua_ok = site.bound_ua is None or self.headers.get("User-Agent") == site.bound_ua
                return cookies.get("PHPSESSID") == GOOD and ua_ok

            def do_GET(self) -> None:
                path = urlparse(self.path).path
                site.requests.append(path)
                site.agents.append(self.headers.get("User-Agent") or "")
                if path.startswith("/login"):
                    return self._send(PAGES["/login/student"])
                if path == "/registration/student_details" and self._logged_in():  # finish-your-profile step
                    return self._send(PAGES["/registration/student_details"])
                if path.startswith("/registration"):
                    return self._send(PAGES["/registration/student"])
                if path.startswith("/internship/detail/public-") and not self._logged_in():  # listings are public
                    return self._send(_detail(f'<a class="top_apply_now_cta btn" href="/registration/student?redirect={path}">Apply now</a>'))
                if not self._logged_in():
                    return self._send("", 302, {"Location": f"/login/student?redirect={path}"})
                if path.startswith(("/internship/detail/easy-", "/internship/detail/public-")):
                    return self._send(easy_detail(path.rsplit("/", 1)[-1]))
                if path.startswith("/internship/detail/footer-"):  # sign-up text on a page you're logged into
                    return self._send(easy_detail(path.rsplit("/", 1)[-1]).replace(
                        "</body>", "<footer><p>Already registered? <a href='/login/student'>Login</a></p></footer></body>"))
                if path.startswith("/internship/detail/profile-"):
                    return self._send(_detail('<a class="top_apply_now_cta btn" href="/registration/student_details?next=1">Apply now</a>'))
                if path in PAGES:
                    return self._send(PAGES[path])
                return self._send("not found", 404)

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                if not self._logged_in():
                    return self._send("", 302, {"Location": "/login/student"})
                if self.headers.get("Content-Type", "").startswith("application/json"):
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                    site.submissions.append((path, body))
                    return self._send('{"success": true}')
                site.submissions.append((path, parse_form(self)))
                return self._send("", 303, {"Location": "/student/applications"})

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def local_session(**kwargs: Any) -> BrowserSession:
    """A real browser whose Internshala cookies are pointed at the mock server (plain HTTP on 127.0.0.1)."""
    cookies = [{**c, "domain": "127.0.0.1", "secure": False, "sameSite": "Lax"} for c in kwargs.pop("cookies", None) or []]
    kwargs["use_proxy"] = False
    return BrowserSession(cookies=cookies, **kwargs)


@pytest.fixture
def mock_site() -> Iterator[MockInternshala]:
    site = MockInternshala()
    yield site
    site.server.shutdown()


def packet(url: str, *, session: list[dict] | None = None, mappings: dict[str, str] | None = None,
           cover_letter: str | None = COVER_LETTER, answers: list[dict] | None = None, resolve: bool = True) -> CandidatePacket:
    maps = {"willing_to_relocate": "Yes", "proficiency in python": "4", "expected_stipend": "10000", **(mappings or {})}
    prefs = {"salary_min": 10000}
    return CandidatePacket(
        first_name="Aarav", last_name="Sharma", email="aarav@example.com", phone="+91 98100 12345",
        location="New Delhi, Delhi, India", application_url=url, company_name="Acme Labs", role_title="Python Development Intern",
        cover_letter_text=cover_letter, internshala_session=SESSION if session is None else session, answers=answers or [],
        resolve_answers=(lambda qs: answer_questions(qs, RESUME, prefs, maps, use_llm=False)) if resolve else None,
    )


# --------------------------------------------------------------------------------------- browser tests
@pytest.mark.e2e
@needs_browser
def test_easy_apply_modal_is_staged_then_submitted(mock_site: MockInternshala) -> None:
    bot = InternshalaSubmitter(session_factory=local_session)
    staged = bot.stage(packet(f"{mock_site.base}/internship/detail/easy-1"))
    assert staged.success and staged.stage == "staged", staged.error
    assert mock_site.submissions == []  # staging never submits
    assert staged.screenshot and staged.screenshot[:4] == b"\x89PNG"
    fields = {f["label"]: f for f in staged.fields}
    assert fields["Why should you be hired for this role?"]["kind"] == "cover_letter"
    assert fields["Why should you be hired for this role?"]["status"] == "filled"
    assert fields["Confirm your availability"]["value"] == "Yes, I am available to join immediately"
    assert fields["Do you have a laptop with a working internet connection?"]["value"] == "Yes"
    assert fields["What is your expected stipend per month (in INR)?"]["value"] == "10000"
    assert fields["Rate your proficiency in Python"]["value"] == "4"
    assert fields["I am willing to relocate to Delhi for this internship"]["value"] == "Yes"
    assert fields["Resume"]["kind"] == "resume" and "Internshala profile resume" in fields["Resume"]["value"]
    assert all(f["status"] == "filled" for f in staged.fields), [f for f in staged.fields if f["status"] != "filled"]
    assert not staged.needs_manual_review or "low confidence" in (staged.review_reason or "")
    questions = {a["question"] for a in staged.answers}
    assert "Describe a Python project you built and your role in it." in questions
    assert "Confirm your availability" in questions

    sent = InternshalaSubmitter(session_factory=local_session).submit(packet(f"{mock_site.base}/internship/detail/easy-1"))
    assert sent.success and sent.stage == "submitted", (sent.error, sent.fields)
    path, data = mock_site.submissions[0]
    assert path == "/application/submit/easy-1"
    assert data["cover_letter"].startswith("I am excited to apply")  # no "Dear Hiring Manager"
    assert "Sincerely" not in data["cover_letter"] and "I would love to contribute" in data["cover_letter"]
    assert data["confirm_availability"] == "yes"
    assert data["custom_question_text_101"].startswith("Computer science student")
    assert data["custom_question_radio_2"] == "yes"
    assert data["custom_question_number_3"] == "10000"
    assert data["custom_question_range_4"] == "4"
    assert data["location_single"] == "yes"
    assert "custom_resume" not in data  # your Internshala profile resume is never replaced


@pytest.mark.e2e
@needs_browser
def test_review_queue_corrections_are_what_internshala_receives(mock_site: MockInternshala) -> None:
    """Your fixes in "Ready to submit" (label overrides) win over saved and generated answers."""
    from app.submitters.base import override_key

    fixed = packet(f"{mock_site.base}/internship/detail/easy-1")
    fixed.overrides = {override_key("What is your expected stipend per month (in INR)?"): "12000",
                       override_key("Confirm your availability"): "Available from 1 June 2027",
                       override_key("I am willing to relocate to Delhi for this internship"): ""}  # blanked: untick
    result = InternshalaSubmitter(session_factory=local_session).submit(fixed)
    assert result.stage == "submitted", result.error
    data = mock_site.submissions[0][1]
    assert data["custom_question_number_3"] == "12000"
    assert data["confirm_availability"] == "other" and data["availability_text"] == "Available from 1 June 2027"
    assert "location_single" not in data  # a blank correction is sent as blank, not replaced by our answer


@pytest.mark.e2e
@needs_browser
def test_saved_notice_period_picks_other_availability(mock_site: MockInternshala) -> None:
    result = InternshalaSubmitter(session_factory=local_session).submit(
        packet(f"{mock_site.base}/internship/detail/easy-2", mappings={"notice_period": "Available from 1 June 2027"}))
    assert result.stage == "submitted", result.error
    data = mock_site.submissions[0][1]
    assert data["confirm_availability"] == "other" and data["availability_text"] == "Available from 1 June 2027"
    availability = next(a for a in result.answers if a["question"] == "Confirm your availability")
    assert availability["answer"] == "Available from 1 June 2027"


@pytest.mark.e2e
@needs_browser
def test_redirect_flow_through_the_resume_interstitial(mock_site: MockInternshala) -> None:
    result = InternshalaSubmitter(session_factory=local_session).submit(packet(f"{mock_site.base}/internship/detail/redirect-1"))
    assert result.success and result.stage == "submitted", result.error
    assert "/student/resume" in mock_site.requests and "/application/form/redirect-1" in mock_site.requests
    path, data = mock_site.submissions[0]
    assert path == "/application/submit/redirect-1" and data["cover_letter"].startswith("I am excited")
    assert data["confirm_availability"] == "yes"


@pytest.mark.e2e
@needs_browser
@pytest.mark.parametrize(("slug", "stage", "message", "final_url"), [
    ("external-1", "unavailable", "External application: apply at https://careers.example.com/apply/123",
     "https://careers.example.com/apply/123"),
    ("applied-1", "already_applied", ia.ALREADY_APPLIED, None),
    ("closed-1", "unavailable", "Applications are closed", None),
    ("gate-1", "unavailable", "Complete your Internshala profile first", None),
])
def test_listings_that_cannot_be_applied_here(mock_site: MockInternshala, slug: str, stage: str, message: str,
                                              final_url: str | None) -> None:
    result = InternshalaSubmitter(session_factory=local_session).stage(packet(f"{mock_site.base}/internship/detail/{slug}"))
    assert not result.success and result.stage == stage
    assert result.error and result.error.startswith(message), result.error
    assert not result.session_expired
    if final_url:
        assert result.final_url == final_url
    assert mock_site.submissions == []


@pytest.mark.e2e
@needs_browser
def test_logged_out_session_is_reported_as_expired(mock_site: MockInternshala) -> None:
    stale = [{**c, "value": "stale"} if c["name"] == "PHPSESSID" else c for c in SESSION]
    result = InternshalaSubmitter(session_factory=local_session).stage(
        packet(f"{mock_site.base}/internship/detail/easy-3", session=stale))
    assert not result.success and result.session_expired and "Sync" in (result.error or "")
    # Nothing synced at all: no browser is opened
    unsynced = InternshalaSubmitter(session_factory=local_session).stage(
        packet(f"{mock_site.base}/internship/detail/easy-3", session=[]))
    assert unsynced.session_expired and "not synced" in (unsynced.error or "")


@pytest.mark.e2e
@needs_browser
def test_signup_page_means_internshala_did_not_accept_the_login(mock_site: MockInternshala) -> None:
    """Listings are public: logged out, "Apply now" opens Internshala's sign-up page. That's a login problem
    (sync again), not "the form did not open" after a 20-second wait."""
    stale = [{**c, "value": "stale"} if c["name"] == "PHPSESSID" else c for c in SESSION]
    started = time.monotonic()
    result = InternshalaSubmitter(session_factory=local_session).stage(
        packet(f"{mock_site.base}/internship/detail/public-1", session=stale))
    assert not result.success and result.session_expired and result.error == ia.EXPIRED
    assert "/registration/student" in mock_site.requests and time.monotonic() - started < 15
    assert ia.check_session(stale, base_url=mock_site.base, session_factory=local_session) is False
    # The same listing with a login Internshala accepts: the real form
    assert InternshalaSubmitter(session_factory=local_session).stage(
        packet(f"{mock_site.base}/internship/detail/public-2")).success


@pytest.mark.e2e
@needs_browser
def test_the_bot_presents_the_browser_your_login_came_from(mock_site: MockInternshala) -> None:
    """Internshala can tie a login to the browser it was made in: the bot shows your Chrome's user agent."""
    mock_site.bound_ua = MAC_CHROME
    random_browser = InternshalaSubmitter(session_factory=local_session).stage(packet(f"{mock_site.base}/internship/detail/public-3"))
    assert random_browser.session_expired  # what happened before: a different browser, so Internshala saw no login
    mock_site.agents.clear()
    yours = InternshalaSubmitter(session_factory=local_session).stage(
        replace(packet(f"{mock_site.base}/internship/detail/public-4"), internshala_user_agent=MAC_CHROME))
    assert yours.success and yours.stage == "staged", yours.error
    assert mock_site.agents and set(mock_site.agents) == {MAC_CHROME}
    assert ia.check_session(SESSION, base_url=mock_site.base, session_factory=local_session, user_agent=MAC_CHROME) is True
    assert ia.browser_kwargs(SESSION, MAC_CHROME)["use_proxy"] is False  # your own connection, never a scraping proxy


@pytest.mark.e2e
@needs_browser
def test_unanswered_required_question_is_never_submitted(mock_site: MockInternshala) -> None:
    """No cover letter and no answer engine: the form is filled as far as possible, then left for you."""
    result = InternshalaSubmitter(session_factory=local_session).submit(
        packet(f"{mock_site.base}/internship/detail/easy-4", cover_letter=None, resolve=False))
    assert not result.success and result.stage == "failed"
    assert "Why should you be hired" in (result.error or "")
    assert mock_site.submissions == []


@pytest.mark.e2e
@needs_browser
def test_session_probe(mock_site: MockInternshala) -> None:
    assert ia.check_session(SESSION, base_url=mock_site.base, session_factory=local_session) is True
    stale = [{**c, "value": "stale"} if c["name"] == "PHPSESSID" else c for c in SESSION]
    assert ia.check_session(stale, base_url=mock_site.base, session_factory=local_session) is False


# ------------------------------------------------------------------------------------------- helpers
def test_cookie_conversion_keeps_host_only_and_domain_cookies_apart() -> None:
    expired = {"name": "lc", "value": "x", "domain": ".internshala.com", "expirationDate": time.time() - 10}
    cookies = {c["name"]: c for c in ia.internshala_cookies([*SESSION, expired])}
    assert "lc" not in cookies  # expired cookies are dropped
    assert cookies["PHPSESSID"]["domain"] == "internshala.com"  # host-only: no leading dot
    assert cookies["is_logged_in"]["domain"] == ".internshala.com"  # domain cookie keeps it
    assert cookies["PHPSESSID"]["httpOnly"] is True and cookies["PHPSESSID"]["secure"] is True
    assert "expires" not in cookies["PHPSESSID"]  # a session cookie stays one
    assert cookies["l"]["expires"] > time.time()
    assert cookies["PHPSESSID"]["sameSite"] == "Lax"  # "unspecified"
    assert cookies["csrf_cookie_name"]["sameSite"] == "Strict"
    assert cookies["is_logged_in"]["sameSite"] == "None"  # no_restriction + secure
    insecure = ia.internshala_cookies([{"name": "a", "value": "b", "domain": "internshala.com", "sameSite": "no_restriction"}])
    assert insecure[0]["sameSite"] == "Lax"  # Chromium refuses SameSite=None without Secure
    no_flag = ia.internshala_cookies([{"name": "a", "value": "b", "domain": ".internshala.com"}])
    assert no_flag[0]["domain"] == ".internshala.com"  # hostOnly missing: inferred from the dot
    assert ia.has_login(SESSION) and ia.has_login([{"name": "is_logged_in", "value": "1"}])
    assert not ia.has_login([{"name": "PHPSESSID", "value": "anon"}])


def test_cover_letter_and_availability_helpers() -> None:
    text = ia.internshala_cover_letter(COVER_LETTER)
    assert text.startswith("I am excited") and "Sincerely" not in text and "Aarav Sharma" not in text
    assert "\n\n" in text  # paragraphs kept
    assert len(ia.internshala_cover_letter("word " * 1000, limit=200)) <= 200
    options = ["Yes, I am available to join immediately", "No, I am currently on notice period",
               "No, I will have to serve notice period", "Other (Please specify your availability)"]
    assert ia.availability_choice("Immediately", options) == (0, None)
    assert ia.availability_choice("I'm currently on notice period", options) == (1, None)
    assert ia.availability_choice(options[2], options) == (2, None)
    assert ia.availability_choice("2 weeks", options) == (3, "2 weeks")
    assert ia.availability_choice("June 2027", options[:3]) == (-1, None)  # no "Other": left for you


# ------------------------------------------------------------------------------------------- API
def _token(client: TestClient) -> dict[str, str]:
    return {"Authorization": f"Bearer {client.post('/api/v1/auth/extension-token').json()['token']}"}


def test_session_sync_validates_and_never_returns_cookie_values(auth_client: TestClient) -> None:
    c = auth_client
    headers = _token(c)
    url = "/api/v1/users/me/integrations/internshala-session"
    other = [*SESSION, {"name": "x", "value": "y", "domain": ".evil.example"}]
    assert c.post(url, json={"cookies": other}, headers=headers).status_code == 422
    no_session = [{"name": "is_logged_in", "value": "1", "domain": ".internshala.com"}]
    assert "session cookie" in c.post(url, json={"cookies": no_session}, headers=headers).json()["detail"]
    logged_out = [{"name": "PHPSESSID", "value": "anon", "domain": "internshala.com", "hostOnly": True}]
    assert "not logged into Internshala" in c.post(url, json={"cookies": logged_out}, headers=headers).json()["detail"]
    assert c.post(url, json={"cookies": [{"name": "a;b", "value": "1", "domain": "internshala.com"}]}, headers=headers).status_code == 422
    assert c.post(url, json={"cookies": [SESSION[0]] * 61}, headers=headers).status_code == 422  # size cap
    assert c.post(url, json={"cookies": []}, headers=headers).status_code == 422

    r = c.post(url, json={"cookies": SESSION}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["cookies"] == 4 and "remember-me-token" not in r.text
    integ = c.get("/api/v1/users/me/integrations")
    block = integ.json()["internshala"]
    assert block == {"connected": True, "session_valid": True, "updated_at": block["updated_at"], "bot_enabled": False,
                     "auto_submit": False, "daily_limit": 15}
    for secret in (GOOD, "remember-me-token", "csrf123"):
        assert secret not in integ.text and secret not in c.get("/api/v1/users/me").text
        assert secret not in c.get("/api/v1/users/me/export").text
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "jane@example.com").one()
        assert {c["name"] for c in user.internshala_session} == {"PHPSESSID", "l", "csrf_cookie_name", "is_logged_in"}
        assert user.consents.get("internshala")

    # The browser the login came from: kept, replaced on the next sync that sends one, never junk
    def agent() -> str | None:
        with SessionLocal() as db:
            return db.query(User).filter(User.email == "jane@example.com").one().internshala_user_agent

    assert agent() is None
    assert c.post(url, json={"cookies": SESSION, "user_agent": MAC_CHROME}, headers=headers).status_code == 200
    assert agent() == MAC_CHROME
    assert c.post(url, json={"cookies": SESSION}, headers=headers).status_code == 200  # an older extension
    assert agent() == MAC_CHROME
    assert c.post(url, json={"cookies": SESSION, "user_agent": "evil\r\nX-Injected: 1"}, headers=headers).status_code == 200
    assert agent() is None

    assert c.delete("/api/v1/users/me/integrations/internshala").json() == {"ok": True}
    assert c.get("/api/v1/users/me/integrations").json()["internshala"]["connected"] is False


def test_session_check_uses_the_probe(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    c = auth_client
    check = "/api/v1/users/me/integrations/internshala/check"
    assert c.post(check).status_code == 400  # nothing synced yet
    c.post("/api/v1/users/me/integrations/internshala-session", json={"cookies": SESSION, "user_agent": MAC_CHROME},
           headers=_token(c))
    seen: list[tuple[list[dict], dict]] = []
    monkeypatch.setattr(ia, "check_session", lambda cookies, **kw: seen.append((cookies, kw)) or False)
    assert c.post(check).json()["session_valid"] is False
    assert seen[0][0][0]["value"] == GOOD and seen[0][1]["user_agent"] == MAC_CHROME  # checked as your own browser
    assert c.get("/api/v1/users/me/integrations").json()["internshala"]["session_valid"] is False
    monkeypatch.setattr(ia, "check_session", lambda cookies, **kw: True)
    assert c.post(check).json()["session_valid"] is True

    def unavailable(cookies: list[dict], **kw: Any) -> bool:
        raise BrowserUnavailable("no chromium")

    monkeypatch.setattr(ia, "check_session", unavailable)
    assert c.post(check).status_code == 503
    assert c.get("/api/v1/users/me/integrations").json()["internshala"]["session_valid"] is True  # unchanged


def test_internshala_preferences(auth_client: TestClient) -> None:
    c = auth_client
    put = lambda prefs: c.put("/api/v1/users/me/preferences", json={"preferences": prefs})  # noqa: E731
    prefs = c.get("/api/v1/users/me/preferences").json()
    assert (prefs["internshala_bot_enabled"], prefs["internshala_auto_submit"], prefs["internshala_daily_limit"]) == (False, False, 15)
    assert put({"internshala_daily_limit": 0}).status_code == 422
    assert put({"internshala_daily_limit": 26}).status_code == 422
    assert put({"internshala_daily_limit": "10"}).status_code == 422
    assert put({"internshala_bot_enabled": "yes"}).status_code == 422
    r = put({"internshala_bot_enabled": True, "internshala_daily_limit": 10})
    assert r.status_code == 200 and r.json()["internshala_bot_enabled"] is True
    with SessionLocal() as db:
        assert db.query(User).filter(User.email == "jane@example.com").one().consents.get("internshala_bot")
    assert c.get("/api/v1/users/me/integrations").json()["internshala"]["bot_enabled"] is True


# ------------------------------------------------------------------------------------------- orchestrator
def _setup(email: str = "jane@example.com", *, bot: bool = True, session: bool = True, valid: bool = True,
           auto_submit: bool = False, limit: int = 15, url: str | None = None,
           status: ApplicationStatus = ApplicationStatus.PREPARING, keep: bool = True,
           raw: dict | None = None, filled: bool | None = None, verdict: str = "verified") -> str:
    if filled is None:  # an approved / sent application was filled by the bot when it was staged
        filled = status in (ApplicationStatus.APPROVED, ApplicationStatus.APPLIED)
    url = url or f"https://internshala.com/internship/detail/python-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        user.preferences = {**(user.preferences or {}), "internshala_bot_enabled": bot, "internshala_auto_submit": auto_submit,
                            "internshala_daily_limit": limit}
        user.internshala_session = SESSION if session else None
        user.internshala_session_valid = valid
        user.linkedin_session_valid = True
        job = Job(company_name="Acme Labs", role_title="Python Development Intern", description="Python FastAPI internship. " * 30,
                  source_url=url, application_url=url, source_platform=ATSPlatform.CUSTOM, job_type=JobType.INTERNSHIP,
                  location="Delhi, India", dedupe_key=f"acme-{uuid.uuid4().hex[:8]}",
                  raw_data={"listing_source": "internshala", "apply_on_site": "Internshala"} if raw is None else raw,
                  company_verdict=verdict)  # these tests are about the bot: the company check passed (see below)
        db.add(job)
        db.flush()
        app = Application(user_id=user.id, job_id=job.id, status=status, auto_submit=keep,
                          review_decision="keep" if keep else None, ats_platform=ATSPlatform.CUSTOM,
                          cover_letter="I am excited to apply.",
                          form_fields=[{"label": "Cover letter", "kind": "cover_letter", "status": "filled"}] if filled else None)
        db.add(app)
        db.commit()
        return str(app.id)


def _app(app_id: str) -> Application:
    """A detached copy: the session is closed right away so no connection stays "idle in transaction"
    (on PostgreSQL that would block the next test's DROP TABLE forever)."""
    with SessionLocal() as db:
        app = db.get(Application, uuid.UUID(app_id))
        _ = app.user, app.job, app.history  # load what the tests read before the session goes
        db.expunge_all()
    return app


class Recorder:
    """Stands in for the browser: records which submitter ran and with what packet."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, stage: SubmissionResult | None = None,
                 submit: SubmissionResult | None = None) -> None:
        self.calls: list[tuple[str, CandidatePacket]] = []
        filled = [{"label": "Why should you be hired for this role?", "kind": "cover_letter", "type": "contenteditable",
                   "value": "I am excited", "required": True, "status": "filled", "options": []}]
        self.stage_result = stage or SubmissionResult(True, "staged", screenshot=b"\x89PNG fake", fields=filled, answers=[])
        self.submit_result = submit or SubmissionResult(True, "submitted", screenshot=b"\x89PNG done", fields=filled)
        recorder = self
        monkeypatch.setattr(InternshalaSubmitter, "stage", lambda self, p: recorder.calls.append(("stage", p)) or recorder.stage_result)
        monkeypatch.setattr(InternshalaSubmitter, "submit", lambda self, p: recorder.calls.append(("submit", p)) or recorder.submit_result)


def test_blocker_is_none_only_when_the_bot_is_ready(auth_client: TestClient, master_resume: dict) -> None:
    cases = [({"filled": True}, None), ({}, orch.INTERNSHALA_FILLING),  # ready, but the real form not read yet
             ({"bot": False}, orch.INTERNSHALA_MISSING["bot_off"]),
             ({"session": False}, orch.INTERNSHALA_MISSING["not_synced"]),
             ({"valid": False}, orch.INTERNSHALA_MISSING["expired"])]
    for kw, expected in cases:
        app_id = _setup(**kw)
        app = _app(app_id)
        user = app.user
        assert orch.direct_submit_blocker(user, app) == expected, kw
    assert all("I Applied" in m for m in orch.INTERNSHALA_MISSING.values())
    assert "Sync Internshala session" in orch.INTERNSHALA_MISSING["not_synced"]
    with SessionLocal() as db:  # other boards that need your own login keep their reason; ordinary jobs have none
        user = db.query(User).filter(User.email == "jane@example.com").one()
        other = Job(company_name="X", role_title="Y", description="z", source_url="https://board.example/1",
                    source_platform=ATSPlatform.CUSTOM, raw_data={"apply_on_site": "Board"})
        plain = Job(company_name="X", role_title="Y", description="z", source_url="https://boards.greenhouse.io/x/jobs/1",
                    source_platform=ATSPlatform.GREENHOUSE)
        db.add_all([other, plain])
        db.flush()
        assert "Board needs your own Board login" in orch.direct_submit_blocker(user, Application(user_id=user.id, job=other))
        assert orch.direct_submit_blocker(user, Application(user_id=user.id, job=plain)) is None


def test_staging_uses_the_internshala_submitter_when_ready(auth_client: TestClient, master_resume: dict,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch)
    app_id = _setup()
    with SessionLocal() as db:
        orch.stage_application(db, app_id)
        db.commit()
    assert [c[0] for c in rec.calls] == ["stage"]
    sent = rec.calls[0][1]
    assert sent.internshala_session == SESSION and sent.application_url.startswith("https://internshala.com/")
    app = _app(app_id)
    assert app.status == ApplicationStatus.PENDING_APPROVAL  # auto-submit is off: waits for your click
    assert app.form_fields[0]["status"] == "filled" and app.form_screenshot_url and not app.needs_manual_review


def test_any_internshala_posting_is_left_alone_while_the_bot_is_off(auth_client: TestClient, master_resume: dict,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """Even one added by URL without the scraper's markers: the bot never opens your account while it's off."""
    rec = Recorder(monkeypatch)
    for session in (True, False):
        app_id = _setup(bot=False, session=session, raw={})
        with SessionLocal() as db:
            orch.stage_application(db, app_id)
            db.commit()
        assert rec.calls == []
        app = _app(app_id)
        assert app.status == ApplicationStatus.PENDING_APPROVAL and "Internshala" in app.manual_review_reason
        assert app.user.internshala_session_valid  # no "session expired" for a bot you never turned on


def test_turning_the_bot_on_fills_the_forms_prepared_while_it_was_off(auth_client: TestClient, master_resume: dict,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch)
    app_id = _setup(bot=False)
    with run_inline(), SessionLocal() as db:
        orch.stage_application(db, app_id)
        db.commit()
    assert rec.calls == [] and orch.direct_submit_blocker(_app(app_id).user, _app(app_id)) == orch.INTERNSHALA_MISSING["bot_off"]
    with run_inline():  # you turn the bot on: the waiting application's real form is filled for you to review
        r = auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"internshala_bot_enabled": True}})
    assert r.status_code == 200, r.text
    assert [c[0] for c in rec.calls] == ["stage"]
    app = _app(app_id)
    assert app.status == ApplicationStatus.PENDING_APPROVAL and app.form_fields and not app.manual_review_reason
    assert orch.direct_submit_blocker(app.user, app) is None


def test_staging_short_circuits_when_the_bot_is_off(auth_client: TestClient, master_resume: dict,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch)
    app_id = _setup(bot=False)
    with SessionLocal() as db:
        orch.stage_application(db, app_id)
        db.commit()
    assert rec.calls == []  # no browser opened
    app = _app(app_id)
    assert app.status == ApplicationStatus.PENDING_APPROVAL and app.needs_manual_review and "I Applied" in app.manual_review_reason


def test_session_expiry_flips_the_internshala_flag(auth_client: TestClient, master_resume: dict,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    Recorder(monkeypatch, stage=SubmissionResult(False, "failed", error=ia.EXPIRED, session_expired=True,
                                                 screenshot=b"\x89PNG sign-up page"))
    app_id = _setup()
    with SessionLocal() as db:
        orch.stage_application(db, app_id)
        db.commit()
    app = _app(app_id)
    assert app.form_screenshot_url is None  # Internshala's sign-up page is not shown as your "filled form"
    assert app.user.internshala_session_valid is False and app.user.linkedin_session_valid is True
    assert app.status == ApplicationStatus.PENDING_APPROVAL and app.needs_manual_review
    with SessionLocal() as db:
        note = db.query(Notification).filter(Notification.event_type == "session_expired").one()
        assert note.title == "Internshala session expired" and "Sync" in note.body
    assert orch.direct_submit_blocker(app.user, app) == orch.INTERNSHALA_MISSING["expired"]


@pytest.mark.parametrize("auto_submit", [False, True])
def test_auto_submit_only_with_the_internshala_pref(auth_client: TestClient, master_resume: dict,
                                                    monkeypatch: pytest.MonkeyPatch, auto_submit: bool) -> None:
    rec = Recorder(monkeypatch)
    app_id = _setup(auto_submit=auto_submit)
    with run_inline(), SessionLocal() as db:
        orch.stage_application(db, app_id)
        db.commit()
    app = _app(app_id)
    if auto_submit:
        assert [c[0] for c in rec.calls] == ["stage", "submit"]
        assert app.status == ApplicationStatus.APPLIED and app.confirmation_screenshot_url
        with SessionLocal() as check:
            assert orch.internshala_submitted_today(check, app.user) == 1
    else:
        assert [c[0] for c in rec.calls] == ["stage"] and app.status == ApplicationStatus.PENDING_APPROVAL


def test_daily_limit_keeps_the_application_waiting(auth_client: TestClient, master_resume: dict,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch)
    sent = _setup(limit=1, status=ApplicationStatus.APPLIED, url="https://internshala.com/internship/detail/sent-1")
    with SessionLocal() as db:
        db.add(ApplicationStatusHistory(application_id=uuid.UUID(sent), old_status=ApplicationStatus.APPROVED,
                                        new_status=ApplicationStatus.APPLIED, changed_by="agent", created_at=datetime.now(UTC)))
        db.commit()
    app_id = _setup(limit=1, status=ApplicationStatus.APPROVED)
    with run_inline(), SessionLocal() as db:
        orch.submit_application(db, app_id)
        db.commit()
    app = _app(app_id)
    assert rec.calls == [] and app.status == ApplicationStatus.APPROVED
    assert app.notes.startswith("Waiting: Daily Internshala limit reached (1/1)")


def test_submit_with_an_expired_session_goes_back_to_review(auth_client: TestClient, master_resume: dict,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    Recorder(monkeypatch, submit=SubmissionResult(False, "failed", error=ia.EXPIRED, session_expired=True))
    app_id = _setup(status=ApplicationStatus.APPROVED)
    with run_inline(), SessionLocal() as db:
        orch.submit_application(db, app_id)
        db.commit()
    app = _app(app_id)
    assert app.status == ApplicationStatus.PENDING_APPROVAL and "Sync" in app.manual_review_reason
    assert app.user.internshala_session_valid is False


def test_submit_is_refused_when_the_bot_is_off(auth_client: TestClient, master_resume: dict,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch)
    app_id = _setup(bot=False, status=ApplicationStatus.APPROVED)
    with run_inline(), SessionLocal() as db:
        orch.submit_application(db, app_id)
        db.commit()
    app = _app(app_id)
    assert rec.calls == [] and app.status == ApplicationStatus.PENDING_APPROVAL
    assert app.manual_review_reason == orch.INTERNSHALA_MISSING["bot_off"]


@pytest.mark.parametrize("when", ["stage", "submit"])
def test_already_applied_is_tracked_as_applied(auth_client: TestClient, master_resume: dict,
                                               monkeypatch: pytest.MonkeyPatch, when: str) -> None:
    done = SubmissionResult(False, "already_applied", error=ia.ALREADY_APPLIED, needs_manual_review=True)
    Recorder(monkeypatch, stage=done, submit=done)
    app_id = _setup(status=ApplicationStatus.PREPARING if when == "stage" else ApplicationStatus.APPROVED)
    with run_inline(), SessionLocal() as db:
        getattr(orch, f"{when}_application")(db, app_id)
        db.commit()
    app = _app(app_id)
    assert app.status == ApplicationStatus.APPLIED and not app.needs_manual_review
    assert "already applied" in app.history[-1].notes


def test_external_listing_waits_with_its_link_and_is_not_retried(auth_client: TestClient, master_resume: dict,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    external = "External application: apply at https://careers.example.com/apply/123"
    rec = Recorder(monkeypatch, stage=SubmissionResult(False, "unavailable", error=external, needs_manual_review=True,
                                                       final_url="https://careers.example.com/apply/123"))
    app_id = _setup()
    with SessionLocal() as db:
        orch.stage_application(db, app_id)
        db.commit()
    app = _app(app_id)
    assert [c[0] for c in rec.calls] == ["stage"]  # a definite answer: no second browser run
    assert app.status == ApplicationStatus.PENDING_APPROVAL and app.manual_review_reason == external


@pytest.mark.e2e
@needs_browser
def test_keep_prepare_review_and_submit_against_the_mock(auth_client: TestClient, master_resume: dict,
                                                         mock_site: MockInternshala, monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole loop with a real browser: prepare → fill (not sent) → your Submit click → sent."""
    import app.submitters.base as base

    monkeypatch.setattr(base, "BrowserSession", local_session)
    app_id = _setup(url=f"{mock_site.base}/internship/detail/easy-9")
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "jane@example.com").one()
        db.add_all([UserFieldMapping(user_id=user.id, field_name="willing_to_relocate", field_value="Yes"),
                    UserFieldMapping(user_id=user.id, field_name="proficiency in python", field_value="4")])
        db.commit()
    with run_inline(), SessionLocal() as db:
        orch.prepare_application(db, app_id)
        db.commit()
    app = _app(app_id)
    assert app.status == ApplicationStatus.PENDING_APPROVAL, (app.error_log, app.manual_review_reason)
    assert mock_site.submissions == []
    assert {f["label"] for f in app.form_fields} >= {"Why should you be hired for this role?", "Confirm your availability"}
    assert orch.direct_submit_blocker(app.user, app) is None  # the review queue's Submit button can send it
    # No saved stipend: that required question waits for you, and you answer it when you approve
    stipend = "What is your expected stipend per month (in INR)?"
    assert app.needs_manual_review and stipend in app.manual_review_reason
    answers = [{**a, "answer": "12000"} if a["question"] == stipend else a for a in app.custom_answers]

    with run_inline(), SessionLocal() as db:
        orch.approve_application(db, db.get(Application, uuid.UUID(app_id)), custom_answers=answers)
        db.commit()
    app = _app(app_id)
    assert app.status == ApplicationStatus.APPLIED, app.error_log
    assert app.confirmation_screenshot_url
    data = mock_site.submissions[0][1]
    assert data["custom_question_number_3"] == "12000"
    assert data["confirm_availability"] == "yes" and data["location_single"] == "yes" and data["custom_question_range_4"] == "4"
    assert data["cover_letter"]


def test_disconnect_sticks_until_you_sync_yourself(auth_client: TestClient) -> None:
    """Disconnecting in the dashboard turns the bot off, and the extension's background re-syncs
    (scheduled / cookie-changed) can't quietly reconnect it; a manual Sync can."""
    c = auth_client
    headers = _token(c)
    url = "/api/v1/users/me/integrations/internshala-session"
    assert c.post(url, json={"cookies": SESSION}, headers=headers).status_code == 200
    c.put("/api/v1/users/me/preferences", json={"preferences": {"internshala_bot_enabled": True}})
    assert c.delete("/api/v1/users/me/integrations/internshala").status_code == 200
    block = c.get("/api/v1/users/me/integrations").json()["internshala"]
    assert not block["connected"] and not block["bot_enabled"]
    for reason in ("scheduled", "cookie-changed"):
        r = c.post(url, json={"cookies": SESSION, "reason": reason}, headers=headers)
        assert r.status_code == 409 and "disconnected" in r.json()["detail"]
    assert not c.get("/api/v1/users/me/integrations").json()["internshala"]["connected"]
    assert c.post(url, json={"cookies": SESSION, "reason": "manual"}, headers=headers).status_code == 200
    assert c.post(url, json={"cookies": SESSION, "reason": "scheduled"}, headers=headers).status_code == 200


def test_apply_with_the_bot_fills_and_submits_on_one_click(auth_client: TestClient, master_resume: dict,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """"Apply with the bot": your click on one card is the approval, even with "Submit automatically" off."""
    rec = Recorder(monkeypatch)
    app_id = _setup(status=ApplicationStatus.PENDING_APPROVAL, auto_submit=False)
    queue = auth_client.get("/api/v1/applications/review-queue").json()["items"]
    assert queue[0]["bot"] == {"site": "Internshala", "missing": None}
    with run_inline():
        r = auth_client.post(f"/api/v1/applications/{app_id}/bot-apply")
    assert r.status_code == 202, r.text
    assert [c[0] for c in rec.calls] == ["stage", "submit"]
    app = _app(app_id)
    assert app.status == ApplicationStatus.APPLIED
    assert any("You asked the bot" in (h.notes or "") for h in app.history)


@pytest.mark.parametrize(("kw", "missing"), [({"bot": False}, "bot_off"), ({"session": False}, "not_synced"),
                                             ({"valid": False}, "expired")])
def test_apply_with_the_bot_says_exactly_what_is_missing(auth_client: TestClient, master_resume: dict,
                                                         monkeypatch: pytest.MonkeyPatch, kw: dict, missing: str) -> None:
    rec = Recorder(monkeypatch)
    app_id = _setup(status=ApplicationStatus.PENDING_APPROVAL, **kw)
    assert auth_client.get("/api/v1/applications/review-queue").json()["items"][0]["bot"]["missing"] == missing
    r = auth_client.post(f"/api/v1/applications/{app_id}/bot-apply")
    assert r.status_code == 409 and r.json()["detail"] == orch.INTERNSHALA_MISSING[missing]
    assert rec.calls == [] and _app(app_id).status == ApplicationStatus.PENDING_APPROVAL


def test_apply_with_the_bot_is_only_for_internshala(auth_client: TestClient, master_resume: dict) -> None:
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "jane@example.com").one()
        job = Job(company_name="X", role_title="Y", description="z", source_url="https://boards.greenhouse.io/x/jobs/9",
                  source_platform=ATSPlatform.GREENHOUSE)
        db.add(job)
        db.flush()
        app = Application(user_id=user.id, job_id=job.id, status=ApplicationStatus.PENDING_APPROVAL)
        db.add(app)
        db.commit()
        app_id = str(app.id)
    r = auth_client.post(f"/api/v1/applications/{app_id}/bot-apply")
    assert r.status_code == 409 and "Internshala" in r.json()["detail"]


def test_a_refused_login_is_tried_once_and_reported_once(auth_client: TestClient, master_resume: dict,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """After a sync every waiting application is filled again. If Internshala refuses the login, the first
    one finds out; the rest don't open a browser, and you get one notification, not one per application."""
    rec = Recorder(monkeypatch, stage=SubmissionResult(False, "failed", error=ia.EXPIRED, session_expired=True))
    waiting = [_setup(status=ApplicationStatus.PENDING_APPROVAL) for _ in range(3)]
    sync = "/api/v1/users/me/integrations/internshala-session"
    with run_inline():
        assert auth_client.post(sync, json={"cookies": SESSION}, headers=_token(auth_client)).status_code == 200
    assert [c[0] for c in rec.calls] == ["stage"]  # one browser, not three
    with SessionLocal() as db:
        kinds = [n.event_type for n in db.query(Notification).all()]
    assert kinds.count("session_expired") == 1 and "application_ready" not in kinds
    for app_id in waiting:
        app = _app(app_id)
        assert app.status == ApplicationStatus.PENDING_APPROVAL and not app.form_fields
        assert orch.direct_submit_blocker(app.user, app) == orch.INTERNSHALA_MISSING["expired"]
    time.sleep(0.01)
    with run_inline():  # you log in again and sync: the bot tries again (once)
        assert auth_client.post(sync, json={"cookies": SESSION}, headers=_token(auth_client)).status_code == 200
    assert [c[0] for c in rec.calls] == ["stage", "stage"]


def test_one_browser_at_a_time_on_your_internshala_account(auth_client: TestClient, master_resume: dict,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    lock = threading.Lock()
    active = {"now": 0, "peak": 0}
    filled = [{"label": "Cover letter", "kind": "cover_letter", "status": "filled", "required": True, "value": "x", "options": []}]

    def slow_stage(self: InternshalaSubmitter, packet: CandidatePacket) -> SubmissionResult:
        with lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        time.sleep(0.3)
        with lock:
            active["now"] -= 1
        return SubmissionResult(True, "staged", fields=filled, answers=[])

    monkeypatch.setattr(InternshalaSubmitter, "stage", slow_stage)
    apps = [_setup() for _ in range(3)]

    def stage(app_id: str) -> None:
        with SessionLocal() as db:
            orch.stage_application(db, app_id)
            db.commit()

    threads = [threading.Thread(target=stage, args=(a,)) for a in apps]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert active["peak"] == 1  # never two browsers with the same login
    assert all(_app(a).status == ApplicationStatus.PENDING_APPROVAL and _app(a).form_fields for a in apps)


def test_submit_automatically_covers_jobs_kept_before_the_bot_could_apply(auth_client: TestClient, master_resume: dict,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Reported: "I turned it on but it doesn't auto apply". A job you kept while the bot was off waited for
    your click forever, even with Submit automatically on. Now it goes out once the bot can apply."""
    rec = Recorder(monkeypatch)
    app_id = _setup(bot=False, auto_submit=True)
    with SessionLocal() as db:
        orch.stage_application(db, app_id)
        db.commit()
    assert rec.calls == [] and _app(app_id).auto_submit is False  # bot off: you apply there yourself
    with run_inline():
        r = auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"internshala_bot_enabled": True}})
    assert r.status_code == 200, r.text
    assert [c[0] for c in rec.calls] == ["stage", "submit"]
    assert _app(app_id).status == ApplicationStatus.APPLIED



@pytest.mark.e2e
@needs_browser
def test_a_registration_page_while_logged_in_is_not_an_expired_login(mock_site: MockInternshala) -> None:
    """Reported: synced, and 5 seconds later "session expired", though Internshala accepted the login (the
    check script said LOGGED IN). Logged-in students land on /registration/... pages too: the bot now checks
    your dashboard before blaming the login."""
    result = InternshalaSubmitter(session_factory=local_session).stage(packet(f"{mock_site.base}/internship/detail/profile-1"))
    assert not result.success and not result.session_expired
    assert result.stage == "unavailable" and "/registration/student_details" in (result.error or "")
    assert "Your login works" in (result.error or "") and "/student/dashboard" in mock_site.requests
    # Sign-up text in the footer of a page you're logged into: ignored, the form is filled
    staged = InternshalaSubmitter(session_factory=local_session).stage(packet(f"{mock_site.base}/internship/detail/footer-1"))
    assert staged.success and staged.stage == "staged", staged.error


def test_renewed_login_cookies_are_kept() -> None:
    """Internshala renews PHPSESSID / l / sessionToken as the bot browses: the bot keeps the fresh ones."""
    renewed = [{"name": "PHPSESSID", "value": "fresh", "domain": "internshala.com", "path": "/", "expires": -1,
                "httpOnly": True, "secure": True, "sameSite": "Lax"},
               {"name": "l", "value": "fresh-l", "domain": "internshala.com", "path": "/", "expires": time.time() + 86400,
                "httpOnly": True, "secure": True, "sameSite": "None"},
               {"name": "is_logged_in", "value": "1", "domain": ".internshala.com", "path": "/", "expires": time.time() + 86400,
                "httpOnly": False, "secure": True, "sameSite": "Lax"},
               {"name": "_ga", "value": "x", "domain": ".google.com", "path": "/", "expires": -1, "httpOnly": False,
                "secure": False, "sameSite": "Lax"}]
    session = SimpleNamespace(context=SimpleNamespace(cookies=lambda: renewed))
    result = SubmissionResult(True, "staged")
    InternshalaSubmitter().after_run(session, result)
    assert [c["name"] for c in result.session_cookies] == ["PHPSESSID", "l", "is_logged_in"]  # Internshala's only
    assert result.session_cookies[0]["expirationDate"] is None and result.session_cookies[2]["hostOnly"] is False
    back = ia.internshala_cookies(result.session_cookies)  # what the next run loads into its browser
    assert [(c["name"], c["domain"], c["sameSite"]) for c in back] == [
        ("PHPSESSID", "internshala.com", "Lax"), ("l", "internshala.com", "None"), ("is_logged_in", ".internshala.com", "Lax")]
    expired = SubmissionResult(False, "failed", session_expired=True)
    InternshalaSubmitter().after_run(session, expired)
    assert expired.session_cookies is None  # never overwrite your login with a logged-out jar


def test_the_app_stores_the_renewed_login(auth_client: TestClient, master_resume: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    fresh = [{**c, "value": "renewed"} if c["name"] == "PHPSESSID" else c for c in SESSION]
    filled = [{"label": "Cover letter", "kind": "cover_letter", "status": "filled", "required": True, "value": "x", "options": []}]
    Recorder(monkeypatch, stage=SubmissionResult(True, "staged", fields=filled, answers=[], session_cookies=fresh))
    app_id = _setup()
    with SessionLocal() as db:
        orch.stage_application(db, app_id)
        db.commit()
    app = _app(app_id)
    assert {c["name"]: c["value"] for c in app.user.internshala_session}["PHPSESSID"] == "renewed"
    assert app.user.internshala_session_valid is True


@pytest.mark.parametrize("verdict", ["unverified", "suspicious"])
def test_the_bot_only_applies_by_itself_to_verified_companies(auth_client: TestClient, master_resume: dict,
                                                             monkeypatch: pytest.MonkeyPatch, verdict: str) -> None:
    """Reported: Internshala is full of unknown and fraud companies. Submit automatically now needs the
    company check to pass; your click (Apply with the bot) is your OK for an unverified one, never for fraud."""
    rec = Recorder(monkeypatch)
    app_id = _setup(auto_submit=True, verdict=verdict)
    with run_inline(), SessionLocal() as db:
        orch.stage_application(db, app_id)
        db.commit()
    app = _app(app_id)
    assert [c[0] for c in rec.calls] == ["stage"] and app.status == ApplicationStatus.PENDING_APPROVAL
    assert app.manual_review_reason.startswith("Not sent automatically: Acme Labs isn't a verified company")
    with run_inline():  # the bot's task runs here, not in a thread that outlives this test
        r = auth_client.post(f"/api/v1/applications/{app_id}/bot-apply")
    if verdict == "suspicious":
        assert r.status_code == 409 and "possible fraud" in r.json()["detail"] and rec.calls == [("stage", rec.calls[0][1])]
        return
    assert r.status_code == 202, r.text


def test_marking_a_company_legit_sends_what_waited_for_it(auth_client: TestClient, master_resume: dict,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch)
    app_id = _setup(auto_submit=True, verdict="unverified")
    with run_inline(), SessionLocal() as db:
        orch.stage_application(db, app_id)
        db.commit()
    assert [c[0] for c in rec.calls] == ["stage"]
    with run_inline():
        r = auth_client.post("/api/v1/jobs/company-trust", json={"company": "Acme Labs", "trusted": True})
    assert r.status_code == 200 and r.json()["applications_updated"] == 1
    assert [c[0] for c in rec.calls] == ["stage", "submit"] and _app(app_id).status == ApplicationStatus.APPLIED
    # And "not legit": avoided from now on, its waiting jobs skipped
    other = _setup(status=ApplicationStatus.MATCHED, verdict="unverified")
    r = auth_client.post("/api/v1/jobs/company-trust", json={"company": "Acme Labs", "trusted": False})
    assert r.json()["applications_updated"] == 1 and _app(other).status == ApplicationStatus.SKIPPED
    prefs = auth_client.get("/api/v1/auth/me").json()["preferences"]
    assert prefs["trusted_companies"] == [] and prefs["companies_to_avoid"][-1] == "Acme Labs"
