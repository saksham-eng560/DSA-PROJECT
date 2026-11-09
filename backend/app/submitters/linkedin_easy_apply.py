"""LinkedIn Easy Apply (PLAN.md §9.1) using the user's synced ``li_at`` session cookie."""

from __future__ import annotations

import logging
from typing import Any

from app.automation import human
from app.automation.browser import linkedin_cookies
from app.submitters.base import BaseSubmitter, CandidatePacket, SessionExpired, SubmissionError, SubmissionResult
from app.submitters.form_engine import extract_fields

logger = logging.getLogger(__name__)

MODAL = ".jobs-easy-apply-modal, [data-test-modal-id='easy-apply-modal'], div[role='dialog']"
EASY_APPLY_BUTTON = "button.jobs-apply-button:has-text('Easy Apply'), button:has-text('Easy Apply')"
NEXT = "button[aria-label='Continue to next step'], button:has-text('Next')"
REVIEW = "button[aria-label='Review your application'], button:has-text('Review')"
SUBMIT = "button[aria-label='Submit application'], button:has-text('Submit application')"
MAX_STEPS = 8


class LinkedInEasyApplySubmitter(BaseSubmitter):
    platform = "linkedin"
    form_root = MODAL
    submit_selectors = (SUBMIT,)

    def session_kwargs(self, packet: CandidatePacket) -> dict[str, Any]:
        if not packet.linkedin_cookie:
            raise SessionExpired("LinkedIn session not synced — install the extension and sync your session")
        return {"cookies": linkedin_cookies(packet.linkedin_cookie)}

    def _run(self, packet: CandidatePacket, submit: bool) -> SubmissionResult:
        try:
            return super()._run(packet, submit)
        except SessionExpired as exc:
            return SubmissionResult(False, "failed", error=str(exc), session_expired=True)

    def open_application(self, page: Any, packet: CandidatePacket) -> None:
        page.goto(packet.application_url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        if "authwall" in page.url or "/login" in page.url or page.locator("form.login__form, #session_key").count():
            raise SessionExpired("LinkedIn session expired — re-sync it with the browser extension")
        human.human_scroll(page, 500)
        button = page.locator(EASY_APPLY_BUTTON)
        if not button.count():
            if page.locator("text=/No longer accepting applications/i").count():
                raise SubmissionError("This LinkedIn job is no longer accepting applications")
            if page.locator("text=/Applied .* ago/i").count():
                raise SubmissionError("You already applied to this job on LinkedIn")
            raise SubmissionError("Easy Apply is not available for this job (external application)")
        human.human_click(page, button.first)
        page.wait_for_selector(MODAL, timeout=15000)

    def _visible(self, page: Any, selector: str) -> bool:
        loc = page.locator(selector)
        try:
            return loc.count() > 0 and loc.first.is_visible()
        except Exception:  # noqa: BLE001
            return False

    def fill(self, page: Any, packet: CandidatePacket) -> SubmissionResult:
        combined = SubmissionResult(success=True, stage="staged")
        for _ in range(MAX_STEPS):
            fields = extract_fields(page, MODAL)
            step = self.fill_fields(page, packet, fields, root=MODAL)
            combined.fields.extend(step.fields)
            combined.answers.extend(step.answers)
            if self._visible(page, SUBMIT):
                # Review step reached: don't auto-follow the company
                follow = page.locator("label[for='follow-company-checkbox'], input#follow-company-checkbox")
                try:
                    if follow.count() and page.locator("input#follow-company-checkbox").is_checked():
                        follow.first.click()
                except Exception:  # noqa: BLE001
                    pass
                break
            advanced = False
            for selector in (REVIEW, NEXT):
                if self._visible(page, selector):
                    human.human_click(page, page.locator(selector).first)
                    page.wait_for_timeout(1500)
                    advanced = True
                    break
            if not advanced:
                break
            errors = page.locator(f"{MODAL} .artdeco-inline-feedback--error")
            if errors.count():
                combined.needs_manual_review = True
                combined.review_reason = "LinkedIn flagged: " + "; ".join(
                    errors.nth(i).inner_text()[:80] for i in range(min(errors.count(), 3)))
                break
        if not self._visible(page, SUBMIT):
            combined.needs_manual_review = True
            combined.review_reason = combined.review_reason or "Could not reach the Easy Apply review step"
        elif any(f["status"] == "unmapped" and f["required"] for f in combined.fields):
            combined.needs_manual_review = True
            combined.review_reason = "Some required Easy Apply questions need your input"
        return combined

    def wait_for_confirmation(self, page: Any, baseline: dict[str, Any] | None = None, timeout_ms: int = 20000) -> tuple[bool, str | None]:
        try:
            page.wait_for_selector("text=/application was sent|Application submitted|applied/i", timeout=timeout_ms)
            return True, None
        except Exception:  # noqa: BLE001
            return False, None
