"""Conversation-oriented management projection, never used to authorize runtime access."""
import re
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .auth import authenticate, manager
from .db import session
from .models import ManagementTrace, Event, FunctionVersion, ExecutionPlan, PlanStep
from .api_models import RuntimeTimingSummary
from .schemas import StrictModel
from .services import _approved_data, _display_value, event_view, business_answer_text
from .trace_api import _admin_timing

router = APIRouter()
PREFIX = "/api/v2/management/workspaces/{ws}/conversations"

def safe_text(value, limit=6000):
    value = str(value or "")[:limit]
    return re.sub(r"(?i)(?:sk-[a-z0-9_-]{12,}|(?:api[_ -]?key|password|authorization)\s*[:=]\s*[^\s,;]+)", "[已隐藏凭据]", value)

def record_interaction(db, run, kind, payload):
    """Called after validation, before clearing the current question, in the same transaction."""
    if kind == "answer":
        question = run.question or {}
        labels = {f["name"]: f.get("label") or f["name"] for f in question.get("fields", [])}
        supplied = payload.get("values", {})
        detail = {"revision": run.revision, "kind": question.get("kind", "arguments"),
                  "values": {str(labels[k]): _display_value(v, k) for k, v in supplied.items() if k in labels}}
        option = next((o for o in question.get("options", []) if o.get("id") == payload.get("option_id")), None)
        if option: detail["selection"] = safe_text(option.get("label", "已选择对象"), 240)
    elif kind == "confirmation":
        detail = {"revision": run.revision, "confirmed": bool(payload.get("confirmed"))}
    else:
        return
    db.add(ManagementTrace(workspace_id=run.workspace_id, run_id=run.id, layer="interaction", kind=kind, detail=detail))

class ConversationRow(StrictModel):
    id: str
    agent_id: str
    agent_name: str
    title: str
    created_at: datetime
    updated_at: datetime
    turn_count: int
    status: str

class ConversationPage(StrictModel):
    items: list[ConversationRow]
    total: int
    offset: int
    limit: int

class ConversationTurn(StrictModel):
    run_id: str
    question: str
    status: str
    revision: int
    created_at: datetime
    finished_at: datetime | None
    timing: RuntimeTimingSummary
    results: list[dict[str, Any]]
    prompts: list[dict[str, Any]]
    interactions: list[dict[str, Any]]
    phases: list[dict[str, Any]]

class ConversationDetail(StrictModel):
    id: str
    title: str
    agent_id: str
    agent_name: str
    turn_count: int
    turns: list[ConversationTurn]
    offset: int
    limit: int

def confirmation_projection(db, ws, run_id, payload):
    actions = []
    source = payload.get("actions")
    if isinstance(source, list):
        for item in source[:8]:
            if not isinstance(item, dict): continue
            version = db.scalar(select(FunctionVersion).where(FunctionVersion.workspace_id == ws,
                FunctionVersion.function_id == item.get("capability_id"), FunctionVersion.version == item.get("version")))
            if version: actions.append((version.definition, item.get("arguments")))
    else:
        step = db.scalar(select(PlanStep).join(ExecutionPlan, ExecutionPlan.id == PlanStep.plan_id).where(
            ExecutionPlan.workspace_id == ws, ExecutionPlan.run_id == run_id, PlanStep.definition["effect"].as_string() == "WRITE"))
        if step: actions.append((step.definition, payload.get("arguments")))
    return {"message": "执行前确认", "actions": [{"name": safe_text(d.get("name", "业务操作"),160),
        "arguments": {k: _display_value(v,k) for k,v in (args if isinstance(args,dict) else {}).items()
                      if k in d.get("input_schema",{}).get("properties",{})}} for d,args in actions]}

@router.get(PREFIX, response_model=ConversationPage)
def conversation_list(ws: str, query: str | None = Query(default=None, max_length=200), agent_id: str | None = None,
                      status: str | None = None, created_after: datetime | None = None, created_before: datetime | None = None,
                      limit: int = Query(default=25, ge=1, le=50), offset: int = Query(default=0, ge=0, le=100000),
                      token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "admin", archived_history=True)
    if created_after and created_before and created_after > created_before:
        raise HTTPException(422, "起始时间不能晚于结束时间")
    result = db.scalar(text("""SELECT intent_admin_conversations(CAST(:ws AS text),CAST(:agent AS text),
        CAST(:status AS text),CAST(:query AS text),CAST(:after AS timestamptz),CAST(:before AS timestamptz),:limit,:offset)"""),
        {"ws": ws, "agent": agent_id, "status": status, "query": query or None, "after": created_after,
         "before": created_before, "limit": limit, "offset": offset})
    return {**result, "items": [{**r, "title": safe_text(r["title"], 60)} for r in result["items"]], "offset": offset, "limit": limit}

def raw_detail(db, ws, cid, limit=50, offset=0, run_id=None):
    result = db.scalar(text("SELECT intent_admin_conversation_detail(:ws,:cid,:limit,:offset,CAST(:rid AS text))"),
                       {"ws": ws, "cid": cid, "limit": limit, "offset": offset, "rid": run_id})
    if not result: raise HTTPException(404, "会话记录不可用")
    return result

