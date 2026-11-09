from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ResumeUpdate(BaseModel):
    parsed_content: dict[str, Any] | None = None
    label: str | None = Field(default=None, max_length=255)


class ResumeCreateFromText(BaseModel):
    text: str = Field(min_length=50)
    label: str | None = None
    is_master: bool = True
