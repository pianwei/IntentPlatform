"""Workspace hierarchy and archival on real restricted PostgreSQL roles."""
import asyncio
import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from intent_platform.db import engine, set_identity, now
from intent_platform.models import (Workspace, Membership, Agent, Release, RuntimeSession, AuthToken,
                                    Run, Call, IngestJob, ResultFile)
from intent_platform.identity_models import LocalAccount
from intent_platform.local_identity import secret_hash
from conftest import conversation, message, drain
from test_local_identity import local, app, guest, key, embed, ticket, bearer, PASSWORD


def base(local):
    return f"/api/v2/management/workspaces/{local['ws']}"


def metadata(client, local):
    result = client.get(base(local), headers=local["headers"])
    assert result.status_code == 200, result.text
    return result.json()


def transition(client, local, action):
    result = client.post(base(local) + "/" + action, headers=local["headers"], json={"revision": metadata(client, local)["revision"]})
    assert result.status_code == 200, result.text
    return result.json()


def account(client, local, roles=("user",)):
    user = "wsreader_" + uuid.uuid4().hex
    with Session(engine) as db, db.begin():
        db.add(LocalAccount(user_id=user, username=user, display_name="张明", password_hash=secret_hash(PASSWORD), must_change_password=False))
        db.add(Membership(user_id=user, workspace_id=local["ws"], roles=list(roles)))
    login = client.post("/api/v2/auth/login", json={"username": user, "password": PASSWORD})
    assert login.status_code == 200
    return user, bearer(login.json()["access_token"])


def test_directory_name_revision_and_platform_tenant_separation(client, local):
    user, headers = account(client, local, ["admin"])
    ws = client.post("/api/v2/management/workspaces", headers=local["headers"], json={"name": "独立空间", "administrator_id": user}).json()["id"]
    platform = client.get("/api/v2/management/platform/workspaces", headers=local["headers"], params={"query": "独立空间", "limit": 1})
    assert platform.status_code == 200 and platform.json()["total"] >= 1
    entry = next(v for v in platform.json()["items"] if v["id"] == ws)
    assert entry["roles"] == [] and entry["can_archive"] and not entry["can_open"]
    assert entry["administrators"][0]["display_name"] == "张明"
    assert client.get(f"/api/v2/management/workspaces/{ws}/agents", headers=local["headers"]).status_code == 403
    assert client.get("/api/v2/management/platform/workspaces", headers=headers).status_code == 403
    path = f"/api/v2/management/workspaces/{ws}"
    changed = client.patch(path, headers=headers, json={"name": "改名空间", "revision": entry["revision"]})
    assert changed.status_code == 200 and changed.json()["revision"] == entry["revision"] + 1
    assert client.patch(path, headers=headers, json={"name": "旧修改", "revision": entry["revision"]}).status_code == 409
    assert client.post(path + "/archive", headers=headers, json={"revision": changed.json()["revision"]}).status_code == 403
    archived = client.post(path + "/archive", headers=local["headers"], json={"revision": changed.json()["revision"]})
    assert archived.status_code == 200, archived.text
    assert client.get(path + "/conversations", headers=local["headers"]).status_code == 403
    assert client.get(path + "/conversations", headers=headers).status_code == 200
    assert client.post(path + "/restore", headers=headers, json={"revision": archived.json()["revision"]}).status_code == 403


def test_member_page_names_search_and_effective_authority(client, local):
    a, fid = app(client, local)
    user, headers = account(client, local, ["maintainer", "user"])
    result = client.get(base(local) + "/members/page", headers=local["headers"], params={"query": "张明", "limit": 1})
    assert result.status_code == 200 and result.json()["total"] == 1
    member = result.json()["items"][0]
    assert member["user_id"] == user and member["display_name"] == "张明" and member["username"] == user
    assert member["roles"] == ["maintainer", "user"] and member["account_active"]
    path = base(local) + f"/members/{user}/effective-access"
    access = client.get(path, headers=local["headers"])
    assert access.status_code == 200, access.text
    allowed = {p["key"]: p["allowed"] for p in access.json()["permissions"]}
    assert allowed["functions.edit"] and not allowed["releases.publish"] and not allowed["members.manage"]
    assert any(item["id"] == a["id"] and item["allowed"] for item in access.json()["agents"])
    assert client.get(path, headers=headers).status_code == 403
    assert client.put(base(local)+f"/functions/{fid}/access", headers=local["headers"], json={"access_mode":"allowlist","allowed_users":[]}).status_code == 200
    updated = client.get(path, headers=local["headers"]).json()
    assert not next(item for item in updated["functions"] if item["id"] == fid)["allowed"]
    empty = client.get(base(local)+"/members/page", headers=local["headers"], params={"query":"%_literal_not_a_pattern"}).json()
    assert empty["items"] == [] and empty["total"] == 0


