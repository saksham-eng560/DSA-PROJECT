"""Answer ATS custom questions (PLAN.md §8 TASK: CUSTOM QUESTION ANSWERING).

Order of precedence:
1. Pre-configured ``user_field_mappings`` (exact user answers).
2. Deterministic rules for factual questions (authorization, sponsorship, salary, EEO, links...).
3. LLM for subjective questions (with confidence + needs_user_review).
4. Conservative fallback text flagged for user review.
"""

from __future__ import annotations

import difflib
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.models.job import Job
from app.schemas.resume_content import ResumeContent
from app.services import llm_schemas
from app.services.job_matcher import estimate_years_experience, job_text
from app.services.llm import LLMError, get_llm, render_prompt
from app.services.text_utils import clip, normalize_text, truncate

logger = logging.getLogger(__name__)

# Suggested field-mapping keys shown in the Settings page.
STANDARD_FIELDS: dict[str, dict[str, Any]] = {
    "work_authorization": {"label": "Legally authorized to work in your target country?", "type": "radio", "options": ["Yes", "No"]},
    "requires_sponsorship": {"label": "Will you now or in the future require visa sponsorship?", "type": "radio", "options": ["Yes", "No"]},
    "willing_to_relocate": {"label": "Willing to relocate?", "type": "radio", "options": ["Yes", "No"]},
    "years_experience": {"label": "Total years of professional experience", "type": "number"},
    "salary_expectation": {"label": "Salary expectation (leave empty to use your preferences)", "type": "text"},
    "expected_stipend": {"label": "Expected internship stipend per month, e.g. 15000 (INR)", "type": "number"},
    "notice_period": {"label": "Notice period / earliest start date", "type": "text"},
    "highest_education": {"label": "Highest level of education", "type": "select",
                          "options": ["High School", "Associate's", "Bachelor's", "Master's", "PhD"]},
    "how_did_you_hear": {"label": "How did you hear about us?", "type": "text"},
    "over_18": {"label": "Are you at least 18 years old?", "type": "radio", "options": ["Yes", "No"]},
    "pronouns": {"label": "Pronouns", "type": "text"},
    "gender": {"label": "Gender (EEO)", "type": "text"},
    "race_ethnicity": {"label": "Race / ethnicity (EEO)", "type": "text"},
    "hispanic_latino": {"label": "Hispanic or Latino? (EEO)", "type": "text"},
    "veteran_status": {"label": "Veteran status (EEO)", "type": "text"},
    "disability_status": {"label": "Disability status (EEO)", "type": "text"},
    "address": {"label": "Street address", "type": "text"},
    "city": {"label": "City", "type": "text"},
    "state": {"label": "State / province", "type": "text"},
    "postal_code": {"label": "ZIP / postal code", "type": "text"},
    "country": {"label": "Country", "type": "text"},
    "website": {"label": "Personal website / portfolio", "type": "text"},
    "phone_country_code": {"label": "Phone country code (read from your resume's phone number if empty)", "type": "text"},
    "school": {"label": "College / university (from your resume if empty)", "type": "text"},
    "degree": {"label": "Degree, e.g. B.Tech Computer Science (from your resume if empty)", "type": "text"},
    "major": {"label": "Major / branch / field of study (from your resume if empty)", "type": "text"},
    "graduation_year": {"label": "Graduation year (from your resume if empty)", "type": "number"},
    "gpa": {"label": "CGPA / GPA / percentage (from your resume if empty)", "type": "text"},
    "date_of_birth": {"label": "Date of birth (only used when a form asks; never guessed)", "type": "text"},
    "workday_password": {"label": "Password to use when creating ATS accounts (stored encrypted)", "type": "password"},
}

DECLINE_PATTERNS = ("decline", "prefer not", "do not wish", "don't wish", "not wish to", "choose not", "not to disclose", "not specified")


