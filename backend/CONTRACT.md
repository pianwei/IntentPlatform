# Local implementation API contract

## INT-026 Zhiyu synthetic catalog

`POST /management/workspaces/{ws}/synthetic/zhiyu/install` accepts optional `model_profile_id`, requires current workspace admin, and installs the reviewed `zhiyu-v1@1` catalog in development. It returns the agent and counts (14 scenarios, 26 atomic functions, six composites). Identical installation is idempotent; modified catalog definitions are not silently overwritten. Production returns 404. Old `/dev/demo/seed` is now restricted to isolated `env=test` regression fixtures.

Published applications may expose `synthetic_catalog` metadata with the fixed persona choices `manager_a`, `manager_b`, `supervisor`. New anonymous sessions accept `demo_persona`; recovery cannot change it. `TokenOut.demo_persona` is nullable for ordinary applications. `POST /runtime/apps/{public_id}/demo-persona` requires the current scoped runtime token and returns a fresh runtime identity; anonymous sessions also receive a new recovery secret. History, confirmations and practice context are not inherited. Key sessions maintain a server-owned successor link; host recovery still verifies Key, visitor, user, application, Origin, epoch and revocation, never a browser-supplied user ID.

`MODEL_ANSWER` is a durable decision without an ExecutionPlan or Call. Final RESULT adds `answer_source: business | model | practice | mixed` and `practice_active`; conversation reads also expose `practice_active`. Private business facts and specific policy questions still require capabilities. Failed, unauthorized or unknown business calls never become model-only success. Internal roleplay facts and session-control fields are excluded by backend projection. Conversation practice state and catalog state are space-isolated; migrations 0015/0016 preserve old snapshots.

## INT-023 display and creation compatibility

`GET /me` and account reads default missing `text_size` to `standard`. `PATCH /me` and account updates merge only explicitly supplied preference fields with stored JSON; changing density must not reset font size, and vice versa. No schema migration is required for these JSON fields.

`GET /runtime/apps/{public_id}/conversations?include_empty=false` excludes conversations with no Run. Omitted `include_empty` defaults to `true` for existing clients. Filtering occurs after the same session, user, application, channel and workspace authorization as the existing history API; this flag grants no additional access and deletes nothing.

Function reads expose persisted `validation`. The Console uses a validation only when its revision matches the saved draft and local input is unchanged. Restoring a page does not run a connector probe. Trial chat still uses the published application and a separate runtime identity; no draft runtime endpoint was added.

## INT-021 current identity and management contract

The default and production identity mode is `local`. The retired `/api/v2/dev/login` and `/api/v2/auth/exchange` return 404 in this mode. `legacy-test` exists only for explicit `env=test` regression fixtures; descriptions of development/introspection behavior below apply only to those historical fixtures. This is not a mixed production identity mode.

All paths below begin with `/api/v2`. Account management requires a local password session and a platform administrator ID listed in server-controlled `INTENT_PLATFORM_ADMINS_JSON`; workspace admin is not platform admin. Bootstrap uses `python -m intent_platform.bootstrap_account --user-id ... --username ... --display-name ...`, interactive password input, and no default password. It never resets an existing account or grants workspace membership. Passwords use Argon2id, at least 19456 KiB / time cost 2 / parallelism 1.

