# Local implementation API contract

## Application contract v2 (current implementation target)

Existing v1 paths remain compatible. New fields below are authoritative for the shared frontend.

- Agent management objects additionally return `public_id` (stable UUID, not a credential), `runtime_ui`, `channels`, and `access:{access_mode:"workspace"|"allowlist",allowed_users:[string]}`. `runtime_ui` is `{title,icon:{kind:"emoji"|"asset",value:string},description,welcome_message,suggested_questions:[string],primary_color:"#RRGGBB",input_placeholder,show_history:boolean}`. `channels` is `{chat:boolean,embed:boolean}`. Agent create/draft accepts optional `runtime_ui` and `channels`; omitted fields preserve existing values on PATCH. Draft PATCH always requires revision. Legacy `name/suggested_questions/allowed_origins` remain supported. Draft cannot change authorization.
- `PUT /api/v2/management/workspaces/{ws}/agents/{id}/access` and `/functions/{id}/access` body `{access_mode,allowed_users}` explicitly change live authorization; allowlist empty denies everyone. Agent responses and function responses include `access`. Legacy `allowed_users` in definitions is retained for old immutable snapshots, but draft changes do not change live grants.
- `PUT /api/v2/management/workspaces/{ws}/agents/{id}/channels` body `{chat:boolean,embed:boolean}` changes live availability. Disable is immediate for API, active streams and workers. Enable requires that the active release enabled that channel. Publishing only activates a channel for the first release; subsequent publishing never overrides an explicit disable.
- `POST /api/v2/management/workspaces/{ws}/assets` multipart `file` uploads a PNG/JPEG/WebP icon; returns `{id,media_type,size}`. Authorized draft preview: `GET .../assets/{id}`. No remote icon URLs or SVG.
- `GET /api/v2/runtime/apps/{public_id}` is unauthenticated and returns only `{public_id,agent_id,release_id,runtime_ui,channels,fixture_mode}` from the active release and live channel policy. No capabilities, membership, origins or conversation data. Both disabled/unpublished => 404. `runtime_ui.icon` with kind asset is rendered with `GET /api/v2/runtime/apps/{public_id}/icon`, which serves only the currently published referenced icon.
- `POST /api/v2/runtime/apps/{public_id}/sessions`, authenticated with a management identity token, body `{runtime_session_id?:string}` returns `{access_token,token_type,user_id,agent_id,runtime_session_id,expires_at,fixture_mode}`. Creates or resumes a **chat** session only for the same trusted user+agent. The token has runtime scope. `POST /api/v2/auth/exchange` also accepts `public_id` and optional `runtime_session_id` to perform this exchange directly from the trusted identity token. Runtime tokens cannot mint other runtime tokens.
- Embed handshake/ticket/exchange retain v1 fields. Add optional `runtime_session_id` to ticket/exchange bodies; the ticket binds the requested session, and exchange must match it. Resume requires the same authenticated user+agent+exact host origin and a non-revoked embed session. Missing ID creates a new session. Token replies include `runtime_session_id` and `expires_at`. Expiry permits trusted reauthentication of the same session; explicit logout revokes the session and cannot be renewed.
- `GET /api/v2/runtime/conversations` using runtime token returns an array `{id,conversation_id,agent_id,release_id,status,fixture_mode,title,created_at,updated_at}` for that runtime session. `GET /api/v2/runtime/conversations/{id}/history` returns `{conversation:ConversationOut,messages:[{run_id,text,revision,status,created_at}],runs:[RunOut],events:[EventOut],cursor:number}` as one database snapshot. Use cursor for subsequent events, never resend messages during recovery. History remains bound to the creating release. `show_history:false` hides the sidebar, not ownership checks.
- `GET /api/v2/runtime/files/{file_id}` requires the owning runtime identity/session and the originating function's current permission; downloads as attachment with nosniff. A file RESULT carries `files:[{id,filename,media_type,size}]` with no arbitrary download URL. Controlled connector file payloads use `{files:[{filename,media_type,content_base64}]}` and are materialized by Worker subject to size limits. These are local artifacts, not server-side fetching of a model-provided URL.
- Nginx serves `/chat/{public_id}` and `/embed/{public_id}` as the static Agent shell with `/runtime/assets/*`. It uses `auth_request` to `GET /api/v2/runtime/apps/{public_id}/chat-policy` or `/embedding-policy`. Each returns only 200 with complete `Content-Security-Policy` or 403 for unknown/unpublished/disabled channels. Embed CSP has `frame-ancestors 'self'` plus that application's published origins minus revocations; chat has `frame-ancestors 'none'`. The gateway preserves this application-specific CSP.
- Management Agent objects also return `live_channels` and `published_channels` (null before first publish); `channels` alone denotes the draft. Conversation objects include `runtime_ui` from their pinned release. `GET /api/v2/runtime/assets/{asset_id}` requires runtime authentication and permits only icons referenced by this session's conversations or its agent's current release; use it for historic icons.
- Management Agent objects return `published_origins` and `revoked_origins` for immediate host revocation controls. `/runtime/agents` returns only `{id,public_id,workspace_id,name,current_release_id,capability_ids,suggested_questions,fixture_mode}` and never management ACLs or draft settings. A conversation bound to a runtime session requires that session's token even for the same user holding a management token; management compatibility only covers old directly created conversations without a runtime session.
- Migration preserves eligible v1 management-token conversations in a legacy chat session per user+agent. The first trusted chat-session exchange without a session ID claims only that same user's matching legacy session. Explicitly revoked old conversations/tokens/parents cannot be claimed. Old runtime-token sessions preserve their host binding and inherit explicit parent logout. Expired credentials alone do not erase history. No run revision, confirmation TTL or release changes during renewal.
- Tokens stay in memory. Browser storage may hold only nonsecret `public_id/runtime_session_id/conversation_id`; possession of these IDs never grants access.
- Old immutable releases are interpreted through the supported v1 compatibility profile; runtime checks the actual prompt/compiler/connector/UI profile and rejects unavailable dependencies. New releases use explicit dependency profile `platform-v2`. Decision/request combinations are strict: READY needs requests and no choices; AMBIGUOUS needs choices and no requests; OUT_OF_SCOPE has neither.

