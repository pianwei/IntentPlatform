"""Run bounded synthetic questions against the published Demo model.

This development diagnostic never accepts or writes a model API credential.
It logs in with an explicitly supplied local account, then uses the normal
scoped runtime session and management trace APIs. Do not use with real data.
"""

import argparse
import json
import math
import statistics
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


CASES = [
    ("transaction-and-status", "比较 T001 交易详情和 A001 账户状态", ["查询交易详情", "查询账户状态"]),
    ("balance-and-status", "查询 A001 的余额和状态", ["查询账户余额", "查询账户状态"]),
    ("customer-risk-and-profile", "告诉我 C001 的风险等级以及客户资料", ["查询客户风险等级记录", "查询客户资料"]),
    ("loan-balance-and-overdue", "查询 L001 的贷款余额和逾期记录", ["查询贷款余额", "查询逾期记录"]),
    ("ticket-detail-and-list", "查询 K001 的工单详情，以及 C001 的工单列表", ["查询工单详情", "查询客户工单"]),
    ("transaction-and-statement", "查询 T001 的交易详情，并列出 A001 在 2026-09-01 至 2026-09-30 的交易流水", ["查询交易详情", "查询交易流水"]),
    ("two-account-status", "分别查询 A001 和 A002 的账户状态", ["查询账户状态", "查询账户状态"]),
    ("holding-and-risk", "查询 C001 持有的产品，同时查询客户风险等级", ["查询客户持有产品", "查询客户风险等级记录"]),
    ("product-and-search", "查询 P001 产品详情，再搜索存款产品", ["查询产品详情", "搜索业务产品"]),
    ("account-status-then-balance", "先查询 A001 的账户状态，再查询 A001 的余额", ["查询账户状态", "查询账户余额"]),
]

COVERAGE_CASES = [
    ("function-01", "请用编号 C001 定位演示客户，返回候选客户编号、姓名和城市。", ["定位客户"], "SUCCEEDED"),
    ("function-02", "客户 C001 的姓名、城市和客户类别是什么？", ["查询客户资料"], "SUCCEEDED"),
    ("function-03", "客户 C001 当前登记的风险等级及记录日期是什么？", ["查询客户风险等级记录"], "SUCCEEDED"),
    ("function-04", "列出客户 C001 名下的账户编号、名称和币种。", ["查询客户账户列表"], "SUCCEEDED"),
    ("function-05", "A001 这个演示账户现在账面余额和可用余额各是多少？", ["查询账户余额"], "SUCCEEDED"),
    ("function-06", "A002 这个演示账户当前处于什么状态，原因是什么？", ["查询账户状态"], "SUCCEEDED"),
    ("function-07", "列出 A001 在 2026-09-01 至 2026-09-30 的支出交易，给出每笔日期和金额。", ["查询交易流水"], "SUCCEEDED"),
    ("function-08", "编号 T001 的这笔演示交易详情是什么，是否已入账？", ["查询交易详情"], "SUCCEEDED"),
    ("function-09", "请按 A001 在 2026-09-01 至 2026-09-30 的记录统计收入、支出、净额和笔数。", ["统计期间收支"], "SUCCEEDED"),
    ("function-10", "客户 C001 名下签了哪些贷款合同？给出合同编号及产品名。", ["查询贷款合同"], "SUCCEEDED"),
    ("function-11", "L001 合同目前还剩多少贷款本金？", ["查询贷款余额"], "SUCCEEDED"),
    ("function-12", "L001 合同接下来有哪些应还日期和每期金额？", ["查询还款计划"], "SUCCEEDED"),
    ("function-13", "L001 合同存在何时发生的逾期记录，逾期多少天和金额？", ["查询逾期记录"], "SUCCEEDED"),
    ("function-14", "帮我搜索类别为储蓄的演示产品，并列出产品编号。", ["搜索业务产品"], "SUCCEEDED"),
    ("function-15", "编号 P003 的演示产品有什么说明？", ["查询产品详情"], "SUCCEEDED"),
    ("function-16", "客户 C001 目前持有哪种演示产品，份额和状态分别是什么？", ["查询客户持有产品"], "SUCCEEDED"),
    ("function-17", "列出 C001 名下仍为 OPEN 的演示核查工单，给出编号和关联账户。", ["查询客户工单"], "SUCCEEDED"),
    ("function-18", "K001 工单的处理记录和原因是什么？", ["查询工单详情"], "SUCCEEDED"),
    ("function-19", "请给客户 C001 的账户 A002 创建一张模拟核查工单，原因是演示账户核验。", ["创建核查工单"], "WAITING_CONFIRMATION"),
    ("function-20", "请将演示账户 A003 临时冻结，原因是演示风险核验。", ["临时冻结演示账户"], "WAITING_CONFIRMATION"),
]