def test_only_platform_can_assign_administrator_without_business_membership(client, local):
    space_admin, space_headers = account(client, local, ["admin"])
    user, _ = account(client, local)
    created = client.post("/api/v2/management/workspaces", headers=local["headers"],
                          json={"name": "Delegated workspace", "administrator_id": space_admin}).json()
    ws = created["id"]
    target = f"/api/v2/management/workspaces/{ws}/members/{user}"
    assert client.put(target, headers=space_headers, json={"roles": ["admin"], "revision": 0, "active": True}).status_code == 403
    path = f"/api/v2/management/platform/workspaces/{ws}/administrators"
    revision = client.get(f"/api/v2/management/workspaces/{ws}", headers=local["headers"]).json()["revision"]
    body = {"user_id": user, "revision": revision}
    assert client.post(path, headers=space_headers, json=body).status_code == 403
    assigned = client.post(path, headers=local["headers"], json=body)
    assert assigned.status_code == 200, assigned.text
    assert user in [m["user_id"] for m in assigned.json()["administrators"]]
    assert assigned.json()["roles"] == [] and not assigned.json()["can_open"]
    assert client.post(path, headers=local["headers"], json=body).status_code == 409
    assert client.get(f"/api/v2/management/workspaces/{ws}/agents", headers=local["headers"]).status_code == 403


def test_archive_closes_runtime_public_and_admin_write_channels_but_keeps_admin_history(client, local):
    a, fid = app(client, local); issued = key(client, local)
    anonymous = guest(client, a); runtime = embed(client, a, issued["secret"])
    headers = bearer(anonymous["access_token"])
    cid = conversation(client, headers, a["id"]); rid = message(client, headers, cid, "Identity sample"); drain()
    before = copy.deepcopy(client.get(base(local)+f"/agents/{a['id']}/releases", headers=local["headers"]).json())
    archived = transition(client, local, "archive")
    assert archived["status"] == "archived" and archived["archived_at"] and not archived["can_open"]
    for prefix in (headers, bearer(runtime["access_token"])):
        for path in ("/api/v2/runtime/conversations",f"/api/v2/runtime/agents/{a['id']}", f"/api/v2/runtime/conversations/{cid}/history",
                     f"/api/v2/runtime/conversations/{cid}/events", f"/api/v2/runtime/runs/{rid}",f"/api/v2/runtime/runs/{rid}/attempts"):
            assert client.get(path, headers=prefix).status_code in {401,403}, path
    for suffix in ("", "/icon", "/embedding-policy", "/chat-policy"):
        assert client.get(f"/api/v2/runtime/apps/{a['public_id']}"+suffix).status_code in {403,404}
    assert client.post(f"/api/v2/runtime/apps/{a['public_id']}/anonymous-sessions", json={}).status_code in {403,404}
    assert client.post("/api/v2/runtime/embed/handshakes", json={"agent_id":a["id"],"host_origin":"http://localhost:5173"}).status_code == 403
    assert client.post(base(local)+f"/agents/{a['id']}/publish", headers=local["headers"], json={"revision":a["revision"]}).status_code == 403
    assert client.post(base(local)+f"/functions/{fid}/validate", headers=local["headers"], json={}).status_code == 403
    assert client.get(base(local)+"/members/page", headers=local["headers"]).status_code == 403
    for path in ("/conversations", f"/conversations/{cid}", "/runs", f"/runs/{rid}/trace", "/audit"):
        assert client.get(base(local)+path, headers=local["headers"]).status_code == 200, path
    assert client.get("/api/v2/me", headers=local["headers"]).status_code == 200
    assert local["ws"] not in [w["id"] for w in client.get("/api/v2/management/workspaces",headers=local["headers"]).json()]
    transition(client, local, "restore")
    assert client.get(base(local)+f"/agents/{a['id']}/releases",headers=local["headers"]).json() == before


