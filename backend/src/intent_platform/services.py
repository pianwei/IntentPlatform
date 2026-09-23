from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import select, func
from .db import now, uid
from .models import Function, FunctionVersion, Agent, Release, Conversation, Run, Call, Event
from .config import settings
from .auth import authorize, manager, token_valid, audit
from .schemas import Definition, RuntimeUI
from .policies import permitted, dependency_error, effective_channels
from .ontology import compile_definition, input_errors
from .ports import connector_checks, execute_connector
from .output_contract import check_output_contract, project_output, OutputContractError


def function_view(f):
    return {"id": f.id, "workspace_id": f.workspace_id, "name": f.definition["name"], "description": f.definition["description"],
            "revision": f.revision, "status": "disabled" if f.disabled else f.status, "definition": f.definition,
            "latest_version": f.latest_version, "is_fixture": f.definition["connector"]["kind"] == "fixture", "validation": f.validation,
            "access": {"access_mode": f.access_mode, "allowed_users": f.allowed_users}}


def normalized_ui(ui, name="智能体", suggested_questions=None):
    values = {k: v for k, v in (ui or {}).items() if k in RuntimeUI.model_fields}
    values.setdefault("title", (ui or {}).get("name", name))
    values.setdefault("suggested_questions", suggested_questions or [])
    return RuntimeUI.model_validate(values).model_dump()


def agent_view(a, db=None):
    published = db.get(Release, a.current_release_id) if db and a.current_release_id else None
    return {"id": a.id, "workspace_id": a.workspace_id, "name": a.name, "capability_ids": a.capability_ids,
            "current_release_id": a.current_release_id, "allowed_origins": a.allowed_origins, "suggested_questions": a.suggested_questions,
            "revision": a.revision, "fixture_mode": settings().fixture_mode,
            "public_id": a.public_id, "runtime_ui": normalized_ui(a.runtime_ui, a.name, a.suggested_questions),
            "channels": a.channels, "live_channels": a.live_channels,
            "published_channels": published.snapshot.get("channels", {"chat": True, "embed": True}) if published else None,
            "published_origins": published.snapshot.get("allowed_origins", []) if published else [], "revoked_origins": a.revoked_origins,
            "integration_refs": a.integration_refs, "published_integration_refs": list(published.snapshot["identity_policy"]["profiles"]) if published and published.snapshot.get("identity_policy") else None,
            "revoked_integration_refs": a.revoked_integration_refs,
            "access": {"access_mode": a.access_mode, "allowed_users": a.allowed_users}}


def release_view(r):
    return {"agent_id": r.agent_id, "release_id": r.id, "version": r.version,
            "capabilities": [{"capability_id": c["capability_id"], "version": c["version"]} for c in r.snapshot["capabilities"]],
            "fixture_mode": r.snapshot["fixture_mode"]}


def get_resource(db, cls, resource_id, workspace_id=None, lock=False):
    q = select(cls).where(cls.id == resource_id)
    if workspace_id:
        q = q.where(cls.workspace_id == workspace_id)
    if lock:
        q = q.with_for_update()
    resource = db.scalar(q)
    if not resource:
        raise HTTPException(404, "Resource not found")
    return resource


def allowed_definition(definition, user_id):
    return not definition.get("allowed_users") or user_id in definition["allowed_users"]


def visible_candidates(db, release, token):
    authorize(db, token, release.workspace_id)
    result = []
    for item in release.snapshot["capabilities"]:
        current = db.get(Function, item["capability_id"])
        if current and not current.disabled and permitted(current, token.user_id):
            result.append(item)
    return result


def compile_for_workspace(db, definition, workspace_id):
    if definition.get("compiler_version", "finite-json-v1") == "finite-json-v1":
        return compile_definition(definition)
    from .domains import project_domains
    from .resolvers import profiles_for_definition
    compiled = compile_definition(definition, projection=project_domains(db, workspace_id, definition["domain_refs"]))
    compiled["resolver_profiles"] = profiles_for_definition(compiled["semantic_definition"], workspace_id)
    return compiled


