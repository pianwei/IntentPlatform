"""Server-owned simulation context. A persona never grants platform authority."""
from fastapi import HTTPException
from .models import Conversation, RuntimeSession, SyntheticState

CATALOG_ID = "zhiyu-v1"
PERSONAS = [
    {"id": "manager_a", "name": "客户经理甲", "description": "查看本人管户客户和业绩"},
    {"id": "manager_b", "name": "客户经理乙", "description": "查看另一组管户客户和业绩"},
    {"id": "supervisor", "name": "模拟主管", "description": "查看辖内客户、业绩与排名"},
]


def catalog_metadata():
    return {"id": CATALOG_ID, "version": 1, "personas": PERSONAS,
            "default_persona": "manager_a", "model_answer_policy": "general-v1"}


def validate_persona(value, snapshot):
    if (snapshot.get("synthetic_catalog") or {}).get("id") != CATALOG_ID:
        if value is not None: raise HTTPException(422, "此应用不支持模拟角色")
        return None
    value = value or "manager_a"
    if value not in {item["id"] for item in PERSONAS}:
        raise HTTPException(422, "未知模拟角色")
    return value


def persona_for_run(db, run):
    conv = db.get(Conversation, run.conversation_id)
    runtime = db.get(RuntimeSession, conv.runtime_session_id) if conv and conv.runtime_session_id else None
    if not runtime or runtime.revoked or runtime.user_id != run.user_id or runtime.workspace_id != run.workspace_id:
        raise PermissionError("A current scoped synthetic session is required")
    if runtime.demo_persona not in {p["id"] for p in PERSONAS}:
        raise PermissionError("Synthetic persona is not bound to this session")
    return runtime.demo_persona


def resolver_kwargs(db, run, profile):
    return {"persona_id": persona_for_run(db, run)} if profile.get("kind") == "zhiyu" else {}


def state_for_run(db, run):
    conv = db.get(Conversation, run.conversation_id)
    context = conv.business_context or {} if conv else {}
    if context.get("authoring_test") and isinstance(context.get("authoring_synthetic_state"), dict):
        return context["authoring_synthetic_state"]
    state = db.get(SyntheticState, (run.workspace_id, CATALOG_ID))
    if not state:
        raise PermissionError("Synthetic catalog unavailable")
    return state.state


def authorize_result(db, call):
    business_authorization = call.definition.get("business_authorization")
    if isinstance(business_authorization, dict) and business_authorization.get("mode") == "none":
        return
    from .models import Run
    from .zhiyu import authorize_operation
    run = db.get(Run, call.run_id)
    state = state_for_run(db, run)
    persona = persona_for_run(db, run)
    authorize_operation(call.definition["connector"]["workflow_id"], call.arguments, state, persona)
    def check(value):
        if isinstance(value, list):
            for item in value: check(item)
        elif isinstance(value, dict):
            if value.get("customer_id"):
                authorize_operation("zhiyu.customer.profile", {"customer_id": value["customer_id"]}, state, persona)
            for item in value.values(): check(item)
    check((call.result or {}).get("data"))


def public_data(value):
    if isinstance(value, list): return [public_data(v) for v in value]
    if isinstance(value, dict):
        return {k: public_data(v) for k, v in value.items() if k not in {"session_action", "approved_facts"}}
    return value


def update_practice_context(db, run):
    from sqlalchemy import select
    from .models import Call
    conv = db.get(Conversation, run.conversation_id)
    for call in db.scalars(select(Call).where(Call.run_id == run.id, Call.status == "SUCCEEDED")):
        if call.definition["connector"].get("version") != "3": continue
        data = (call.result or {}).get("data", {})
        if isinstance(data, dict) and data.get("session_action") == "START":
            authorize_result(db, call)
            conv.business_context = {**(conv.business_context or {}), "practice": {
                "source_run_id": run.id, "persona_id": persona_for_run(db, run),
                "customer_id": data.get("customer_id"), "facts": data.get("approved_facts", {}), "turns": []}}