def test_restore_never_revives_old_recovery_tickets_or_revocations_and_global_key_survives(client, local):
    a, fid = app(client, local); issued = key(client, local); runtime = embed(client, a, issued["secret"])
    anonymous = guest(client, a); cid = conversation(client,bearer(anonymous["access_token"]),a["id"])
    pending, body = ticket(client, a, issued["secret"])
    assert pending.status_code == 200
    old_payload = {**body,"ticket":pending.json()["ticket"]}
    client.put(base(local)+f"/functions/{fid}/access", headers=local["headers"], json={"access_mode":"allowlist","allowed_users":[]})
    transition(client, local, "archive"); transition(client, local, "restore")
    for token in (runtime, anonymous):
        assert client.get("/api/v2/runtime/conversations",headers=bearer(token["access_token"])).status_code == 401
    assert client.post("/api/v2/runtime/embed/exchange",json=old_payload).status_code == 403
    path = f"/api/v2/runtime/apps/{a['public_id']}/anonymous-sessions"
    assert client.post(path,json={"runtime_session_id":anonymous["runtime_session_id"],"recovery_secret":anonymous["recovery_secret"]}).status_code == 403
    assert ticket(client,a,issued["secret"],runtime=runtime["runtime_session_id"])[0].status_code == 403
    new_guest = guest(client,a); new_embed = embed(client,a,issued["secret"])
    assert new_embed["runtime_session_id"] != runtime["runtime_session_id"]
    assert client.get("/api/v2/runtime/conversations",headers=bearer(new_guest["access_token"])).json() == []
    assert client.get(f"/api/v2/runtime/conversations/{cid}/history",headers=bearer(new_guest["access_token"])).status_code in {403,404}
    with Session(engine) as db:
        set_identity(db,local["user"])
        assert db.get(Workspace,local["ws"]).access_epoch == 1
        assert db.get(RuntimeSession,new_embed["runtime_session_id"]).workspace_epoch == 1
        from intent_platform.models import Function
        assert db.get(Function,fid).access_mode == "allowlist"


def test_archive_immediately_ends_unsent_runs_and_ingest_without_new_version(client, local):
    a, _ = app(client,local); anonymous=guest(client,a)
    cid=conversation(client,bearer(anonymous["access_token"]),a["id"])
    rid=message(client,bearer(anonymous["access_token"]),cid,"Identity sample")
    queued=client.post(base(local)+"/ingest-jobs/documents", headers={**local["headers"],"Idempotency-Key":"archival-"+uuid.uuid4().hex},files={"file":("archive.txt",b"Synthetic source","text/plain")})
    assert queued.status_code == 202, queued.text
    transition(client,local,"archive")
    with Session(engine) as db:
        set_identity(db,anonymous["user_id"])
        assert db.get(Run,rid).status == "CANCELLED"
        set_identity(db,local["user"])
        job=db.get(IngestJob,queued.json()["job_id"])
        assert job.status=="FAILED" and job.error["code"]=="WORKSPACE_ARCHIVED" and job.result is None
    transition(client,local,"restore"); drain()
    with Session(engine) as db:
        set_identity(db,anonymous["user_id"])
        assert db.scalar(select(Call.id).where(Call.run_id==rid)) is None


