"""The local 20-capability catalog is synthetic engineering data only."""

from datetime import date

import pytest

from intent_platform.demo import (
    action_definitions, business_skill_templates, domain_definition,
    execute, function_packages, initial_state, reconcile,
)
from intent_platform.demo.skills import extract_demo_inputs, match_skill
from intent_platform.domains import compile_domain
from intent_platform.ontology import compile_definition, input_errors
from intent_platform.output_contract import check_output_contract, project_output
from intent_platform.schemas import Definition


PACKAGES = function_packages()


def test_domain_actions_and_twenty_published_definition_shapes():
    projection = compile_domain(domain_definition())
    assert len(PACKAGES) == 20
    assert len({item["key"] for item in PACKAGES}) == 20
    assert len({item["intent"] for item in PACKAGES}) == 20
    assert len(action_definitions()) == 6
    for package in PACKAGES:
        definition = Definition.model_validate(package["definition"]).model_dump()
        assert definition["connector"] == {**definition["connector"], "kind": "fixture", "version": "2", "workflow_id": package["key"]}
        assert definition["object_type"] in projection["concepts"]
        assert definition["result_type"] in projection["concepts"]
        assert not input_errors(definition["input_schema"], package["sample_arguments"])
        assert len(package["evaluation_utterances"]) >= 5
        assert len(definition["positive_examples"]) >= 5
        assert compile_definition(definition)["consistent"]
        assert check_output_contract(definition)
        assert package["fixture_only"]
    assert sum(p["definition"]["effect"] == "WRITE" for p in PACKAGES) == 2


def test_catalog_can_compile_against_a_published_domain_reference():
    projection = compile_domain(domain_definition())
    for package in function_packages({"package_id": "synthetic-domain", "version": 1}):
        assert compile_definition(package["definition"], projection=projection)["compiler_version"] == "finite-json-v2"


@pytest.mark.parametrize("package", PACKAGES, ids=lambda p: p["key"])
def test_each_function_runs_and_returns_approved_result(package):
    key, definition, args = package["key"], package["definition"], package["sample_arguments"]
    result, state = execute(key, args, initial_state(), f"op-{key}")
    assert result["status"] == "SUCCEEDED"
    assert result["fixture_mode"] is True
    assert project_output(definition, result["data"])[0] == result["data"]
    assert state is not None


@pytest.mark.parametrize("package", PACKAGES, ids=lambda p: p["key"])
def test_each_function_rejects_bad_input_and_has_bounded_fault_outcomes(package):
    key, args = package["key"], package["sample_arguments"]
    state = initial_state()
    invalid, unchanged = execute(key, {**args, "_unexpected": True}, state, "bad")
    assert invalid["status"] == "FAILED" and invalid["error_code"] == "INVALID_ARGUMENTS" and unchanged is state
    failed, unchanged = execute(key, args, state, "failed", fault="definite_failure")
    assert failed["status"] == "FAILED" and unchanged is state
    unknown, unchanged = execute(key, args, state, "timeout", fault="timeout_before_commit")
    assert unknown["status"] == "UNKNOWN" and unchanged is state


def test_relationships_filters_and_integer_cent_accounting():
    state = initial_state()
    found, _ = execute("demo.customer.search", {"query": "张晨"}, state, "lookup")
    assert [r["customer_id"] for r in found["data"]["customers"]] == ["C001", "C003"]
    accounts, _ = execute("demo.account.list", {"customer_id": "C001"}, state, "list")
    assert [r["account_id"] for r in accounts["data"]["accounts"]] == ["A001", "A002"]
    filtered, _ = execute("demo.transaction.list", {"account_id": "A001", "direction": "OUT"}, state, "tx")
    assert [r["transaction_id"] for r in filtered["data"]["transactions"]] == ["T002", "T003"]
    summary, _ = execute("demo.transaction.summary", {"account_id": "A001", "start_date": "2026-09-01",
                                                        "end_date": "2026-09-30"}, state, "sum")
    assert summary["data"] == {"account_id": "A001", "start_date": "2026-09-01", "end_date": "2026-09-30",
                               "income": 2000.0, "expense": 380.0, "net": 1620.0, "count": 3}
    empty, _ = execute("demo.loan.list", {"customer_id": "C003"}, state, "empty")
    assert empty["data"]["loans"] == []
    wrong, _ = execute("demo.transaction.summary", {"account_id": "A001", "start_date": "2026-09-31",
                                                      "end_date": "2026-10-01"}, state, "wrong")
    assert wrong["error_code"] == "INVALID_DATE"


def test_writes_are_stateful_idempotent_and_reconcilable_without_replay():
    original = initial_state()
    args = {"customer_id": "C001", "account_id": "A001", "reason": "演示核查"}
    first, changed = execute("demo.ticket.create", args, original, "write-1", fault="unknown_after_commit")
    assert first["status"] == "UNKNOWN" and original["tickets"] == initial_state()["tickets"]
    assert reconcile("write-1", changed)["status"] == "SUCCEEDED"
    assert reconcile("write-1", original)["status"] == "UNKNOWN"
    repeat, same = execute("demo.ticket.create", args, changed, "write-1")
    assert repeat["data"]["ticket_id"] == "K002" and same is changed and len(same["tickets"]) == 2
    conflict, same = execute("demo.ticket.create", {**args, "reason": "不同原因"}, changed, "write-1")
    assert conflict["error_code"] == "OPERATION_ID_CONFLICT" and same is changed
    wrong_owner, unchanged = execute("demo.ticket.create", {**args, "customer_id": "C002"}, changed, "write-2")
    assert wrong_owner["error_code"] == "ACCOUNT_CUSTOMER_MISMATCH" and unchanged is changed
    frozen, frozen_state = execute("demo.account.freeze", {"account_id": "A001", "reason": "演示冻结"}, changed, "freeze-1")
    assert frozen["status"] == "SUCCEEDED"
    status, _ = execute("demo.account.status", {"account_id": "A001"}, frozen_state, "read-1")
    balance, _ = execute("demo.account.balance", {"account_id": "A001"}, frozen_state, "read-2")
    assert status["data"]["status"] == "FROZEN" and balance["data"]["available"] == 0.0
    tickets, _ = execute("demo.ticket.list", {"customer_id": "C001"}, frozen_state, "read-3")
    assert [r["ticket_id"] for r in tickets["data"]["tickets"]] == ["K001", "K002"]


def test_five_skill_templates_and_explicit_fixture_extraction():
    skills = business_skill_templates()
    assert len(skills) == 5 and len({s["key"] for s in skills}) == 5
    keys = {p["key"] for p in PACKAGES}
    for skill in skills:
        assert skill["steps"] and len(skill["steps"]) <= 8
        assert all(step["capability_key"] in keys for step in skill["steps"])
        assert all(dep in {s["step_id"] for s in skill["steps"]} for step in skill["steps"] for dep in step["depends_on"])
    selected = match_skill("请给我李宁的客户资金概览", skills)
    assert selected["key"] == "demo.skill.customer_funds"
    assert match_skill("查李宁的账户和工单", skills) is None
    assert extract_demo_inputs("李宁近30天的账户 A003 收支分析", date(2026, 9, 23)) == {
        "account_id": "A003", "customer_query": "李宁", "start_date": "2026-08-25", "end_date": "2026-09-23"}