Application contract version: `2`; existing event envelope remains `schema_version:1`. All routes return JSON unless `stream=true` is requested. Unified browser base URL: `http://127.0.0.1:5173`; port 8000 is the internal API service (or explicit source-development process). Auth: `Authorization: Bearer <access_token>`; errors `{detail: string|object}`. Times UTC ISO 8601. IDs opaque strings. All list endpoints return arrays. Runtime sessions are owner-bound; runtime tokens cannot use management APIs.

## Development and identity

- `GET /health` → `{status:"ok", database:"postgresql", environment:"development", fixture_mode:true}`.
- `POST /api/v2/dev/login` body `{user_id:"dev-admin"}` (default) → `{access_token,token_type:"bearer",user_id,workspace_id:"ws-dev",roles:["admin","maintainer","publisher","user"],fixture_mode:true}`. Also `dev-reader` (user) and `dev-other` (separate workspace `ws-other`). Creates only development identities/spaces. Production returns 404.
- `POST /api/v2/dev/seed` authenticated manager → `{workspace_id,functions:[...],agent:{...},fixture_mode:true}`. Idempotently creates TWO explicitly named development fixtures, never 20 real capabilities. Agent published automatically for local walkthrough. Suggested inputs returned as `suggested_questions` on agent.
- `POST /api/v2/auth/exchange` body `{identity_token}` → login token response. Configured internal introspection service validates identity; existing workspace memberships determine scopes. No client-supplied identity is trusted.
- `GET /api/v2/me` → `{user_id,scope:"management"|"runtime",workspaces:[{id,name,roles}],fixture_mode}`.

## Management

Prefix `/api/v2/management`.

- `GET /workspaces` → `[{id,name,roles}]`.
- `GET /workspaces/{ws}/functions` → `[{id,workspace_id,name,description,revision,status:"draft"|"validated"|"published"|"disabled",definition,latest_version,is_fixture,validation}]`.
- `POST /workspaces/{ws}/functions/import` body `{definition: FunctionDefinition, document_text?:string}` → function object above (201).
- `POST /workspaces/{ws}/documents` multipart `file` → `{id,filename,text,source,fixture_mode:false}`; local .txt/.md/.docx/.pdf, no external parser.
- `PATCH /workspaces/{ws}/functions/{id}/draft` body `{revision:number,definition:FunctionDefinition}` → function; stale revision 409. Editing creates a new draft revision; published immutable versions remain unchanged.
- `POST /workspaces/{ws}/functions/{id}/validate` body `{arguments?:object,probe?:boolean}` → `{valid:boolean,revision,errors:[string],checks:{...},probe:{...}|null}`. `probe:true` invokes connector in authorized test context; writes require explicit `allow_effects:true`.
- `POST /workspaces/{ws}/functions/{id}/publish` body optional `{}` → function with `latest_version` incremented. Requires validation of current revision.
- `POST /workspaces/{ws}/functions/{id}/disable` → function. Immediately excludes it from old releases too.
- `GET /workspaces/{ws}/agents` → `[{id,workspace_id,name,capability_ids,current_release_id,allowed_origins,suggested_questions,fixture_mode}]`.
- `POST /workspaces/{ws}/agents` body `{name,capability_ids:[string],allowed_origins?:[string],suggested_questions?:[string]}` → agent (201).
- `PATCH /workspaces/{ws}/agents/{id}/draft` same fields as creation → agent.
- `POST /workspaces/{ws}/agents/{id}/publish` body optional `{}` → `{agent_id,release_id,version,capabilities:[{capability_id,version}],fixture_mode}`. Atomic immutable snapshot, no silent model substitution.
- `GET /workspaces/{ws}/agents/{id}/releases` → release array.
- `POST /workspaces/{ws}/agents/{id}/rollback` body `{release_id}` → agent. New conversations use that release; existing conversations never migrate.
- `GET /workspaces/{ws}/audit` → audit array; no secrets/raw sensitive responses.
- `POST /workspaces/{ws}/evaluations` body `{agent_id,cases:[{text,expected_capability_id}],label}` → `{id,label,total,correct,accuracy,results,fixture_mode}`. Development diagnostic, not frozen S1/S2/S3 or real acceptance.

