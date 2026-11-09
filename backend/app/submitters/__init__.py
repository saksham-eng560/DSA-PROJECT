"""Submitter registry."""

from __future__ import annotations

from app.models.enums import ATSPlatform
from app.submitters.base import BaseSubmitter, CandidatePacket, SessionExpired, SubmissionError, SubmissionResult
from app.submitters.generic_submit import GenericSubmitter
from app.submitters.greenhouse_submit import GreenhouseSubmitter
from app.submitters.internshala_apply import InternshalaSubmitter
from app.submitters.lever_submit import LeverSubmitter
from app.submitters.linkedin_easy_apply import LinkedInEasyApplySubmitter
from app.submitters.workday_submit import WorkdaySubmitter

SUBMITTERS: dict[ATSPlatform, type[BaseSubmitter]] = {
    ATSPlatform.GREENHOUSE: GreenhouseSubmitter,
    ATSPlatform.LEVER: LeverSubmitter,
    ATSPlatform.WORKDAY: WorkdaySubmitter,
    ATSPlatform.LINKEDIN: LinkedInEasyApplySubmitter,
}


def get_submitter(platform: ATSPlatform) -> BaseSubmitter:
    return SUBMITTERS.get(platform, GenericSubmitter)()


__all__ = [
    "BaseSubmitter",
    "CandidatePacket",
    "InternshalaSubmitter",
    "SessionExpired",
    "SubmissionError",
    "SubmissionResult",
    "get_submitter",
]
