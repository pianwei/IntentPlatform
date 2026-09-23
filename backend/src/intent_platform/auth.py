import secrets
from datetime import timedelta
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from .config import settings
from .db import now, session, set_identity
from .models import AuthToken, Membership, Workspace, Audit, Agent, Release, RuntimeSession
from .policies import permitted, effective_channels


def token_valid(db, token):
    if not token or token.revoked or token.expires_at <= now():
        return False
    from .trust import token_integration, integration_allowed, member_authority
    try:
        ref = token_integration(token)
        if token.scope == "management" and token.source == "introspection":
            from .trust import integration_catalog
            members = db.scalars(select(Membership).where(Membership.user_id == token.user_id)).all()
            if not any(member_authority(m)[1] and token.user_id in integration_catalog(m.workspace_id).get(ref, {}).get("user_ids", []) and m.workspace_id in token.delegation["workspace_ids"] for m in members):
                return False
    except (ValueError, TypeError):
        return False
    if token.parent_token_id:
        parent = db.get(AuthToken, token.parent_token_id)
        if not parent or parent.revoked or parent.expires_at <= now():
            return False
    if token.scope == "runtime":
        set_identity(db, token.user_id)
        a = db.get(Agent, token.agent_id)
        r = db.get(Release, a.current_release_id) if a else None
        runtime = db.get(RuntimeSession, token.runtime_session_id) if token.runtime_session_id else None
        if not a or not r or not runtime or runtime.revoked or runtime.user_id != token.user_id or runtime.agent_id != a.id or not permitted(a, token.user_id):
            return False
        if not member_authority(db.get(Membership, (token.user_id, a.workspace_id)))[1]:
            return False
        if not runtime.integration_id or not integration_allowed(a, r, token, runtime.channel, runtime.host_origin, runtime.integration_id, {"identity_policy": runtime.identity_policy}):
            return False
        if not effective_channels(a, r).get(runtime.channel):
            return False
        if runtime.channel == "embed" and (runtime.host_origin not in r.snapshot["allowed_origins"] or runtime.host_origin in a.revoked_origins):
            return False
    return True


def authenticate(authorization: str | None = Header(default=None), db: Session = Depends(session)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    token = db.get(AuthToken, authorization[7:])
    if token:
        set_identity(db, token.user_id)
    if not token_valid(db, token):
        raise HTTPException(401, "Session expired or revoked")
    set_identity(db, token.user_id)
    return token


def authorize(db, token, workspace_id, roles=None):
    if not token_valid(db, token):
        raise HTTPException(401, "Session expired or revoked")
    member = db.get(Membership, (token.user_id, workspace_id))
    from .trust import member_authority, identity_workspace_allowed
    effective_roles, active = member_authority(member)
    if not active or not identity_workspace_allowed(token, workspace_id):
        raise HTTPException(403, "Resource unavailable")
    if roles:
        if token.scope != "management":
            raise HTTPException(403, "Runtime token cannot access management APIs")
        if "admin" not in effective_roles and not set(roles).intersection(effective_roles):
            raise HTTPException(403, "Insufficient workspace role")
    return member


def manager(db, token, workspace_id, role="maintainer"):
    return authorize(db, token, workspace_id, [role])


def issue_token(db, user_id, scope="management", agent_id=None, source="development", delegation=None, parent=None, runtime_session_id=None):
    token = AuthToken(id=secrets.token_urlsafe(36), user_id=user_id, scope=scope, agent_id=agent_id,
                      expires_at=now() + timedelta(seconds=settings().token_seconds), source=source,
                      delegation=delegation or {}, parent_token_id=parent, runtime_session_id=runtime_session_id)
    db.add(token); db.flush()
    return token


def workspaces(db, token):
    rows = db.execute(select(Workspace, Membership).join(Membership, Workspace.id == Membership.workspace_id)
                      .where(Membership.user_id == token.user_id, Membership.active.is_(True))).all()
    from .trust import member_authority, identity_workspace_allowed
    return [{"id": ws.id, "name": ws.name, "roles": member_authority(member)[0]} for ws, member in rows if member_authority(member)[1] and identity_workspace_allowed(token, ws.id)]


def audit(db, user_id, workspace_id, action, resource_id, detail=None):
    db.add(Audit(user_id=user_id, workspace_id=workspace_id, action=action, resource_id=resource_id, detail=detail or {}))


def bootstrap_identity(db, user_id):
    if not settings().allow_dev_auth or settings().env == "production":
        raise HTTPException(404, "Not found")
    users = {"dev-admin": ("ws-dev", ["admin", "maintainer", "publisher", "user"]),
             "dev-reader": ("ws-dev", ["user"]), "dev-other": ("ws-other", ["admin", "maintainer", "publisher", "user"])}
    if user_id not in users:
        raise HTTPException(400, "Use dev-admin, dev-reader or dev-other")
    ws, roles = users[user_id]
    # Serialize concurrent first-login bootstrap without granting database superuser rights.
    from sqlalchemy import text
    db.execute(text("SELECT pg_advisory_xact_lock(9102201)"))
    if not db.get(Workspace, ws):
        db.add(Workspace(id=ws, name="开发夹具空间" if ws == "ws-dev" else "开发隔离空间")); db.flush()
    if not db.get(Membership, (user_id, ws)):
        db.add(Membership(user_id=user_id, workspace_id=ws, roles=roles)); db.flush()
    set_identity(db, user_id)
    from .trust import member_authority
    roles, active = member_authority(db.get(Membership, (user_id, ws)))
    if not active: raise HTTPException(403, "Development membership is disabled by current authority")
    return issue_token(db, user_id), ws, roles
