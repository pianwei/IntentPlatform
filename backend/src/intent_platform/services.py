from datetime import timedelta
import math
from fastapi import HTTPException
from sqlalchemy import select, func
from .db import now, uid
from .models import Function, FunctionVersion, Agent, Release, Conversation, Run, Call, Event, BusinessSkill, BusinessSkillVersion, ModelProfile, ModelProfileVersion
from .config import settings
from .auth import authorize, manager, token_valid, audit
from .schemas import Definition, RuntimeUI
from .policies import permitted, dependency_error, effective_channels
from .ontology import compile_definition, input_errors
from .ports import connector_checks, execute_connector
from .output_contract import check_output_contract, project_output, OutputContractError


def function_view(f):
    from copy import deepcopy
    validation = deepcopy(f.validation)
    if validation and validation.get("execution_test"):
        # Test facts remain private to the author until explicitly shared as
        # approved output examples. General definition readers see status only.
        validation["execution_test"] = {key: value for key, value in validation["execution_test"].items()
                                        if key in {"passed", "valid", "revision", "status", "run_id", "error"}}
        if validation.get("probe"):
            validation["probe"].pop("data", None)
    if validation and validation.get("failure_execution_test"):
        validation["failure_execution_test"] = {key: value for key, value in validation["failure_execution_test"].items()
                                                if key in {"passed", "valid", "revision", "status", "run_id", "error"}}
    return {"id": f.id, "workspace_id": f.workspace_id, "name": f.definition["name"], "description": f.definition["description"],
            "revision": f.revision, "status": "disabled" if f.disabled else f.status, "definition": f.definition,
            "latest_version": f.latest_version, "is_fixture": f.definition["connector"]["kind"] == "fixture", "validation": validation,
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
            "planning_enabled": a.planning_enabled, "business_skill_ids": a.business_skill_ids,
            "routing_strategy": a.routing_strategy,
            "model_profile_id": a.model_profile_id,
            "access": {"access_mode": a.access_mode, "allowed_users": a.allowed_users}}


