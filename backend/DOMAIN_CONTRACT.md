# INT-010 finite domain / semantic / binding contract

This is an additive local engineering contract. Existing immutable releases retain `router-v1` / `finite-json-v1`; their rows are never rewritten. New semantics require explicit `compiler_version:"finite-json-v2"` on a function. No bank catalog or model accuracy is implied by local fixtures.

## Domain registry (management identity, workspace role)

Prefix `/api/v2/management/workspaces/{ws}/domains`.

- `GET /` returns domain drafts: `{id,workspace_id,key,revision,latest_version,definition,validation}`.
- `POST /` body `{key,definition:DomainDefinition}` creates a draft; stable key is unique in its workspace.
- `PATCH /{id}/draft` body `{revision,definition}`; optimistic revision, stale 409.
- `POST /{id}/validate` checks finite structure, concept references, inheritance cycles, direction/cardinality and unit conversions; returns `{valid,revision,errors,checks}`.
- `POST /{id}/publish` body `{revision}` requires the validated current draft; creates immutable `{id,package_id,workspace_id,version,definition,compiled}`.
- `GET /{id}/versions` returns immutable versions. No edit/delete published version API.
- `GET /api/v2/management/workspaces/{ws}/resolvers` returns only currently granted `{reference,version,entity_type}` entries for management forms, without URLs or credentials.

`DomainDefinition` is `{name,description,source:{reference,owner,document_id?,revision?,section?},concepts:[{id,label,kind:"object"|"result",parents:[concept_id]}],relations:[{id,source_type,target_type}],units:[{id,dimension}],conversions:[{from_unit,to_unit,factor}]}`. Concept IDs are namespaced stable strings. Parent references must exist in this package; duplicate IDs, unknown types, cycles, invalid dimensions/conversions are rejected. Published function projections may combine explicitly referenced versions, but conflicting same-ID definitions are rejected. This package implements workspace-private domains; implicit cross-workspace/public import, OWL rules, arbitrary expressions and multiple principal types are unsupported and rejected.

## Opt-in function v2 additions

Existing Definition fields remain. New fields:

- `compiler_version:"finite-json-v1"|"finite-json-v2"` (default v1).
- `domain_refs:[{package_id,version}]` pins published versions in the same workspace.
- `participant_roles:[{role,type,required,min_count,max_count}]`, single principal concept type per role. `relations:[{name,source,target}]` uses a declared domain relation ID and the exact source/target **role names**. Function `object_type/result_type` must be registered concepts of the appropriate kind. `action` is the declared action that runtime proposals must match exactly.
- `argument_bindings:{parameter:{role,resolver_ref}}` binds a scalar string business ID through a trusted resolver. Its role must have minimum and maximum cardinality 1, with exactly one bound ID parameter per role. Optional or plural binding roles are rejected. Direct model or form assignment of that parameter is rejected. The model supplies only a mention or a history reference; choices use opaque option IDs.
- `parameter_semantics:{parameter:{kind:"quantity"|"date"|"datetime",unit?:unit_id,allow_relative?:boolean}}`. Quantities require an explicit `parameter_units` entry; only registered positive conversion factors are allowed. Dates are canonical YYYY-MM-DD; datetimes require an explicit offset. Relative dates support only `today` / `yesterday`, based on server run creation time and the published workspace timezone; other relative expressions require clarification/rejection, never guessing.
- `trusted_preconditions` is a finite expression: `{all:[...]}`, `{any:[...]}`, `{not:...}`, or `{fact,operator:"eq"|"ne"|"exists",value?}`. Available facts are server `identity.authenticated`, `entity.{role}.authorized`, and `history.{role}.authorized`; missing facts are UNKNOWN. TRUE/FALSE/UNKNOWN use three-valued logic; execution requires TRUE. Existing argument preconditions remain separate.
- `result_bindings:[{name,entity_type,collection_path,id_field,label_field,resolver_ref}]` declares trusted entity lists in successful connector results for future references. Paths are dotted object keys; arrays are selected only by an explicit reference index. No model-generated output is registered as a business result.

The v2 compiler emits one versioned JSON projection and generated RDF/SHACL. Python and SHACL share positive/negative conformance cases for types, role cardinality and relation direction, in addition to the existing input-schema cases. Unsupported constraints fail publication. A release pins domain definitions, resolver configuration identity, timezone/context policy and its router/compiler implementations. Mixed v1/v2 capability releases use router-v2, which still validates each capability by its pinned compiler; already-existing v1 releases keep router-v1.

## Model proposal v2 (not a public execution API)

Envelope remains `{decision,requests,options}` with strict decision/request consistency. Each READY request adds `{action,object_type,result_type,participants:[{role,type,mention?:string,history_ref?:{call_id,binding,index?:number}}],relations:[{name,source,target}],parameter_units:{parameter:unit_id},negated:boolean}` to `{capability_id,arguments}`. No resolver endpoint, credential, raw entity ID, permission claim or arbitrary plan is accepted. Unknown/negated/mismatched semantics cause zero business dispatch. All candidate IDs are checked against the current authorized release projection.

## Trusted resolver and history protocol

