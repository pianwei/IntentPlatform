"""Database-backed proof that the synthetic release executes through the real worker."""
import uuid
import pytest

from intent_platform.worker import tick


def _drive(client, headers, run_id, limit=80):
    for _ in range(limit):
        state = client.get(f"/api/v2/runtime/runs/{run_id}", headers=headers)
        assert state.status_code == 200, state.text
        data = state.json()
        if data["status"] in {"SUCCEEDED", "FAILED", "UNKNOWN", "CANCELLED", "WAITING_INPUT", "WAITING_CONFIRMATION"}:
            return data
        tick()
    raise AssertionError("planner did not reach a user-visible state")


def _message(client, headers, conversation_id, text):
    response = client.post(f"/api/v2/runtime/conversations/{conversation_id}/messages",
                           headers=headers, json={"text": text, "client_message_id": str(uuid.uuid4())})
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def _conversation(client, headers, agent_id):
    response = client.post(f"/api/v2/runtime/agents/{agent_id}/conversations", headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _seed(client, admin):
    response = client.post("/api/v2/dev/demo/seed", headers=admin, json={})
    assert response.status_code == 200, response.text
    return response.json()


def test_demo_seed_and_three_step_funds_skill(client, admin):
    item = _seed(client, admin)
    assert item["function_count"] == 20
    assert item["skill_count"] == 5
    agent = item["agent"]
    assert agent["planning_enabled"] is True
    assert len(agent["capability_ids"]) == 20
    cid = _conversation(client, admin, agent["id"])
    run_id = _message(client, admin, cid, "查询 C001 的所有账户余额，给我做资金概览")
    state = _drive(client, admin, run_id)
    assert state["status"] == "SUCCEEDED", state
    events = client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=admin)
    assert events.status_code == 200, events.text
    assert any(event["event_type"] == "RESULT" for event in events.json())
    answers = [event["payload"]["data"] for event in events.json()
               if event["event_type"] == "RESULT" and event["payload"].get("title") == "答复"]
    assert len(answers) == 1 and "余额合计" in answers[0] and "14,500.00" in answers[0]


def test_customer_choice_and_missing_inputs_resume_without_replaying(client, admin):
    agent = _seed(client, admin)["agent"]
    cid = _conversation(client, admin, agent["id"])
    run_id = _message(client, admin, cid, "张晨的所有账户余额，给我做资金概览")
    choice = _drive(client, admin, run_id)
    assert choice["status"] == "WAITING_INPUT" and choice["question"]["kind"] == "planner_choice"
    answer = client.post(f"/api/v2/runtime/runs/{run_id}/answers", headers=admin,
                         json={"revision": choice["revision"], "question_id": choice["question"]["id"],
                               "option_id": "C003", "values": {}})
    assert answer.status_code == 200, answer.text
    assert _drive(client, admin, run_id)["status"] == "SUCCEEDED"
    stale = client.post(f"/api/v2/runtime/runs/{run_id}/answers", headers=admin,
                        json={"revision": choice["revision"], "question_id": choice["question"]["id"],
                              "option_id": "C001", "values": {}})
    assert stale.status_code == 409
    cid = _conversation(client, admin, agent["id"])
    run_id = _message(client, admin, cid, "对 A001 做收支分析")
    missing = _drive(client, admin, run_id)
    assert missing["status"] == "WAITING_INPUT" and missing["question"]["kind"] == "planner_arguments"
    assert {field["name"] for field in missing["question"]["fields"]} == {"start_date", "end_date"}
    answer = client.post(f"/api/v2/runtime/runs/{run_id}/answers", headers=admin,
                         json={"revision": missing["revision"], "question_id": missing["question"]["id"],
                               "values": {"start_date": "2026-09-01", "end_date": "2026-09-30"}})
    assert answer.status_code == 200, answer.text
    assert _drive(client, admin, run_id)["status"] == "SUCCEEDED"


