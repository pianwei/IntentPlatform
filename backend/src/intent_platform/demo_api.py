"""Idempotent, development-only installation of the coherent 20-function demo."""
from copy import deepcopy
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .auth import authenticate, manager, audit
from .config import settings
from .db import session
from .demo import (action_definitions, business_skill_templates, domain_definition,
                   function_packages, initial_state)
from .domain_schemas import DomainDefinition
from .domains import compile_domain
from .models import (ActionDefinition, ActionDefinitionVersion, Agent, BusinessSkill,
                     BusinessSkillVersion, DemoState, DomainPackage, DomainVersion, Function, ModelProfile, Release)
from .schemas import Definition, ValidateBody
from .services import validate_function, publish_function, publish_agent, agent_view, compile_for_workspace

router = APIRouter()


def validate_legacy_fixture(db, function, token):
    """Arrange unchanged source fixtures only in the explicit isolated test seed.

    publish_function still checks the exact canonical definition and Skill.
    Normal authoring always uses the current actual-execution gate.
    """
    if settings().identity_mode != "local":
        return validate_function(db, function, token, ValidateBody())
    compile_for_workspace(db, function.definition, function.workspace_id)
    if function.definition.get("execution_kind") == "composite":
        from .agent_projection import composite_parts
        composite_parts(db, function.workspace_id, function.definition, token.user_id)
    function.validation = {"valid": True, "revision": function.revision, "errors": [],
        "checks": {"reviewed_legacy_fixture": True}, "probe": None}
    function.validated_revision = function.revision
    return function.validation


