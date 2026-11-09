"""Unknown ATS: DOM analysis + LLM field mapping (PLAN.md §9.4)."""

from __future__ import annotations

import logging
from typing import Any

from app.automation import human
from app.services import llm_schemas
from app.services.llm import LLMError, get_llm, render_prompt
from app.submitters.base import BaseSubmitter, CandidatePacket, SubmissionResult
from app.submitters.form_engine import FormField, classify_field, extract_fields

logger = logging.getLogger(__name__)

APPLY_LINK_SELECTORS = (
    "a:has-text('Apply now')",
    "button:has-text('Apply now')",
    "a:has-text('Apply for this job')",
    "button:has-text('Apply for this job')",
    "a:has-text('Apply')",
    "button:has-text('Apply')",
)


class GenericSubmitter(BaseSubmitter):
    platform = "generic"

    def open_application(self, page: Any, packet: CandidatePacket) -> None:
        super().open_application(page, packet)
        # If the page is a job description without a form, follow the "Apply" call-to-action.
        if not page.locator("form input, form textarea").count():
            for selector in APPLY_LINK_SELECTORS:
                loc = page.locator(selector)
                try:
                    if loc.count() and loc.first.is_visible():
                        human.human_click(page, loc.first)
                        page.wait_for_load_state("domcontentloaded")
                        page.wait_for_timeout(1500)
                        break
                except Exception:  # noqa: BLE001
                    continue
        # Choose the form with the most inputs as the application form
        best = page.evaluate(
            """() => { const forms = Array.from(document.querySelectorAll('form'));
                       let best = -1, n = 0;
                       forms.forEach((f, i) => { const c = f.querySelectorAll('input,select,textarea').length; if (c > n) { n = c; best = i; } });
                       if (best >= 0) forms[best].setAttribute('data-aa-form', '1');
                       return best; }"""
        )
        self.form_root = "form[data-aa-form='1']" if best is not None and best >= 0 else None

    def fill(self, page: Any, packet: CandidatePacket) -> SubmissionResult:
        fields = extract_fields(page, self.form_root)
        self._llm_map = self.llm_mapping(fields, packet)
        return self.fill_fields(page, packet, fields)

    def classify(self, f: FormField) -> str | None:
        kind = classify_field(f)
        if kind:
            return kind
        mapping = getattr(self, "_llm_map", {}).get(f.handle)
        if mapping and mapping.get("value_source") == "profile" and mapping.get("profile_key"):
            return mapping["profile_key"]
        if mapping and mapping.get("value_source") in ("resume_file", "cover_letter"):
            return "resume" if mapping["value_source"] == "resume_file" else "cover_letter"
        return None

    def llm_mapping(self, fields: list[FormField], packet: CandidatePacket) -> dict[str, dict[str, Any]]:
        unknown = [f for f in fields if classify_field(f) is None]
        llm = get_llm()
        if not unknown or not llm.available:
            return {}
        try:
            data = llm.complete_json(
                render_prompt(
                    "form_field_mapper",
                    candidate_json={k: packet.profile_value(k) for k in (
                        "first_name", "last_name", "full_name", "email", "phone", "location", "linkedin", "github",
                        "portfolio", "current_company", "current_title")},
                    company_name=packet.company_name,
                    role_title=packet.role_title,
                    fields_json=[{"field_id": f.handle, "label": f.label, "type": f.type, "name": f.name,
                                  "required": f.required, "options": f.options[:25]} for f in unknown],
                ),
                schema=llm_schemas.FORM_MAPPING_SCHEMA,
                effort="low",
                task="form_mapping",
            )
        except LLMError as exc:
            logger.warning("LLM form mapping failed: %s", exc)
            return {}
        return {m.get("field_id"): m for m in data.get("mappings") or [] if m.get("field_id")}
