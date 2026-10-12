from typing import Any, Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, model_validator, StrictBool
from .origins import exact_origin


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Connector(StrictModel):
    kind: Literal["fixture", "http", "dify", "hiagent"] = "fixture"
    endpoint: str | None = None
    method: Literal["GET", "POST"] = "GET"
    credential_ref: str | None = None
    timeout_seconds: float = Field(default=10, gt=0, le=120)
    idempotency_header: str | None = None
    status_endpoint: str | None = None
    reconcile_by: Literal["operation_id", "idempotency_key"] = "operation_id"
    version: str = "1"
    immutable_remote: bool = False
    workflow_id: str | None = None
    # HiAgent deployments differ: require explicit operator-reviewed wire mapping.
    request_mapping: dict[str, str] = Field(default_factory=dict)
    result_path: str = ""
    status_path: str = ""
    success_values: list[str] = Field(default_factory=lambda: ["succeeded"])
    failure_values: list[str] = Field(default_factory=lambda: ["failed", "stopped", "partial-succeeded"])
    pending_values: list[str] = Field(default_factory=lambda: ["running", "pending", "paused"])
    operation_id_path: str = ""
    authorization_header: str | None = None
    authorization_prefix: str = ""
    http_mapping: dict | None = None
    http_body_required: bool = False
    http_failure_statuses: list[Literal[400, 404, 409, 422]] = Field(default_factory=list)


class Condition(StrictModel):
    field: str
    operator: Literal["eq", "ne", "in", "exists"]
    value: Any = None


class Presentation(StrictModel):
    component: Literal["detail", "table", "text", "markdown", "citation", "file"] = "detail"
    title: str = "结果"
    field_map: dict[str, str] = Field(default_factory=dict)
    files_field: str | None = None


