"""Finite action and business-Skill configuration for the management console."""
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from .auth import authenticate, manager, audit
from .db import session
from .models import (ActionDefinition, ActionDefinitionVersion, BusinessSkill,
                     BusinessSkillVersion, Function, FunctionVersion)
from .planning import _skill_spec, validate_plan
from .planning_schemas import PlanCondition, PlanForeach
from .schemas import StrictModel

router = APIRouter()
BASE = "/api/v2/management/workspaces/{ws}"


@router.get(BASE + "/function-options")
def function_options(ws: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    from .function_authoring import options
    return options(db, ws)


@router.post(BASE + "/function-options/bootstrap")
def bootstrap_function_options(ws: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    from .function_authoring import bootstrap
    value = bootstrap(db, ws, token.user_id)
    audit(db, token.user_id, ws, "function.defaults", ws)
    db.commit()
    return value


class ActionBody(StrictModel):
    key: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,39}$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=2000)
    effect: str = Field(pattern=r"^(READ_ONLY|WRITE)$")


class ActionEdit(StrictModel):
    revision: int = Field(ge=1)
    definition: ActionBody


class SkillStepBody(StrictModel):
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    capability_id: str | None = None
    capability_key: str | None = None
    arguments: dict = Field(default_factory=dict)
    parameter_units: dict[str, str] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    when: PlanCondition | None = None
    foreach: PlanForeach | None = None

    @model_validator(mode="after")
    def capability(self):
        if bool(self.capability_id) == bool(self.capability_key):
            raise ValueError("Choose one registered capability ID or demo key")
        return self


class SkillBody(StrictModel):
    mode: str = Field(default="template", pattern=r"^(template|document)$")
    document: str = Field(default="", max_length=24000)
    key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=4000)
    triggers: list[str] = Field(default_factory=list, max_length=40)
    input_fields: list[str] = Field(default_factory=list, max_length=20)
    applicable_entities: list[str] = Field(default_factory=list, max_length=40)
    applicable_actions: list[str] = Field(default_factory=list, max_length=40)
    steps: list[SkillStepBody] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def authoring_mode(self):
        if self.mode == "document":
            if not self.document.strip() or self.steps:
                raise ValueError("Document Skill requires Markdown and cannot contain fixed steps")
        elif not self.steps or self.document:
            raise ValueError("Template Skill requires fixed steps and cannot contain a document")
        return self


class SkillEdit(StrictModel):
    revision: int = Field(ge=1)
    definition: SkillBody


class Revision(StrictModel):
    revision: int = Field(ge=1)


def _action_view(item):
    return {"id": item.id, "workspace_id": item.workspace_id, "key": item.key,
            "definition": item.definition, "revision": item.revision,
            "latest_version": item.latest_version}


def _skill_view(item):
    return {"id": item.id, "workspace_id": item.workspace_id, "key": item.key,
            "definition": item.definition, "revision": item.revision,
            "latest_version": item.latest_version, "disabled": item.disabled}


