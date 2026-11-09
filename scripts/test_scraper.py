#!/usr/bin/env python3
"""Run one scraper from the command line (debugging / canary checks for DOM changes).

Examples:
    python scripts/test_scraper.py greenhouse --source stripe --keywords "Software Engineer"
    python scripts/test_scraper.py lever --source netflix
    python scripts/test_scraper.py workday --source https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite -k Engineer
    python scripts/test_scraper.py linkedin -k "Backend Engineer" -l "San Francisco"
    python scripts/test_scraper.py generic --source https://company.com/careers
    python scripts/test_scraper.py internshala -k "Software Engineer" -l "Delhi"
    python scripts/test_scraper.py url https://job-boards.greenhouse.io/stripe/jobs/123
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
# Repo checkout: <root>/backend/app ; Docker image: /app/app
sys.path.insert(0, str(_ROOT / "backend" if (_ROOT / "backend" / "app").is_dir() else _ROOT))

from app.scrapers import SCRAPERS, SearchQuery, fetch_job_from_url

SOURCE_KEYS = {
    "greenhouse": "greenhouse_boards",
    "lever": "lever_companies",
    "ashby": "ashby_boards",
    "workday": "workday_sites",
    "generic": "career_pages",
    "internshala": "internshala_urls",
    "internships": "internship_lists",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("platform", choices=[*SCRAPERS, "url"])
    parser.add_argument("target", nargs="?", help="URL when platform=url")
    parser.add_argument("--source", "-s", action="append", default=[], help="board token / company slug / site URL")
    parser.add_argument("--keywords", "-k", action="append", default=[])
    parser.add_argument("--location", "-l", action="append", default=[])
    parser.add_argument("--remote", action="store_true")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="print full JSON")
    args = parser.parse_args()

    if args.platform == "url":
        if not args.target:
            parser.error("url requires a target URL")
        job = fetch_job_from_url(args.target)
        jobs = [job] if job else []
    else:
        sources = {SOURCE_KEYS[args.platform]: args.source} if args.platform in SOURCE_KEYS else {}
        query = SearchQuery(keywords=args.keywords, locations=args.location, remote=args.remote,
                            posted_within_days=args.days, limit=args.limit, sources=sources)
        jobs = SCRAPERS[args.platform]().search(query)

    if args.json:
        print(json.dumps([j.to_dict() for j in jobs], indent=2, default=str))
    else:
        for j in jobs:
            salary = f" | {j.salary_min}-{j.salary_max} {j.salary_currency}" if j.salary_min else ""
            print(f"- {j.role_title} @ {j.company_name} | {j.location or '—'}{' (remote)' if j.is_remote else ''}"
                  f" | {j.job_type.value if j.job_type else '?'}{salary}\n  {j.source_url}")
    print(f"\n{len(jobs)} job(s) found", file=sys.stderr)
    return 0 if jobs else 1


if __name__ == "__main__":
    raise SystemExit(main())
