"""A runtime session sees business facts; only administrators see three-layer facts."""
from copy import deepcopy
import json

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from conftest import conversation, drain, message
from intent_platform.db import engine, set_identity
from intent_platform.models import Call, DemoState, Event, ManagementTrace, Membership, ResultFile, Run
from intent_platform.services import event_view


def _runtime(client, admin, seeded):
    # The session-scoped database is deliberately persistent between test runs.
    # Restore only the two development seed functions this fixture owns.
    for function in seeded["functions"]:
        restored = client.put(f"/api/v2/management/workspaces/ws-dev/functions/{function['id']}/access",
                              headers=admin, json={"access_mode": "workspace", "allowed_users": []})
        assert restored.status_code == 200, restored.text
    agent = seeded["agent"]
    response = client.post(f"/api/v2/runtime/apps/{agent['public_id']}/sessions", headers=admin, json={})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}, agent


def test_runtime_read_channels_project_business_steps_without_internal_trace(client, admin, seeded):
    headers, agent = _runtime(client, admin, seeded)
    cid = conversation(client, headers, agent["id"])
    rid = message(client, headers, cid, seeded["functions"][0]["name"] + " record_id=demo-001")
    drain()
    run = client.get(f"/api/v2/runtime/runs/{rid}", headers=headers)
    events = client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=headers)
    history = client.get(f"/api/v2/runtime/conversations/{cid}/history", headers=headers)
    attempts = client.get(f"/api/v2/runtime/runs/{rid}/attempts", headers=headers)
    for response in (run, events, history, attempts):
        assert response.status_code == 200, response.text
    row = run.json()
    assert row["calls"] and all(set(call) == {"id", "status"} for call in row["calls"])
    assert row["bindings"] == []
    assert row["steps"] == []
    detail = client.get(f"/api/v2/management/workspaces/ws-dev/runs/{rid}/trace", headers=admin)
    assert detail.status_code == 200 and detail.json()["steps"][0]["name"] == seeded["functions"][0]["name"]
    assert set(row["timing"]) == {"total_ms", "user_wait_ms", "processing_ms"}
    legacy_results = [item["payload"] for item in events.json() if item["event_type"] == "RESULT"]
    assert legacy_results and isinstance(legacy_results[0]["data"], dict)
    assert legacy_results[0]["result_unavailable"] is False
    assert history.json()["runs"][0]["steps"] == row["steps"]
    assert attempts.json() and all(set(item) == {"stage", "status", "duration_ms"} for item in attempts.json())
    public = json.dumps({"run": row, "events": events.json(), "history": history.json(),
                         "attempts": attempts.json()}, ensure_ascii=False)
    for private in ("source_call_id", "resolver_ref", "operation_id", "capability_id",
                    "credential_ref", "trace_id", "provenance", "plan_source", "skill_version_id"):
        assert private not in public
    assert client.get(f"/api/v2/management/workspaces/ws-dev/runs/{rid}/trace", headers=headers).status_code == 403


def test_admin_trace_is_space_scoped_and_detail_allowlisted(client, admin, seeded):
    headers, agent = _runtime(client, admin, seeded)
    cid = conversation(client, headers, agent["id"])
    rid = message(client, headers, cid, seeded["functions"][0]["name"] + " record_id=demo-001")
    drain()
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin")
        db.add(ManagementTrace(workspace_id="ws-dev", run_id=rid, layer="entity", kind="goal_parsed",
                               detail={"object_types": ["demo:Customer"], "secret": "never-return-this"}))
    base = "/api/v2/management/workspaces/ws-dev/runs"
    listing = client.get(base, headers=admin, params={"run_id": rid})
    trace = client.get(base + f"/{rid}/trace", headers=admin)
    assert listing.status_code == 200 and len(listing.json()) == 1
    assert trace.status_code == 200, trace.text
    assert trace.json()["run"]["run_id"] == rid
    assert trace.json()["layers"]["entity"][0]["detail"] == {"object_types": ["demo:Customer"]}
    assert "never-return-this" not in trace.text
    other = client.post("/api/v2/dev/login", json={"user_id": "dev-other"})
    other_headers = {"Authorization": "Bearer " + other.json()["access_token"]}
    assert client.get(base + f"/{rid}/trace", headers=other_headers).status_code == 403


def test_historical_event_payload_is_allowlisted_even_if_raw_ledger_contains_extra_fields():
    result = Event(workspace_id="ws-dev", conversation_id="conv-demo", run_id="run-demo", revision=1,
                   sequence=1, event_type="RESULT", payload={"status": "SUCCEEDED", "title": "业务结果",
                     "data": {"secret": "raw-secret"}, "files": [{"id": "forged-file"}],
                     "provenance": {"capability_id": "cap-private"}})
    projected = event_view(result)
    assert projected["payload"]["data"] is None
    assert projected["payload"]["files"] == []
    assert "raw-secret" not in json.dumps(projected)
    error = Event(workspace_id="ws-dev", conversation_id="conv-demo", run_id="run-demo", revision=1,
                  sequence=2, event_type="ERROR", payload={"code": "INTERNAL", "message": "password=raw-secret",
                    "trace_id": "call-private"})
    projected = event_view(error)
    assert projected["payload"] == {"message": "请求未完成，请查看运行状态", "code": "ERROR"}