SKILL_CASES = [
    ("skill-customer-funds", "查询 C001 的所有账户余额，给我做资金概览",
     ["定位客户", "查询客户账户列表", "查询账户余额"], "SUCCEEDED"),
    ("skill-account-cashflow", "分析 A001 在 2026-09-01 到 2026-09-30 的收支分析",
     ["查询交易流水", "统计期间收支"], "SUCCEEDED"),
    ("skill-customer-loans", "查询 C001 的贷款概览",
     ["定位客户", "查询贷款合同", "查询贷款余额", "查询还款计划", "查询逾期记录"], "SUCCEEDED"),
    ("skill-abnormal-account", "对 A002 做异常账户核查，客户 C001，原因：演示状态复核",
     ["创建核查工单"], "WAITING_CONFIRMATION"),
    ("skill-customer-panorama", "查询 C001 的客户业务全景",
     ["定位客户", "查询客户资料", "查询客户账户列表", "查询贷款合同", "查询客户持有产品"], "SUCCEEDED"),
]

# Frozen synthetic input/output expectations; do not derive a gold answer from
# the model's chosen arguments. Mutable demo state drift is reported as a failure.
GOLD_INPUTS = [
    {"query": "C001"}, {"customer_id": "C001"}, {"customer_id": "C001"}, {"customer_id": "C001"},
    {"account_id": "A001"}, {"account_id": "A002"},
    {"account_id": "A001", "start_date": "2026-09-01", "end_date": "2026-09-30", "direction": "OUT"},
    {"transaction_id": "T001"}, {"account_id": "A001", "start_date": "2026-09-01", "end_date": "2026-09-30"},
    {"customer_id": "C001"}, {"loan_id": "L001"}, {"loan_id": "L001"}, {"loan_id": "L001"},
    {"category": "储蓄"}, {"product_id": "P003"}, {"customer_id": "C001"},
    {"customer_id": "C001", "status": "OPEN"}, {"ticket_id": "K001"},
    {"customer_id": "C001", "account_id": "A002", "reason": "演示账户核验"},
    {"account_id": "A003", "reason": "演示风险核验"},
]


def development_cases():
    from intent_platform.demo.catalog import function_packages
    result = []
    for index, (base, package, arguments) in enumerate(zip(COVERAGE_CASES, function_packages(), GOLD_INPUTS)):
        cid, question, expected, status = base
        params = "，".join(f"{key}={value}" for key, value in arguments.items())
        questions = [question, f"请帮我{package['intent']}，{params}。", f"{package['definition']['aliases'][0]}；{params}。",
                     f"我需要{package['intent']}的结果，业务参数为：{params}。"]
        result.extend((f"{cid}-{i+1}", q, expected, status, None) for i, q in enumerate(questions))
        missing = next(iter(package["definition"]["input_schema"].get("required", [])), None)
        remaining = {k: v for k, v in arguments.items() if k != missing} if missing else {}
        suffix = "，".join(f"{k}={v}" for k, v in remaining.items())
        if missing:
            result.append((cid + "-5", package["intent"] + ("，" + suffix if suffix else "") + "。缺少的信息请询问，不要猜测。", expected, "WAITING_INPUT", None))
        else:
            # Product search has no required argument. Do not invent a missing
            # parameter requirement solely to force a question in the report.
            result.append((cid + "-5", f"只要{package['intent']}，筛选条件是{params}。", expected, status, None))
    return result


def actual_invocations(steps):
    result = []
    for step in steps:
        inputs, output = step.get("input") or {}, step.get("output")
        if isinstance(inputs.get("items"), list):
            outputs = output if isinstance(output, list) else []
            for index, item in enumerate(inputs["items"]):
                result.append({"name": step["name"], "status": step["status"], "input": item,
                               "output": outputs[index] if index < len(outputs) else None})
        else:
            # The administrator trace flattens a one-object batch's input, while
            # retaining its one-item output list. All registered diagnostic
            # handlers return object data; compare that invocation's object.
            if isinstance(output, list) and len(output) == 1 and isinstance(output[0], dict):
                output = output[0]
            result.append({"name": step["name"], "status": step["status"], "input": inputs, "output": output})
    return result