def test_confirmed_demo_write_and_empty_customer_result(client, admin):
    agent = _seed(client, admin)["agent"]
    cid = _conversation(client, admin, agent["id"])
    text = "对 A002 做异常账户核查，客户 C001，原因：演示状态复核"
    run_id = _message(client, admin, cid, text)
    waiting = _drive(client, admin, run_id)
    assert waiting["status"] == "WAITING_CONFIRMATION", waiting
    assert waiting["calls"] and len(waiting["calls"]) == 1  # Status query only.
    rejected = client.post(f"/api/v2/runtime/runs/{run_id}/confirmations", headers=admin,
                           json={"revision": waiting["revision"], "confirmed": False})
    assert rejected.status_code == 200 and rejected.json()["status"] == "CANCELLED"
    cid = _conversation(client, admin, agent["id"])
    run_id = _message(client, admin, cid, text)
    waiting = _drive(client, admin, run_id)
    accepted = client.post(f"/api/v2/runtime/runs/{run_id}/confirmations", headers=admin,
                           json={"revision": waiting["revision"], "confirmed": True})
    assert accepted.status_code == 200, accepted.text
    completed = _drive(client, admin, run_id)
    assert completed["status"] == "SUCCEEDED", completed
    assert len(completed["calls"]) == 2
    cid = _conversation(client, admin, agent["id"])
    missing_run = _message(client, admin, cid, "C999 的所有账户余额，给我做资金概览")
    missing = _drive(client, admin, missing_run)
    assert missing["status"] == "FAILED"
    assert not any(step["status"] == "RUNNING" for step in missing["steps"])


def test_five_skills_and_fixture_fallback_boundary(client, admin):
    agent = _seed(client, admin)["agent"]
    examples = [
        "查询 C001 的所有账户余额，给我做资金概览",
        "分析 A001 在 2026-09-01 到 2026-09-30 的收支分析",
        "查询 C001 的贷款概览",
        "查询 C001 的客户业务全景",
    ]
    for text in examples:
        cid = _conversation(client, admin, agent["id"])
        state = _drive(client, admin, _message(client, admin, cid, text))
        assert state["status"] == "SUCCEEDED", (text, state)
        events = client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=admin).json()
        answers = [event["payload"]["data"] for event in events
                   if event["event_type"] == "RESULT" and event["payload"].get("title") == "答复"]
        assert len(answers) == 1 and "{" not in answers[0]
    cid = _conversation(client, admin, agent["id"])
    unsupported = _drive(client, admin, _message(client, admin, cid, "比较 T001 交易和 A001 账户状态"))
    assert unsupported["status"] == "FAILED"
    assert unsupported["calls"] == []


def test_plan_validator_rejects_unavailable_cycles_and_forged_identifiers():
    from intent_platform.demo import function_packages
    from intent_platform.planning import validate_plan
    packages = function_packages()
    available = [{"capability_id": item["key"], "definition": item["definition"], "version": 1} for item in packages]
    base = {"inputs": {"account_id": "A001"}, "steps": [{"step_id": "balance", "capability_id": "demo.account.balance",
             "arguments": {"account_id": {"$input": "account_id"}}, "depends_on": []}]}
    assert validate_plan(base, available, "查询 A001 余额").steps[0].step_id == "balance"
    for bad in (
        {"inputs": {}, "steps": [{**base["steps"][0], "capability_id": "unregistered"}]},
        {"inputs": {"account_id": "A999"}, "steps": base["steps"]},
        {"inputs": base["inputs"], "steps": [{**base["steps"][0], "depends_on": ["balance"]}]},
        {"inputs": base["inputs"], "steps": base["steps"] * 9},
    ):
        with pytest.raises(ValueError):
            validate_plan(bad, available, "查询 A001 余额")


def test_fixture_object_permissions_filter_customer_and_deny_direct_object():
    from intent_platform.demo import execute, initial_state
    state = initial_state()
    found, _ = execute("demo.customer.search", {"query": "张晨"}, state, "search-1", user_id="dev-reader")
    assert [row["customer_id"] for row in found["data"]["customers"]] == ["C001"]
    with pytest.raises(PermissionError):
        execute("demo.account.balance", {"account_id": "A003"}, state, "balance-1", user_id="dev-reader")


def test_model_fallback_repairs_invalid_plan_once_without_execution(monkeypatch):
    from intent_platform.demo import function_packages
    from intent_platform.planning import propose
    import intent_platform.model_profiles as profiles
    packages = function_packages()
    candidates = [{"capability_id": item["key"], "definition": item["definition"], "version": 1} for item in packages]
    responses = [
        {"decision": "PLAN", "plan": {"inputs": {"account_id": "A001"}, "steps": [
            {"step_id": "balance", "capability_id": "not-published", "arguments": {"account_id": {"$input": "account_id"}}}]}},
        {"decision": "PLAN", "plan": {"inputs": {"account_id": "A001"}, "steps": [
            {"step_id": "balance", "capability_id": "demo.account.balance",
             "arguments": {"account_id": {"$input": "account_id"}}}]}},
    ]
    monkeypatch.setattr(profiles, "structured_model_call", lambda *args: responses.pop(0))
    raw, source, skill = propose("查询 A001 的可用余额", candidates, [],
                                {"model_profile": {"mode": "openai-compatible"}}, "dev-admin", "ws-dev")
    assert source == "model" and skill is None
    assert raw["steps"][0]["capability_id"] == "demo.account.balance"
    assert responses == []