def test_final_answer_translates_only_reserved_synthesis_metadata():
    from intent_platform.services import business_answer_text
    text = "account_id=A001，账户余额为 12000。`deterministic_balance_totals` 为空。"
    assert business_answer_text(text) == "account_id=A001，账户余额为 12000。`账户余额合计` 为空。"
    assert business_answer_text("pre_deterministic_balance_totals_note；account_id；customer_id") == "pre_deterministic_balance_totals_note；account_id；customer_id"
    event = Event(workspace_id="ws-dev", conversation_id="conv-demo", run_id="run-demo", revision=1,
        sequence=3, event_type="RESULT", payload={"title": "答复", "data": text, "component": "text"})
    assert "deterministic_balance_totals" not in event_view(event)["payload"]["data"]
    assert event.payload["data"] == text  # Historical immutable facts are not rewritten.


def test_new_and_historical_final_answers_share_poll_history_and_sse_projection(client, admin, seeded):
    import asyncio
    from intent_platform.auth import authenticate
    from intent_platform.main import events as stream_events
    from intent_platform.services import emit, final_payload
    headers, agent = _runtime(client, admin, seeded)
    cid = conversation(client, headers, agent["id"])
    rid = message(client, headers, cid, seeded["functions"][0]["name"] + " record_id=demo-001")
    drain()
    original = "已完成查询，account_id=A001；deterministic_balance_totals 为空。"
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin")
        run = db.get(Run, rid)
        old = emit(db, run, "RESULT", {"title": "答复", "component": "text", "data": original})
        fresh = emit(db, run, "RESULT", final_payload(db, run, "部分结果：" + original, True))
        db.flush()
        old_id, first_sequence, last_sequence = old.id, old.sequence, fresh.sequence
        assert "deterministic_balance_totals" not in fresh.payload["data"]
        assert old.payload["data"] == original
    responses = [client.get(f"/api/v2/runtime/runs/{rid}", headers=headers),
                 client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=headers, params={"after": first_sequence - 1}),
                 client.get(f"/api/v2/runtime/conversations/{cid}/history", headers=headers)]
    for response in responses:
        assert response.status_code == 200, response.text
        assert "deterministic_balance_totals" not in response.text
    projected = responses[1].json()
    assert all("account_id=A001" in event["payload"]["data"] for event in projected)
    assert all("账户余额合计" in event["payload"]["data"] for event in projected)
    with Session(engine) as db:
        token = authenticate(headers["Authorization"], db)
        response = stream_events(cid, first_sequence - 1, True, token, db)
        assert db.get(Event, old_id).payload["data"] == original
    async def read_final_chunks():
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
            if chunk.startswith(f"id: {last_sequence}\n"):
                await response.body_iterator.aclose()
                break
        return "".join(chunks)
    restored = asyncio.run(read_final_chunks())
    assert "deterministic_balance_totals" not in restored
    assert "account_id=A001" in restored and "账户余额合计" in restored


def test_revoked_function_blocks_run_projection(client, admin, seeded):
    headers, agent = _runtime(client, admin, seeded)
    cid = conversation(client, headers, agent["id"])
    function = seeded["functions"][0]
    rid = message(client, headers, cid, function["name"] + " record_id=demo-001")
    drain()
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin")
        run = db.get(Run, rid)
        assert run.status in {"SUCCEEDED", "FAILED"}
    try:
        response = client.put(f"/api/v2/management/workspaces/ws-dev/functions/{function['id']}/access",
                              headers=admin, json={"access_mode": "allowlist", "allowed_users": []})
        assert response.status_code == 200, response.text
        assert client.get(f"/api/v2/runtime/runs/{rid}", headers=headers).status_code == 403
        assert client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=headers).status_code == 403
    finally:
        client.put(f"/api/v2/management/workspaces/ws-dev/functions/{function['id']}/access",
                   headers=admin, json={"access_mode": "workspace", "allowed_users": []})


