"""Restricted PostgreSQL and loopback identity fixtures; no real SSO acceptance."""
import copy
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from intent_platform.auth import issue_token
from intent_platform.config import settings
from intent_platform.db import engine, set_identity, now
from intent_platform.models import Workspace, Membership, Agent, Release, RuntimeSession, Run, Call, ProcessingAttempt
from intent_platform.worker import claim_run, prepare_run, claim_call, process_call, mark_dispatch
from test_application_sessions import new_app, runtime, embed, MG
from conftest import conversation, message


@pytest.fixture
def identity_service(monkeypatch):
    state = {"identity": {"active": False}}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            data = json.dumps(state["identity"]).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    monkeypatch.setattr(settings(), "identity_introspection_url", f"http://127.0.0.1:{server.server_port}/introspect")
    yield state
    server.shutdown(); server.server_close(); thread.join(3)


def trusted_app(client, admin, identity_service, monkeypatch):
    app, function = new_app(client, admin)
    profile = {"label": "Synthetic trusted identity", "version": "1", "workspace_ids": ["ws-dev"], "agent_ids": [app["id"]],
               "user_ids": ["dev-admin", "dev-reader"], "channels": ["chat", "embed"], "origins": ["http://localhost:5173"]}
    monkeypatch.setattr(settings(), "integrations_json", json.dumps({"local-a": profile, "local-b": profile}))
    body = {"revision": app["revision"], "name": app["name"], "capability_ids": app["capability_ids"], "allowed_origins": app["allowed_origins"], "integration_refs": ["local-a", "local-b"]}
    changed = client.patch(MG + f"/agents/{app['id']}/draft", headers=admin, json=body)
    assert changed.status_code == 200, changed.text
    app = changed.json()
    assert client.post(MG + f"/agents/{app['id']}/publish", headers=admin, json={"revision": app["revision"]}).status_code == 200
    identity_service["identity"] = {"active": True, "sub": "dev-admin", "delegation": {"integration_id": "local-a", "workspace_ids": ["ws-dev"], "agent_ids": [app["id"]], "user_ids": ["dev-admin"], "origins": ["http://localhost:5173"]}}
    return app, function


def exchange_identity(client, public_id=None, session_id=None):
    body = {"identity_token": "synthetic-upstream-token"}
    if public_id: body["public_id"] = public_id
    if session_id: body["runtime_session_id"] = session_id
    return client.post("/api/v2/auth/exchange", json=body)


@pytest.mark.parametrize("bad", ["false_string", "integer", "sub_empty", "delegation_list", "list_string", "wildcard", "unapproved_ref", "wrong_user", "origin_injection"])
def test_introspection_strict_types_and_approved_delegation(client, admin, identity_service, monkeypatch, bad):
    app, _ = trusted_app(client, admin, identity_service, monkeypatch)
    value = identity_service["identity"]
    if bad == "false_string": value["active"] = "false"
    if bad == "integer": value["active"] = 1
    if bad == "sub_empty": value["sub"] = " "
    if bad == "delegation_list": value["delegation"] = []
    if bad == "list_string": value["delegation"]["agent_ids"] = app["id"]
    if bad == "wildcard": value["delegation"]["agent_ids"] = ["*"]
    if bad == "unapproved_ref": value["delegation"]["integration_id"] = "unapproved"
    if bad == "wrong_user": value["delegation"]["user_ids"] = ["dev-reader"]
    if bad == "origin_injection": value["delegation"]["origins"] = ["https://bank.test; script-src *"]
    assert exchange_identity(client, app["public_id"]).status_code in {401, 403}