def test_archive_cancels_confirmation_and_inflight_intake_cannot_commit_after_restore(client, local):
    from intent_platform import ingest_worker
    from intent_platform.models import Document
    a, _ = app(client, local); runtime = guest(client, a); headers = bearer(runtime["access_token"])
    cid = conversation(client, headers, a["id"]); rid = message(client, headers, cid, "Confirm synthetic write")
    with Session(engine) as db, db.begin():
        set_identity(db, runtime["user_id"])
        run = db.get(Run, rid); run.status = "WAITING_CONFIRMATION"; run.confirmation_expires_at = now() + timedelta(minutes=5)
    filename = "archive-inflight-" + uuid.uuid4().hex + ".txt"
    queued = client.post(base(local)+"/ingest-jobs/documents", headers={**local["headers"], "Idempotency-Key": uuid.uuid4().hex},
                         files={"file": (filename, b"Synthetic delayed parsing")})
    assert queued.status_code == 202
    work = ingest_worker._claim(local["user"], local["ws"])
    assert work["id"] == queued.json()["job_id"]
    transition(client, local, "archive"); transition(client, local, "restore")
    assert client.post(f"/api/v2/runtime/runs/{rid}/confirmations", headers=headers,
                       json={"revision": 1, "confirmed": True}).status_code == 401
    assert not ingest_worker.finish(work, prepared="Completed after archive")
    with Session(engine) as db:
        set_identity(db, runtime["user_id"])
        run = db.get(Run, rid)
        assert run.status == "CANCELLED" and not run.confirmed and run.confirmation_expires_at is None
        assert db.scalar(select(Call.id).where(Call.run_id == rid)) is None
        set_identity(db, local["user"])
        assert db.scalar(select(Document.id).where(Document.filename == filename)) is None


@pytest.mark.parametrize("restore_before_finish", [False, True])
def test_sent_plan_preserves_fact_but_never_dispatches_dependent_steps(client, local, monkeypatch, restore_before_finish):
    from intent_platform import worker, planning
    from intent_platform.models import ExecutionPlan, PlanStep, Conversation
    a, fid = app(client, local); runtime = guest(client, a); headers = bearer(runtime["access_token"])
    cid = conversation(client, headers, a["id"]); rid = message(client, headers, cid, "Identity sample")
    worker.prepare_run(worker.claim_run(runtime["user_id"])); work = worker.claim_call(runtime["user_id"])
    worker.mark_dispatch(work)
    with Session(engine) as db, db.begin():
        set_identity(db, runtime["user_id"])
        call = db.get(Call, work["id"]); conv = db.get(Conversation, cid)
        plan = ExecutionPlan(workspace_id=local["ws"], run_id=rid, release_id=conv.release_id, source="skill", status="EXECUTING",
                             snapshot={"steps": [{"id": "first", "depends_on": []}, {"id": "next", "depends_on": ["first"]}], "result_steps": ["first"]})
        db.add(plan); db.flush()
        first = PlanStep(workspace_id=local["ws"], plan_id=plan.id, step_key="first", capability_id=fid, version=call.version,
                         definition=call.definition, spec={"id": "first"}, status="RUNNING", call_ids=[call.id])
        second = PlanStep(workspace_id=local["ws"], plan_id=plan.id, step_key="next", capability_id=fid, version=call.version,
                          definition=call.definition, spec={"id": "next", "depends_on": ["first"]}, status="PENDING")
        db.add_all([first, second]); db.flush(); plan_id, first_id, second_id = plan.id, first.id, second.id
    transition(client, local, "archive")
    if restore_before_finish: transition(client, local, "restore")
    with pytest.raises(PermissionError, match="before summarization"):
        planning._summarize({**work, "id": rid}, plan)
    def unexpected(*args, **kwargs): pytest.fail("Archived plan must not advance or invoke a summarization model")
    monkeypatch.setattr(planning, "advance", unexpected)
    monkeypatch.setattr(planning, "_summarize", unexpected)
    worker.finish_call(work, {"status": "SUCCEEDED", "data": {"fixture": True, "notice": "Actual completed result"}})
    with Session(engine) as db:
        set_identity(db, runtime["user_id"])
        assert db.get(Call, work["id"]).status == "SUCCEEDED"
        assert db.get(ExecutionPlan, plan_id).status == "CANCELLED"
        assert db.get(PlanStep, first_id).status == "SUCCEEDED"
        assert db.get(PlanStep, second_id).status == "SKIPPED"
        assert len(list(db.scalars(select(Call.id).where(Call.run_id == rid)))) == 1