def release_view(r):
    snapshot = r.snapshot or {}
    ui = snapshot.get("runtime_ui") or {}
    capabilities = [{"name": (c.get("definition") or {}).get("name", "业务功能"),
                     "version": c.get("version"),
                     "execution_kind": (c.get("definition") or {}).get("execution_kind", "direct")}
                    for c in snapshot.get("capabilities", [])]
    skills = [{"name": (item.get("definition") or {}).get("name", "业务规则"),
               "version": item.get("version")}
              for item in snapshot.get("business_skills", [])]
    icon = ui.get("icon") or {}
    summary = {"title": ui.get("title"), "description": ui.get("description"),
               "welcome_message": ui.get("welcome_message"), "suggested_questions": ui.get("suggested_questions", []),
               "primary_color": ui.get("primary_color"), "input_placeholder": ui.get("input_placeholder"),
               "show_history": ui.get("show_history"),
               "icon": icon.get("value") if icon.get("kind") == "emoji" else ("已配置" if icon.get("kind") == "asset" else None),
               "channels": snapshot.get("channels", {}), "capabilities": capabilities,
               "business_skills": skills,
               "routing": "三层动态路由" if snapshot.get("prompt_policy_version") == "three-layer-v1" else "业务路由"}
    return {"agent_id": r.agent_id, "release_id": r.id, "version": r.version,
            "created_at": r.created_at, "summary": summary,
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
    if cls is Agent and (resource.runtime_ui or {}).get("authoring_test"):
        raise HTTPException(404, "Resource not found")
    if cls is Release and resource.snapshot.get("authoring_test"):
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
    if release.snapshot.get("agent_projection"):
        allowed = {c["capability_id"] for c in result}
        composites = release.snapshot["agent_projection"].get("composites", {})
        result = [c for c in result if c["capability_id"] not in composites or
                  set(composites[c["capability_id"]]["dependency_ids"]) <= allowed]
    return result


def compile_for_workspace(db, definition, workspace_id):
    definition = {**definition, "output_examples": []}
    if definition.get("compiler_version", "finite-json-v1") == "finite-json-v1":
        return compile_definition(definition)
    from .domains import project_domains
    from .resolvers import profiles_for_definition
    compiled = compile_definition(definition, projection=project_domains(db, workspace_id, definition["domain_refs"]))
    compiled["resolver_profiles"] = profiles_for_definition(compiled["semantic_definition"], workspace_id)
    return compiled


def validate_function(db, f, token, body):
    if f.definition.get("authoring_version") == "function-v2" or settings().identity_mode == "local" or settings().env == "production":
        errors, checks, tested = [], {}, None
        failure_tested = (f.validation or {}).get("failure_execution_test")
        previous = (f.validation or {}).get("execution_test")
        if previous and previous.get("revision") == f.revision and not body.probe:
            # A static re-check must not lose an in-flight/UNKNOWN ledger.
            tested = previous
        try:
            from .source_review import assert_ready
            assert_ready(db, f)
            compiled = compile_for_workspace(db, f.definition, f.workspace_id)
            check_output_contract(f.definition)
            checks = {"schema": True, "output_contract": True}
            if f.definition.get("execution_kind") == "composite":
                from .agent_projection import composite_parts
                composite_parts(db, f.workspace_id, f.definition, token.user_id)
                checks["composite_dependencies"] = True
            else:
                connector_checks(f.definition, f.workspace_id)
                checks["connector_configuration"] = True
            if body.probe:
                from .authoring_tests import execution_test
                tested = execution_test(db, f, token, body, compiled)
                if not tested["passed"]:
                    errors.append(tested.get("error", "Actual execution test did not pass"))
                if tested["passed"] and body.failure_arguments is not None:
                    failure_body = body.model_copy(update={"arguments": body.failure_arguments})
                    failure_tested = execution_test(db, f, token, failure_body, compiled, expected_failure=True)
                    checks["failure_probe"] = {"status": failure_tested["status"], "expected_status": "FAILED"}
                    if not failure_tested["passed"]:
                        errors.append("Failure execution test must establish a definite expected business failure")
            if (f.source_state or {}).get("require_failure_probe") and not (
                    failure_tested and failure_tested.get("revision") == f.revision and failure_tested.get("passed")):
                errors.append("Imported operations require successful and failed execution tests on this revision")
        except HTTPException:
            raise
        except Exception as exc:
            errors.append(str(exc))
        result = {"valid": not errors, "revision": f.revision, "errors": errors, "checks": checks,
                  "probe": {"status": tested["status"], "data": tested.get("data", {})} if tested else None,
                  "execution_test": tested, "failure_execution_test": failure_tested}
        f.validation = result; f.validated_revision = f.revision if not errors else None
        f.status = "validated" if not errors else "draft"
        audit(db, token.user_id, f.workspace_id, "function.validate", f.id,
              {"valid": not errors, "actual_execution": bool(tested), "passed": bool(tested and tested["passed"])})
        return result
    if f.definition.get("execution_kind") == "composite":
        errors = []
        try:
            compile_for_workspace(db, f.definition, f.workspace_id)
            from .agent_projection import composite_parts
            composite_parts(db, f.workspace_id, f.definition, token.user_id)
            check_output_contract(f.definition)
        except (ValueError, HTTPException) as exc:
            errors.append(str(exc))
        result = {"valid": not errors, "revision": f.revision, "errors": errors,
                  "checks": {"composite_dependencies": not errors}, "probe": None}
        f.validation = result; f.validated_revision = f.revision if not errors else None
        if not errors: f.status = "validated"
        return result
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


def publish_function(db, f, token, *, reviewed_fixture=False):
    if f.validated_revision != f.revision or not f.validation or not f.validation["valid"]:
        raise HTTPException(409, "Validate the current draft revision first")
    if reviewed_fixture:
        from .zhiyu import function_packages
        approved = [Definition.model_validate(p["definition"]).model_dump() for p in function_packages(
                    f.definition.get("domain_refs", [None])[0], three_layer=True)]
        legacy_demo = settings().env == "test" and settings().allow_dev_auth and f.definition["connector"].get("workflow_id", "").startswith("demo.")
        if legacy_demo:
            from .demo import function_packages as demo_packages
            approved += [Definition.model_validate(p["definition"]).model_dump() for strategy in (False, True)
                         for p in demo_packages(f.definition["domain_refs"][0], three_layer=strategy)]
        if f.definition.get("execution_kind") == "composite":
            from .zhiyu.skills import business_skill_templates, composite_definition
            if legacy_demo:
                from .demo import business_skill_templates
                from .demo.composites import composite_definition
            comp = f.definition["composite"]
            version = db.get(BusinessSkillVersion, comp["skill_version_id"])
            template = next((s for s in business_skill_templates() if version and s["key"] == version.definition["key"]), None)
            from .agent_projection import versioned_capability
            deps = [versioned_capability(db, f.workspace_id, d["function_id"], d["version"], token.user_id) for d in comp["dependencies"]]
            if template:
                from .planning_api import SkillBody
                canonical = SkillBody.model_validate({k: v for k, v in template.items() if k != "version"}).model_dump()
                if SkillBody.model_validate(version.definition).model_dump() != canonical:
                    raise HTTPException(409, "Reviewed fixture Skill differs from the canonical template")
                approved.append(Definition.model_validate(composite_definition(template, version.id, deps, f.definition["domain_refs"][0])).model_dump())
        def canonical_fixture(definition):
            # JSONB orders argument object keys; rebuilding a source template
            # can therefore reorder JSON Schema required arrays. Their order
            # has no semantic meaning. Normalize that order only, never values,
            # constraints, capabilities, rule steps or version pins.
            from copy import deepcopy
            value = deepcopy(definition)
            def schema(node):
                if not isinstance(node, dict): return
                if isinstance(node.get("required"), list): node["required"] = sorted(node["required"])
                for child in node.get("properties", {}).values(): schema(child)
                if node.get("items"): schema(node["items"])
            schema(value.get("input_schema")); schema(value.get("output_schema"))
            return value
        if canonical_fixture(f.definition) not in [canonical_fixture(d) for d in approved]:
            nearest = min(approved, key=lambda definition: sum(definition.get(k) != f.definition.get(k) for k in Definition.model_fields))
            changed = [key for key in Definition.model_fields if nearest.get(key) != f.definition.get(key)]
            raise HTTPException(409, {"message": "Only unchanged source-controlled legacy fixtures may be bootstrapped",
                "definition_fields": changed, "catalog_function": f.definition["connector"].get("workflow_id")})
    if not reviewed_fixture and (f.definition.get("authoring_version") == "function-v2" or settings().identity_mode == "local" or settings().env == "production"):
        tested = f.validation.get("execution_test") or {}
        if tested.get("revision") != f.revision or not tested.get("passed") or tested.get("status") != "SUCCEEDED":
            raise HTTPException(409, "An actual successful execution test of the current draft revision is required")
        from .authoring_tests import current_execution_evidence
        if not current_execution_evidence(db, f):
            raise HTTPException(409, "Current successful execution ledger or author authorization is unavailable")
        if (f.source_state or {}).get("require_failure_probe"):
            if not current_execution_evidence(db, f, expected_failure=True):
                raise HTTPException(409, "A definite expected failed execution test of this revision is required")
    compiled = compile_for_workspace(db, f.definition, f.workspace_id)
    from .source_review import assert_ready
    try:
        manifest = assert_ready(db, f)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if manifest["documents"] or f.source_state:
        compiled["source_manifest"] = manifest
    if f.definition.get("execution_kind") == "composite":
        from .agent_projection import composite_parts
        composite_parts(db, f.workspace_id, f.definition, token.user_id)
    else:
        connector_checks(f.definition, f.workspace_id)
    if f.definition["connector"]["kind"] != "fixture" or f.definition.get("execution_kind") == "composite":
        try:
            check_output_contract(f.definition)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
    f.latest_version += 1
    published = FunctionVersion(workspace_id=f.workspace_id, function_id=f.id, version=f.latest_version,
                                definition=compiled.get("semantic_definition", f.definition), compiled=compiled)
    db.add(published); db.flush()
    f.status = "published"; f.disabled = False
    audit(db, token.user_id, f.workspace_id, "function.publish", f.id, {"version": f.latest_version, "version_id": published.id, "revision": f.revision})
    db.flush()
    return function_view(f)


def publish_agent(db, agent, token, expected_revision=None, *, authoring_capability=None):
    if (agent.runtime_ui or {}).get("authoring_test") and authoring_capability is None:
        raise HTTPException(404, "Private authoring application")
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
        if authoring_capability and fid == authoring_capability["capability_id"]:
            if f.disabled or not permitted(f, token.user_id):
                raise HTTPException(403, "Function test authorization unavailable")
            capabilities.append(authoring_capability)
            continue
        if not f.latest_version or f.disabled or not permitted(f, token.user_id):
            raise HTTPException(409, "Every capability must be published, enabled and authorized")
        version = db.scalar(select(FunctionVersion).where(FunctionVersion.function_id == fid, FunctionVersion.version == f.latest_version))
        if version.definition.get("execution_kind") != "composite":
            connector_checks(version.definition, agent.workspace_id)
        elif agent.routing_strategy != "three-layer-v1":
            raise HTTPException(409, "Composite functions require the three-layer strategy")
        capabilities.append({"capability_id": fid, "version": version.version, "definition": version.definition, "compiled": version.compiled})
    cfg = settings()
    projection = None
    if agent.routing_strategy == "three-layer-v1":
        from .agent_projection import compile_agent
        try:
            capabilities, compiled_skills, projection = compile_agent(db, agent, token.user_id, authoring_capability)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
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
                "identity_policy": {"version": "local-v1" if cfg.identity_mode == "local" else "integration-v1", "profiles": identity_profiles},
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
    if agent.planning_enabled or projection:
        from .models import ActionDefinition, ActionDefinitionVersion
        from .planning import _skill_spec, validate_plan
        snapshot["dependency_profile"] = "platform-v4"
        snapshot["prompt_policy_version"] = "planner-v3"
        snapshot["planning_policy"] = {"version": "finite-plan-v1", "max_steps": 8,
                                       "max_batch": 5, "max_calls": 20, "max_parallel": 3}
        candidate_by_key = {c["definition"]["connector"].get("workflow_id"): c for c in capabilities
                            if c["definition"]["connector"].get("workflow_id")}
        published_skills = []
        for skill_id in ([] if projection else agent.business_skill_ids):
            skill = get_resource(db, BusinessSkill, skill_id, agent.workspace_id)
            if skill.disabled or not skill.latest_version:
                raise HTTPException(409, "Every selected business Skill must be published and enabled")
            version = db.scalar(select(BusinessSkillVersion).where(BusinessSkillVersion.skill_id == skill.id,
                                                                   BusinessSkillVersion.version == skill.latest_version))
            if not version:
                raise HTTPException(409, "Pinned business Skill version is unavailable")
            try:
                spec = _skill_spec(version.definition, {}, candidate_by_key)
                validate_plan(spec, capabilities, "")
            except ValueError as exc:
                raise HTTPException(409, "Business Skill is incompatible with this agent: " + str(exc)) from None
            published_skills.append({"skill_id": skill.id, "version_id": version.id,
                                     "version": version.version, "definition": version.definition})
        snapshot["business_skills"] = compiled_skills if projection else published_skills
        action_keys = {c["definition"]["action"] for c in capabilities}
        action_versions = []
        for key in sorted(action_keys):
            action = db.scalar(select(ActionDefinition).where(ActionDefinition.workspace_id == agent.workspace_id,
                                                               ActionDefinition.key == key))
            if not action or not action.latest_version:
                if projection:
                    continue
                raise HTTPException(409, "Publish the selected capabilities' action definitions first")
            version = db.scalar(select(ActionDefinitionVersion).where(ActionDefinitionVersion.action_id == action.id,
                                                                       ActionDefinitionVersion.version == action.latest_version))
            action_versions.append({"action_id": action.id, "version_id": version.id, "version": version.version,
                                    "definition": version.definition})
        snapshot["action_definitions"] = action_versions
        if agent.model_profile_id:
            profile = get_resource(db, ModelProfile, agent.model_profile_id, agent.workspace_id)
            if profile.disabled or not profile.latest_version:
                raise HTTPException(409, "Selected model profile is unavailable")
            version = db.scalar(select(ModelProfileVersion).where(ModelProfileVersion.profile_id == profile.id,
                                                                  ModelProfileVersion.version == profile.latest_version))
            if not version or not version.secret_ciphertext:
                raise HTTPException(409, "Selected model version has no server credential")
            from .model_profiles import pinned_snapshot
            snapshot["model_profile"] = pinned_snapshot(profile, version)
        elif cfg.env == "production":
            raise HTTPException(409, "Planner publication requires a configured immutable model profile")
        if snapshot["model_profile"]["mode"] == "fixture":
            snapshot["fixture_mode"] = True
    if projection:
        snapshot.update({"dependency_profile": "platform-v5", "prompt_policy_version": "three-layer-v1", "three_layer_prompt_revision": "scope-v4",
                         "agent_projection": projection, "user_output_policy": "final-only-v1",
                         "workspace_id": agent.workspace_id})
    if agent.synthetic_catalog:
        from .synthetic_runtime import catalog_metadata
        if agent.synthetic_catalog != catalog_metadata():
            raise HTTPException(409, "Unknown synthetic catalog configuration")
        snapshot["synthetic_catalog"] = agent.synthetic_catalog
        snapshot["three_layer_prompt_revision"] = "scope-v9"
    if any(s["definition"].get("mode") == "document" for s in snapshot.get("business_skills", [])):
        snapshot["three_layer_prompt_revision"] = "scope-v10"
    if authoring_capability:
        snapshot["authoring_test"] = dict(agent.runtime_ui["authoring_test"])
    error = dependency_error(snapshot, db)
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
    if settings().identity_mode == "local":
        from .local_identity import assert_local_release
        assert_local_release(db.get(Release, conv.release_id))
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
            "practice_active": bool((conv.business_context or {}).get("practice")),
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


_PRIVATE_NAMES = ("password", "secret", "token", "credential", "api_key", "authorization")


def _display_value(value, name="", depth=0, max_depth=3):
    """Bound untrusted historical event values before returning them to a user."""
    if any(marker in name.lower() for marker in _PRIVATE_NAMES):
        return "已隐藏"
    if isinstance(value, str):
        return value[:16000]
    if isinstance(value, float) and not math.isfinite(value):
        return "已省略"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth >= max_depth:
        return "已省略"
    if isinstance(value, list):
        return [_display_value(item, depth=depth + 1, max_depth=max_depth) for item in value[:100]]
    if isinstance(value, dict):
        return {key: _display_value(item, key, depth + 1, max_depth) for key, item in list(value.items())[:40]
                if isinstance(key, str) and len(key) <= 64 and key not in {"session_action", "approved_facts"}}
    return "已省略"


def _approved_data(definition, value):
    if not isinstance(value, dict):
        return None
    if definition.get("output_schema"):
        from .output_contract import project_output, OutputContractError
        try:
            return _display_value(project_output(definition, value)[0])
        except (ValueError, OutputContractError):
            return None
    # Pinned versions predating finite output contracts have only their explicit
    # presentation field map as an approval boundary.
    fields = (definition.get("presentation") or {}).get("field_map") or {}
    if fields:
        return _display_value({field: value[field] for field in fields.values() if field in value})
    # The original development-only fixture predates output mapping. Its
    # synthetic data remains usable, but it can never become a production path.
    if definition.get("connector", {}).get("kind") == "fixture" and settings().env != "production":
        return _display_value(value)
    return None


def _runtime_question(question):
    if not question:
        return None
    return {"id": question["id"], "kind": question.get("kind", "arguments"),
            "fields": [{key: field[key] for key in ("name", "type", "required", "label", "unit", "schema") if key in field}
                       for field in (question.get("fields") or []) if isinstance(field, dict)],
            "options": [{"id": option["id"], "label": str(option["label"])[:160]}
                        for option in (question.get("options") or []) if isinstance(option, dict)
                        and "id" in option and "label" in option]}


def _runtime_steps(db, run):
    from .models import ExecutionPlan, PlanStep, ProcessingAttempt
    plan = db.scalar(select(ExecutionPlan).where(ExecutionPlan.run_id == run.id))
    steps = []
    if plan:
        ordered = {item["step_id"]: index for index, item in enumerate(plan.snapshot.get("steps", []))}
        rows = list(db.scalars(select(PlanStep).where(PlanStep.plan_id == plan.id)))
        for step in sorted(rows, key=lambda row: ordered.get(row.step_key, 1000)):
            values = (step.arguments or {}).get("items", [])
            if not isinstance(values, list):
                values = []
            values = [item for item in values if isinstance(item, dict)]
            approved_input = {key: _display_value(value, key) for key, value in values[0].items()
                              if key in step.definition["input_schema"].get("properties", {})} if len(values) == 1 else {
                "items": [{key: _display_value(value, key) for key, value in item.items()
                           if key in step.definition["input_schema"].get("properties", {})} for item in values[:5]]}
            raw = (step.result or {}).get("data")
            if isinstance(raw, list):
                approved_output = [_approved_data(step.definition, item) for item in raw[:5]]
            else:
                approved_output = _approved_data(step.definition, raw)
            duration = (round((step.finished_at - step.started_at).total_seconds() * 1000, 2)
                        if step.started_at and step.finished_at else None)
            steps.append({"name": step.definition["name"], "status": step.status,
                          "input": approved_input, "output": approved_output, "elapsed_ms": duration})
        return steps
    calls = list(db.scalars(select(Call).where(Call.run_id == run.id).order_by(Call.id)))
    for call in calls:
        attempts = list(db.scalars(select(ProcessingAttempt).where(ProcessingAttempt.run_id == run.id,
                                                              ProcessingAttempt.call_id == call.id)
                                   .order_by(ProcessingAttempt.started_at)))
        started = next((a.started_at for a in attempts if a.started_at), None)
        finished = next((a.finished_at for a in reversed(attempts) if a.finished_at), None)
        steps.append({"name": call.definition["name"], "status": call.status,
                      "input": {key: _display_value(value, key) for key, value in call.arguments.items()
                                if key in call.definition["input_schema"].get("properties", {})},
                      "output": _approved_data(call.definition, (call.result or {}).get("data")),
                      "elapsed_ms": round((finished - started).total_seconds() * 1000, 2) if started and finished else None})
    return steps


def _runtime_timing(db, run):
    stamps = list(db.scalars(select(Event).where(Event.run_id == run.id, Event.event_type == "STATUS")
                             .order_by(Event.sequence)))
    waiting_start = None
    waiting_ms = 0.0
    for event in stamps:
        status = event.payload.get("status")
        if status in {"WAITING_INPUT", "WAITING_CONFIRMATION"} and waiting_start is None:
            waiting_start = event.created_at
        elif status not in {"WAITING_INPUT", "WAITING_CONFIRMATION"} and waiting_start is not None:
            waiting_ms += max(0.0, (event.created_at - waiting_start).total_seconds() * 1000)
            waiting_start = None
    if waiting_start is not None and run.finished_at:
        waiting_ms += max(0.0, (run.finished_at - waiting_start).total_seconds() * 1000)
    total = round(max(0.0, (run.finished_at - run.created_at).total_seconds() * 1000), 2) if run.finished_at else None
    observed_wait = round(waiting_ms, 2) if stamps and waiting_start is None else None
    return {"total_ms": total, "user_wait_ms": observed_wait,
            "processing_ms": round(max(0.0, total - observed_wait), 2) if total is not None and observed_wait is not None else None}


def authorize_fixture_result_access(db, call, user_id):
    """Recheck current synthetic object grants before restoring fixture-v2 data.

    This is deliberately limited to the new demo connector. Older published
    fixtures and resolver-backed functions retain their pinned read behavior.
    """
    connector = call.definition.get("connector", {})
    if connector.get("kind") == "fixture" and connector.get("version") == "3":
        from .synthetic_runtime import authorize_result
        try:
            authorize_result(db, call)
        except (PermissionError, ValueError, KeyError):
            raise HTTPException(403, "Business object access was revoked") from None
        return
    if connector.get("kind") != "fixture" or connector.get("version") != "2" or not call.result:
        return
    from .models import DemoState
    from .demo.engine import authorize_operation, authorized_customers

    state = db.get(DemoState, call.workspace_id)
    run = db.get(Run, call.run_id)
    conv = db.get(Conversation, run.conversation_id) if run else None
    private = (conv.business_context or {}).get("authoring_demo_state") if conv and (conv.business_context or {}).get("authoring_test") else None
    if not state:
        raise HTTPException(403, "Business object access is unavailable")
    try:
        current_state = private if private is not None else state.state
        authorize_operation(connector["workflow_id"], call.arguments, current_state, user_id)
        if connector["workflow_id"] == "demo.customer.search":
            allowed = authorized_customers(user_id, current_state)
            data = (call.result or {}).get("data") or {}
            rows = data.get("customers", []) if isinstance(data, dict) else []
            if any(not isinstance(row, dict) or row.get("customer_id") not in allowed for row in rows):
                raise PermissionError("Synthetic search result access changed")
    except (KeyError, TypeError, ValueError, PermissionError):
        raise HTTPException(403, "Business object access was revoked") from None


def authorize_run_capabilities(db, run):
    """Recheck current function grants before any run result is projected."""
    from .models import ExecutionPlan, PlanStep
    calls = list(db.scalars(select(Call).where(Call.run_id == run.id)))
    capability_ids = {call.capability_id for call in calls}
    plan = db.scalar(select(ExecutionPlan).where(ExecutionPlan.run_id == run.id))
    if plan:
        capability_ids.update(db.scalars(select(PlanStep.capability_id).where(PlanStep.plan_id == plan.id)))
        if plan.snapshot.get("composite_function_id"):
            capability_ids.add(plan.snapshot["composite_function_id"])
        capability_ids.update(g["function_id"] for g in plan.snapshot.get("composite_groups", []))
    for capability_id in capability_ids:
        function = db.get(Function, capability_id)
        if not function or function.disabled or not permitted(function, run.user_id):
            raise HTTPException(403, "Result access was revoked")
    for call in calls:
        authorize_fixture_result_access(db, call, run.user_id)
    conversation = db.get(Conversation, run.conversation_id)
    release = db.get(Release, conversation.release_id) if conversation else None
    if release and (release.snapshot.get("prompt_policy_version") == "three-layer-v1" or any(call.bindings for call in calls)):
        from .context_binding import revalidate_call_bindings
        from .resolvers import resolver_budget
        try:
            with resolver_budget(calls=80, seconds=20):
                for call in calls:
                    revalidate_call_bindings(db, run, call, release)
        except (ValueError, PermissionError):
            raise HTTPException(403, "Business object access was revoked") from None


def business_answer_text(value):
    """Translate reserved synthesis metadata without stripping business fields."""
    import re
    return re.sub(r"(?<![A-Za-z0-9_])deterministic_balance_totals(?![A-Za-z0-9_])",
                  "账户余额合计", value, flags=re.IGNORECASE)


def event_view(e):
    """The same allowlist is applied to polling, SSE and restored history."""
    from sqlalchemy.orm import object_session
    payload = e.payload or {}
    event_type = e.event_type
    db = object_session(e)
    if e.event_type == "STATUS" and payload.get("status") in {"FAILED", "UNKNOWN", "CANCELLED"} and db:
        from .models import ExecutionPlan
        plan = db.scalar(select(ExecutionPlan).where(ExecutionPlan.run_id == e.run_id))
        # Read adapter for already-closed legacy plans predating persistent final
        # events. New plans always append a new final event in status_event.
        if plan and not plan.snapshot.get("three_layer"):
            final = db.scalar(select(Event.id).where(Event.run_id == e.run_id, Event.event_type == "RESULT", Event.payload["title"].as_string() == "答复"))
            last_status = db.scalar(select(Event.id).where(Event.run_id == e.run_id, Event.event_type == "STATUS").order_by(Event.sequence.desc()).limit(1))
            if not final and last_status == e.id:
                from .planning import partial_result_rows, public_result_rows
                rows = public_result_rows(partial_result_rows(db, plan))
                if rows:
                    event_type = "RESULT"
                    payload = {"title": "答复", "data": "已完成部分：" + __import__("json").dumps(rows, ensure_ascii=False)[:11000]}
    if e.event_type == "RESULT" and payload.get("call_id") and db:
        from .models import ExecutionPlan, PlanStep
        plan = db.scalar(select(ExecutionPlan).where(ExecutionPlan.run_id == e.run_id))
        if plan:
            run = db.get(Run, e.run_id)
            event_type = "STATUS"
            payload = {"status": "EXECUTING", "fixture_mode": payload.get("fixture_mode", False)}
    if event_type == "STATUS":
        public = {"status": payload.get("status"), "fixture_mode": bool(payload.get("fixture_mode", False))}
    elif event_type == "QUESTION":
        public = {"question_id": payload.get("question_id"), "kind": payload.get("kind", "arguments"),
                  "fields": _runtime_question({"id": payload.get("question_id"), "fields": payload.get("fields", [])})["fields"],
                  "options": _runtime_question({"id": payload.get("question_id"), "options": payload.get("options", [])})["options"],
                  "message": str(payload.get("message", "请补充信息"))[:240],
                  "errors": [str(item)[:160] for item in payload.get("errors", [])[:8]] if isinstance(payload.get("errors"), list) else []}
    elif event_type == "CONFIRMATION":
        from sqlalchemy.orm import object_session
        from .models import ExecutionPlan, PlanStep
        db = object_session(e)
        approved_actions = []
        source_actions = payload.get("actions")
        if isinstance(source_actions, list) and db:
            for item in source_actions[:8]:
                if not isinstance(item, dict):
                    continue
                version = db.scalar(select(FunctionVersion).where(FunctionVersion.function_id == item.get("capability_id"),
                                                                     FunctionVersion.version == item.get("version")))
                if not version:
                    continue
                permitted_fields = (version.definition.get("input_schema") or {}).get("properties", {})
                arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
                approved_actions.append({"name": str(version.definition.get("name", "业务操作"))[:160],
                                         "arguments": {key: _display_value(value, key) for key, value in arguments.items()
                                                       if key in permitted_fields}})
        elif db:
            plan = db.scalar(select(ExecutionPlan).where(ExecutionPlan.run_id == e.run_id))
            step = (db.scalar(select(PlanStep).where(PlanStep.plan_id == plan.id,
                                                     PlanStep.definition["effect"].as_string() == "WRITE")) if plan else None)
            if step:
                permitted_fields = (step.definition.get("input_schema") or {}).get("properties", {})
                arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
                approved_actions.append({"name": str(step.definition.get("name", "业务操作"))[:160],
                                         "arguments": {key: _display_value(value, key) for key, value in arguments.items()
                                                       if key in permitted_fields}})
        public = {"name": str(payload.get("name", "业务操作"))[:160],
                  "arguments": (approved_actions[0]["arguments"] if len(approved_actions) == 1 else
                                {"业务操作": approved_actions} if approved_actions else {}),
                  "actions": approved_actions,
                  "expires_at": payload.get("expires_at"), "message": "确认后将执行所示业务操作"}
    elif event_type == "RESULT":
        call = None
        db = object_session(e)
        if db and payload.get("call_id"):
            call = db.get(Call, payload["call_id"])
        if call and call.run_id == e.run_id:
            approved = _approved_data(call.definition, payload.get("data"))
            presentation = call.definition.get("presentation") or {}
            mapping = presentation.get("field_map") or {}
            fields = ([{"label": label, "value": approved.get(field)} for label, field in mapping.items() if field in approved]
                      if isinstance(approved, dict) else [])
            if approved is None and presentation.get("files_field") == "files":
                approved = {}
            component = presentation.get("component", "detail")
            title = presentation.get("title", "业务结果")
            allowed_files = {item.get("id"): item for item in (call.result or {}).get("files", [])
                             if isinstance(item, dict) and isinstance(item.get("id"), str)}
            files = [{key: item[key] for key in ("id", "filename", "media_type", "size") if key in item}
                     for item in allowed_files.values()][:20] if approved is not None else []
        elif not payload.get("call_id") and payload.get("title") == "答复" and isinstance(payload.get("data"), str):
            approved, fields, files, component, title = business_answer_text(payload["data"][:12000]), [], [], "text", "答复"
            if isinstance(payload.get("files"), list):
                files = [{k: item[k] for k in ("id", "filename", "media_type", "size") if k in item}
                         for item in payload["files"] if isinstance(item, dict)][:20]
            elif db:
                files = final_files(db, e.run_id)
        else:
            approved, fields, files, component, title = None, [], [], "detail", "业务结果"
        public = {"status": call.status if call and call.run_id == e.run_id else "SUCCEEDED",
                  "component": component if component in {"detail", "table", "text", "markdown", "citation", "file"} else "detail",
                  "title": str(title)[:160], "data": approved,
                  "answer_source": payload.get("answer_source", "business") if payload.get("answer_source", "business") in {"business", "model", "practice", "mixed"} else "business",
                  "practice_active": bool(payload.get("practice_active", False)),
                  "fields": fields, "files": files, "fixture_mode": bool(payload.get("fixture_mode", False)),
                  "result_unavailable": bool(payload.get("result_unavailable", False) or approved is None)}
        if not payload.get("call_id") and payload.get("title") == "答复" and db and approved is not None:
            source = payload["components"] if isinstance(payload.get("components"), list) else final_components(db, e.run_id)
            public["components"] = [{"title": str(item.get("title", "业务结果"))[:160], "component": item["component"],
                "data": _display_value(item.get("data"), max_depth=5),
                "fields": [{"label": str(field.get("label", ""))[:80], "value": _display_value(field.get("value"), max_depth=5)}
                    for field in item.get("fields", [])[:40] if isinstance(field, dict)]}
                for item in source[:8] if isinstance(item, dict) and item.get("component") in {"table", "detail", "citation", "text", "markdown"}]
    else:
        codes = {"BUSINESS_FAILED": "业务处理未成功", "OUTPUT_CONTRACT_ERROR": "业务结果无法按已发布格式展示",
                 "RESULT_UNAVAILABLE": "业务操作已完成，结果暂不可展示", "UNKNOWN": "结果尚不明确，请核对后再操作",
                 "STEP_FAILED": "部分业务步骤未完成", "PLAN_LIMIT": "本次请求超过执行上限",
                 "PLAN_ARGUMENTS": "业务参数不符合已发布规则", "FILE_REJECTED": "文件无法安全展示",
                 "ENTITY_NOT_FOUND": "未找到当前有权访问的业务对象，请核对条件或编号",
                 "FORBIDDEN": "当前权限无法完成请求", "OUT_OF_SCOPE": "当前功能无法完成请求"}
        code = payload.get("code")
        public = {"message": codes.get(code, "请求未完成，请查看运行状态"), "code": code if code in codes else "ERROR"}
    return {"schema_version": "1", "conversation_id": e.conversation_id, "run_id": e.run_id,
            "revision": e.revision, "sequence": e.sequence, "event_type": event_type, "payload": public}


def run_view(db, run):
    authorize_run_capabilities(db, run)
    if run.question and run.question.get("kind") in {"entity_mention", "entity_choice", "history_choice"}:
        from .context_binding import authorize_question
        try:
            authorize_question(db, run, run.question["id"])
        except (ValueError, PermissionError):
            raise HTTPException(403, "Question entity or source access was revoked")
    return {"run_id": run.id, "conversation_id": run.conversation_id, "revision": run.revision, "status": run.status,
            "decision": run.decision, "question": _runtime_question(run.question),
            "calls": [{"id": c.id, "status": c.status}
                      for c in db.scalars(select(Call).where(Call.run_id == run.id)).all()], "fixture_mode": settings().fixture_mode,
            "bindings": [], "steps": [], "timing": _runtime_timing(db, run)}


def status_event(db, run, message=None):
    if run.status in {"SUCCEEDED", "FAILED", "UNKNOWN", "CANCELLED"} and run.finished_at is None:
        run.finished_at = now()
    if run.status in {"FAILED", "UNKNOWN", "CANCELLED"}:
        from .planning import persist_partial_result
        persist_partial_result(db, run)
    emit(db, run, "STATUS", {"status": run.status, "decision": run.decision, "message": message, "fixture_mode": settings().fixture_mode})


def final_files(db, run_id):
    """Files obey the same final-source boundary as visible business results."""
    from .models import ExecutionPlan, PlanStep
    plan = db.scalar(select(ExecutionPlan).where(ExecutionPlan.run_id == run_id))
    allowed_calls = None
    if plan:
        # Composite file mapping is intentionally unsupported until an explicit
        # file output contract exists. Internal attachments are never promoted.
        if plan.snapshot.get("composite_function_id"):
            return []
        from .planning import visible_results
        visible = visible_results(plan.snapshot) - {sid for group in plan.snapshot.get("composite_groups", []) for sid in group["step_ids"]}
        allowed_calls = {cid for step in db.scalars(select(PlanStep).where(PlanStep.plan_id == plan.id))
                         if step.step_key in visible for cid in step.call_ids}
    result = []
    for call in db.scalars(select(Call).where(Call.run_id == run_id, Call.status == "SUCCEEDED")):
        if allowed_calls is None or call.id in allowed_calls:
            result.extend({k: item[k] for k in ("id", "filename", "media_type", "size") if k in item}
                          for item in (call.result or {}).get("files", []) if isinstance(item, dict))
    return result[:20]


def final_components(db, run_id):
    from .models import ExecutionPlan
    from .planning import partial_result_rows
    plan = db.scalar(select(ExecutionPlan).where(ExecutionPlan.run_id == run_id))
    if not plan:
        return []
    release = db.get(Release, plan.release_id)
    definitions = {c["capability_id"]: c["definition"] for c in release.snapshot["capabilities"]}
    result = []
    from .result_presentation import content_path, content_value, response_mode
    for row in partial_result_rows(db, plan):
        definition = definitions.get(row["capability_id"], {})
        presentation = definition.get("presentation", {})
        data = _display_value(row["data"], max_depth=5 if definition.get("execution_kind") == "composite" else 3)
        component = presentation.get("component", "detail")
        if response_mode(definition) == "content":
            data = content_value(data, definition)
            if not isinstance(data, str):
                # Never fall back to JSON serialization when a declared content
                # path is absent or no longer matches the pinned result schema.
                continue
            component = "markdown" if (content_path(definition) or "").split(".")[-1] == "markdown" else "text"
        result.append({"title": str(presentation.get("title") or row["name"])[:160],
            "component": component if component in {"table", "detail", "citation", "text", "markdown"} else "detail",
            "data": data, "fields": [{"label": str(label)[:80], "value": data.get(key)}
                for label, key in presentation.get("field_map", {}).items() if key in data] if isinstance(data, dict) else []})
    return result[:8]


def final_payload(db, run, answer, fixture_mode):
    """Freeze visible result facts at the event sequence that published them."""
    components = final_components(db, run.id)
    from .models import ExecutionPlan
    from .planning import partial_result_rows
    from .result_presentation import remove_result_components, response_mode_for_rows
    plan = db.scalar(select(ExecutionPlan).where(ExecutionPlan.run_id == run.id))
    if plan:
        release = db.get(Release, plan.release_id)
        capabilities = release.snapshot["capabilities"] if release else []
        rows = partial_result_rows(db, plan)
        mode = response_mode_for_rows(rows, capabilities)
        components = remove_result_components(mode, components)
    return {"status": run.status, "component": "text", "title": "答复", "data": business_answer_text(answer),
            "components": components, "files": final_files(db, run.id),
            "fixture_mode": fixture_mode}
