"""Local account, visitor and key boundaries on restricted PostgreSQL roles."""
import json
import uuid
from datetime import timedelta
import pytest
from sqlalchemy import select, text, func
from sqlalchemy.orm import Session
from intent_platform.config import settings
from intent_platform.db import engine, set_identity, now
from intent_platform.models import Workspace, Membership, RuntimeSession, AuthToken, Agent, Release, Conversation, Run, Call, ResultFile
from intent_platform.identity_models import LocalAccount, IntegrationKey, AnonymousPrincipal
from intent_platform.local_identity import secret_hash, HASHER
from conftest import conversation, message, drain

PASSWORD = "Local-test-only-password!"
ORIGIN = {"Origin": "http://localhost:5173"}


def bearer(value):
    return {"Authorization": "Bearer " + value}


@pytest.fixture
def local(client, monkeypatch):
    cfg = settings(); user = "user_" + uuid.uuid4().hex; ws = "ws_" + uuid.uuid4().hex
    monkeypatch.setattr(cfg, "identity_mode", "local")
    monkeypatch.setattr(cfg, "allow_dev_auth", False)
    monkeypatch.setattr(cfg, "platform_admins_json", json.dumps([user]))
    with Session(engine) as db, db.begin():
        db.add(LocalAccount(user_id=user, username=user, display_name="Test administrator", password_hash=secret_hash(PASSWORD), must_change_password=False))
        db.add(Workspace(id=ws, name="Local identity test")); db.flush()
        db.add(Membership(user_id=user, workspace_id=ws, roles=["admin", "publisher", "maintainer", "user"]))
    result = client.post("/api/v2/auth/login", json={"username": user, "password": PASSWORD})
    assert result.status_code == 200
    headers = bearer(result.json()["access_token"])
    return {"user": user, "ws": ws, "headers": headers, "token": result.json()["access_token"]}


def app(client, local):
    base = f"/api/v2/management/workspaces/{local['ws']}"
    defaults = client.post(base + "/function-options/bootstrap", headers=local["headers"])
    assert defaults.status_code == 200, defaults.text
    definition = {"name": "Identity sample", "description": "Test synthetic read", "object_type": "business:Document",
        "result_type": "business:Detail", "domain_refs": defaults.json()["domain_refs"], "authoring_version": "function-v2",
        "output_schema": {"type": "object", "properties": {"fixture": {"type": "boolean"}, "notice": {"type": "string", "maxLength": 200}},
                          "required": ["fixture", "notice"], "additionalProperties": False},
        "presentation": {"component": "detail", "field_map": {"模拟": "fixture", "说明": "notice"}}}
    f = client.post(base + "/functions/import", headers=local["headers"], json={"definition": definition})
    assert f.status_code == 201
    fid = f.json()["id"]
    checked = client.post(base + f"/functions/{fid}/validate", headers=local["headers"], json={"probe": True, "arguments": {}})
    assert checked.status_code == 200 and checked.json()["valid"], checked.text
    assert client.post(base + f"/functions/{fid}/publish", headers=local["headers"]).status_code == 200
    a = client.post(base + "/agents", headers=local["headers"], json={"name": "Public sample", "capability_ids": [fid], "allowed_origins": ["http://localhost:5173"]})
    assert a.status_code == 201
    value = a.json()
    assert client.post(base + f"/agents/{value['id']}/publish", headers=local["headers"], json={"revision": value["revision"]}).status_code == 200
    return value, fid


def guest(client, a, **body):
    response = client.post(f"/api/v2/runtime/apps/{a['public_id']}/anonymous-sessions", json=body)
    assert response.status_code == 200
    return response.json()


def key(client, local):
    response = client.post("/api/v2/me/api-keys", headers=local["headers"], json={"name": "Host backend"})
    assert response.status_code == 201
    return response.json()