def test_model_fallback_may_request_one_clarification(monkeypatch):
    from intent_platform.planning import propose
    import intent_platform.model_profiles as profiles
    monkeypatch.setattr(profiles, "structured_model_call", lambda *args: {
        "decision": "NEEDS_INPUT", "question": "请提供账户编号"})
    raw, source, skill = propose("查询余额", [], [],
                                {"model_profile": {"mode": "openai-compatible"}}, "dev-admin", "ws-dev")
    assert source == "clarification" and skill is None and raw["_clarification"] == "请提供账户编号"


def test_model_repairs_malformed_response_and_unavailable_skill(monkeypatch):
    from intent_platform.demo import function_packages
    from intent_platform.planning import propose
    import intent_platform.model_profiles as profiles

    candidates = [{"capability_id": item["key"], "definition": item["definition"], "version": 1}
                  for item in function_packages()]
    good = {"decision": "PLAN", "plan": {"inputs": {"account_id": "A001"}, "steps": [
        {"step_id": "balance", "capability_id": "demo.account.balance",
         "arguments": {"account_id": {"$input": "account_id"}}}]}}
    for bad in ({"decision": "PLAN", "plan": {"steps": "not-a-list"}},
                {"decision": "SKILL", "skill_key": "broken.skill", "inputs": {"account_id": "A001"}}):
        responses = [bad, good]
        monkeypatch.setattr(profiles, "structured_model_call", lambda *args: responses.pop(0))
        skills = ([{"definition": {"key": "broken.skill", "name": "Broken Skill", "steps": [
            {"step_id": "x", "capability_key": "unavailable", "arguments": {"account_id": "A001"}}]},
            "version_id": "broken-version"}] if bad["decision"] == "SKILL" else [])
        raw, source, skill = propose("查询 A001 的余额", candidates, skills,
                                    {"model_profile": {"mode": "openai-compatible"}}, "dev-admin", "ws-dev")
        assert source == "model" and skill is None and raw["steps"][0]["step_id"] == "balance"
        assert responses == []


def test_matching_demo_skill_precedes_configured_model(monkeypatch):
    from intent_platform.demo import function_packages
    from intent_platform.demo.skills import business_skill_templates
    from intent_platform.planning import propose
    import intent_platform.model_profiles as profiles

    candidates = [{"capability_id": item["key"], "definition": item["definition"], "version": 1}
                  for item in function_packages()]
    skills = [{"definition": item, "version_id": item["key"] + ":v1"} for item in business_skill_templates()]
    monkeypatch.setattr(profiles, "structured_model_call", lambda *args: pytest.fail("matched Skill called model"))
    raw, source, skill_id = propose("查询 C001 的所有账户余额，给我做资金概览", candidates, skills,
                                    {"model_profile": {"mode": "openai-compatible"}}, "dev-admin", "ws-dev")
    assert source == "skill" and skill_id and len(raw["steps"]) == 3


def test_mocked_model_plan_executes_condition_and_result_chain(client, admin, monkeypatch):
    """Model I/O is stubbed here; the durable validator and executor are real."""
    import intent_platform.planning as planning
    agent = _seed(client, admin)["agent"]
    def model_plan(text, candidates, skills, snapshot, user_id, workspace_id):
        by_key = {row["definition"]["connector"].get("workflow_id"): row["capability_id"] for row in candidates}
        return {"inputs": {"account_id": "A002", "customer_id": "C001"}, "steps": [
            {"step_id": "status", "capability_id": by_key["demo.account.status"],
             "arguments": {"account_id": {"$input": "account_id"}}, "depends_on": []},
            {"step_id": "tickets", "capability_id": by_key["demo.ticket.list"],
             "arguments": {"customer_id": {"$input": "customer_id"}}, "depends_on": ["status"],
             "when": {"step": "status", "path": "data.status", "operator": "eq", "value": "FROZEN"}},
        ]}, "model", None
    monkeypatch.setattr(planning, "propose", model_plan)
    cid = _conversation(client, admin, agent["id"])
    run_id = _message(client, admin, cid, "A002 的账户状态和 C001 的工单如何？")
    state = _drive(client, admin, run_id)
    assert state["status"] == "SUCCEEDED", state
    assert [row["status"] for row in state["steps"]] == ["SUCCEEDED", "SUCCEEDED"]
    assert len(state["calls"]) == 2


