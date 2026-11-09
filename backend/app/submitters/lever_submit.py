"""Lever hosted application forms (jobs.lever.co/{company}/{id}/apply)."""

from __future__ import annotations

from typing import Any

from app.submitters.base import BaseSubmitter, CandidatePacket, SubmissionError
from app.submitters.form_engine import FormField, classify_field

LEVER_NAMES = {
    "name": "full_name",
    "email": "email",
    "phone": "phone",
    "org": "current_company",
    "location": "location",
    "urls[linkedin]": "linkedin",
    "urls[github]": "github",
    "urls[portfolio]": "portfolio",
    "urls[other]": "portfolio",
    "resume": "resume",
    "comments": "cover_letter",
}


class LeverSubmitter(BaseSubmitter):
    platform = "lever"
    form_root = "form#application-form, form.application-form, form"
    submit_selectors = ("#btn-submit", "button:has-text('Submit application')", "button[type=submit]")

    def open_application(self, page: Any, packet: CandidatePacket) -> None:
        url = packet.application_url
        if "/apply" not in url:
            url = url.rstrip("/") + "/apply"
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_selector("input[name=email], input[type=file]", timeout=15000)
        except Exception as exc:
            raise SubmissionError("Lever application form not found (posting may be closed)") from exc
        # Upload the resume first: Lever parses it and pre-fills name/email/phone.
        if packet.resume_path:
            try:
                page.locator("input[name=resume], input[type=file]").first.set_input_files(packet.resume_path)
                page.wait_for_timeout(2500)
            except Exception:  # noqa: BLE001
                pass

    def classify(self, f: FormField) -> str | None:
        name = (f.name or "").lower()
        if name in LEVER_NAMES:
            return LEVER_NAMES[name]
        if name.startswith("cards[") or name.startswith("eeo["):
            return None
        return classify_field(f)
