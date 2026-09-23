# Backend implementation evidence — 2026-09-22

Scope: local FastAPI/SQLAlchemy/PostgreSQL API and Worker with explicitly labelled development model/business fixtures. This is local engineering evidence, not M1 real-bank integration, M2 20-function acceptance or M3 quality acceptance.

## Executed checks

| Check | Actual environment / operation | Result |
|---|---|---|
| Python dependencies | Python 3.13.9, isolated `backend/.venv`; direct requirements and installed transitive versions recorded without checksum generation | Installed; first restricted-network attempt failed, official dependency installation succeeded with authorized escalation |
| Backend tests, finite domain update | `.venv/Scripts/python.exe -m pytest -q -x --tb=short` from `backend/`; real restricted `intent_app` on `intent_test` | **184 passed in 32.17s**; one existing TestClient/AnyIO warning; includes 102 platform tests and 82 evaluation scoring/runner tests |
| Domain upgrade | `.venv/Scripts/python.exe scripts/verify_domain_upgrade.py`; initially empty `intent_domain_upgrade_test`, real Alembic 0002 then 0003 | Passed; old release/conversation/run/call values unchanged, legacy v1 dependency still usable, UNKNOWN/revision/operation ID retained, new domain RLS/immutability enforced |
| OpenAPI export | Current application `app.openapi()` written to `backend/openapi.json` | 56 paths exported; source syntax compilation passed |
| Backend tests, application update | `.venv/Scripts/python.exe -m pytest -q --tb=short` from `backend/`; separate PostgreSQL `intent_test` with restricted `intent_app` runtime role and separate owner migration identity | **58 passed in 10.53s**; one upstream TestClient/AnyIO deprecation warning |
| Application upgrade | `.venv/Scripts/python.exe scripts/verify_upgrade.py` from `backend/`; initially empty dedicated `intent_upgrade_test`, real Alembic 0001 then 0002 | Passed; original snapshots and ACL preserved; application-role checks cover active/expired sessions, parent/child/conversation revocation and legacy management history claim |
| Static syntax | `.venv/Scripts/python -m compileall -q src` from `backend/` | Passed |
| Fresh versioned migration | `python -m intent_platform.migrate` targeting empty `intent_migration_test` using owner role | Frozen Alembic 0001, 77 DDL statements applied; app connection reports `intent_app` and schema version `0001` |
| Installed package migration resources | Built local `intent-platform` wheel, installed into `.venv`, cleared PYTHONPATH and invoked installed `intent_platform.migrate` from project root | Package contains `migrations/versions/0001_schema.json` and scripts; no source working directory dependency |
| Initial local demonstration | Development login → two named fixtures → function/agent publishing → conversation → message → independent Worker → result/events | SUCCEEDED, one persisted call and five event envelopes; synthetic business/model only |

Runtime and migration test URLs must name the same separate database ending in `_test`, on the same host/port. Integration tests do not clear or reset the demonstration database. The tests verify the application role is neither superuser nor RLS-bypass-capable; no SQLite is used.

## Test coverage

- Python/RDF agreement for integer versus integral float, null versus absent, strict booleans, scalar/array distinction, duplicate array items and unsupported schema rejection.
- Workspace-scoped target grants including ports/path boundaries; credentials bound to workspace, connector and execution/status targets; unknown versus known-failed remote statuses; false object authorization stops the business request.
- Real PostgreSQL RLS visibility and immutable-release SQL triggers; cross-space rejection, concurrent message deduplication, single action/call slot and monotonically ordered durable outbox/events.
- Parameter answers and stale revisions, new-message invalidation including claimed-but-unsent calls, complete multi-action confirmation, independent read requests continuing after one fails, fixed conversation release and draft concurrency.
- Atomic one-time embed ticket consumption under concurrent requests, mismatched binding rejection, runtime/management scope separation, user isolation, parent logout, host revocation and queued execution eligibility.
- Three recovery windows represented by persistent pending/dispatch/unknown/event states; expired reconciliation leases repeat only status queries; no blind replay of unknown writes; event recovery does not execute a new call.
- A threaded loopback HTTP server receives actual requests. Read responses, explicit failure and post-confirmation write timeout are exercised over real sockets. The timeout becomes UNKNOWN and repeated Worker iterations do not repeat the write. Payloads and the remote business system remain synthetic.
- Malformed model proposals become explicit failures rather than lease-retry loops. Local text/DOCX parsers and the generated core OpenAPI response schemas are checked.

