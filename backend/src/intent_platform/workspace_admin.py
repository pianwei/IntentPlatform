"""Space allocation for server-approved platform principals; no data-access grant."""
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator
from sqlalchemy import select, text, func
from sqlalchemy.orm import Session

from .api_models import WorkspaceOut
from .auth import authenticate, audit
from .config import settings
from .db import session, uid, now
from .models import Membership, Workspace, Agent, Function
from .schemas import StrictModel
from .workspace_schemas import (WorkspaceMetadata, WorkspacePage, WorkspaceRename, WorkspaceRevision,
                                WorkspaceAdministrator, MemberPage, EffectiveAccess)

router = APIRouter()


def identity_directory():
    cfg = settings()
    if cfg.identity_mode == "local":
        from .db import engine
        from .identity_models import LocalAccount
        with Session(engine) as directory_db:
            return {row.user_id: row.display_name for row in directory_db.scalars(select(LocalAccount).where(LocalAccount.active.is_(True)))}
    entries = json.loads(cfg.identity_directory_json) if cfg.identity_directory_json is not None else {}
    if cfg.env in {"development", "test"} and cfg.allow_dev_auth and cfg.identity_directory_json is None:
        entries = {"dev-admin": "开发管理员", "dev-reader": "开发业务用户", "dev-other": "开发隔离空间管理员", **entries}
    return entries


def platform_administrators():
    cfg = settings()
    if cfg.identity_mode == "local":
        return set(json.loads(cfg.platform_admins_json or "[]")) & set(identity_directory())
    values = json.loads(cfg.platform_admins_json) if cfg.platform_admins_json is not None else []
    if cfg.env in {"development", "test"} and cfg.allow_dev_auth and cfg.platform_admins_json is None:
        values = ["dev-admin"]
    return set(values) & set(identity_directory())


def can_allocate(token):
    return token.scope == "management" and token.user_id in platform_administrators()


class DirectoryIdentity(StrictModel):
    user_id: str
    label: str


class PlatformContext(StrictModel):
    can_manage_workspaces: bool
    identities: list[DirectoryIdentity]


class WorkspaceCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    administrator_id: str = Field(min_length=1, max_length=160)

    @field_validator("name", "administrator_id")
    @classmethod
    def meaningful(cls, value):
        if not value.strip() or value != value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Use a nonempty trimmed value without control characters")
        return value


@router.get("/api/v2/management/platform-context", response_model=PlatformContext)
def platform_context(token=Depends(authenticate), db: Session = Depends(session)):
    if token.scope != "management":
        raise HTTPException(403, "Management identity required")
    from .trust import member_authority, identity_workspace_allowed
    can_manage = can_allocate(token)
    is_admin = any("admin" in member_authority(row)[0] and member_authority(row)[1]
                   and identity_workspace_allowed(token, row.workspace_id)
                   for row in db.scalars(select(Membership).where(Membership.user_id == token.user_id)))
    entries = identity_directory() if can_manage or is_admin else {}
    return {"can_manage_workspaces": can_manage,
            "identities": [{"user_id": user, "label": label} for user, label in sorted(entries.items())]}


@router.post("/api/v2/management/workspaces", response_model=WorkspaceOut, status_code=201)
def create_workspace(body: WorkspaceCreate, token=Depends(authenticate), db: Session = Depends(session)):
    if not can_allocate(token):
        raise HTTPException(403, "Server-approved platform administrator required")
    if body.administrator_id not in identity_directory():
        raise HTTPException(403, "Administrator must be in the trusted identity directory")
    from .trust import ROLES
    ws = uid("ws")
    # The new-space creator is recorded for this narrow audit insert policy.
    # It grants no read access, no membership and no database bypass to the caller.
    db.execute(text("INSERT INTO workspaces(id,name,created_by) VALUES (:id,:name,:actor)"),
               {"id": ws, "name": body.name, "actor": token.user_id})
    assigned = Membership(user_id=body.administrator_id, workspace_id=ws, roles=sorted(ROLES),
                          role_ceiling=sorted(ROLES), active=True)
    db.add(assigned); db.flush()
    audit(db, token.user_id, ws, "workspace.create", ws, {"administrator_id": body.administrator_id})
    db.commit()
    return {"id": ws, "name": body.name,
            "roles": sorted(ROLES) if body.administrator_id == token.user_id else []}


def identity_labels(db, ids):
    from .identity_models import LocalAccount
    if settings().identity_mode == "local":
        return {r.user_id: {"username": r.username, "display_name": r.display_name, "account_active": r.active}
                for r in db.scalars(select(LocalAccount).where(LocalAccount.user_id.in_(ids)))}
    directory = identity_directory()
    return {user: {"username": None, "display_name": directory.get(user, "历史账号"),
                   "account_active": user in directory} for user in ids}


