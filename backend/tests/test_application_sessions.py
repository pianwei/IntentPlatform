"""Application publication, renewal and authorization on real PostgreSQL."""
import base64
import uuid
from datetime import timedelta
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from intent_platform.db import engine, set_identity, now
from intent_platform.models import Run, Call, AuthToken, RuntimeSession, Release
from intent_platform.schemas import Proposal
from intent_platform.worker import claim_run, prepare_run, claim_call, process_call, reconcile_one
from conftest import conversation, message, drain

MG = "/api/v2/management/workspaces/ws-dev"


def new_app(client, admin, *, confirm=False):
    name = "application-fixture-" + uuid.uuid4().hex[:8]
    f = client.post(MG + "/functions/import", headers=admin, json={"definition": {"name": name, "description": "Local non-bank application fixture", "requires_confirmation": confirm}}).json()
    assert client.post(MG + f"/functions/{f['id']}/validate", headers=admin, json={}).json()["valid"]
    assert client.post(MG + f"/functions/{f['id']}/publish", headers=admin).status_code == 200
    a = client.post(MG + "/agents", headers=admin, json={"name": name, "capability_ids": [f["id"]], "allowed_origins": ["http://localhost:5173"]}).json()
    assert client.post(MG + f"/agents/{a['id']}/publish", headers=admin, json={"revision": a["revision"]}).status_code == 200
    return a, f


def runtime(client, admin, app, session_id=None):
    r = client.post(f"/api/v2/runtime/apps/{app['public_id']}/sessions", headers=admin,
                    json={"runtime_session_id": session_id} if session_id else {})
    assert r.status_code == 200, r.text
    data = r.json()
    return {"Authorization": "Bearer " + data["access_token"]}, data


def embed(client, admin, app, session_id=None):
    body = {"agent_id": app["id"], "host_origin": "http://localhost:5173"}
    h = client.post("/api/v2/runtime/embed/handshakes", json=body)
    assert h.status_code == 200, h.text
    body["instance_nonce"] = h.json()["instance_nonce"]
    if session_id:
        body["runtime_session_id"] = session_id
    t = client.post("/api/v2/integrations/embed-tickets", headers=admin, json=body)
    assert t.status_code == 200, t.text
    result = client.post("/api/v2/runtime/embed/exchange", json={**body, "ticket": t.json()["ticket"]})
    assert result.status_code == 200, result.text
    value = result.json()
    return {"Authorization": "Bearer " + value["access_token"]}, value


@pytest.mark.parametrize("proposal", [
    {"decision": "OUT_OF_SCOPE", "requests": [{"capability_id": "allowed", "arguments": {}}]},
    {"decision": "READY", "requests": []},
    {"decision": "AMBIGUOUS", "requests": [{"capability_id": "allowed"}], "options": [{"id": "a", "label": "a"}, {"id": "b", "label": "b"}]},
])
def test_decision_shape_never_authorizes_conflicting_requests(proposal):
    with pytest.raises(ValidationError):
        Proposal.model_validate(proposal)


def test_out_of_scope_with_requests_creates_no_call(client, admin, monkeypatch):
    a, f = new_app(client, admin); cid = conversation(client, admin, a["id"])
    rid = message(client, admin, cid, "unsupported")
    monkeypatch.setattr("intent_platform.worker.route_model", lambda *a, **k: {"decision": "OUT_OF_SCOPE", "requests": [{"capability_id": f["id"], "arguments": {}}]})
    drain()
    result = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    assert result["status"] == "FAILED" and result["decision"] == "ERROR" and result["calls"] == []


def test_acl_is_explicit_and_draft_does_not_open_access(client, admin):
    a, f = new_app(client, admin)
    path = MG + f"/agents/{a['id']}"
    restricted = client.put(path + "/access", headers=admin, json={"access_mode": "allowlist", "allowed_users": ["dev-admin"]})
    assert restricted.json()["access"]["allowed_users"] == ["dev-admin"]
    edited = client.patch(path + "/draft", headers=admin, json={"revision": a["revision"], "name": "Changed title", "capability_ids": [f["id"]]})
    assert edited.status_code == 200 and edited.json()["access"]["allowed_users"] == ["dev-admin"]
    reader = client.post("/api/v2/dev/login", json={"user_id": "dev-reader"}).json()["access_token"]
    assert client.post(f"/api/v2/runtime/apps/{a['public_id']}/sessions", headers={"Authorization": "Bearer " + reader}, json={}).status_code == 403
    denied = client.put(path + "/access", headers=admin, json={"access_mode": "allowlist", "allowed_users": []})
    assert denied.status_code == 200
    assert client.post(f"/api/v2/runtime/apps/{a['public_id']}/sessions", headers=admin, json={}).status_code == 403
    client.put(path + "/access", headers=admin, json={"access_mode": "workspace", "allowed_users": []})
    rt, _ = runtime(client, admin, a)
    function_path = MG + f"/functions/{f['id']}"
    client.put(function_path + "/access", headers=admin, json={"access_mode": "allowlist", "allowed_users": ["dev-admin"]})
    d = f["definition"]; d["allowed_users"] = ["dev-reader"]
    draft = client.patch(function_path + "/draft", headers=admin, json={"revision": f["revision"], "definition": d})
    assert draft.json()["access"]["allowed_users"] == ["dev-admin"]
    assert client.get(f"/api/v2/runtime/agents/{a['id']}", headers=rt).json()["capabilities"][0]["id"] == f["id"]