# Different ways forms spell the same answer (degree levels, countries): compared by canonical key.
_ALIASES: list[tuple[str, re.Pattern[str]]] = [
    ("phd", re.compile(r"\b(ph\.?\s?d|doctora(te|l)|doctor of)\b")),
    ("master", re.compile(r"\b(master'?s?|m\.?\s?tech|m\.?\s?sc|m\.?\s?s|mba|m\.?\s?e|m\.?\s?a|post ?grad(uate)?|mca)\b")),
    ("bachelor", re.compile(r"\b(bachelor'?s?|b\.?\s?tech|b\.?\s?e|b\.?\s?sc|b\.?\s?s|b\.?\s?a|bca|b\.?\s?com|undergrad(uate)?)\b")),
    ("associate", re.compile(r"\bassociate'?s? degree\b|\bassociate'?s?$")),
    ("high school", re.compile(r"\b(high school|secondary school|12th|class xii|hsc|ged)\b")),
    ("india", re.compile(r"^(india|in|ind|bharat|\+?91)$|\bindia\b")),
    ("united states", re.compile(r"^(us|usa|u\.s\.a?\.?|united states( of america)?|america|\+?1)$|\bunited states\b")),
    ("united kingdom", re.compile(r"^(uk|u\.k\.|great britain|britain|england|united kingdom|\+?44)$|\bunited kingdom\b")),
    ("canada", re.compile(r"^(ca|can|canada)$|\bcanada\b")),
    ("delhi", re.compile(r"\b(new delhi|delhi)\b")),
    ("bengaluru", re.compile(r"\b(bengaluru|bangalore)\b")),
    ("gurugram", re.compile(r"\b(gurugram|gurgaon)\b")),
    ("mumbai", re.compile(r"\b(mumbai|bombay)\b")),
]


def _canonical(text: str) -> str | None:
    for key, pattern in _ALIASES:
        if pattern.search(text):
            return key
    return None


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text))


def choose_option(answer: str, options: list[str]) -> str:
    """Return the option that best matches ``answer``; ``answer`` itself when none is a good match.

    exact > yes/no > "decline" > same degree / country > whole-word overlap > close spelling.
    Never falls back to an unrelated option (e.g. "India" never picks "British Indian Ocean Territory").
    """
    if not options:
        return answer
    norm = normalize_text(answer)
    by_norm = {normalize_text(o): o for o in options}
    if norm in by_norm:
        return by_norm[norm]
    if norm in ("yes", "no") or re.match(r"(yes|no)\b", norm):
        head = "yes" if norm.startswith("yes") else "no"
        for o in options:
            if re.match(rf"{head}\b", normalize_text(o)):
                return o
    if any(p in norm for p in DECLINE_PATTERNS):
        for o in options:
            if any(p in normalize_text(o) for p in DECLINE_PATTERNS):
                return o
    answer_words = _words(norm)
    canon = _canonical(norm)
    if canon:
        same = [o for o in options if _canonical(normalize_text(o)) == canon]
        if same:  # e.g. two bachelor's options: the one sharing the most words ("B.S." for "B.S. Computer Science")
            return min(same, key=lambda o: (-len(answer_words & _words(normalize_text(o))), len(o)))
    best, best_score = None, 0.0
    for o in options:
        option_words = _words(normalize_text(o))
        if not answer_words or not option_words:
            continue
        shared = answer_words & option_words
        if not shared:
            continue
        score = len(shared) / len(answer_words | option_words)
        if answer_words <= option_words or option_words <= answer_words:
            score += 0.5
        if score > best_score:
            best, best_score = o, score
    if best is not None and best_score >= 0.5:
        return best
    for o in options:
        on = normalize_text(o)
        if len(norm) >= 4 and len(on) >= 4 and (re.search(rf"\b{re.escape(norm)}\b", on) or re.search(rf"\b{re.escape(on)}\b", norm)):
            return o
    match = difflib.get_close_matches(norm, list(by_norm), n=1, cutoff=0.6)
    return by_norm[match[0]] if match else answer


class _Ctx:
    def __init__(self, resume: ResumeContent, prefs: dict[str, Any], mappings: dict[str, str], job: Job | None) -> None:
        self.resume = resume
        self.prefs = prefs
        self.mappings = mappings
        self.job = job
        self.question = ""  # the (normalized) question being answered

    def m(self, key: str) -> str | None:
        value = self.mappings.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None


