"""Greenhouse hosted application forms (boards.greenhouse.io and job-boards.greenhouse.io)."""

from __future__ import annotations

from typing import Any

from app.automation import human
from app.submitters.base import BaseSubmitter, CandidatePacket, SubmissionError
from app.submitters.form_engine import FormField, classify_field


class GreenhouseSubmitter(BaseSubmitter):
    platform = "greenhouse"
    submit_selectors = (
        "button:has-text('Submit application')",
        "button:has-text('Submit Application')",
        "#submit_app",
        "input#submit_app",
        "button[type=submit]",
        "input[type=submit]",
    )

    FORM_ROOTS = ("#application-form", "form#application_form", "#application", "form[action*='applications']", "main form", "form")

    def open_application(self, page: Any, packet: CandidatePacket) -> None:
        super().open_application(page, packet)
        # Some boards show the description first with an "Apply" button that reveals / scrolls to the form.
        for selector in ("button:has-text('Apply')", "a:has-text('Apply for this job')", "a[href='#app']"):
            loc = page.locator(selector)
            try:
                if loc.count() and loc.first.is_visible() and not page.locator("input[type=file]").count():
                    human.human_click(page, loc.first)
                    page.wait_for_timeout(800)
                    break
            except Exception:  # noqa: BLE001
                continue
        # Embedded boards on company sites render the form in an iframe
        frame_el = page.locator("iframe#grnhse_iframe, iframe[src*='greenhouse.io']")
        if frame_el.count():
            src = frame_el.first.get_attribute("src")
            if src:
                page.goto(src, wait_until="domcontentloaded")
        for root in self.FORM_ROOTS:
            if page.locator(root).count():
                self.form_root = root
                break
        else:
            raise SubmissionError("Greenhouse application form not found (posting may be closed)")

    def classify(self, f: FormField) -> str | None:
        ident = f"{f.id} {f.name}".lower()
        if "question_" in ident or ident.startswith("job_application[answers"):
            kind = classify_field(f)
            # Greenhouse custom questions sometimes ask for LinkedIn/website: keep profile mapping for those
            return kind if kind in ("linkedin", "github", "portfolio") else None
        if f.id in ("country", "candidate-location") or "location" in ident:
            return "location" if f.id != "country" else None
        return classify_field(f)
