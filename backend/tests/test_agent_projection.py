"""Actual database/Worker proof for per-agent semantics and final-only output.

Model responses here are explicit protocol fixtures, never model-quality evidence.
"""
import uuid
import json
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from intent_platform.db import engine, set_identity
from intent_platform.models import Agent, Release, Function, Call, BusinessSkill, FunctionVersion
from intent_platform.worker import tick


@pytest.fixture(scope="module")
def three_app(client):
    token = client.post("/api/v2/dev/login", json={"user_id": "dev-admin"}).json()["access_token"]
    headers = {"Authorization": "Bearer " + token}
    seed = client.post("/api/v2/dev/demo/seed", headers=headers, json={})
    assert seed.status_code == 200, seed.text
    from intent_platform.demo.catalog import function_packages
    from intent_platform.schemas import Definition
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        release = db.get(Release, seed.json()["agent"]["current_release_id"])
        ref = release.snapshot["capabilities"][0]["definition"]["domain_refs"][0]
        sid = db.scalar(select(BusinessSkill.id).where(BusinessSkill.workspace_id == "ws-dev", BusinessSkill.key == "demo.skill.customer_funds"))
    ids = {}
    for package in function_packages(ref, three_layer=True):
        if package["key"] not in {"demo.customer.search", "demo.account.list", "demo.account.balance"}: continue
        d = Definition.model_validate(package["definition"]).model_dump()
        result = client.post("/api/v2/management/workspaces/ws-dev/functions/import", headers=headers, json={"definition": d})
        assert result.status_code == 201, result.text
        fid = result.json()["id"]
        val = client.post(f"/api/v2/management/workspaces/ws-dev/functions/{fid}/validate", headers=headers, json={})
        assert val.status_code == 200 and val.json()["valid"], val.text
        pub = client.post(f"/api/v2/management/workspaces/ws-dev/functions/{fid}/publish", headers=headers)
        assert pub.status_code == 200, pub.text
        ids[package["key"]] = fid
    app = client.post("/api/v2/management/workspaces/ws-dev/agents", headers=headers, json={"name": "INT019 test " + str(uuid.uuid4()),
        "capability_ids": list(ids.values()), "business_skill_ids": [sid], "routing_strategy": "three-layer-v1"})
    assert app.status_code == 201, app.text
    agent = app.json()
    pub = client.post(f"/api/v2/management/workspaces/ws-dev/agents/{agent['id']}/publish", headers=headers, json={"revision": agent["revision"]})
    assert pub.status_code == 200, pub.text
    return headers, agent, ids


def start(client, headers, aid, text):
    r = client.post(f"/api/v2/runtime/agents/{aid}/conversations", headers=headers)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    r = client.post(f"/api/v2/runtime/conversations/{cid}/messages", headers=headers,
        json={"text": text, "client_message_id": str(uuid.uuid4())})
    assert r.status_code == 202, r.text
    return cid, r.json()["run_id"]


def drive(client, headers, rid):
    for _ in range(35):
        data = client.get(f"/api/v2/runtime/runs/{rid}", headers=headers).json()
        if data["status"] in {"SUCCEEDED", "FAILED", "WAITING_INPUT", "WAITING_CONFIRMATION", "UNKNOWN"}: return data
        tick()
    raise AssertionError(data)


def fake_funds(monkeypatch, query="C001"):
    calls = []
    def invoke(work, stage, data, instruction):
        calls.append((stage, data))
        if stage == "entity": return {"goal": "资金概览", "entities": [{"type": "demo:Customer", "mention": query}], "actions": ["READ", "LIST"], "negated": False}
        if stage == "skill": return {"skill_key": "demo.skill.customer_funds", "inputs": {"customer_query": query}}
        raise AssertionError("Applicable rule must precede autonomous planning")
    monkeypatch.setattr("intent_platform.three_layer._invoke", invoke)
    return calls