def check_gold(case_id, steps, expected_status):
    if not case_id.startswith("function-"): return None
    from intent_platform.demo.catalog import function_packages
    from intent_platform.demo.engine import execute, initial_state
    number = int(case_id.split("-")[1]) - 1
    package, arguments = function_packages()[number], GOLD_INPUTS[number]
    rows = [row for row in actual_invocations(steps) if row["name"] == package["intent"]]
    if expected_status == "WAITING_INPUT":
        return {"parameters_correct": True, "results_correct": True,
                "no_target_execution": all(row["status"] not in {"SUCCEEDED", "RUNNING"} for row in rows)}
    if expected_status == "WAITING_CONFIRMATION":
        # Before confirmation, the persisted plan holds resolved inputs in its
        # confirmation event; the caller validates those below instead.
        return {"parameters_correct": None, "results_correct": None, "no_target_execution": all(
            row["status"] not in {"SUCCEEDED", "RUNNING"} for row in rows)}
    expected = execute(package["key"], arguments, initial_state(), "gold-only", user_id="dev-admin")[0]["data"]
    return {"parameters_correct": len(rows) == 1 and rows[0]["input"] == arguments,
            "results_correct": len(rows) == 1 and rows[0]["output"] == expected,
            "no_target_execution": None}


def check_executed_results(question, steps, case_id=""):
    """Independent pure-executor oracle, with argument provenance checked first."""
    from intent_platform.demo.catalog import function_packages
    from intent_platform.demo.engine import execute, initial_state
    by_name = {p["intent"]: p for p in function_packages()}
    prior_values = set()
    def collect(value):
        if isinstance(value, dict):
            for child in value.values(): collect(child)
        elif isinstance(value, list):
            for child in value: collect(child)
        elif isinstance(value, (str, int, float)):
            prior_values.add(str(value))
    checks = []
    for row in actual_invocations(steps):
        if row["status"] != "SUCCEEDED": continue
        package = by_name.get(row["name"])
        if not package:
            checks.append({"name": row["name"], "passed": False, "reason": "unregistered_function"}); continue
        arguments = row["input"]
        expected_arguments = (GOLD_INPUTS[int(case_id.split("-")[1]) - 1]
                              if case_id.startswith("function-") else {})
        # Frozen expectations cover schema-declared Chinese enum aliases such
        # as 支出 -> OUT; matching is not inferred from the chosen model plan.
        provenance = all(str(value) in question or str(value) in prior_values
                         or expected_arguments.get(key) == value
                         for key, value in arguments.items())
        if package["definition"]["effect"] == "WRITE":
            checks.append({"name": row["name"], "passed": False, "reason": "unexpected_write"}); continue
        try:
            oracle = execute(package["key"], arguments, initial_state(), "oracle", user_id="dev-admin")[0]
            correct = oracle["status"] == "SUCCEEDED" and oracle["data"] == row["output"]
        except (ValueError, KeyError, TypeError):
            correct = False
        checks.append({"name": row["name"], "parameters_from_request_or_results": provenance,
                       "output_matches_fixture": correct, "passed": provenance and correct})
        if provenance and correct: collect(row["output"])
    return checks


