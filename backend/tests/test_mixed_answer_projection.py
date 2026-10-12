"""Model input boundaries for mixed answers; no database or remote model calls."""
import json
from types import SimpleNamespace

import pytest

from intent_platform import model_answer, model_profiles, planning, services, worker


@pytest.fixture
def completed_business(monkeypatch):
    definition = {
        "name": "授权订单汇总", "compiler_version": "finite-json-v1",
        "output_schema": {"type": "object", "additionalProperties": False,
                          "properties": {"count": {"type": "integer"},
                                         "source": {"type": "string", "maxLength": 100}},
                          "required": ["count", "source"]},
        "presentation": {"component": "detail", "title": "订单汇总",
                         "field_map": {"数量": "count", "来源": "source"}},
    }
    step = SimpleNamespace(step_key="internal-step", status="SUCCEEDED", definition=definition,
                           spec={}, result={"data": {"count": 7, "source": "已批准的模拟订单资料",
                                                     "secret": "must-never-be-shown"}})

    class ReadOnlySession:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args): return SimpleNamespace(id="internal-run")
        def scalars(self, *args): return [step]

    monkeypatch.setattr(planning, "Session", lambda *_: ReadOnlySession())
    monkeypatch.setattr(planning, "set_identity", lambda *_: None)
    monkeypatch.setattr(worker, "current_token", lambda *_: True)
    monkeypatch.setattr(services, "authorize_run_capabilities", lambda *_: None)
    work = {"id": "internal-run", "user_id": "test-user", "workspace_id": "test-workspace",
            "text": "查询订单数量，并解释应收账款是什么意思",
            "snapshot": {"model_profile": {"mode": "openai"}, "three_layer_prompt_revision": "scope-v9"}}
    plan = SimpleNamespace(id="internal-plan", skill_version_id=None, snapshot={
        "steps": [{"step_id": "internal-step", "arguments": {}}], "result_steps": ["internal-step"],
        "three_layer": {"goal": {"goal": work["text"]}}, "general_question": "应收账款是什么意思"})
    return work, plan


def test_mixed_business_model_cannot_see_general_request_or_management_fields(completed_business, monkeypatch):
    work, plan = completed_business
    observed = []

    def business_call(_snapshot, _user, _space, system, payload):
        data = json.loads(payload)
        assert "question" not in data
        assert "应收账款" not in payload and work["text"] not in payload
        assert "internal-" not in payload and "must-never-be-shown" not in payload
        assert data["business_outcomes"] == ["授权订单汇总"]
        assert data["results"] == [{"name": "授权订单汇总", "data": {"count": 7, "source": "已批准的模拟订单资料"}}]
        assert "Do not add general knowledge" in system
        observed.append("business")
        return {"answer": "订单共7笔，来源：已批准的模拟订单资料。"}

    def general_call(_snapshot, _user, _space, _system, payload):
        assert json.loads(payload) == {"question": "应收账款是什么意思"}
        observed.append("general")
        return {"answer": "应收账款是尚未收取的货款。"}

    monkeypatch.setattr(model_profiles, "structured_model_call", business_call)
    monkeypatch.setattr(model_answer, "structured_model_call", general_call)
    answer = planning._summarize(work, plan)
    business, general = answer.split("\n\n通用解释（模型回答）：\n")
    assert "7笔" in business and "应收账款" not in business
    assert general == "应收账款是尚未收取的货款。"
    assert observed == ["business", "general"]


@pytest.mark.parametrize("revision,mixed", [("scope-v8", True), ("scope-v9", False)])
def test_old_mixed_and_pure_business_keep_their_original_question(completed_business, monkeypatch, revision, mixed):
    work, plan = completed_business
    work["snapshot"]["three_layer_prompt_revision"] = revision
    if not mixed:
        plan.snapshot.pop("general_question")

    def call(_snapshot, _user, _space, system, payload):
        data = json.loads(payload)
        assert data["question"] == work["text"]
        assert "business_outcomes" not in data
        assert "This is only the business-facts section" not in system
        return {"answer": "已有业务答复"}

    monkeypatch.setattr(model_profiles, "structured_model_call", call)
    monkeypatch.setattr(model_answer, "explain_general", lambda *_: "既有一般解释")
    answer = planning._summarize(work, plan)
    assert answer == ("业务结果：\n已有业务答复\n\n通用解释（模型回答）：\n既有一般解释" if mixed else "已有业务答复")


def test_general_model_failure_preserves_completed_facts_without_business_replay(completed_business, monkeypatch):
    work, plan = completed_business
    calls = []

    def business_call(*args):
        calls.append("business-summary")
        return {"answer": "已查询到7笔订单。"}

    def general_failure(*args):
        calls.append("general-explanation")
        raise TimeoutError("synthetic model timeout")

    monkeypatch.setattr(model_profiles, "structured_model_call", business_call)
    monkeypatch.setattr(model_answer, "structured_model_call", general_failure)
    answer = planning._summarize(work, plan)
    assert answer.startswith("业务结果：\n已查询到7笔订单。")
    assert "通用解释暂不可用；以上已完成的业务结果保留。" in answer
    assert calls == ["business-summary", "general-explanation"]


def test_authorization_revocation_blocks_both_synthesis_calls(completed_business, monkeypatch):
    work, plan = completed_business
    monkeypatch.setattr(worker, "current_token", lambda *_: False)
    monkeypatch.setattr(model_profiles, "structured_model_call", lambda *_: pytest.fail("must not synthesize after revocation"))
    monkeypatch.setattr(model_answer, "structured_model_call", lambda *_: pytest.fail("must not explain after revocation"))
    with pytest.raises(PermissionError, match="authorization changed"):
        planning._summarize(work, plan)