def validate_function(db, f, token, body):
    errors, checks, probe = [], {}, None
    try:
        from .source_review import assert_ready
        assert_ready(db, f)
        compiled = compile_for_workspace(db, f.definition, f.workspace_id)
        checks = {"schema": True, "python_rdf_consistent": True, "conformance_cases": compiled["conformance_cases"]}
        connector_checks(f.definition, f.workspace_id)
        checks["connector_configuration"] = True
        protected_output = f.definition["connector"]["kind"] != "fixture"
        if protected_output:
            check_output_contract(f.definition)
            checks["output_contract"] = True
        for sample in f.definition.get("positive_examples", []):
            if compiled["compiler_version"] == "finite-json-v2" and "proposal" in sample:
                from .semantic_v2 import semantic_pair
                if semantic_pair(compiled["semantic_definition"], sample["proposal"], compiled["domain_projection"]) != (True, True):
                    raise ValueError("Positive semantic sample violates capability semantics")
            if "arguments" in sample and input_errors(f.definition["input_schema"], sample["arguments"]):
                raise ValueError("Positive argument sample violates input schema")
        for sample in f.definition.get("negative_examples", []):
            if compiled["compiler_version"] == "finite-json-v2" and "proposal" in sample:
                from .semantic_v2 import semantic_pair
                if semantic_pair(compiled["semantic_definition"], sample["proposal"], compiled["domain_projection"]) != (False, False):
                    raise ValueError("Negative semantic sample unexpectedly passes capability semantics")
                continue
            if "arguments" in sample and not input_errors(f.definition["input_schema"], sample["arguments"]):
                raise ValueError("Negative argument sample unexpectedly passes input schema")
        if body.probe:
            if f.definition["effect"] == "WRITE" and not body.allow_effects:
                raise HTTPException(409, "Write probe requires explicit allow_effects in an authorized test environment")
            argument_errors = input_errors(f.definition["input_schema"], body.arguments)
            if argument_errors:
                raise ValueError("; ".join(argument_errors))
            raw_probe = execute_connector(f.definition, body.arguments, uid("probe"), token.user_id, f.workspace_id)
            probe = raw_probe if not protected_output else {"status": raw_probe["status"], "operation_id": raw_probe.get("operation_id"),
                                                               "fixture_mode": False, "data": {}}
            if protected_output and raw_probe["status"] == "SUCCEEDED":
                try:
                    projected, inline_files = project_output(f.definition, raw_probe.get("data"))
                    probe["data"] = projected
                    if inline_files is not None:
                        from .result_files import inspect_inline_files
                        probe["files"] = inspect_inline_files(inline_files)
                except (OutputContractError, ValueError):
                    errors.append("Successful probe result does not match the approved output contract")
                    probe["data"] = {}
                    probe["result_unavailable"] = True
            if probe["status"] != "SUCCEEDED":
                errors.append("Probe did not establish successful business completion")
            if body.failure_arguments is not None and probe["status"] == "SUCCEEDED":
                failure_errors = input_errors(f.definition["input_schema"], body.failure_arguments)
                if failure_errors: raise ValueError("Failure probe arguments must satisfy the declared input schema")
                failure_probe = execute_connector(f.definition, body.failure_arguments, uid("probe_failure"), token.user_id, f.workspace_id)
                checks["failure_probe"] = {"status": failure_probe["status"], "expected_status": "FAILED", "http_status_code": failure_probe.get("http_status_code")}
                if failure_probe["status"] != "FAILED": errors.append("Failure probe did not establish a definite expected business failure")
        if (f.source_state or {}).get("require_failure_probe") and (not body.probe or body.failure_arguments is None):
            errors.append("Imported operations require explicit successful and failed connector probes on this revision")
        if f.definition["connector"]["kind"] != "fixture" and (not probe or probe["status"] != "SUCCEEDED"):
            errors.append("A successful real connector probe is required before publishing")
    except HTTPException:
        raise
    except Exception as exc:
        errors.append(str(exc))
    result = {"valid": not errors, "revision": f.revision, "errors": errors, "checks": checks, "probe": probe}
    f.validation = result
    f.validated_revision = f.revision if not errors else None
    f.status = "validated" if not errors else "draft"
    audit(db, token.user_id, f.workspace_id, "function.validate", f.id, {"valid": not errors, "fixture": f.definition["connector"]["kind"] == "fixture"})
    return result


def publish_function(db, f, token):
    if f.validated_revision != f.revision or not f.validation or not f.validation["valid"]:
        raise HTTPException(409, "Validate the current draft revision first")
    compiled = compile_for_workspace(db, f.definition, f.workspace_id)
    from .source_review import assert_ready
    try:
        manifest = assert_ready(db, f)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if manifest["documents"] or f.source_state:
        compiled["source_manifest"] = manifest
    connector_checks(f.definition, f.workspace_id)
    if f.definition["connector"]["kind"] != "fixture":
        try:
            check_output_contract(f.definition)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
    f.latest_version += 1
    db.add(FunctionVersion(workspace_id=f.workspace_id, function_id=f.id, version=f.latest_version,
                           definition=compiled.get("semantic_definition", f.definition), compiled=compiled))
    f.status = "published"; f.disabled = False
    audit(db, token.user_id, f.workspace_id, "function.publish", f.id, {"version": f.latest_version})
    db.flush()
    return function_view(f)