def ticket(client, a, secret, visitor="host-visitor-aaaaaaaa", runtime=None):
    body = {"agent_id": a["id"], "host_origin": "http://localhost:5173"}
    handshake = client.post("/api/v2/runtime/embed/handshakes", json=body)
    assert handshake.status_code == 200
    body["instance_nonce"] = handshake.json()["instance_nonce"]
    body["visitor_id"] = visitor
    if runtime: body["runtime_session_id"] = runtime
    response = client.post("/api/v2/integrations/embed-tickets", headers=bearer(secret), json=body)
    if response.status_code == 200:
        body["runtime_session_id"] = response.json()["runtime_session_id"]
    return response, body


def embed(client, a, secret, visitor="host-visitor-aaaaaaaa", runtime=None):
    response, body = ticket(client, a, secret, visitor, runtime)
    assert response.status_code == 200
    exchanged = client.post("/api/v2/runtime/embed/exchange", json={**body, "ticket": response.json()["ticket"]})
    assert exchanged.status_code == 200
    return exchanged.json()


def test_password_parameters_profile_refresh_and_old_identity_disabled(client, local):
    assert HASHER.memory_cost >= 19456 and HASHER.time_cost >= 2 and HASHER.parallelism >= 1
    profile = client.get("/api/v2/me", headers=local["headers"])
    assert profile.status_code == 200 and profile.json()["is_platform_admin"]
    edited = client.patch("/api/v2/me", headers=local["headers"], json={"display_name": "New name", "preferences": {"density": "compact"}})
    assert edited.status_code == 200 and edited.json()["preferences"]["density"] == "compact"
    assert client.post("/api/v2/auth/refresh", headers={"Origin": "https://untrusted.example"}).status_code == 403
    refreshed = client.post("/api/v2/auth/refresh", headers=ORIGIN)
    assert refreshed.status_code == 200
    assert client.get("/api/v2/me", headers=local["headers"]).status_code == 401
    assert client.get("/api/v2/me", headers=bearer(refreshed.json()["access_token"])).status_code == 200
    assert client.post("/api/v2/dev/login", json={"user_id": "dev-admin"}).status_code == 404
    assert client.post("/api/v2/auth/exchange", json={"identity_token": "obsolete"}).status_code == 404


def test_admin_creation_setup_once_and_local_role_ceiling(client, local):
    created = client.post("/api/v2/management/accounts", headers=local["headers"], json={"username": "new_"+uuid.uuid4().hex, "display_name": "Reader"})
    assert created.status_code == 201
    account = created.json()["account"]; setup = created.json()["setup_token"]
    assert client.post("/api/v2/auth/set-password", json={"setup_token": setup, "new_password": PASSWORD}).status_code == 200
    assert client.post("/api/v2/auth/set-password", json={"setup_token": setup, "new_password": PASSWORD}).status_code == 401
    login = client.post("/api/v2/auth/login", json={"username": account["username"], "password": PASSWORD})
    assert login.status_code == 200
    reader = bearer(login.json()["access_token"])
    assert client.get("/api/v2/management/accounts", headers=reader).status_code == 403
    assert client.get("/api/v2/me", headers=reader).json()["workspaces"] == []
    assert client.patch("/api/v2/me", headers=reader, json={"is_platform_admin": True}).status_code == 422