def project_results(row):
    events = sorted(row["events"], key=lambda e: e["sequence"])
    calls = {c["id"]: c for c in row["calls"]}
    finals = [e for e in events if e["event_type"] == "RESULT" and e["payload"].get("title") == "答复" and not e["payload"].get("call_id")]
    selected = finals[-1:] if finals else [] if row["has_plan"] else [e for e in events if e["event_type"] == "RESULT"]
    result = []
    for e in selected:
        p = e["payload"]; call = calls.get(p.get("call_id"))
        if call:
            d = call["definition"]; data = _approved_data(d, p.get("data")); presentation = d.get("presentation", {})
            allowed_files = {f.get("id"): f for f in (call.get("result") or {}).get("files", []) if isinstance(f, dict)}
            payload = {"component": presentation.get("component", "detail"), "title": presentation.get("title", "业务结果"),
                       "data": data, "fields": [{"label": label, "value": data.get(key)} for label, key in presentation.get("field_map", {}).items()] if isinstance(data, dict) else [],
                       "files": [{k: f[k] for k in ("id", "filename", "size", "media_type") if k in f} for f in allowed_files.values()][:20] if data is not None else []}
        elif isinstance(p.get("data"), str) and p.get("title") == "答复":
            payload = {"component": "text", "title": "答复", "data": safe_text(business_answer_text(p["data"]), 12000), "fields": [],
                       "components": [{"title": safe_text(c.get("title", "业务结果"), 160), "component": c["component"],
                                       "data": _display_value(c.get("data"), max_depth=5),
                                       "fields": [{"label": str(f.get("label", ""))[:80], "value": _display_value(f.get("value"), max_depth=5)} for f in c.get("fields", [])[:40] if isinstance(f, dict)]}
                                      for c in p.get("components", [])[:8] if isinstance(c, dict) and c.get("component") in {"detail", "table", "text", "markdown", "citation"}],
                       "files": [{k: f[k] for k in ("id", "filename", "size", "media_type") if k in f} for f in p.get("files", [])[:20] if isinstance(f, dict)]}
        else: continue
        result.append(payload)
    return result

@router.get(PREFIX + "/{cid}", response_model=ConversationDetail)
def conversation_detail(ws: str, cid: str, limit: int = Query(default=25, ge=1, le=50), offset: int = Query(default=0, ge=0, le=100000),
                        token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "admin", archived_history=True)
    result = raw_detail(db, ws, cid, limit, offset)
    turns = []
    for row in result["turns"]:
        traces = list(db.scalars(select(ManagementTrace).where(ManagementTrace.workspace_id == ws, ManagementTrace.run_id == row["run_id"])
                                .order_by(ManagementTrace.created_at, ManagementTrace.id)))
        timing_row = {**row, "run_id": row["run_id"], "agent_id": result["agent_id"], "decision": None,
                      "created_at": datetime.fromisoformat(row["created_at"]),
                      "finished_at": datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
                      "status_events": [{"created_at": e["created_at"], "status": e["payload"].get("status")} for e in sorted(row["events"], key=lambda e:e["sequence"]) if e["event_type"] == "STATUS"]}
        prompts = []
        for e in sorted(row["events"], key=lambda e:e["sequence"]):
            if e["event_type"] not in {"QUESTION", "CONFIRMATION", "ERROR"}: continue
            obj = Event(workspace_id=ws, conversation_id=cid, run_id=row["run_id"], sequence=e["sequence"], revision=row["revision"], event_type=e["event_type"], payload=e["payload"])
            projected = confirmation_projection(db,ws,row["run_id"],e["payload"]) if e["event_type"] == "CONFIRMATION" else event_view(obj)["payload"]
            prompts.append({"kind": e["event_type"], "created_at": e["created_at"], "payload": projected})
        interactions = [{"kind": t.kind, "created_at": t.created_at, "detail": {k: _display_value(v, k) for k, v in t.detail.items() if k in {"revision", "kind", "values", "selection", "confirmed"}}} for t in traces if t.layer == "interaction"]
        phases = [{"layer": layer, "count": sum(t.layer == layer for t in traces),
                   "status": "FAILED" if any(t.layer == layer and ("fail" in t.kind or "reject" in t.kind or t.detail.get("status") == "FAILED") for t in traces) else "RECORDED" if any(t.layer == layer for t in traces) else "UNRECORDED"}
                  for layer in ("entity", "dynamics", "planning", "execution")]
        turns.append({"run_id": row["run_id"], "question": safe_text(row["question"]), "status": row["status"], "revision": row["revision"],
                      "created_at": row["created_at"], "finished_at": row["finished_at"], "timing": _admin_timing(timing_row),
                      "results": project_results(row), "prompts": prompts, "interactions": interactions, "phases": phases})
    return {"id": cid, "title": safe_text(result["title"],60), "agent_id": result["agent_id"], "agent_name": result["agent_name"],
            "turn_count": result["turn_count"], "turns": turns, "offset": offset, "limit": limit}

@router.get(PREFIX + "/{cid}/files/{file_id}")
def log_file(ws: str, cid: str, file_id: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "admin", archived_history=True)
    f = db.execute(text("SELECT * FROM intent_admin_log_file(:ws,:cid,:fid)"), {"ws": ws, "cid": cid, "fid": file_id}).mappings().first()
    if not f: raise HTTPException(404, "文件不可用")
    # A management download must also be referenced by an approved final result.
    detail = raw_detail(db, ws, cid, run_id=f["run_id"])
    allowed = any(file_id == item.get("id") for turn in detail["turns"] for result in project_results(turn) for item in result.get("files", []))
    if not allowed: raise HTTPException(404, "文件不在当前最终结果中")
    from .application_api import binary_response
    return binary_response(f["data"], f["media_type"], f["filename"])
