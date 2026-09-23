from datetime import datetime
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, UniqueConstraint, Text, LargeBinary
from uuid import uuid4
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base, uid, now
from .config import settings


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str]


class Membership(Base):
    __tablename__ = "memberships"
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    roles: Mapped[list] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(default=1)
    role_ceiling: Mapped[list] = mapped_column(JSONB, default=lambda context: list(context.get_current_parameters().get("roles", [])))


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str]
    scope: Mapped[str]
    agent_id: Mapped[str | None]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(default="development")
    delegation: Mapped[dict] = mapped_column(JSONB, default=dict)
    parent_token_id: Mapped[str | None]
    runtime_session_id: Mapped[str | None] = mapped_column(ForeignKey("runtime_sessions.id"))


class Function(Base):
    __tablename__ = "functions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("fn"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    definition: Mapped[dict] = mapped_column(JSONB)
    document_text: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(default="draft")
    latest_version: Mapped[int] = mapped_column(default=0)
    validated_revision: Mapped[int | None]
    validation: Mapped[dict | None] = mapped_column(JSONB)
    disabled: Mapped[bool] = mapped_column(default=False)
    access_mode: Mapped[str] = mapped_column(default="workspace")
    allowed_users: Mapped[list] = mapped_column(JSONB, default=list)
    source_state: Mapped[dict] = mapped_column(JSONB, default=dict)


