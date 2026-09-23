export type Workspace = { id: string; name: string; roles: string[] };
export type Identity = {
  access_token: string;
  user_id: string;
  roles?: string[];
  fixture_mode: boolean;
  agent_id?: string;
  runtime_session_id?: string;
  expires_at?: string;
};
export type AccessPolicy = { access_mode: "workspace" | "allowlist"; allowed_users: string[] };
export type Channels = { chat: boolean; embed: boolean };
export type RuntimeUI = {
  title: string;
  icon: { kind: "emoji" | "asset"; value: string };
  description: string;
  welcome_message: string;
  suggested_questions: string[];
  primary_color: string;
  input_placeholder: string;
  show_history: boolean;
};
export type PublicApp = { public_id: string; agent_id: string; release_id: string; runtime_ui: RuntimeUI; channels: Channels; fixture_mode: boolean };
export type FunctionDefinition = {
  compiler_version?: "finite-json-v1" | "finite-json-v2";
  domain_refs?: { package_id: string; version: number }[];
  participant_roles?: { role: string; type: string; required: boolean; min_count: number; max_count: number }[];
  relations?: { name: string; source: string; target: string }[];
  argument_bindings?: Record<string, { role: string; resolver_ref: string }>;
  parameter_semantics?: Record<string, { kind: "quantity" | "date" | "datetime"; unit?: string | null; allow_relative?: boolean }>;
  result_bindings?: { name: string; entity_type: string; collection_path: string; id_field: string; label_field: string; resolver_ref: string }[];
  trusted_preconditions?: Record<string, unknown> | null;
  name: string;
  description: string;
  object_type: string;
  action: string;
  result_type: string;
  aliases: string[];
  input_schema: Record<string, unknown>;
  output_schema?: Record<string, unknown> | null;
  connector: {
    version?: string;
    http_mapping?: { path: Record<string, string>; query: Record<string, string>; headers: Record<string, string>; body: Record<string, string> } | null;
    http_body_required?: boolean;
    http_failure_statuses?: number[];
    kind: "http" | "dify" | "hiagent" | "fixture";
    endpoint: string | null;
    method: string;
    credential_ref: string | null;
    timeout_seconds: number;
    idempotency_header?: string | null;
    status_endpoint?: string | null;
  };
  effect: "READ_ONLY" | "WRITE";
  requires_confirmation: boolean;
  allowed_users: string[];
  preconditions: unknown[];
  presentation: {
    component: string;
    title: string;
    field_map: Record<string, string>;
    files_field?: string | null;
  };
  positive_examples: Record<string, unknown>[];
  negative_examples: Record<string, unknown>[];
};
export type Capability = {
  id: string;
  workspace_id: string;
  name: string;
  description: string;
  revision: number;
  status: string;
  definition: FunctionDefinition;
  latest_version: number | null;
  is_fixture: boolean;
  validation?: unknown;
};
export type Agent = {
  id: string;
  public_id?: string;
  runtime_ui?: RuntimeUI;
  channels?: Channels;
  live_channels?: Channels;
  published_channels?: Channels | null;
  access?: AccessPolicy;
  name: string;
  workspace_id: string;
  revision?: number;
  capability_ids?: string[];
  current_release_id?: string;
  release_id?: string;
  allowed_origins?: string[];
  published_origins?: string[];
  revoked_origins?: string[];
  integration_refs?: string[];
  published_integration_refs?: string[] | null;
  revoked_integration_refs?: string[];
  suggested_questions: string[];
  fixture_mode: boolean;
  planning_enabled?: boolean;
  business_skill_ids?: string[];
  model_profile_id?: string | null;
  capabilities?: { id: string; name: string; description: string }[];
};
export type RuntimeStatus =
  | "RECEIVED"
  | "ROUTING"
  | "WAITING_INPUT"
  | "WAITING_CONFIRMATION"
  | "EXECUTING"
  | "SUCCEEDED"
  | "FAILED"
  | "UNKNOWN"
  | "CANCELLED";
export type RuntimeEvent = {
  schema_version: string;
  conversation_id: string;
  run_id: string;
  revision: number;
  sequence: number;
  event_type: "STATUS" | "QUESTION" | "CONFIRMATION" | "RESULT" | "ERROR";
  payload: Record<string, unknown>;
};
export type RuntimeStepSummary = {
  name: string;
  status: string;
  input: Record<string, unknown>;
  output: unknown | null;
  elapsed_ms: number | null;
};
export type RuntimeTimingSummary = {
  total_ms: number | null;
  user_wait_ms: number | null;
  processing_ms: number | null;
};
export type RuntimeAttemptSummary = {
  stage: string;
  status: string;
  duration_ms: number | null;
};
export type Run = {
  run_id: string;
  conversation_id: string;
  revision: number;
  status: RuntimeStatus;
  decision?: string;
  question?: { id: string; kind?: string; fields?: Field[]; options?: Choice[] } | null;
  bindings?: Record<string, unknown>[];
  calls?: { id: string; status: string }[];
  steps?: RuntimeStepSummary[];
  timing?: RuntimeTimingSummary;
  fixture_mode?: boolean;
};
export type Field = {
  name: string;
  label?: string;
  unit?: string | null;
  type: string | string[];
  required: boolean;
};
export type Choice = { id: string; label: string };
export type Conversation = {
  id: string;
  conversation_id: string;
  agent_id: string;
  release_id: string;
  status: string;
  fixture_mode: boolean;
  title?: string;
  created_at?: string;
  updated_at?: string;
  runtime_ui?: RuntimeUI;
};
export type ConversationHistory = {
  conversation: Conversation;
  messages: { run_id: string; text: string; revision: number; status: string; created_at: string }[];
  runs: Run[];
  events: RuntimeEvent[];
  cursor: number;
};
export type RuntimeFile = { id: string; filename: string; media_type: string; size: number };
export type Validation = {
  valid: boolean;
  revision: number;
  errors: string[];
  checks: Record<string, unknown>;
  probe: unknown;
};
export type Release = {
  release_id: string;
  id?: string;
  version: number;
  capabilities: { capability_id: string; version: number }[];
  fixture_mode: boolean;
};
