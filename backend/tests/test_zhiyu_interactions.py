"""Zhiyu API/Worker interactions with explicitly synthetic model proposals.

These prove current published planning, binding, ledger and recovery behavior;
they do not score real model routing or rewrite the installed catalog.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from intent_platform.db import engine, set_identity
from intent_platform.models import Call, SyntheticState
from intent_platform.synthetic_runtime import CATALOG_ID
from test_local_identity import local, bearer, guest
from test_zhiyu_runtime import zhiyu
from conftest import conversation, message, drain


def proposal_fixture(monkeypatch, key, arguments, mention=None):
    from intent_platform import three_layer
    def proposal(work, skills):
        cap = next(c for c in work["candidates"]
                   if c["definition"]["connector"].get("workflow_id") == "zhiyu." + key)
        return ({"inputs": {}, "steps": [{"step_id": "business", "capability_id": cap["capability_id"],
                  "arguments": deepcopy(arguments), "depends_on": []}]}, "model", None,
                {"goal": work["text"], "entities": [{"type": "zhiyu:Customer", "mention": mention}] if mention else [],
                 "actions": [cap["definition"]["action"]], "negated": False}, [cap["capability_id"]])
    monkeypatch.setattr(three_layer, "propose", proposal)


def read(client, headers, run_id):
    response = client.get(f"/api/v2/runtime/runs/{run_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def evidence(identity, workspace, run_id):
    with Session(engine) as db:
        set_identity(db, identity["user_id"])
        calls = [{"id": c.id, "status": c.status, "arguments": c.arguments, "result": c.result,
                  "effect": c.definition["effect"], "connector_version": c.definition["connector"].get("version")}
                 for c in db.scalars(select(Call).where(Call.run_id == run_id))]
        state = deepcopy(db.get(SyntheticState, (workspace, CATALOG_ID)).state)
        return calls, state


def test_same_name_selection_continues_with_one_explicit_customer(client, local, zhiyu, monkeypatch):
    proposal_fixture(monkeypatch, "customer.profile", {"customer_id": "远川制造"}, "远川制造")
    identity = guest(client, zhiyu)
    headers = bearer(identity["access_token"])
    cid = conversation(client, headers, zhiyu["id"])
    rid = message(client, headers, cid, "查看远川制造客户资料")
    drain()
    pending = read(client, headers, rid)
    assert pending["status"] == "WAITING_INPUT" and pending["question"]["kind"] == "entity_choice"
    assert len(pending["question"]["options"]) == 2
    assert evidence(identity, local["ws"], rid)[0] == []
    selected = next(option for option in pending["question"]["options"] if "ZY-C004" in option["label"])
    body = {"revision": pending["revision"], "question_id": pending["question"]["id"], "option_id": selected["id"]}
    assert client.post(f"/api/v2/runtime/runs/{rid}/answers", headers=headers, json=body).status_code == 200
    assert client.post(f"/api/v2/runtime/runs/{rid}/answers", headers=headers, json=body).status_code == 409
    drain()
    assert read(client, headers, rid)["status"] == "SUCCEEDED"
    calls, _ = evidence(identity, local["ws"], rid)
    assert len(calls) == 1 and calls[0]["arguments"] == {"customer_id": "ZY-C004"}
    assert calls[0]["result"]["data"]["customer_id"] == "ZY-C004" and calls[0]["connector_version"] == "3"


def test_missing_customer_input_continues_without_replanning_or_early_calls(client, local, zhiyu, monkeypatch):
    proposal_fixture(monkeypatch, "customer.profile", {"customer_id": {"$input": "customer_id"}})
    identity = guest(client, zhiyu)
    headers = bearer(identity["access_token"])
    cid = conversation(client, headers, zhiyu["id"])
    rid = message(client, headers, cid, "查询客户资料")
    drain()
    pending = read(client, headers, rid)
    assert pending["status"] == "WAITING_INPUT" and pending["question"]["kind"] == "planner_arguments"
    assert [field["name"] for field in pending["question"]["fields"]] == ["customer_id"]
    assert evidence(identity, local["ws"], rid)[0] == []
    body = {"revision": pending["revision"], "question_id": pending["question"]["id"], "values": {"customer_id": "星澜科技"}}
    assert client.post(f"/api/v2/runtime/runs/{rid}/answers", headers=headers, json=body).status_code == 200
    assert client.post(f"/api/v2/runtime/runs/{rid}/answers", headers=headers, json=body).status_code == 409
    drain()
    assert read(client, headers, rid)["status"] == "SUCCEEDED"
    calls, _ = evidence(identity, local["ws"], rid)
    assert len(calls) == 1 and calls[0]["arguments"] == {"customer_id": "ZY-C001"}


@pytest.mark.parametrize("change", ["reject", "new_goal"])
def test_marketing_rejection_or_changed_goal_invalidates_old_confirmation(client, local, zhiyu, monkeypatch, change):
    args = {"customer_id": "ZY-C001", "title": "回访", "due_on": "2026-10-20"}
    proposal_fixture(monkeypatch, "marketing.create", args, "ZY-C001")
    identity = guest(client, zhiyu)
    headers = bearer(identity["access_token"])
    cid = conversation(client, headers, zhiyu["id"])
    rid = message(client, headers, cid, "给ZY-C001创建营销任务：回访，截止2026-10-20")
    before = evidence(identity, local["ws"], rid)[1]
    drain()
    pending = read(client, headers, rid)
    assert pending["status"] == "WAITING_CONFIRMATION" and evidence(identity, local["ws"], rid)[0] == []
    stale = {"revision": pending["revision"], "confirmed": True}
    if change == "reject":
        response = client.post(f"/api/v2/runtime/runs/{rid}/confirmations", headers=headers,
                               json={"revision": pending["revision"], "confirmed": False})
        assert response.status_code == 200 and response.json()["status"] == "CANCELLED"
    else:
        replacement = message(client, headers, cid, "取消创建，改为查询客户资料")
        replacement_state = read(client, headers, replacement)
        assert client.post(f"/api/v2/runtime/runs/{replacement}/cancel", headers=headers,
                           json={"revision": replacement_state["revision"]}).status_code == 200
    assert client.post(f"/api/v2/runtime/runs/{rid}/confirmations", headers=headers, json=stale).status_code == 409
    drain()
    assert read(client, headers, rid)["status"] == "CANCELLED"
    calls, state = evidence(identity, local["ws"], rid)
    assert calls == [] and state["marketing_tasks"] == before["marketing_tasks"]
    assert state.get("_operations", {}) == before.get("_operations", {})


def test_duplicate_marketing_message_and_anonymous_recovery_create_one_task(client, local, zhiyu, monkeypatch):
    args = {"customer_id": "ZY-C001", "title": "单次回访核验", "due_on": "2026-10-20"}
    proposal_fixture(monkeypatch, "marketing.create", args, "ZY-C001")
    identity = guest(client, zhiyu)
    headers = bearer(identity["access_token"])
    cid = conversation(client, headers, zhiyu["id"])
    body = {"text": "给ZY-C001创建营销任务：单次回访核验，截止2026-10-20", "client_message_id": str(uuid.uuid4())}
    def send():
        return client.post(f"/api/v2/runtime/conversations/{cid}/messages", headers=headers, json=body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: send(), range(2)))
    assert all(response.status_code == 202 for response in responses)
    assert len({response.json()["run_id"] for response in responses}) == 1
    rid = responses[0].json()["run_id"]
    before = evidence(identity, local["ws"], rid)[1]
    drain()
    pending = read(client, headers, rid)
    assert pending["status"] == "WAITING_CONFIRMATION" and evidence(identity, local["ws"], rid)[0] == []
    confirmation = {"revision": pending["revision"], "confirmed": True}
    assert client.post(f"/api/v2/runtime/runs/{rid}/confirmations", headers=headers, json=confirmation).status_code == 200
    drain()
    assert read(client, headers, rid)["status"] == "SUCCEEDED"
    restored = guest(client, zhiyu, runtime_session_id=identity["runtime_session_id"], recovery_secret=identity["recovery_secret"])
    headers = bearer(restored["access_token"])
    assert client.get(f"/api/v2/runtime/conversations/{cid}/history", headers=headers).status_code == 200
    repeated = send()
    assert repeated.status_code == 202 and repeated.json()["run_id"] == rid
    assert client.post(f"/api/v2/runtime/runs/{rid}/confirmations", headers=headers, json=confirmation).status_code == 409
    drain()
    calls, state = evidence(restored, local["ws"], rid)
    assert len(calls) == 1 and calls[0]["status"] == "SUCCEEDED" and calls[0]["effect"] == "WRITE"
    assert calls[0]["arguments"] == args
    added = set(state["marketing_tasks"]) - set(before["marketing_tasks"])
    assert len(added) == 1 and state["marketing_tasks"][added.pop()]["title"] == args["title"]
    assert len(state.get("_operations", {})) == len(before.get("_operations", {})) + 1