def test_projection_preview_is_read_only_and_runs_real_layers(client, three_app, monkeypatch):
    headers, agent, ids = three_app
    before = client.get("/api/v2/management/workspaces/ws-dev/agents", headers=headers).json()
    r = client.post(f"/api/v2/management/workspaces/ws-dev/agents/{agent['id']}/compile-preview", headers=headers, json={"revision": agent["revision"]})
    assert r.status_code == 200 and r.json()["valid"], r.text
    assert set(r.json()["projection"]["explicit_function_ids"]) == set(ids.values())
    assert r.json()["projection"]["domain"]["relations"]
    assert client.get("/api/v2/management/workspaces/ws-dev/agents", headers=headers).json() == before
    calls = fake_funds(monkeypatch)
    cid, rid = start(client, headers, agent["id"], "查询 C001 的所有账户余额，资金概览")
    state = drive(client, headers, rid)
    assert state["status"] == "SUCCEEDED", state
    assert state["steps"] == [] and state["bindings"] == []
    assert [x[0] for x in calls] == ["entity", "skill"]
    trace = client.get(f"/api/v2/management/workspaces/ws-dev/runs/{rid}/trace", headers=headers).json()
    assert any(t["kind"] == "goal_recognized" for t in trace["layers"]["entity"])
    assert any(t["kind"] == "objects_bound" for t in trace["layers"]["entity"])
    assert any(t["kind"] == "step_validated" for t in trace["layers"]["dynamics"])
    events = client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=headers).json()
    assert len([e for e in events if e["event_type"] == "RESULT"]) == 1
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        executed = list(db.scalars(select(Call).where(Call.run_id == rid)))
        assert len(executed) == 4
        assert all(c.bindings for c in executed if c.capability_id != ids["demo.customer.search"])


def test_rule_ambiguity_resumes_without_replay(client, three_app, monkeypatch):
    headers, agent, _ = three_app
    fake_funds(monkeypatch, "张晨")
    cid, rid = start(client, headers, agent["id"], "张晨的所有账户余额，资金概览")
    state = drive(client, headers, rid)
    assert state["status"] == "WAITING_INPUT", state
    answer = client.post(f"/api/v2/runtime/runs/{rid}/answers", headers=headers, json={
        "revision": state["revision"], "question_id": state["question"]["id"], "option_id": "C003", "values": {}})
    assert answer.status_code == 200, answer.text
    assert drive(client, headers, rid)["status"] == "SUCCEEDED"
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        assert len(list(db.scalars(select(Call).where(Call.run_id == rid)))) == 3


def test_no_skill_uses_explicit_functions_and_zero_calls_after_invalid_plan(client, three_app, monkeypatch):
    headers, agent, ids = three_app
    stages = []
    def invoke(work, stage, data, instruction):
        stages.append(stage)
        if stage == "entity": return {"goal": "余额", "entities": [{"type": "demo:Account", "mention": "A001"}], "actions": ["READ"], "negated": False}
        if stage == "skill": return {"skill_key": None, "inputs": {}}
        return {"decision": "PLAN", "plan": {"inputs": {}, "steps": [{"step_id": "illegal", "capability_id": "outside", "arguments": {}, "depends_on": []}]}}
    monkeypatch.setattr("intent_platform.three_layer._invoke", invoke)
    _, rid = start(client, headers, agent["id"], "查询 A001 余额")
    state = drive(client, headers, rid)
    assert state["status"] == "FAILED" and state["calls"] == []
    assert stages == ["entity", "skill", "plan", "plan"]


def test_public_model_allows_reviewed_subset_but_not_changed_description():
    from intent_platform.public_demo import assert_synthetic_snapshot
    from intent_platform.demo.catalog import function_packages
    package = function_packages(include_extension=True)[-1]
    snapshot = {"capabilities": [{"definition": package["definition"]}]}
    assert_synthetic_snapshot(snapshot)
    package["definition"]["description"] = "unreviewed private material"
    with pytest.raises(ValueError): assert_synthetic_snapshot(snapshot)


