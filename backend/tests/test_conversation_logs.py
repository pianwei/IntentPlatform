"""Conversation views expose bounded business projections without widening runtime ownership."""
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from conftest import conversation, message, drain
from intent_platform.db import engine, set_identity, now
from intent_platform.models import Conversation, Run, Call, Event, ManagementTrace, ExecutionPlan, ResultFile
from intent_platform.services import emit
from test_trace_projection import _runtime

BASE = "/api/v2/management/workspaces/ws-dev/conversations"

def test_conversation_title_filters_pagination_and_final_answer(client, admin, seeded):
    runtime, agent = _runtime(client, admin, seeded)
    cid = conversation(client, runtime, agent["id"])
    question = seeded["functions"][0]["name"] + " record_id=demo-001"
    rid = message(client, runtime, cid, question); drain()
    unique = "会话检索-" + uuid4().hex[:10]
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin"); run = db.get(Run,rid)
        db.add(ManagementTrace(workspace_id="ws-dev",run_id=rid,layer="entity",kind="goal_recognized",detail={"goal":unique}))
        emit(db,run,"RESULT",{"title":"答复","component":"text","data":"这才是最终总结","components":[],"files":[]})
    response=client.get(BASE,headers=admin,params={"query":unique,"agent_id":agent["id"],"limit":1})
    assert response.status_code==200,response.text
    assert response.json()["total"]==1 and response.json()["items"][0]["title"]==unique
    assert client.get(BASE,headers=admin,params={"query":unique,"offset":1}).json()["items"]==[]
    detail=client.get(BASE+"/"+cid,headers=admin)
    assert detail.status_code==200,detail.text
    turn=detail.json()["turns"][0]
    assert turn["question"]==question and turn["results"][0]["data"]=="这才是最终总结"
    assert len(turn["results"])==1 and turn["timing"]["total_ms"]>=0
    assert client.get(BASE,headers=runtime).status_code==403
    assert client.get(BASE+"/"+cid,headers=runtime).status_code==403
    assert client.get(BASE,headers=admin,params={"created_after":"2026-09-29T00:00:00Z","created_before":"2026-09-28T00:00:00Z"}).status_code==422


def test_admin_can_read_other_owner_but_runtime_cannot(client, admin, seeded):
    _runtime(client,admin,seeded)
    other=client.post("/api/v2/dev/login",json={"user_id":"dev-reader"}).json()
    headers={"Authorization":"Bearer "+other["access_token"]}
    cid=conversation(client,headers,seeded["agent"]["id"])
    rid=message(client,headers,cid,"一个其他用户的记录")
    with Session(engine) as db,db.begin():
        set_identity(db,"dev-reader");r=db.get(Run,rid);r.status="CANCELLED"
    detail=client.get(BASE+"/"+cid,headers=admin)
    assert detail.status_code==200 and detail.json()["turns"][0]["run_id"]==rid
    assert client.get(BASE+"/"+cid,headers=headers).status_code==403
    assert client.get(f"/api/v2/runtime/conversations/{cid}/history",headers=admin).status_code in (403,404)
    assert client.get(BASE.replace("ws-dev","ws-other")+"/"+cid,headers=admin).status_code in (403,404)


def test_missing_plan_final_does_not_substitute_step_and_masks_credentials(client,admin,seeded):
    runtime,agent=_runtime(client,admin,seeded)
    cid=conversation(client,runtime,agent["id"])
    rid=message(client,runtime,cid,seeded["functions"][0]["name"]+" record_id=demo-001");drain()
    with Session(engine) as db,db.begin():
        set_identity(db,"dev-admin");conv=db.get(Conversation,cid);r=db.get(Run,rid)
        r.text="password=must-not-leak"
        db.add(ExecutionPlan(workspace_id="ws-dev",run_id=rid,release_id=conv.release_id,source="model",snapshot={"steps":[]}))
    turn=client.get(BASE+"/"+cid,headers=admin).json()["turns"][0]
    assert turn["results"]==[] and "must-not-leak" not in turn["question"]