def publish_agent(db, agent, token, expected_revision=None):
    if expected_revision is not None and expected_revision != agent.revision:
        raise HTTPException(409, "Agent draft revision changed")
    from .trust import selected_profiles
    try:
        identity_profiles = selected_profiles(agent)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    capabilities = []
    from .origins import exact_origin
    try:
        for origin in agent.allowed_origins:
            exact_origin(origin)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    for fid in agent.capability_ids:
        f = get_resource(db, Function, fid, agent.workspace_id, lock=True)
        if not f.latest_version or f.disabled or not permitted(f, token.user_id):
            raise HTTPException(409, "Every capability must be published, enabled and authorized")
        version = db.scalar(select(FunctionVersion).where(FunctionVersion.function_id == fid, FunctionVersion.version == f.latest_version))
        connector_checks(version.definition, agent.workspace_id)
        capabilities.append({"capability_id": fid, "version": version.version, "definition": version.definition, "compiled": version.compiled})
    cfg = settings()
    if cfg.env == "production" and not cfg.model_version:
        raise HTTPException(409, "A trusted immutable model version is required for production publication")
    from .models import Asset, Document
    ui = normalized_ui(agent.runtime_ui, agent.name, agent.suggested_questions)
    if ui["icon"]["kind"] == "asset":
        get_resource(db, Asset, ui["icon"]["value"], agent.workspace_id)
    for c in capabilities:
        for ref in c["definition"].get("document_refs", []):
            if not isinstance(ref.get("revision"), int) or not ref.get("document_id"):
                raise HTTPException(409, "Referenced document revision is unavailable")
            from .document_versions import get_version
            get_version(db, agent.workspace_id, ref["document_id"], ref["revision"])
        for ref in c["compiled"].get("source_manifest", {}).get("documents", []):
            from .document_versions import get_version
            get_version(db, agent.workspace_id, ref["document_id"], ref["revision"])
    snapshot = {"schema_version": "1", "capabilities": capabilities, "dependency_profile": "platform-v2",
                "identity_policy": {"version": "integration-v1", "profiles": identity_profiles},
                "model_profile": {"mode": cfg.model_mode, "name": cfg.model_name, "version": "development-deterministic-v1" if cfg.model_mode == "fixture" else cfg.model_version, "url": cfg.model_url, "temperature": 0},
                "prompt_policy_version": "router-v1", "ontology_compiler_version": "finite-json-v1", "ui_schema_version": "1",
                "connector_versions": {c["capability_id"]: c["definition"]["connector"]["version"] for c in capabilities},
                "document_revisions": [ref for c in capabilities for ref in c["definition"].get("document_refs", [])],
                "source_manifests": {c["capability_id"]: c["compiled"]["source_manifest"] for c in capabilities if "source_manifest" in c["compiled"]},
                "runtime_ui": ui, "channels": agent.channels,
                "allowed_origins": agent.allowed_origins, "fixture_mode": cfg.fixture_mode or any(c["definition"]["connector"]["kind"] == "fixture" for c in capabilities)}
    if any(c["compiled"]["compiler_version"] == "finite-json-v2" for c in capabilities):
        import json
        from zoneinfo import ZoneInfo
        timezone_name = json.loads(cfg.workspace_timezones_json).get(agent.workspace_id, "UTC")
        try:
            ZoneInfo(timezone_name)
        except (ValueError, KeyError, TypeError):
            raise HTTPException(409, "Workspace timezone is unavailable")
        profiles = {}
        domains = {}
        for c in capabilities:
            for ref, profile in c["compiled"].get("resolver_profiles", {}).items():
                if ref in profiles and profiles[ref] != profile:
                    raise HTTPException(409, "Selected capabilities pin conflicting resolver versions")
                profiles[ref] = profile
            for domain in c["compiled"].get("domain_projection", {}).get("versions", []):
                if domain["package_id"] in domains and domains[domain["package_id"]] != domain:
                    raise HTTPException(409, "Selected capabilities pin conflicting domain package versions")
                domains[domain["package_id"]] = domain
        snapshot.update({"dependency_profile": "platform-v3", "workspace_id": agent.workspace_id,
                         "prompt_policy_version": "router-v3", "ontology_compiler_version": "finite-json-v2",
                         "compiler_versions": {c["capability_id"]: c["compiled"]["compiler_version"] for c in capabilities},
                         "resolver_profiles": profiles, "domain_packages": list(domains.values()),
                         "context_policy": {"version": "context-v1", "timezone": timezone_name, "max_calls": 10, "max_items": 40}})
        from .routing_v3 import model_body
        try:
            model_body("", capabilities, [])
        except ValueError as exc:
            raise HTTPException(409, str(exc))
    error = dependency_error(snapshot)
    if error:
        raise HTTPException(409, error)
    if not agent.current_release_id:
        agent.live_channels = dict(agent.channels)
    version = (db.scalar(select(func.max(Release.version)).where(Release.agent_id == agent.id)) or 0) + 1
    release = Release(workspace_id=agent.workspace_id, agent_id=agent.id, version=version, snapshot=snapshot)
    db.add(release); db.flush(); agent.current_release_id = release.id
    audit(db, token.user_id, agent.workspace_id, "agent.publish", agent.id, {"release_id": release.id, "version": version})
    return release_view(release)