def final_output_contains(events, expected):
    """Check user-visible structured results, independently of admin trace."""
    def contains(value):
        if value == expected:
            return True
        if isinstance(value, dict):
            return any(contains(child) for child in value.values())
        return any(contains(child) for child in value) if isinstance(value, list) else False
    return any(contains(component.get("data")) for event in events if event["event_type"] == "RESULT"
               for component in event["payload"].get("components", []))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    parser.add_argument("--public-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--workspace", default="ws-dev")
    parser.add_argument("--credentials-file", type=Path, required=True,
                        help="Private local JSON containing username and password; never written to reports")
    parser.add_argument("--suite", choices=["composite", "coverage", "skills", "development"], default="composite")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--case-id", action="append", help="Repeat to diagnose specific cases; full suites remain required for final verification")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.base_url.rstrip("/") not in {"http://127.0.0.1:5173", "http://localhost:5173"}:
        parser.error("Only the local synthetic development service is allowed")
    all_cases = [(case_id, question, expected, "SUCCEEDED", "model")
                 for case_id, question, expected in CASES]
    if args.suite == "coverage":
        all_cases = [(*item, "model") for item in COVERAGE_CASES]
    elif args.suite == "skills":
        all_cases = [(*item, "skill") for item in SKILL_CASES]
    elif args.suite == "development":
        all_cases = development_cases()
    limit = len(all_cases) if args.limit is None else args.limit
    if not 1 <= limit <= len(all_cases):
        parser.error("limit must be within the selected suite size")
    if args.output.exists():
        parser.error("output already exists; keep prior evidence")

    def call(client, method, path, token=None, payload=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = client.request(method, path, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()

    with httpx.Client(base_url=args.base_url, timeout=15, trust_env=False) as client:
        app = call(client, "GET", f"/api/v2/runtime/apps/{args.public_id}")
        if not app.get("fixture_mode") or app.get("agent_id") != args.agent_id:
            raise SystemExit("Target must be the synthetic Demo application")
        credentials = json.loads(args.credentials_file.read_text(encoding="utf-8-sig"))
        manager = call(client, "POST", "/api/v2/auth/login", payload={
            "username": credentials["username"], "password": credentials["password"]})["access_token"]
        del credentials
        scoped = call(client, "POST", f"/api/v2/runtime/apps/{args.public_id}/sessions", manager, {})["access_token"]
        results = []
        if args.case_id and set(args.case_id) - {item[0] for item in all_cases}:
            parser.error("case ID is not in the selected suite")
        selected = [item for item in all_cases if item[0] in args.case_id] if args.case_id else all_cases[:limit]
        if not selected:
            parser.error("case ID is not in the selected suite")
        report = {"kind": "development-real-model-synthetic-demo", "suite": args.suite,
                  "created_at": datetime.now(timezone.utc).isoformat(),
                  "base_url": args.base_url, "public_id": args.public_id, "agent_id": args.agent_id,
                  "release_id": app["release_id"], "model_label": "DeepSeek-V4.1-Flash; deepseek-flash; thinking disabled",
                  "planned_total": len(selected), "passed": 0, "total": 0, "complete": False, "cases": results}
        def save_report():
            report.update(passed=sum(item["passed"] for item in results), total=len(results))
            grouped = {}
            for item in results:
                if item["case_id"].startswith("function-"):
                    key = "-".join(item["case_id"].split("-")[:2])
                    group = grouped.setdefault(key, {"passed": 0, "total": 0})
                    group["total"] += 1; group["passed"] += int(item["passed"])
            report["per_function"] = grouped
            times = sorted(item["elapsed_ms"] for item in results
                           if isinstance(item.get("elapsed_ms"), (float, int)))
            report["completed_run_timing_ms"] = ({"count": len(times), "median": statistics.median(times),
                "p95": times[max(0, math.ceil(len(times) * .95) - 1)], "max": times[-1]} if times else None)
            report["timing_scope"] = "Observed local end-to-end model plus synthetic execution; not bank performance acceptance"
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        save_report()
        for case_id, question, expected, expected_status, expected_source in selected:
            item = {"case_id": case_id, "question": question, "expected_step_names": expected}
            started = time.monotonic()
            try:
                conversation = call(client, "POST", f"/api/v2/runtime/agents/{args.agent_id}/conversations", scoped, {})
                item["conversation_id"] = conversation["id"]
                item["release_id"] = conversation["release_id"]
                if conversation["release_id"] != app["release_id"]:
                    raise ValueError("Published candidate changed during this suite")
                accepted = call(client, "POST", f"/api/v2/runtime/conversations/{conversation['id']}/messages",
                                scoped, {"text": question, "client_message_id": str(uuid.uuid4())})
                item["run_id"] = accepted["run_id"]
                deadline = time.monotonic() + 240
                while True:
                    run = call(client, "GET", f"/api/v2/runtime/runs/{accepted['run_id']}", scoped)
                    if run["status"] in {"SUCCEEDED", "FAILED", "UNKNOWN", "CANCELLED", "WAITING_CONFIRMATION"} or (
                        run["status"] == "WAITING_INPUT" and run.get("question")
                    ) or time.monotonic() >= deadline:
                        break
                    time.sleep(0.5)
                trace = call(client, "GET", f"/api/v2/management/workspaces/{args.workspace}/runs/{accepted['run_id']}/trace", manager)
                steps = trace.get("steps", [])
                step_names = [step["name"] for step in steps if step["status"] == "SUCCEEDED"]
                planned_names = [step["name"] for step in steps]
                write_pending = (expected_status == "WAITING_CONFIRMATION" and
                                 all(step["status"] not in {"RUNNING", "SUCCEEDED"}
                                     for step in steps if step["name"] in expected))
                item.update({"status": run["status"], "decision": run.get("decision"),
                             "plan_source": trace.get("plan_source"), "actual_step_names": step_names,
                             "planned_step_names": planned_names,
                             "elapsed_ms": run.get("timing", {}).get("total_ms"),
                             "question_kind": (run.get("question") or {}).get("kind"),
                             "runtime_question": run.get("question")})
                item["passed"] = (run["status"] == expected_status and (expected_source is None or trace.get("plan_source") == expected_source)
                                  and not (Counter(expected) - Counter(
                                      step_names if expected_status == "SUCCEEDED" else planned_names)) if expected_status != "WAITING_INPUT" else run["status"] == "WAITING_INPUT")
                item["passed"] = (item["passed"]
                                  and (expected_status != "WAITING_CONFIRMATION" or write_pending))
                item["runtime_projection_clean"] = run.get("steps") == [] and run.get("bindings") == []
                item["actual_invocations"] = actual_invocations(steps)
                item["execution_checks"] = check_executed_results(question, steps, case_id)
                events = call(client, "GET", f"/api/v2/runtime/conversations/{conversation['id']}/events", scoped)
                internal_keys = {"skill_version_id", "agent_projection", "model_profile", "compiler_version",
                                 "resolver_ref", "validation_evidence", "call_id", "capability_id", "plan_source"}
                def clean(value):
                    if isinstance(value, dict):
                        return not (set(value) & internal_keys) and all(clean(child) for child in value.values())
                    return all(clean(child) for child in value) if isinstance(value, list) else True
                item["runtime_projection_clean"] = item["runtime_projection_clean"] and clean(events)
                final_results = [event for event in events if event["event_type"] == "RESULT"]
                item["final_result_present"] = bool(final_results and final_results[-1]["payload"].get("data"))
                if expected_status == "SUCCEEDED":
                    item["passed"] = item["passed"] and item["final_result_present"]
                gold = check_gold(case_id, steps, expected_status)
                if gold and expected_status == "SUCCEEDED":
                    from intent_platform.demo.catalog import function_packages
                    from intent_platform.demo.engine import execute, initial_state
                    number = int(case_id.split("-")[1]) - 1
                    target_output = execute(function_packages()[number]["key"], GOLD_INPUTS[number], initial_state(),
                                            "final-gold", user_id="dev-admin")[0]["data"]
                    gold["final_result_correct"] = final_output_contains(events, target_output)
                if args.suite == "composite" and expected_status == "SUCCEEDED":
                    item["requested_final_outputs_present"] = all(final_output_contains(events, row["output"])
                        for row in actual_invocations(steps) if row["name"] in expected and row["status"] == "SUCCEEDED")
                    item["passed"] = item["passed"] and item["requested_final_outputs_present"]
                if gold and expected_status == "WAITING_CONFIRMATION":
                    confirmation = next((e["payload"] for e in reversed(events) if e["event_type"] == "CONFIRMATION"), {})
                    gold["parameters_correct"] = confirmation.get("arguments") == GOLD_INPUTS[int(case_id.split("-")[1]) - 1]
                if gold:
                    item["gold_checks"] = gold
                    item["passed"] = item["passed"] and all(v is not False for v in gold.values())
                item["passed"] = item["passed"] and item["runtime_projection_clean"] and all(c["passed"] for c in item["execution_checks"])
                if run["status"] in {"WAITING_CONFIRMATION", "WAITING_INPUT"}:
                    # Every write case stops before dispatch. Close the pending
                    # test request so it cannot later be confirmed accidentally.
                    cancelled = call(client, "POST", f"/api/v2/runtime/runs/{accepted['run_id']}/cancel", scoped,
                                     {"revision": run["revision"]})
                    item["cleanup_status"] = cancelled["status"]
                    item["passed"] = item["passed"] and cancelled["status"] == "CANCELLED"
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                item.update({"passed": False, "error_type": type(exc).__name__,
                             "error": str(exc)[:300]})
            item["observed_case_ms"] = round((time.monotonic() - started) * 1000, 2)
            results.append(item)
            save_report()
            print(f"{case_id}: {'PASS' if item['passed'] else 'FAIL'} {item.get('status', item.get('error_type', ''))}", flush=True)

    report["complete"] = True
    save_report()
    print(f"Synthetic {args.suite} results: {report['passed']}/{report['total']}", flush=True)
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