class FunctionVersion(Base):
    __tablename__ = "function_versions"
    __table_args__ = (UniqueConstraint("function_id", "version"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("fv"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    function_id: Mapped[str] = mapped_column(ForeignKey("functions.id"))
    version: Mapped[int]
    definition: Mapped[dict] = mapped_column(JSONB)
    compiled: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("agent"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str]
    capability_ids: Mapped[list] = mapped_column(JSONB)
    allowed_origins: Mapped[list] = mapped_column(JSONB, default=list)
    suggested_questions: Mapped[list] = mapped_column(JSONB, default=list)
    current_release_id: Mapped[str | None]
    revision: Mapped[int] = mapped_column(default=1)
    revoked_origins: Mapped[list] = mapped_column(JSONB, default=list)
    allowed_users: Mapped[list] = mapped_column(JSONB, default=list)
    access_mode: Mapped[str] = mapped_column(default="workspace")
    public_id: Mapped[str] = mapped_column(String, unique=True, default=lambda: str(uuid4()))
    runtime_ui: Mapped[dict] = mapped_column(JSONB, default=dict)
    channels: Mapped[dict] = mapped_column(JSONB, default=lambda: {"chat": True, "embed": True})
    live_channels: Mapped[dict] = mapped_column(JSONB, default=lambda: {"chat": True, "embed": True})
    integration_refs: Mapped[list] = mapped_column(JSONB, default=lambda: ["development"] if settings().allow_dev_auth and settings().env != "production" else [])
    revoked_integration_refs: Mapped[list] = mapped_column(JSONB, default=list)
    planning_enabled: Mapped[bool] = mapped_column(default=False)
    business_skill_ids: Mapped[list] = mapped_column(JSONB, default=list)
    model_profile_id: Mapped[str | None] = mapped_column(ForeignKey("model_profiles.id"))


class Release(Base):
    __tablename__ = "agent_releases"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("release"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    version: Mapped[int]
    snapshot: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ActionDefinition(Base):
    __tablename__ = "action_definitions"
    __table_args__ = (UniqueConstraint("workspace_id", "key"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("action"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    key: Mapped[str]
    definition: Mapped[dict] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(default=1)
    latest_version: Mapped[int] = mapped_column(default=0)


class ActionDefinitionVersion(Base):
    __tablename__ = "action_definition_versions"
    __table_args__ = (UniqueConstraint("action_id", "version"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("action_version"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    action_id: Mapped[str] = mapped_column(ForeignKey("action_definitions.id"))
    version: Mapped[int]
    definition: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BusinessSkill(Base):
    __tablename__ = "business_skills"
    __table_args__ = (UniqueConstraint("workspace_id", "key"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("skill"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    key: Mapped[str]
    definition: Mapped[dict] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(default=1)
    latest_version: Mapped[int] = mapped_column(default=0)
    disabled: Mapped[bool] = mapped_column(default=False)


class BusinessSkillVersion(Base):
    __tablename__ = "business_skill_versions"
    __table_args__ = (UniqueConstraint("skill_id", "version"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("skill_version"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("business_skills.id"))
    version: Mapped[int]
    definition: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ModelProfile(Base):
    __tablename__ = "model_profiles"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("model"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str]
    revision: Mapped[int] = mapped_column(default=1)
    latest_version: Mapped[int] = mapped_column(default=0)
    disabled: Mapped[bool] = mapped_column(default=False)


class ModelProfileVersion(Base):
    __tablename__ = "model_profile_versions"
    __table_args__ = (UniqueConstraint("profile_id", "version"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("model_version"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("model_profiles.id"))
    version: Mapped[int]
    config: Mapped[dict] = mapped_column(JSONB)
    secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("conv"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    release_id: Mapped[str] = mapped_column(ForeignKey("agent_releases.id"))
    user_id: Mapped[str] = mapped_column(index=True)
    token_id: Mapped[str] = mapped_column(ForeignKey("auth_tokens.id"))
    sequence: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(default="active")
    runtime_session_id: Mapped[str | None] = mapped_column(ForeignKey("runtime_sessions.id"), index=True)
    title: Mapped[str] = mapped_column(default="新会话")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("conversation_id", "client_message_id"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("run"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    user_id: Mapped[str] = mapped_column(index=True)
    client_message_id: Mapped[str]
    text: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(default="RECEIVED", index=True)
    decision: Mapped[str | None]
    question: Mapped[dict | None] = mapped_column(JSONB)
    proposal: Mapped[dict | None] = mapped_column(JSONB)
    answers: Mapped[dict] = mapped_column(JSONB, default=dict)
    confirmed: Mapped[bool] = mapped_column(default=False)
    confirmation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    bindings: Mapped[dict] = mapped_column(JSONB, default=dict)
    validation_evidence: Mapped[dict | None] = mapped_column(JSONB)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Call(Base):
    __tablename__ = "calls"
    __table_args__ = (UniqueConstraint("run_id", "action_id"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("call"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    action_id: Mapped[str]
    revision: Mapped[int]
    capability_id: Mapped[str]
    version: Mapped[int]
    definition: Mapped[dict] = mapped_column(JSONB)
    arguments: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(default="PENDING", index=True)
    result: Mapped[dict | None] = mapped_column(JSONB)
    operation_id: Mapped[str | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bindings: Mapped[dict] = mapped_column(JSONB, default=dict)


class ExecutionPlan(Base):
    __tablename__ = "execution_plans"
    __table_args__ = (UniqueConstraint("run_id"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("plan"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    release_id: Mapped[str] = mapped_column(ForeignKey("agent_releases.id"))
    source: Mapped[str]
    skill_version_id: Mapped[str | None] = mapped_column(ForeignKey("business_skill_versions.id"))
    status: Mapped[str] = mapped_column(default="PENDING")
    inputs: Mapped[dict] = mapped_column(JSONB, default=dict)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlanStep(Base):
    __tablename__ = "plan_steps"
    __table_args__ = (UniqueConstraint("plan_id", "step_key"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("step"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("execution_plans.id"))
    step_key: Mapped[str]
    capability_id: Mapped[str] = mapped_column(ForeignKey("functions.id"))
    version: Mapped[int]
    definition: Mapped[dict] = mapped_column(JSONB)
    spec: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(default="PENDING")
    arguments: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[dict | None] = mapped_column(JSONB)
    call_ids: Mapped[list] = mapped_column(JSONB, default=list)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ManagementTrace(Base):
    __tablename__ = "management_traces"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("trace"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    layer: Mapped[str]
    kind: Mapped[str]
    detail: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DemoState(Base):
    __tablename__ = "demo_states"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    state: Mapped[dict] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(default=1)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("conversation_id", "sequence"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("event"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    revision: Mapped[int]
    sequence: Mapped[int]
    event_type: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Handshake(Base):
    __tablename__ = "handshakes"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    agent_id: Mapped[str]
    workspace_id: Mapped[str]
    host_origin: Mapped[str]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Ticket(Base):
    __tablename__ = "embed_tickets"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str]
    agent_id: Mapped[str]
    workspace_id: Mapped[str]
    host_origin: Mapped[str]
    instance_nonce: Mapped[str]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed: Mapped[bool] = mapped_column(default=False)
    source_token_id: Mapped[str] = mapped_column(ForeignKey("auth_tokens.id"))
    runtime_session_id: Mapped[str | None]
    integration_id: Mapped[str | None]


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("audit"))
    workspace_id: Mapped[str] = mapped_column(index=True)
    user_id: Mapped[str]
    action: Mapped[str]
    resource_id: Mapped[str]
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("doc"))
    workspace_id: Mapped[str] = mapped_column(index=True)
    filename: Mapped[str]
    text: Mapped[str] = mapped_column(Text)
    latest_revision: Mapped[int] = mapped_column(default=1)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "revision"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("document_version"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    revision: Mapped[int]
    filename: Mapped[str]
    text: Mapped[str] = mapped_column(Text)
    original: Mapped[bytes | None] = mapped_column(LargeBinary)
    sections: Mapped[list] = mapped_column(JSONB)
    created_by: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OpenAPIImport(Base):
    __tablename__ = "openapi_imports"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("import"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    document_revision: Mapped[int]
    parser_version: Mapped[str] = mapped_column(default="openapi-3.0-finite-v1")
    report: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class IngestJob(Base):
    __tablename__ = "ingest_jobs"
    __table_args__ = (UniqueConstraint("workspace_id", "created_by", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("ingest"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    created_by: Mapped[str]
    token_id: Mapped[str]
    idempotency_key: Mapped[str]
    kind: Mapped[str]
    status: Mapped[str] = mapped_column(default="PENDING")
    filename: Mapped[str | None]
    payload: Mapped[bytes | None] = mapped_column(LargeBinary)
    document_id: Mapped[str | None]
    source_revision: Mapped[int | None]
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[dict | None] = mapped_column(JSONB)
    attempt_id: Mapped[str | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Evaluation(Base):
    __tablename__ = "evaluations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("eval"))
    workspace_id: Mapped[str] = mapped_column(index=True)
    result: Mapped[dict] = mapped_column(JSONB)


class RuntimeSession(Base):
    __tablename__ = "runtime_sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("session"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    user_id: Mapped[str] = mapped_column(index=True)
    channel: Mapped[str]
    host_origin: Mapped[str | None]
    revoked: Mapped[bool] = mapped_column(default=False)
    legacy_claimable: Mapped[bool] = mapped_column(default=False)
    current_token_id: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    integration_id: Mapped[str | None]
    identity_policy: Mapped[dict | None] = mapped_column(JSONB)


class WorkspaceGovernance(Base):
    __tablename__ = "workspace_governance"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(default=1)
    limits: Mapped[dict] = mapped_column(JSONB)


class ProcessingAttempt(Base):
    __tablename__ = "processing_attempts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("attempt"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    revision: Mapped[int]
    call_id: Mapped[str | None]
    kind: Mapped[str]
    status: Mapped[str] = mapped_column(default="active")
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timings: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_stage: Mapped[str | None]


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("asset"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    media_type: Mapped[str]
    data: Mapped[bytes] = mapped_column(LargeBinary)


class ResultFile(Base):
    __tablename__ = "result_files"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("file"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    call_id: Mapped[str] = mapped_column(ForeignKey("calls.id"))
    filename: Mapped[str]
    media_type: Mapped[str]
    data: Mapped[bytes] = mapped_column(LargeBinary)


class DomainPackage(Base):
    __tablename__ = "domain_packages"
    __table_args__ = (UniqueConstraint("workspace_id", "key"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("domain"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    key: Mapped[str]
    revision: Mapped[int] = mapped_column(default=1)
    latest_version: Mapped[int] = mapped_column(default=0)
    definition: Mapped[dict] = mapped_column(JSONB)
    validation: Mapped[dict | None] = mapped_column(JSONB)
    validated_revision: Mapped[int | None]


class DomainVersion(Base):
    __tablename__ = "domain_versions"
    __table_args__ = (UniqueConstraint("package_id", "version"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("domainversion"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    package_id: Mapped[str] = mapped_column(ForeignKey("domain_packages.id"))
    version: Mapped[int]
    definition: Mapped[dict] = mapped_column(JSONB)
    compiled: Mapped[dict] = mapped_column(JSONB)
    published_by: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
