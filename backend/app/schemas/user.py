from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    full_name: str = Field(min_length=1, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    location: str | None = Field(default=None, max_length=255)
    linkedin_url: str | None = None


class PasswordChange(BaseModel):
    current_password: str | None = None
    new_password: str = Field(min_length=8, max_length=256)


class PreferencesUpdate(BaseModel):
    preferences: dict[str, Any]


class FieldMappingIn(BaseModel):
    field_name: str = Field(min_length=1, max_length=255)
    field_value: str
    field_type: str | None = "text"


class FieldMappingsUpdate(BaseModel):
    mappings: list[FieldMappingIn]


class ATSCredentialsUpdate(BaseModel):
    credentials: dict[str, str]


class LinkedInCookieIn(BaseModel):
    li_at: str = Field(min_length=10, max_length=4000)
    profile_url: str | None = None


class InternshalaCookieIn(BaseModel):
    """One cookie as Chrome's ``chrome.cookies`` API reports it (field names kept as Chrome spells them)."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=256)
    value: str = Field(default="", max_length=4096)
    domain: str = Field(min_length=1, max_length=255)
    path: str = Field(default="/", max_length=1024)
    secure: bool = False
    http_only: bool = Field(default=False, alias="httpOnly")
    same_site: str | None = Field(default=None, alias="sameSite", max_length=32)
    expiration_date: float | None = Field(default=None, alias="expirationDate")
    host_only: bool | None = Field(default=None, alias="hostOnly")


class InternshalaSessionIn(BaseModel):
    cookies: list[InternshalaCookieIn] = Field(min_length=1, max_length=60)
    reason: str = Field(default="manual", max_length=20)  # manual | scheduled | cookie-changed (extension)
    user_agent: str | None = Field(default=None, max_length=512)  # the browser the login belongs to


class DeleteAccountRequest(BaseModel):
    confirm: str  # must equal "DELETE"