Independent code review findings were fixed and added to the relevant regression coverage: credential/target scope, status-query credential forwarding, pending/failure status classification, integral numeric consistency, malformed proposals, unsent cancellation, origin revocation, reconciliation leases, complete confirmation and independent-action aggregation.

## Not established by this evidence

The 30 application-update cases additionally cover strict decision/request consistency; explicit live ACLs independent of draft edits; empty allowlist denial; UI snapshots and channel gates; canonical-origin/CSP injection rejection including old snapshots; chat/embed renewal and history; cross-session rejection; parent logout and real PostgreSQL concurrent creation/renewal versus logout; confirmation expiry; paused UNKNOWN reconciliation; unavailable dependency rejection; historic icons; authenticated attachment downloads; and local CSP-compatible documentation.

Already dispatched operations with expired worker leases remain UNKNOWN even if session/function permissions or confirmation expire. Management credentials cannot bypass a scoped runtime conversation. Runtime summaries omit ACLs and draft configuration. File tests use controlled local fixtures, not a bank file service.

The upgrade script refuses an existing schema rather than deleting evidence. Repeat a complete upgrade only after provisioning a fresh dedicated `intent_upgrade_test` through the authorized infrastructure workflow. Revision 0002 never rewrites immutable releases.

No real bank APIs, approved production model/SSO/object authorization service, 20-function catalog, full OpenAPI mapping workflow, real bank business entity resolver, complete domain-package governance, frozen S1/S2/S3 evaluator, real Dify/HiAgent deployment, production load curve, browser-host acceptance, or full bank offline/upgrade/recovery acceptance was verified by this backend test run. The parent task owns separate frontend and deployment evidence. Real-resource and remaining implementation gaps are described in README.md and must remain visible in project task records.

The candidate backend code is local only; this task made no Git commit, remote push or production deployment.

## INT-010 finite implementation and verification

The 44 new platform cases cover finite inheritance/cycles/unknown concepts, action/result/role/relation direction/cardinality, strict explicit execution intent, generated Python/SHACL agreement, unit conversion/collision/overflow, canonical dates/timezones and three-valued conditions. Published optional/plural business-ID bindings and unknown trusted role facts are rejected.

Real loopback resolver requests plus PostgreSQL exercise unique entity binding, mention and multiple-choice answers, opaque-ID validation, duplicate-label rejection, same-conversation successful result references, explicit history row choice, foreign/revoked sources, direct model ID rejection, changed resolver versions, transport timeout/HTTP/protocol errors, and authorization again at dispatch/reconciliation. Result payloads and identities are synthetic. Historical context checks a bounded prefix and retains already authorized items when clipped. Question events/history/current-run/evaluation reads revalidate current entity grants; no-event cursors perform no resolver calls. Nested budgets cannot reset outer limits.

Mixed v1/v2 publication preserves existing v1 conversations. New model versions are explicit, old snapshots remain unchanged, and evidence only marks same-revision server validation as known. Evaluation profile capabilities are projected to the current authorized subset; a zero-capability OUT_OF_SCOPE run still supplies honest S2 evidence while revoked historical action evidence is denied.

During regression, retained UNKNOWN calls without a query endpoint exposed a reconciliation scan starvation boundary; scanning now excludes unqueryable operations and delays missing-lookup entries without replay. Independent action slots are claimed in stable action order. A test initially modified the shared test-only seeded agent; it was corrected to create its own agent, and only that test fixture's previous release pointer was restored, retaining all database/release evidence. No demonstration data was touched.

Current scope excludes real model semantic accuracy, real bank resolver/object permissions, production concurrency/performance, arbitrary rules/OWL, public cross-workspace domain distribution, plural/optional ID bindings, arbitrary relative dates and full OpenAPI mapping. The parent task owns frontend/browser and deployment acceptance.