def test_ui_snapshot_channels_and_dynamic_csp(client, admin):
    a, f = new_app(client, admin); rt, _ = runtime(client, admin, a)
    cid = conversation(client, rt, a["id"])
    public = f"/api/v2/runtime/apps/{a['public_id']}"
    initial = client.get(public).json()
    draft = client.patch(MG + f"/agents/{a['id']}/draft", headers=admin, json={"revision": a["revision"], "name": "draft", "capability_ids": [f["id"]],
        "runtime_ui": {"title": "Published title", "welcome_message": "Welcome", "primary_color": "#123456"}, "channels": {"chat": True, "embed": False}})
    assert draft.status_code == 200, draft.text
    assert client.get(public).json()["runtime_ui"] == initial["runtime_ui"]
    assert client.get(public + "/embedding-policy").headers["Content-Security-Policy"].endswith("frame-ancestors 'self' http://localhost:5173")
    assert client.get(public + "/chat-policy").headers["Content-Security-Policy"].endswith("frame-ancestors 'none'")
    assert client.post(MG + f"/agents/{a['id']}/publish", headers=admin, json={"revision": draft.json()["revision"]}).status_code == 200
    assert client.get(public).json()["runtime_ui"]["title"] == "Published title"
    assert client.get(public + "/embedding-policy").status_code == 403
    h = client.get(f"/api/v2/runtime/conversations/{cid}/history", headers=rt)
    assert h.status_code == 200 and h.json()["conversation"]["runtime_ui"] == initial["runtime_ui"]
    assert client.put(MG + f"/agents/{a['id']}/channels", headers=admin, json={"chat": True, "embed": True}).status_code == 409
    assert client.put(MG + f"/agents/{a['id']}/channels", headers=admin, json={"chat": False, "embed": False}).status_code == 200
    assert client.get(public + "/chat-policy").status_code == 403
    assert client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=rt).status_code == 401
    assert client.get("/api/v2/runtime/apps/nonexistent/embedding-policy").status_code == 403
    assert client.get("/api/v2/runtime/apps/nonexistent/chat-policy").status_code == 403


@pytest.mark.parametrize("channel", ["chat", "embed"])
def test_session_renewal_preserves_history_and_forbids_inheritance(client, admin, channel):
    a, f = new_app(client, admin)
    get_session = runtime if channel == "chat" else embed
    old, token = get_session(client, admin, a)
    cid = conversation(client, old, a["id"]); rid = message(client, old, cid, f["name"]); drain()
    original = client.get(f"/api/v2/runtime/conversations/{cid}/history", headers=old).json()
    assert original["cursor"] == original["events"][-1]["sequence"]
    with Session(engine) as db, db.begin():
        db.get(AuthToken, token["access_token"]).expires_at = now() - timedelta(seconds=1)
    assert client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=old).status_code == 401
    new, renewed = get_session(client, admin, a, token["runtime_session_id"])
    assert renewed["runtime_session_id"] == token["runtime_session_id"]
    restored = client.get(f"/api/v2/runtime/conversations/{cid}/history", headers=new).json()
    assert restored == original
    assert client.get("/api/v2/runtime/conversations", headers=new).json()[0]["id"] == cid
    other, _ = get_session(client, admin, a)
    assert client.get(f"/api/v2/runtime/conversations/{cid}/history", headers=other).status_code == 403
    reader = client.post("/api/v2/dev/login", json={"user_id": "dev-reader"}).json()["access_token"]
    assert client.post(f"/api/v2/runtime/apps/{a['public_id']}/sessions", headers={"Authorization": "Bearer " + reader}, json={"runtime_session_id": token["runtime_session_id"]}).status_code == 403
    assert client.post(f"/api/v2/runtime/apps/{a['public_id']}/sessions", headers=new, json={}).status_code == 403
    assert client.post("/api/v2/runtime/sessions/logout", headers=new).status_code == 200
    assert client.post(f"/api/v2/runtime/apps/{a['public_id']}/sessions", headers=admin, json={"runtime_session_id": token["runtime_session_id"]}).status_code == 403


