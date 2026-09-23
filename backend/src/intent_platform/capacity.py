"""Workspace-wide admission and leases, coordinated by PostgreSQL transactions."""
import json
from sqlalchemy import select, func, text
from fastapi import HTTPException
from .config import settings
from .db import now
from .models import WorkspaceGovernance, ProcessingAttempt, Run
from .governance_schemas import Limits


def lock_workspace(db, ws):
    db.execute(text("SELECT pg_advisory_xact_lock(9102213, hashtext(:ws))"), {"ws": ws})


def ceiling(ws):
    raw = json.loads(settings().workspace_limits_json)
    if not isinstance(raw, dict): raise ValueError("Invalid server capacity ceiling")
    return Limits.model_validate(raw.get(ws, {})).model_dump()


def effective_limits(db, ws):
    maximum = ceiling(ws); saved = db.get(WorkspaceGovernance, ws)
    configured = saved.limits if saved else maximum
    return {key: min(configured.get(key, value), value) for key, value in maximum.items()}


def usage(db, ws):
    active = list(db.scalars(select(ProcessingAttempt.kind).where(ProcessingAttempt.workspace_id == ws, ProcessingAttempt.status == "active", ProcessingAttempt.lease_until > now())))
    return {"queued_runs": db.execute(text("SELECT intent_queue_count(:ws)"), {"ws": ws}).scalar(),
            "routing": active.count("routing"), "execution": sum(kind in {"execution", "reconciliation"} for kind in active)}


def admit(db, ws, excluding_conversation=None):
    lock_workspace(db, ws)
    pending = db.execute(text("SELECT intent_queue_count(:ws,:excluded)"), {"ws": ws, "excluded": excluding_conversation}).scalar()
    if pending >= effective_limits(db, ws)["max_queued_runs"]:
        raise HTTPException(429, "Workspace pending queue is full", headers={"Retry-After": "1"})


def claim_attempt(db, run, kind, lease_until, call_id=None):
    lock_workspace(db, run.workspace_id)
    # Expired leases cease consuming capacity without inventing successful durations.
    for row in db.scalars(select(ProcessingAttempt).where(ProcessingAttempt.workspace_id == run.workspace_id, ProcessingAttempt.status == "active", ProcessingAttempt.lease_until <= now()).with_for_update()):
        row.status = "lease_expired"; row.error_stage = "lease"
    db.flush()
    key = "routing" if kind == "routing" else "execution"
    if usage(db, run.workspace_id)[key] >= effective_limits(db, run.workspace_id)["max_" + key]: return None
    stamp = now()
    queued = run.queued_at if kind == "routing" else None
    row = ProcessingAttempt(workspace_id=run.workspace_id, run_id=run.id, revision=run.revision, call_id=call_id, kind=kind,
                            queued_at=queued, started_at=stamp, lease_until=lease_until,
                            timings={"queue_ms": max(0.0, (stamp - queued).total_seconds() * 1000)} if kind == "routing" else {})
    db.add(row); db.flush(); return row


def attempt_view(row):
    return {key: getattr(row, key) for key in ("id", "run_id", "revision", "kind", "call_id", "status", "queued_at", "started_at", "finished_at", "lease_until", "error_stage")} | row.timings
