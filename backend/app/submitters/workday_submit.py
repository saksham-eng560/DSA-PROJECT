"""Workday multi-page application flow (PLAN.md §9.3).

Workday is an SPA with per-company candidate accounts. The flow is:
Apply -> Apply Manually -> Sign in (or Create Account) -> My Information -> My Experience (resume)
-> Application Questions -> Voluntary Disclosures -> Self Identify -> Review -> Submit.
Tenants customise pages heavily, so each page is filled through the generic form engine using
``data-automation-id`` hooks, and anything we cannot fill is surfaced for manual review.
"""

from __future__ import annotations

import logging
from typing import Any

from app.automation import human
from app.submitters.base import BaseSubmitter, CandidatePacket, SubmissionError, SubmissionResult
from app.submitters.form_engine import FormField, classify_field, extract_fields

logger = logging.getLogger(__name__)

AID = "[data-automation-id='{}']"
NEXT_BUTTON = "[data-automation-id='bottom-navigation-next-button'], button:has-text('Save and Continue'), button:has-text('Next')"
SUBMIT_BUTTON = "button:has-text('Submit'), [data-automation-id='bottom-navigation-next-button']:has-text('Submit')"
MAX_PAGES = 10
STEP_ROOT = "[data-automation-id='applyFlowPage'], main, form"

WORKDAY_IDS = {
    "legalnamesection_firstname": "first_name",
    "legalnamesection_lastname": "last_name",
    "email": "email",
    "phone-number": "phone",
    "phonenumber": "phone",
    "addresssection_city": "location",
    "linkedinquestion": "linkedin",
}


class WorkdaySubmitter(BaseSubmitter):
    platform = "workday"
    submit_selectors = (SUBMIT_BUTTON,)

    def _click_if_present(self, page: Any, selector: str, wait: int = 1500) -> bool:
        loc = page.locator(selector)
        try:
            if loc.count() and loc.first.is_visible():
                human.human_click(page, loc.first)
                page.wait_for_timeout(wait)
                return True
        except Exception:  # noqa: BLE001
            return False
        return False

    def open_application(self, page: Any, packet: CandidatePacket) -> None:
        url = packet.application_url.removesuffix("/apply")
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        if not self._click_if_present(page, f"{AID.format('adventureButton')}, a:has-text('Apply'), button:has-text('Apply')", 2500):
            raise SubmissionError("Workday 'Apply' button not found (posting may be closed)")
        self._click_if_present(page, f"{AID.format('applyManually')}, a:has-text('Apply Manually'), button:has-text('Apply Manually')", 2500)
        self._authenticate(page, packet)

    def _authenticate(self, page: Any, packet: CandidatePacket) -> None:
        password = (packet.ats_credentials or {}).get("workday_password")
        email_box = page.locator(f"{AID.format('email')}, input[type=email]")
        if not email_box.count():
            return  # already signed in / guest application
        if not password:
            raise SubmissionError(
                "Workday requires a candidate account. Add a 'workday_password' in Settings → Field mappings."
            )
        # Try sign-in first
        self._click_if_present(page, f"{AID.format('signInLink')}, button:has-text('Sign In')", 1200)
        human.human_type(page, page.locator(f"{AID.format('email')}, input[type=email]").first, packet.email)
        human.human_type(page, page.locator(f"{AID.format('password')}, input[type=password]").first, password)
        self._click_if_present(page, f"{AID.format('signInSubmitButton')}, button:has-text('Sign In')", 3000)
        if not page.locator(f"{AID.format('errorMessage')}, [role=alert]").count() and not page.locator(AID.format("password")).count():
            return
        # Create an account
        if not self._click_if_present(page, f"{AID.format('createAccountLink')}, button:has-text('Create Account')", 1500):
            raise SubmissionError("Workday sign-in failed and no 'Create Account' option was found")
        human.human_type(page, page.locator(AID.format("email")).first, packet.email)
        human.human_type(page, page.locator(AID.format("password")).first, password)
        verify = page.locator(AID.format("verifyPassword"))
        if verify.count():
            human.human_type(page, verify.first, password)
        checkbox = page.locator(AID.format("createAccountCheckbox"))
        if checkbox.count():
            checkbox.first.check(force=True)
        self._click_if_present(page, f"{AID.format('createAccountSubmitButton')}, button:has-text('Create Account')", 4000)
        if page.locator("text=/verify your (email|account)/i").count():
            raise SubmissionError("Workday sent an account verification e-mail — verify it, then approve again")

    def classify(self, f: FormField) -> str | None:
        ident = f"{f.id} {f.name}".lower().replace("-", "")
        for key, kind in WORKDAY_IDS.items():
            if key.replace("-", "") in ident:
                return kind
        if f.type == "file":
            return "resume"
        return classify_field(f)

    def _on_review_page(self, page: Any) -> bool:
        try:
            if page.locator("h2:has-text('Review'), [data-automation-id*='review' i]").count():
                return True
            return page.locator(SUBMIT_BUTTON).count() > 0 and page.locator("button:has-text('Submit')").first.is_visible()
        except Exception:  # noqa: BLE001
            return False

    def fill(self, page: Any, packet: CandidatePacket) -> SubmissionResult:
        combined = SubmissionResult(success=True, stage="staged")
        for _ in range(MAX_PAGES):
            if self._on_review_page(page):
                break
            fields = extract_fields(page, STEP_ROOT)
            handled = {f.handle for f in fields}
            # "Use my last application" / resume upload pages
            upload = page.locator(f"{AID.format('file-upload-input-ref')}, input[type=file]")
            if upload.count() and packet.resume_path:
                try:
                    upload.first.set_input_files(packet.resume_path)
                    page.wait_for_timeout(3000)
                except Exception:  # noqa: BLE001
                    pass
                fields = [f for f in fields if f.type != "file"]
            step = self.fill_fields(page, packet, fields, root=STEP_ROOT, handled=handled)
            combined.fields.extend(step.fields)
            combined.answers.extend(step.answers)
            if step.needs_manual_review and any(f["status"] == "unmapped" and f["required"] for f in step.fields):
                combined.needs_manual_review = True
                combined.review_reason = step.review_reason
            if not self._click_if_present(page, NEXT_BUTTON, 3000):
                break
            errors = page.locator(AID.format("errorMessage"))
            if errors.count():
                combined.needs_manual_review = True
                combined.review_reason = "Workday reported validation errors: " + "; ".join(
                    errors.nth(i).inner_text()[:80] for i in range(min(errors.count(), 3)))
                break
        if not self._on_review_page(page) and not combined.needs_manual_review:
            combined.needs_manual_review = True
            combined.review_reason = "Did not reach the Workday review page automatically"
        return combined