def test_parent_logout_cannot_be_resumed_with_new_identity(client, admin):
    a, _ = new_app(client, admin); rt, t = runtime(client, admin, a)
    assert client.post("/api/v2/runtime/sessions/logout", headers=admin).status_code == 200
    fresh = client.post("/api/v2/dev/login", json={}).json()["access_token"]
    assert client.post(f"/api/v2/runtime/apps/{a['public_id']}/sessions", headers={"Authorization": "Bearer " + fresh}, json={"runtime_session_id": t["runtime_session_id"]}).status_code == 403


def test_queued_work_resolves_renewed_token_and_confirmation_deadline(client, admin, monkeypatch):
    a, f = new_app(client, admin, confirm=True); old, t = runtime(client, admin, a)
    cid = conversation(client, old, a["id"]); rid = message(client, old, cid, f["name"]); drain()
    pending = client.get(f"/api/v2/runtime/runs/{rid}", headers=old).json()
    new, _ = runtime(client, admin, a, t["runtime_session_id"])
    assert client.post(f"/api/v2/runtime/runs/{rid}/confirmations", headers=new, json={"revision": pending["revision"], "confirmed": True}).status_code == 200
    prepare_run(claim_run("dev-admin")); claimed = claim_call("dev-admin")
    assert claimed["run_id"] == rid
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin"); db.get(Run, rid).confirmation_expires_at = now() - timedelta(seconds=1)
    sent = []
    monkeypatch.setattr("intent_platform.worker.execute_connector", lambda *a, **k: sent.append(True))
    process_call(claimed)
    assert not sent
    with Session(engine) as db:
        set_identity(db, "dev-admin"); assert db.get(Call, claimed["id"]).dispatched_at is None


def test_unknown_reconciliation_pauses_after_function_revocation(client, admin, monkeypatch):
    a, f = new_app(client, admin); cid = conversation(client, admin, a["id"])
    rid = message(client, admin, cid, f["name"]); drain()
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin"); call = db.scalar(select(Call).where(Call.run_id == rid))
        call.status = "UNKNOWN"; call.lease_until = None; call.operation_id = "known-operation"
        call_id = call.id
        call.definition = {**call.definition, "connector": {**call.definition["connector"], "status_endpoint": "http://localhost/status/{operation_id}"}}
        db.get(Run, rid).status = "UNKNOWN"
    client.put(MG + f"/functions/{f['id']}/access", headers=admin, json={"access_mode": "allowlist", "allowed_users": []})
    queries = []
    monkeypatch.setattr("intent_platform.worker.execute_connector", lambda *a, **k: queries.append(True))
    reconcile_one("dev-admin")
    assert not queries
    with Session(engine) as db:
        set_identity(db, "dev-admin"); assert db.get(Call, call_id).status == "UNKNOWN"


def test_unavailable_pinned_dependencies_fail_closed(client, admin, monkeypatch):
    from intent_platform.ports import ROUTING_PROMPTS
    a, f = new_app(client, admin); cid = conversation(client, admin, a["id"])
    rid = message(client, admin, cid, f["name"])
    monkeypatch.delitem(ROUTING_PROMPTS, "router-v1")
    drain()
    assert client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()["calls"] == []
    assert client.post(f"/api/v2/runtime/apps/{a['public_id']}/sessions", headers=admin, json={}).status_code == 503
    assert client.post(MG + f"/agents/{a['id']}/publish", headers=admin).status_code == 409