def test_composite_pins_one_layer_and_never_exposes_implicit_tools(client, three_app, monkeypatch):
    from intent_platform.demo.skills import business_skill_templates
    from intent_platform.schemas import Definition
    headers, agent, ids = three_app
    base = "/api/v2/management/workspaces/ws-dev"
    rule = {k: v for k, v in business_skill_templates()[0].items() if k != "version"}
    rule["key"] = "test.composite." + uuid.uuid4().hex
    created = client.post(base + "/business-skills", headers=headers, json=rule)
    assert created.status_code == 201, created.text
    skill = created.json()
    pub = client.post(base + f"/business-skills/{skill['id']}/publish", headers=headers, json={"revision": skill["revision"]})
    assert pub.status_code == 200, pub.text
    version = client.get(base + f"/business-skills/{skill['id']}/versions", headers=headers).json()[0]
    direct = client.get(base + f"/functions/{ids['demo.account.balance']}/versions", headers=headers).json()[0]
    schema = {"type": "object", "properties": {"balances": {"type": "array", "items": direct["definition"]["output_schema"], "maxItems": 5}}, "required": ["balances"], "additionalProperties": False}
    definition = Definition(name="组合资金查询", description="用登记步骤查询客户账户余额", object_type="demo:Customer", action="READ",
        execution_kind="composite", composite={"skill_version_id": version["id"],
            "dependencies": [{"function_id": fid, "version": 1} for fid in ids.values()],
            "final_output_mapping": {"balances": {"$step": "balances", "path": "data"}}},
        input_schema={"type": "object", "properties": {"customer_query": {"type": "string"}}, "required": ["customer_query"], "additionalProperties": False},
        output_schema=schema, presentation={"component": "table", "field_map": {"余额": "balances"}}).model_dump()
    f = client.post(base + "/functions/import", headers=headers, json={"definition": definition}).json()
    valid = client.post(base + f"/functions/{f['id']}/validate", headers=headers, json={})
    assert valid.json()["valid"], valid.text
    published = client.post(base + f"/functions/{f['id']}/publish", headers=headers)
    assert published.status_code == 200, published.text
    app = client.post(base + "/agents", headers=headers, json={"name": "组合 " + uuid.uuid4().hex,
                     "capability_ids": [f["id"]], "routing_strategy": "three-layer-v1"}).json()
    preview = client.post(base + f"/agents/{app['id']}/compile-preview", headers=headers, json={"revision": app["revision"]})
    assert preview.json()["valid"], preview.text
    assert set(preview.json()["projection"]["dependency_function_ids"]) == set(ids.values())
    pub = client.post(base + f"/agents/{app['id']}/publish", headers=headers, json={"revision": app["revision"]})
    assert pub.status_code == 200, pub.text
    def invoke(work, stage, data, instruction):
        if stage == "entity": return {"goal": "资金查询", "entities": [{"type": "demo:Customer", "mention": "C001"}], "actions": ["READ"], "negated": False}
        if stage == "skill": return {"skill_key": rule["key"], "inputs": {"customer_query": "C001"}}
        raise AssertionError("Composite rule must execute without autonomous tools")
    monkeypatch.setattr("intent_platform.three_layer._invoke", invoke)
    cid, rid = start(client, headers, app["id"], "查询 C001 资金概览")
    assert drive(client, headers, rid)["status"] == "SUCCEEDED"
    events = client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=headers).json()
    assert len([e for e in events if e["event_type"] == "RESULT"]) == 1
    def no_rule(work, stage, data, instruction):
        if stage == "entity": return {"goal": "查询", "entities": [{"type": "demo:Customer", "mention": "C001"}], "actions": ["READ"], "negated": False}
        if stage == "skill": return {"skill_key": None, "inputs": {}}
        assert {c["capability_id"] for c in data["functions"]} == {f["id"]}
        return {"decision": "NEEDS_INPUT", "question": "请补充客户资金查询目标"}
    monkeypatch.setattr("intent_platform.three_layer._invoke", no_rule)
    _, rid = start(client, headers, app["id"], "查询 C001")
    state = drive(client, headers, rid)
    assert state["status"] == "WAITING_INPUT" and state["calls"] == []
    def composed(work, stage, data, instruction):
        if stage == "entity": return {"goal": "查询", "entities": [{"type": "demo:Customer", "mention": "C001"}], "actions": ["READ"], "negated": False}
        if stage == "skill": return {"skill_key": None, "inputs": {}}
        assert {c["capability_id"] for c in data["functions"]} == {f["id"]}
        return {"decision": "PLAN", "plan": {"inputs": {"customer_query": "C001"}, "steps": [{"step_id": "overview", "capability_id": f["id"], "arguments": {"customer_query": {"$input": "customer_query"}}, "depends_on": []}]}}
    monkeypatch.setattr("intent_platform.three_layer._invoke", composed)
    cid, rid = start(client, headers, app["id"], "查询 C001")
    assert drive(client, headers, rid)["status"] == "SUCCEEDED"
    rows = client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=headers).json()
    result = next(e["payload"] for e in rows if e["event_type"] == "RESULT")
    component_json = json.dumps(result.get("components", []), ensure_ascii=False)
    # A published structured/table response keeps its facts in the single
    # result component; the answer text is only a short lead-in, not a second
    # serialized copy of the same business data.
    assert result["data"] == "查询完成，共找到 2 条记录，结果如下。"
    assert len(result["components"]) == 1 and result["components"][0]["component"] == "table"
    assert "余额" in component_json and "12000.0" in component_json and "customers" not in component_json