def test_pinned_integration_renewal_revoke_and_rollback(client, admin, identity_service, monkeypatch):
    app, function = trusted_app(client, admin, identity_service, monkeypatch)
    response = exchange_identity(client, app["public_id"]); assert response.status_code == 200, response.text
    token = response.json(); headers = {"Authorization": "Bearer " + token["access_token"]}
    cid = conversation(client, headers, app["id"])
    rid = message(client, headers, cid, function["name"])
    old = client.get(MG + f"/agents/{app['id']}/releases", headers=admin).json()[0]["release_id"]
    identity_service["identity"]["delegation"]["integration_id"] = "local-b"
    assert exchange_identity(client, app["public_id"], token["runtime_session_id"]).status_code == 403
    identity_service["identity"]["delegation"]["integration_id"] = "local-a"
    renewed = exchange_identity(client, app["public_id"], token["runtime_session_id"]); assert renewed.status_code == 200, renewed.text
    headers = {"Authorization": "Bearer " + renewed.json()["access_token"]}
    assert client.get(f"/api/v2/runtime/conversations/{cid}/history", headers=headers).status_code == 200
    assert client.post(MG + f"/agents/{app['id']}/revoke-integration", headers=admin, json={"integration_ref": "local-a"}).status_code == 200
    for path in (f"/api/v2/runtime/conversations/{cid}/history", f"/api/v2/runtime/conversations/{cid}/events", f"/api/v2/runtime/runs/{rid}/attempts"):
        assert client.get(path, headers=headers).status_code in {401, 403}
    assert exchange_identity(client, app["public_id"], token["runtime_session_id"]).status_code == 403
    assert client.post(MG + f"/agents/{app['id']}/rollback", headers=admin, json={"release_id": old}).status_code == 200
    assert exchange_identity(client, app["public_id"], token["runtime_session_id"]).status_code == 403
    assert claim_run("dev-admin")["done"] is True
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        assert db.get(Run, rid).decision == "FORBIDDEN"
        assert not list(db.scalars(select(Call).where(Call.run_id == rid)))


def test_embed_ticket_integration_cannot_change_or_survive_revoke(client, admin, identity_service, monkeypatch):
    app, _ = trusted_app(client, admin, identity_service, monkeypatch)
    issued = exchange_identity(client); assert issued.status_code == 200, issued.text
    headers = {"Authorization": "Bearer " + issued.json()["access_token"]}
    body = {"agent_id": app["id"], "host_origin": "http://localhost:5173"}
    body["instance_nonce"] = client.post("/api/v2/runtime/embed/handshakes", json=body).json()["instance_nonce"]
    ticket = client.post("/api/v2/integrations/embed-tickets", headers=headers, json=body); assert ticket.status_code == 200, ticket.text
    client.post(MG + f"/agents/{app['id']}/revoke-integration", headers=admin, json={"integration_ref": "local-a"})
    assert client.post("/api/v2/runtime/embed/exchange", json={**body, "ticket": ticket.json()["ticket"]}).status_code == 403


def test_catalog_is_workspace_scoped_and_contains_no_transport_secrets(client, admin, identity_service, monkeypatch):
    app, _ = trusted_app(client, admin, identity_service, monkeypatch)
    monkeypatch.setattr(settings(), "credentials_json", json.dumps({"here": {"workspace_ids": ["ws-dev"], "connector_kind": "http", "endpoint": "http://127.0.0.1/private", "value": "not-in-catalog"}, "other": {"workspace_ids": ["ws-other"], "connector_kind": "http", "value": "other-secret"}}))
    result = client.get(MG + "/catalogs", headers=admin); assert result.status_code == 200, result.text
    assert result.json()["credentials"] == [{"ref": "here", "connector_kind": "http"}]
    assert "not-in-catalog" not in result.text and "endpoint" not in result.text and "agent_ids" not in result.text and "other-secret" not in result.text


@pytest.fixture
def isolated_workspace(client):
    key = uuid.uuid4().hex; ws = "governance-" + key; user = "admin-" + key; other = "user-" + key
    with Session(engine) as db, db.begin():
        db.add(Workspace(id=ws, name="Synthetic governance fixture")); db.flush()
        db.add_all([Membership(workspace_id=ws, user_id=user, roles=["admin", "user"]), Membership(workspace_id=ws, user_id=other, roles=["user"])])
        db.flush(); tokens = {u: issue_token(db, u).id for u in (user, other)}
    return ws, user, other, {u: {"Authorization": "Bearer " + token} for u, token in tokens.items()}