FunctionDefinition (optional fields default as shown):

```json
{
  "name":"开发夹具：资料查询",
  "description":"本地演示，不计入真实功能验收",
  "object_type":"dev:Record", "action":"READ", "result_type":"dev:Detail",
  "aliases":["查询资料"],
  "input_schema":{"type":"object","properties":{"record_id":{"type":"string","minLength":1}},"required":["record_id"],"additionalProperties":false},
  "connector":{"kind":"fixture","endpoint":null,"method":"GET","credential_ref":null,"timeout_seconds":10,"idempotency_header":null,"status_endpoint":null},
  "effect":"READ_ONLY", "requires_confirmation":false,
  "allowed_users":[],
  "preconditions":[],
  "presentation":{"component":"detail","title":"资料","field_map":{}},
  "positive_examples":[],"negative_examples":[]
}
```

`connector.kind`: `fixture|http|dify|hiagent`. Endpoint/credential references must pass configured allowlists; secrets never returned. Dify/HiAgent have configured HTTP workflow adapters. `effect`: `READ_ONLY|WRITE`; writes always require confirmation in this implementation. `allowed_users:[]` means workspace members. Conditions are `{field,operator:"eq"|"ne"|"in"|"exists",value}`; UNKNOWN blocks execution. Schema subset: scalar string/integer/number/boolean, enum/minimum/maximum/minLength/maxLength, arrays with scalar items and minItems/maxItems, explicit null unions; unsupported keywords rejected by compiler. Presentation component: `detail|table|text|markdown|citation|file`; fallback detail is sanitized data. Model-only dev aliases are marked fixture behavior, not production keyword routing.

## Runtime

Prefix `/api/v2/runtime`.

- `GET /agents` → accessible published agent array.
- `GET /agents/{id}` → `{id,name,workspace_id,release_id,capabilities:[{id,name,description}],suggested_questions,fixture_mode}`.
- `POST /agents/{id}/conversations` optional body `{}` → `{id,conversation_id,agent_id,release_id,status:"active",fixture_mode}` (201).
- `POST /conversations/{id}/messages` body `{text,client_message_id}` → `{run_id,conversation_id,revision,status,duplicate:boolean}` (202). `client_message_id` deduplicates within conversation. Processing is Worker-driven; poll events/run.
- `GET /runs/{id}` → `{run_id,conversation_id,revision,status,decision,question:{id,fields:[{name,type,required}],options:[{id,label}]}|null,calls:[{id,capability_id,status}],fixture_mode}`.
- `POST /runs/{id}/answers` body `{revision,question_id,values?:object,option_id?:string}` → run object. Stale question/revision 409.
- `POST /runs/{id}/confirmations` body `{revision,confirmed:boolean}` → run object. Confirms only saved parameters/capability and current identity; expires server-side. false cancels.
- `POST /runs/{id}/cancel` body `{revision}` → run object. Already dispatched work cannot be advertised as undone.
- `GET /conversations/{id}/events?after=0` → event array. `stream=true` → SSE with JSON `data`, event `id=sequence`; auth header supported via fetch. Reconnect never resends messages.
- `POST /sessions/logout` → `{revoked:true}`; revokes current token/runtime session and prevents future event reads.

Event envelope (all events include these exact names):

```json
{"schema_version":"1","conversation_id":"...","run_id":"...","revision":1,"sequence":1,"event_type":"STATUS","payload":{"status":"ROUTING","fixture_mode":true}}
```

