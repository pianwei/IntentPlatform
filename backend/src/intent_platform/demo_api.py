"""Idempotent, development-only installation of the coherent 20-function demo."""
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
from .services import validate_function, publish_function, publish_agent, agent_view

router = APIRouter()


@router.post("/api/v2/dev/demo/seed")
def seed_demo(model_profile_id: str | None = Body(default=None, embed=True),
              token=Depends(authenticate), db: Session = Depends(session)):
    if settings().env == "production" or not settings().allow_dev_auth:
        raise HTTPException(404, "Not found")
    if token.scope != "management":
        raise HTTPException(403, "Management identity required")
    from .auth import workspaces
    spaces = workspaces(db, token)
    if not spaces:
        raise HTTPException(403, "No workspace")
    ws = spaces[0]["id"]; manager(db, token, ws, "admin")
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
    for package in function_packages(domain_ref):
        definition = Definition.model_validate(package["definition"]).model_dump()
        f = db.scalar(select(Function).where(Function.workspace_id == ws,
                                             Function.definition["connector"]["workflow_id"].as_string() == package["key"]))
        if f is None:
            f = Function(workspace_id=ws, definition=definition)
            db.add(f); db.flush()
        elif f.definition != definition:
            raise HTTPException(409, "Existing demo function differs; revise and publish explicitly")
        if not f.latest_version:
            validation = validate_function(db, f, token, ValidateBody())
            if not validation["valid"]:
                raise HTTPException(409, "Demo function validation failed: " + "; ".join(validation["errors"]))
            publish_function(db, f, token)
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
    if db.get(DemoState, ws) is None:
        db.add(DemoState(workspace_id=ws, state=initial_state()))
    name = "三层意图演示助手"
    agent = db.scalar(select(Agent).where(Agent.workspace_id == ws, Agent.name == name))
    capability_ids = [item.id for item in functions]
    skill_ids = [item.id for item in skills]
    changed = False
    if agent is None:
        agent = Agent(workspace_id=ws, name=name, capability_ids=capability_ids,
                      planning_enabled=True, business_skill_ids=skill_ids,
                      model_profile_id=model_profile_id,
                      suggested_questions=["查询 C001 的所有账户余额，给我做资金概览",
                                           "分析 A001 在 2026-09-01 到 2026-09-30 的收支",
                                           "查询 C001 的客户业务全景"],
                      allowed_origins=settings().cors_origins.split(","))
        db.add(agent); db.flush()
    elif agent.capability_ids != capability_ids or agent.business_skill_ids != skill_ids or not agent.planning_enabled or agent.model_profile_id != model_profile_id:
        agent.capability_ids = capability_ids
        agent.business_skill_ids = skill_ids
        agent.planning_enabled = True
        agent.model_profile_id = model_profile_id
        agent.revision += 1
        changed = True
    published = db.get(Release, agent.current_release_id) if agent.current_release_id else None
    if changed or not published or published.snapshot.get("prompt_policy_version") != "planner-v1":
        publish_agent(db, agent, token, agent.revision)
    audit(db, token.user_id, ws, "demo.seed", agent.id, {"synthetic_functions": len(functions)})
    result = {"workspace_id": ws, "agent": agent_view(agent, db), "function_count": len(functions),
              "skill_count": len(skills), "domain_version": domain.latest_version,
              "chat_path": "/chat/" + agent.public_id, "embed_path": "/embed/" + agent.public_id,
              "fixture_mode": model_profile_id is None}
    db.commit()
    return result