def isolated_app(client, fixture):
    ws, user, other, headers = fixture; prefix = f"/api/v2/management/workspaces/{ws}"
    function = client.post(prefix + "/functions/import", headers=headers[user], json={"definition": {"name": "synthetic-governance", "description": "Local quota fixture"}}).json()
    assert client.post(prefix + f"/functions/{function['id']}/validate", headers=headers[user], json={}).json()["valid"]
    assert client.post(prefix + f"/functions/{function['id']}/publish", headers=headers[user]).status_code == 200
    app = client.post(prefix + "/agents", headers=headers[user], json={"name": "Local caps", "capability_ids": [function["id"]]}).json()
    assert client.post(prefix + f"/agents/{app['id']}/publish", headers=headers[user]).status_code == 200
    return app, function


def test_member_ceiling_self_guard_stale_revision_and_live_removal(client, isolated_workspace, monkeypatch):
    ws, user, other, headers = isolated_workspace; prefix = f"/api/v2/management/workspaces/{ws}"
    assert client.get(prefix + "/members", headers=headers[other]).status_code == 403
    body = {"revision": 1, "roles": ["admin"], "active": True}
    assert client.put(prefix + f"/members/{other}", headers=headers[user], json=body).status_code == 403
    assert client.put(prefix + f"/members/{user}", headers=headers[user], json=body).status_code == 403
    body["roles"] = ["user"]; body["active"] = False
    result = client.put(prefix + f"/members/{other}", headers=headers[user], json=body); assert result.status_code == 200, result.text
    assert result.json()["revision"] == 2 and not result.json()["active"]
    assert client.put(prefix + f"/members/{other}", headers=headers[user], json=body).status_code == 409
    assert client.get("/api/v2/me", headers=headers[other]).json()["workspaces"] == []
    newcomer = "new-" + uuid.uuid4().hex
    monkeypatch.setattr(settings(), "membership_grants_json", json.dumps({ws: {user: {"roles": ["admin", "user"], "active": True}, newcomer: {"roles": ["maintainer", "user"], "active": True}}}))
    rows = client.get(prefix + "/members", headers=headers[user]).json()
    assert next(r for r in rows if r["user_id"] == newcomer)["revision"] == 0
    created = client.put(prefix + f"/members/{newcomer}", headers=headers[user], json={"revision": 0, "roles": ["user"], "active": True})
    assert created.status_code == 200 and created.json()["revision"] == 1


def test_distributed_queue_routing_execution_caps_and_attempt_evidence(client, isolated_workspace, monkeypatch):
    ws, user, other, headers = isolated_workspace; prefix = f"/api/v2/management/workspaces/{ws}"
    monkeypatch.setattr(settings(), "workspace_limits_json", json.dumps({ws: {"max_queued_runs": 1, "max_routing": 1, "max_execution": 1}}))
    app, function = isolated_app(client, isolated_workspace)
    cids = [conversation(client, headers[u], app["id"]) for u in (user, other)]
    barrier = threading.Barrier(2)
    def send(i):
        barrier.wait()
        return client.post(f"/api/v2/runtime/conversations/{cids[i]}/messages", headers=headers[(user, other)[i]], json={"text": function["name"], "client_message_id": "same"})
    with ThreadPoolExecutor(max_workers=2) as pool: responses = list(pool.map(send, (0, 1)))
    assert sorted(r.status_code for r in responses) == [202, 429]
    winner = next(i for i, r in enumerate(responses) if r.status_code == 202); loser = 1 - winner
    users = (user, other); rid = responses[winner].json()["run_id"]
    duplicate = client.post(f"/api/v2/runtime/conversations/{cids[winner]}/messages", headers=headers[users[winner]], json={"text": function["name"], "client_message_id": "same"})
    assert duplicate.status_code == 202 and duplicate.json()["duplicate"]
    first = claim_run(users[winner]); assert first and not first.get("done")
    second_rid = message(client, headers[users[loser]], cids[loser], function["name"])
    assert claim_run(users[loser]) is None
    prepare_run(first)
    second = claim_run(users[loser]); assert second; prepare_run(second)
    first_call = claim_call(users[winner]); assert first_call
    assert claim_call(users[loser]) is None
    process_call(first_call)
    second_call = claim_call(users[loser]); assert second_call; process_call(second_call)
    rows = client.get(prefix + f"/runs/{rid}/attempts", headers=headers[user]).json()
    routing = next(r for r in rows if r["kind"] == "routing")
    execution = next(r for r in rows if r["kind"] == "execution")
    assert routing["queue_ms"] >= 0 and routing["model_ms"] is not None and routing["validation_ms"] is not None and routing["context_ms"] is None
    assert routing["total_ms"] >= routing["model_ms"] and routing["status"] == "completed"
    assert execution["connector_ms"] is not None and execution["queue_ms"] is None and execution["queued_at"] is None
    assert client.get(prefix + "/limits", headers=headers[user]).json()["usage"] == {"queued_runs": 0, "routing": 0, "execution": 0}
    assert client.put(prefix + "/limits", headers=headers[user], json={"revision": 0, "limits": {"max_queued_runs": 2, "max_routing": 1, "max_execution": 1}}).status_code == 403