class Definition(StrictModel):
    authoring_version: Literal["legacy", "function-v2"] = "legacy"
    output_examples: list[dict] = Field(default_factory=list, max_length=5)
    execution_kind: Literal["direct", "composite"] = "direct"
    composite: dict | None = None
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=12000)
    object_type: str = "local:Object"
    action: str = "READ"
    result_type: str = "local:Detail"
    aliases: list[str] = Field(default_factory=list)
    input_schema: dict = Field(default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": False})
    output_schema: dict | None = None
    output_mode: Literal["fields", "none"] = "fields"
    connector: Connector = Field(default_factory=Connector)
    effect: Literal["READ_ONLY", "WRITE"] = "READ_ONLY"
    requires_confirmation: bool = False
    allowed_users: list[str] = Field(default_factory=list)
    preconditions: list[Condition] = Field(default_factory=list)
    presentation: Presentation = Field(default_factory=Presentation)
    positive_examples: list[dict] = Field(default_factory=list)
    negative_examples: list[dict] = Field(default_factory=list)
    document_refs: list[dict] = Field(default_factory=list)
    participant_roles: list[dict] = Field(default_factory=list)
    relations: list[dict] = Field(default_factory=list)
    success_condition: Condition | None = None
    compiler_version: Literal["finite-json-v1", "finite-json-v2"] = "finite-json-v1"
    domain_refs: list[dict] = Field(default_factory=list)
    argument_bindings: dict[str, dict] = Field(default_factory=dict)
    parameter_semantics: dict[str, dict] = Field(default_factory=dict)
    trusted_preconditions: dict | None = None
    result_bindings: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def write_confirmation(self):
        if self.execution_kind == "composite":
            from .agent_projection import CompositeDefinition
            if self.composite is None:
                raise ValueError("Composite function requires a pinned rule and dependencies")
            self.composite = CompositeDefinition.model_validate(self.composite).model_dump(by_alias=True)
        elif self.composite is not None:
            raise ValueError("Direct function cannot contain a composite rule")
        if self.effect == "WRITE":
            self.requires_confirmation = True
        if self.output_mode == "none":
            from .output_contract import check_output_contract
            check_output_contract(self.model_dump())
        return self


class ImportBody(StrictModel):
    definition: Definition
    document_text: str = ""


class DraftBody(StrictModel):
    revision: int
    definition: Definition


class ValidateBody(StrictModel):
    revision: int | None = Field(default=None, ge=1)
    arguments: dict = Field(default_factory=dict)
    probe: bool = False
    allow_effects: bool = False
    failure_arguments: dict | None = None
    model_profile_id: str | None = None
    test_goal: str | None = Field(default=None, max_length=2000)


class AgentBody(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    capability_ids: list[str] = Field(min_length=1)
    allowed_origins: list[str] = Field(default_factory=list)
    suggested_questions: list[str] = Field(default_factory=list)
    revision: int | None = None
    allowed_users: list[str] | None = None  # creation compatibility only; never a draft authorization update
    runtime_ui: "RuntimeUI | None" = None
    channels: "Channels | None" = None
    integration_refs: list[str] | None = Field(default=None, max_length=32)
    planning_enabled: bool = False
    business_skill_ids: list[str] = Field(default_factory=list, max_length=32)
    model_profile_id: str | None = None
    routing_strategy: Literal["legacy", "three-layer-v1"] = "legacy"

    @model_validator(mode="after")
    def origins(self):
        self.name = self.name.strip()
        if not self.name: raise ValueError("Agent name is required")
        if len(set(self.capability_ids)) != len(self.capability_ids):
            raise ValueError("Duplicate capability IDs")
        if len(set(self.business_skill_ids)) != len(self.business_skill_ids):
            raise ValueError("Duplicate business Skill IDs")
        if self.integration_refs is not None and (len(self.integration_refs) != len(set(self.integration_refs)) or any(not ref.strip() for ref in self.integration_refs)):
            raise ValueError("Integration references must be distinct nonempty IDs")
        for origin in self.allowed_origins:
            exact_origin(origin)
        return self


class MessageBody(StrictModel):
    text: str = Field(min_length=1, max_length=16000)
    client_message_id: str = Field(min_length=1, max_length=128)


class AnswerBody(StrictModel):
    revision: int
    question_id: str
    values: dict = Field(default_factory=dict)
    option_id: str | None = None


class ConfirmationBody(StrictModel):
    revision: int
    confirmed: bool


class EmbedBody(StrictModel):
    visitor_id: str | None = Field(default=None, min_length=16, max_length=256)
    agent_id: str
    host_origin: str
    instance_nonce: str | None = None
    ticket: str | None = None
    runtime_session_id: str | None = None

    @model_validator(mode="after")
    def origin(self):
        exact_origin(self.host_origin)
        return self


class RoutingRequest(StrictModel):
    capability_id: str = Field(min_length=1)
    arguments: dict = Field(default_factory=dict)


class RoutingRequestV2(RoutingRequest):
    action: str
    object_type: str
    result_type: str
    participants: list["Participant"] = Field(default_factory=list, max_length=32)
    relations: list["RoleRelation"] = Field(default_factory=list, max_length=32)
    parameter_units: dict[str, str] = Field(default_factory=dict)
    negated: StrictBool | None = None


class RoutingChoice(StrictModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)


class Proposal(StrictModel):
    decision: Literal["READY", "AMBIGUOUS", "OUT_OF_SCOPE"] = "READY"
    requests: list[RoutingRequest] = Field(default_factory=list, max_length=8)
    options: list[RoutingChoice] = Field(default_factory=list, max_length=100)
    fixture_mode: bool = False

    @model_validator(mode="after")
    def coherent_decision(self):
        if self.decision == "READY" and (not self.requests or self.options):
            raise ValueError("READY requires requests and no options")
        if self.decision == "AMBIGUOUS" and (self.requests or len(self.options) < 2 or len({x.id for x in self.options}) != len(self.options)):
            raise ValueError("AMBIGUOUS requires distinct alternatives and no requests")
        if self.decision == "OUT_OF_SCOPE" and (self.requests or self.options):
            raise ValueError("OUT_OF_SCOPE cannot carry executable requests or options")
        return self


class ProposalV2(Proposal):
    requests: list[RoutingRequestV2] = Field(default_factory=list, max_length=8)


class AccessPolicy(StrictModel):
    access_mode: Literal["workspace", "allowlist"]
    allowed_users: list[str] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def explicit_policy(self):
        if self.access_mode == "workspace" and self.allowed_users:
            raise ValueError("Workspace access cannot also specify an allowlist")
        if len(set(self.allowed_users)) != len(self.allowed_users) or any(not u.strip() for u in self.allowed_users):
            raise ValueError("Allowlist users must be distinct nonempty IDs")
        return self


class Channels(StrictModel):
    chat: bool = True
    embed: bool = True


class AppIcon(StrictModel):
    kind: Literal["emoji", "asset"] = "emoji"
    value: str = Field(default="💬", min_length=1, max_length=160)

    @model_validator(mode="after")
    def no_remote_url(self):
        if self.kind == "emoji" and (len(self.value) > 16 or any(c in self.value for c in "<>:/\\")):
            raise ValueError("Use a short emoji or a controlled icon asset")
        if self.kind == "asset" and not self.value.startswith("asset_"):
            raise ValueError("Icon must reference an uploaded asset")
        return self


class RuntimeUI(StrictModel):
    title: str = Field(default="智能体", min_length=1, max_length=160)
    icon: AppIcon = Field(default_factory=AppIcon)
    description: str = Field(default="", max_length=2000)
    welcome_message: str = Field(default="您好，请描述您需要办理的事项。", max_length=4000)
    suggested_questions: list[str] = Field(default_factory=list, max_length=12)
    primary_color: str = Field(default="#2563eb", pattern=r"^#[0-9a-fA-F]{6}$")
    input_placeholder: str = Field(default="请输入您的问题", max_length=200)
    show_history: bool = True


AgentBody.model_rebuild()
from .domain_schemas import Participant, RoleRelation
RoutingRequestV2.model_rebuild()
ProposalV2.model_rebuild()
