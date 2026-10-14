import importlib.util
from pathlib import Path

import pytest

from intent_platform import zhiyu


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "int035_realdata.py"
SPEC = importlib.util.spec_from_file_location("int035_realdata", MODULE_PATH)
int035 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(int035)


def snapshot():
    return {
        "meta": {
            "read_only_transaction_verified": True,
            "production_origin_independently_verified": False,
        },
        "managers": [{"manager_id": "ZY-M010", "name": "manager-sample"}],
        "customers": [
            {"customer_id": "ZY-C010", "name": "customer-sample-1", "manager_id": "ZY-M010"},
            {"customer_id": "ZY-C011", "name": "customer-sample-2", "manager_id": "ZY-M011"},
        ],
        "customer_latest_metrics": [{"customer_id": "ZY-C010", "cny_deposit_balance": 123.45}],
        "payroll": [{"customer_id": "ZY-C010", "payroll_count": 7}],
        "credit_limits": [{"customer_id": "ZY-C010", "currency_code": "CNY", "limit_amount": 1000, "available_amount": 400}],
        "customer_tags": [{"customer_id": "ZY-C010", "label": "tag-sample"}],
        "products": [
            {"product_id": "ZY-P010", "name": "product-sample-1", "category": "category-sample", "department": "dept-sample", "description": "description-1"},
            {"product_id": "ZY-P011", "name": "product-sample-2", "category": "category-sample", "department": "dept-sample", "description": "description-2"},
        ],
        "marketing_segments": [{"segment_id": "ZY-S010", "name": "segment-sample", "description": "segment-description"}],
        "marketing_segment_customers": [{"segment_id": "ZY-S010", "customer_id": "ZY-C010"}],
        "marketing_tasks": [{"task_id": "ZY-MT010", "title": "task-sample", "end_date": "2026-10-20", "status_code": "OPEN"}],
        "marketing_task_customers": [{"task_id": "ZY-MT010", "customer_id": "ZY-C010"}],
        "visits": [{"visit_id": "ZY-V010", "customer_id": "ZY-C010", "visited_at": "2026-10-01", "status_code": "DONE", "result": "sensitive free text", "theme": "sensitive theme"}],
    }


def test_source_replay_maps_only_supported_rows_and_strips_visit_free_text():
    data = snapshot()
    state = int035.source_state(data, zhiyu.initial_state())

    assert state["customers"]["ZY-C010"]["deposit_cents"] == 12345
    assert state["customers"]["ZY-C010"]["credit_used_cents"] == 60000
    assert state["customers"]["ZY-C010"]["payroll_count"] == 7
    assert state["visits"]["ZY-V010"]["minutes"] == ""
    assert len(state["customers"]) == 7
    assert len(state["products"]) == 6
    assert state["cooperation"]["ZY-C001"] == ["ZY-P001", "ZY-P004"]
    assert state["policies"]["ZY-POL001"]["title"] == "模拟异地业务政策"
    assert state["faqs"]["ZY-FAQ001"]["title"] == "模拟合并额度管控填写说明"
    assert len(state["contacts"]) == 3
    assert len(state["todos"]) == 3
    assert len(state["performance"]) == 4
    assert len(state["postloan_tasks"]) == 3
    assert state["_int035_provenance"]["customers"]["ZY-C010"] == "super_workbench_source"
    assert state["_int035_provenance"]["customers"]["ZY-C001"] == "project_simulation"
    assert state["_int035_provenance"]["policies"]["ZY-POL001"] == "project_simulation"
    assert "sensitive free text" not in repr(state)


def test_source_cases_include_fact_assertions_and_a_real_skill_key():
    cases = int035.source_cases(snapshot())
    by_id = {case["id"]: case for case in cases}

    assert by_id["crm-profile-01"]["assertions"]["data.deposit"] == 123.45
    assert by_id["crm-marketing-list-01"]["assertions"]["data.tasks.length"] == 1
    assert by_id["crm-visit-list-01"]["assertions"]["data.visits.0.visit_id"] == "ZY-V010"
    assert by_id["crm-skill-product-comparison-01"]["expected_skill_key"] == "zhiyu.skill.product_comparison"
    assert by_id["crm-permission-cross-manager-01"]["expected_status"] == "SUCCEEDED"
    assert by_id["crm-permission-cross-manager-01"]["expected_keys"] == ["zhiyu.customer.search"]
    assert by_id["crm-permission-cross-manager-01"]["assertions"]["data.customers.length"] == 0
    assert by_id["crm-permission-cross-manager-01"]["boundary_kind"] == "function_owned_object_authorization"
    assert all(case["data_origin"] == "super_workbench_source" for case in cases)