| Route | Request and actual response |
|---|---|
| `POST /auth/login` | `{username,password}` → TokenOut. Usernames normalized to lowercase; failed login throttled. Also sets the refresh cookie. |
| `POST /auth/refresh` | Refresh cookie → new TokenOut, rotates the cookie and invalidates the previous access token. Concurrent reuse succeeds at most once. |
| `POST /auth/logout` | Password bearer → `{revoked:true}`, revokes that login and linked refresh credential, clears cookie. |
| `GET /me` | Password bearer → `{user_id,username,display_name,preferences:{density:"comfortable"|"compact",text_size:"standard"|"large"|"extra_large"},scope,workspaces:[{id,name,roles}],is_platform_admin,must_change_password,auth_method,fixture_mode}`. |
| `PATCH /me` | Optional `{display_name,preferences:{density?,text_size?}}` → updated profile; no role/username/user_id edits. |
| `POST /me/password` | `{current_password,new_password}` → `{changed:true,reauthenticate:true}`. New password 12–256 characters. Invalidates password sessions/refresh credentials, keeps integration Keys. |
| `GET /management/accounts` | Array of AccountOut, maximum 1000; no password hashes or secrets. |
| `POST /management/accounts` | `{username,display_name,user_id?}` → 201 `{account:AccountOut,setup_token}`. Optional user_id is an explicit binding of an existing provisioned subject or configured platform ID, never automatic history ownership. |
| `PATCH /management/accounts/{user_id}` | `{revision,active?,display_name?,preferences?}` → AccountOut; stale revision 409. Cannot disable the last active platform administrator. |
| `POST /management/accounts/{user_id}/reset-password` | → `{account,setup_token}`; revokes password sessions, refresh credentials and all owner Keys; requires password setup before login. |
| `POST /auth/set-password` | `{setup_token,new_password}` → `{changed:true}`. Setup token is single-use and defaults to 1-hour expiry. |
| `GET /management/accounts/{user_id}/operations` | Array of at most 100 `{id,actor_id,subject_id,action,created_at}`, latest first; no secrets. |
| `GET /me/api-keys` | Owner's KeyOut array. |
| `POST /me/api-keys` | `{name,expires_at?}` → 201 `{key:KeyOut,secret}`; explicit expiry must be a future timezone-aware timestamp. |
| `POST /me/api-keys/{id}/rotate` | → `{key,secret}`; increments version, immediately invalidates prior secret, tickets and derived runtime credentials. Revoked Keys cannot rotate. |
| `POST /me/api-keys/{id}/revoke` | → KeyOut. Revocation is checked on recovery, history, SSE, files and Worker send. |

AccountOut is `{user_id,username,display_name,active,must_change_password,revision,preferences,is_platform_admin,created_at}`. KeyOut is `{id,user_id,name,revoked,version,expires_at,created_at}`. Setup tokens and newly created/rotated Key secrets are returned only by that operation; list/read APIs cannot retrieve them. Passwords and supplied secrets are excluded from validation error bodies. API responses use `Cache-Control:no-store`.

Access tokens stay in memory. The Console refresh cookie is `intent_refresh`, HttpOnly, SameSite=Strict, limited to `/api/v2/auth`, and Secure in production; default lifetime 7 days. Browser write requests on auth/me/management reject an unapproved Origin; cookie refresh requires an approved Origin unless an Authorization header is supplied for a non-browser client. CORS permits credentials only for the configured exact origins.

`POST /runtime/apps/{public_id}/anonymous-sessions` accepts `{}` for first entry and returns TokenOut with `runtime_session_id` and one-time `recovery_secret`. Recovery requires both `{runtime_session_id,recovery_secret}` for that application; the response rotates the access token and returns `recovery_secret:null`. The server stores only an Argon2id digest. The browser may persist this dedicated per-application recovery credential, never an access token. Anonymous principals have no Membership, default to 7-day expiry, can access only their published public application and permitted capabilities, and cannot recover Key/password/legacy sessions. Bare IDs cannot recover history. Public Chat availability follows the published/live chat channel, not the Key owner's membership.

The iframe host retains an account-owned Key on its backend. `POST /integrations/embed-tickets` takes `Authorization: Bearer <Key secret>` and `{agent_id,host_origin,instance_nonce,visitor_id,runtime_session_id?}`. The host generates visitor_id (16–256 characters) and owns the visitor→session map; browser-submitted identity or previous session IDs are not trusted. Response `{ticket,expires_at,runtime_session_id}` creates a tokenless session on first issuance so the host can store the authoritative association. `/runtime/embed/exchange` must supply that returned session ID with ticket/agent/origin/nonce and consumes the ticket once. Key owner, current active membership, application/function grants, Key version and host policy bound every derived session. A Key grants neither management API access nor direct history reads.

`POST /integrations/runtime-sessions/{sid}/revoke`, authenticated with the same owner Key, accepts `{visitor_id}` and returns `{revoked:true}` only for the matching source Key/owner/visitor. It revokes the runtime session and its tokens; host logout calls it before clearing host state and sending HOST_LOGOUT. Different visitors sharing one Key never share runtime_session history. A revoked Key/account remains revoked after refresh, publication or rollback; already authorized and sent business operations retain their actual outcomes.

`POST /management/workspaces/{ws}/agents/{aid}/migrate-identity` accepts `{revision}` for a local publisher, returns ReleaseOut, and creates a new release changing only `identity_policy` to `local-v1`; all business dependencies are copied from the current release. Old immutable releases and conversations remain unchanged and management-only. Explicit account binding to an old user_id does not claim an old runtime session; the old automatic legacy-session claiming path is unavailable in local mode.

