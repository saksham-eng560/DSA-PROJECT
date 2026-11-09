"""The company-check agent: is the company behind a posting real, and is the posting safe to apply to?

Every posting gets a verdict before anything is sent for you:

* ``verified``   a company the agent can stand behind: a renowned company from the catalog, a posting on
                 the company's own job board (Greenhouse, Lever, Ashby, Workday...), or one the AI recognises
                 *and* whose official website the posting itself points to. Only verified companies are
                 applied to automatically (the Internshala bot included).
* ``unverified`` nothing wrong found, nothing proven either: it waits for your click.
* ``suspicious`` the classic internship-scam signs (asks you to pay a fee or deposit, apply over WhatsApp /
                 Telegram, "earn per day", MLM...). Skipped, never applied to.

You always have the last word: "Mark legit" (``trusted_companies``) or "Not legit" (``companies_to_avoid``).
The rules are instant; the AI check is optional, cautious and cached per company.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from app.models.enums import ATSPlatform
from app.services.company_catalog import TIERS, Company, by_board, match_company, normalize_company, owns_url

logger = logging.getLogger(__name__)

VERIFIED, UNVERIFIED, SUSPICIOUS = "verified", "unverified", "suspicious"

# The company's own applicant-tracking system: companies pay for these and own the board.
OWN_BOARDS = {ATSPlatform.GREENHOUSE: "Greenhouse", ATSPlatform.LEVER: "Lever", ATSPlatform.ASHBY: "Ashby",
              ATSPlatform.WORKDAY: "Workday"}
OWN_BOARD_HOSTS = {"greenhouse.io": "Greenhouse", "lever.co": "Lever", "ashbyhq.com": "Ashby",
                   "myworkdayjobs.com": "Workday", "smartrecruiters.com": "SmartRecruiters",
                   "workable.com": "Workable", "recruitee.com": "Recruitee", "bamboohr.com": "BambooHR"}

# Scam signs common on internship boards. A match is ignored when negated ("no registration fee",
# "Internshala never asks you to pay").
_NEGATION = re.compile(r"\b(no|never|not|without|free of|zero|nil|don'?t|do not|won'?t|will not)\b[^.!?\n]{0,30}$", re.I)
HARD_FLAGS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(registration|security|joining|training|kit|processing|onboarding|certificate|certification|"
                r"interview|application|enrol?ment)\s+(fee|fees|deposit|charges?|amount)\b", re.I),
     "Asks you to pay a fee or deposit"),
    (re.compile(r"\b(security|refundable)\s+deposit\b", re.I), "Asks for a deposit"),
    (re.compile(r"\b(pay|deposit|invest|transfer)\s+(rs\.?|inr|₹|\$|usd)\s?\d", re.I), "Asks you to pay money"),
    (re.compile(r"\b(whats\s?app|telegram)\b[^.\n]{0,50}\b(apply|contact|message|join|send|ping|dm)\b|"
                r"\b(apply|contact|message|join|send|ping|dm)\b[^.\n]{0,50}\b(whats\s?app|telegram)\b", re.I),
     "Asks you to apply or talk over WhatsApp / Telegram"),
    (re.compile(r"\b(earn|income|make)\b[^.\n]{0,30}\b(per|a|every|each)\s+(day|hour)\b|\bearn up to\b|"
                r"\bunlimited (earning|income)s?\b", re.I), "Promises earnings per day / unlimited income"),
    (re.compile(r"\b(mlm|multi[- ]level marketing|network marketing|chain marketing|pyramid scheme)\b", re.I),
     "Network / MLM marketing"),
    (re.compile(r"\b(no interview|without (any )?interview|100% (job|placement) guarantee|guaranteed (job|placement))\b",
                re.I), "Guarantees a job without an interview"),
)
SOFT_FLAGS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[\w.+-]+@(gmail|yahoo|outlook|hotmail|rediffmail|ymail)\.com\b", re.I),
     "Gives a personal e-mail address (gmail / yahoo...) to apply"),
    (re.compile(r"\b(data entry|typing (work|job)|copy[- ]paste (work|job)|form filling (work|job))\b", re.I),
     "Data-entry / typing work"),
    (re.compile(r"\b(commission[- ]based|commission only|only incentives?|incentives? only|incentive[- ]based)\b", re.I),
     "Pay is commission / incentives only"),
    (re.compile(r"\burgent(ly)? (hiring|requirement)\b", re.I), "“Urgent hiring”"),
    (re.compile(r"\bunpaid\b", re.I), "Unpaid"),
)
_GENERIC_NAME = re.compile(r"^[a-z]+( [a-z]+)? (enterprises?|consultancy|consultants|solutions|services|ventures|global|"
                           r"group|associates|infotech|international)$")


@dataclass
class CompanyCheck:
    verdict: str
    score: int
    reasons: list[str] = field(default_factory=list)
    tier: str | None = None
    method: str = "rules"
    catalog_name: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "score": self.score, "reasons": self.reasons, "tier": self.tier,
                "method": self.method, "catalog_name": self.catalog_name,
                "checked_at": datetime.now(UTC).isoformat()}


def _flags(text: str, rules: tuple[tuple[re.Pattern[str], str], ...]) -> list[str]:
    found: list[str] = []
    for pattern, label in rules:
        for match in pattern.finditer(text):
            if _NEGATION.search(text[max(0, match.start() - 40):match.start()]):
                continue  # "there is no registration fee"
            if label not in found:
                found.append(label)
            break
    return found


def _own_board(platform: Any, urls: list[str]) -> str | None:
    if platform in OWN_BOARDS:
        return OWN_BOARDS[platform]
    for url in urls:
        host = (urlparse(url or "").hostname or "").lower()
        for suffix, label in OWN_BOARD_HOSTS.items():
            if host == suffix or host.endswith("." + suffix):
                return label
    return None


def check_company(company_name: str, description: str, *, platform: Any = None, urls: list[str] | None = None,
                  raw: dict[str, Any] | None = None, domain: str | None = None,
                  trusted: list[str] | None = None) -> CompanyCheck:
    """The rules: instant, no network. ``trusted`` = companies you marked legit."""
    urls = [u for u in (urls or []) if u]
    raw = raw or {}
    text = f"{description or ''}\n{raw.get('stipend') or ''}"
    hard = _flags(text, HARD_FLAGS)
    soft = _flags(text, SOFT_FLAGS)
    name = normalize_company(company_name)
    catalog = match_company(company_name)
    board_token = raw.get("board_token") or raw.get("board")
    if catalog is None and board_token:  # a posting on a catalog company's own board
        for kind in ("greenhouse", "lever", "ashby", "workday"):
            catalog = catalog or by_board(kind, str(board_token))
    own_board = _own_board(platform, urls)
    reasons: list[str] = []
    score = 50
    if catalog is not None:
        score += 35
        reasons.append(f"{catalog.name}: a renowned company ({catalog.tier_label})")
        if any(owns_url(catalog, u) for u in [*urls, f"https://{domain}" if domain else ""]):
            score += 10
            reasons.append(f"The posting links to {catalog.domain}, the company's own site")
    if own_board:
        score += 30
        reasons.append(f"Posted on the company's own {own_board} job board")
    if raw.get("listing_source") == "internshala" and catalog is None:
        score -= 5  # anyone can post on Internshala
    if catalog is None and _GENERIC_NAME.match(name):
        soft.append("Generic company name")
    if len((description or "").strip()) < 160 and not own_board and catalog is None:
        soft.append("Very little information about the role or company")
    score -= 12 * len(soft) + 60 * len(hard)
    reasons += [f"⚠ {f}" for f in hard + soft]
    score = max(0, min(100, score))
    tier = catalog.tier if catalog else None
    you_trust = bool(name) and name in {normalize_company(t) for t in trusted or [] if t}
    if you_trust:
        return CompanyCheck(VERIFIED, max(score, 90), ["You marked this company legit", *reasons], tier, "you",
                            catalog.name if catalog else None)
    if hard or len(soft) >= 3:  # one scam sign, or three warning signs together
        verdict = SUSPICIOUS
    elif (catalog is not None or own_board) and score >= 70:
        verdict = VERIFIED
    else:
        verdict = UNVERIFIED
        if not reasons:
            reasons.append("Not a company the agent knows, and not posted on the company's own job board")
    return CompanyCheck(verdict, score, reasons, tier, "rules", catalog.name if catalog else None)


def check_job(job: Any, trusted: list[str] | None = None) -> CompanyCheck:
    """The rules for a saved ``Job`` or a freshly scraped ``ScrapedJob``."""
    raw = getattr(job, "raw_data", None) or getattr(job, "raw", None) or {}
    return check_company(job.company_name, job.description or "", platform=job.source_platform,
                         urls=[job.application_url, job.source_url], raw=raw,
                         domain=getattr(job, "company_domain", None), trusted=trusted)


# ------------------------------------------------------------------ the AI check (optional, cautious)
LLM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "recognized": {"type": "boolean"},
        "official_domain": {"type": "string"},
        "legit": {"type": "string", "enum": ["yes", "no", "unsure"]},
        "concerns": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["recognized", "official_domain", "legit", "concerns"],
}


def _prompt(company: str, role: str, description: str) -> str:
    return (
        "COMPANY LEGITIMACY CHECK for an internship applicant (protect them from fake companies and scams).\n"
        f"Company name as posted: {company}\nRole: {role}\nPosting text:\n{description[:2500]}\n\n"
        "Answer only from what you reliably know. recognized = true only if you know this exact company as an "
        "established, real business. official_domain = its website domain (e.g. example.com) or \"\" if you are "
        "not sure. legit = \"no\" if the posting shows scam signs (fees, deposits, WhatsApp-only contact, "
        "earn-per-day promises, MLM); \"unsure\" when you don't know the company. List concerns briefly."
    )


def verify_with_llm(job: Any, check: CompanyCheck) -> CompanyCheck:
    """Ask the AI about a company the rules couldn't verify. It can only *verify* when it recognises the
    company AND names a website domain that the posting itself points to (so a model can't vouch for a
    company it has merely heard a similar name of); it can flag scam signs the rules missed."""
    from app.services.llm import get_llm

    llm = get_llm()
    if not llm.available or check.verdict != UNVERIFIED:
        return check
    try:
        data = llm.complete_json(_prompt(job.company_name, job.role_title, job.description or ""),
                                 schema=LLM_SCHEMA, effort="low", task="company_check")
    except Exception as exc:  # noqa: BLE001 - the AI is optional; the rules' verdict stands
        logger.info("Company check by AI failed for %s: %s", job.company_name, exc)
        return check
    domain = str(data.get("official_domain") or "").lower().strip().removeprefix("www.").strip("/")
    concerns = [str(c) for c in data.get("concerns") or []][:3]
    haystack = " ".join(filter(None, [job.application_url, job.source_url, getattr(job, "company_domain", None),
                                      job.description or ""])).lower()
    if data.get("legit") == "no" and concerns:
        return CompanyCheck(SUSPICIOUS, min(check.score, 20), [*check.reasons, *(f"⚠ AI: {c}" for c in concerns)],
                            check.tier, "ai", check.catalog_name)
    if data.get("recognized") and data.get("legit") == "yes" and re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", domain) \
            and domain in haystack:
        return CompanyCheck(VERIFIED, max(check.score, 75),
                            [*check.reasons, f"The AI recognises {job.company_name} and the posting points to {domain}"],
                            check.tier, "ai", check.catalog_name)
    note = "The AI doesn't know this company well enough to vouch for it"
    return CompanyCheck(UNVERIFIED, check.score, [*check.reasons, note], check.tier, "ai", check.catalog_name)


def is_trusted(job: Any, prefs: dict[str, Any]) -> bool:
    """May the agent apply to this job automatically? Verified companies only (or ones you marked legit)."""
    trusted = {normalize_company(t) for t in prefs.get("trusted_companies") or [] if t}
    if normalize_company(job.company_name) in trusted:
        return True
    return getattr(job, "company_verdict", None) == VERIFIED


def summary(job: Any, prefs: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the dashboard shows: verdict, tier, reasons (your own marking wins)."""
    check = dict(getattr(job, "company_check", None) or {})
    verdict = getattr(job, "company_verdict", None) or check.get("verdict")
    if prefs is not None and normalize_company(job.company_name) in {
            normalize_company(t) for t in prefs.get("trusted_companies") or [] if t}:
        verdict = VERIFIED
        check["reasons"] = ["You marked this company legit", *(check.get("reasons") or [])]
    company: Company | None = match_company(job.company_name)
    tier = getattr(job, "company_tier", None) or (company.tier if company else None)
    return {"verdict": verdict, "score": check.get("score"), "reasons": (check.get("reasons") or [])[:6],
            "method": check.get("method"), "tier": tier, "tier_label": TIERS.get(tier or "")}
