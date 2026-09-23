import json
import uuid
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from intent_platform.db import engine, set_identity, now
from intent_platform.models import Call, Run
from intent_platform.worker import claim_run, prepare_run, claim_call, process_call
from conftest import conversation, message, drain


def clone_agent(client, admin, seeded):
    b = {"name": "Lifecycle fixture " + uuid.uuid4().hex[:6], "capability_ids": seeded["agent"]["capability_ids"], "allowed_origins": ["http://localhost:5173"]}
    a = client.post("/api/v2/management/workspaces/ws-dev/agents", headers=admin, json=b).json()
    assert client.post(f"/api/v2/management/workspaces/ws-dev/agents/{a['id']}/publish", headers=admin).status_code == 200
    return a


def test_new_message_cancels_claimed_but_unsent_action(client, admin, seeded):
    cid = conversation(client, admin, seeded["agent"]["id"])
    rid = message(client, admin, cid, seeded["agent"]["suggested_questions"][0])
    work = claim_run("dev-admin"); assert work["id"] == rid; prepare_run(work)
    dispatched = claim_call("dev-admin"); assert dispatched["run_id"] == rid
    message(client, admin, cid, "changed goal unsupported")
    process_call(dispatched)
    old = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    assert old["status"] == "CANCELLED" and old["calls"][0]["status"] == "CANCELLED"
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        assert db.get(Call, dispatched["id"]).dispatched_at is None
    drain()


def test_revoked_origin_invalidates_existing_runtime_and_queued_work(client, admin, seeded):
    a = clone_agent(client, admin, seeded); aid = a["id"]
    hs = client.post("/api/v2/runtime/embed/handshakes", json={"agent_id": aid, "host_origin": "http://localhost:5173"}).json()
    b = {"agent_id": aid, "host_origin": "http://localhost:5173", "instance_nonce": hs["instance_nonce"]}
    ticket = client.post("/api/v2/integrations/embed-tickets", headers=admin, json=b).json()
    rt = client.post("/api/v2/runtime/embed/exchange", json={**b, "ticket": ticket["ticket"]}).json()["access_token"]
    headers = {"Authorization": "Bearer " + rt}
    cid = conversation(client, headers, aid); rid = message(client, headers, cid, seeded["agent"]["suggested_questions"][0])
    assert client.post(f"/api/v2/management/workspaces/ws-dev/agents/{aid}/revoke-origin", headers=admin, json={"origin": b["host_origin"]}).status_code == 200
    assert client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=headers).status_code == 401
    assert client.post("/api/v2/runtime/embed/handshakes", json={"agent_id": aid, "host_origin": b["host_origin"]}).status_code == 403
    drain()
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        assert not list(db.scalars(select(Call).where(Call.run_id == rid)))


def test_all_confirmation_actions_and_independent_failure(client, admin, monkeypatch):
    ids, aliases = [], []
    for index in range(2):
        alias = "multi-" + uuid.uuid4().hex[:7]; aliases.append(alias)
        definition = {"name": alias, "description": "Independent local read fixture", "aliases": [alias], "requires_confirmation": index == 1}
        r = client.post("/api/v2/management/workspaces/ws-dev/functions/import", headers=admin, json={"definition": definition})
        fid = r.json()["id"]; ids.append(fid)
        assert client.post(f"/api/v2/management/workspaces/ws-dev/functions/{fid}/validate", headers=admin, json={}).json()["valid"]
        assert client.post(f"/api/v2/management/workspaces/ws-dev/functions/{fid}/publish", headers=admin).status_code == 200
    a = client.post("/api/v2/management/workspaces/ws-dev/agents", headers=admin, json={"name": "Independent fixture", "capability_ids": ids}).json()
    assert client.post(f"/api/v2/management/workspaces/ws-dev/agents/{a['id']}/publish", headers=admin).status_code == 200
    cid = conversation(client, admin, a["id"]); rid = message(client, admin, cid, ";".join(aliases)); drain()
    run = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    assert run["status"] == "WAITING_CONFIRMATION" and run["calls"] == []
    events = client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=admin).json()
    confirmation = next(e["payload"] for e in events if e["event_type"] == "CONFIRMATION")
    assert [x["name"] for x in confirmation["actions"]] == aliases
    assert all("capability_id" not in x for x in confirmation["actions"])
    assert client.post(f"/api/v2/runtime/runs/{rid}/confirmations", headers=admin, json={"revision": run["revision"], "confirmed": True}).status_code == 200
    executed = []
    def connector(definition, *args, **kwargs):
        executed.append(definition["name"])
        return {"status": "FAILED" if definition["name"] == aliases[0] else "SUCCEEDED", "data": {"fixture": True}, "fixture_mode": True}
    monkeypatch.setattr("intent_platform.worker.execute_connector", connector)
    drain()
    assert executed == aliases
    result = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    assert result["status"] == "FAILED" and {c["status"] for c in result["calls"]} == {"FAILED", "SUCCEEDED"}


def test_reconciling_lease_recovers_without_business_replay(client, admin, seeded, monkeypatch):
    from intent_platform.worker import reconcile_one
    cid = conversation(client, admin, seeded["agent"]["id"])
    rid = message(client, admin, cid, seeded["agent"]["suggested_questions"][0]); drain()
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin")
        call = db.scalar(select(Call).where(Call.run_id == rid))
        definition = dict(call.definition); definition["connector"] = {**definition["connector"], "status_endpoint": "http://127.0.0.1/status/{operation_id}"}
        call.definition = definition; call.status = "RECONCILING"; call.operation_id = "previous-operation"; call.lease_until = now() - timedelta(seconds=1)
        db.get(Run, rid).status = "UNKNOWN"
    queries = []
    def query(*args, **kwargs):
        queries.append(kwargs.get("reconcile_operation"))
        return {"status": "SUCCEEDED", "data": {"fixture": True}, "fixture_mode": True}
    monkeypatch.setattr("intent_platform.worker.execute_connector", query)
    assert reconcile_one("dev-admin")
    assert queries == ["previous-operation"]
    assert client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()["status"] == "SUCCEEDED"


def test_openapi_core_response_models_are_explicit(client):
    document = client.get("/openapi.json").json()
    paths = document["paths"]
    checks = [("/api/v2/dev/login", "post", "200"), ("/api/v2/management/workspaces/{ws}/functions/import", "post", "201"),
              ("/api/v2/management/workspaces/{ws}/agents/{aid}/publish", "post", "200"),
              ("/api/v2/runtime/runs/{rid}", "get", "200"), ("/api/v2/runtime/conversations/{cid}/events", "get", "200")]
    for path, method, status in checks:
        schema = paths[path][method]["responses"][status]["content"]["application/json"]["schema"]
        assert schema and ("$ref" in schema or "items" in schema)
    assert document["components"]["schemas"]["LoginBody"]["additionalProperties"] is False