def test_public_guests_have_no_memberships_isolated_history_and_secret_recovery(client, local):
    a, _ = app(client, local); first = guest(client, a); second = guest(client, a)
    headers = bearer(first["access_token"])
    cid = conversation(client, headers, a["id"]); rid = message(client, headers, cid, "Identity sample"); drain()
    assert client.get(f"/api/v2/runtime/runs/{rid}", headers=headers).status_code == 200
    assert client.get(f"/api/v2/runtime/conversations/{cid}/history", headers=bearer(second["access_token"])).status_code in {403, 404}
    assert client.get("/api/v2/runtime/conversations", headers=bearer(second["access_token"])).json() == []
    assert client.get(f"/api/v2/management/workspaces/{local['ws']}/agents", headers=headers).status_code == 403
    with Session(engine) as db:
        assert db.get(Membership, (first["user_id"], local["ws"])) is None
        set_identity(db, second["user_id"])
        assert db.get(Conversation, cid) is None  # Real owner RLS, not just HTTP rejection.
    path = f"/api/v2/runtime/apps/{a['public_id']}/anonymous-sessions"
    assert client.post(path, json={"runtime_session_id": first["runtime_session_id"]}).status_code == 403
    assert client.post(path, json={"runtime_session_id": first["runtime_session_id"], "recovery_secret": second["recovery_secret"]}).status_code == 403
    restored = guest(client, a, runtime_session_id=first["runtime_session_id"], recovery_secret=first["recovery_secret"])
    assert restored["recovery_secret"] is None
    assert client.get(f"/api/v2/runtime/conversations/{cid}/history", headers=bearer(restored["access_token"])).status_code == 200
    assert client.get(f"/api/v2/runtime/runs/{rid}", headers=headers).status_code == 401


def test_key_shared_owner_visitors_are_separate_and_cannot_use_guest_recovery(client, local):
    a, _ = app(client, local); issued = key(client, local)
    first = embed(client, a, issued["secret"]); second = embed(client, a, issued["secret"], "host-visitor-bbbbbbbb")
    cid = conversation(client, bearer(first["access_token"]), a["id"])
    assert client.get(f"/api/v2/runtime/conversations/{cid}/history", headers=bearer(second["access_token"])).status_code == 403
    assert client.get("/api/v2/runtime/conversations", headers=bearer(second["access_token"])).json() == []
    bad, _ = ticket(client, a, issued["secret"], "host-visitor-bbbbbbbb", first["runtime_session_id"])
    assert bad.status_code == 403
    resumed = embed(client, a, issued["secret"], runtime=first["runtime_session_id"])
    assert resumed["runtime_session_id"] == first["runtime_session_id"]
    path = f"/api/v2/runtime/apps/{a['public_id']}/anonymous-sessions"
    assert client.post(path, json={"runtime_session_id": first["runtime_session_id"], "recovery_secret": "guess"}).status_code == 403
    assert client.get("/api/v2/me", headers=bearer(issued["secret"])).status_code == 401


def test_key_rotation_replay_and_current_owner_access(client, local):
    a, _ = app(client, local); issued = key(client, local); active = embed(client, a, issued["secret"])
    response, body = ticket(client, a, issued["secret"])
    payload = {**body, "ticket": response.json()["ticket"]}
    assert client.post("/api/v2/runtime/embed/exchange", json=payload).status_code == 200
    assert client.post("/api/v2/runtime/embed/exchange", json=payload).status_code == 403
    rotated = client.post(f"/api/v2/me/api-keys/{issued['key']['id']}/rotate", headers=local["headers"])
    assert rotated.status_code == 200
    assert client.get("/api/v2/runtime/conversations", headers=bearer(active["access_token"])).status_code == 401
    assert ticket(client, a, issued["secret"])[0].status_code == 401
    allowed = embed(client, a, rotated.json()["secret"])
    with Session(engine) as db, db.begin():
        set_identity(db, local["user"]); agent = db.get(Agent, a["id"]); agent.access_mode="allowlist"; agent.allowed_users=[]
    assert client.get("/api/v2/runtime/conversations", headers=bearer(allowed["access_token"])).status_code == 401
    assert ticket(client, a, rotated.json()["secret"])[0].status_code == 403


def test_self_password_change_preserves_keys_admin_reset_revokes_them(client, local):
    a, _ = app(client, local); issued = key(client, local); runtime = embed(client, a, issued["secret"])
    changed = client.post("/api/v2/me/password", headers=local["headers"], json={"current_password": PASSWORD, "new_password": PASSWORD+"new"})
    assert changed.status_code == 200
    assert client.get("/api/v2/me", headers=local["headers"]).status_code == 401
    assert client.get("/api/v2/runtime/conversations", headers=bearer(runtime["access_token"])).status_code == 200
    login = client.post("/api/v2/auth/login", json={"username": local["user"], "password": PASSWORD+"new"})
    assert login.status_code == 200
    current = bearer(login.json()["access_token"])
    assert client.post(f"/api/v2/management/accounts/{local['user']}/reset-password", headers=current).status_code == 200
    assert client.get("/api/v2/runtime/conversations", headers=bearer(runtime["access_token"])).status_code == 401
    assert ticket(client, a, issued["secret"])[0].status_code == 401


