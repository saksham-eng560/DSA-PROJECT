"""Detect which ATS / job board a URL belongs to and extract its identifiers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from app.models.enums import ATSPlatform

_PATTERNS: list[tuple[ATSPlatform, re.Pattern[str]]] = [
    (ATSPlatform.GREENHOUSE, re.compile(r"(^|\.)greenhouse\.io$")),
    (ATSPlatform.LEVER, re.compile(r"(^|\.)lever\.co$")),
    (ATSPlatform.ASHBY, re.compile(r"(^|\.)ashbyhq\.com$")),
    (ATSPlatform.WORKDAY, re.compile(r"(^|\.)(myworkdayjobs|myworkdaysite|workday)\.com$")),
    (ATSPlatform.LINKEDIN, re.compile(r"(^|\.)linkedin\.com$")),
    (ATSPlatform.INDEED, re.compile(r"(^|\.)indeed\.(com|co\.[a-z]{2}|[a-z]{2})$")),
    (ATSPlatform.GLASSDOOR, re.compile(r"(^|\.)glassdoor\.(com|co\.[a-z]{2}|[a-z]{2})$")),
    (ATSPlatform.WELLFOUND, re.compile(r"(^|\.)(wellfound|angel)\.(com|co)$")),
    (ATSPlatform.BAMBOOHR, re.compile(r"(^|\.)bamboohr\.com$")),
    (ATSPlatform.ICIMS, re.compile(r"(^|\.)icims\.com$")),
    (ATSPlatform.TALEO, re.compile(r"(^|\.)taleo\.net$")),
    (ATSPlatform.SMARTRECRUITERS, re.compile(r"(^|\.)smartrecruiters\.com$")),
    (ATSPlatform.JOBVITE, re.compile(r"(^|\.)jobvite\.com$")),
]


def detect_ats_platform(url: str | None) -> ATSPlatform:
    if not url:
        return ATSPlatform.UNKNOWN
    host = (urlparse(url).hostname or "").lower()
    for platform, pattern in _PATTERNS:
        if pattern.search(host):
            return platform
    if "gh_jid=" in url:
        return ATSPlatform.GREENHOUSE
    return ATSPlatform.CUSTOM if host else ATSPlatform.UNKNOWN


@dataclass
class ATSRef:
    platform: ATSPlatform
    board: str | None = None  # greenhouse board token / lever company / ashby board / workday tenant
    job_id: str | None = None
    site: str | None = None  # workday site
    host: str | None = None


def parse_ats_url(url: str) -> ATSRef:
    platform = detect_ats_platform(url)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    parts = [p for p in parsed.path.split("/") if p]
    ref = ATSRef(platform=platform, host=host)
    if platform == ATSPlatform.GREENHOUSE:
        # boards.greenhouse.io/{token}/jobs/{id} | job-boards.greenhouse.io/{token}/jobs/{id}
        # boards.greenhouse.io/embed/job_app?for={token}&token={id}
        qs = parse_qs(parsed.query)
        if parts and parts[0] == "embed":
            ref.board = (qs.get("for") or [None])[0]
            ref.job_id = (qs.get("token") or [None])[0]
        elif parts:
            ref.board = parts[0]
            if len(parts) >= 3 and parts[1] == "jobs":
                ref.job_id = re.sub(r"\D", "", parts[2]) or None
        if not ref.job_id and qs.get("gh_jid"):
            ref.job_id = qs["gh_jid"][0]
    elif platform == ATSPlatform.LEVER:
        # jobs.lever.co/{company}/{uuid}[/apply]
        if parts:
            ref.board = parts[0]
        if len(parts) >= 2:
            ref.job_id = parts[1]
    elif platform == ATSPlatform.ASHBY:
        # jobs.ashbyhq.com/{board}/{uuid}[/application]
        if parts:
            ref.board = parts[0]
        if len(parts) >= 2:
            ref.job_id = parts[1]
    elif platform == ATSPlatform.WORKDAY:
        # {tenant}.wd5.myworkdayjobs.com/[en-US/]{site}/job/{location}/{title}_{req}
        ref.board = host.split(".")[0]
        clean = [p for p in parts if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
        if clean:
            ref.site = clean[0]
        if "job" in clean:
            ref.job_id = "/" + "/".join(clean[clean.index("job"):])
    elif platform == ATSPlatform.LINKEDIN:
        m = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)", parsed.path) or re.search(r"currentJobId=(\d+)", parsed.query)
        if m:
            ref.job_id = m.group(1)
    return ref
