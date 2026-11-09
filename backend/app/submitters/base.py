"""Submitter interface: ``stage`` fills the form and pauses (screenshot); ``submit`` fills and clicks Submit.

Staging and submission run in separate, ephemeral browser sessions: the approval wait can take
hours, so the approved packet (tailored resume, cover letter, edited answers) is replayed on submit.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from app.automation import human
from app.automation.browser import BrowserSession, BrowserUnavailable
from app.automation.captcha import CaptchaError, solve_on_page
from app.config import settings
from app.submitters.form_engine import (
    FormField,
    classify_field,
    extract_fields,
    fill_field,
    looks_submitted,
    visible_errors,
)

logger = logging.getLogger(__name__)

AnswerResolver = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]

#: Profile values a form field can be classified as (``CandidatePacket.profile_value``).
PROFILE_KINDS = ("first_name", "last_name", "full_name", "email", "phone", "location", "linkedin", "github",
                 "portfolio", "current_company", "current_title")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def override_key(label: str) -> str:
    """Key of one form field in ``CandidatePacket.overrides`` (and ``Application.field_overrides``)."""
    return f"label:{_norm(label)}"


class SubmissionError(Exception):
    pass


class SessionExpired(SubmissionError):
    pass


@dataclass
class CandidatePacket:
    first_name: str
    last_name: str
    email: str
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    current_company: str = ""
    current_title: str = ""
    resume_path: str | None = None
    cover_letter_text: str | None = None
    cover_letter_path: str | None = None
    answers: list[dict[str, Any]] = field(default_factory=list)
    resolve_answers: AnswerResolver | None = None
    ats_credentials: dict[str, Any] = field(default_factory=dict)
    linkedin_cookie: str | None = None
    internshala_session: list[dict[str, Any]] | None = None  # cookies synced by the extension
    internshala_user_agent: str | None = None  # the browser those cookies came from
    application_url: str = ""
    company_name: str = ""
    role_title: str = ""
    #: Your corrections from the review queue: a profile kind ("email") or ``override_key(label)`` -> value.
    #: They win over the profile, stored answers and generated answers alike.
    overrides: dict[str, str] = field(default_factory=dict)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def override_for(self, label: str) -> str | None:
        return self.overrides.get(override_key(label)) if self.overrides else None

    def profile_value(self, key: str) -> str | None:
        if key in self.overrides:
            return str(self.overrides[key]) or None
        return {
            "first_name": self.first_name,
            "last_name": self.last_name,
            "full_name": self.full_name,
            "email": self.email,
            "phone": self.phone,
            "location": self.location,
            "linkedin": self.linkedin,
            "github": self.github,
            "portfolio": self.portfolio,
            "current_company": self.current_company,
            "current_title": self.current_title,
        }.get(key) or None


@dataclass
class SubmissionResult:
    success: bool
    stage: str  # staged | submitted | dry_run | failed
    screenshot: bytes | None = None
    fields: list[dict[str, Any]] = field(default_factory=list)
    answers: list[dict[str, Any]] = field(default_factory=list)
    needs_manual_review: bool = False
    review_reason: str | None = None
    confirmation_number: str | None = None
    error: str | None = None
    session_expired: bool = False
    final_url: str | None = None
    # The site's login cookies as they stood when the browser closed (it may have renewed them): keep these
    session_cookies: list[dict[str, Any]] | None = None


class BaseSubmitter:
    platform = "generic"
    #: selector of the element that contains the application form (None = whole page)
    form_root: str | None = None
    submit_selectors: tuple[str, ...] = (
        "button[type=submit]",
        "input[type=submit]",
        "button:has-text('Submit application')",
        "button:has-text('Submit Application')",
        "button:has-text('Submit')",
        "button:has-text('Apply')",
    )
    unmapped_review_threshold = 0.30  # PLAN.md §9.4: >30% unmapped -> manual review

    def __init__(self, session_factory: Callable[..., BrowserSession] | None = None) -> None:
        self.session_factory = session_factory or BrowserSession

    # ------------------------------------------------------------------ lifecycle
    def stage(self, packet: CandidatePacket) -> SubmissionResult:
        return self._run(packet, submit=False)

    def submit(self, packet: CandidatePacket) -> SubmissionResult:
        return self._run(packet, submit=True)

    def session_kwargs(self, packet: CandidatePacket) -> dict[str, Any]:
        return {}

    def _run(self, packet: CandidatePacket, submit: bool) -> SubmissionResult:
        try:
            with self.session_factory(**self.session_kwargs(packet)) as session:
                result = self._drive(session, packet, submit)
                try:
                    self.after_run(session, result)
                except Exception:  # never lose the result over bookkeeping
                    logger.exception("%s: after_run failed", self.platform)
                return result
        except BrowserUnavailable as exc:  # "unavailable": trying again won't help until Chromium is fixed
            return SubmissionResult(False, "unavailable", error=str(exc))

    def after_run(self, session: BrowserSession, result: SubmissionResult) -> None:
        """Called with the browser still open, after every run (e.g. to keep renewed login cookies)."""

    def _drive(self, session: BrowserSession, packet: CandidatePacket, submit: bool) -> SubmissionResult:
        page = session.page
        try:
            self.open_application(page, packet)
            human.dwell()
            result = self.fill(page, packet)
            self._solve_captcha(page)
            result.screenshot = session.screenshot()
            if not submit:
                result.stage = "staged"
                result.success = True
                return result
            if result.needs_manual_review and any(f["status"] == "unmapped" and f.get("required") for f in result.fields):
                result.success = False
                result.stage = "failed"
                result.error = result.review_reason or "Required fields could not be filled"
                return result
            if settings.SUBMISSION_DRY_RUN:
                result.success = True
                result.stage = "dry_run"
                return result
            baseline = self.baseline(page)
            self.click_submit(page)
            self._solve_captcha(page)
            ok, number = self.wait_for_confirmation(page, baseline)
            result.screenshot = session.screenshot()
            result.final_url = page.url
            if ok:
                result.success = True
                result.stage = "submitted"
                result.confirmation_number = number
            else:
                errors = visible_errors(page)
                result.success = False
                result.stage = "failed"
                result.error = "Submission not confirmed" + (f": {'; '.join(errors)}" if errors else "")
            return result
        except SessionExpired as exc:
            return SubmissionResult(False, "failed", error=str(exc), session_expired=True,
                                    screenshot=self._safe_screenshot(session))
        except (SubmissionError, CaptchaError) as exc:
            return SubmissionResult(False, "failed", error=str(exc), screenshot=self._safe_screenshot(session),
                                    needs_manual_review=True, review_reason=str(exc))
        except Exception as exc:
            logger.exception("%s submitter crashed", self.platform)
            first = next((line.strip() for line in str(exc).splitlines() if line.strip()), "")  # not Playwright's call log
            return SubmissionResult(False, "failed", error=f"{type(exc).__name__}: {first}",
                                    screenshot=self._safe_screenshot(session))

    @staticmethod
    def _safe_screenshot(session: BrowserSession) -> bytes | None:
        try:
            return session.screenshot()
        except Exception:  # noqa: BLE001
            return None

    def _solve_captcha(self, page: Any) -> None:
        solve_on_page(page)

    # ------------------------------------------------------------------ steps (override per platform)
    def open_application(self, page: Any, packet: CandidatePacket) -> None:
        page.goto(packet.application_url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:  # noqa: BLE001 - pages with long-polling never go idle
            pass

    def fill(self, page: Any, packet: CandidatePacket) -> SubmissionResult:
        fields = extract_fields(page, self.form_root)
        return self.fill_fields(page, packet, fields)

    def fill_fields(self, page: Any, packet: CandidatePacket, fields: list[FormField],
                    root: str | None = None, handled: set[str] | None = None) -> SubmissionResult:
        """Fill ``fields``, then look again: answering one question often reveals another
        ("If yes, please explain", a city after a country). Up to two follow-up passes.
        ``handled``: handles of fields the caller already took care of (e.g. a resume upload)."""
        report, answers = self._fill_pass(page, packet, fields)
        seen = {f.handle for f in fields} | (handled or set())
        for _ in range(2):
            try:
                page.wait_for_timeout(400)
                revealed = [f for f in extract_fields(page, root if root is not None else self.form_root)
                            if f.handle not in seen and not f.value]
            except Exception:  # noqa: BLE001 - the page navigated or closed; nothing more to fill here
                break
            if not revealed:
                break
            seen.update(f.handle for f in revealed)
            more_report, more_answers = self._fill_pass(page, packet, revealed)
            report.extend(more_report)
            answers.extend(more_answers)
        return self._summarize(report, answers, bool(fields))

    def _fill_pass(self, page: Any, packet: CandidatePacket,
                   fields: list[FormField]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        report: list[dict[str, Any]] = []
        questions: list[FormField] = []
        for f in fields:
            kind = self.classify(f)
            if kind in ("resume", "cover_letter"):
                path = packet.resume_path if kind == "resume" else packet.cover_letter_path
                if f.type == "file":
                    ok = bool(path) and fill_field(page, f, path)
                    report.append(self._report(f, kind, path and path.rsplit("/", 1)[-1], ok))
                elif kind == "cover_letter":
                    ok = fill_field(page, f, packet.cover_letter_text)
                    report.append(self._report(f, kind, (packet.cover_letter_text or "")[:80], ok))
                continue
            override = packet.override_for(f.label or f.name or f.handle)
            if override is not None:  # you corrected this field in the review queue: your value, verbatim
                # (no placeholder: "01/02/2004" in a DD/MM/YYYY box must not be re-read as a US date and flipped)
                target = replace(f, placeholder="") if f.type in ("text", "textarea") else f
                ok = (bool(f.value) and _norm(f.value) == _norm(override)) or fill_field(page, target, override)
                report.append({**self._report(f, kind or "question", override, ok), "source": "user"})
                continue
            if kind:
                value = packet.profile_value(kind)
                if value is None and kind in ("full_name",):
                    value = packet.full_name
                if f.value and _norm(f.value) == _norm(value or ""):
                    report.append(self._report(f, kind, value, True))
                    continue
                ok = fill_field(page, f, value)
                report.append(self._report(f, kind, value, ok))
                continue
            questions.append(f)

        answers = self.resolve(packet, [q.as_question() for q in questions])
        by_handle = {a.get("field_id"): a for a in answers}
        for q in questions:
            ans = by_handle.get(q.handle)
            value = ans.get("answer") if ans else None
            ok = fill_field(page, q, value) if value not in (None, "") else False
            if not ok and q.value and value in (None, ""):
                ok = True  # nothing to change: the form already has a value here
                value = q.value
            entry = self._report(q, "question", value, ok)
            if ans:
                entry["confidence"] = ans.get("confidence")
                entry["needs_user_review"] = ans.get("needs_user_review")
            report.append(entry)
        return report, answers

    def _summarize(self, report: list[dict[str, Any]], answers: list[dict[str, Any]], had_fields: bool) -> SubmissionResult:
        total = len([r for r in report if r["kind"] != "cover_letter" or r["required"]])
        unmapped = [r for r in report if r["status"] == "unmapped" and (r["required"] or r["kind"] != "question")]
        required_unmapped = [r for r in report if r["status"] == "unmapped" and r["required"]]
        ratio = len(unmapped) / total if total else 0
        needs_review = bool(required_unmapped) or ratio > self.unmapped_review_threshold or not had_fields
        reason = None
        if not had_fields:
            reason = "No form fields were found on the application page"
        elif required_unmapped:
            reason = "Required fields need your input: " + ", ".join(r["label"][:60] for r in required_unmapped[:5])
        elif ratio > self.unmapped_review_threshold:
            reason = f"{int(ratio * 100)}% of fields could not be mapped automatically"
        if any(a.get("needs_user_review") for a in answers) and not reason:
            reason = "Some answers were generated with low confidence — please review them"
        return SubmissionResult(
            success=True, stage="staged", fields=report, answers=answers,
            needs_manual_review=needs_review, review_reason=reason,
        )

    def classify(self, f: FormField) -> str | None:
        return classify_field(f)

    def resolve(self, packet: CandidatePacket, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not questions:
            return []
        stored = {_norm(a.get("question") or ""): a for a in packet.answers}
        resolved: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        for q in questions:
            prior = stored.get(_norm(q["question"]))
            if prior and prior.get("answer") not in (None, ""):
                resolved.append({**prior, "field_id": q["field_id"], "options": q.get("options") or prior.get("options"),
                                 "required": q.get("required")})
            else:
                missing.append(q)
        if missing and packet.resolve_answers:
            resolved.extend(packet.resolve_answers(missing))
        return resolved

    @staticmethod
    def _report(f: FormField, kind: str, value: Any, ok: bool) -> dict[str, Any]:
        return {
            "label": f.label or f.name or f.handle,
            "kind": kind,
            "type": f.type,
            "value": "" if value is None else str(value)[:500],
            "required": f.required,
            "status": "filled" if ok else ("skipped" if not f.required and kind == "question" else "unmapped"),
            "options": f.options[:30],
        }

    def click_submit(self, page: Any) -> None:
        for selector in self.submit_selectors:
            loc = page.locator(selector)
            try:
                if loc.count() and loc.first.is_visible():
                    human.human_click(page, loc.first)
                    return
            except Exception:  # noqa: BLE001
                continue
        raise SubmissionError("Could not find the submit button")

    def baseline(self, page: Any) -> dict[str, Any]:
        ok, _ = looks_submitted(page)
        return {"url": page.url, "already_matched": ok}

    def _submit_button_visible(self, page: Any) -> bool:
        for selector in self.submit_selectors[:3]:
            try:
                loc = page.locator(selector)
                if loc.count() and loc.first.is_visible():
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def wait_for_confirmation(self, page: Any, baseline: dict[str, Any] | None = None, timeout_ms: int = 20000) -> tuple[bool, str | None]:
        baseline = baseline or {"url": None, "already_matched": False}
        waited = 0
        while waited <= timeout_ms:
            ok, number = looks_submitted(page)
            if ok:
                # Guard against confirmation-like text that was already on the page (e.g. in the JD).
                changed = page.url != baseline["url"] or not self._submit_button_visible(page)
                if not baseline["already_matched"] or changed:
                    return True, number
            page.wait_for_timeout(1000)
            waited += 1000
        return False, None