@pytest.mark.parametrize("outcome", ["SUCCEEDED", "UNKNOWN"])
def test_sent_outcomes_survive_archive_without_replay_or_new_reconciliation(client,local,outcome,monkeypatch):
    from intent_platform import worker
    a,_=app(client,local); anonymous=guest(client,a)
    cid=conversation(client,bearer(anonymous["access_token"]),a["id"])
    rid=message(client,bearer(anonymous["access_token"]),cid,"Identity sample")
    worker.prepare_run(worker.claim_run(anonymous["user_id"])); work=worker.claim_call(anonymous["user_id"])
    assert work and work["run_id"]==rid
    worker.mark_dispatch(work)
    transition(client,local,"archive")
    worker.finish_call(work,{"status":outcome,"data":{"fixture":True,"notice":"Recorded actual result"}})
    def unexpected(*args,**kwargs): pytest.fail("Archive must not send a new call or status query")
    monkeypatch.setattr(worker,"execute_connector",unexpected)
    assert not worker._reconcile_one(anonymous["user_id"],local["ws"])
    with Session(engine) as db:
        set_identity(db,anonymous["user_id"])
        call=db.get(Call,work["id"])
        assert call.status==outcome and call.dispatched_at
        assert db.get(Run,rid).status==outcome


def test_archive_wins_before_dispatch_and_final_boundary_uses_same_workspace_lock(client,local):
    from intent_platform import worker
    from intent_platform.capacity import lock_workspace
    a,_=app(client,local); runtime=embed(client,a,key(client,local)["secret"])
    cid=conversation(client,bearer(runtime["access_token"]),a["id"])
    rid=message(client,bearer(runtime["access_token"]),cid,"Identity sample")
    worker.prepare_run(worker.claim_run(local["user"])); work=worker.claim_call(local["user"])
    assert work and work["run_id"]==rid
    started=ThreadEvent()
    def archive_while_locked():
        started.set()
        return transition(client,local,"archive")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(engine) as db, db.begin():
            set_identity(db,local["user"]); lock_workspace(db,local["ws"])
            future=pool.submit(archive_while_locked); assert started.wait(2)
            assert not future.done()
        assert future.result(timeout=10)["status"]=="archived"
    with pytest.raises(PermissionError): worker.mark_dispatch(work)
    with Session(engine) as db:
        set_identity(db,local["user"])
        assert db.get(Call,work["id"]).dispatched_at is None


def test_sse_and_file_close_while_management_history_remains_readable(client,local):
    from intent_platform.main import events
    a,_=app(client,local); runtime=embed(client,a,key(client,local)["secret"])
    headers=bearer(runtime["access_token"])
    cid=conversation(client,headers,a["id"]);rid=message(client,headers,cid,"Identity sample");drain()
    with Session(engine) as db, db.begin():
        set_identity(db,local["user"])
        call=db.scalar(select(Call).where(Call.run_id==rid))
        artifact=ResultFile(workspace_id=local["ws"],conversation_id=cid,run_id=rid,call_id=call.id,filename="archive.txt",media_type="text/plain",data=b"synthetic")
        db.add(artifact);db.flush();fid=artifact.id
    with Session(engine) as db:
        set_identity(db,local["user"])
        response=events(cid,after=0,stream=True,token=db.get(AuthToken,runtime["access_token"]),db=db)
    async def verify():
        while "heartbeat" not in await anext(response.body_iterator): pass
        transition(client,local,"archive")
        with pytest.raises(StopAsyncIteration): await anext(response.body_iterator)
    asyncio.run(verify())
    assert client.get(f"/api/v2/runtime/files/{fid}",headers=headers).status_code==401
    assert client.get(base(local)+f"/conversations/{cid}",headers=local["headers"]).status_code==200


def test_archive_does_not_block_another_workspace_or_allow_raw_cleanup_function(client,local):
    a,_=app(client,local); runtime=guest(client,a)
    created=client.post("/api/v2/management/workspaces",headers=local["headers"],json={"name":"Another workspace","administrator_id":local["user"]}).json()
    other={**local,"ws":created["id"]};other_app,_=app(client,other);other_runtime=guest(client,other_app)
    with Session(engine) as db:
        set_identity(db,local["user"])
        with pytest.raises(Exception,match="not authorized"):
            db.execute(text("SELECT intent_archive_workspace_work(:ws)"),{"ws":other["ws"]})
    transition(client,local,"archive")
    assert client.get("/api/v2/runtime/conversations",headers=bearer(runtime["access_token"])).status_code==401
    assert client.get("/api/v2/runtime/conversations",headers=bearer(other_runtime["access_token"])).status_code==200
    assert client.get(base(other)+"/functions",headers=local["headers"]).status_code==200
