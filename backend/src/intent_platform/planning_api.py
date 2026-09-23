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
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    when: PlanCondition | None = None
    foreach: PlanForeach | None = None

    @model_validator(mode="after")
    def capability(self):
        if bool(self.capability_id) == bool(self.capability_key):
            raise ValueError("Choose one registered capability ID or demo key")
        return self


class SkillBody(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=4000)
    triggers: list[str] = Field(default_factory=list, max_length=40)
    input_fields: list[str] = Field(default_factory=list, max_length=20)
    steps: list[SkillStepBody] = Field(min_length=1, max_length=8)


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