def workspace_authority(db, token, ws):
    from .trust import member_authority, identity_workspace_allowed
    if token.scope != "management":
        raise HTTPException(403, "Management identity required")
    member = db.get(Membership, (token.user_id, ws))
    roles, active = member_authority(member)
    active = active and identity_workspace_allowed(token, ws)
    platform = can_allocate(token)
    return platform, roles if active else [], active


def metadata(db, token, row):
    from .trust import member_authority
    platform, roles, active = workspace_authority(db, token, row.id)
    if not platform and not active:
        raise HTTPException(403, "Workspace metadata unavailable")
    members = list(db.scalars(select(Membership).where(Membership.workspace_id == row.id)))
    labels = identity_labels(db, [m.user_id for m in members])
    admins, count = [], 0
    for member in members:
        effective, enabled = member_authority(member)
        identity = labels.get(member.user_id, {"username": None, "display_name": "历史账号", "account_active": False})
        if enabled and identity["account_active"]:
            count += 1
            if "admin" in effective:
                admins.append({"user_id": member.user_id, "username": identity["username"], "display_name": identity["display_name"]})
    return {"id": row.id, "name": row.name, "status": row.status, "revision": row.revision,
            "archived_at": row.archived_at, "roles": roles, "administrators": admins, "member_count": count,
            "can_manage": platform, "can_open": active and row.status == "active",
            "can_archive": platform, "can_rename": platform or "admin" in roles}


