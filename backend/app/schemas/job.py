from __future__ import annotations

from pydantic import BaseModel, Field


class JobImportRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    prepare: bool = False