def test_agent_names_are_unique_within_space(client, local):
    a, fid = app(client, local)
    assert client.post(f"/api/v2/management/workspaces/{local['ws']}/agents", headers=local["headers"],
                       json={"name": " public SAMPLE ", "capability_ids": [fid]}).status_code == 409


def test_ticket_binds_host_session_before_exchange_and_revocation_closes_it(client, local):
    a, _ = app(client, local); issued = key(client, local)
    response, body = ticket(client, a, issued["secret"])
    assert response.status_code == 200
    sid = response.json()["runtime_session_id"]
    with Session(engine) as db:
        set_identity(db, local["user"])
        assert db.get(RuntimeSession, sid).current_token_id is None
    payload = {**body, "ticket": response.json()["ticket"]}
    assert client.post("/api/v2/runtime/embed/exchange", json={**payload, "runtime_session_id": "other"}).status_code == 403
    assert client.post(f"/api/v2/me/api-keys/{issued['key']['id']}/revoke", headers=local["headers"]).status_code == 200
    assert client.post("/api/v2/runtime/embed/exchange", json=payload).status_code == 403


def test_host_logout_key_must_match_visitor_and_cannot_recover_revoked_session(client, local):
    a, _ = app(client, local); issued = key(client, local); other = key(client, local)
    runtime = embed(client, a, issued["secret"])
    path = f"/api/v2/integrations/runtime-sessions/{runtime['runtime_session_id']}/revoke"
    assert client.post(path, headers=bearer(other["secret"]), json={"visitor_id": "host-visitor-aaaaaaaa"}).status_code == 403
    assert client.post(path, headers=bearer(issued["secret"]), json={"visitor_id": "host-visitor-bbbbbbbb"}).status_code == 403
    assert client.post(path, headers=bearer(issued["secret"]), json={"visitor_id": "host-visitor-aaaaaaaa"}).status_code == 200
    assert client.get("/api/v2/runtime/conversations", headers=bearer(runtime["access_token"])).status_code == 401
    assert ticket(client, a, issued["secret"], runtime=runtime["runtime_session_id"])[0].status_code == 403


def test_key_current_membership_and_public_chat_channel_remain_independent(client, local):
    a, _ = app(client, local); issued = key(client, local); runtime = embed(client, a, issued["secret"])
    anonymous = guest(client, a)
    with Session(engine) as db, db.begin():
        db.get(Membership, (local["user"], local["ws"])).active = False
    assert client.get("/api/v2/runtime/conversations", headers=bearer(runtime["access_token"])).status_code == 401
    assert ticket(client, a, issued["secret"])[0].status_code == 403
    assert client.get("/api/v2/runtime/conversations", headers=bearer(anonymous["access_token"])).status_code == 200
    with Session(engine) as db, db.begin():
        db.get(Membership, (local["user"], local["ws"])).active = True
        set_identity(db, local["user"]); db.get(Agent, a["id"]).live_channels = {"chat": False, "embed": True}
    path = f"/api/v2/runtime/apps/{a['public_id']}/anonymous-sessions"
    assert client.post(path, json={}).status_code == 403
    assert client.post(path, json={"runtime_session_id": anonymous["runtime_session_id"], "recovery_secret": anonymous["recovery_secret"]}).status_code == 403
    assert client.get("/api/v2/runtime/conversations", headers=bearer(anonymous["access_token"])).status_code == 401
    assert embed(client, a, issued["secret"])["runtime_session_id"]