## INT-011 source and connector update — actual final checks

The entries above retain their earlier execution scope and results. The finite OpenAPI/source workflow is now implemented; arbitrary OpenAPI structures and real bank acceptance remain excluded.

| Check | Exact command from backend | Actual result |
|---|---|---|
| Public connector/source/scoring-input regression | `.venv/Scripts/python.exe -m pytest -q -x --tb=short` | **247 passed, 1 warning in 48.16s**, restricted `intent_app` on `intent_test` |
| New parser/source/archive cases | `.venv/Scripts/python.exe -m pytest -q tests/test_openapi_parser.py tests/test_source_import_runtime.py tests/test_evaluation_archive_integrity.py -x --tb=short` | **52 passed, 1 warning in 10.12s** |
| Final wire-redaction flag adjustment | `.venv/Scripts/python.exe -m pytest -q tests/test_evaluation_archive_integrity.py tests/test_evaluation_runner.py -x --tb=short` | **57 passed, 1 warning in 8.07s**; includes actual Runtime API/PostgreSQL runner cases |
| Independent source upgrade | `.venv/Scripts/python.exe scripts/verify_import_upgrade.py` | **Passed** in initially empty `intent_import_upgrade_test`; owner migrates 0003→0004, restricted app verifies preservation/RLS/immutability |
| Public schema and syntax | application `app.openapi()` exported; `.venv/Scripts/python.exe -m compileall -q src scripts` | **65 paths / 69 operations / 82 schemas**; syntax passed |

The warning is the existing Starlette TestClient/AnyIO deprecation. Source/runtime tests use actual loopback sockets and synthetic business payloads: all four explicitly supported HTTP business failure statuses validate, 401/403/500 and a real response timeout do not; normal successful parameters are probed first. Required empty JSON arrives as `{}`, while an absent body remains absent. Mixed query/header/path/body values survive a published function→agent→confirmation→Worker call. Configured credential/idempotency and transport headers cannot override Host/framing/proxy routing, for either HTTP version. Reference expansion counts repeated UTF-8 text/key bytes and final report bytes as well as nodes. Malformed DOCX revision upload returns 422 with no new version.

Current-revision conflicts, exact excerpts, complete citation readback, attributed reasons, stale validation/publish rejection, immutable source versions, actual retained originals, cross-space denial and domain references to exact new source revisions have regression coverage. Successful API parsing never invokes the endpoint. New sources do not grant targets or credentials.

The upgrade fixture retains old document/function/function-version/release/conversation/run/call values, allowing only newly added column defaults. A legacy original remains null, original text and v1 release dependencies are preserved, and UNKNOWN/revision/operation ID remain unchanged. New document versions and import records enforce RLS and immutable UPDATE/DELETE rejection with the restricted application role. The demonstration database and all earlier archive evidence were untouched. Historical upgrade-script function accesses were narrowed to their frozen schema columns; their earlier executed outcomes are retained above, not represented as new reruns.

Four archive integrity regressions prove `token_count:1` versus `token_count:2` remains a scored mismatch, the legitimate capability ID `credential_ref` retains an integer function version, known token contamination refuses an archive without a valid trial file, and an inconsistent supplied report is rejected. Both before serialization and after parsing the actual serialized bytes, authoritative dataset/observations/report models validate and the report matches recomputation. Bearer/Basic wire values remain redacted. Unknown secrets in arbitrary business JSON still require caller review; this is not an automatic data-sanitization certification.

## INT-013.1/.2 governance — actual final checks

The prior entries preserve their original scope. These checks cover the finite governance product package, before the separately owned process-fault harness is added.