def test_current_permission_filters_entity_model_input(client, three_app, monkeypatch):
    from intent_platform.models import Function
    headers, agent, ids = three_app
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin")
        f = db.get(Function, ids["demo.account.balance"])
        previous = f.disabled
        f.disabled = True
    def invoke(work, stage, data, instruction):
        assert stage == "entity"
        assert all(ids["demo.account.balance"] not in item["function_ids"] for item in data["actions"] + data["entities"])
        return {"goal": "unknown", "entities": [{"type": "not-registered"}], "actions": [], "negated": False}
    monkeypatch.setattr("intent_platform.three_layer._invoke", invoke)
    try:
        _, rid = start(client, headers, agent["id"], "查余额")
        state = drive(client, headers, rid)
        assert state["status"] == "FAILED" and not state["calls"]
    finally:
        with Session(engine) as db, db.begin():
            set_identity(db, "dev-admin")
            db.get(Function, ids["demo.account.balance"]).disabled = previous


def test_catalog_installs_five_visible_composites_and_preserves_strategy(client, admin, monkeypatch):
    from intent_platform.public_demo import assert_synthetic_snapshot
    from intent_platform.models import Release
    created = client.post("/api/v2/management/workspaces", headers=admin,
                          json={"name": "synthetic " + uuid.uuid4().hex, "administrator_id": "dev-admin"})
    assert created.status_code == 201, created.text
    ws = created.json()["id"]
    seeded = client.post("/api/v2/dev/demo/seed", headers=admin, json={"workspace_id": ws, "routing_strategy": "three-layer-v1"})
    assert seeded.status_code == 200, seeded.text
    assert seeded.json()["function_count"] == 20 and seeded.json()["composite_count"] == 5
    agent = seeded.json()["agent"]
    assert len(agent["capability_ids"]) == 25 and agent["business_skill_ids"] == []
    repeated = client.post("/api/v2/dev/demo/seed", headers=admin, json={"workspace_id": ws})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["agent"]["current_release_id"] == agent["current_release_id"]
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        release = db.get(Release, agent["current_release_id"])
        assert release.snapshot["three_layer_prompt_revision"] == "scope-v4"
        assert_synthetic_snapshot(release.snapshot)
        from copy import deepcopy
        tampered = deepcopy(release.snapshot)
        tampered["agent_projection"]["actions"][0]["name"] = "private content"
        with pytest.raises(ValueError): assert_synthetic_snapshot(tampered)
        flow = next(c["capability_id"] for c in release.snapshot["capabilities"] if c["definition"]["connector"].get("workflow_id") == "demo.composite.account_cashflow")
    base = f"/api/v2/management/workspaces/{ws}"
    subset = client.post(base + "/agents", headers=admin, json={"name": "收支组合子集", "capability_ids": [flow], "routing_strategy": "three-layer-v1"}).json()
    published = client.post(base + f"/agents/{subset['id']}/publish", headers=admin, json={"revision": subset["revision"]})
    assert published.status_code == 200, published.text
    def invoke(work, stage, data, instruction):
        if stage == "entity": return {"goal": "统计账户收支", "entities": [{"type": "demo:Account", "mention": "A001"}], "actions": ["SUMMARIZE"], "negated": False}
        assert stage == "skill"
        return {"skill_key": "demo.skill.account_cashflow", "inputs": {"account_id": "A001", "start_date": "2026-09-01", "end_date": "2026-09-30"}}
    monkeypatch.setattr("intent_platform.three_layer._invoke", invoke)
    cid, rid = start(client, admin, subset["id"], "统计 A001 2026-09-01 到 2026-09-30 收支")
    assert drive(client, admin, rid)["status"] == "SUCCEEDED"
    events = client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=admin).json()
    final = next(e["payload"] for e in events if e["event_type"] == "RESULT")
    assert "transactions" in final["data"] and "summary" in final["data"]
    assert final["components"] == []  # Full deterministic Markdown is the single user-facing result.


