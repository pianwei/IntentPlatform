from datetime import datetime
from typing import Literal
from pydantic import Field, field_validator
from .schemas import StrictModel


class Preferences(StrictModel):
    density: Literal["comfortable", "compact"] = "comfortable"
    text_size: Literal["standard", "large", "extra_large"] = "standard"


class PasswordLogin(StrictModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256, repr=False)


class ProfilePatch(StrictModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    preferences: Preferences | None = None


class PasswordChange(StrictModel):
    current_password: str = Field(min_length=1, max_length=256, repr=False)
    new_password: str = Field(min_length=12, max_length=256, repr=False)


class PasswordSetup(StrictModel):
    setup_token: str = Field(min_length=1, max_length=256, repr=False)
    new_password: str = Field(min_length=12, max_length=256, repr=False)


class AccountCreate(StrictModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.@-]*$")
    display_name: str = Field(min_length=1, max_length=160)
    user_id: str | None = Field(default=None, min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.@-]*$")

    @field_validator("display_name")
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip(): raise ValueError("Display name is required")
        return value.strip()


class AccountPatch(ProfilePatch):
    revision: int = Field(ge=1)
    active: bool | None = None


class AccountOut(StrictModel):
    user_id: str
    username: str
    display_name: str
    active: bool
    must_change_password: bool
    revision: int
    preferences: Preferences
    is_platform_admin: bool
    created_at: datetime


class AccountSetupOut(StrictModel):
    account: AccountOut
    setup_token: str


class KeyCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    expires_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip(): raise ValueError("Key name is required")
        return value.strip()


class KeyOut(StrictModel):
    id: str
    user_id: str
    name: str
    revoked: bool
    version: int
    expires_at: datetime | None
    created_at: datetime


class KeySecretOut(StrictModel):
    key: KeyOut
    secret: str


class AnonymousSessionBody(StrictModel):
    runtime_session_id: str | None = None
    recovery_secret: str | None = Field(default=None, max_length=256, repr=False)
    demo_persona: str | None = None


class KeySessionRevoke(StrictModel):
    visitor_id: str = Field(min_length=16, max_length=256)