@router.get("/api/v2/management/platform/workspaces", response_model=WorkspacePage)
def workspace_directory(query: str | None = Query(default=None, max_length=200),
                        status: Literal["active", "archived", "all"] = "active",
                        limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0, le=100000),
                        token=Depends(authenticate), db: Session = Depends(session)):
    if not can_allocate(token):
        raise HTTPException(403, "Platform administrator required")
    criteria = []
    if status != "all": criteria.append(Workspace.status == status)
    if query and query.strip(): criteria.append(func.strpos(func.lower(Workspace.name), query.strip().lower()) > 0)
    total = db.scalar(select(func.count()).select_from(Workspace).where(*criteria))
    rows = db.scalars(select(Workspace).where(*criteria).order_by(func.lower(Workspace.name), Workspace.id).limit(limit).offset(offset))
    return {"items": [metadata(db, token, row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/api/v2/management/workspaces/{ws}", response_model=WorkspaceMetadata)
def workspace_metadata(ws: str, token=Depends(authenticate), db: Session = Depends(session)):
    row = db.get(Workspace, ws)
    if not row: raise HTTPException(404, "Workspace unavailable")
    return metadata(db, token, row)


def editable_workspace(db, token, ws, revision, *, platform_only=False):
    from .capacity import lock_workspace
    platform, roles, _ = workspace_authority(db, token, ws)
    if not platform and (platform_only or "admin" not in roles):
        raise HTTPException(403, "Platform administrator required" if platform_only else "Workspace administrator required")
    lock_workspace(db, ws)
    row = db.scalar(select(Workspace).where(Workspace.id == ws).with_for_update().execution_options(populate_existing=True))
    if not row: raise HTTPException(404, "Workspace unavailable")
    if row.revision != revision: raise HTTPException(409, "Workspace revision changed; reload before saving")
    return row


@router.patch("/api/v2/management/workspaces/{ws}", response_model=WorkspaceMetadata)
def rename_workspace(ws: str, body: WorkspaceRename, token=Depends(authenticate), db: Session = Depends(session)):
    row = editable_workspace(db, token, ws, body.revision)
    if row.status != "active": raise HTTPException(409, "Restore the workspace before renaming")
    previous = row.name; row.name = body.name; row.revision += 1
    audit(db, token.user_id, ws, "workspace.rename", ws, {"previous_name": previous, "name": row.name, "revision": row.revision})
    db.flush(); result = metadata(db, token, row); db.commit(); return result


@router.post("/api/v2/management/workspaces/{ws}/archive", response_model=WorkspaceMetadata)
def archive_workspace(ws: str, body: WorkspaceRevision, token=Depends(authenticate), db: Session = Depends(session)):
    row = editable_workspace(db, token, ws, body.revision, platform_only=True)
    if row.status != "active": raise HTTPException(409, "Workspace is already archived")
    row.status = "archived"; row.revision += 1; row.access_epoch += 1
    row.archived_at = now(); row.archived_by = token.user_id; db.flush()
    db.execute(text("SELECT intent_archive_workspace_work(:ws)"), {"ws": ws})
    audit(db, token.user_id, ws, "workspace.archive", ws, {"revision": row.revision, "access_epoch": row.access_epoch})
    result = metadata(db, token, row); db.commit(); return result


@router.post("/api/v2/management/workspaces/{ws}/restore", response_model=WorkspaceMetadata)
def restore_workspace(ws: str, body: WorkspaceRevision, token=Depends(authenticate), db: Session = Depends(session)):
    row = editable_workspace(db, token, ws, body.revision, platform_only=True)
    if row.status != "archived": raise HTTPException(409, "Workspace is already active")
    row.status = "active"; row.revision += 1; row.archived_at = None; row.archived_by = None
    audit(db, token.user_id, ws, "workspace.restore", ws, {"revision": row.revision, "access_epoch": row.access_epoch})
    db.flush(); result = metadata(db, token, row); db.commit(); return result


@router.post("/api/v2/management/platform/workspaces/{ws}/administrators", response_model=WorkspaceMetadata)
def assign_administrator(ws: str, body: WorkspaceAdministrator, token=Depends(authenticate), db: Session = Depends(session)):
    row = editable_workspace(db, token, ws, body.revision, platform_only=True)
    if row.status != "active": raise HTTPException(409, "Restore the workspace before assigning administrators")
    from .trust import member_ceiling
    member = db.get(Membership, (body.user_id, ws))
    grant = member_ceiling(ws, body.user_id, member)
    if body.user_id not in identity_directory() or not grant["active"] or "admin" not in grant["roles"]:
        raise HTTPException(403, "Administrator must be active and within the trusted member ceiling")
    if member is None:
        member = Membership(user_id=body.user_id, workspace_id=ws, roles=["admin"], role_ceiling=grant["roles"])
        db.add(member)
    else:
        member.roles = sorted(set(member.roles) | {"admin"}); member.active = True; member.revision += 1
    row.revision += 1
    audit(db, token.user_id, ws, "workspace.assign_administrator", ws,
          {"administrator_id": body.user_id, "revision": row.revision})
    db.flush(); result = metadata(db, token, row); db.commit(); return result


def displayed_members(db, ws):
    from .governance_api import member_view
    from .trust import trusted_members
    rows = {m.user_id: m for m in db.scalars(select(Membership).where(Membership.workspace_id == ws))}
    ids = sorted(set(rows) | set(trusted_members(ws) or {}))
    labels = identity_labels(db, ids)
    return [{**member_view(ws, user, rows.get(user)),
             **labels.get(user, {"username": None, "display_name": "历史账号", "account_active": False})} for user in ids]


@router.get("/api/v2/management/workspaces/{ws}/members/page", response_model=MemberPage)
def member_page(ws: str, query: str | None = Query(default=None, max_length=200),
                status: Literal["active", "inactive", "all"] = "active",
                limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0, le=100000),
                token=Depends(authenticate), db: Session = Depends(session)):
    from .auth import manager
    manager(db, token, ws, "admin")
    values = displayed_members(db, ws)
    if query and query.strip():
        values = [m for m in values if query.strip().casefold() in f"{m['username'] or ''} {m['display_name']}".casefold()]
    if status != "all": values = [m for m in values if (m["active"] and m["account_active"]) == (status == "active")]
    values.sort(key=lambda m: (m["display_name"].casefold(), m["user_id"]))
    return {"items": values[offset:offset+limit], "total": len(values), "limit": limit, "offset": offset}


@router.get("/api/v2/management/workspaces/{ws}/members/{user_id}/effective-access", response_model=EffectiveAccess)
def effective_access(ws: str, user_id: str, token=Depends(authenticate), db: Session = Depends(session)):
    from .auth import manager
    from .policies import permitted
    manager(db, token, ws, "admin")
    member = next((m for m in displayed_members(db, ws) if m["user_id"] == user_id), None)
    if not member: raise HTTPException(404, "Member unavailable")
    row = db.get(Workspace, ws)
    active = member["active"] and member["account_active"] and member["server_granted"] and row.status == "active"
    roles = member["roles"] if active else []
    def role(required): return active and ("admin" in roles or required in roles)
    permissions = [{"key": key, "label": label, "allowed": role(required)} for key, label, required in (
        ("members.manage", "管理空间成员", "admin"), ("functions.edit", "维护功能与智能体草稿", "maintainer"),
        ("releases.publish", "发布功能和智能体", "publisher"), ("logs.read", "查看空间运行日志", "admin"))]
    permissions.append({"key": "runtime.use", "label": "使用获准的智能体", "allowed": bool(active)})
    def resource(item, available):
        allowed = bool(active and available and permitted(item, user_id))
        return {"id": item.id, "name": item.definition["name"] if isinstance(item, Function) else item.name, "allowed": allowed,
                "reason": "按当前空间角色及资源授权可访问；具体对象仍在执行时校验" if allowed else
                "成员或账号已停用" if not active else "功能已停用或尚未发布" if not available else "不在该资源允许名单中"}
    functions = list(db.scalars(select(Function).where(Function.workspace_id == ws).order_by(Function.definition["name"].as_string(), Function.id)))
    agents = list(db.scalars(select(Agent).where(Agent.workspace_id == ws).order_by(Agent.name, Agent.id)))
    return {"member": member, "workspace": {"id": ws, "name": row.name, "status": row.status}, "roles": roles,
            "permissions": permissions, "agents": [resource(a, bool(a.current_release_id)) for a in agents],
            "functions": [resource(f, bool(f.latest_version) and not f.disabled) for f in functions]}