def test_partial_final_is_a_new_event_after_consumed_cursor(client, three_app, monkeypatch):
    from intent_platform.models import Event
    import intent_platform.worker as worker
    headers, agent, ids = three_app
    def invoke(work, stage, data, instruction):
        if stage == "entity": return {"goal": "账户查询", "entities": [{"type": "demo:Account", "mention": "A001"}, {"type": "demo:Customer", "mention": "C001"}], "actions": ["READ", "LIST"], "negated": False}
        if stage == "skill": return {"skill_key": None, "inputs": {}}
        return {"decision": "PLAN", "plan": {"inputs": {}, "steps": [
            {"step_id": "balance", "capability_id": ids["demo.account.balance"], "arguments": {"account_id": "A001"}, "depends_on": []},
            {"step_id": "accounts", "capability_id": ids["demo.account.list"], "arguments": {"customer_id": "C001"}, "depends_on": []}]}}
    monkeypatch.setattr("intent_platform.three_layer._invoke", invoke)
    original = worker.execute_connector
    def execute(definition, *args, **kwargs):
        if definition["connector"].get("workflow_id") == "demo.account.list":
            return {"status": "FAILED", "data": {"message": "synthetic failure"}, "operation_id": None}
        return original(definition, *args, **kwargs)
    monkeypatch.setattr(worker, "execute_connector", execute)
    cid, rid = start(client, headers, agent["id"], "查询 A001 余额并列出 C001 的账户")
    assert drive(client, headers, rid)["status"] == "FAILED"
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        original_events = list(db.scalars(select(Event).where(Event.run_id == rid).order_by(Event.sequence)))
        call_results = [e for e in original_events if e.event_type == "RESULT" and e.payload.get("call_id")]
        final = next(e for e in original_events if e.event_type == "RESULT" and e.payload.get("title") == "答复")
        assert final.sequence > max(e.sequence for e in call_results)
        after = max(e.sequence for e in call_results)
    events = client.get(f"/api/v2/runtime/conversations/{cid}/events?after={after}", headers=headers).json()
    result = next(e["payload"] for e in events if e["event_type"] == "RESULT")
    assert "A001" in result["data"] and result["components"] == []
    assert not any(e["event_type"] == "RESULT" and e["sequence"] <= after for e in events)
    from intent_platform.models import PlanStep, ExecutionPlan
    # Simulate later reconciliation updating the ledger. The already-published
    # event must retain the facts it had at its own cursor.
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin")
        plan = db.scalar(select(ExecutionPlan).where(ExecutionPlan.run_id == rid))
        step = db.scalar(select(PlanStep).where(PlanStep.plan_id == plan.id, PlanStep.step_key == "balance"))
        step.result = {"data": {"account_id": "A001", "balance": 999, "available": 999, "currency": "CNY"}}
    restored = client.get(f"/api/v2/runtime/conversations/{cid}/events?after={after}", headers=headers).json()
    again = next(e["payload"] for e in restored if e["event_type"] == "RESULT")
    assert again["components"] == result["components"] and again["files"] == result["files"]


def test_history_is_reauthorized_and_available_to_new_entity_stage(client, three_app, monkeypatch):
    headers, agent, ids = three_app
    fake_funds(monkeypatch, "C001")
    cid, rid = start(client, headers, agent["id"], "C001 资金概览")
    assert drive(client, headers, rid)["status"] == "SUCCEEDED"
    seen = []
    def invoke(work, stage, data, instruction):
        if stage == "entity":
            candidates = [h for h in data["authorized_history"] if h["type"] == "demo:Account"]
            assert len(candidates) == 2
            seen.append(candidates[0]["history_ref"])
            return {"goal": "上次账户余额", "entities": [{"type": "demo:Account", "history_ref": seen[0]}], "actions": ["READ"], "negated": False}
        if stage == "skill": return {"skill_key": None, "inputs": {}}
        return {"decision": "PLAN", "plan": {"inputs": {}, "steps": [{"step_id": "balance", "capability_id": ids["demo.account.balance"], "arguments": {"account_id": "A001"}, "depends_on": []}]}}
    monkeypatch.setattr("intent_platform.three_layer._invoke", invoke)
    response = client.post(f"/api/v2/runtime/conversations/{cid}/messages", headers=headers, json={"text": "刚才第一个账户余额", "client_message_id": str(uuid.uuid4())})
    assert response.status_code == 202
    rid = response.json()["run_id"]
    assert drive(client, headers, rid)["status"] == "SUCCEEDED"
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        call = db.scalar(select(Call).where(Call.run_id == rid))
        assert any(v.get("history_ref") == seen[0] for v in call.bindings.values())


