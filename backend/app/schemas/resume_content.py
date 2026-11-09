"""Canonical structure of parsed / tailored resume content (PLAN.md §4 resumes.parsed_content)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _clean_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [v for v in value.replace("\n", ",").split(",")]
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_str(item)
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PersonalInfo(_Base):
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""

    _v = field_validator("*", mode="before")(lambda cls, v: _clean_str(v))


class Education(_Base):
    institution: str = ""
    degree: str = ""
    field: str = ""
    gpa: str = ""
    start_date: str = ""
    end_date: str = ""
    highlights: list[str] = Field(default_factory=list)

    @field_validator("institution", "degree", "field", "gpa", "start_date", "end_date", mode="before")
    @classmethod
    def _s(cls, v: Any) -> str:
        return _clean_str(v)

    @field_validator("highlights", mode="before")
    @classmethod
    def _l(cls, v: Any) -> list[str]:
        return _clean_list(v)


class Experience(_Base):
    company: str = ""
    title: str = ""
    start_date: str = ""
    end_date: str = ""
    location: str = ""
    bullets: list[str] = Field(default_factory=list)

    @field_validator("company", "title", "start_date", "end_date", "location", mode="before")
    @classmethod
    def _s(cls, v: Any) -> str:
        return _clean_str(v)

    @field_validator("bullets", mode="before")
    @classmethod
    def _bullets(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            v = v.split("\n")
        return [_clean_str(b).lstrip("•-*· ").strip() for b in v if _clean_str(b)]


class Project(_Base):
    name: str = ""
    description: str = ""
    technologies: list[str] = Field(default_factory=list)
    url: str = ""

    @field_validator("name", "description", "url", mode="before")
    @classmethod
    def _s(cls, v: Any) -> str:
        return _clean_str(v)

    @field_validator("technologies", mode="before")
    @classmethod
    def _l(cls, v: Any) -> list[str]:
        return _clean_list(v)


class Skills(_Base):
    technical: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)

    _v = field_validator("*", mode="before")(lambda cls, v: _clean_list(v))

    def all(self) -> list[str]:
        return [*self.technical, *self.tools, *self.languages, *self.soft_skills]


class Certification(_Base):
    name: str = ""
    issuer: str = ""
    date: str = ""

    _v = field_validator("*", mode="before")(lambda cls, v: _clean_str(v))


class ResumeContent(_Base):
    personal_info: PersonalInfo = Field(default_factory=PersonalInfo)
    summary: str = ""
    education: list[Education] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    skills: Skills = Field(default_factory=Skills)
    certifications: list[Certification] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)

    @field_validator("summary", mode="before")
    @classmethod
    def _s(cls, v: Any) -> str:
        return _clean_str(v)

    @field_validator("awards", mode="before")
    @classmethod
    def _awards(cls, v: Any) -> list[str]:
        if isinstance(v, list):
            v = [a.get("name") or a.get("title") or "" if isinstance(a, dict) else a for a in v]
        return _clean_list(v)

    @field_validator("certifications", mode="before")
    @classmethod
    def _certs(cls, v: Any) -> list[Any]:
        if not v:
            return []
        return [{"name": c} if isinstance(c, str) else c for c in v]

    @field_validator("skills", mode="before")
    @classmethod
    def _skills(cls, v: Any) -> Any:
        if isinstance(v, list):
            return {"technical": v}
        return v or {}

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    def full_text(self) -> str:
        """Flatten every fact in the resume to text (used for truthfulness checks & embeddings)."""
        parts: list[str] = [self.summary]
        for exp in self.experience:
            parts += [exp.company, exp.title, exp.location, *exp.bullets]
        for edu in self.education:
            parts += [edu.institution, edu.degree, edu.field, *edu.highlights]
        for proj in self.projects:
            parts += [proj.name, proj.description, *proj.technologies]
        parts += self.skills.all()
        parts += [c.name for c in self.certifications]
        parts += self.awards
        return "\n".join(p for p in parts if p)

    def skills_text(self) -> str:
        techs = [t for p in self.projects for t in p.technologies]
        return ", ".join([*self.skills.all(), *techs])


def normalize_resume(data: dict[str, Any] | None) -> dict[str, Any]:
    return ResumeContent.model_validate(data or {}).to_dict()