_CURRENCIES = {"INR": r"\binr\b|₹|\brs\.?\s|rupee|\blpa\b|lakh", "USD": r"\busd\b|\$|dollar",
               "EUR": r"\beur\b|€|\beuro", "GBP": r"\bgbp\b|£|pound sterling"}
_MONTHLY = re.compile(r"per month|monthly|/\s*month|\bstipend\b")


def _salary(ctx: _Ctx) -> tuple[str | None, float]:
    if _MONTHLY.search(ctx.question):  # an internship stipend is monthly; your salary range is yearly
        return (ctx.m("expected_stipend"), 0.95) if ctx.m("expected_stipend") else (None, 0.0)
    if ctx.m("salary_expectation"):
        return ctx.m("salary_expectation"), 0.95
    low = ctx.prefs.get("salary_min")
    if low:
        asked = next((cur for cur, pattern in _CURRENCIES.items() if re.search(pattern, ctx.question)), None)
        if asked and asked != (ctx.prefs.get("salary_currency") or "USD"):
            return None, 0.0  # never answer a ₹ question with a $ figure
        return str(int(low)), 0.85  # behavioral rule #9: bottom of the range
    return None, 0.0


def _years(ctx: _Ctx) -> tuple[str | None, float]:
    if ctx.m("years_experience"):
        return ctx.m("years_experience"), 0.95
    years = estimate_years_experience(ctx.resume)
    return str(round(years)), 0.7


def _eeo(key: str) -> Callable[[_Ctx], tuple[str | None, float]]:
    def resolver(ctx: _Ctx) -> tuple[str | None, float]:
        return (ctx.m(key) or "Decline to self-identify"), 0.9
    return resolver


def _mapping(key: str, default: str | None = None, default_conf: float = 0.5) -> Callable[[_Ctx], tuple[str | None, float]]:
    def resolver(ctx: _Ctx) -> tuple[str | None, float]:
        if ctx.m(key):
            return ctx.m(key), 0.95
        return default, default_conf if default is not None else 0.0
    return resolver


def _profile(getter: Callable[[ResumeContent], str]) -> Callable[[_Ctx], tuple[str | None, float]]:
    def resolver(ctx: _Ctx) -> tuple[str | None, float]:
        value = getter(ctx.resume)
        return (value, 0.95) if value else (None, 0.0)
    return resolver


def _current(attr: str) -> Callable[[ResumeContent], str]:
    return lambda r: getattr(r.experience[0], attr) if r.experience else ""


def _mapping_or(key: str, derive: Callable[[ResumeContent], str | None], confidence: float = 0.8) -> Callable[[_Ctx], tuple[str | None, float]]:
    """Your saved answer; otherwise read it off your resume."""
    def resolver(ctx: _Ctx) -> tuple[str | None, float]:
        if ctx.m(key):
            return ctx.m(key), 0.95
        try:
            value = derive(ctx.resume)
        except (IndexError, AttributeError, ValueError):
            value = None
        return (value, confidence) if value else (None, 0.0)
    return resolver


def _edu(attr: str) -> Callable[[ResumeContent], str | None]:
    return lambda r: getattr(r.education[0], attr) or None if r.education else None


def _grad_year(r: ResumeContent) -> str | None:
    if not r.education:
        return None
    m = re.search(r"(19|20)\d{2}", r.education[0].end_date or "")
    return m.group(0) if m else None


def _location_part(index: int) -> Callable[[ResumeContent], str | None]:
    """'New Delhi, Delhi, India' -> city (0), state (1 of 3), country (last)."""
    def derive(r: ResumeContent) -> str | None:
        parts = [p.strip() for p in (r.personal_info.location or "").split(",") if p.strip()]
        if not parts or "remote" in parts[0].lower():
            return None
        if index == -1:
            return parts[-1] if len(parts) > 1 else None
        if index == 1:
            return parts[1] if len(parts) > 2 else None
        return parts[0]
    return derive


def _dial_code(r: ResumeContent) -> str | None:
    phone = (r.personal_info.phone or "").strip()
    m = re.match(r"\+(\d{1,3})\b|\+(\d{1,3})[\s-]", phone)
    if m:
        return f"+{m.group(1) or m.group(2)}"
    country = normalize_text(_location_part(-1)(r) or "")
    return {"india": "+91", "united states": "+1", "usa": "+1", "united kingdom": "+44", "uk": "+44", "canada": "+1"}.get(country)