def test_scope_revision_shares_one_repair_budget_and_traces_safe_stage(client, three_app, monkeypatch):
    headers, agent, ids = three_app
    stages = []
    def invoke(work, stage, data, instruction):
        stages.append(stage)
        if stage == "entity":
            assert data["allowed_entity_types"] and data["available_goals"] and data["output_schema"]
            if len(stages) == 1:
                return {"goal": "余额", "entities": [{"type": "unregistered-secret-value"}], "actions": ["READ"]}
            assert data["previous_error"]["reason_code"] == "UNREGISTERED_ENTITY_OR_ACTION"
            return {"goal": "余额", "entities": [{"type": "demo:Account", "mention": "A001"}], "actions": ["READ"]}
        if stage == "skill":
            assert data["direct_alternatives"] and "final_output_mapping" in instruction
            return {"skill_key": None, "inputs": {}}
        return {"decision": "PLAN", "plan": {"inputs": {}, "steps": [{"step_id": "outside", "capability_id": "secret-unavailable", "arguments": {}, "depends_on": []}]}}
    monkeypatch.setattr("intent_platform.three_layer._invoke", invoke)
    _, rid = start(client, headers, agent["id"], "查询 A001 余额")
    state = drive(client, headers, rid)
    assert state["status"] == "FAILED" and state["calls"] == []
    assert stages == ["entity", "entity", "skill", "plan"]
    detail = client.get(f"/api/v2/management/workspaces/ws-dev/runs/{rid}/trace", headers=headers).json()
    rejected = next(t["detail"] for t in detail["layers"]["planning"] if t["kind"] == "plan_rejected")
    assert rejected == {"stage": "plan", "error_type": "ValueError", "reason_code": "UNAVAILABLE_CAPABILITY"}
    assert "secret" not in str(detail)


def test_requested_earlier_result_remains_in_final_components(client, three_app, monkeypatch):
    headers, agent, ids = three_app
    def invoke(work, stage, data, instruction):
        if stage == "entity": return {"goal": "定位并列出账户", "entities": [{"type": "demo:Customer", "mention": "C001"}], "actions": ["LOCATE", "LIST"]}
        if stage == "skill": return {"skill_key": None, "inputs": {}}
        return {"decision": "PLAN", "plan": {"inputs": {}, "result_steps": ["customer", "accounts"], "steps": [
            {"step_id": "customer", "capability_id": ids["demo.customer.search"], "arguments": {"query": "C001"}, "depends_on": []},
            {"step_id": "accounts", "capability_id": ids["demo.account.list"], "arguments": {"customer_id": {"$step": "customer", "path": "data.customers.0.customer_id"}}, "depends_on": ["customer"]}]}}
    monkeypatch.setattr("intent_platform.three_layer._invoke", invoke)
    cid, rid = start(client, headers, agent["id"], "定位 C001 客户，给我客户候选清单和该客户的账户列表")
    assert drive(client, headers, rid)["status"] == "SUCCEEDED"
    events = client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=headers).json()
    answer = next(e["payload"] for e in events if e["event_type"] == "RESULT")
    assert len(answer["components"]) == 2
    assert any("customers" in c["data"] for c in answer["components"])
    assert any("accounts" in c["data"] for c in answer["components"])
    assert all(fid not in str(answer) for fid in ids.values())