def runtime_agent(db, agent_id, token):
    if token.scope == "runtime" and token.agent_id != agent_id:
        raise HTTPException(403, "Runtime session belongs to a different agent")
    a = get_resource(db, Agent, agent_id)
    authorize(db, token, a.workspace_id)
    if not permitted(a, token.user_id):
        raise HTTPException(403, "Agent authorization is unavailable")
    if not a.current_release_id:
        raise HTTPException(404, "Agent is not published")
    return a


def owned_conversation(db, cid, token, lock=False):
    conv = get_resource(db, Conversation, cid, lock=lock)
    authorize(db, token, conv.workspace_id)
    agent = runtime_agent(db, conv.agent_id, token)
    from .models import RuntimeSession
    runtime = db.get(RuntimeSession, conv.runtime_session_id) if conv.runtime_session_id else None
    if runtime and token.scope != "runtime":
        raise HTTPException(403, "Use the scoped runtime session to access its conversations")
    if runtime and runtime.revoked:
        raise HTTPException(403, "Runtime session was revoked")
    if not effective_channels(agent, db.get(Release, agent.current_release_id)).get(runtime.channel if runtime else "chat"):
        raise HTTPException(403, "Conversation channel disabled")
    if runtime is None:
        from .trust import integration_allowed
        if not integration_allowed(agent, db.get(Release, conv.release_id), token, "chat"):
            raise HTTPException(403, "Conversation identity integration unavailable")
    if conv.user_id != token.user_id or conv.status != "active" or (token.scope == "runtime" and token.agent_id != conv.agent_id):
        raise HTTPException(404, "Conversation unavailable")
    if token.scope == "runtime" and (not token.runtime_session_id or conv.runtime_session_id != token.runtime_session_id):
        raise HTTPException(403, "Runtime session cannot inherit another session")
    return conv


def conversation_view(db, conv):
    release = db.get(Release, conv.release_id)
    return {"id": conv.id, "conversation_id": conv.id, "agent_id": conv.agent_id, "release_id": conv.release_id,
            "status": conv.status, "fixture_mode": release.snapshot["fixture_mode"], "title": conv.title,
            "created_at": conv.created_at, "updated_at": conv.updated_at,
            "runtime_ui": normalized_ui(release.snapshot.get("runtime_ui"))}


def owned_run(db, rid, token, lock=False):
    run = get_resource(db, Run, rid, lock=lock)
    owned_conversation(db, run.conversation_id, token)
    return run


def emit(db, run, event_type, payload):
    conv = db.scalar(select(Conversation).where(Conversation.id == run.conversation_id).with_for_update())
    conv.sequence += 1
    event = Event(workspace_id=run.workspace_id, conversation_id=conv.id, run_id=run.id,
                  revision=run.revision, sequence=conv.sequence, event_type=event_type, payload=payload)
    db.add(event); db.flush()
    return event


def event_view(e):
    return {"schema_version": "1", "conversation_id": e.conversation_id, "run_id": e.run_id,
            "revision": e.revision, "sequence": e.sequence, "event_type": e.event_type, "payload": e.payload}


def run_view(db, run):
    from .context_binding import binding_summaries
    if run.question and run.question.get("kind") in {"entity_mention", "entity_choice", "history_choice"}:
        from .context_binding import authorize_question
        try:
            authorize_question(db, run, run.question["id"])
        except (ValueError, PermissionError):
            raise HTTPException(403, "Question entity or source access was revoked")
    return {"run_id": run.id, "conversation_id": run.conversation_id, "revision": run.revision, "status": run.status,
            "decision": run.decision, "question": run.question,
            "calls": [{"id": c.id, "action_id": c.action_id, "capability_id": c.capability_id, "status": c.status, "operation_id": c.operation_id}
                      for c in db.scalars(select(Call).where(Call.run_id == run.id)).all()], "fixture_mode": settings().fixture_mode,
            "bindings": binding_summaries(run.bindings)}


def status_event(db, run, message=None):
    emit(db, run, "STATUS", {"status": run.status, "decision": run.decision, "message": message, "fixture_mode": settings().fixture_mode})