def test_controlled_files_and_historic_icon_permissions(client, admin, monkeypatch):
    a, f = new_app(client, admin)
    png = b"\x89PNG\r\n\x1a\n" + b"local-test-fixture"
    asset = client.post(MG + "/assets", headers=admin, files={"file": ("icon.png", png, "image/png")})
    assert asset.status_code == 201
    assert client.post(MG + "/assets", headers=admin, files={"file": ("icon.svg", b"<svg/>", "image/svg+xml")}).status_code == 422
    app = client.patch(MG + f"/agents/{a['id']}/draft", headers=admin, json={"revision": a["revision"], "name": a["name"], "capability_ids": [f["id"]], "runtime_ui": {"title": "Icon title", "icon": {"kind": "asset", "value": asset.json()["id"]}}}).json()
    assert client.post(MG + f"/agents/{a['id']}/publish", headers=admin, json={"revision": app["revision"]}).status_code == 200
    rt, _ = runtime(client, admin, a); cid = conversation(client, rt, a["id"])
    updated = client.patch(MG + f"/agents/{a['id']}/draft", headers=admin, json={"revision": app["revision"], "name": a["name"], "capability_ids": [f["id"]], "runtime_ui": {"title": "New version", "icon": {"kind": "emoji", "value": "💬"}}}).json()
    assert client.post(MG + f"/agents/{a['id']}/publish", headers=admin, json={"revision": updated["revision"]}).status_code == 200
    assert client.get(f"/api/v2/runtime/apps/{a['public_id']}/icon").status_code == 404
    assert client.get(f"/api/v2/runtime/assets/{asset.json()['id']}", headers=rt).content == png
    monkeypatch.setattr("intent_platform.worker.execute_connector", lambda *a, **k: {"status": "SUCCEEDED", "data": {"files": [{"filename": "report.txt", "media_type": "text/plain", "content_base64": base64.b64encode(b"fixture artifact").decode()}]}, "fixture_mode": True})
    message(client, rt, cid, f["name"]); drain()
    history = client.get(f"/api/v2/runtime/conversations/{cid}/history", headers=rt).json()
    result = next(e["payload"] for e in history["events"] if e["event_type"] == "RESULT")
    assert "content_base64" not in str(history) and result["data"] == {}
    file_id = result["files"][0]["id"]
    download = client.get(f"/api/v2/runtime/files/{file_id}", headers=rt)
    assert download.content == b"fixture artifact" and download.headers["content-disposition"].startswith("attachment;")
    other, _ = runtime(client, admin, a)
    assert client.get(f"/api/v2/runtime/files/{file_id}", headers=other).status_code == 403
    assert client.get(f"/api/v2/runtime/files/{file_id}").status_code == 401
    client.put(MG + f"/functions/{f['id']}/access", headers=admin, json={"access_mode": "allowlist", "allowed_users": []})
    assert client.get(f"/api/v2/runtime/files/{file_id}", headers=rt).status_code == 403


@pytest.mark.parametrize("origin", ["https://evil.example;script-src*", "https://evil.example; script-src *", "https://evil.example\r\nX-Test: yes", "https://evil.example:65536", "https://*.example", "https://name:secret@example.com", "https://EXAMPLE.com", "https://example.com:443"])
def test_origins_reject_header_injection_and_noncanonical_forms(client, admin, seeded, origin):
    result = client.post(MG + "/agents", headers=admin, json={"name": "bad-origin", "capability_ids": seeded["agent"]["capability_ids"], "allowed_origins": [origin]})
    assert result.status_code == 422


def test_old_invalid_snapshot_fails_closed_without_rewriting_it(client, admin):
    a, _ = new_app(client, admin)
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin"); agent = db.get(__import__('intent_platform.models', fromlist=['Agent']).Agent, a["id"])
        old = db.get(Release, agent.current_release_id)
        bad = Release(workspace_id=agent.workspace_id, agent_id=agent.id, version=2, snapshot={**old.snapshot, "allowed_origins": ["https://evil.example; script-src *"]})
        db.add(bad); db.flush(); agent.current_release_id = bad.id
    assert client.get(f"/api/v2/runtime/apps/{a['public_id']}/embedding-policy").status_code == 403
    # The independent chat channel is still safe and does not inherit embed source strings.
    assert client.get(f"/api/v2/runtime/apps/{a['public_id']}/chat-policy").status_code == 200


def test_legacy_management_conversation_obeys_channels_without_breaking_embed(client, admin):
    a, f = new_app(client, admin); cid = conversation(client, admin, a["id"])
    rid = message(client, admin, cid, f["name"]); drain()
    assert client.put(MG + f"/agents/{a['id']}/channels", headers=admin, json={"chat": False, "embed": True}).status_code == 200
    for path in (f"/conversations/{cid}/events", f"/conversations/{cid}/history", f"/runs/{rid}"):
        assert client.get("/api/v2/runtime" + path, headers=admin).status_code == 403
    assert client.post(f"/api/v2/runtime/conversations/{cid}/messages", headers=admin, json={"text": f["name"], "client_message_id": str(uuid.uuid4())}).status_code == 403
    assert client.post(f"/api/v2/runtime/agents/{a['id']}/conversations", headers=admin).status_code == 403
    rt, _ = embed(client, admin, a)
    assert conversation(client, rt, a["id"])