@router.post("/api/v2/dev/demo/seed")
def seed_demo(model_profile_id: str | None = Body(default=None, embed=True),
              routing_strategy: str | None = Body(default=None), workspace_id: str | None = Body(default=None),
              token=Depends(authenticate), db: Session = Depends(session)):
    if settings().env != "test":
        raise HTTPException(410, "旧演示目录已退役，请安装智驭业务目录")
    if settings().env == "production" or not settings().allow_dev_auth:
        raise HTTPException(404, "Not found")
    if token.scope != "management":
        raise HTTPException(403, "Management identity required")
    from .auth import workspaces
    spaces = workspaces(db, token)
    if not spaces:
        raise HTTPException(403, "No workspace")
    ws = workspace_id or spaces[0]["id"]; manager(db, token, ws, "admin")
    agent = db.scalar(select(Agent).where(Agent.workspace_id == ws, Agent.name == "三层意图演示助手"))
    routing_strategy = routing_strategy or (agent.routing_strategy if agent else "legacy")
    if routing_strategy not in {"legacy", "three-layer-v1"}:
        raise HTTPException(422, "Unknown routing strategy")
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": ws + ":demo-seed-v1"})
    if model_profile_id:
        profile = db.scalar(select(ModelProfile).where(ModelProfile.id == model_profile_id,
                                                      ModelProfile.workspace_id == ws))
        if not profile or profile.disabled or not profile.latest_version:
            raise HTTPException(409, "Selected model profile is unavailable")
    key = "demo-banking-v1"
    domain = db.scalar(select(DomainPackage).where(DomainPackage.workspace_id == ws, DomainPackage.key == key))
    if domain is None:
        definition = DomainDefinition.model_validate(domain_definition()).model_dump()
        domain = DomainPackage(workspace_id=ws, key=key, definition=definition)
        db.add(domain); db.flush()
    if not domain.latest_version:
        compiled = compile_domain(domain.definition)
        domain.validation = {"valid": True, "revision": domain.revision, "errors": [],
                             "checks": {"finite_domain": True, "concept_count": len(compiled["concepts"])}}
        domain.validated_revision = domain.revision
        domain.latest_version = 1
        db.add(DomainVersion(workspace_id=ws, package_id=domain.id, version=1,
                             definition=domain.definition, compiled=compiled, published_by=token.user_id))
        db.flush()
    domain_ref = {"package_id": domain.id, "version": domain.latest_version}
    for item in action_definitions():
        action = db.scalar(select(ActionDefinition).where(ActionDefinition.workspace_id == ws,
                                                            ActionDefinition.key == item["key"]))
        if action is None:
            action = ActionDefinition(workspace_id=ws, key=item["key"], definition=item)
            db.add(action); db.flush()
        if not action.latest_version:
            action.latest_version = 1
            db.add(ActionDefinitionVersion(workspace_id=ws, action_id=action.id, version=1, definition=action.definition))
    functions = []
    catalog_upgraded = False
    legacy_definitions = {p["key"]: Definition.model_validate(p["definition"]).model_dump() for p in function_packages(domain_ref)}
    for package in function_packages(domain_ref, three_layer=routing_strategy == "three-layer-v1"):
        definition = Definition.model_validate(package["definition"]).model_dump()
        query = select(Function).where(Function.workspace_id == ws,
                   Function.definition["connector"]["workflow_id"].as_string() == package["key"])
        # Existing demo releases identify their own catalogue; self-service
        # clones using the same workflow key must not replace those records.
        f = db.scalar(query.where(Function.id.in_(agent.capability_ids))) if agent else None
        if f is None:
            f = db.scalar(query)
        revise_legacy_direction = False
        if f is None:
            f = Function(workspace_id=ws, definition=definition)
            db.add(f); db.flush()
        elif Definition.model_validate(f.definition).model_dump() != definition:
            # The one known catalog correction adds the connector's existing
            # IN/OUT restriction to the published input contract. Only an exact
            # prior fixture draft may be advanced; user edits are never replaced.
            previous = deepcopy(definition)
            if package["key"] == "demo.transaction.list":
                previous["input_schema"]["properties"]["direction"] = {
                    "type": "string", "minLength": 1, "maxLength": 8}
            known = [previous, legacy_definitions[package["key"]]]
            if Definition.model_validate(f.definition).model_dump() not in known:
                raise HTTPException(409, "Existing demo function differs; revise and publish explicitly")
            f.definition = definition
            f.revision += 1
            f.validation = None
            f.validated_revision = None
            f.status = "draft"
            revise_legacy_direction = catalog_upgraded = True
        if not f.latest_version or revise_legacy_direction:
            validation = validate_legacy_fixture(db, f, token)
            if not validation["valid"]:
                raise HTTPException(409, "Demo function validation failed: " + "; ".join(validation["errors"]))
            publish_function(db, f, token, reviewed_fixture=True)
        functions.append(f)
    skills = []
    for item in business_skill_templates():
        item = {key: value for key, value in item.items() if key != "version"}
        skill = db.scalar(select(BusinessSkill).where(BusinessSkill.workspace_id == ws, BusinessSkill.key == item["key"]))
        if skill is None:
            skill = BusinessSkill(workspace_id=ws, key=item["key"], definition=item)
            db.add(skill); db.flush()
        if not skill.latest_version:
            skill.latest_version = 1
            db.add(BusinessSkillVersion(workspace_id=ws, skill_id=skill.id, version=1, definition=skill.definition))
        skills.append(skill)
    composites = []
    if routing_strategy == "three-layer-v1":
        from .agent_projection import versioned_capability
        from .demo.composites import composite_definition
        by_key = {f.definition["connector"]["workflow_id"]: f for f in functions}
        for skill in skills:
            sv = db.scalar(select(BusinessSkillVersion).where(BusinessSkillVersion.skill_id == skill.id,
                                                             BusinessSkillVersion.version == skill.latest_version))
            keys = list(dict.fromkeys(s["capability_key"] for s in sv.definition["steps"]))
            dependencies = [versioned_capability(db, ws, by_key[key].id) for key in keys]
            definition = Definition.model_validate(composite_definition(sv.definition, sv.id, dependencies, domain_ref)).model_dump()
            workflow = definition["connector"]["workflow_id"]
            f = db.scalar(select(Function).where(Function.workspace_id == ws,
                          Function.definition["connector"]["workflow_id"].as_string() == workflow))
            if f is None:
                f = Function(workspace_id=ws, definition=definition)
                db.add(f); db.flush()
            elif Definition.model_validate(f.definition).model_dump() != definition:
                raise HTTPException(409, "Existing demo composite differs; revise and publish explicitly")
            if not f.latest_version:
                validation = validate_legacy_fixture(db, f, token)
                if not validation["valid"]:
                    raise HTTPException(409, "Demo composite validation failed: " + "; ".join(validation["errors"]))
                publish_function(db, f, token, reviewed_fixture=True)
            composites.append(f)
    if db.get(DemoState, ws) is None:
        db.add(DemoState(workspace_id=ws, state=initial_state()))
    name = "三层意图演示助手"
    agent = db.scalar(select(Agent).where(Agent.workspace_id == ws, Agent.name == name))
    # Rechecking the demo catalog must not silently detach a configured model.
    # An explicit profile ID selects/replaces it; omitted and null preserve it.
    selected_model_profile_id = model_profile_id if model_profile_id is not None else (
        agent.model_profile_id if agent is not None else None
    )
    capability_ids = [item.id for item in [*functions, *composites]]
    skill_ids = [] if composites else [item.id for item in skills]
    changed = False
    if agent is None:
        agent = Agent(workspace_id=ws, name=name, capability_ids=capability_ids,
                      planning_enabled=True, business_skill_ids=skill_ids,
                      routing_strategy=routing_strategy,
                      model_profile_id=selected_model_profile_id,
                      suggested_questions=["查询 C001 的所有账户余额，给我做资金概览",
                                           "分析 A001 在 2026-09-01 到 2026-09-30 的收支",
                                           "查询 C001 的客户业务全景"],
                      allowed_origins=settings().cors_origins.split(","))
        db.add(agent); db.flush()
    elif agent.capability_ids != capability_ids or agent.business_skill_ids != skill_ids or not agent.planning_enabled or agent.model_profile_id != selected_model_profile_id or agent.routing_strategy != routing_strategy:
        agent.capability_ids = capability_ids
        agent.business_skill_ids = skill_ids
        agent.planning_enabled = True
        agent.routing_strategy = routing_strategy
        agent.model_profile_id = selected_model_profile_id
        agent.revision += 1
        changed = True
    published = db.get(Release, agent.current_release_id) if agent.current_release_id else None
    if changed or catalog_upgraded or not published or (routing_strategy == "three-layer-v1" and published.snapshot.get("three_layer_prompt_revision") != "scope-v4") or published.snapshot.get("prompt_policy_version") != ("three-layer-v1" if routing_strategy == "three-layer-v1" else "planner-v3"):
        publish_agent(db, agent, token, agent.revision)
    audit(db, token.user_id, ws, "demo.seed", agent.id, {"synthetic_functions": len(functions)})
    result = {"workspace_id": ws, "agent": agent_view(agent, db), "function_count": len(functions),
              "skill_count": len(skills), "domain_version": domain.latest_version,
              "composite_count": len(composites),
              "chat_path": "/chat/" + agent.public_id, "embed_path": "/embed/" + agent.public_id,
              "fixture_mode": selected_model_profile_id is None}
    db.commit()
    return result