RULES: list[tuple[re.Pattern[str], Callable[[_Ctx], tuple[str | None, float]]]] = [
    (re.compile(r"(?:require|need).{0,40}sponsor|sponsor.{0,40}(?:require|need)"), _mapping("requires_sponsorship")),
    (re.compile(r"authori[sz]ed to work|legally (?:able|eligible|authori)|right to work|work permit|eligible to work"), _mapping("work_authorization")),
    (re.compile(r"sponsor|visa status|h-?1b|immigration"), _mapping("requires_sponsorship")),
    (re.compile(r"relocat"), _mapping("willing_to_relocate")),
    (re.compile(r"salary|compensation|pay expectation|expected (?:pay|ctc|stipend)|desired pay|\bstipend\b|\bctc\b"), _salary),
    (re.compile(r"how many years|years of (?:professional |relevant |work )?experience"), _years),
    (re.compile(r"notice period|start date|when can you start|earliest.*start|available to start"), _mapping("notice_period", "2 weeks", 0.5)),
    (re.compile(r"how did you (?:hear|find|learn)|where did you (?:hear|find)|referr?al source|source of application"), _mapping("how_did_you_hear", "Job board", 0.6)),
    (re.compile(r"18 years|over 18|at least 18|legal age"), _mapping("over_18", "Yes", 0.8)),
    (re.compile(r"highest (?:level of )?(?:education|degree|qualification)"), _mapping_or("highest_education", _edu("degree"), 0.8)),
    (re.compile(r"pronoun"), _mapping("pronouns")),
    (re.compile(r"hispanic|latino"), _eeo("hispanic_latino")),
    (re.compile(r"gender|\bsex\b"), _eeo("gender")),
    (re.compile(r"\brace\b|ethnic"), _eeo("race_ethnicity")),
    (re.compile(r"veteran"), _eeo("veteran_status")),
    (re.compile(r"disabilit"), _eeo("disability_status")),
    (re.compile(r"linkedin"), _profile(lambda r: r.personal_info.linkedin)),
    (re.compile(r"github"), _profile(lambda r: r.personal_info.github)),
    (re.compile(r"portfolio|personal (?:web)?site|website"), lambda c: (c.m("website") or c.resume.personal_info.portfolio or None, 0.9)),
    (re.compile(r"current (?:or most recent )?(?:company|employer)"), _profile(_current("company"))),
    (re.compile(r"current (?:or most recent )?(?:job )?title|current (?:role|position)"), _profile(_current("title"))),
    (re.compile(r"street address|^address"), _mapping("address")),
    (re.compile(r"country code|dial(?:ling)? code|phone code|calling code"), _mapping_or("phone_country_code", _dial_code, 0.85)),
    (re.compile(r"current (?:city|location)|where are you (?:currently )?(?:located|based)|^location$"),
     _mapping_or("city", lambda r: r.personal_info.location or None, 0.85)),
    (re.compile(r"\bcity\b"), _mapping_or("city", _location_part(0))),
    (re.compile(r"\bstate\b|province"), _mapping_or("state", _location_part(1), 0.7)),
    (re.compile(r"zip|postal|pin ?code"), _mapping("postal_code")),
    (re.compile(r"country"), _mapping_or("country", _location_part(-1))),
    (re.compile(r"(?:university|college|school|institute|institution)(?: name)?\b|where did you study|alma mater"),
     _mapping_or("school", _edu("institution"), 0.9)),
    (re.compile(r"\bdegree\b|qualification"), _mapping_or("degree", _edu("degree"), 0.85)),
    (re.compile(r"\bmajor\b|field of study|discipline|\bbranch\b|speciali[sz]ation|\bstream\b|area of study"),
     _mapping_or("major", _edu("field"), 0.85)),
    (re.compile(r"graduat(?:ion|e|ing)|passing year|year of passing|pass(?:ing)?[- ]out year|\bbatch\b"),
     _mapping_or("graduation_year", _grad_year, 0.85)),
    (re.compile(r"\bc?gpa\b|grade point|\bcgpa\b|percentage|aggregate marks"), _mapping_or("gpa", _edu("gpa"), 0.85)),
    (re.compile(r"\bi (?:agree|acknowledge|certify|consent|confirm|understand|have read)|privacy (?:policy|notice)|terms (?:of|and)|consent to|accurate and complete"),
     lambda c: ("Yes", 0.85)),
    (re.compile(r"previously (?:worked|been employed)|former employee|worked (?:here|for us) before"), lambda c: ("No", 0.6)),
    (re.compile(r"non-?compete|restrictive covenant"), lambda c: ("No", 0.6)),
]


