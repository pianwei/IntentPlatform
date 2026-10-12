"""The development report must not mark wrong arguments/results as correct."""
import importlib.util
from pathlib import Path

path = Path(__file__).parents[1] / "scripts/verify_deepseek_demo.py"
spec = importlib.util.spec_from_file_location("demo_diagnostic", path)
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


def test_development_matrix_has_five_cases_per_function_without_fake_required_field():
    cases = diagnostic.development_cases()
    assert len(cases) == 100
    assert len({case[0] for case in cases}) == 100
    for number in range(1, 21):
        assert len([case for case in cases if case[0].startswith(f"function-{number:02d}-")]) == 5
    assert next(case for case in cases if case[0] == "function-14-5")[3] == "SUCCEEDED"
    assert next(case for case in cases if case[0] == "function-05-5")[3] == "WAITING_INPUT"


def test_gold_rejects_wrong_object_even_when_its_business_result_is_valid():
    from intent_platform.demo.engine import execute, initial_state
    args = {"account_id": "A003"}
    result = execute("demo.account.balance", args, initial_state(), "test-gold", user_id="dev-admin")[0]
    steps = [{"name": "查询账户余额", "status": "SUCCEEDED", "input": args, "output": result["data"]}]
    gold = diagnostic.check_gold("function-05-1", steps, "SUCCEEDED")
    assert not gold["parameters_correct"]
    assert not gold["results_correct"]
    assert not diagnostic.check_executed_results("查询 A001 余额", steps, "function-05-1")[0]["passed"]


def test_schema_enum_translation_is_checked_against_frozen_gold():
    from intent_platform.demo.engine import execute, initial_state
    args = diagnostic.GOLD_INPUTS[6]
    result = execute("demo.transaction.list", args, initial_state(), "test-enum", user_id="dev-admin")[0]
    steps = [{"name": "查询交易流水", "status": "SUCCEEDED", "input": args, "output": result["data"]}]
    check = diagnostic.check_executed_results(diagnostic.COVERAGE_CASES[6][1], steps, "function-07-1")
    assert check[0]["passed"]
    steps[0]["output"] = {"transactions": []}
    assert not diagnostic.check_gold("function-07-1", steps, "SUCCEEDED")["results_correct"]


def test_missing_input_case_cannot_pass_after_target_dispatch():
    steps = [{"name": "查询账户余额", "status": "SUCCEEDED", "input": {"account_id": "A001"}, "output": {}}]
    assert not diagnostic.check_gold("function-05-5", steps, "WAITING_INPUT")["no_target_execution"]


def test_admin_or_text_output_does_not_replace_missing_user_final_component():
    result = {"account_id": "A001", "status": "ACTIVE"}
    events = [{"event_type": "RESULT", "payload": {"data": str(result), "components": []}}]
    assert not diagnostic.final_output_contains(events, result)
    events[0]["payload"]["components"] = [{"data": {"approved": [result]}}]
    assert diagnostic.final_output_contains(events, result)


def test_single_item_batch_trace_keeps_exact_object_oracle():
    from intent_platform.demo.engine import execute, initial_state
    args = {"loan_id": "L001"}
    result = execute("demo.loan.balance", args, initial_state(), "single-batch", user_id="dev-admin")[0]["data"]
    steps = [{"name": "查询贷款余额", "status": "SUCCEEDED", "input": args, "output": [result]}]
    assert diagnostic.check_executed_results("查询 L001 的贷款余额", steps)[0]["passed"]
    steps[0]["output"] = [{**result, "outstanding": 1}]
    assert not diagnostic.check_executed_results("查询 L001 的贷款余额", steps)[0]["passed"]
    steps[0]["output"] = [result, result]
    assert not diagnostic.check_executed_results("查询 L001 的贷款余额", steps)[0]["passed"]
