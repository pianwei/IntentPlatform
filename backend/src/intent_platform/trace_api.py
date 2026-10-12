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
from .models import ExecutionPlan, ManagementTrace, ProcessingAttempt, PlanStep, Function, BusinessSkillVersion, Release
from .schemas import StrictModel
from .services import _approved_data, _display_value, _runtime_steps


router = APIRouter()
PREFIX = "/api/v2/management/workspaces/{ws}/runs"
LAYERS = ("entity", "dynamics", "planning", "execution")
DETAIL_KEYS = {
    "entity": {"goal", "fields", "kind", "count", "candidate_count", "object_types", "bound_object_type", "binding_source", "input_fields", "source", "entities", "actions", "negated", "step", "bindings"},
    "dynamics": {"step", "name", "count", "candidate_count", "effect", "actions", "status", "validation", "capability_ids", "semantic", "parameters", "preconditions", "entity_binding", "error_type"},
    "planning": {"stage", "reason_code", "error_type", "http_status", "schema_paths", "source", "skill_version_id", "steps", "step", "validation", "status"},
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
    title: str = "处理记录"
    display: list[dict[str, Any]] = Field(default_factory=list)

TRACE_TITLES = {"goal_recognized":"理解业务目标", "goal_parsed":"识别业务对象", "objects_bound":"确认业务对象",
 "missing_input":"需要补充参数", "input_supplied":"收到补充参数", "ambiguous":"发现同名对象", "object_not_found":"未找到业务对象",
 "candidate_filtered":"筛选可用功能", "actions_selected":"选择业务动作", "step_validated":"执行前检查", "step_rejected":"执行前检查未通过",
 "confirmation_required":"等待写入确认", "step_dispatched":"调用业务功能", "plan_approved":"计划校验通过", "plan_rejected":"计划未通过校验",
 "clarification_requested":"请求澄清目标", "dependency_blocked":"前置步骤未完成", "reference_unavailable":"所需结果不可用",
 "step_skipped":"条件不满足，跳过步骤", "answer_completed":"完成答复汇总", "step_finished":"业务步骤完成", "status_reconciled":"完成状态核对",
 "model_answer_completed":"模型直接回答完成", "model_answer_failed":"模型回答未完成"}
TRACE_LABELS = {"goal":"业务目标","fields":"参数","input_fields":"输入参数","count":"数量","candidate_count":"候选数量",
 "object_types":"对象类型","entities":"识别对象","bound_object_type":"绑定对象类型","binding_source":"对象来源","source":"处理方式",
 "actions":"业务动作","negated":"排除条件","step":"业务步骤","steps":"计划步骤","bindings":"对象绑定",
 "name":"功能名称","effect":"操作类型","status":"状态","validation":"校验结果","capability_ids":"候选功能",
 "semantic":"关系与单位校验","parameters":"参数校验","preconditions":"前置条件","entity_binding":"对象授权绑定",
 "skill_version_id":"组合规则","stage":"处理阶段","reason_code":"未通过原因","error_type":"错误类别",
 "schema_paths":"问题位置","call_count":"调用次数","error_stage":"异常阶段","http_status":"HTTP状态码","kind":"对象种类",
 "type":"对象类型","mention":"用户提及","history_ref":"历史引用","parameter":"参数","role":"业务角色"}
TRACE_VALUES = {"skill":"业务规则", "model":"模型规划", "model_answer":"模型直接回答", "practice":"模拟陪练", "preview":"工程预览", "passed":"通过", "READ":"查询", "WRITE":"写入",
 "context_validation":"上下文校验", "classification":"意图分类", "answer_generation":"生成回答",
 "practice_state_update":"更新陪练状态", "result_persistence":"保存结果",
 "MODEL_AUTH_REJECTED":"模型认证失败", "MODEL_QUOTA_EXHAUSTED":"模型额度不足", "MODEL_ACCESS_FORBIDDEN":"模型访问受限",
 "MODEL_RATE_LIMITED":"模型请求被限流", "MODEL_REMOTE_FAILURE":"模型服务异常", "MODEL_REQUEST_REJECTED":"模型请求被拒绝",
 "MODEL_TIMEOUT":"模型请求超时", "MODEL_NETWORK_ERROR":"网络连接失败", "MODEL_INVALID_RESPONSE":"模型响应格式无效",
 "MODEL_SYNTHETIC_CATALOG_UNAVAILABLE":"模拟规则文件不可用", "MODEL_SYNTHETIC_CATALOG_REJECTED":"模拟目录校验未通过",
 "MODEL_CONFIGURATION_UNAVAILABLE":"模型配置或凭据不可用", "MODEL_PROCESSING_ERROR":"模型回答处理失败",
 "SUCCEEDED":"已完成","FAILED":"失败","UNKNOWN":"待核对","SKIPPED":"已跳过","PENDING":"待执行","EXECUTING":"执行中",
 "resolver":"授权对象检索", "history":"会话历史", "user_input_or_prior_result":"用户输入或前一步结果",
 "parameter":"参数","role":"业务角色", "source":"来源", "lookup":"查询", "query":"查询", "aggregate":"统计",
 "target":"操作对象","owner":"归属对象","subject":"主体对象",
 "customer_id":"客户编号","account_id":"账户编号","transaction_id":"交易编号","loan_id":"合同编号",
 "product_id":"产品编号","ticket_id":"工单编号","start_date":"开始日期","end_date":"结束日期","reason":"原因"}

def display_entry(item, names):
    def readable(value):
        if isinstance(value, list): return [readable(v) for v in value[:40]]
        if isinstance(value, dict): return {TRACE_LABELS.get(k, TRACE_VALUES.get(k,k)):readable(v) for k,v in value.items() if not k.endswith("_id")}
        if isinstance(value, str):
            if value in names: return names[value]
            if value in TRACE_VALUES: return TRACE_VALUES[value]
            if value.startswith(("fv_","fn_","skillv_","bsv_","agent_","run_")): return "已发布版本"
        return value
    item["title"] = TRACE_TITLES.get(item["kind"], "阶段处理记录")
    item["display"] = [{"label":TRACE_LABELS[key],"value":readable(value)} for key,value in item["detail"].items() if key in TRACE_LABELS and value is not None]
    return item


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
    manager(db, token, ws, "admin", archived_history=True)
    rows = db.execute(text("""SELECT * FROM intent_admin_trace_runs(
        CAST(:ws AS text), CAST(:agent AS text), CAST(:status AS text), CAST(:run AS text),
        CAST(:after AS timestamptz), CAST(:before AS timestamptz), CAST(:limit AS integer))"""),
        {"ws": ws, "agent": agent_id, "status": status, "run": run_id,
         "after": created_after, "before": created_before, "limit": limit}).mappings().all()
    return [_row(row) for row in rows]


@router.get(PREFIX + "/{run_id}/trace", response_model=TraceDetail)
def run_trace(ws: str, run_id: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "admin", archived_history=True)
    row = db.execute(text("SELECT * FROM intent_admin_trace_detail(CAST(:ws AS text), CAST(:run AS text))"),
                     {"ws": ws, "run": run_id}).mappings().first()
    if not row:
        raise HTTPException(404, "Run trace unavailable")
    layers = {layer: [] for layer in LAYERS}
    plan = db.scalar(select(ExecutionPlan).where(ExecutionPlan.workspace_id == ws, ExecutionPlan.run_id == run_id))
    names = {f.id: f.definition.get("name", "业务功能") for f in db.scalars(select(Function).where(Function.workspace_id==ws))}
    if plan:
        release = db.get(Release, plan.release_id)
        projection = release.snapshot.get("agent_projection", {}) if release else {}
        for entity in projection.get("entities", []):
            names[entity["id"]] = entity.get("label", entity["id"])
    for step in db.scalars(select(PlanStep).join(ExecutionPlan,ExecutionPlan.id==PlanStep.plan_id).where(ExecutionPlan.run_id==run_id)):
        names[step.step_key] = step.definition.get("name", "业务步骤")
        for key, field in step.definition.get("input_schema", {}).get("properties", {}).items():
            if isinstance(field, dict) and field.get("title"):
                names[key] = str(field["title"])[:120]
    for skill in db.scalars(select(BusinessSkillVersion).where(BusinessSkillVersion.workspace_id==ws)):
        names[skill.id] = skill.definition.get("name", "组合规则")
    for item in db.scalars(select(ManagementTrace).where(ManagementTrace.workspace_id == ws,
                                                        ManagementTrace.run_id == run_id)
                           .order_by(ManagementTrace.created_at, ManagementTrace.id)):
        if item.layer in layers:
            layers[item.layer].append(display_entry(_trace_entry(item), names))
    attempt_rows = list(db.scalars(select(ProcessingAttempt).where(
        ProcessingAttempt.workspace_id == ws, ProcessingAttempt.run_id == run_id)
        .order_by(ProcessingAttempt.started_at, ProcessingAttempt.id)))
    steps = _runtime_steps(db, SimpleNamespace(id=run_id)) if plan else _legacy_steps(row, attempt_rows)
    return {"run": _row(row), "layers": layers, "steps": steps,
            "timing": _admin_timing(row), "attempts": [attempt_view(item) for item in attempt_rows],
            "plan_source": plan.source if plan else None}