def _field_mapping_lookup(question: str, mappings: dict[str, str]) -> str | None:
    q = normalize_text(question)
    for name, value in mappings.items():
        n = normalize_text(name.replace("_", " "))
        if n and (n == q or (len(n) > 6 and n in q)):
            return value
    return None


def rule_based_answer(question: dict[str, Any], ctx: _Ctx) -> dict[str, Any] | None:
    text = normalize_text(question.get("question") or "")
    options = question.get("options") or []
    field_type = question.get("field_type") or ("select" if options else "text")
    direct = _field_mapping_lookup(text, ctx.mappings)
    ctx.question = text
    if direct:
        answer, confidence = direct, 0.97
    else:
        answer, confidence = None, 0.0
        for pattern, resolver in RULES:
            if pattern.search(text):
                answer, confidence = resolver(ctx)
                break
    if answer is None:
        return None
    if options:
        chosen = choose_option(answer, options)
        if chosen not in options:
            if not direct and not FACTUAL.search(text):
                return None  # e.g. "Do you have a degree?" (Yes / No): a resume fact isn't an option, so ask the LLM
            confidence = min(confidence, 0.4)
        answer = chosen
    return {
        "question": question.get("question"),
        "field_type": field_type,
        "answer": _fit(str(answer), question),
        "confidence": round(confidence, 2),
        "needs_user_review": confidence < 0.7,
        "source": "rule",
    }


def _fit(answer: str, question: dict[str, Any]) -> str:
    """Respect the field's character limit (cut at a sentence or word boundary)."""
    limit = question.get("max_length")
    if not limit or len(answer) <= int(limit):
        return answer
    return clip(answer, int(limit))


# Facts about the candidate that must never be guessed: left blank for the user to answer at approval.
FACTUAL = re.compile(
    r"sponsor|visa|immigration|authori[sz]ed to work|legally|right to work|work permit|eligible to work|citizen"
    r"|convict|criminal|felony|clearance|relocat|18 years|over 18|legal age|background check|drug (?:test|screen)"
    r"|date of birth|\bdob\b|birth ?date|aadhaar|\bpan\b|passport|social security|\bssn\b"
)

# Standard answers learned from the user's approvals, so the same question is answered automatically next time.
LEARNABLE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:require|need).{0,40}sponsor|sponsor.{0,40}(?:require|need)"), "requires_sponsorship"),
    (re.compile(r"authori[sz]ed to work|legally (?:able|eligible|authori)|right to work|eligible to work"), "work_authorization"),
    (re.compile(r"relocat"), "willing_to_relocate"),
    (re.compile(r"18 years|over 18|at least 18|legal age"), "over_18"),
    (re.compile(r"notice period|when can you start|earliest.*start|available to start"), "notice_period"),
    (re.compile(r"highest (?:level of )?(?:education|degree)"), "highest_education"),
]


def learnable_key(question: str) -> str | None:
    text = normalize_text(question)
    return next((key for pattern, key in LEARNABLE if pattern.search(text)), None)


