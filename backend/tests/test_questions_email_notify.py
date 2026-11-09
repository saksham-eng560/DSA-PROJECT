import base64
from datetime import UTC, datetime

import httpx
import respx

from app.models.application import Application
from app.models.communication import Communication
from app.models.enums import ApplicationStatus, ATSPlatform
from app.models.interview import Interview
from app.models.job import Job
from app.models.user import Notification, User
from app.services.email_parser import EmailMessage, heuristic_parse, is_job_related, match_application
from app.services.gmail_service import parse_gmail_message, process_message
from app.services.notifier import notify
from app.services.question_answerer import answer_questions, choose_option, learnable_key
from app.services.rate_limiter import rate_limiter

RESUME = {"personal_info": {"name": "Jane Doe", "linkedin": "https://linkedin.com/in/jane"}, "summary": "Engineer",
          "experience": [{"company": "Acme", "title": "SWE", "start_date": "2020", "end_date": "Present"}],
          "skills": {"technical": ["Python"]}}


def test_choose_option() -> None:
    assert choose_option("yes", ["Yes", "No"]) == "Yes"
    assert choose_option("Decline to self-identify", ["Male", "Female", "I don't wish to answer"]) == "I don't wish to answer"
    assert choose_option("Bachelor's degree", ["High School", "Bachelor's", "Master's"]) == "Bachelor's"


def test_rule_based_answers() -> None:
    questions = [
        {"question": "Are you legally authorized to work in the US?", "options": ["Yes", "No"]},
        {"question": "Will you require visa sponsorship now or in the future?", "options": ["Yes", "No"]},
        {"question": "Desired salary?"},
        {"question": "Years of professional experience", "field_type": "number"},
        {"question": "Veteran status", "options": ["I am a veteran", "I am not a veteran", "Decline to self identify"]},
        {"question": "Current company"},
        {"question": "I certify that the information provided is accurate and complete", "field_type": "checkbox"},
    ]
    answers = answer_questions(questions, RESUME, {"salary_min": 130000}, {"work_authorization": "Yes", "requires_sponsorship": "No"})
    by_q = {a["question"]: a for a in answers}
    assert by_q["Are you legally authorized to work in the US?"]["answer"] == "Yes"
    assert by_q["Will you require visa sponsorship now or in the future?"]["answer"] == "No"
    assert by_q["Desired salary?"]["answer"] == "130000"  # bottom of range
    assert int(by_q["Years of professional experience"]["answer"]) >= 5
    assert by_q["Veteran status"]["answer"] == "Decline to self identify"
    assert by_q["Current company"]["answer"] == "Acme"
    assert by_q["I certify that the information provided is accurate and complete"]["answer"] == "Yes"
    assert all(not a["needs_user_review"] for a in answers if a["question"] != "Years of professional experience")


def test_facts_are_never_guessed() -> None:
    """Without a saved answer, eligibility questions stay blank (flagged) instead of defaulting to the first option."""
    questions = [
        {"question": "Will you now or in the future require visa sponsorship?", "options": ["Yes", "No"], "required": True},
        {"question": "Are you legally authorized to work in the United States?", "options": ["Yes", "No"]},
        {"question": "Have you ever been convicted of a felony?", "options": ["Yes", "No"]},
        {"question": "Preferred office", "options": ["Berlin", "London"]},
    ]
    by_q = {a["question"]: a for a in answer_questions(questions, RESUME, {}, {}, use_llm=False)}
    for q in questions[:3]:
        assert by_q[q["question"]]["answer"] == "" and by_q[q["question"]]["needs_user_review"]
    assert by_q["Preferred office"]["answer"] == "Berlin" and by_q["Preferred office"]["needs_user_review"]
    assert learnable_key("Will you now or in the future require visa sponsorship?") == "requires_sponsorship"
    assert learnable_key("Are you legally authorized to work in the United States?") == "work_authorization"
    assert learnable_key("Why do you want to work here?") is None


