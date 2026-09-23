"""Restricted PostgreSQL plus real loopback resolver for shadow API authorization."""
import copy
import threading
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from intent_platform.config import settings
from intent_platform.db import engine, set_identity
from intent_platform.models import Run, Call, Event, Release, Agent
from test_domain_runtime import resolver_service, semantic_app, WS
from conftest import conversation, message, drain


def payload(app):
    return {"release_id": app["release_id"], "text": app["alias"] + " target=one"}


def endpoint(app): return f"/api/v2/runtime/conversations/{app['cid']}/shadow-comparison"


def counts(cid):
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        return [db.scalar(select(func.count()).select_from(model).where(model.conversation_id == cid)) for model in (Run, Event)] + [db.scalar(select(func.count()).select_from(Call).join(Run).where(Run.conversation_id == cid))]


def test_shadow_is_opt_in_owner_publisher_and_has_no_ledger_side_effects(client, admin, semantic_app, monkeypatch):
    app = semantic_app
    assert client.post(endpoint(app), headers=app["headers"], json=payload(app)).status_code == 403
    monkeypatch.setattr(settings(), "allow_shadow_evaluation", True)
    assert client.post(endpoint(app), headers=admin, json=payload(app)).status_code == 403
    assert client.post(endpoint(app), headers=app["headers"], json={**payload(app), "expected": "READY"}).status_code == 422
    before = counts(app["cid"]); business = app["state"]["business"]
    result = client.post(endpoint(app), headers=app["headers"], json=payload(app))
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["profile"]["prompt_version"] == "router-v3"
    assert [r["decision"] for r in data["variants"]] == ["NEEDS_ARGUMENTS", "READY", "READY"]
    assert data["business_execution"] == {"status": "NOT_EXECUTED", "count": 0}
    assert counts(app["cid"]) == before and app["state"]["business"] == business
    assert all(r["model_request"] is None and r["model_ms"] is None for r in data["variants"])
    other = client.post(f"/api/v2/runtime/apps/{app['agent']['public_id']}/sessions", headers=admin, json={}).json()
    assert client.post(endpoint(app), headers={"Authorization": "Bearer " + other["access_token"]}, json=payload(app)).status_code == 403
    reader = client.post("/api/v2/dev/login", json={"user_id": "dev-reader"}).json()
    r = client.post(f"/api/v2/runtime/apps/{app['agent']['public_id']}/sessions", headers={"Authorization": "Bearer " + reader["access_token"]}, json={}).json()
    reader_headers = {"Authorization": "Bearer " + r["access_token"]}
    cid = conversation(client, reader_headers, app["agent"]["id"])
    assert client.post(f"/api/v2/runtime/conversations/{cid}/shadow-comparison", headers=reader_headers, json=payload(app)).status_code == 403


