"""Real API/Worker proof for absent, supplied and unauthorized optional filters."""
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from intent_platform.db import engine, set_identity
from intent_platform.models import Call
from test_local_identity import local
from test_zhiyu_runtime import zhiyu, protocol_run
from conftest import drain


@pytest.mark.parametrize("customer_id", [None, "ZY-C001", "ZY-C002"])
def test_marketing_filter_optional_through_plan_binding_and_worker(client, local, zhiyu, monkeypatch, customer_id):
    from intent_platform import three_layer
    from intent_platform.worker import prepare_run
    question = f"查询{customer_id}的营销任务" if customer_id else "查询我的营销任务"
    work, guest, headers, cid, rid = protocol_run(client, zhiyu, text=question)
    capability = next(c for c in work["candidates"] if c["definition"]["connector"].get("workflow_id") == "zhiyu.marketing.list")
    arguments = {"customer_id": customer_id} if customer_id is not None else {}
    def proposal(work, skills):
        entities = [{"type": "zhiyu:Customer", "mention": customer_id}] if customer_id else []
        return ({"inputs": {}, "steps": [{"step_id": "tasks", "capability_id": capability["capability_id"],
                  "arguments": arguments, "depends_on": []}]}, "model", None,
                {"goal": work["text"], "entities": entities, "actions": [capability["definition"]["action"]], "negated": False},
                [capability["capability_id"]])
    monkeypatch.setattr(three_layer, "propose", proposal)
    prepare_run(work)
    drain()
    response = client.get(f"/api/v2/runtime/runs/{rid}", headers=headers)
    assert response.status_code == 200
    with Session(engine) as db:
        set_identity(db, guest["user_id"])
        calls = db.scalars(select(Call).where(Call.run_id == rid)).all()
        if customer_id == "ZY-C002":
            assert response.json()["status"] == "FAILED"
            assert calls == []
        else:
            assert response.json()["status"] == "SUCCEEDED", response.text
            assert len(calls) == 1 and calls[0].status == "SUCCEEDED"
            assert calls[0].arguments == arguments
            bindings = [v for v in calls[0].bindings.values() if isinstance(v, dict) and v.get("parameter") == "customer_id"]
            assert len(bindings) == (0 if customer_id is None else 1)
            assert len(calls[0].result["data"]["tasks"]) == 1