def test_llm_answers_for_subjective_questions(fake_llm) -> None:
    provider = fake_llm({"CUSTOM QUESTION ANSWERING": {"custom_answers": [
        {"question": "Why do you want to join us?", "field_type": "text", "answer": "Because...", "confidence": 0.9,
         "needs_user_review": False},
        {"question": "Preferred office", "field_type": "select", "answer": "berlin", "confidence": 0.5, "needs_user_review": False},
    ]}})
    answers = answer_questions([{"question": "Why do you want to join us?"},
                                {"question": "Preferred office", "options": ["Berlin", "London"]}], RESUME, {}, {})
    assert answers[0]["answer"] == "Because..." and answers[0]["source"] == "llm"
    assert answers[1]["answer"] == "Berlin" and answers[1]["needs_user_review"] is True  # low confidence
    assert "password" not in provider.calls[0]["prompt"].lower()


def test_email_heuristics() -> None:
    apps = [{"id": "a1", "company_name": "Stripe", "role_title": "Software Engineer"},
            {"id": "a2", "company_name": "Acme Corp", "role_title": "Data Analyst"}]
    invite = EmailMessage("recruiting@stripe.com", "Next steps", "Hi Jane, we'd like to schedule a call - phone screen "
                          "on Tuesday, October 6 at 10:00 am ET. https://meet.google.com/abc-defg-hij", "Sam",
                          datetime(2026, 9, 29, tzinfo=UTC))
    result = heuristic_parse(invite, apps, "Jane Doe")
    assert result["intent"] == "interview_invite"
    assert result["status_update"] == "screening"
    assert result["matched_application_id"] == "a1"
    assert result["extracted_details"]["interview_date"].startswith("2026-10-06T10:00")
    assert result["extracted_details"]["meeting_link"] == "https://meet.google.com/abc-defg-hij"
    reject = EmailMessage("no-reply@greenhouse-mail.io", "Your application to Acme Corp",
                          "Unfortunately we have decided to move forward with other candidates.")
    assert heuristic_parse(reject, apps, "Jane")["intent"] == "rejection"
    assert match_application(reject, apps) == "a2"
    offer = EmailMessage("ceo@acme.com", "Offer", "We are pleased to offer you the position!")
    assert heuristic_parse(offer, apps, "Jane")["intent"] == "offer"
    assert not is_job_related(EmailMessage("news@shop.com", "Sale!", "50% off shoes"), ["Stripe"])
    assert is_job_related(reject, [])