def test_shadow_workspace_limit_across_independent_runtime_sessions(client, admin, semantic_app, monkeypatch):
    import intent_platform.shadow_api as shadow
    app = semantic_app; monkeypatch.setattr(settings(), "allow_shadow_evaluation", True)
    entered = threading.Event(); resume = threading.Event(); original = shadow.model_attempt
    def wait_model(*args):
        if args[-1] == "A": entered.set(); assert resume.wait(10)
        return original(*args)
    monkeypatch.setattr(shadow, "model_attempt", wait_model)
    second = client.post(f"/api/v2/runtime/apps/{app['agent']['public_id']}/sessions", headers=admin, json={}).json()
    headers = {"Authorization": "Bearer " + second["access_token"]}
    cid = conversation(client, headers, app["agent"]["id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(client.post, endpoint(app), headers=app["headers"], json=payload(app))
        assert entered.wait(10)
        try:
            busy = client.post(f"/api/v2/runtime/conversations/{cid}/shadow-comparison", headers=headers, json=payload(app))
            assert busy.status_code == 429 and busy.headers["retry-after"] == "1"
        finally: resume.set()
        first = future.result(timeout=20)
    assert first.status_code == 200, first.text
    assert counts(cid) == [0, 0, 0]


@pytest.mark.parametrize("revocation", ["function", "session"])
def test_shadow_fresh_authorization_between_model_attempts(client, admin, semantic_app, monkeypatch, revocation):
    import intent_platform.shadow_api as shadow
    app = semantic_app; monkeypatch.setattr(settings(), "allow_shadow_evaluation", True)
    calls = []; original = shadow.model_attempt
    def revoke(*args):
        calls.append(args[-1]); result = original(*args)
        if revocation == "function":
            assert client.post(WS + f"/functions/{app['fid']}/disable", headers=admin).status_code == 200
        else:
            assert client.post("/api/v2/runtime/sessions/logout", headers=app["headers"]).status_code == 200
        return result
    monkeypatch.setattr(shadow, "model_attempt", revoke)
    result = client.post(endpoint(app), headers=app["headers"], json=payload(app))
    assert result.status_code in {401, 403, 409} and calls == ["A"]
    assert "variants" not in result.json() and counts(app["cid"]) == [0, 0, 0]


def test_old_v2_snapshot_and_conversation_continue_after_v3_publication(client, admin, semantic_app):
    app = semantic_app
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin")
        original = db.get(Release, app["release_id"])
        old_snapshot = copy.deepcopy(original.snapshot); old_snapshot["prompt_policy_version"] = "router-v2"
        old = Release(workspace_id="ws-dev", agent_id=app["agent"]["id"], version=2, snapshot=old_snapshot)
        db.add(old); db.flush(); old_id = old.id
        db.get(Agent, app["agent"]["id"]).current_release_id = old_id
    old_cid = conversation(client, app["headers"], app["agent"]["id"])
    result = client.post(WS + f"/agents/{app['agent']['id']}/publish", headers=admin)
    assert result.status_code == 200, result.text
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        assert db.get(Release, old_id).snapshot == old_snapshot
        assert db.get(Release, result.json()["release_id"]).snapshot["prompt_policy_version"] == "router-v3"
    rid = message(client, app["headers"], old_cid, payload(app)["text"]); drain()
    runtime = client.get(f"/api/v2/runtime/runs/{rid}", headers=app["headers"])
    assert runtime.json()["status"] == "SUCCEEDED", runtime.text
    profile = client.get(f"/api/v2/runtime/conversations/{old_cid}/evaluation-profile", headers=app["headers"]).json()
    assert profile["prompt_version"] == "router-v2"


def test_shadow_and_normal_v3_send_real_complete_model_payload(client, admin, resolver_service, monkeypatch):
    from test_domain_runtime import semantic_app as make_semantic_app
    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"]))); received.append(body)
            system = body["messages"][0]["content"]
            cards = json.loads(system.split("\nAuthorized data:\n", 1)[1])
            c = cards["capabilities"][0]
            item = {"capability_id": c["capability_id"], "arguments": {}}
            if "domain_projection" in cards:
                item.update(action=c["action"], object_type=c["object_type"], result_type=c["result_type"], negated=False,
                            participants=[{"role": "target", "type": "test:Item", "mention": "one"}], relations=c["relations"], parameter_units={})
            data = json.dumps({"choices": [{"message": {"content": json.dumps({"decision": "READY", "requests": [item]})}}]}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    monkeypatch.setattr(settings(), "model_mode", "openai")
    monkeypatch.setattr(settings(), "model_url", f"http://127.0.0.1:{server.server_port}/model")
    monkeypatch.setattr(settings(), "model_name", "synthetic-socket-model")
    monkeypatch.setattr(settings(), "model_version", "synthetic-protocol-v1")
    monkeypatch.setattr(settings(), "allow_shadow_evaluation", True)
    try:
        app = make_semantic_app.__wrapped__(client, admin, resolver_service)
        result = client.post(endpoint(app), headers=app["headers"], json=payload(app))
        assert result.status_code == 200, result.text
        rows = result.json()["variants"]
        assert len(received) == 3 and [row["model_request"] for row in rows] == received
        assert all(row["model_ms"] > 0 and row["model_response"] is not None and not row["fixture_mode"] for row in rows)
        assert counts(app["cid"]) == [0, 0, 0]
        b = json.loads(received[1]["messages"][0]["content"].split("\nAuthorized data:\n", 1)[1])
        assert len(b["capabilities"]) == 2 and len(b["domain_projection"]["versions"]) == 1
        assert b["domain_projection"]["closure"] and b["domain_projection"]["conversions"]
        rid = message(client, app["headers"], app["cid"], payload(app)["text"]); drain()
        r = client.get(f"/api/v2/runtime/runs/{rid}", headers=app["headers"])
        assert r.json()["status"] == "SUCCEEDED", r.text
        assert len(received) == 4 and received[3] == received[1]
    finally:
        server.shutdown(); server.server_close(); thread.join(3)
