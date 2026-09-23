import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from intent_platform.db import engine, set_identity
from intent_platform.models import Function, Release, Call, Run, Event
from conftest import conversation, message, drain


def test_true_postgres_rls_identity_and_immutable_release(client, admin, seeded):
    with Session(engine) as db:
        row = db.execute(text("SELECT current_user, rolbypassrls, rolsuper FROM pg_roles WHERE rolname = current_user")).first()
        assert row[0] == "intent_app" and row[1:] == (False, False)
        assert db.scalar(select(Function).where(Function.id == seeded["functions"][0]["id"])) is None
        set_identity(db, "dev-other")
        assert db.scalar(select(Function).where(Function.id == seeded["functions"][0]["id"])) is None
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE agent_releases SET version = 999 WHERE id=:id"), {"id": seeded["agent"]["current_release_id"]})
        db.rollback()
    other = client.post("/api/v2/dev/login", json={"user_id": "dev-other"}).json()["access_token"]
    assert client.get("/api/v2/management/workspaces/ws-dev/functions", headers={"Authorization": "Bearer " + other}).status_code == 403


def test_vertical_chain_dedup_outbox_and_cursor(client, admin, seeded):
    cid = conversation(client, admin, seeded["agent"]["id"])
    payload = {"text": seeded["agent"]["suggested_questions"][0], "client_message_id": str(uuid.uuid4())}
    def send(): return client.post(f"/api/v2/runtime/conversations/{cid}/messages", headers=admin, json=payload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: send(), range(2)))
    assert all(r.status_code == 202 for r in responses)
    assert len({r.json()["run_id"] for r in responses}) == 1
    rid = responses[0].json()["run_id"]; drain()
    run = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    assert run["status"] == "SUCCEEDED" and len(run["calls"]) == 1
    events = client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=admin).json()
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert any(e["event_type"] == "RESULT" and e["payload"]["fixture_mode"] for e in events)
    assert client.get(f"/api/v2/runtime/conversations/{cid}/events?after={events[-1]['sequence']}", headers=admin).json() == []
    drain()
    assert len(client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()["calls"]) == 1


def test_arguments_revision_and_new_message_invalidation(client, admin, seeded):
    cid = conversation(client, admin, seeded["agent"]["id"])
    rid = message(client, admin, cid, seeded["agent"]["suggested_questions"][1]); drain()
    run = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    assert run["status"] == "WAITING_INPUT"
    answer = {"revision": run["revision"], "question_id": run["question"]["id"], "values": {"record_id": "actual-test-input"}}
    assert client.post(f"/api/v2/runtime/runs/{rid}/answers", headers=admin, json=answer).status_code == 200
    assert client.post(f"/api/v2/runtime/runs/{rid}/answers", headers=admin, json=answer).status_code == 409
    drain()
    assert client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()["status"] == "SUCCEEDED"
    rid2 = message(client, admin, cid, seeded["agent"]["suggested_questions"][1]); drain()
    before = client.get(f"/api/v2/runtime/runs/{rid2}", headers=admin).json()
    message(client, admin, cid, "unsupported utterance")
    assert client.post(f"/api/v2/runtime/runs/{rid2}/answers", headers=admin, json={"revision": before["revision"], "question_id": before["question"]["id"], "values": {"record_id": "x"}}).status_code == 409
    drain()


def test_ticket_atomic_replay_scope_logout_and_user_isolation(client, admin, seeded):
    aid = seeded["agent"]["id"]; origin = "http://localhost:5173"
    hs = client.post("/api/v2/runtime/embed/handshakes", json={"agent_id": aid, "host_origin": origin}).json()
    b = {"agent_id": aid, "host_origin": origin, "instance_nonce": hs["instance_nonce"]}
    ticket = client.post("/api/v2/integrations/embed-tickets", headers=admin, json=b).json()
    exchange = {**b, "ticket": ticket["ticket"]}
    assert client.post("/api/v2/runtime/embed/exchange", json={**exchange, "host_origin": "http://evil.test"}).status_code == 403
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post("/api/v2/runtime/embed/exchange", json=exchange), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 403]
    token = next(r.json()["access_token"] for r in responses if r.status_code == 200)
    runtime = {"Authorization": "Bearer " + token}
    assert client.get("/api/v2/management/workspaces", headers=runtime).status_code == 403
    cid = conversation(client, runtime, aid)
    rid = message(client, runtime, cid, seeded["agent"]["suggested_questions"][0])
    reader_token = client.post("/api/v2/dev/login", json={"user_id": "dev-reader"}).json()["access_token"]
    assert client.get(f"/api/v2/runtime/conversations/{cid}/events", headers={"Authorization": "Bearer " + reader_token}).status_code in {403, 404}
    assert client.post("/api/v2/runtime/sessions/logout", headers=admin).status_code == 200
    drain()
    assert client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=runtime).status_code == 401
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        assert not list(db.scalars(select(Call).where(Call.run_id == rid)))


def test_conversation_pins_release_and_draft_revision(client, admin):
    from test_application_sessions import new_app
    app, _ = new_app(client, admin)
    aid = app["id"]
    before = client.post(f"/api/v2/runtime/agents/{aid}/conversations", headers=admin).json()
    a = next(a for a in client.get("/api/v2/management/workspaces/ws-dev/agents", headers=admin).json() if a["id"] == aid)
    patch = {"name": a["name"], "capability_ids": a["capability_ids"], "allowed_origins": a["allowed_origins"], "suggested_questions": a["suggested_questions"], "revision": a["revision"]}
    assert client.patch(f"/api/v2/management/workspaces/ws-dev/agents/{aid}/draft", headers=admin, json=patch).status_code == 200
    assert client.patch(f"/api/v2/management/workspaces/ws-dev/agents/{aid}/draft", headers=admin, json=patch).status_code == 409
    assert client.post(f"/api/v2/management/workspaces/ws-dev/agents/{aid}/publish", headers=admin, json={"revision": a["revision"]}).status_code == 409
    response = client.post(f"/api/v2/management/workspaces/ws-dev/agents/{aid}/publish", headers=admin, json={"revision": a["revision"] + 1})
    assert response.status_code == 200, response.text
    published = response.json()
    after = client.post(f"/api/v2/runtime/agents/{aid}/conversations", headers=admin).json()
    assert before["release_id"] != after["release_id"] == published["release_id"]


def test_invalid_model_output_is_terminal_not_lease_loop(client, admin, seeded, monkeypatch):
    from intent_platform.worker import tick
    monkeypatch.setattr("intent_platform.worker.route_model", lambda *a: {"decision": "READY", "requests": ["not-an-object"]})
    cid = conversation(client, admin, seeded["agent"]["id"])
    rid = message(client, admin, cid, "invalid proposal")
    drain()
    run = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    assert run["status"] == "FAILED" and run["decision"] == "ERROR"