def _gmail_raw(sender: str, subject: str, body: str, msg_id: str = "m1") -> dict:
    return {
        "id": msg_id, "threadId": "t1", "labelIds": ["INBOX"], "internalDate": str(int(datetime(2026, 9, 29, tzinfo=UTC).timestamp() * 1000)),
        "payload": {"mimeType": "multipart/alternative", "headers": [
            {"name": "From", "value": sender}, {"name": "To", "value": "jane@example.com"}, {"name": "Subject", "value": subject}],
            "parts": [{"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}}]},
    }


def _seed_application(db, status=ApplicationStatus.APPLIED):  # type: ignore[no-untyped-def]
    user = User(email="jane@example.com", full_name="Jane Doe")
    db.add(user)
    db.flush()
    job = Job(company_name="Stripe", role_title="Software Engineer", description="Python", source_url="https://s/1",
              source_platform=ATSPlatform.GREENHOUSE)
    db.add(job)
    db.flush()
    app = Application(user_id=user.id, job_id=job.id, status=status, submitted_at=datetime.now(UTC))
    db.add(app)
    db.flush()
    return user, app


def test_process_message_updates_status_and_creates_interview(db) -> None:
    user, app = _seed_application(db)
    parsed = parse_gmail_message(_gmail_raw("Sam Recruiter <sam@stripe.com>", "Interview invitation - Software Engineer",
                                            "Hi Jane! We'd like to invite you to interview. Are you free on October 8 at 3:00 pm PT? "
                                            "Zoom: https://zoom.us/j/99887766 Thanks, Sam"))
    assert parsed["sender_email"] == "sam@stripe.com" and "invite you" in parsed["body_text"]
    apps = [{"id": str(app.id), "company_name": "Stripe", "role_title": "Software Engineer", "status": "applied"}]
    comm = process_message(db, user, parsed, apps)
    assert comm is not None and comm.application_id == app.id
    assert comm.detected_intent.value == "interview_invite"
    db.refresh(app)
    assert app.status == ApplicationStatus.INTERVIEW
    assert app.first_response_at is not None
    interview = db.query(Interview).one()
    assert interview.meeting_platform == "zoom"
    assert interview.prep_notes and interview.likely_questions
    assert db.query(Notification).filter(Notification.event_type == "interview_scheduled").count() == 1
    # Idempotent: the same Gmail message is never processed twice
    assert process_message(db, user, parsed, apps) is None
    # Status never moves backwards: an acknowledgment after the interview doesn't downgrade
    ack = parse_gmail_message(_gmail_raw("no-reply@stripe.com", "Thanks for applying to Stripe",
                                         "We have received your application for Software Engineer.", "m2"))
    process_message(db, user, ack, apps)
    db.refresh(app)
    assert app.status == ApplicationStatus.INTERVIEW
    assert db.query(Communication).count() == 2


def test_notifier_channels(db) -> None:
    user = User(email="n@example.com", full_name="N", preferences={"notification_channels": ["dashboard", "discord"],
                                                                   "discord_webhook_url": "https://discord.com/api/webhooks/x"})
    db.add(user)
    db.flush()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("https://discord.com/api/webhooks/x").mock(return_value=httpx.Response(204))
        notify(db, user, "application_ready", "Review app", "body", link="/dashboard/applications/1")
        assert route.called
        assert "http://localhost:3000/dashboard/applications/1" in route.calls[0].request.content.decode()
        route.reset()
        notify(db, user, "scan_completed", "Scan done", "")  # dashboard-only event
        assert not route.called
    assert db.query(Notification).count() == 2


def test_rate_limiter_limits() -> None:
    uid = "user-rl"
    ok, _ = rate_limiter.can_apply(uid, "workday", 25)
    assert ok
    rate_limiter.record_application(uid, "workday")
    ok, reason = rate_limiter.can_apply(uid, "workday", 25)
    assert not ok and "Cooling down" in reason
    ok, reason = rate_limiter.can_apply(uid, "greenhouse", 1)
    assert not ok and "Daily application limit" in reason
    rate_limiter.pause_platform("indeed", 60)
    assert rate_limiter.is_paused("indeed") and not rate_limiter.allow_request("indeed")


def test_stipend_and_currency_are_never_mixed_up() -> None:
    """A monthly stipend isn't your yearly salary, and a ₹ question never gets a $ figure."""
    qs = [{"question": "What is your expected stipend per month (in INR)?", "field_type": "number"},
          {"question": "Expected CTC (INR)"}, {"question": "Desired salary?"}]
    usd = {"salary_min": 150000, "salary_currency": "USD"}
    by_q = {a["question"]: a for a in answer_questions(qs, RESUME, usd, {}, use_llm=False)}
    assert by_q[qs[0]["question"]]["answer"] == "" and by_q[qs[0]["question"]]["needs_user_review"]
    assert by_q["Expected CTC (INR)"]["answer"] == "" and by_q["Desired salary?"]["answer"] == "150000"
    by_q = {a["question"]: a for a in answer_questions(qs, RESUME, usd, {"expected_stipend": "15000"}, use_llm=False)}
    assert by_q[qs[0]["question"]]["answer"] == "15000" and not by_q[qs[0]["question"]]["needs_user_review"]
    inr = {"salary_min": 600000, "salary_currency": "INR"}
    assert answer_questions([qs[1]], RESUME, inr, {}, use_llm=False)[0]["answer"] == "600000"
