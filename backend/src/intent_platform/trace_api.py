"""Administrator-only three-layer trace, separate from runtime events."""

from datetime import datetime
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .api_models import RuntimeStepSummary, RuntimeTimingSummary
from .auth import authenticate, manager
from .capacity import attempt_view
from .db import session
from .governance_schemas import AttemptOut
from .models import ExecutionPlan, ManagementTrace, ProcessingAttempt
from .schemas import StrictModel
from .services import _approved_data, _display_value, _runtime_steps


router = APIRouter()
PREFIX = "/api/v2/management/workspaces/{ws}/runs"
LAYERS = ("entity", "dynamics", "planning", "execution")
DETAIL_KEYS = {
    "entity": {"fields", "kind", "count", "candidate_count", "object_types", "bound_object_type", "binding_source", "input_fields", "source"},
    "dynamics": {"step", "name", "count", "effect", "actions", "status", "validation"},
    "planning": {"source", "skill_version_id", "steps", "step", "validation", "status"},
    "execution": {"step", "status", "call_count", "error_stage"},
}


class RunTraceRow(StrictModel):
    run_id: str
    agent_id: str
    status: str
    decision: str | None
    created_at: datetime
    finished_at: datetime | None
    elapsed_ms: float | None


class TraceEntry(StrictModel):
    layer: str
    kind: str
    detail: dict[str, Any]
    created_at: datetime


class TraceDetail(StrictModel):
    run: RunTraceRow
    layers: dict[str, list[TraceEntry]]
    steps: list[RuntimeStepSummary]
    timing: RuntimeTimingSummary
    attempts: list[AttemptOut]
    plan_source: str | None


def _row(row):
    elapsed = (round(max(0.0, (row["finished_at"] - row["created_at"]).total_seconds() * 1000), 2)
               if row["finished_at"] else None)
    return {"run_id": row["run_id"], "agent_id": row["agent_id"], "status": row["status"],
            "decision": row["decision"], "created_at": row["created_at"],
            "finished_at": row["finished_at"], "elapsed_ms": elapsed}


def _trace_entry(row):
    allowed = DETAIL_KEYS[row.layer]
    detail = {key: _display_value(value, key) for key, value in (row.detail or {}).items() if key in allowed}
    return {"layer": row.layer, "kind": row.kind[:80], "detail": detail, "created_at": row.created_at}


def _admin_timing(row):
    """Use only bounded STATUS timestamps returned by the checked definer view."""
    stamps = row["status_events"] or []
    waiting_start = None
    waiting_ms = 0.0
    for stamp in stamps:
        created = datetime.fromisoformat(stamp["created_at"])
        status = stamp.get("status")
        if status in {"WAITING_INPUT", "WAITING_CONFIRMATION"} and waiting_start is None:
            waiting_start = created
        elif status not in {"WAITING_INPUT", "WAITING_CONFIRMATION"} and waiting_start is not None:
            waiting_ms += max(0.0, (created - waiting_start).total_seconds() * 1000)
            waiting_start = None
    if waiting_start is not None and row["finished_at"]:
        waiting_ms += max(0.0, (row["finished_at"] - waiting_start).total_seconds() * 1000)
    total = _row(row)["elapsed_ms"]
    observed_wait = round(waiting_ms, 2) if stamps and waiting_start is None else None
    return {"total_ms": total, "user_wait_ms": observed_wait,
            "processing_ms": round(max(0.0, total - observed_wait), 2)
            if total is not None and observed_wait is not None else None}


def _legacy_steps(row, attempts):
    """Project owner-scoped legacy calls without returning their raw ledger data."""
    steps = []
    for call in row["calls"] or []:
        definition = call["definition"]
        arguments = call.get("arguments") or {}
        related = [attempt for attempt in attempts if attempt.call_id == call["id"]]
        started = next((attempt.started_at for attempt in related if attempt.started_at), None)
        finished = next((attempt.finished_at for attempt in reversed(related) if attempt.finished_at), None)
        steps.append({"name": definition["name"], "status": call["status"],
                      "input": {key: _display_value(value, key) for key, value in arguments.items()
                                if key in definition["input_schema"].get("properties", {})},
                      "output": _approved_data(definition, (call.get("result") or {}).get("data")),
                      "elapsed_ms": round((finished - started).total_seconds() * 1000, 2)
                      if started and finished else None})
    return steps


@router.get(PREFIX, response_model=list[RunTraceRow])
def list_traces(ws: str, agent_id: str | None = None, status: str | None = None,
                run_id: str | None = None, created_after: datetime | None = None,
                created_before: datetime | None = None, limit: int = Query(default=50, ge=1, le=100),
                token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "admin")
    rows = db.execute(text("""SELECT * FROM intent_admin_trace_runs(
        CAST(:ws AS text), CAST(:agent AS text), CAST(:status AS text), CAST(:run AS text),
        CAST(:after AS timestamptz), CAST(:before AS timestamptz), CAST(:limit AS integer))"""),
        {"ws": ws, "agent": agent_id, "status": status, "run": run_id,
         "after": created_after, "before": created_before, "limit": limit}).mappings().all()
    return [_row(row) for row in rows]


@router.get(PREFIX + "/{run_id}/trace", response_model=TraceDetail)
def run_trace(ws: str, run_id: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "admin")
    row = db.execute(text("SELECT * FROM intent_admin_trace_detail(CAST(:ws AS text), CAST(:run AS text))"),
                     {"ws": ws, "run": run_id}).mappings().first()
    if not row:
        raise HTTPException(404, "Run trace unavailable")
    layers = {layer: [] for layer in LAYERS}
    for item in db.scalars(select(ManagementTrace).where(ManagementTrace.workspace_id == ws,
                                                        ManagementTrace.run_id == run_id)
                           .order_by(ManagementTrace.created_at, ManagementTrace.id)):
        if item.layer in layers:
            layers[item.layer].append(_trace_entry(item))
    plan = db.scalar(select(ExecutionPlan).where(ExecutionPlan.workspace_id == ws, ExecutionPlan.run_id == run_id))
    attempt_rows = list(db.scalars(select(ProcessingAttempt).where(
        ProcessingAttempt.workspace_id == ws, ProcessingAttempt.run_id == run_id)
        .order_by(ProcessingAttempt.started_at, ProcessingAttempt.id)))
    steps = _runtime_steps(db, SimpleNamespace(id=run_id)) if plan else _legacy_steps(row, attempt_rows)
    return {"run": _row(row), "layers": layers, "steps": steps,
            "timing": _admin_timing(row), "attempts": [attempt_view(item) for item in attempt_rows],
            "plan_source": plan.source if plan else None}