def test_consumed_correction_survives_worker_exit_and_lease_recovery(client, three_app, monkeypatch):
    from datetime import timedelta
    from intent_platform.db import now
    from intent_platform.models import Run, ProcessingAttempt
    import intent_platform.three_layer as layers
    headers, agent, _ = three_app
    stages = []
    def invoke(work, stage, data, instruction):
        stages.append(stage)
        return {"goal": "bad", "entities": [{"type": "unregistered"}], "actions": ["READ"]}
    monkeypatch.setattr(layers, "_invoke", invoke)
    original = layers.claim_repair
    class WorkerExit(BaseException): pass
    def crash(work):
        assert original(work)
        raise WorkerExit()
    monkeypatch.setattr(layers, "claim_repair", crash)
    _, rid = start(client, headers, agent["id"], "查询 A001 余额")
    with pytest.raises(WorkerExit): tick()
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin")
        run = db.get(Run, rid)
        assert run.proposal["three_layer_repair_used"] is True
        run.lease_until = now() - timedelta(seconds=1)
        for attempt in db.scalars(select(ProcessingAttempt).where(ProcessingAttempt.run_id == rid)):
            attempt.lease_until = run.lease_until
    monkeypatch.setattr(layers, "claim_repair", original)
    state = drive(client, headers, rid)
    assert state["status"] == "FAILED" and not state["calls"]
    assert stages == ["entity", "entity"]


def test_consumed_correction_survives_same_run_clarification(client, three_app, monkeypatch):
    headers, agent, _ = three_app
    stages = []
    def invoke(work, stage, data, instruction):
        stages.append(stage)
        if stage == "entity":
            if stages.count("entity") != 2:
                return {"goal": "bad", "entities": [{"type": "unregistered"}], "actions": ["READ"]}
            return {"goal": "余额", "entities": [{"type": "demo:Account", "mention": "A001"}], "actions": ["READ"]}
        if stage == "skill": return {"skill_key": None, "inputs": {}}
        return {"decision": "NEEDS_INPUT", "question": "补充目标"}
    monkeypatch.setattr("intent_platform.three_layer._invoke", invoke)
    _, rid = start(client, headers, agent["id"], "查询 A001 余额")
    state = drive(client, headers, rid)
    assert state["status"] == "WAITING_INPUT"
    response = client.post(f"/api/v2/runtime/runs/{rid}/answers", headers=headers, json={
        "revision": state["revision"], "question_id": state["question"]["id"], "values": {"detail": "只要余额"}})
    assert response.status_code == 200, response.text
    assert drive(client, headers, rid)["status"] == "FAILED"
    assert stages == ["entity", "entity", "skill", "plan", "entity"]


def test_batch_results_follow_persisted_call_order_not_database_scan(client, three_app, monkeypatch):
    from intent_platform.models import Run, ExecutionPlan, PlanStep
    from intent_platform.planning import advance
    headers, agent, _ = three_app
    fake_funds(monkeypatch)
    _, rid = start(client, headers, agent["id"], "查询 C001 的所有账户余额，资金概览")
    assert drive(client, headers, rid)["status"] == "SUCCEEDED"
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin")
        plan = db.scalar(select(ExecutionPlan).where(ExecutionPlan.run_id == rid))
        step = next(s for s in db.scalars(select(PlanStep).where(PlanStep.plan_id == plan.id)) if s.spec.get("foreach"))
        natural = list(db.scalars(select(Call).where(Call.id.in_(step.call_ids))))
        assert len(natural) == 2
        # Deliberately persist a valid invocation order opposite to the actual
        # database scan, so the test fails without order restoration.
        ordered = list(reversed(natural))
        step.call_ids = [c.id for c in ordered]
        step.arguments = {"items": [c.arguments for c in ordered]}
        step.status = "RUNNING"; step.result = None
        plan.status = "PENDING"
        run = db.get(Run, rid); run.status = "EXECUTING"
        advance(db, run)
        assert step.result["data"] == [(c.result or {})["data"] for c in ordered]
        assert [d["account_id"] for d in step.result["data"]] == [a["account_id"] for a in step.arguments["items"]]