def test_disable_revokes_keys_and_sessions_last_admin_is_protected(client, local, monkeypatch):
    a, _ = app(client, local); issued = key(client, local); runtime = embed(client, a, issued["secret"])
    current = client.get("/api/v2/management/accounts", headers=local["headers"]).json()
    account = next(row for row in current if row["user_id"] == local["user"])
    assert client.patch(f"/api/v2/management/accounts/{local['user']}", headers=local["headers"], json={"active": False, "revision": account["revision"]}).status_code == 409
    other = "admin_" + uuid.uuid4().hex
    with Session(engine) as db, db.begin():
        db.add(LocalAccount(user_id=other, username=other, display_name="Second administrator", password_hash=secret_hash(PASSWORD), must_change_password=False))
    monkeypatch.setattr(settings(), "platform_admins_json", json.dumps([local["user"], other]))
    response = client.patch(f"/api/v2/management/accounts/{local['user']}", headers=local["headers"], json={"active": False, "revision": account["revision"]})
    assert response.status_code == 200
    assert client.get("/api/v2/me", headers=local["headers"]).status_code == 401
    assert client.get("/api/v2/runtime/conversations", headers=bearer(runtime["access_token"])).status_code == 401
    assert ticket(client, a, issued["secret"])[0].status_code == 401


def test_worker_rechecks_key_before_send_and_keeps_already_sent_results(client, local):
    from intent_platform.worker import claim_run, prepare_run, claim_call, mark_dispatch, finish_call
    a, _ = app(client, local); issued = key(client, local); runtime = embed(client, a, issued["secret"])
    headers = bearer(runtime["access_token"])
    cid = conversation(client, headers, a["id"]); rid = message(client, headers, cid, "Identity sample")
    prepare_run(claim_run(local["user"])); work = claim_call(local["user"])
    assert work and work["run_id"] == rid
    assert client.post(f"/api/v2/me/api-keys/{issued['key']['id']}/revoke", headers=local["headers"]).status_code == 200
    with pytest.raises(PermissionError): mark_dispatch(work)
    with Session(engine) as db:
        set_identity(db, local["user"]); assert db.get(Call, work["id"]).dispatched_at is None
    # A separate request was dispatched before revocation. Persist its actual
    # successful business outcome; do not turn it into a retryable cancellation.
    issued = key(client, local); runtime = embed(client, a, issued["secret"])
    cid = conversation(client, bearer(runtime["access_token"]), a["id"])
    rid = message(client, bearer(runtime["access_token"]), cid, "Identity sample")
    prepare_run(claim_run(local["user"])); work = claim_call(local["user"])
    assert work and work["run_id"] == rid
    mark_dispatch(work)
    client.post(f"/api/v2/me/api-keys/{issued['key']['id']}/revoke", headers=local["headers"])
    finish_call(work, {"status": "SUCCEEDED", "data": {"fixture": True, "notice": "Recorded"}})
    with Session(engine) as db:
        set_identity(db, local["user"])
        assert db.get(Call, work["id"]).status == "SUCCEEDED"
        assert db.get(Call, work["id"]).dispatched_at is not None
        assert db.get(Call, work["id"]).result["data"]["notice"] == "Recorded"


def test_history_file_and_sse_close_after_key_revocation(client, local):
    import asyncio
    from intent_platform.main import events
    a, _ = app(client, local); issued = key(client, local); runtime = embed(client, a, issued["secret"])
    headers = bearer(runtime["access_token"])
    cid = conversation(client, headers, a["id"]); rid = message(client, headers, cid, "Identity sample"); drain()
    with Session(engine) as db, db.begin():
        set_identity(db, local["user"])
        call = db.scalar(select(Call).where(Call.run_id == rid))
        artifact = ResultFile(workspace_id=local["ws"], conversation_id=cid, run_id=rid, call_id=call.id,
                              filename="synthetic.txt", media_type="text/plain", data=b"synthetic result")
        db.add(artifact); db.flush(); file_id = artifact.id
    assert client.get(f"/api/v2/runtime/files/{file_id}", headers=headers).status_code == 200
    with Session(engine) as db:
        set_identity(db, local["user"])
        response = events(cid, after=0, stream=True, token=db.get(AuthToken, runtime["access_token"]), db=db)
    async def verify_stream():
        while True:
            if "heartbeat" in await anext(response.body_iterator): break
        client.post(f"/api/v2/me/api-keys/{issued['key']['id']}/revoke", headers=local["headers"])
        with pytest.raises(StopAsyncIteration): await anext(response.body_iterator)
    asyncio.run(verify_stream())
    for path in (f"/api/v2/runtime/files/{file_id}", f"/api/v2/runtime/conversations/{cid}/history", f"/api/v2/runtime/conversations/{cid}/events"):
        assert client.get(path, headers=headers).status_code == 401