def test_expired_attempt_is_unknown_and_cannot_dispatch(client, isolated_workspace):
    ws, user, other, headers = isolated_workspace
    app, function = isolated_app(client, isolated_workspace)
    cid = conversation(client, headers[user], app["id"]); rid = message(client, headers[user], cid, function["name"])
    work = claim_run(user); prepare_run(work); call = claim_call(user)
    with Session(engine) as db, db.begin():
        set_identity(db, user); attempt = db.get(ProcessingAttempt, call["attempt_id"]); attempt.lease_until = now() - timedelta(seconds=1)
    with pytest.raises(PermissionError): mark_dispatch(call)
    with Session(engine) as db:
        set_identity(db, user); assert db.get(Call, call["id"]).dispatched_at is None
        set_identity(db, "unrelated-user"); assert not list(db.scalars(select(ProcessingAttempt).where(ProcessingAttempt.run_id == rid)))
        flags = db.execute(text("SELECT relforcerowsecurity FROM pg_class WHERE relname IN ('workspace_governance','processing_attempts')")).scalars().all()
        assert flags == [True, True]


def test_single_space_identity_cannot_administer_another_membership(client, admin, identity_service, monkeypatch, isolated_workspace):
    app, _ = trusted_app(client, admin, identity_service, monkeypatch)
    ws, user, other, headers = isolated_workspace
    with Session(engine) as db, db.begin():
        db.add(Membership(workspace_id=ws, user_id="dev-admin", roles=["admin", "user"]))
    issued = exchange_identity(client); assert issued.status_code == 200, issued.text
    token = {"Authorization": "Bearer " + issued.json()["access_token"]}
    assert [row["id"] for row in client.get("/api/v2/me", headers=token).json()["workspaces"]] == ["ws-dev"]
    assert client.get(f"/api/v2/management/workspaces/{ws}/members", headers=token).status_code == 403
    assert client.put(f"/api/v2/management/workspaces/{ws}/limits", headers=token, json={"revision": 0, "limits": {}}).status_code == 403
    with Session(engine) as db, db.begin():
        db.get(Membership, ("dev-admin", ws)).active = False  # close only this test's extra membership


def test_old_session_retains_old_history_but_cannot_create_new_identity_release(client, admin, identity_service, monkeypatch):
    app, function = trusted_app(client, admin, identity_service, monkeypatch)
    response = exchange_identity(client, app["public_id"]); assert response.status_code == 200, response.text
    headers = {"Authorization": "Bearer " + response.json()["access_token"]}
    cid = conversation(client, headers, app["id"])
    changed = client.patch(MG + f"/agents/{app['id']}/draft", headers=admin, json={"revision": app["revision"], "name": app["name"], "capability_ids": app["capability_ids"], "integration_refs": ["local-b"]}).json()
    assert client.post(MG + f"/agents/{app['id']}/publish", headers=admin, json={"revision": changed["revision"]}).status_code == 200
    assert client.get(f"/api/v2/runtime/conversations/{cid}/history", headers=headers).status_code == 200
    assert client.post(f"/api/v2/runtime/agents/{app['id']}/conversations", headers=headers).status_code == 403
    assert client.get(f"/api/v2/runtime/agents/{app['id']}", headers=headers).status_code == 403
    identity_service["identity"]["delegation"]["integration_id"] = "local-b"
    new_identity = exchange_identity(client, app["public_id"]); assert new_identity.status_code == 200
    assert client.post(f"/api/v2/runtime/agents/{app['id']}/conversations", headers={"Authorization": "Bearer " + new_identity.json()["access_token"]}).status_code == 201


