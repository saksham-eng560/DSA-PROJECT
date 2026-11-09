"""The "Ready to submit" review sheet: everything the agent prefilled for one application, one row
per item, so you can say yes or no to each, fix what's wrong in place and submit with one click.

Rows come from the staged form (``form_fields``), the question answers (``custom_answers``), your
earlier corrections (``field_overrides``) and the cover letter. Rows that need you (flagged answers,
low confidence, required fields the agent couldn't fill) come first, the rest in form order.

Row keys: a profile kind (``email``) for profile fields, ``override_key(label)`` (``label:<label>``)
for questions, ``cover_letter``, ``resume`` and ``file:<label>`` for other uploads. Profile and
question keys are exactly the keys ``CandidatePacket.overrides`` understands.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.models.application import Application
from app.submitters.base import PROFILE_KINDS, override_key

LOW_CONFIDENCE = 0.7  # the same bar question_answerer uses for needs_user_review
_RESUME_LABEL = re.compile(r"resume|\bcv\b|curriculum", re.I)
_ANSWER_TYPES = {"select": "select", "radio": "radio", "checkbox": "checkbox", "number": "number", "textarea": "textarea"}


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _row(key: str, label: str, kind: str, value: str, *, type_: str = "text", options: list[str] | None = None,
         required: bool = False, filled: bool = False, flagged: bool = False, source: str = "agent",
         confidence: float | None = None, note: str | None = None, url: str | None = None) -> dict[str, Any]:
    return {"key": key, "label": label, "kind": kind, "value": value, "type": type_, "options": list(options or [])[:50],
            "required": required, "filled": filled, "flagged": flagged, "source": source, "confidence": confidence,
            "note": note, "url": url}


def _flag_note(*, required: bool, value: str, unmapped: bool, needs_review: bool, confidence: float | None) -> str | None:
    if required and not value.strip():
        return "Required: the agent couldn't fill this in"
    if unmapped:
        return "The form didn't take this value: check it or pick another"
    if confidence is not None and confidence < LOW_CONFIDENCE:
        return f"The agent is only {round(confidence * 100)}% sure: check it"
    if needs_review:
        return "The agent wants you to check this one"
    return None


def needs_attention(row: dict[str, Any]) -> bool:
    """Flagged, or required and still empty (uploads are informational: you can't change them here)."""
    empty = row["required"] and not row["value"].strip() and row["kind"] not in ("resume", "file")
    return bool(row["flagged"] or empty)


def build_rows(form_fields: list[dict[str, Any]] | None, custom_answers: list[dict[str, Any]] | None,
               overrides: dict[str, Any] | None, cover_letter: str | None, resume_url: str | None = None) -> list[dict[str, Any]]:
    """The ordered review sheet: rows needing your attention first, then the rest in form order."""
    fields = [f for f in form_fields or [] if isinstance(f, dict)]
    overrides = overrides or {}
    answers = {override_key(_text(a.get("question"))): a for a in custom_answers or []
               if isinstance(a, dict) and _text(a.get("question")).strip()}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(row: dict[str, Any]) -> None:
        if row["key"] not in seen:  # a question in both the form and the answers, the same profile value twice...
            seen.add(row["key"])
            rows.append(row)

    def cover_row(required: bool = False, label: str = "Cover letter") -> dict[str, Any]:
        letter = _text(cover_letter)
        return _row("cover_letter", label, "cover_letter", letter, type_="textarea", required=required,
                    filled=bool(letter.strip()), flagged=required and not letter.strip(),
                    note="Required: write or paste a cover letter" if required and not letter.strip() else None)

    def resume_row(label: str = "Resume", value: str = "", required: bool = False, unmapped: bool = False) -> dict[str, Any]:
        return _row("resume", label, "resume", value or "Your resume", type_="file", required=required,
                    filled=bool(resume_url) and not unmapped, flagged=required and unmapped, url=resume_url,
                    note="The upload didn't take on the staged form: check the screenshot" if required and unmapped else None)

    # The resume row is the upload labelled resume / CV (else the first upload the agent put the resume in)
    uploads = [i for i, f in enumerate(fields) if f.get("kind") == "resume"]
    resume_at = next((i for i in uploads if _RESUME_LABEL.search(_text(fields[i].get("label")))), uploads[0] if uploads else None)

    for i, f in enumerate(fields):
        label = _text(f.get("label")).strip() or "Untitled field"
        kind = _text(f.get("kind")) or "question"
        type_ = _text(f.get("type")) or "text"
        required = bool(f.get("required"))
        unmapped = f.get("status") == "unmapped"
        if kind == "cover_letter":
            add(cover_row(required, label))
            continue
        if i == resume_at:
            add(resume_row(label, _text(f.get("value")), required, unmapped))
            continue
        if kind == "resume" or type_ == "file":  # another upload (transcript...): shown, not editable here
            add(_row("file:" + override_key(label).split(":", 1)[1], label, "file", _text(f.get("value")), type_="file", required=required,
                     filled=f.get("status") == "filled", flagged=required and unmapped,
                     note="This upload didn't take on the staged form" if required and unmapped else None))
            continue

        label_key = override_key(label)
        profile = kind in PROFILE_KINDS
        key = kind if profile else label_key
        answer = None if profile else answers.get(label_key)
        # What the submitter will type: your label correction, your profile correction, the stored answer, the form's value
        if label_key in overrides:
            value, user_set = _text(overrides[label_key]), True
        elif profile and kind in overrides:
            value, user_set = _text(overrides[kind]), True
        elif answer is not None and _text(answer.get("answer")).strip():
            value, user_set = _text(answer.get("answer")), answer.get("source") == "user"
        else:
            value, user_set = _text(f.get("value")), False
        origin = answer or f
        confidence = origin.get("confidence")
        # "Unmapped" describes the staged value; an answer changed since then hasn't been tried yet
        unmapped_now = unmapped and not user_set and (answer is None or _text(answer.get("answer")) == _text(f.get("value")))
        note = None if user_set else _flag_note(required=required, value=value, unmapped=unmapped_now,
                                                needs_review=bool(origin.get("needs_user_review")), confidence=confidence)
        add(_row(key, label, "profile" if profile else "question", value, type_=type_,
                 options=f.get("options") or (answer or {}).get("options"), required=required,
                 filled=bool(value.strip()) and not unmapped_now, flagged=note is not None,
                 source="user" if user_set else ("profile" if profile else _text((answer or {}).get("source")) or "agent"),
                 confidence=None if user_set else confidence, note=note))

    # Answers the form report doesn't list (pre-fetched ATS questions, multi-step forms)
    for a in (custom_answers or []):
        question = _text(a.get("question")).strip() if isinstance(a, dict) else ""
        if not question:
            continue
        key = override_key(question)
        user_set = key in overrides or a.get("source") == "user"
        value = _text(overrides[key]) if key in overrides else _text(a.get("answer"))
        required = bool(a.get("required"))
        confidence = a.get("confidence")
        note = None if user_set else _flag_note(required=required, value=value, unmapped=False,
                                                needs_review=bool(a.get("needs_user_review")), confidence=confidence)
        add(_row(key, question, "question", value, type_=_ANSWER_TYPES.get(_text(a.get("field_type")), "text"),
                 options=a.get("options"), required=required, filled=bool(value.strip()), flagged=note is not None,
                 source="user" if user_set else _text(a.get("source")) or "agent",
                 confidence=None if user_set else confidence, note=note))

    if "cover_letter" not in seen and _text(cover_letter).strip():
        add(cover_row())
    if "resume" not in seen and resume_url:
        add(resume_row())

    return [r for r in rows if needs_attention(r)] + [r for r in rows if not needs_attention(r)]


def review_rows(app: Application, resume_url: str | None = None) -> list[dict[str, Any]]:
    return build_rows(app.form_fields, app.custom_answers, app.field_overrides, app.cover_letter, resume_url)


@dataclass
class SheetEdits:
    overrides: dict[str, str]
    answers: list[dict[str, Any]]
    cover_letter: str | None
    changed: list[str]  # labels of the rows you changed


def apply_edits(app: Application, submitted: list[tuple[str, str]], cover_letter: str | None = None) -> SheetEdits:
    """Work out what your confirmed / corrected rows change, without touching ``app`` yet.

    A row sent back unchanged is simply confirmed. A changed profile row becomes an override for that
    profile value; a changed question becomes your answer (what the agent learns from) and a label
    override (so exactly your text is typed, however the form is read on submit).
    Raises ``ValueError`` for a key that isn't a review-sheet key.
    """
    current = {r["key"]: r for r in review_rows(app)}
    overrides = {str(k): _text(v) for k, v in (app.field_overrides or {}).items()}
    answers = [dict(a) for a in app.custom_answers or [] if isinstance(a, dict)]
    by_key = {override_key(_text(a.get("question"))): a for a in answers if _text(a.get("question")).strip()}
    letter = app.cover_letter
    changed: list[str] = []
    for key, raw in submitted:
        if key in ("resume",) or key.startswith("file:"):
            continue  # uploads are informational
        if key == "cover_letter":
            if raw != _text(letter):
                letter = raw
                changed.append("Cover letter")
            continue
        if key not in PROFILE_KINDS and not (key.startswith("label:") and len(key) > 6):
            raise ValueError(f"Unknown field '{key[:80]}'")
        value = raw.strip()
        row = current.get(key)
        if row is not None and value == row["value"].strip():
            continue  # confirmed as is
        overrides[key] = value
        changed.append(row["label"] if row else key)
        if key in PROFILE_KINDS:
            continue
        answer = by_key.get(key)
        if answer is None and row is not None:
            answer = {"question": row["label"], "field_type": row["type"] if row["type"] in _ANSWER_TYPES else "text",
                      "options": row["options"] or None, "required": row["required"]}
            answers.append(answer)
            by_key[key] = answer
        if answer is not None:
            answer.update(answer=value, needs_user_review=False, source="user", confidence=1.0)
    if cover_letter is not None and cover_letter != _text(letter):
        letter = cover_letter
        if "Cover letter" not in changed:
            changed.append("Cover letter")
    return SheetEdits(overrides=overrides, answers=answers, cover_letter=letter, changed=changed)


def missing_required(form_fields: list[dict[str, Any]] | None, edits: SheetEdits) -> list[str]:
    """Labels of required rows that would still be empty once ``edits`` are applied."""
    rows = build_rows(form_fields, edits.answers, edits.overrides, edits.cover_letter)
    return [r["label"] for r in rows if r["required"] and not r["value"].strip() and r["kind"] not in ("resume", "file")]
