"""Public wire schemas, shared through the generated OpenAPI document."""
from typing import Any, Literal
from datetime import datetime
from pydantic import Field
from .schemas import StrictModel, Definition, RuntimeUI, Channels, AccessPolicy


class LoginBody(StrictModel):
    user_id: Literal["dev-admin", "dev-reader", "dev-other"] = "dev-admin"


class IdentityBody(StrictModel):
    identity_token: str = Field(min_length=1)
    public_id: str | None = None
    runtime_session_id: str | None = None


class PublishBody(StrictModel):
    revision: int | None = None


class RollbackBody(StrictModel):
    release_id: str = Field(min_length=1)


class RevisionBody(StrictModel):
    revision: int = Field(ge=1)


class OriginBody(StrictModel):
    origin: str = Field(min_length=1)


class EvaluationCase(StrictModel):
    text: str = Field(min_length=1, max_length=16000)
    expected_capability_id: str = Field(min_length=1)


class EvaluationBody(StrictModel):
    agent_id: str
    cases: list[EvaluationCase] = Field(min_length=1, max_length=1000)
    label: str = "diagnostic"


class WorkspaceOut(StrictModel):
    id: str
    name: str
    roles: list[str]


class TokenOut(StrictModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user_id: str
    workspace_id: str | None = None
    agent_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    fixture_mode: bool
    runtime_session_id: str | None = None
    expires_at: datetime | None = None


class MeOut(StrictModel):
    user_id: str
    scope: Literal["management", "runtime"]
    workspaces: list[WorkspaceOut]
    fixture_mode: bool


class ValidationOut(StrictModel):
    valid: bool
    revision: int
    errors: list[str]
    checks: dict[str, Any]
    probe: dict[str, Any] | None


class FunctionOut(StrictModel):
    id: str
    workspace_id: str
    name: str
    description: str
    revision: int
    status: Literal["draft", "validated", "published", "disabled"]
    definition: Definition
    latest_version: int
    is_fixture: bool
    validation: ValidationOut | None
    access: AccessPolicy


class AgentOut(StrictModel):
    id: str
    workspace_id: str
    name: str
    capability_ids: list[str]
    current_release_id: str | None
    allowed_origins: list[str]
    suggested_questions: list[str]
    revision: int
    fixture_mode: bool
    public_id: str
    runtime_ui: RuntimeUI
    channels: Channels
    live_channels: Channels
    published_channels: Channels | None
    access: AccessPolicy
    published_origins: list[str]
    revoked_origins: list[str]
    integration_refs: list[str]
    published_integration_refs: list[str] | None
    revoked_integration_refs: list[str]
    planning_enabled: bool = False
    business_skill_ids: list[str] = Field(default_factory=list)
    model_profile_id: str | None = None


class RuntimeAgentSummaryOut(StrictModel):
    id: str
    public_id: str
    workspace_id: str
    name: str
    current_release_id: str
    capability_ids: list[str]
    suggested_questions: list[str]
    fixture_mode: bool


class CapabilityVersionOut(StrictModel):
    capability_id: str
    version: int


class ReleaseOut(StrictModel):
    agent_id: str
    release_id: str
    version: int
    capabilities: list[CapabilityVersionOut]
    fixture_mode: bool


class CapabilityOut(StrictModel):
    id: str
    name: str
    description: str


class RuntimeAgentOut(StrictModel):
    id: str
    name: str
    workspace_id: str
    release_id: str
    capabilities: list[CapabilityOut]
    suggested_questions: list[str]
    fixture_mode: bool


class ConversationOut(StrictModel):
    id: str
    conversation_id: str
    agent_id: str
    release_id: str
    status: str
    fixture_mode: bool
    title: str
    created_at: datetime
    updated_at: datetime
    runtime_ui: RuntimeUI


RunStatus = Literal["RECEIVED", "ROUTING", "WAITING_INPUT", "WAITING_CONFIRMATION", "EXECUTING", "SUCCEEDED", "FAILED", "UNKNOWN", "CANCELLED"]
Decision = Literal["READY", "NEEDS_ARGUMENTS", "AMBIGUOUS", "NEEDS_CONFIRMATION", "OUT_OF_SCOPE", "FORBIDDEN", "ERROR"]


class MessageOut(StrictModel):
    run_id: str
    conversation_id: str
    revision: int
    status: RunStatus
    duplicate: bool


class QuestionField(StrictModel):
    name: str
    type: str | list[str]
    required: bool
    label: str | None = None
    unit: str | None = None


class OptionOut(StrictModel):
    id: str
    label: str


class QuestionOut(StrictModel):
    id: str
    fields: list[QuestionField]
    options: list[OptionOut]
    request_index: int | None = None
    kind: Literal["arguments", "function_choice", "entity_mention", "entity_choice", "history_choice", "planner_arguments", "planner_choice", "planner_clarification"] = "arguments"
    binding_key: str | None = None


class CallOut(StrictModel):
    id: str
    status: Literal["PENDING", "EXECUTING", "RECONCILING", "SUCCEEDED", "FAILED", "UNKNOWN", "CANCELLED"]


class RuntimeStepSummary(StrictModel):
    name: str
    status: str
    input: dict[str, Any]
    output: Any | None
    elapsed_ms: float | None


class RuntimeTimingSummary(StrictModel):
    total_ms: float | None
    user_wait_ms: float | None
    processing_ms: float | None


class RuntimeAttemptSummary(StrictModel):
    stage: str
    status: str
    duration_ms: float | None


class RunOut(StrictModel):
    run_id: str
    conversation_id: str
    revision: int
    status: RunStatus
    decision: Decision | None
    question: QuestionOut | None
    calls: list[CallOut]
    fixture_mode: bool
    bindings: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[RuntimeStepSummary] = Field(default_factory=list)
    timing: RuntimeTimingSummary


class EventOut(StrictModel):
    schema_version: Literal["1"]
    conversation_id: str
    run_id: str
    revision: int
    sequence: int
    event_type: Literal["STATUS", "QUESTION", "CONFIRMATION", "RESULT", "ERROR"]
    payload: dict[str, Any]


class HandshakeOut(StrictModel):
    instance_nonce: str
    expires_at: datetime
    protocol_version: Literal["1"]


class TicketOut(StrictModel):
    ticket: str
    expires_at: datetime


class SeedOut(StrictModel):
    workspace_id: str
    functions: list[FunctionOut]
    agent: AgentOut
    fixture_mode: bool


class SessionBody(StrictModel):
    runtime_session_id: str | None = None


class PublicAppOut(StrictModel):
    public_id: str
    agent_id: str
    release_id: str
    runtime_ui: RuntimeUI
    channels: Channels
    fixture_mode: bool


class HistoryMessage(StrictModel):
    run_id: str
    text: str
    revision: int
    status: RunStatus
    created_at: datetime


class HistoryOut(StrictModel):
    conversation: ConversationOut
    messages: list[HistoryMessage]
    runs: list[RunOut]
    events: list[EventOut]
    cursor: int


class AssetOut(StrictModel):
    id: str
    media_type: str
    size: int