def test_published_prompt_revisions_keep_their_own_instructions(three_app, monkeypatch):
    from copy import deepcopy
    from intent_platform.three_layer import propose
    _, agent, ids = three_app
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        snapshot = deepcopy(db.get(Release, db.get(Agent, agent["id"]).current_release_id).snapshot)
    observed = {}
    def invoke(work, stage, data, instruction):
        revision = work["snapshot"].get("three_layer_prompt_revision", "scope-v1")
        observed[(revision, stage)] = (data, instruction)
        if stage == "entity": return {"goal": "余额", "entities": [{"type": "demo:Account", "mention": "A001"}], "actions": ["READ"]}
        return {"decision": "PLAN", "plan": {"inputs": {}, "steps": [{"step_id": "balance", "capability_id": ids["demo.account.balance"], "arguments": {"account_id": "A001"}, "depends_on": []}]}}
    monkeypatch.setattr("intent_platform.three_layer._invoke", invoke)
    for revision in (None, "scope-v2", "scope-v3", "scope-v4"):
        selected = deepcopy(snapshot)
        selected.pop("three_layer_prompt_revision", None)
        if revision: selected["three_layer_prompt_revision"] = revision
        raw, *_ = propose({"snapshot": selected, "candidates": selected["capabilities"], "text": "查询 A001 余额"}, [])
        assert raw["steps"][0]["arguments"] == {"account_id": "A001"}
    assert "available_goals" not in observed[("scope-v1", "entity")][0]
    assert "available_goals" in observed[("scope-v2", "entity")][0]
    marker = "Every accepted user-provided parameter"
    assert marker not in observed[("scope-v1", "plan")][1] and marker not in observed[("scope-v2", "plan")][1]
    assert marker in observed[("scope-v3", "plan")][1]
    assert marker in observed[("scope-v4", "plan")][1]
    assert "supplied_parameter_evidence" not in observed[("scope-v3", "entity")][0]
    assert "supplied_parameter_evidence" in observed[("scope-v4", "entity")][0]
    assert all("aliases" in item for item in observed[("scope-v4", "entity")][0]["available_goals"])


@pytest.mark.parametrize("empty", [False, True])
def test_non_customer_collection_choice_and_empty_trace_are_entity_neutral(client, three_app, monkeypatch, empty):
    import intent_platform.worker as worker
    headers, agent, ids = three_app
    def invoke(work, stage, data, instruction):
        if stage == "entity": return {"goal": "选择账户查询", "entities": [{"type": "demo:Customer", "mention": "C001"}], "actions": ["LIST", "READ"]}
        if stage == "skill": return {"skill_key": None, "inputs": {}}
        return {"decision": "PLAN", "plan": {"inputs": {}, "steps": [
            {"step_id": "accounts", "capability_id": ids["demo.account.list"], "arguments": {"customer_id": "C001"}, "depends_on": []},
            {"step_id": "balance", "capability_id": ids["demo.account.balance"], "arguments": {"account_id": {"$step": "accounts", "path": "data.accounts.0.account_id"}}, "depends_on": ["accounts"]}]}}
    monkeypatch.setattr("intent_platform.three_layer._invoke", invoke)
    original = worker.execute_connector
    def execute(definition, *args, **kwargs):
        if empty and definition["connector"].get("workflow_id") == "demo.account.list":
            return {"status": "SUCCEEDED", "data": {"accounts": []}, "operation_id": None}
        return original(definition, *args, **kwargs)
    monkeypatch.setattr(worker, "execute_connector", execute)
    cid, rid = start(client, headers, agent["id"], "列出 C001 的账户，选择一个账户查询余额")
    state = drive(client, headers, rid)
    assert state["status"] == ("FAILED" if empty else "WAITING_INPUT")
    events = client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=headers).json()
    message = next(e["payload"]["message"] for e in events if e["event_type"] == ("ERROR" if empty else "QUESTION"))
    assert "业务对象" in message and "客户" not in message
    detail = client.get(f"/api/v2/management/workspaces/ws-dev/runs/{rid}/trace", headers=headers).json()
    assert any(t["detail"].get("kind") == "entity" for t in detail["layers"]["entity"] if t["kind"] == ("object_not_found" if empty else "ambiguous"))
    if not empty:
        answer = client.post(f"/api/v2/runtime/runs/{rid}/answers", headers=headers, json={"revision": state["revision"], "question_id": state["question"]["id"], "option_id": "A001", "values": {}})
        assert answer.status_code == 200, answer.text
        assert drive(client, headers, rid)["status"] == "SUCCEEDED"
        detail = client.get(f"/api/v2/management/workspaces/ws-dev/runs/{rid}/trace", headers=headers).json()
        assert any(t["detail"].get("bound_object_type") == "demo:Account" for t in detail["layers"]["entity"] if t["kind"] == "candidate_selected")