| Check | Exact command from backend | Actual result |
|---|---|---|
| Governance regression | `.venv/Scripts/python.exe -m pytest -q tests/test_governance.py -x --tb=short` | **20 passed, 1 warning in 10.71s**, restricted `intent_app` on `intent_test` |
| Complete backend regression | `.venv/Scripts/python.exe -m pytest -q -x --tb=short` | **267 passed, 1 warning in 116.13s**, restricted `intent_app` on `intent_test` |
| Independent governance upgrade | `.venv/Scripts/python.exe scripts/verify_governance_upgrade.py` | **Passed** in initially empty `intent_governance_upgrade_test`, real 0004→0005 |
| Historical application boundary plus current runtime | Set `INTENT_UPGRADE_TEST_DATABASE=intent_upgrade_v5_test`; `.venv/Scripts/python.exe scripts/verify_upgrade.py` | **Passed**: 0001→0002 old-field preservation, then head runtime compatibility |
| Historical domain boundary plus current runtime | Set `INTENT_UPGRADE_TEST_DATABASE=intent_domain_upgrade_v5_test`; `.venv/Scripts/python.exe scripts/verify_domain_upgrade.py` | **Passed**: 0002→0003 old-field preservation, then head runtime compatibility |
| Historical source boundary plus current runtime | Set `INTENT_UPGRADE_TEST_DATABASE=intent_import_upgrade_v5_test`; `.venv/Scripts/python.exe scripts/verify_import_upgrade.py` | **Passed**: 0003→0004 old-field preservation, then head runtime compatibility |
| Public schema and syntax | `app.openapi()` exported; `.venv/Scripts/python.exe -m compileall -q src`; explicit `py_compile` of the four upgrade scripts | **72 paths / 77 operations / 93 schemas**; syntax passed |

The warning remains the upstream Starlette TestClient/AnyIO deprecation. Tests use real PostgreSQL connections and a real loopback introspection server with explicitly synthetic identities. Negative identity cases reject nonboolean active flags, malformed/empty subject or delegation, wildcard/unapproved scopes and origin injection. Target-workspace checks prevent an identity delegated only to workspace A from using the same user's membership in B. Catalog projections omit secrets and transport details. Role ceilings, self-edit denial, stale revisions, trusted roster assignment and disabled members have direct regression coverage.

App/session cases cover pinned integration profiles, same-integration renewal, cross-integration denial, one-time embed ticket revocation, old history after a changed published integration, current-release new-conversation denial, live revocation through rollback and queued dispatch refusal. Runtime session identity policies preserve historical access without allowing an old identity to enter a new differently authorized release.

Two users concurrently submit to a workspace with queue capacity one: exactly one admission succeeds, the other returns 429 without a new run, and an idempotent repeat retains the original run. Separate Worker connections share routing slots. The aggregate SQL function does not widen owner-only Run RLS; workspace admins read only cross-owner timing projections. Lease fencing rejects an expired reconciliation attempt's late response after a newer claim. A revoked post-claim identity makes zero connector calls and leaves connector_ms null. Unobserved queue/unfinished total durations remain null.

The new upgrade script compares old record fields and immutable snapshots before/after 0005; it checks active legacy renewal, revoked legacy denial, unchanged UNKNOWN/operation/revision and new FORCE RLS for both app and owner roles. Each historical rerun used its own newly provisioned database, retained original stage evidence, then advanced to head for current-runtime checks. None of these scripts deleted prior upgrade records or touched the demonstration database.

During development, the real identity request exposed an empty optional service credential producing an invalid `Bearer ` header; the adapter now omits that header when no service credential is configured. The initial complete regression also found a test republishing a shared pre-governance draft without selecting an integration. That test now creates its own app and retains its pinned-release/stale-revision assertions; the new-publication selection gate was not relaxed. The final full regression above ran after these corrections. Product code remained frozen while historical helper compatibility and documentation were finished.

This evidence does not prove bank SSO/delegation correctness, real provisioning/secret distribution, approved model quality, production load/fairness/latency or physical Worker crash windows. The separately coordinated process-fault package and parent-owned gateway/browser/backup/restore checks must report their own results. No arbitrary trust editor, production capacity claim or shadow ablation is included in INT-013.1/.2.

## INT-012.3 model projection and finite shadow comparison