def fallback_answer(question: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    options = question.get("options") or []
    field_type = question.get("field_type") or ("select" if options else "text")
    q = normalize_text(question.get("question") or "")
    resume = ctx.resume
    company = ctx.job.company_name if ctx.job else "your company"
    role = ctx.job.role_title if ctx.job else "open"
    if FACTUAL.search(q):
        answer = ""
    elif options:
        answer = options[0]
    elif field_type == "number":
        answer = ""
    elif re.search(r"\bwhy\b|interest(?:ed)? in|motivat|excite", q):
        skills = ", ".join(resume.skills.technical[:3]) or "my background"
        answer = (
            f"I'm interested in {company} because the {role} role lines up with the work I enjoy most. "
            f"My experience with {skills} would let me contribute quickly, and I'm excited to grow with the team."
        )
    elif re.search(r"tell us about|describe|summary|about yourself|statement|cover letter|additional information", q):
        answer = resume.summary or ""
    else:
        answer = ""
    return {
        "question": question.get("question"),
        "field_type": field_type,
        "answer": answer,
        "confidence": 0.3,
        "needs_user_review": True,
        "source": "fallback",
    }


def answer_questions(
    questions: list[dict[str, Any]],
    resume_content: dict[str, Any],
    prefs: dict[str, Any],
    field_mappings: dict[str, str],
    job: Job | None = None,
    use_llm: bool = True,
) -> list[dict[str, Any]]:
    resume = ResumeContent.model_validate(resume_content)
    ctx = _Ctx(resume, prefs, field_mappings, job)
    answers: dict[int, dict[str, Any]] = {}
    pending: list[tuple[int, dict[str, Any]]] = []
    for idx, q in enumerate(questions):
        ans = rule_based_answer(q, ctx)
        if ans is not None:
            answers[idx] = ans
        else:
            pending.append((idx, q))

    llm = get_llm()
    if pending and use_llm and llm.available:
        low = prefs.get("salary_min")
        high = prefs.get("salary_max")
        salary_hint = f"{low} - {high} {prefs.get('salary_currency') or 'USD'}" if low else "not specified - ask the user"
        try:
            data = llm.complete_json(
                render_prompt(
                    "question_answerer",
                    salary_hint=salary_hint,
                    company_name=job.company_name if job else "",
                    role_title=job.role_title if job else "",
                    job_description_text=truncate(job_text(job), 6000) if job else "",
                    resume_json=resume.to_dict(),
                    field_mappings_json={k: v for k, v in field_mappings.items() if "password" not in k},
                    questions_json=[
                        {"question": q.get("question"), "field_type": q.get("field_type"), "options": q.get("options") or [],
                         "required": bool(q.get("required")),
                         **({"max_length": q["max_length"]} if q.get("max_length") else {})}
                        for _, q in pending
                    ],
                    today=datetime.now(UTC).date().isoformat(),
                ),
                schema=llm_schemas.CUSTOM_ANSWERS_SCHEMA,
                effort="medium",  # these answers go out under your name: worth a careful read
                task="question_answering",
            )
            llm_answers = data.get("custom_answers") or []
            by_question = {normalize_text(a.get("question") or ""): a for a in llm_answers}
            for pos, (idx, q) in enumerate(pending):
                a = by_question.get(normalize_text(q.get("question") or "")) or (llm_answers[pos] if pos < len(llm_answers) else None)
                if not a:
                    continue
                options = q.get("options") or []
                answer = str(a.get("answer") or "")
                confidence = float(a.get("confidence") or 0.5)
                if options:
                    chosen = choose_option(answer, options)
                    if chosen not in options:
                        confidence = min(confidence, 0.4)
                    answer = chosen
                answers[idx] = {
                    "question": q.get("question"),
                    "field_type": q.get("field_type") or a.get("field_type") or "text",
                    "answer": _fit(answer, q),
                    "confidence": round(max(0.0, min(1.0, confidence)), 2),
                    "needs_user_review": bool(a.get("needs_user_review")) or confidence < 0.7,
                    "source": "llm",
                }
        except LLMError as exc:
            logger.warning("LLM question answering failed: %s", exc)

    for idx, q in pending:
        if idx not in answers:
            answers[idx] = fallback_answer(q, ctx)
    result = []
    for idx, q in enumerate(questions):
        ans = answers[idx]
        if ans.get("answer"):
            ans["answer"] = _fit(str(ans["answer"]), q)
        if q.get("field_id"):
            ans["field_id"] = q["field_id"]
        if q.get("options"):
            ans["options"] = q["options"]
        ans["required"] = bool(q.get("required"))
        result.append(ans)
    return result


def mappings_dict(field_mappings: list[Any]) -> dict[str, str]:
    return {m.field_name: m.field_value for m in field_mappings}