## Conversation management logs

Space administrators use `GET /api/v2/management/workspaces/{ws}/conversations` with optional `query,agent_id,status,created_after,created_before,limit,offset`. Limit defaults to 25 and is at most 50. Response is `{items:[{id,agent_id,agent_name,title,created_at,updated_at,turn_count,status}],total,offset,limit}`. Titles are summaries and may repeat; conversation IDs remain the stable keys.

`GET .../conversations/{cid}` accepts limit/offset and returns `{id,title,agent_id,agent_name,turn_count,turns,offset,limit}`. A turn has `{run_id,question,status,revision,created_at,finished_at,timing,results,prompts,interactions,phases}`. The UI shows the whole-turn input/final output/status/timing first, then expandable entity/dynamics/planning and execution detail. Phase status is `RECORDED|UNRECORDED|FAILED`, not an invented success. Existing `/runs/{run_id}/trace` remains the detailed management read path.

`GET .../conversations/{cid}/files/{file_id}` downloads only files referenced by that turn's approved final results. These read-only management projections do not widen runtime owner RLS. Validated answers/confirmations are recorded transactionally from this version onward; missing historical raw answers cannot be reconstructed and must not be fabricated. Agent names are unique within a workspace after trim/case normalization; IDs/public IDs/release IDs still exist even when the UI hides technical primary keys.

## INT-019 function-driven correction

The current Demo target is [DEMO_CONTRACT.md](DEMO_CONTRACT.md). Definitions add `execution_kind:direct|composite` (legacy default direct), pinned composite Skill/dependency/final-output mapping; agents add `routing_strategy:legacy|three-layer-v1` (legacy API default). `POST /workspaces/{ws}/agents/{id}/compile-preview` accepts `{revision}` and returns `{valid,issues,projection}` without publishing. `GET /workspaces/{ws}/functions/{id}/versions` returns immutable definitions; `GET /workspaces/{ws}/business-skills/{id}/versions` returns immutable rules.

New releases opt in to versioned per-agent entity/action/planning projection; old snapshots retain their original strategy. Current runtime read projection, including old conversations, exposes final results and necessary interactions only: no intermediate step diagnostics, `steps:[]`, `bindings:[]`, and whole-run processing timing. Detailed traces remain administrator-only.

`GET /api/v2/management/platform-context` returns `{can_manage_workspaces,identities:[{user_id,label}]}`; `POST /api/v2/management/workspaces` body `{name,administrator_id}` returns `{id,name,roles}` (201). Only server-configured platform principals can allocate; assigning a different administrator gives the creator no implicit tenant data access. Workspace administrators retain existing trusted membership ceilings. These additions do not authorize runtime tokens for management.

## Application contract v2 (current implementation target)

Business v1 paths remain compatible where they do not conflict with the current local identity and old-history boundary above. Identity exchange and legacy session claiming are not production compatibility promises.