| Check | Exact command from backend | Actual result |
|---|---|---|
| New shadow, CLI and model-wire cases | `.venv/Scripts/python.exe -m pytest -q tests/test_shadow_routing.py tests/test_shadow_cli.py tests/test_shadow_api.py -x --tb=short` | **21 passed, 1 warning in 9.39s** |
| Final capability-set metric correction | Same three shadow test files and command | **29 passed, 1 warning in 10.63s**, including eight additional collection/ambiguity/missing-evidence cases |
| Complete backend software regression | `.venv/Scripts/python.exe -m pytest -q -x --tb=short` | **288 passed, 3 skipped, 1 warning in 138.67s** |
| Final complete regression after metric correction | `.venv/Scripts/python.exe -m pytest -q -x --tb=short` | **296 passed, 3 skipped, 1 warning in 158.44s** |
| Source CLI launch | `.venv/Scripts/python.exe scripts/evaluate_shadow.py --help` | Passed; source-path wrapper works independently of an older installed wheel |
| Public schema/syntax | `app.openapi()` export; `.venv/Scripts/python.exe -m compileall -q src`; `py_compile scripts/evaluate_shadow.py` | **73 paths / 78 operations / 96 schemas**; passed |

The three skips are the separately owned physical-process fault scenarios, which require their explicit launcher and dedicated database; skipped cases are not counted as passes. Their actual executions and any final combined-image rerun belong to the separate process evidence. The remaining warning is the existing Starlette TestClient/AnyIO deprecation.

`test_shadow_and_normal_v3_send_real_complete_model_payload` uses a real loopback model HTTP service and the restricted PostgreSQL application role. Each A/B/C wire request exactly matches its returned evidence; B contains the complete authorized candidates and deduplicated domain versions, inheritance and conversions. The shadow sequence creates no Run, Event or Call. The test then sends an ordinary user message through the actual Worker, deterministic entity/semantic checks, ledger and result path to SUCCEEDED; the normal fourth model request is equal to B's second request. These are synthetic protocol payloads, not a bank model quality claim.

`test_old_v2_snapshot_and_conversation_continue_after_v3_publication` preserves an explicitly labelled old-v2 fixture snapshot, publishes a separate v3 release, runs the old conversation successfully and observes its profile still pinned to v2. Pure wire tests also verify v1/v2 retain their previous payload forms. Existing domain/runtime tests now create v3 releases and retain their full authorization, confirmation, binding, event and UNKNOWN assertions.

Projection cases cover full deduplication, conflicting IDs/package versions, oversized complete-request rejection, structured relation removal in both card and nested domain input, unchanged units/inheritance, and A's description/parameter-only cards. C removes relation checking while B rejects reversed relations; all three reject untrusted business IDs. Fixture mode has no claimed wire request/response or model duration. Timeouts/malformed responses retain actual attempted timing and unknown validation.

PostgreSQL shadow tests use independent runtime sessions to prove one workspace comparison slot, zero ledger mutation, scoped owner/publisher authorization, server opt-in, gold-field rejection, and fresh function/session revocation between model calls. No subsequent model arm or old evidence is returned after revocation. CLI tests prove only text/release ID are sent, each failure remains in all three denominators, incomplete/mismatched/executed evidence cannot become success, known-secret scoring input refuses an archive, wire secrets remain redacted, and existing archive directories are never overwritten. Sealed execution is opt-in and errors do not echo credential values.

The finite comparison does not freeze external resolver data, infer causal/statistical significance, replace normal B E2E/S3, certify production latency or establish real bank acceptance. Its shadow route metric is deliberately separate from the unchanged gold-exact-v1 evaluator. This package added no schema migration and did not alter the demonstration database or older immutable releases.

Independent review found the new limited shadow metric initially treated repeated independent uses of one capability as duplicate set entries and omitted function-ambiguity alternatives. The correction uses a unique capability set, stable function-choice IDs, and selected capability IDs for entity/history ambiguity. Gold rejects duplicate IDs; missing selections or candidate information become errors, never correct empty sets. Eight direct regressions cover correct/incorrect function alternatives, repeated requests, both entity/history kinds and missing evidence. The ordinary gold-exact-v1 scorer was not changed.
