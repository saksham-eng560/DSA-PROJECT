"""Greenhouse Job Board API (public): https://developers.greenhouse.io/job-board.html"""

from __future__ import annotations

import html
import logging
from typing import Any

from app.models.enums import ATSPlatform
from app.scrapers.ats_detect import parse_ats_url
from app.scrapers.base import BaseScraper, ScrapedJob, SearchQuery, parse_date

logger = logging.getLogger(__name__)

API = "https://boards-api.greenhouse.io/v1/boards"


class GreenhouseScraper(BaseScraper):
    platform = ATSPlatform.GREENHOUSE
    rate_key = "greenhouse"

    _names: dict[str, str] = {}  # board token -> company name, shared across scans

    def board_name(self, token: str) -> str:
        if token in self._names:
            return self._names[token]
        try:
            name = self.get_json(f"{API}/{token}").get("name") or token.title()
        except Exception:  # noqa: BLE001
            return token.replace("-", " ").title()
        self._names[token] = name
        return name

    def _to_job(self, token: str, company: str, item: dict[str, Any]) -> ScrapedJob:
        location = (item.get("location") or {}).get("name")
        url = item.get("absolute_url") or f"https://job-boards.greenhouse.io/{token}/jobs/{item['id']}"
        metadata = {m.get("name"): m.get("value") for m in item.get("metadata") or [] if isinstance(m, dict)}
        return ScrapedJob(
            company_name=item.get("company_name") or company,
            role_title=item.get("title") or "",
            description=html.unescape(item.get("content") or ""),
            source_url=url,
            application_url=url,
            source_platform=ATSPlatform.GREENHOUSE,
            location=location,
            posted_date=parse_date(item.get("first_published") or item.get("updated_at")),
            external_id=str(item.get("id")),
            raw={"board_token": token, "departments": [d.get("name") for d in item.get("departments") or []],
                 "metadata": metadata, "questions": item.get("questions")},
        ).finalize()

    def list_board(self, token: str) -> list[ScrapedJob]:
        data = self.get_json(f"{API}/{token}/jobs", params={"content": "true"})
        company = self.board_name(token)
        return [self._to_job(token, company, item) for item in data.get("jobs", [])]

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        jobs = self.map_sources(query.sources.get("greenhouse_boards") or [],
                                lambda token: self.filter(self.list_board(token), query), query, "Greenhouse board")
        return jobs[: query.limit]

    def fetch_job(self, url: str) -> ScrapedJob | None:
        ref = parse_ats_url(url)
        if not ref.board or not ref.job_id:
            return None
        item = self.get_json(f"{API}/{ref.board}/jobs/{ref.job_id}", params={"questions": "true"})
        return self._to_job(ref.board, self.board_name(ref.board), item)

    def application_questions(self, board: str, job_id: str) -> list[dict[str, Any]]:
        """Return the form questions for a posting (labels, required flags, field types, options)."""
        item = self.get_json(f"{API}/{board}/jobs/{job_id}", params={"questions": "true"})
        out: list[dict[str, Any]] = []
        for q in item.get("questions") or []:
            for f in q.get("fields") or []:
                out.append(
                    {
                        "question": q.get("label"),
                        "required": bool(q.get("required")),
                        "field_id": f.get("name"),
                        "field_type": {"input_text": "text", "textarea": "text", "input_file": "file",
                                       "multi_value_single_select": "select", "multi_value_multi_select": "checkbox"}.get(
                            f.get("type"), "text"),
                        "options": [v.get("label") for v in f.get("values") or []],
                    }
                )
        return out