- Agent management objects additionally return `public_id` (stable UUID, not a credential), `runtime_ui`, `channels`, and `access:{access_mode:"workspace"|"allowlist",allowed_users:[string]}`. `runtime_ui` is `{title,icon:{kind:"emoji"|"asset",value:string},description,welcome_message,suggested_questions:[string],primary_color:"#RRGGBB",input_placeholder,show_history:boolean}`. `channels` is `{chat:boolean,embed:boolean}`. Agent create/draft accepts optional `runtime_ui` and `channels`; omitted fields preserve existing values on PATCH. Draft PATCH always requires revision. Legacy `name/suggested_questions/allowed_origins` remain supported. Draft cannot change authorization.
- `PUT /api/v2/management/workspaces/{ws}/agents/{id}/access` and `/functions/{id}/access` body `{access_mode,allowed_users}` explicitly change live authorization. An empty allowlist denies named-user access, including Key owners; public anonymous Chat follows its channel policy rather than the agent's named-user allowlist. Anonymous capabilities still require the public workspace-access policy and do not inherit function allowlists. Agent responses and function responses include `access`. Legacy `allowed_users` in definitions is retained for old immutable snapshots, but draft changes do not change live grants.
- `PUT /api/v2/management/workspaces/{ws}/agents/{id}/channels` body `{chat:boolean,embed:boolean}` changes live availability. Disable is immediate for API, active streams and workers. Enable requires that the active release enabled that channel. Publishing only activates a channel for the first release; subsequent publishing never overrides an explicit disable.
- `POST /api/v2/management/workspaces/{ws}/assets` multipart `file` uploads a PNG/JPEG/WebP icon; returns `{id,media_type,size}`. Authorized draft preview: `GET .../assets/{id}`. No remote icon URLs or SVG.
- `GET /api/v2/runtime/apps/{public_id}` is unauthenticated and returns only `{public_id,agent_id,release_id,runtime_ui,channels,fixture_mode}` from the active release and live channel policy. No capabilities, membership, origins or conversation data. Both disabled/unpublished => 404. `runtime_ui.icon` with kind asset is rendered with `GET /api/v2/runtime/apps/{public_id}/icon`, which serves only the currently published referenced icon.
- `POST /api/v2/runtime/apps/{public_id}/sessions`, authenticated with a local password management token, body `{runtime_session_id?:string}` returns TokenOut. This optional authenticated chat path resumes only that user's password-source session. The public Chat UI uses `/anonymous-sessions`; runtime tokens cannot mint other runtime tokens, and no path inherits another source's session.
- Embed handshake/ticket/exchange use the Key host protocol above. The host stores the platform's returned runtime_session_id, and exchange must match the ticket's complete binding. Explicit logout revokes the session and cannot be renewed.
- `GET /api/v2/runtime/conversations` using runtime token returns an array `{id,conversation_id,agent_id,release_id,status,fixture_mode,title,created_at,updated_at}` for that runtime session. `GET /api/v2/runtime/conversations/{id}/history` returns `{conversation:ConversationOut,messages:[{run_id,text,revision,status,created_at}],runs:[RunOut],events:[EventOut],cursor:number}` as one database snapshot. Use cursor for subsequent events, never resend messages during recovery. History remains bound to the creating release. `show_history:false` hides the sidebar, not ownership checks.
- `GET /api/v2/runtime/files/{file_id}` requires the owning runtime identity/session and the originating function's current permission; downloads as attachment with nosniff. A file RESULT carries `files:[{id,filename,media_type,size}]` with no arbitrary download URL. Controlled connector file payloads use `{files:[{filename,media_type,content_base64}]}` and are materialized by Worker subject to size limits. These are local artifacts, not server-side fetching of a model-provided URL.
- Nginx serves `/chat/{public_id}` and `/embed/{public_id}` as the static Agent shell with `/runtime/assets/*`. It uses `auth_request` to `GET /api/v2/runtime/apps/{public_id}/chat-policy` or `/embedding-policy`. Each returns only 200 with complete `Content-Security-Policy` or 403 for unknown/unpublished/disabled channels. Embed CSP has `frame-ancestors 'self'` plus that application's published origins minus revocations; chat has `frame-ancestors 'none'`. The gateway preserves this application-specific CSP.
- Management Agent objects also return `live_channels` and `published_channels` (null before first publish); `channels` alone denotes the draft. Conversation objects include `runtime_ui` from their pinned release. `GET /api/v2/runtime/assets/{asset_id}` requires runtime authentication and permits only icons referenced by this session's conversations or its agent's current release; use it for historic icons.
- Management Agent objects return `published_origins` and `revoked_origins` for immediate host revocation controls. `/runtime/agents` returns only `{id,public_id,workspace_id,name,current_release_id,capability_ids,suggested_questions,fixture_mode}` and never management ACLs or draft settings. A conversation bound to a runtime session requires that session's token even for the same user holding a management token. In local mode, legacy direct conversations are available only through the authorized read-only management projection.
- Historical migrations created legacy session associations; local identity never claims them. Old source sessions/releases are management-only, even when a new local account explicitly shares the old subject ID. Expired credentials do not erase retained history; current source recovery never changes business release or confirmation TTL.
- Access tokens stay in memory. Nonsecret locators grant no access; only the dedicated anonymous per-application recovery credential and the HttpOnly Console refresh cookie persist as described above. Key secrets never enter runtime browser storage.
- Old immutable releases are interpreted through the supported v1 compatibility profile; runtime checks the actual prompt/compiler/connector/UI profile and rejects unavailable dependencies. New releases use explicit dependency profile `platform-v2`. Decision/request combinations are strict: READY needs requests and no choices; AMBIGUOUS needs choices and no requests; OUT_OF_SCOPE has neither.