@pytest.mark.parametrize("renew", [False, True])
def test_concurrent_session_establishment_and_logout_are_serialized(client, admin, monkeypatch, renew):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event as ThreadEvent
    from intent_platform import runtime_sessions as sessions
    from intent_platform.models import Agent
    from intent_platform.main import logout
    from intent_platform.auth import token_valid
    a, _ = new_app(client, admin)
    prior = runtime(client, admin, a)[1] if renew else None
    parent_id = admin["Authorization"][7:]
    logout_id = prior["access_token"] if prior else parent_id
    established, release_creator, logout_ready = ThreadEvent(), ThreadEvent(), ThreadEvent()
    original_issue = sessions.issue_token
    def gated_issue(*args, **kwargs):
        established.set()
        assert release_creator.wait(5)
        return original_issue(*args, **kwargs)
    monkeypatch.setattr(sessions, "issue_token", gated_issue)
    def create():
        with Session(engine) as db, db.begin():
            set_identity(db, "dev-admin")
            token = sessions.scoped_session(db, db.get(AuthToken, parent_id), db.get(Agent, a["id"]), "chat", prior["runtime_session_id"] if prior else None)
            return token.runtime_session_id
    def revoke():
        with Session(engine) as db:
            set_identity(db, "dev-admin"); token = db.get(AuthToken, logout_id)
            assert token_valid(db, token)
            logout_ready.set()
            return logout(token, db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        creator = pool.submit(create)
        assert established.wait(5)
        revoker = pool.submit(revoke)
        assert logout_ready.wait(5)
        release_creator.set()
        session_id = creator.result(timeout=10)
        assert revoker.result(timeout=10) == {"revoked": True}
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        assert db.get(RuntimeSession, session_id).revoked is True


def test_runtime_summary_does_not_expose_draft_or_access_policy(client, admin):
    a, _ = new_app(client, admin); rt, _ = runtime(client, admin, a)
    result = client.get("/api/v2/runtime/agents", headers=rt)
    assert result.status_code == 200
    assert set(result.json()[0]) == {"id", "public_id", "workspace_id", "name", "current_release_id", "capability_ids", "suggested_questions", "fixture_mode"}
    cid = conversation(client, rt, a["id"])
    assert client.get(f"/api/v2/runtime/conversations/{cid}/history", headers=admin).status_code == 403
    client.post("/api/v2/runtime/sessions/logout", headers=admin)
    fresh = client.post("/api/v2/dev/login", json={}).json()["access_token"]
    assert client.get(f"/api/v2/runtime/conversations/{cid}/history", headers={"Authorization": "Bearer " + fresh}).status_code == 403


@pytest.mark.parametrize("revocation", ["session", "function", "confirmation"])
def test_sent_write_with_lost_worker_remains_unknown_after_revocation(client, admin, monkeypatch, revocation):
    a, f = new_app(client, admin, confirm=True); rt, _ = runtime(client, admin, a)
    cid = conversation(client, rt, a["id"]); rid = message(client, rt, cid, f["name"]); drain()
    pending = client.get(f"/api/v2/runtime/runs/{rid}", headers=rt).json()
    assert client.post(f"/api/v2/runtime/runs/{rid}/confirmations", headers=rt, json={"revision": pending["revision"], "confirmed": True}).status_code == 200
    prepare_run(claim_run("dev-admin")); work = claim_call("dev-admin")
    assert work["run_id"] == rid
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin"); call = db.get(Call, work["id"])
        call.definition = {**call.definition, "effect": "WRITE"}
        call.dispatched_at = now() - timedelta(seconds=10); call.lease_until = now() - timedelta(seconds=1)
        if revocation == "confirmation":
            db.get(Run, rid).confirmation_expires_at = now() - timedelta(seconds=1)
    if revocation == "session":
        client.post("/api/v2/runtime/sessions/logout", headers=rt)
    if revocation == "function":
        client.post(MG + f"/functions/{f['id']}/disable", headers=admin)
    sent = []
    monkeypatch.setattr("intent_platform.worker.execute_connector", lambda *a, **k: sent.append(True))
    assert claim_call("dev-admin") == {"done": True}
    assert not sent
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        assert db.get(Call, work["id"]).status == "UNKNOWN" and db.get(Run, rid).status == "UNKNOWN"


def test_documentation_script_is_local_and_csp_compatible(client):
    page = client.get("/docs")
    assert '<script src="/docs/app.js" defer></script>' in page.text and "<script>" not in page.text
    script = client.get("/docs/app.js")
    assert script.status_code == 200 and script.headers["content-type"].startswith("application/javascript")
    assert "fetch('/openapi.json'" in script.text and "https://" not in script.text