def test_demo_object_revocation_blocks_restored_run_events_history_stream_and_file(client, admin):
    from intent_platform.worker import tick

    seeded = client.post("/api/v2/dev/demo/seed", headers=admin, json={})
    assert seeded.status_code == 200, seeded.text
    agent = seeded.json()["agent"]
    login = client.post("/api/v2/dev/login", json={"user_id": "dev-reader"})
    assert login.status_code == 200, login.text
    parent = {"Authorization": "Bearer " + login.json()["access_token"]}
    session = client.post(f"/api/v2/runtime/apps/{agent['public_id']}/sessions", headers=parent, json={})
    assert session.status_code == 200, session.text
    runtime = {"Authorization": "Bearer " + session.json()["access_token"]}
    cid = conversation(client, runtime, agent["id"])
    rid = message(client, runtime, cid, "查询 C001 的所有账户余额，给我做资金概览")
    for _ in range(80):
        response = client.get(f"/api/v2/runtime/runs/{rid}", headers=runtime)
        assert response.status_code == 200, response.text
        if response.json()["status"] == "SUCCEEDED":
            break
        tick()
    else:
        raise AssertionError("Demo run did not complete")

    with Session(engine) as db, db.begin():
        # Calls and result_files are owner-scoped under RLS even for workspace admins.
        set_identity(db, "dev-reader")
        call = db.scalar(select(Call).where(Call.run_id == rid, Call.status == "SUCCEEDED").limit(1))
        assert call is not None
        file = ResultFile(workspace_id="ws-dev", conversation_id=cid, run_id=rid, call_id=call.id,
                          filename="demo.txt", media_type="text/plain", data=b"demo-result")
        db.add(file); db.flush(); file_id = file.id
    assert client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=runtime).status_code == 200
    assert client.get(f"/api/v2/runtime/conversations/{cid}/history", headers=runtime).status_code == 200
    assert client.get(f"/api/v2/runtime/files/{file_id}", headers=runtime).status_code == 200

    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin")
        state = db.get(DemoState, "ws-dev")
        assert state is not None
        saved = deepcopy(state.state)
        changed = deepcopy(saved)
        from intent_platform.demo.engine import initial_state
        changed.setdefault("_customer_permissions", initial_state()["_customer_permissions"])["dev-reader"] = []
        state.state = changed
    try:
        assert client.get(f"/api/v2/runtime/runs/{rid}", headers=runtime).status_code == 403
        assert client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=runtime).status_code == 403
        assert client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=runtime,
                          params={"stream": "true"}).status_code == 403
        assert client.get(f"/api/v2/runtime/conversations/{cid}/history", headers=runtime).status_code == 403
        assert client.get(f"/api/v2/runtime/files/{file_id}", headers=runtime).status_code == 403
    finally:
        with Session(engine) as db, db.begin():
            set_identity(db, "dev-admin")
            db.get(DemoState, "ws-dev").state = saved


def test_admin_trace_reads_other_and_revoked_user_without_runtime_cross_user_access(client, admin):
    from intent_platform.worker import tick

    seeded = client.post("/api/v2/dev/demo/seed", headers=admin, json={})
    assert seeded.status_code == 200, seeded.text
    agent = seeded.json()["agent"]
    login = client.post("/api/v2/dev/login", json={"user_id": "dev-reader"})
    assert login.status_code == 200, login.text
    parent = {"Authorization": "Bearer " + login.json()["access_token"]}
    session = client.post(f"/api/v2/runtime/apps/{agent['public_id']}/sessions", headers=parent, json={})
    assert session.status_code == 200, session.text
    runtime = {"Authorization": "Bearer " + session.json()["access_token"]}
    cid = conversation(client, runtime, agent["id"])
    rid = message(client, runtime, cid, "查询 C001 的所有账户余额，给我做资金概览")
    for _ in range(80):
        current = client.get(f"/api/v2/runtime/runs/{rid}", headers=runtime)
        assert current.status_code == 200, current.text
        if current.json()["status"] == "SUCCEEDED":
            break
        tick()
    else:
        raise AssertionError("Demo run did not complete")

    base = "/api/v2/management/workspaces/ws-dev/runs"
    listing = client.get(base, headers=admin, params={"run_id": rid})
    detail = client.get(f"{base}/{rid}/trace", headers=admin)
    assert listing.status_code == 200 and [row["run_id"] for row in listing.json()] == [rid]
    assert detail.status_code == 200, detail.text
    assert len(detail.json()["steps"]) == 3
    assert detail.json()["layers"]["entity"] and detail.json()["layers"]["planning"]
    assert client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).status_code in {403, 404}
    with Session(engine) as db:
        set_identity(db, "dev-reader")
        raw = db.execute(text("SELECT run_id FROM intent_admin_trace_runs(:ws,NULL,NULL,:run,NULL,NULL,10)"),
                         {"ws": "ws-dev", "run": rid}).all()
        assert raw == []  # The definer function itself must reject non-admin DB identities.

    other = client.post("/api/v2/dev/login", json={"user_id": "dev-other"})
    assert other.status_code == 200, other.text
    other_headers = {"Authorization": "Bearer " + other.json()["access_token"]}
    assert client.get(f"{base}/{rid}/trace", headers=other_headers).status_code == 403
    assert client.get(f"/api/v2/management/workspaces/ws-other/runs/{rid}/trace",
                      headers=other_headers).status_code == 404

    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin")
        member = db.get(Membership, ("dev-reader", "ws-dev"))
        assert member is not None
        prior_active = member.active
        member.active = False
    try:
        assert client.get(base, headers=admin, params={"run_id": rid}).json()[0]["run_id"] == rid
        revoked = client.get(f"{base}/{rid}/trace", headers=admin)
        assert revoked.status_code == 200 and len(revoked.json()["steps"]) == 3
        assert client.get(f"/api/v2/runtime/runs/{rid}", headers=runtime).status_code in {401, 403, 404}
    finally:
        with Session(engine) as db, db.begin():
            set_identity(db, "dev-admin")
            db.get(Membership, ("dev-reader", "ws-dev")).active = prior_active