@router.get(BASE + "/actions")
def list_actions(ws: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    return [_action_view(item) for item in db.scalars(select(ActionDefinition).where(ActionDefinition.workspace_id == ws).order_by(ActionDefinition.key))]


@router.post(BASE + "/actions", status_code=201)
def create_action(ws: str, body: ActionBody, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    if db.scalar(select(ActionDefinition.id).where(ActionDefinition.workspace_id == ws, ActionDefinition.key == body.key)):
        raise HTTPException(409, "Action key already exists")
    item = ActionDefinition(workspace_id=ws, key=body.key, definition=body.model_dump())
    db.add(item); db.flush(); audit(db, token.user_id, ws, "action.create", item.id)
    value = _action_view(item); db.commit(); return value


@router.patch(BASE + "/actions/{action_id}/draft")
def edit_action(ws: str, action_id: str, body: ActionEdit, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    item = db.scalar(select(ActionDefinition).where(ActionDefinition.workspace_id == ws, ActionDefinition.id == action_id).with_for_update())
    if not item: raise HTTPException(404, "Action unavailable")
    if item.revision != body.revision or item.key != body.definition.key:
        raise HTTPException(409, "Action revision or key changed")
    item.definition = body.definition.model_dump(); item.revision += 1
    audit(db, token.user_id, ws, "action.edit", item.id, {"revision": item.revision})
    value = _action_view(item); db.commit(); return value


@router.post(BASE + "/actions/{action_id}/publish")
def publish_action(ws: str, action_id: str, body: Revision, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "publisher")
    item = db.scalar(select(ActionDefinition).where(ActionDefinition.workspace_id == ws, ActionDefinition.id == action_id).with_for_update())
    if not item: raise HTTPException(404, "Action unavailable")
    if item.revision != body.revision:
        raise HTTPException(409, "Action revision changed")
    ActionBody.model_validate(item.definition)
    item.latest_version += 1
    db.add(ActionDefinitionVersion(workspace_id=ws, action_id=item.id, version=item.latest_version,
                                   definition=item.definition))
    audit(db, token.user_id, ws, "action.publish", item.id, {"version": item.latest_version})
    value = _action_view(item); db.commit(); return value


def _skill_candidates(db, ws):
    candidates = []
    for function in db.scalars(select(Function).where(Function.workspace_id == ws, Function.disabled.is_(False),
                                                       Function.latest_version > 0)):
        version = db.scalar(select(FunctionVersion).where(FunctionVersion.function_id == function.id,
                                                          FunctionVersion.version == function.latest_version))
        if version:
            candidates.append({"capability_id": function.id, "version": version.version,
                               "definition": version.definition, "compiled": version.compiled})
    return candidates


def _validate_skill(db, ws, definition):
    if definition.get("mode") == "document":
        SkillBody.model_validate(definition)
        return True
    candidates = _skill_candidates(db, ws)
    by_key = {c["definition"]["connector"].get("workflow_id"): c for c in candidates
              if c["definition"]["connector"].get("workflow_id")}
    spec = _skill_spec(definition, {}, by_key)
    validate_plan(spec, candidates, "")
    return True


@router.get(BASE + "/business-skills")
def list_skills(ws: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    return [_skill_view(item) for item in db.scalars(select(BusinessSkill).where(BusinessSkill.workspace_id == ws).order_by(BusinessSkill.key))]


@router.post(BASE + "/business-skills", status_code=201)
def create_skill(ws: str, body: SkillBody, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    if db.scalar(select(BusinessSkill.id).where(BusinessSkill.workspace_id == ws, BusinessSkill.key == body.key)):
        raise HTTPException(409, "Business Skill key already exists")
    item = BusinessSkill(workspace_id=ws, key=body.key, definition=body.model_dump())
    db.add(item); db.flush(); audit(db, token.user_id, ws, "business_skill.create", item.id)
    value = _skill_view(item); db.commit(); return value


@router.patch(BASE + "/business-skills/{skill_id}/draft")
def edit_skill(ws: str, skill_id: str, body: SkillEdit, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    item = db.scalar(select(BusinessSkill).where(BusinessSkill.workspace_id == ws, BusinessSkill.id == skill_id).with_for_update())
    if not item: raise HTTPException(404, "Business Skill unavailable")
    if item.revision != body.revision or item.key != body.definition.key:
        raise HTTPException(409, "Business Skill revision or key changed")
    item.definition = body.definition.model_dump(); item.revision += 1
    audit(db, token.user_id, ws, "business_skill.edit", item.id, {"revision": item.revision})
    value = _skill_view(item); db.commit(); return value


@router.post(BASE + "/business-skills/{skill_id}/publish")
def publish_skill(ws: str, skill_id: str, body: Revision, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "publisher")
    item = db.scalar(select(BusinessSkill).where(BusinessSkill.workspace_id == ws, BusinessSkill.id == skill_id).with_for_update())
    if not item: raise HTTPException(404, "Business Skill unavailable")
    if item.disabled or item.revision != body.revision:
        raise HTTPException(409, "Business Skill disabled or revision changed")
    definition = SkillBody.model_validate({key: value for key, value in item.definition.items() if key != "version"}).model_dump()
    try:
        _validate_skill(db, ws, definition)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    item.latest_version += 1
    db.add(BusinessSkillVersion(workspace_id=ws, skill_id=item.id, version=item.latest_version,
                                definition=definition))
    audit(db, token.user_id, ws, "business_skill.publish", item.id, {"version": item.latest_version})
    value = _skill_view(item); db.commit(); return value


@router.post(BASE + "/business-skills/{skill_id}/disable")
def disable_skill(ws: str, skill_id: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "admin")
    item = db.scalar(select(BusinessSkill).where(BusinessSkill.workspace_id == ws, BusinessSkill.id == skill_id).with_for_update())
    if not item: raise HTTPException(404, "Business Skill unavailable")
    item.disabled = True
    audit(db, token.user_id, ws, "business_skill.disable", item.id)
    value = _skill_view(item); db.commit(); return value


@router.get(BASE + "/business-skills/{skill_id}/versions")
def skill_versions(ws: str, skill_id: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    return [{"id": v.id, "skill_id": v.skill_id, "version": v.version, "definition": v.definition}
            for v in db.scalars(select(BusinessSkillVersion).where(BusinessSkillVersion.workspace_id == ws,
                               BusinessSkillVersion.skill_id == skill_id).order_by(BusinessSkillVersion.version.desc()))]


@router.post(BASE + "/functions/{fid}/output-examples")
def save_output_example(ws: str, fid: str, body: Revision, token=Depends(authenticate), db: Session = Depends(session)):
    from copy import deepcopy
    from .services import get_resource, function_view
    manager(db, token, ws)
    item = get_resource(db, Function, fid, ws, lock=True)
    from .authoring_tests import test_result
    tested = test_result(db, item, token)
    if item.revision != body.revision or tested.get("revision") != item.revision or not tested.get("passed") or not isinstance(tested.get("data"), dict):
        raise HTTPException(409, "Save an example from a successful execution test of the current revision")
    from .services import _display_value
    data = _display_value(deepcopy(tested["data"]), max_depth=6)
    def no_file_bytes(value):
        if isinstance(value, list): return [no_file_bytes(item) for item in value]
        if isinstance(value, dict):
            return {key: no_file_bytes(item) for key, item in value.items()
                    if key.lower() not in {"content_base64", "base64", "file_bytes", "raw_bytes", "binary", "bytes", "inline_files"}}
        return value
    data = no_file_bytes(data)
    examples = list(item.definition.get("output_examples", []))
    if data not in examples:
        examples = [*examples, data][-5:]
    item.definition = {**item.definition, "output_examples": examples}
    audit(db, token.user_id, ws, "function.example.save", fid, {"revision": item.revision})
    value = function_view(item); db.commit(); return value


@router.get(BASE + "/functions/{fid}/execution-test")
def function_execution_test(ws: str, fid: str, failure: bool = False, token=Depends(authenticate), db: Session = Depends(session)):
    from .services import get_resource
    from .authoring_tests import test_result
    manager(db, token, ws)
    return test_result(db, get_resource(db, Function, fid, ws), token, evidence_key="failure_execution_test" if failure else "execution_test")


class TestConfirmation(StrictModel):
    run_id: str
    revision: int = Field(ge=1)
    confirmed: bool


@router.post(BASE + "/functions/{fid}/execution-test/confirmations")
def confirm_execution_test(ws: str, fid: str, body: TestConfirmation, token=Depends(authenticate), db: Session = Depends(session)):
    from .services import get_resource
    from .authoring_tests import confirm_test
    manager(db, token, ws)
    return confirm_test(db, get_resource(db, Function, fid, ws, lock=True), token, body)


@router.post(BASE + "/agents/{aid}/compile-preview")
def compile_preview(ws: str, aid: str, body: Revision, token=Depends(authenticate), db: Session = Depends(session)):
    from .models import Agent
    from .services import get_resource
    from .agent_projection import compile_agent, public_projection
    manager(db, token, ws, "publisher")
    agent = get_resource(db, Agent, aid, ws)
    if agent.revision != body.revision:
        raise HTTPException(409, "Agent draft revision changed")
    try:
        _, _, projection = compile_agent(db, agent, token.user_id)
        return {"valid": True, "issues": [], "projection": public_projection(projection)}
    except ValueError as exc:
        return {"valid": False, "issues": [{"code": "COMPILATION_FAILED", "message": str(exc)}],
                "projection": {"version": "agent-projection-v1", "entities": [], "actions": [], "skills": [],
                               "explicit_function_ids": agent.capability_ids, "dependency_function_ids": []}}