def test_legacy_identity_migration_copies_release_without_inheriting_history(client, local):
    from copy import deepcopy
    a, _ = app(client, local)
    with Session(engine) as db, db.begin():
        set_identity(db, local["user"])
        agent = db.get(Agent, a["id"])
        original = db.get(Release, agent.current_release_id)
        old_snapshot = deepcopy(original.snapshot)
        old_snapshot["identity_policy"] = {"version": "integration-v1", "profiles": {"development": {}}}
        historical = Release(workspace_id=local["ws"], agent_id=a["id"], version=2, snapshot=old_snapshot)
        db.add(historical); db.flush(); agent.current_release_id = historical.id; legacy_id = historical.id
        old_token = AuthToken(id="test-legacy-"+uuid.uuid4().hex, user_id=local["user"], scope="management", source="development", expires_at=now()+timedelta(hours=1))
        db.add(old_token); db.flush(); legacy_token = old_token.id
        old_session = RuntimeSession(workspace_id=local["ws"], agent_id=a["id"], user_id=local["user"], channel="chat", integration_id="development", current_token_id=legacy_token)
        db.add(old_session); db.flush(); legacy_session = old_session.id
        old_conversation = Conversation(workspace_id=local["ws"], user_id=local["user"], agent_id=a["id"], release_id=legacy_id, token_id=legacy_token, runtime_session_id=legacy_session)
        db.add(old_conversation); db.flush(); legacy_cid = old_conversation.id; revision = agent.revision
    assert client.get("/api/v2/me", headers=bearer(legacy_token)).status_code == 401
    path = f"/api/v2/runtime/apps/{a['public_id']}/anonymous-sessions"
    assert client.post(path, json={}).status_code == 403
    migrated = client.post(f"/api/v2/management/workspaces/{local['ws']}/agents/{a['id']}/migrate-identity", headers=local["headers"], json={"revision": revision})
    assert migrated.status_code == 200
    with Session(engine) as db:
        set_identity(db, local["user"])
        current = db.get(Release, db.get(Agent, a["id"]).current_release_id)
        assert current.id != legacy_id and current.snapshot["identity_policy"]["version"] == "local-v1"
        assert {k:v for k,v in current.snapshot.items() if k != "identity_policy"} == {k:v for k,v in old_snapshot.items() if k != "identity_policy"}
        assert db.get(Release, legacy_id).snapshot == old_snapshot
        assert db.get(Conversation, legacy_cid).runtime_session_id == legacy_session
    visitor = guest(client, a)
    assert client.get(f"/api/v2/runtime/conversations/{legacy_cid}/history", headers=bearer(visitor["access_token"])).status_code in {403, 404}
    assert client.post(f"/api/v2/runtime/apps/{a['public_id']}/sessions", headers=local["headers"], json={"runtime_session_id": legacy_session}).status_code == 403


