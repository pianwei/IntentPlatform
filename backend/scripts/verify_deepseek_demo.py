"""Run bounded synthetic questions against the published Demo model.

This development diagnostic never accepts or writes a model API credential.
It logs in with the existing local development identity, then uses the normal
scoped runtime session and management trace APIs. Do not use with real data.
"""

import argparse
import json
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    parser.add_argument("--public-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--workspace", default="ws-dev")
    parser.add_argument("--suite", choices=["composite", "coverage", "skills"], default="composite")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--case-id", choices=[item[0] for item in CASES + COVERAGE_CASES + SKILL_CASES])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    all_cases = [(case_id, question, expected, "SUCCEEDED", "model")
                 for case_id, question, expected in CASES]
    if args.suite == "coverage":
        all_cases = [(*item, "model") for item in COVERAGE_CASES]
    elif args.suite == "skills":
        all_cases = [(*item, "skill") for item in SKILL_CASES]
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
        manager = call(client, "POST", "/api/v2/dev/login", payload={"user_id": "dev-admin"})["access_token"]
        scoped = call(client, "POST", f"/api/v2/runtime/apps/{args.public_id}/sessions", manager, {})["access_token"]
        results = []
        selected = [item for item in all_cases if item[0] == args.case_id] if args.case_id else all_cases[:limit]
        if not selected:
            parser.error("case ID is not in the selected suite")
        for case_id, question, expected, expected_status, expected_source in selected:
            item = {"case_id": case_id, "question": question, "expected_step_names": expected}
            try:
                conversation = call(client, "POST", f"/api/v2/runtime/agents/{args.agent_id}/conversations", scoped, {})
                item["conversation_id"] = conversation["id"]
                item["release_id"] = conversation["release_id"]
                accepted = call(client, "POST", f"/api/v2/runtime/conversations/{conversation['id']}/messages",
                                scoped, {"text": question, "client_message_id": str(uuid.uuid4())})
                item["run_id"] = accepted["run_id"]
                deadline = time.monotonic() + 120
                while True:
                    run = call(client, "GET", f"/api/v2/runtime/runs/{accepted['run_id']}", scoped)
                    if run["status"] in {"SUCCEEDED", "FAILED", "UNKNOWN", "CANCELLED", "WAITING_CONFIRMATION"} or (
                        run["status"] == "WAITING_INPUT" and run.get("question")
                    ) or time.monotonic() >= deadline:
                        break
                    time.sleep(0.5)
                trace = call(client, "GET", f"/api/v2/management/workspaces/{args.workspace}/runs/{accepted['run_id']}/trace", manager)
                steps = run.get("steps", [])
                step_names = [step["name"] for step in steps if step["status"] == "SUCCEEDED"]
                planned_names = [step["name"] for step in steps]
                write_pending = (expected_status == "WAITING_CONFIRMATION" and
                                 all(step["status"] not in {"RUNNING", "SUCCEEDED"}
                                     for step in steps if step["name"] in expected))
                item.update({"status": run["status"], "decision": run.get("decision"),
                             "plan_source": trace.get("plan_source"), "actual_step_names": step_names,
                             "planned_step_names": planned_names,
                             "elapsed_ms": run.get("timing", {}).get("total_ms"),
                             "question_kind": (run.get("question") or {}).get("kind")})
                item["passed"] = (run["status"] == expected_status and trace.get("plan_source") == expected_source
                                  and not (Counter(expected) - Counter(
                                      step_names if expected_status == "SUCCEEDED" else planned_names))
                                  and (expected_status != "WAITING_CONFIRMATION" or write_pending))
            except (httpx.HTTPError, KeyError, TypeError) as exc:
                item.update({"passed": False, "error_type": type(exc).__name__,
                             "error": str(exc)[:300]})
            results.append(item)
            print(f"{case_id}: {'PASS' if item['passed'] else 'FAIL'} {item.get('status', item.get('error_type', ''))}", flush=True)

    report = {"kind": "development-real-model-synthetic-demo", "suite": args.suite,
              "created_at": datetime.now(timezone.utc).isoformat(),
              "base_url": args.base_url, "public_id": args.public_id, "agent_id": args.agent_id,
              "release_id": app["release_id"], "model_label": "DeepSeek-V4.1-Flash; deepseek-flash; thinking disabled",
              "passed": sum(item["passed"] for item in results), "total": len(results), "cases": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Synthetic {args.suite} results: {report['passed']}/{report['total']}", flush=True)
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
