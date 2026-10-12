from datetime import datetime
from typing import Literal
from pydantic import Field, field_validator
from .schemas import StrictModel
from .governance_schemas import MemberOut


class MemberDisplay(MemberOut):
    username: str | None = None
    display_name: str
    account_active: bool


class MemberPage(StrictModel):
    items: list[MemberDisplay]
    total: int
    limit: int
    offset: int


class AdministratorDisplay(StrictModel):
    user_id: str
    username: str | None = None
    display_name: str


class WorkspaceMetadata(StrictModel):
    id: str
    name: str
    status: Literal["active", "archived"]
    revision: int
    archived_at: datetime | None
    roles: list[str]
    administrators: list[AdministratorDisplay]
    member_count: int
    can_manage: bool
    can_open: bool
    can_archive: bool
    can_rename: bool


class WorkspacePage(StrictModel):
    items: list[WorkspaceMetadata]
    total: int
    limit: int
    offset: int


class WorkspaceRevision(StrictModel):
    revision: int = Field(ge=1)


class WorkspaceRename(WorkspaceRevision):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def meaningful(cls, value):
        if value != value.strip() or not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Use a nonempty trimmed name without control characters")
        return value


class WorkspaceAdministrator(WorkspaceRevision):
    user_id: str = Field(min_length=1, max_length=160)


class PermissionExplanation(StrictModel):
    key: str
    label: str
    allowed: bool


class ResourceAccess(StrictModel):
    id: str
    name: str
    allowed: bool
    reason: str


class AccessWorkspace(StrictModel):
    id: str
    name: str
    status: Literal["active", "archived"]


class EffectiveAccess(StrictModel):
    member: MemberDisplay
    workspace: AccessWorkspace
    roles: list[str]
    permissions: list[PermissionExplanation]
    agents: list[ResourceAccess]
    functions: list[ResourceAccess]