Types: `STATUS` (`{status,decision?,message?,fixture_mode}`), `QUESTION` (`{question_id,fields,options,message}`), `CONFIRMATION` (`{capability_id,name,arguments,expires_at,message}`), `RESULT` (`{call_id,status,component,title,data,fields?:[{label,value}],provenance:{capability_id,version},fixture_mode}`), `ERROR` (`{code,message,trace_id}`). Statuses: `RECEIVED|ROUTING|WAITING_INPUT|WAITING_CONFIRMATION|EXECUTING|SUCCEEDED|FAILED|UNKNOWN|CANCELLED`; decision `READY|NEEDS_ARGUMENTS|AMBIGUOUS|NEEDS_CONFIRMATION|OUT_OF_SCOPE|FORBIDDEN|ERROR`. Frontend must preserve run/revision attribution.

## iframe

1. Agent Web calls unauthenticated `POST /api/v2/runtime/embed/handshakes` body `{agent_id,host_origin}` → `{instance_nonce,expires_at,protocol_version:"1"}`. Host must match published `allowed_origins`.
2. iframe sends parent exact origin `{type:"AGENT_READY",protocol_version:"1",agent_id,instance_nonce}`.
3. Trusted host backend (local dev host may use explicit dev user token) calls `POST /api/v2/integrations/embed-tickets` authenticated as delegated user body `{agent_id,host_origin,instance_nonce}` → `{ticket,expires_at}`. Production requires configured trusted integration identity path; direct dev impersonation unavailable.
4. Parent sends `{type:"AGENT_INIT",protocol_version:"1",agent_id,instance_nonce,ticket}` with exact targetOrigin. iframe checks origin AND source.
5. iframe calls `POST /api/v2/runtime/embed/exchange` body `{ticket,agent_id,host_origin,instance_nonce}` → `{access_token,token_type:"bearer",user_id,agent_id,fixture_mode}`. Ticket consumed atomically, replay/expired/mismatch rejected.
6. `AUTH_REQUIRED`, `AGENT_RESIZE`, `HOST_LOGOUT`, `AGENT_ERROR` messages also include protocol/agent/nonce; logout calls server endpoint. Keep tokens in memory. Never send ticket with `*`.

New development seed allowed origins: `http://localhost:5173`, `http://127.0.0.1:5173`. Existing immutable releases keep their original origins. Production domains must be configured explicitly. The unified gateway obtains application-specific CSP from the backend channel policy endpoints described above.

No real bank function, real SSO/model/hardware acceptance is implied by fixture success. All database integration tests use PostgreSQL; SQLite is not supported.

## Implemented contract refinements

- OpenAPI now publishes strict input and response models for identity, function/validation, agent/release, conversation/run and event envelopes. `GET /openapi.json` is the machine-readable shared contract.
- Agent draft PATCH requires `revision`; publish accepts optional expected `revision`. Publish locks the agent and its function dependencies. Agent supports `allowed_users:[]` (workspace members); authorization changes are checked independently of release snapshots.
- `POST /api/v2/management/workspaces/{ws}/agents/{id}/revoke-origin` body `{origin}` immediately invalidates existing bound runtime tokens, event streams and queued execution. Ordinary allowed-origin draft edits do not alter the active release until published. Tokens store the exact host binding; production tickets additionally require trusted introspection integration delegation.
- CONFIRMATION includes `actions:[{capability_id,version,name,arguments}]`. A single action preserves the original `name/arguments`; multiple actions set `arguments:{actions:[...]}` and require showing every item. One confirmation authorizes only that saved run/revision and complete parameter snapshot.
- `Call` contains a stable action slot and immutable call ID across answer revisions. `dispatched_at` marks the final authorization/dispatch boundary; new messages cancel claimed-but-unsent actions, while already dispatched callbacks keep original attribution. UNKNOWN is reconciled only through the registered query endpoint. Expired RECONCILING leases may repeat the query, never the business write.
- Connector status mapping includes `failure_values` and `pending_values`, and optional `reconcile_by:"operation_id"|"idempotency_key"`. `status_endpoint` credential use must be explicitly authorized alongside the execution endpoint. Unknown remote status remains UNKNOWN; a success condition is checked only after a remote success terminal state.
- Global `ALLOWED_HOSTS` is only an outer network boundary. Actual business targets are server-granted per workspace, origin including port, path prefix and connector kind. Credential references are separately bound to workspace, kind, exact execution endpoint and any status endpoint template. No form field can expand these grants.
- Production also requires the configured trusted object/data authorization service to return `allowed:true` for user, workspace, business target, action and bound arguments before a real connector call. Local HTTP socket fixtures do not claim this bank integration has been completed.
