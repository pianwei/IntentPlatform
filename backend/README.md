# Backend local engineering implementation

Python/FastAPI API and independent Worker share SQLAlchemy/PostgreSQL models. `intent_app` runs with no superuser or RLS bypass privileges; the separate `intent_owner` identity performs migrations. SQLite is deliberately unsupported. [CONTRACT.md](CONTRACT.md) defines frontend integration.

This code provides runnable local engineering, not completed bank acceptance. Development identity/model/business fixtures are explicit and cannot run under `INTENT_ENV=production`. `/api/v2/dev/seed` registers only two clearly named synthetic capabilities, never a pretend list of 20 real bank functions.

## Commands

From `backend/`, with the PostgreSQL service and owner/app roles already provisioned:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt -c constraints.txt
$env:PYTHONPATH = "$PWD/src"
.venv/Scripts/python -m intent_platform.migrate
.venv/Scripts/python -m uvicorn intent_platform.main:app --host 127.0.0.1 --port 8000
```

Run `.venv/Scripts/python -m intent_platform.worker` in a second terminal with the same runtime configuration. `alembic -c alembic.ini upgrade head` is also available for the initial schema. The owner credential is for the migration process only; never replace the API/Worker's application credential with it.

`migrate` runs Alembic to head. Revision `0001` reads frozen DDL from `src/intent_platform/migrations/versions/0001_schema.json`, independent of future ORM edits. Migration Python/DDL resources are included in the wheel; the CLI locates them inside the installed package and does not depend on the source working directory. It also recognizes this first development session's pre-Alembic bootstrap for a one-time local adoption; production refuses implicit adoption. Future changes require a new Alembic revision. There is no automatic destructive downgrade. Versioned FunctionVersion and AgentRelease rows reject UPDATE/DELETE at the PostgreSQL trigger level. Reverting a release changes only the agent's default pointer.

Tests use a separately provisioned `intent_test` PostgreSQL database with the same roles and grants:

```powershell
.venv/Scripts/python -m pytest -q
```

The test configuration defaults to `intent_test` and refuses a database name not ending in `_test`. It never clears the demonstration `intent` database. Tests include a real loopback HTTP server; its payloads are synthetic, and do not constitute bank API integration.

## Configuration

The deployed browser entry is the unified gateway on port 5173: `/console/`, `/chat/{public_id}` and `/embed/{public_id}`. API port 8000 is internal to the container stack. `/docs` loads only local `/docs/app.js` and `/openapi.json`, compatible with `script-src 'self'`.

Revision `0002` adds application configuration, explicit access, renewable runtime sessions, icons and files without changing old immutable snapshots. Run `.venv/Scripts/python scripts/verify_upgrade.py` against a separately provisioned, initially empty `intent_upgrade_test` for a real 0001→0002 preservation/renewal check using the restricted application role. It preserves evidence and refuses a nonempty target.

All keys use prefix `INTENT_`; environment variables override an optional current-directory `.env`.

| Key | Meaning |
|---|---|
| `ENV` | `development`, `test`, or `production` |
| `DATABASE_URL` | PostgreSQL psycopg URL for the restricted runtime role |
| `MIGRATION_DATABASE_URL` | Separate PostgreSQL schema-owner URL |
| `ALLOW_DEV_AUTH` | Enables fixed local identities only; production requires false |
| `MODEL_MODE` | `fixture` or `openai`; production forbids fixture |
| `MODEL_URL` / `MODEL_NAME` / `MODEL_API_KEY` | Explicit internal OpenAI-compatible chat completions URL, pinned model name and optional secret |
| `MODEL_VERSION` | Trusted immutable model version; required for new production publication. Fixture releases explicitly record their implemented version |
| `RESOLVERS_JSON` | Versioned server-owned resolver references bound to workspace/type/endpoint/credential grants; see DOMAIN_CONTRACT.md |
| `WORKSPACE_TIMEZONES_JSON` | Workspace-to-IANA-timezone map, fixed in v2 releases; absent workspace uses UTC |
| `ALLOWED_HOSTS` | Comma-separated exact hosts for all outbound endpoints; no redirects, no public/metadata addresses, DNS address pinned for connect |
| `CONNECTOR_TARGETS_JSON` | Server-controlled workspace/connector/origin/port/path grants, described below |
| `CREDENTIALS_JSON` | Server-controlled credential references bound to workspace and exact execution/status endpoint templates; never accepted from imported definitions |
| `IDENTITY_INTROSPECTION_URL` / `IDENTITY_SERVICE_KEY` | Trusted internal identity introspection service and its credential |
| `INTEGRATIONS_JSON` | Strict server-approved integration references and versions, explicitly bound to workspaces, channels, origins, agents and users; see GOVERNANCE_CONTRACT.md |
| `MEMBERSHIP_GRANTS_JSON` | Optional authoritative per-workspace user/role ceilings; browser member edits can only narrow or assign these approved roles |
| `WORKSPACE_LIMITS_JSON` | Server ceilings for pending queue, routing and outbound execution/status-query concurrency; absent workspace defaults to 100/4/8 |
| `ALLOW_SHADOW_EVALUATION` | Default false; explicitly permits publisher-owned scoped-session A/B/C shadow requests with no business dispatch |
| `OBJECT_AUTHORIZATION_URL` / `OBJECT_AUTHORIZATION_KEY` | Trusted business object/data authorization service; required in production and checked before real connector requests |
| `CORS_ORIGINS` | Exact allowed frontend origins |
| `TOKEN_SECONDS` / `TICKET_SECONDS` / `CONFIRMATION_SECONDS` | Expiry windows; defaults are development values, not frozen bank policy |
| `WORKER_POLL_SECONDS` / `WORKER_LEASE_SECONDS` | Polling and lease recovery parameters |

Example **nonsecret** target authorization: `{"ws-dev":[{"origin":"http://127.0.0.1:9010","path_prefix":"/test-api","kinds":["http"]}]}`. The origin includes the exact port. Another workspace, protocol, port, connector kind or unrelated path is refused even when its hostname is globally allowed.

Credential entries have shape `{reference:{value,workspace_ids,connector_kind,endpoint,status_endpoint?}}`. `value` is a secret supplied only through the authorized environment/secret system. If reconciliation is configured, the credential's `status_endpoint` must exactly match the connector template too; a function cannot forward an execution credential to a different status collector. Default empty maps deny external business connectors. Model/identity credentials have separate trusted configuration, never a user-editable reference.

The identity adapter POSTs `{token: identity_token}` to the configured trusted introspection service. It requires the actual boolean `active:true`, a nonempty `sub`, existing effective database membership, and strict trusted `delegation` with `integration_id`, explicit `origins`, `agent_ids`, `workspace_ids`, and `user_ids`. The integration must be in the server-approved catalog; management operations check the target workspace and runtime operations check the pinned application policy. No browser body can manufacture these claims or choose a user/role. Actual bank SSO/SSO logout integration and identity provisioning remain environment-specific work.

## Implemented behavior and limits

- Agents have display drafts, published UI snapshots, stable public IDs and independent live chat/embed controls. Explicit access endpoints separate authorization from draft edits: `workspace` means workspace members; an empty `allowlist` denies everyone. Public identifiers are not credentials; runtime summaries omit management ACLs.
- Tokens rotate inside persistent user/agent/channel/host-bound sessions. Trusted reauthentication restores history and its pinned release without resetting questions or confirmation expiry. Explicit logout cannot be renewed. Eligible old management-token conversations migrate into same-user/same-agent claimable chat sessions.
- The gateway obtains complete per-application CSP from policy endpoints. Canonical origins are checked both on write and when reading legacy policies. Published and historical icons use controlled image assets. Disabled/unknown channels fail closed.
- Controlled inline connector files become bounded local artifacts with authenticated attachment downloads. The owning session and originating capability are checked; arbitrary URLs are never fetched. Bank file transport, retention and storage policy remain to be integrated.
- Prompt/compiler/connector dependencies resolve through explicit implementation registries; unavailable versions fail closed. This does not prove external model or workflow immutability in the bank environment.

- Function drafts use optimistic revision checks; published definitions and compiled projections are immutable. JSON/Pydantic structure and the finite JSON Schema subset compile to RDF/SHACL, with common positive/negative cases checked for Python/RDF agreement. Unsupported schema constructs are rejected explicitly.
- Model input includes all currently authorized release candidates. The production adapter uses configured internal JSON-mode completion; a fixed development double uses explicit aliases only in fixture mode. The double is not an accuracy claim. Missing or incompatible pinned model configuration fails closed.
- Runtime uses persistent run/revision/action/call IDs and transactional events as outbox. Worker claims with row locks, checks current identity/permission immediately before the dispatch boundary, and conservatively marks uncertain external writes UNKNOWN. Only registered status queries reconcile unknown calls; no blind write replay. Reconnect reads existing events.
- New messages invalidate pending questions/confirmations and unsent actions. Already dispatched results remain attached to their original run. Confirmation events display all actions they authorize. Host/session/parent-session revocation is checked on subsequent API/SSE reads and before worker dispatch.
- HTTP connectors support approved GET/POST, explicit auth headers, status/success mapping and optional idempotency/status queries. Dify uses inputs/response_mode/user, checks remote status, and refuses publishing unless the operator explicitly confirms an immutable remote binding. Its returned workflow run ID is the reconciliation identity. No automatic inference of a privately deployed Dify version is made.
- `hiagent` is an explicit configurable HTTP request/result/status mapping adapter. The deployed HiAgent protocol has not been verified; this implementation does not invent an API path or authentication scheme and does not claim a completed HiAgent integration.
- Text/Markdown, DOCX and text PDF parsing is local; scanned PDFs are rejected for readable text supplementation. Document parse work is offloaded from the async event loop, but a production parsing-job scheduler, resource quotas and comprehensive adversarial document handling remain to be hardened.
- Diagnostic evaluation persists case-level route outcomes and fixture labels. It is not the frozen S1/S2/S3 evaluator, production load benchmark or A01–A14 acceptance report.

Real 20-function onboarding, actual bank entity/data authorization integration, production SSO, approved model/hardware performance, full ontology/domain-package governance, genuine Dify/HiAgent deployment interoperation, bank file-service integration, and full frozen acceptance remain resource-dependent or additional implementation work. The local runtime implements finite trusted entity resolution, explicit references to authorized successful result rows, and the finite OpenAPI workflow below. Arbitrary OpenAPI/schema support and a production evaluation scheduler require further implementation. No local test result changes those acceptance statuses.

## Finite domain and binding update (INT-010)

[DOMAIN_CONTRACT.md](DOMAIN_CONTRACT.md) defines immutable workspace domain versions, explicit v2 function opt-in, finite inheritance/role/direction/cardinality constraints, registered quantity conversions, date/time normalization and three-valued trusted preconditions. Python and generated SHACL run shared positive/negative cases before publication. Unsupported constructs are rejected. Existing releases stay on their pinned v1 router/compiler; mixed releases dispatch each capability through its registered compiler.

Only server-configured resolver references can produce business IDs. Unique authorized matches bind automatically; multiple matches require a current opaque choice with distinguishable masked labels. Explicit historical references use authorized SUCCEEDED calls from the same conversation and revalidate source/object grants. New messages invalidate pending choices, and dispatch/reconciliation recheck current authorization. Context, question-history and event reads use bounded resolver budgets; no-event cursors do not query old candidates. The same-response question cache never persists across requests.

Revision 0003 adds immutable domain tables under RLS, trusted run/call binding metadata and nullable server validation evidence. `python scripts/verify_domain_upgrade.py` proves 0002→0003 preservation in an initially empty dedicated `intent_domain_upgrade_test`; it refuses existing evidence and performs no destructive reset. Historical scripts check their stated migration boundary first, then migrate to head for current-runtime compatibility checks. Their old-stage fixtures use frozen SQL instead of future ORM fields.

Real bank domain definitions, authoritative resolver contracts/labels, model semantic accuracy and twenty business functions are still unverified. The finite engine does not implement arbitrary OWL/rules, implicit cross-workspace domain import, plural/optional business-ID bindings, fuzzy historical-row guessing, or arbitrary relative-date expressions.

## Source and HTTP import update (INT-011)

[IMPORT_CONTRACT.md](IMPORT_CONTRACT.md) fixes the finite source workflow: immutable document revisions with actual originals and stable section references; bounded local OpenAPI 3.0 JSON/YAML parsing; explicit operation selection; current-revision source conflicts and attributed review reasons; successful and failed real connector probes before publication. Old text-only documents become revision 1 with no invented original. Sources and confirmed claims are pinned into immutable function and agent releases. Source review does not establish that a maintainer's business interpretation is correct.

HTTP connector `2` independently maps scalar path/query/header inputs and flat JSON fields, retaining simultaneous query/body data and required empty JSON objects. Final paths stay inside server target and credential-template grants. A reviewed `http_failure_statuses` list supports 400/404/409/422 JSON business failures; other errors and uncertain timeouts do not satisfy a negative probe. HTTP `1` keeps its original wire behavior; both versions reject Host/framing/proxy header overrides. No browser import expands server trust.

Revision `0004` adds immutable document_versions/openapi_imports under RLS and mutable draft source state. `python scripts/verify_import_upgrade.py` checks real 0003→0004 preservation in the initially empty, independently provisioned `intent_import_upgrade_test`; existing evidence is refused, never reset. It then checks current-runtime compatibility at head. The three historical scripts accept `INTENT_UPGRADE_TEST_DATABASE` for a newly provisioned database ending in `_test`; previously recorded upgrade evidence remains unchanged.

The standalone evaluation CLI preserves business scoring inputs and refuses known-secret contamination rather than changing scores through broad key redaction; wire traces retain controlled redaction. See [EVALUATION_CONTRACT.md](EVALUATION_CONTRACT.md). Archive creation validates the actual serialized dataset, observations and report and checks recomputed equality. This does not certify unknown-secret review, bank provenance or acceptance.

This package does not implement OpenAPI 3.1, remote refs, nested/polymorphic request bodies, arbitrary parameter serialization, PUT/PATCH/DELETE connectors, automatic business-semantic extraction, generic generated response schemas, a secret-management UI or production load acceptance. JSON/YAML parsing uses the exactly pinned PyYAML dependency from requirements/constraints. Test evidence and precise local limitations are in [VALIDATION.md](VALIDATION.md).

## Finite governance update (INT-013.1/.2)

[GOVERNANCE_CONTRACT.md](GOVERNANCE_CONTRACT.md) defines safe integration/resolver/credential reference catalogs, member roles bounded by trusted server ceilings, and application integration selection. New releases pin exact integration profiles; live revocation is independent and survives rollback. Session renewal keeps the same trusted integration. Old authorized history retains its pinned policy, while a new conversation must satisfy the current release. Old drafts require an explicit integration selection before republishing. Existing immutable snapshots are not rewritten.

PostgreSQL workspace transaction locks and persisted attempt leases enforce queue/routing/execution limits across users and Worker processes. Queue aggregation exposes only a permitted workspace count, preserving owner-only Run access. Timing records distinguish routing, execution and reconciliation attempts; skipped/unknown stages are null, and a connector duration exists only when a connector was actually invoked. Late expired claims cannot overwrite newer results. Administrators can read workspace timing without gaining access to conversation contents.

Revision `0005` adds role ceilings, identity bindings, queue timestamps and FORCE RLS governance/attempt tables. `python scripts/verify_governance_upgrade.py` verifies 0004→0005 in an initially empty `intent_governance_upgrade_test`, including unchanged old records/UNKNOWN state, controlled legacy renewal and FORCE RLS against runtime and owner roles. Database backup must use the separately authorized backup identity; the migration owner cannot bypass FORCE RLS for a complete dump. No API/Worker role is widened for backup.

Real bank identity catalogs/provisioning, server secret deployment and production concurrency/latency remain unverified. This update adds no arbitrary trust administration, distributed tracing service, cleanup scheduler or ablation implementation. Independent process-fault validation is a separate verification package.

## Versioned model projection and shadow comparison (INT-012.3)

New domain-enabled releases select `router-v3`. Its actual model payload contains the full authorized candidate set and one deduplicated domain projection, including pinned source definitions, concepts, inheritance closure, relations, units and conversions. Conflicts or an oversized complete payload are rejected instead of silently selecting/truncating candidates. Existing v1/v2 implementations and immutable releases retain their original payloads and behavior.

[ABLATION_CONTRACT.md](ABLATION_CONTRACT.md) defines the opt-in shadow endpoint and `scripts/evaluate_shadow.py`. A compares description/parameter routing, B uses complete v3 semantics, and C removes structured relation input and checks together. All arms share the authorized release/model/text/history reference. The API requires a scoped owner with effective publisher/admin authority, rechecks changes and limits each workspace to one comparison. It never creates a Run/Call/Event or invokes a business connector; trusted resolver reads still require current authorization. Fixture mode cannot supply real model-wire evidence.

The dedicated CLI sends no gold labels to the model API, retains failed/timeout cases in each denominator, and atomically creates a new non-overwriting `shadow.json` archive. Its limited exact route metric is separate from normal gold-exact-v1 E2E/S3 reports. This finite shadow facility does not establish model accuracy, causal/statistical significance, stable real resolver data or bank acceptance. No migration or ordinary-chat ablation switch is added.