def test_processor_preflight_checks_source_results_without_model_calls():
    report = int035.processor_preflight(snapshot())

    assert report["status"] == "passed"
    assert report["external_model_calls"] == 0
    assert report["checks"] >= 8


def test_source_replay_rejects_unverified_source_mode():
    data = snapshot()
    data["meta"]["read_only_transaction_verified"] = False

    with pytest.raises(ValueError, match="read-only provenance"):
        int035.source_state(data, zhiyu.initial_state())
    with pytest.raises(ValueError, match="read-only provenance"):
        int035.source_cases(data)


def test_source_id_collisions_are_namespaced_without_replacing_simulation():
    data = snapshot()
    data["managers"][0]["manager_id"] = "ZY-M001"
    data["customers"][0]["customer_id"] = "ZY-C001"
    data["customers"][0]["manager_id"] = "ZY-M001"
    data["customer_latest_metrics"][0]["customer_id"] = "ZY-C001"
    data["payroll"][0]["customer_id"] = "ZY-C001"
    data["credit_limits"][0]["customer_id"] = "ZY-C001"
    data["customer_tags"][0]["customer_id"] = "ZY-C001"
    data["products"][0]["product_id"] = "ZY-P001"
    data["marketing_segments"][0]["segment_id"] = "ZY-S001"
    data["marketing_segment_customers"][0].update(segment_id="ZY-S001", customer_id="ZY-C001")
    data["marketing_tasks"][0]["task_id"] = "ZY-MT001"
    data["marketing_task_customers"][0].update(task_id="ZY-MT001", customer_id="ZY-C001")
    data["visits"][0].update(visit_id="ZY-V001", customer_id="ZY-C001")

    state = int035.source_state(data, zhiyu.initial_state())
    ids = state["_int035_source_key_map"]

    assert ids["managers"]["ZY-M001"] != "ZY-M001"
    assert ids["customers"]["ZY-C001"] != "ZY-C001"
    assert ids["products"]["ZY-P001"] != "ZY-P001"
    assert ids["segments"]["ZY-S001"] != "ZY-S001"
    assert ids["marketing_tasks"]["ZY-MT001"] != "ZY-MT001"
    assert ids["visits"]["ZY-V001"] != "ZY-V001"
    assert state["customers"]["ZY-C001"]["manager_id"] == "ZY-M001"
    assert state["customers"][ids["customers"]["ZY-C001"]]["manager_id"] == ids["managers"]["ZY-M001"]
    assert state["products"]["ZY-P001"]["name"] == "星航科创贷"
    assert state["products"][ids["products"]["ZY-P001"]]["name"] == "product-sample-1"
    assert state["_int035_provenance"]["customers"][ids["customers"]["ZY-C001"]] == "super_workbench_source"
    assert int035.processor_preflight(data)["status"] == "passed"


def test_hybrid_pool_keeps_source_cases_and_fills_fixed_quotas_with_simulation():
    source = int035.source_cases(snapshot())
    simulator = int035.simulator_fill_cases(snapshot())
    all_cases = int035.hybrid_cases(snapshot())

    assert simulator
    assert len(all_cases) == 384
    assert all(case["data_origin"] == "project_simulation" for case in simulator)
    source_ids = {case["id"] for case in source}
    hybrid_source_ids = {case["id"] for case in all_cases if case["data_origin"] == "super_workbench_source"}
    assert source_ids == hybrid_source_ids
    assert all(case["data_origin"] in {"super_workbench_source", "project_simulation"} for case in all_cases)
    assert any("zhiyu.policy.search" in case["expected_keys"] for case in simulator)
    assert any("zhiyu.customer.cooperation" in case["expected_keys"] for case in simulator)
    assert any("客户编号ZY-C001" in case["text"] for case in simulator)
    assert len({case["id"] for case in all_cases}) == len(all_cases)


def test_source_origin_caveat_does_not_block_read_only_replay():
    data = snapshot()
    data["meta"].pop("production_origin_independently_verified")

    state = int035.source_state(data, zhiyu.initial_state())

    assert state["_int035_source_snapshot"] is True


def test_lightweight_full_evaluation_has_fixed_384_case_quotas():
    cases = int035.hybrid_cases(snapshot())
    from collections import Counter

    assert len(cases) == 384
    assert Counter(case["group"] for case in cases) == {
        "single": 260, "boundary": 40, "multi_turn": 30,
        "compound": 30, "autonomous": 12, "fallback": 12,
    }
    assert Counter(case["expected_keys"][0] for case in cases if case["group"] == "single") == {
        item["key"]: 10 for item in zhiyu.function_packages()
    }
    assert all(len(case["turns"]) == 2 for case in cases if case["group"] == "multi_turn")
    assert all(case["data_origin"] in {"super_workbench_source", "project_simulation"} for case in cases)
    assert any(case["id"] == "crm-profile-01" for case in cases)