def test_new_answer_confirmation_history_uses_validated_interactions(client,admin,seeded):
    from intent_platform.schemas import Definition
    from test_domain_runtime import publish_function
    alias="日志确认-"+uuid4().hex[:8]
    definition=Definition(name=alias,description="合成日志写操作",aliases=[alias],effect="WRITE",requires_confirmation=True,
        input_schema={"type":"object","properties":{"record_id":{"type":"string"}},"required":["record_id"],"additionalProperties":False}).model_dump()
    fid=publish_function(client,admin,definition)
    agent=client.post("/api/v2/management/workspaces/ws-dev/agents",headers=admin,json={"name":alias,"capability_ids":[fid]}).json()
    assert client.post(f"/api/v2/management/workspaces/ws-dev/agents/{agent['id']}/publish",headers=admin).status_code==200
    session=client.post(f"/api/v2/runtime/apps/{agent['public_id']}/sessions",headers=admin,json={}).json()
    runtime={"Authorization":"Bearer "+session["access_token"]};write={"name":alias}
    cid=conversation(client,runtime,agent["id"])
    rid=message(client,runtime,cid,write["name"]);drain()
    run=client.get(f"/api/v2/runtime/runs/{rid}",headers=runtime).json()
    assert run["status"]=="WAITING_INPUT",run
    values={f["name"]:"demo-001" for f in run["question"]["fields"]}
    body={"revision":run["revision"],"question_id":run["question"]["id"],"values":values}
    assert client.post(f"/api/v2/runtime/runs/{rid}/answers",headers=runtime,json=body).status_code==200
    assert client.post(f"/api/v2/runtime/runs/{rid}/answers",headers=runtime,json=body).status_code==409
    drain(); run=client.get(f"/api/v2/runtime/runs/{rid}",headers=runtime).json()
    assert run["status"]=="WAITING_CONFIRMATION",run
    assert client.post(f"/api/v2/runtime/runs/{rid}/confirmations",headers=runtime,json={"revision":run["revision"],"confirmed":False}).status_code==200
    turn=client.get(BASE+"/"+cid,headers=admin).json()["turns"][0]
    assert [i["kind"] for i in turn["interactions"]]==["answer","confirmation"]
    assert turn["interactions"][-1]["detail"]["confirmed"] is False
    confirm=next(p for p in turn["prompts"] if p["kind"]=="CONFIRMATION")
    assert confirm["payload"]["actions"][0]["name"]==write["name"]


def test_admin_file_can_be_read_in_old_paginated_turn_only_if_approved(client,admin,seeded):
    runtime,agent=_runtime(client,admin,seeded)
    cid=conversation(client,runtime,agent["id"])
    rid=message(client,runtime,cid,seeded["functions"][0]["name"]+" record_id=demo-001");drain()
    with Session(engine) as db,db.begin():
        set_identity(db,"dev-admin");run=db.get(Run,rid);call=db.scalar(select(Call).where(Call.run_id==rid))
        file=ResultFile(workspace_id="ws-dev",conversation_id=cid,run_id=rid,call_id=call.id,filename="result.txt",media_type="text/plain",data=b"approved")
        forbidden=ResultFile(workspace_id="ws-dev",conversation_id=cid,run_id=rid,call_id=call.id,filename="hidden.txt",media_type="text/plain",data=b"hidden")
        db.add_all([file,forbidden]);db.flush();fid,forbidden_id=file.id,forbidden.id
        emit(db,run,"RESULT",{"title":"答复","component":"text","data":"已完成", "files":[{"id":fid,"filename":"result.txt","media_type":"text/plain","size":8}]})
        for i in range(55):
            db.add(Run(workspace_id="ws-dev",conversation_id=cid,user_id="dev-admin",client_message_id=uuid4().hex,text=f"后续 {i}",status="CANCELLED",created_at=now()+timedelta(seconds=i+1)))
    response=client.get(BASE+"/"+cid,headers=admin)
    assert len(response.json()["turns"])==25 and all(r["run_id"]!=rid for r in response.json()["turns"])
    response=client.get(BASE+f"/{cid}/files/{fid}",headers=admin)
    assert response.status_code==200 and response.content==b"approved",response.text
    assert client.get(BASE+f"/{cid}/files/{forbidden_id}",headers=admin).status_code==404
    assert client.get(BASE+f"/{cid}/files/{fid}",headers=runtime).status_code==403
