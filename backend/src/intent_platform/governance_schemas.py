from datetime import datetime
from typing import Literal
from pydantic import Field, StrictBool, StrictInt, model_validator
from .schemas import StrictModel

Role = Literal["admin", "maintainer", "publisher", "user"]


class MemberBody(StrictModel):
    revision: int = Field(ge=0)
    roles: list[Role] = Field(max_length=4)
    active: StrictBool

    @model_validator(mode="after")
    def distinct(self):
        if len(self.roles) != len(set(self.roles)): raise ValueError("Duplicate roles")
        if self.active and not self.roles: raise ValueError("Active members require a role")
        return self


class MemberOut(MemberBody):
    user_id: str
    ceiling_roles: list[Role]
    server_granted: bool


class Limits(StrictModel):
    max_queued_runs: StrictInt = Field(default=100, ge=1, le=10000)
    max_routing: StrictInt = Field(default=4, ge=1, le=256)
    max_execution: StrictInt = Field(default=8, ge=1, le=256)


class LimitsBody(StrictModel):
    revision: int = Field(ge=0)
    limits: Limits


class LimitsOut(LimitsBody):
    ceiling: Limits
    usage: dict[str, int]


class IntegrationOut(StrictModel):
    ref: str
    label: str
    version: str
    channels: list[str]
    fixture_mode: bool


class ResolverOut(StrictModel):
    ref: str
    version: str
    object_types: list[str]


class CredentialOut(StrictModel):
    ref: str
    connector_kind: str


class CatalogOut(StrictModel):
    integrations: list[IntegrationOut]
    resolvers: list[ResolverOut]
    credentials: list[CredentialOut]


class RevokeIntegrationBody(StrictModel):
    integration_ref: str = Field(min_length=1, max_length=160)


class AttemptOut(StrictModel):
    id: str
    run_id: str
    revision: int
    kind: Literal["routing", "execution", "reconciliation"]
    call_id: str | None
    status: str
    queued_at: datetime | None
    started_at: datetime
    finished_at: datetime | None
    lease_until: datetime
    queue_ms: float | None = None
    context_ms: float | None = None
    model_ms: float | None = None
    validation_ms: float | None = None
    connector_ms: float | None = None
    total_ms: float | None = None
    error_stage: str | None
