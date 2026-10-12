"""Authoring metadata backed by the existing immutable domain/action registries.

Platform definitions are pure defaults. Adopting them explicitly creates or
updates space-owned versions; historical function and agent snapshots are never
rewritten. Legacy resources appear without a GET changing business state.
"""
from copy import deepcopy
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from typing import Literal
from .db import session, uid, now
from .auth import authenticate, manager, audit
from .schemas import StrictModel
from .models import (BusinessCatalogItem as Item, BusinessCatalogVersion as Version,
                     DomainPackage, DomainVersion, ActionDefinition, ActionDefinitionVersion,
                     Function, FunctionVersion, Audit)
from .domains import compile_domain
from .workspace_admin import can_allocate, identity_directory

router = APIRouter()
PLATFORM = "/api/v2/management/platform/business-catalog"
SPACE = "/api/v2/management/workspaces/{ws}/business-catalog"


class CatalogFields(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    effect: Literal["READ_ONLY", "WRITE"] | None = None
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def meaningful(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Use a meaningful business name")
        return value.strip()


class CatalogCreate(CatalogFields):
    kind: Literal["object", "action"]


class CatalogEdit(CatalogFields):
    revision: int = Field(ge=1)


class Adoption(StrictModel):
    default_id: str
    version: int = Field(ge=1)


def platform(db, token, write=False):
    if not can_allocate(token):
        raise HTTPException(403, "Platform administrator required")
    db.execute(text("SELECT set_config('intent.catalog_admin','true',true)"))


def space_write(db, token, ws):
    manager(db, token, ws)
    manager(db, token, ws, "publisher")


def view(item):
    d = item.definition
    return {"id": item.id, "kind": item.kind, "key": item.key, "name": d["name"],
            "description": d.get("description", ""), "effect": d.get("effect"), "enabled": d.get("enabled", True),
            "revision": item.revision, "latest_version": item.latest_version, "origin": item.origin,
            "default_id": item.default_id, "adopted_version": item.adopted_version, "created_at": item.created_at}


def legacy_items(db, ws, existing):
    """Include resources installed after the metadata migration without mutating GET."""
    keys = {(i.kind, i.domain_package_id or i.action_definition_id, i.key) for i in existing}
    rows = []
    for package in db.scalars(select(DomainPackage).where(DomainPackage.workspace_id == ws, DomainPackage.latest_version > 0)):
        version = db.scalar(select(DomainVersion).where(DomainVersion.package_id == package.id, DomainVersion.version == package.latest_version))
        if not version: continue
        for concept in version.definition["concepts"]:
            if concept["kind"] != "object" or ("object", package.id, concept["id"]) in keys: continue
            rows.append(Item(id="catalog_legacy_object_" + package.id + "_" + concept["id"].encode().hex(),
                workspace_id=ws, kind="object", key=concept["id"], definition={"name": concept["label"], "description": version.definition.get("description", ""), "effect": None, "enabled": True},
                origin="legacy", revision=1, latest_version=1, domain_package_id=package.id, created_at=version.created_at))
    for action in db.scalars(select(ActionDefinition).where(ActionDefinition.workspace_id == ws, ActionDefinition.latest_version > 0)):
        if ("action", action.id, action.key) in keys: continue
        version = db.scalar(select(ActionDefinitionVersion).where(ActionDefinitionVersion.action_id == action.id, ActionDefinitionVersion.version == action.latest_version))
        if version:
            rows.append(Item(id="catalog_legacy_action_" + action.id, workspace_id=ws, kind="action", key=action.key,
                definition={"name": version.definition["name"], "description": version.definition.get("description", ""), "effect": version.definition["effect"], "enabled": True},
                origin="legacy", revision=1, latest_version=1, action_definition_id=action.id, created_at=version.created_at))
    return rows


def items(db, ws):
    rows = list(db.scalars(select(Item).where(Item.workspace_id == ws).order_by(Item.created_at, Item.id)))
    return rows + legacy_items(db, ws, rows) if ws else rows


def item_for(db, ws, identifier, lock=False):
    query = select(Item).where(Item.id == identifier, Item.workspace_id == ws)
    item = db.scalar(query.with_for_update() if lock else query)
    if item: return item
    if ws:
        item = next((i for i in items(db, ws) if i.id == identifier), None)
        if item:
            if lock:
                db.add(item); db.flush()
                db.add(Version(workspace_id=ws, item_id=item.id, version=1, definition=deepcopy(item.definition), created_at=item.created_at)); db.flush()
            return item
    raise HTTPException(404, "Business catalogue item unavailable")


def fields(body, kind):
    value = body.model_dump(exclude={"kind", "revision"})
    if kind == "object":
        if value.get("effect") is not None: raise HTTPException(422, "Objects cannot have an execution effect")
    elif value.get("effect") is None:
        value["effect"] = "READ_ONLY"
    return value


def backing_version(db, item, user_id):
    d = item.definition
    if item.kind == "object":
        package = db.get(DomainPackage, item.domain_package_id) if item.domain_package_id else None
        if package:
            package = db.scalar(select(DomainPackage).where(DomainPackage.id == package.id).with_for_update())
            latest = db.scalar(select(DomainVersion).where(DomainVersion.package_id == package.id, DomainVersion.version == package.latest_version))
            definition = deepcopy(latest.definition if latest else package.definition)
            for concept in definition["concepts"]:
                if concept["id"] == item.key: concept["label"] = d["name"]
            package.revision += 1
        else:
            result_key = item.key + "Result"
            definition = {"name": d["name"] + "业务对象", "description": d.get("description") or d["name"],
                "source": {"reference": "catalog:" + item.id, "owner": "业务目录"},
                "concepts": [{"id": item.key, "label": d["name"], "kind": "object"},
                             {"id": result_key, "label": d["name"] + "结果", "kind": "result"}],
                "relations": [], "units": [], "conversions": []}
            package = DomainPackage(workspace_id=item.workspace_id, key="catalog_" + item.id.split("_")[-1], definition=definition, latest_version=0)
            db.add(package); db.flush(); item.domain_package_id = package.id
        package.definition = definition; package.latest_version += 1
        db.add(DomainVersion(workspace_id=item.workspace_id, package_id=package.id, version=package.latest_version,
                             definition=definition, compiled=compile_domain(definition), published_by=user_id))
    else:
        action = db.get(ActionDefinition, item.action_definition_id) if item.action_definition_id else None
        if action:
            action = db.scalar(select(ActionDefinition).where(ActionDefinition.id == action.id).with_for_update())
            action.revision += 1
        else:
            action = ActionDefinition(workspace_id=item.workspace_id, key=item.key, definition={}, latest_version=0)
            db.add(action); db.flush(); item.action_definition_id = action.id
        action.definition = {"key": item.key, "name": d["name"], "description": d.get("description") or d["name"], "effect": d["effect"]}
        action.latest_version += 1
        db.add(ActionDefinitionVersion(workspace_id=item.workspace_id, action_id=action.id, version=action.latest_version, definition=action.definition))
    db.flush()


def append_version(db, item, token, action):
    if item.workspace_id: backing_version(db, item, token.user_id)
    row = Version(workspace_id=item.workspace_id, item_id=item.id, version=item.latest_version, definition=deepcopy(item.definition))
    db.add(row); db.flush()
    audit(db, token.user_id, item.workspace_id or "@platform", action, item.id,
          {"version": row.version, "version_id": row.id, "revision": item.revision})


def create(db, ws, body, token):
    generated = uid().split("_")[-1]
    key = "catalog:Object" + generated if body.kind == "object" else "ACTION_" + generated[:24].upper()
    item = Item(workspace_id=ws, kind=body.kind, key=key, definition=fields(body, body.kind),
                origin="workspace" if ws else "platform", revision=1, latest_version=1)
    db.add(item); db.flush(); append_version(db, item, token, "catalog.create")
    value = view(item); db.commit(); return value


def edit(db, ws, identifier, body, token):
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": "catalog:" + identifier})
    item = item_for(db, ws, identifier, lock=True)
    if item.revision != body.revision: raise HTTPException(409, "Catalogue revision changed")
    if item.default_id: raise HTTPException(409, "Adopt a default version or create a space-owned item; do not modify an adopted default")
    item.definition = fields(body, item.kind); item.revision += 1; item.latest_version += 1
    append_version(db, item, token, "catalog.edit")
    value = view(item); db.commit(); return value


def version_list(db, ws, identifier):
    item = item_for(db, ws, identifier)
    rows = list(db.scalars(select(Version).where(Version.item_id == identifier, Version.workspace_id == ws).order_by(Version.version.desc())))
    if not rows:
        return [{"id": None, "version": 1, "definition": item.definition, "created_at": item.created_at}]
    return [{"id": v.id, "version": v.version, "definition": v.definition, "created_at": v.created_at} for v in rows]


@router.get(PLATFORM)
def platform_list(token=Depends(authenticate), db: Session = Depends(session)):
    platform(db, token)
    return {"items": [view(i) for i in items(db, None)]}


@router.post(PLATFORM, status_code=201)
def platform_create(body: CatalogCreate, token=Depends(authenticate), db: Session = Depends(session)):
    platform(db, token, True)
    return create(db, None, body, token)


@router.patch(PLATFORM + "/{identifier}")
def platform_edit(identifier: str, body: CatalogEdit, token=Depends(authenticate), db: Session = Depends(session)):
    platform(db, token, True)
    return edit(db, None, identifier, body, token)


@router.get(PLATFORM + "/{identifier}/versions")
def platform_versions(identifier: str, token=Depends(authenticate), db: Session = Depends(session)):
    platform(db, token)
    return version_list(db, None, identifier)


@router.get(PLATFORM + "/{identifier}/audit")
def platform_audit(identifier: str, token=Depends(authenticate), db: Session = Depends(session)):
    platform(db, token); item_for(db, None, identifier)
    names = identity_directory()
    return [{"id": a.id, "user_id": a.user_id, "actor_name": names.get(a.user_id), "action": a.action,
             "resource_id": a.resource_id, "detail": a.detail, "created_at": a.created_at}
            for a in db.scalars(select(Audit).where(Audit.workspace_id == "@platform", Audit.resource_id == identifier).order_by(Audit.created_at.desc()).limit(200))]


@router.get(SPACE)
def space_list(ws: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    return {"items": [view(i) for i in items(db, ws)], "defaults": [view(i) for i in items(db, None)]}


@router.post(SPACE, status_code=201)
def space_create(ws: str, body: CatalogCreate, token=Depends(authenticate), db: Session = Depends(session)):
    space_write(db, token, ws)
    return create(db, ws, body, token)


@router.patch(SPACE + "/{identifier}")
def space_edit(ws: str, identifier: str, body: CatalogEdit, token=Depends(authenticate), db: Session = Depends(session)):
    space_write(db, token, ws)
    return edit(db, ws, identifier, body, token)


@router.get(SPACE + "/{identifier}/versions")
def space_versions(ws: str, identifier: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    return version_list(db, ws, identifier)


def adoption_target(db, ws, default_id, version):
    default = item_for(db, None, default_id)
    chosen = db.scalar(select(Version).where(Version.item_id == default.id, Version.version == version, Version.workspace_id.is_(None)))
    if not chosen: raise HTTPException(404, "Default catalogue version unavailable")
    adopted = db.scalar(select(Item).where(Item.workspace_id == ws, Item.default_id == default_id).with_for_update())
    if not adopted:
        # Only stable built-in keys have an explicit correspondence. Names alone
        # never merge user-defined concepts or actions.
        adopted = next((i for i in items(db, ws) if i.kind == default.kind and i.key == default.key and i.origin == "legacy"), None)
    return default, chosen, adopted


def affected(db, ws, item):
    if not item: return []
    field = "object_type" if item.kind == "object" else "action"
    rows = []
    for f in db.scalars(select(Function).where(Function.workspace_id == ws)):
        versions = list(db.scalars(select(FunctionVersion).where(FunctionVersion.function_id == f.id, FunctionVersion.workspace_id == ws)))
        if f.definition.get(field) == item.key or any(v.definition.get(field) == item.key for v in versions):
            rows.append({"id": f.id, "name": f.definition["name"]})
    return rows


@router.get(SPACE + "/adopt-impact")
def adoption_impact(ws: str, default_id: str, version: int = Query(ge=1), token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    _, chosen, adopted = adoption_target(db, ws, default_id, version)
    return {"functions": affected(db, ws, adopted), "changes": chosen.definition,
            "adopted_version": adopted.adopted_version if adopted else None, "target_version": chosen.version}


@router.post(SPACE + "/adopt")
def adopt(ws: str, body: Adoption, token=Depends(authenticate), db: Session = Depends(session)):
    space_write(db, token, ws)
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": "catalog-adopt:" + ws + ":" + body.default_id})
    default, chosen, adopted = adoption_target(db, ws, body.default_id, body.version)
    if not chosen.definition.get("enabled", True): raise HTTPException(409, "Disabled default version cannot be newly adopted")
    if adopted and adopted.default_id and adopted.adopted_version == chosen.version:
        return view(adopted)
    if adopted:
        adopted = item_for(db, ws, adopted.id, lock=True)
        adopted.revision += 1; adopted.latest_version += 1
    else:
        adopted = Item(workspace_id=ws, kind=default.kind, key=default.key, origin="platform", definition={}, revision=1, latest_version=1)
        db.add(adopted); db.flush()
    adopted.definition = deepcopy(chosen.definition); adopted.origin = "platform"; adopted.default_id = default.id; adopted.adopted_version = chosen.version
    append_version(db, adopted, token, "catalog.adopt")
    value = view(adopted); db.commit(); return value


@router.get(SPACE + "/relationships")
def relationships(ws: str, include_drafts: bool = False, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    definitions = []
    for f in db.scalars(select(Function).where(Function.workspace_id == ws, Function.disabled.is_(False))):
        if include_drafts:
            definitions.append((f, None, f.definition))
        elif f.latest_version:
            v = db.scalar(select(FunctionVersion).where(FunctionVersion.function_id == f.id, FunctionVersion.version == f.latest_version, FunctionVersion.workspace_id == ws))
            if v: definitions.append((f, v.version, v.definition))
    composites = {}
    for f, version, d in definitions:
        if d.get("execution_kind") == "composite":
            for dependency in (d.get("composite") or {}).get("dependencies", []):
                composites.setdefault((dependency["function_id"], dependency["version"]), []).append({"id": f.id, "name": d["name"], "version": version})
    directory = items(db, ws)
    objects = {i.key: {"id": i.id, "name": i.definition["name"], "actions": []} for i in directory if i.kind == "object"}
    actions = {i.key: i.definition["name"] for i in directory if i.kind == "action"}
    grouped = {}
    for f, version, d in definitions:
        if d.get("execution_kind", "direct") != "direct": continue
        object_row = objects.setdefault(d["object_type"], {"id": d["object_type"], "name": d["object_type"], "actions": []})
        group_key = (d["object_type"], d["action"])
        action = grouped.get(group_key)
        if action is None:
            action = {"key": d["action"], "name": actions.get(d["action"], d["action"]), "functions": []}
            grouped[group_key] = action; object_row["actions"].append(action)
        parents = composites.get((f.id, version), []) if version is not None else [c for (fid, _), values in composites.items() if fid == f.id for c in values]
        action["functions"].append({"id": f.id, "name": d["name"], "version": version, "execution_kind": "direct", "composites": parents})
    return {"objects": sorted(objects.values(), key=lambda row: (row["name"], row["id"]))}