Application contract version: `2`; existing event envelope remains `schema_version:1`. Responses are JSON except documented streams and downloads. Unified browser base URL: `http://127.0.0.1:5173`; port 8000 is the internal API service (or explicit source-development process). Auth: `Authorization: Bearer <access_token>`; errors `{detail: string|object}`. Times UTC ISO 8601. IDs opaque strings. Account/Key lists return arrays; conversation management logs use the paged objects above. Runtime sessions are owner-bound; runtime tokens cannot use management APIs.

## Historical development identity fixtures

The development login/introspection behavior in this section is retained only under explicit `identity_mode=legacy-test, env=test`. Use the INT-021 local identity section for the product. Development model mode by itself grants no identity bypass.

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
3. Host backend calls `POST /api/v2/integrations/embed-tickets` with the owner's Key and `{agent_id,host_origin,instance_nonce,visitor_id,runtime_session_id?}` → `{ticket,expires_at,runtime_session_id}`. Host identity/session association comes from its own server state, not the browser.
4. Parent sends `{type:"AGENT_INIT",protocol_version:"1",agent_id,instance_nonce,ticket,runtime_session_id}` with exact targetOrigin. iframe checks origin AND source and accepts the host-authoritative session binding.
5. iframe calls `POST /api/v2/runtime/embed/exchange` with `{ticket,agent_id,host_origin,instance_nonce,runtime_session_id}` → TokenOut. Ticket consumed atomically; replay, expiry and mismatches are rejected.
6. `AUTH_REQUIRED`, `AGENT_RESIZE`, `HOST_LOGOUT`, `AGENT_ERROR` retain protocol/agent/nonce binding. Host backend revokes the bound session before clearing its session state and sending HOST_LOGOUT. Keep access tokens in memory; never send a ticket with `*`.

New development seed allowed origins: `http://localhost:5173`, `http://127.0.0.1:5173`. Existing immutable releases keep their original origins. Production domains must be configured explicitly. The unified gateway obtains application-specific CSP from the backend channel policy endpoints described above.

No real bank function, real SSO/model/hardware acceptance is implied by fixture success. All database integration tests use PostgreSQL; SQLite is not supported.

## INT-031 business authoring catalog and version history

`GET /api/v2/management/platform/business-catalog` returns only versioned platform defaults to trusted platform administrators. Platform create/edit/version/audit operations use the same route family. Workspace routes under `/api/v2/management/workspaces/{ws}/business-catalog` expose the space-owned directory and available defaults; `POST .../adopt` pins an explicit default version, `GET .../adopt-impact` previews affected functions, and `GET .../relationships` returns the object → action → published direct function → composite dependency tree. Drafts appear only with `include_drafts=true`. Directory reads do not adopt, merge by display name, grant business permissions or rewrite releases. Space writes require the existing maintainer/publisher roles; global writes require the platform administrator.

`GET /api/v2/management/workspaces/{ws}/functions/{fid}/versions` returns immutable published versions (`version > 0`) and actual creation times for the history dialog. Negative private authoring-test snapshots are never exposed through ordinary history. Release responses expose a display-safe summary and creation time, not the full pinned configuration or model credentials. Audit/version association is shown only when the stored resource/version linkage exists; missing history is reported as unrecorded, never inferred from nearby timestamps.

## Implemented contract refinements

### Current function authoring (INT-029)

Management endpoints below remain workspace-scoped and require existing maintainer/publisher authority. `GET /workspaces/{ws}/function-options` returns the published entity/action catalog, relation labels and server-registered resolver bindings; `POST .../function-options/bootstrap` idempotently installs business defaults. Resolver choices include configured profiles and, only outside production with the installed synthetic catalogue, trusted references from this workspace's enabled immutable published function versions. Unpublished draft references and unrelated workspaces do not add choices; credentials and endpoints are never returned. Ordinary authors do not select compiler or connector revisions. New definitions use `authoring_version: function-v2`; immutable older definitions retain their original path.

Drafts declare finite typed input/output fields, business entity/action mapping and approved presentation fields. `POST .../functions/{fid}/validate` with `probe:true` starts an actual private execution of the current revision through the plan, binding, connector and call ledger. It is not a schema-only success. `GET .../functions/{fid}/execution-test` reads that same ledger. A write returns a resolved object/parameter preview and remains `WAITING_CONFIRMATION` until `POST .../execution-test/confirmations` with `{run_id, revision, confirmed}`. Test-environment write permission does not replace confirmation. Pending or UNKNOWN execution does not permit publishing or automatic resubmission. `failure:true` selects the separate expected-failure ledger for imports that require both probes; network errors and UNKNOWN cannot satisfy a definite business failure test. Publishing rechecks revision, dependencies and authority evidence.