def test_two_worker_connections_share_one_routing_slot(client, isolated_workspace, monkeypatch):
    ws, user, other, headers = isolated_workspace
    monkeypatch.setattr(settings(), "workspace_limits_json", json.dumps({ws: {"max_queued_runs": 2, "max_routing": 1, "max_execution": 1}}))
    app, function = isolated_app(client, isolated_workspace)
    for u in (user, other): message(client, headers[u], conversation(client, headers[u], app["id"]), function["name"])
    barrier = threading.Barrier(2)
    def claim(u): barrier.wait(); return claim_run(u)
    with ThreadPoolExecutor(max_workers=2) as pool: work = list(pool.map(claim, (user, other)))
    assert sum(item is not None for item in work) == 1
    assert client.get(f"/api/v2/management/workspaces/{ws}/limits", headers=headers[user]).json()["usage"] == {"queued_runs": 1, "routing": 1, "execution": 0}
    prepare_run(next(item for item in work if item))


def test_pre_dispatch_revocation_has_no_connector_timing(client, isolated_workspace, monkeypatch):
    ws, user, other, headers = isolated_workspace
    app, function = isolated_app(client, isolated_workspace)
    rid = message(client, headers[user], conversation(client, headers[user], app["id"]), function["name"])
    prepare_run(claim_run(user)); work = claim_call(user)
    client.post(f"/api/v2/management/workspaces/{ws}/agents/{app['id']}/revoke-integration", headers=headers[user], json={"integration_ref": "development"})
    def unexpected(*args, **kwargs): raise AssertionError("Connector must not be invoked")
    monkeypatch.setattr("intent_platform.worker.execute_connector", unexpected)
    process_call(work)
    rows = client.get(f"/api/v2/management/workspaces/{ws}/runs/{rid}/attempts", headers=headers[user]).json()
    execution = next(row for row in rows if row["kind"] == "execution")
    assert execution["connector_ms"] is None and execution["total_ms"] is not None


def test_late_reconciliation_response_cannot_overwrite_new_attempt(client, isolated_workspace):
    from intent_platform.capacity import claim_attempt
    from intent_platform.worker import finish_call
    ws, user, other, headers = isolated_workspace
    app, function = isolated_app(client, isolated_workspace)
    rid = message(client, headers[user], conversation(client, headers[user], app["id"]), function["name"])
    prepare_run(claim_run(user)); stale = claim_call(user)
    with Session(engine) as db, db.begin():
        set_identity(db, user)
        old = db.get(ProcessingAttempt, stale["attempt_id"]); old.kind = "reconciliation"; old.lease_until = now() - timedelta(seconds=1)
        call = db.get(Call, stale["id"]); call.status = "RECONCILING"
        new = claim_attempt(db, db.get(Run, rid), "reconciliation", now() + timedelta(seconds=60), call.id)
        fresh = {**stale, "attempt_id": new.id}
    after_claim, stale_finished = threading.Event(), threading.Event()
    def late():
        after_claim.wait(5)
        finish_call(stale, {"status": "UNKNOWN", "data": {"old": True}})
        stale_finished.set()
    thread = threading.Thread(target=late); thread.start(); after_claim.set(); assert stale_finished.wait(5); thread.join(5)
    with Session(engine) as db:
        set_identity(db, user); assert db.get(Call, stale["id"]).status == "RECONCILING"
    finish_call(fresh, {"status": "SUCCEEDED", "data": {"current": True}})
    with Session(engine) as db:
        set_identity(db, user)
        assert db.get(Call, stale["id"]).result["data"] == {"current": True}
        old = db.get(ProcessingAttempt, stale["attempt_id"])
        assert old.status == "lease_expired" and old.finished_at is None and old.timings.get("total_ms") is None
