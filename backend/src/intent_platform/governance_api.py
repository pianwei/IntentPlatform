import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import session
from .models import Membership, WorkspaceGovernance, ProcessingAttempt, Run, Agent, Release
from .auth import authenticate, manager, authorize, audit
from .services import get_resource, owned_run, agent_view
from .api_models import AgentOut
from .trust import integration_catalog, trusted_members, member_ceiling, member_authority
from .config import settings
from .capacity import ceiling, effective_limits, usage, lock_workspace, attempt_view
from .governance_schemas import CatalogOut, MemberOut, MemberBody, LimitsOut, LimitsBody, AttemptOut, RevokeIntegrationBody

router = APIRouter()
PREFIX = "/api/v2/management/workspaces/{ws}"


@router.get(PREFIX + "/catalogs", response_model=CatalogOut)
def catalogs(ws: str, token=Depends(authenticate), db: Session = Depends(session)):
    authorize(db, token, ws, ["maintainer", "publisher"])
    integrations = [{"ref": ref, **{k: p[k] for k in ("label", "version", "channels")}, "fixture_mode": p.get("fixture_mode", False)} for ref, p in integration_catalog(ws).items()]
    resolvers = [{"ref": ref, "version": p["version"], "object_types": [p["entity_type"]]} for ref, p in json.loads(settings().resolvers_json).items() if ws in p.get("workspace_ids", [])]
    credentials = [{"ref": ref, "connector_kind": p["connector_kind"]} for ref, p in json.loads(settings().credentials_json).items() if isinstance(p, dict) and ws in p.get("workspace_ids", [])]
    return {"integrations": integrations, "resolvers": resolvers, "credentials": credentials}


def member_view(ws, user, row):
    grant = member_ceiling(ws, user, row)
    roles, active = member_authority(row)
    return {"user_id": user, "roles": roles, "active": active, "revision": row.revision if row else 0,
            "ceiling_roles": grant["roles"], "server_granted": grant["active"]}


@router.get(PREFIX + "/members", response_model=list[MemberOut])
def members(ws: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "admin")
    rows = {m.user_id: m for m in db.scalars(select(Membership).where(Membership.workspace_id == ws))}
    return [member_view(ws, user, rows.get(user)) for user in sorted(set(rows) | set(trusted_members(ws) or {}))]


@router.put(PREFIX + "/members/{user_id}", response_model=MemberOut)
def update_member(ws: str, user_id: str, body: MemberBody, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "admin")
    if user_id == token.user_id: raise HTTPException(403, "Self authority changes are not permitted")
    lock_workspace(db, ws)
    row = db.scalar(select(Membership).where(Membership.workspace_id == ws, Membership.user_id == user_id).with_for_update())
    if body.revision != (row.revision if row else 0): raise HTTPException(409, "Member revision changed")
    grant = member_ceiling(ws, user_id, row)
    if not grant["active"] or set(body.roles) - set(grant["roles"]): raise HTTPException(403, "Requested authority exceeds the trusted member ceiling")
    if row is None:
        row = Membership(workspace_id=ws, user_id=user_id, role_ceiling=grant["roles"]); db.add(row)
    else: row.revision += 1
    row.roles = body.roles; row.active = body.active; db.flush()
    audit(db, token.user_id, ws, "member.update", user_id, {"roles": body.roles, "active": body.active, "revision": row.revision})
    result = member_view(ws, user_id, row); db.commit(); return result


def limits_view(db, ws):
    row = db.get(WorkspaceGovernance, ws)
    return {"revision": row.revision if row else 0, "limits": effective_limits(db, ws), "ceiling": ceiling(ws), "usage": usage(db, ws)}


@router.get(PREFIX + "/limits", response_model=LimitsOut)
def get_limits(ws: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "admin"); return limits_view(db, ws)


@router.put(PREFIX + "/limits", response_model=LimitsOut)
def update_limits(ws: str, body: LimitsBody, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "admin"); lock_workspace(db, ws)
    row = db.get(WorkspaceGovernance, ws)
    if body.revision != (row.revision if row else 0): raise HTTPException(409, "Capacity revision changed")
    values = body.limits.model_dump()
    if any(value > ceiling(ws)[key] for key, value in values.items()): raise HTTPException(403, "Capacity exceeds the server ceiling")
    if row is None: row = WorkspaceGovernance(workspace_id=ws, limits=values); db.add(row)
    else: row.limits = values; row.revision += 1
    db.flush(); audit(db, token.user_id, ws, "limits.update", ws, {"limits": values, "revision": row.revision})
    result = limits_view(db, ws); db.commit(); return result


@router.post(PREFIX + "/agents/{aid}/revoke-integration", response_model=AgentOut)
def revoke_integration(ws: str, aid: str, body: RevokeIntegrationBody, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "publisher"); agent = get_resource(db, Agent, aid, ws, lock=True)
    agent.revoked_integration_refs = sorted(set(agent.revoked_integration_refs) | {body.integration_ref})
    audit(db, token.user_id, ws, "agent.revoke_integration", aid, {"integration_ref": body.integration_ref})
    result = agent_view(agent, db); db.commit(); return result


def attempts(db, run):
    return [attempt_view(row) for row in db.scalars(select(ProcessingAttempt).where(ProcessingAttempt.run_id == run.id).order_by(ProcessingAttempt.started_at, ProcessingAttempt.id))]


@router.get(PREFIX + "/runs/{run_id}/attempts", response_model=list[AttemptOut])
def management_attempts(ws: str, run_id: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "admin")
    return [attempt_view(row) for row in db.scalars(select(ProcessingAttempt).where(ProcessingAttempt.workspace_id == ws, ProcessingAttempt.run_id == run_id).order_by(ProcessingAttempt.started_at, ProcessingAttempt.id))]


@router.get("/api/v2/runtime/runs/{run_id}/attempts", response_model=list[AttemptOut])
def runtime_attempts(run_id: str, token=Depends(authenticate), db: Session = Depends(session)):
    if token.scope != "runtime": raise HTTPException(403, "Scoped runtime session required")
    return attempts(db, owned_run(db, run_id, token))