Resolvers are server configuration in `INTENT_RESOLVERS_JSON`, keyed by a versioned reference, e.g. `resolver.customer@1`. Each entry is `{version,workspace_ids,entity_type,endpoint,credential_ref?,timeout_seconds}`. Execution uses the existing per-workspace HTTP target and credential grants. Forms/models cannot create grants. Endpoint, declared type/version and credential reference are pinned in releases; secrets are not copied. A missing/changed configuration makes the pinned resolver unavailable.

Resolver POST body: `{user_id,workspace_id,entity_type,query,selected_id?}`. Response: `{candidates:[{id,label,type,authorized:boolean}]}`. Only `authorized:true`, matching types and unique nonempty IDs are accepted; labels must already be safe for the user. Zero authorized matches denies binding, one binds automatically, multiple require a user choice. A chosen ID is revalidated through the trusted resolver before dispatch; business object authorization still runs at the connector boundary.

Multiple authorized candidates must have distinct masked labels, including candidates assembled from historical rows. Duplicate labels are rejected: raw business IDs are never exposed merely to distinguish options, and the first match is never selected implicitly.

History includes only authorized SUCCEEDED calls in the same conversation, with registered result bindings and bounded rows. A history reference resolves server-side; omitted index with several rows becomes a choice. Cross-conversation/source-revoked/unknown/failed references are rejected. Explicit current-run provenance records the source call/version/row and resolver; new messages do not silently carry previous entity bindings. Existing revision cancellation invalidates old questions and bindings. A successful old result can still be explicitly referenced if it remains authorized.

## Frontend / runtime additions

- `QuestionOut` and QUESTION payload add `kind:"arguments"|"function_choice"|"entity_mention"|"entity_choice"|"history_choice"`. Existing `question_id`, `revision`, `fields`, `options` and answer endpoint remain. Missing kind on v1 means arguments/function choice as previously.
- `fields` may include `label` and `unit`. Entity mention uses a string field named `mention`; it is a display-name search, never a raw business ID. Choices remain `{id,label}`; IDs are opaque server choices.
- Answering entity/history choice uses existing `{revision,question_id,option_id}`; a mention uses `values:{mention:string}`. Backend binds the saved question to its request/role and rechecks authorization. It never treats an entity option as a capability ID.
- `/runs/{id}` adds `bindings` containing nonsecret provenance summaries only: parameter, source kind, role, resolver reference and optional source call/binding/index; business IDs and credentials are omitted.
- Debug-domain UI is not required for this backend package. Domain management APIs and their schemas are exported through OpenAPI for the next frontend package.

## Evidence boundary

Unit IDs use `[a-zA-Z][a-zA-Z0-9_.-]*` to prevent conversion-key collisions. Input and converted quantities must remain finite. Datetime input is the supported Python ISO timestamp subset containing `T` and an explicit offset, normalized to UTC ISO format. Trusted entity/history facts require a declared bound role.

Automatic history context scans at most 10 recent successful calls and checks at most 40 rows under a 20-second resolver budget. Already authorized results survive clipping. `run.bindings.context` records checked/included/truncated and unavailable-source flags. The claim transaction commits before context network I/O. Explicit collections over 40 rows require a row index. Required binding transport failures become explicit routing failures; optional historical context may omit unavailable rows. Dispatch and UNKNOWN reconciliation revalidate entity/history access with separate bounded budgets, then recheck live runtime authorization.

New releases include `model_profile.version`: the implemented fixture version is explicit; real versions come only from trusted `INTENT_MODEL_VERSION`, required for new production publication. Old snapshots without that field retain legacy profile comparison and remain unmodified. `INTENT_WORKSPACE_TIMEZONES_JSON` supplies workspace timezones (default UTC); changes affect only new releases. Migration 0003 additionally adds nullable `Run.validation_evidence`; only same-revision server-checked requests count as validation evidence. Runtime evaluation GET endpoints require the owning scoped session and current capability access, and read a REPEATABLE READ snapshot.

Migration `0003` adds scoped domain versions, run/call binding metadata and nullable server validation evidence. Published domain versions use the existing immutability trigger; app role has RLS. Acceptance includes cycle/unknown/direction/cardinality/unit/date/UNKNOWN negatives, hostile or stale entity choices, cross-user/space/history rejection, live revocation, preserved v1 behavior and full backend software regression. A configurable local resolver fixture tests the protocol; real bank resolvers, model quality, twenty functions and frozen S1/S2/S3 remain unverified.

V2 capabilities require an explicit JSON `negated:false`; missing/null or coercible strings/numbers cannot authorize execution. The optional schema representation exists only for mixed v1 compatibility. Current entity/history question labels are re-authorized on events/history/active-run/evaluation reads. A whole read uses at most 40 resolver requests and a 20-second deadline/transport-timeout budget; repeated question validation is deduplicated only within that response. Budget exhaustion fails closed. No cross-request authorization cache delays revocation.

INT-012.3 adds an independent `router-v3` for newly published domain-enabled releases: the complete deduplicated authorized domain projection is included in the actual model request. Existing `router-v1`/`router-v2` releases continue unchanged; finite-json-v2 compilation/binding semantics stay pinned. See [ABLATION_CONTRACT.md](ABLATION_CONTRACT.md) for the separate opt-in shadow A/B/C endpoint, which cannot execute business work or replace normal E2E/S3 verification.