def test_refresh_replay_is_atomic_and_secret_validation_does_not_echo(client, local):
    from concurrent.futures import ThreadPoolExecutor
    cookie = client.cookies.get("intent_refresh")
    def rotate():
        return client.post("/api/v2/auth/refresh", headers={**ORIGIN, "Cookie": "intent_refresh="+cookie}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: rotate(), range(2)))
    assert sorted(statuses) == [200, 401]
    sentinel = "private!"
    rejected = client.post("/api/v2/auth/set-password", json={"setup_token": "missing", "new_password": sentinel})
    assert rejected.status_code == 422 and sentinel not in rejected.text
    for _ in range(5):
        assert client.post("/api/v2/auth/login", json={"username": local["user"], "password": "incorrect"}).status_code == 401
    assert client.post("/api/v2/auth/login", json={"username": local["user"], "password": PASSWORD}).status_code == 429


def test_public_three_layer_fixture_uses_real_published_dependencies_and_worker(client, local, monkeypatch):
    # Development catalogue installation is test arrangement only. Runtime
    # executes with local identity and the development login remains closed.
    with monkeypatch.context() as arrange:
        arrange.setattr(settings(), "allow_dev_auth", True)
        seeded = client.post("/api/v2/dev/demo/seed", headers=local["headers"], json={"workspace_id": local["ws"], "routing_strategy": "three-layer-v1"})
        assert seeded.status_code == 200, seeded.text
    a = seeded.json()["agent"]; visitor = guest(client, a)
    def invoke(work, stage, data, instruction):
        if stage == "entity": return {"goal": "Funds overview", "entities": [{"type": "demo:Customer", "mention": "C001"}], "actions": ["READ", "LIST"], "negated": False}
        if stage == "skill": return {"skill_key": "demo.skill.customer_funds", "inputs": {"customer_query": "C001"}}
        raise AssertionError("Approved rule should apply")
    monkeypatch.setattr("intent_platform.three_layer._invoke", invoke)
    headers = bearer(visitor["access_token"])
    cid = conversation(client, headers, a["id"]); rid = message(client, headers, cid, "C001 customer funds overview"); drain()
    state = client.get(f"/api/v2/runtime/runs/{rid}", headers=headers)
    assert state.status_code == 200 and state.json()["status"] == "SUCCEEDED"
    assert client.get(f"/api/v2/runtime/conversations/{cid}/history", headers=headers).status_code == 200
    with Session(engine) as db:
        set_identity(db, visitor["user_id"])
        assert db.scalar(select(func.count(Call.id)).where(Call.run_id == rid)) >= 3


def test_reset_serializes_with_key_issuance_and_rechecks_after_wait(client, local, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from intent_platform import local_identity
    entered = Event(); original = local_identity.locked_account
    def observed_lock(db, user_id):
        entered.set()
        return original(db, user_id)
    with Session(engine) as db:
        account = original(db, local["user"])
        local_identity.invalidate_account(db, account, revoke_keys=True)
        monkeypatch.setattr(local_identity, "locked_account", observed_lock)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.post, "/api/v2/me/api-keys", headers=local["headers"], json={"name": "Concurrent issuance"})
            try:
                assert entered.wait(5), "Request did not reach account authority lock"
            finally:
                db.commit()
            assert future.result(timeout=5).status_code == 401
    with Session(engine) as db:
        assert db.scalar(select(func.count(IntegrationKey.id)).where(IntegrationKey.user_id == local["user"])) == 0


def test_key_cannot_expand_current_function_access(client, local):
    from intent_platform.models import Function
    a, fid = app(client, local); issued = key(client, local); runtime = embed(client, a, issued["secret"])
    headers = bearer(runtime["access_token"])
    cid = conversation(client, headers, a["id"]); rid = message(client, headers, cid, "Identity sample"); drain()
    assert client.get(f"/api/v2/runtime/conversations/{cid}/history", headers=headers).status_code == 200
    with Session(engine) as db, db.begin():
        set_identity(db, local["user"])
        function = db.get(Function, fid); function.access_mode = "allowlist"; function.allowed_users = []
    for path in (f"/api/v2/runtime/runs/{rid}", f"/api/v2/runtime/conversations/{cid}/history", f"/api/v2/runtime/conversations/{cid}/events"):
        assert client.get(path, headers=headers).status_code == 403