def test_invalid_later_step_rejects_whole_plan_before_any_call(client, admin, monkeypatch):
    import intent_platform.planning as planning

    agent = _seed(client, admin)["agent"]
    def invalid_plan(text, candidates, skills, snapshot, user_id, workspace_id):
        by_key = {row["definition"]["connector"].get("workflow_id"): row["capability_id"] for row in candidates}
        return {"inputs": {"account_id": "A001"}, "steps": [
            {"step_id": "balance", "capability_id": by_key["demo.account.balance"],
             "arguments": {"account_id": {"$input": "account_id"}}, "depends_on": []},
            {"step_id": "bad", "capability_id": by_key["demo.account.status"],
             "arguments": {"account_id": 42}, "depends_on": ["balance"]},
        ]}, "model", None
    monkeypatch.setattr(planning, "propose", invalid_plan)
    cid = _conversation(client, admin, agent["id"])
    run_id = _message(client, admin, cid, "查询 A001 余额和状态")
    state = _drive(client, admin, run_id)
    assert state["status"] == "FAILED" and state["calls"] == []


def test_unknown_demo_write_reconciles_to_completed_plan_without_replay(client, admin, monkeypatch):
    from datetime import timedelta
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from intent_platform.db import engine, now, set_identity
    from intent_platform.models import Call, DemoState
    import intent_platform.worker as worker

    agent = _seed(client, admin)["agent"]
    cid = _conversation(client, admin, agent["id"])
    run_id = _message(client, admin, cid, "对 A002 做异常账户核查，客户 C001，原因：演示状态复核")
    waiting = _drive(client, admin, run_id)
    assert waiting["status"] == "WAITING_CONFIRMATION"
    accepted = client.post(f"/api/v2/runtime/runs/{run_id}/confirmations", headers=admin,
                           json={"revision": waiting["revision"], "confirmed": True})
    assert accepted.status_code == 200

    execute = worker.execute_connector
    def lose_write_response(definition, arguments, call_id, user_id, workspace_id,
                            reconcile_operation=None, before_send=None):
        result = execute(definition, arguments, call_id, user_id, workspace_id,
                         reconcile_operation=reconcile_operation, before_send=before_send)
        if definition["connector"].get("workflow_id") == "demo.ticket.create" and not reconcile_operation:
            assert result["status"] == "SUCCEEDED"
            return {**result, "status": "UNKNOWN", "data": {}}
        return result
    monkeypatch.setattr(worker, "execute_connector", lose_write_response)
    unknown = _drive(client, admin, run_id)
    assert unknown["status"] == "UNKNOWN" and len(unknown["calls"]) == 2
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin")
        write = db.scalar(select(Call).where(Call.run_id == run_id, Call.status == "UNKNOWN"))
        assert write is not None
        original_call_id = write.id
        write.lease_until = now() - timedelta(seconds=1)
    monkeypatch.setattr(worker, "execute_connector", execute)
    assert worker.tick()
    completed = _drive(client, admin, run_id)
    assert completed["status"] == "SUCCEEDED" and len(completed["calls"]) == 2
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        assert db.get(Call, original_call_id).status == "SUCCEEDED"
        state = db.get(DemoState, "ws-dev")
        assert len(state.state["_operations"]) >= 1
        assert original_call_id in state.state["_operations"]


def test_seeded_business_skill_can_be_edited_without_mutating_published_release(client, admin):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from intent_platform.db import engine, set_identity
    from intent_platform.models import Release
    agent = _seed(client, admin)["agent"]
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        before = db.get(Release, agent["current_release_id"]).snapshot["business_skills"]
    base = "/api/v2/management/workspaces/ws-dev/business-skills"
    listed = client.get(base, headers=admin)
    assert listed.status_code == 200, listed.text
    skill = next(row for row in listed.json() if row["key"] == "demo.skill.customer_funds")
    definition = {key: value for key, value in skill["definition"].items() if key != "version"}
    definition["description"] += " 演示修订。"
    edited = client.patch(f"{base}/{skill['id']}/draft", headers=admin,
                          json={"revision": skill["revision"], "definition": definition})
    assert edited.status_code == 200, edited.text
    published = client.post(f"{base}/{skill['id']}/publish", headers=admin,
                            json={"revision": edited.json()["revision"]})
    assert published.status_code == 200, published.text
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        assert db.get(Release, agent["current_release_id"]).snapshot["business_skills"] == before