`POST .../functions/{fid}/output-examples` with `{revision}` explicitly saves only a successful current-revision test's approved output, strips secret-looking fields and file bytes, preserves the execution revision and retains at most five examples. Ordinary list/detail responses omit test data and confirmation previews; the dedicated test endpoint checks its owner. Publishing can be performed by another authorized publisher without exposing the author's private results, but the original author's current permissions, test runtime identity and originating login session must remain valid. An expired or logged-out test identity may require a new execution test even when the draft revision has not changed.

New composite authoring selects published direct subfunctions and a Markdown Business Skill document. The function's `composite` pins the Skill version and direct-function dependency versions in `composite.dependencies`. Its `BusinessSkillVersion` is `mode:document` and holds the document, applicable goals/entities/actions and bounded input fields; dependencies are not fields of the Skill create/update request. The document rule does not contain an authored steps array. The model proposes finite steps and `final_output_mapping` for each run; the backend checks references, pinned versions, paths, authorization, budget and confirmation before dispatch. The approved plan and final mapping persist for recovery. Autonomous planning receives only explicitly selected direct capabilities, not implicit composite dependencies. Legacy template releases keep their original immutable rules.

Migration `0017` excludes private authoring-test agents, releases and conversations from public directories; it does not rewrite earlier snapshots. The explicit synthetic upgrade `POST .../synthetic/zhiyu/document-skills` actual-tests six registered documents before publishing a new `scope-v10` release. Public-model validation continues to require the source-controlled synthetic catalog; arbitrary edited business materials are not implicitly permitted.

- INT-022 adds workspace metadata pagination, optimistic renaming, platform-only administrator allocation/archive/restore, member display pagination and effective-access explanations; see [workspace lifecycle](GOVERNANCE_CONTRACT.md#workspace-directory-and-lifecycle-int-022). Archived spaces reject public, runtime, admission, publishing and intake entry points. Only authorized space administrators retain management history reads. Restoration requires fresh runtime identities and never revives old tickets, recoveries or pending business work; cross-space accounts and Keys are not globally disabled.

- OpenAPI now publishes strict input and response models for identity, function/validation, agent/release, conversation/run and event envelopes. `GET /openapi.json` is the machine-readable shared contract.
- Agent draft PATCH requires `revision`; publish accepts optional expected `revision`. Publish locks the agent and its function dependencies. Agent supports `allowed_users:[]` (workspace members); authorization changes are checked independently of release snapshots.
- `POST /api/v2/management/workspaces/{ws}/agents/{id}/revoke-origin` body `{origin}` immediately invalidates existing bound runtime tokens, event streams and queued execution. Ordinary allowed-origin draft edits do not alter the active release until published. Current local tickets also require a live owner Key, membership, visitor and exact host binding.
- CONFIRMATION includes `actions:[{capability_id,version,name,arguments}]`. A single action preserves the original `name/arguments`; multiple actions set `arguments:{actions:[...]}` and require showing every item. One confirmation authorizes only that saved run/revision and complete parameter snapshot.
- `Call` contains a stable action slot and immutable call ID across answer revisions. `dispatched_at` marks the final authorization/dispatch boundary; new messages cancel claimed-but-unsent actions, while already dispatched callbacks keep original attribution. UNKNOWN is reconciled only through the registered query endpoint. Expired RECONCILING leases may repeat the query, never the business write.
- Connector status mapping includes `failure_values` and `pending_values`, and optional `reconcile_by:"operation_id"|"idempotency_key"`. `status_endpoint` credential use must be explicitly authorized alongside the execution endpoint. Unknown remote status remains UNKNOWN; a success condition is checked only after a remote success terminal state.
- Global `ALLOWED_HOSTS` is only an outer network boundary. Actual business targets are server-granted per workspace, origin including port, path prefix and connector kind. Credential references are separately bound to workspace, kind, exact execution endpoint and any status endpoint template. No form field can expand these grants.
- Production also requires the configured trusted object/data authorization service to return `allowed:true` for user, workspace, business target, action and bound arguments before a real connector call. Local HTTP socket fixtures do not claim this bank integration has been completed.
