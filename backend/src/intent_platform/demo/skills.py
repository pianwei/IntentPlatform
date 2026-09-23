"""Five versionable, finite demonstration rules and explicit fixture parsing.

The matcher is a labelled local preview. Production planning must use the
published Skill snapshot and the authorized model/plan validator.
"""

from copy import deepcopy
from datetime import date, timedelta
import re

from .engine import initial_state


_INPUT = lambda name: {"$input": name}
_STEP = lambda step, path: {"$step": step, "path": path}
_ITEM = lambda name: {"$item": name}


_SKILLS = [
    {
        "key": "demo.skill.customer_funds", "name": "客户资金概览", "version": 1,
        "description": "定位客户、查其账户，再逐账户查询余额；客户同名时须先选择，不得自动取第一项。",
        "triggers": ["资金概览", "所有账户余额", "客户账户余额", "资产账户概览"],
        "input_fields": ["customer_query"],
        "steps": [
            {"step_id": "customer", "capability_key": "demo.customer.search", "arguments": {"query": _INPUT("customer_query")}, "depends_on": []},
            {"step_id": "accounts", "capability_key": "demo.account.list",
             "arguments": {"customer_id": _STEP("customer", "data.customers.0.customer_id")}, "depends_on": ["customer"]},
            {"step_id": "balances", "capability_key": "demo.account.balance",
             "arguments": {"account_id": _ITEM("account_id")}, "depends_on": ["accounts"],
             "foreach": {"step": "accounts", "path": "data.accounts", "item_key": "account_id"}},
        ],
    },
    {
        "key": "demo.skill.account_cashflow", "name": "账户收支分析", "version": 1,
        "description": "读取指定区间的账户流水，并用确定性统计功能核算收入、支出与净额。",
        "triggers": ["收支分析", "期间收支", "收入支出情况", "流水和汇总"],
        "input_fields": ["account_id", "start_date", "end_date"],
        "steps": [
            {"step_id": "transactions", "capability_key": "demo.transaction.list",
             "arguments": {"account_id": _INPUT("account_id"), "start_date": _INPUT("start_date"),
                           "end_date": _INPUT("end_date")}, "depends_on": []},
            {"step_id": "summary", "capability_key": "demo.transaction.summary",
             "arguments": {"account_id": _INPUT("account_id"), "start_date": _INPUT("start_date"),
                           "end_date": _INPUT("end_date")}, "depends_on": ["transactions"]},
        ],
    },
    {
        "key": "demo.skill.customer_loans", "name": "客户贷款概览", "version": 1,
        "description": "定位客户、列出贷款，逐合同读取余额、还款安排及逾期记录。",
        "triggers": ["贷款概览", "贷款余额和还款", "贷款全貌", "贷款逾期情况"],
        "input_fields": ["customer_query"],
        "steps": [
            {"step_id": "customer", "capability_key": "demo.customer.search", "arguments": {"query": _INPUT("customer_query")}, "depends_on": []},
            {"step_id": "loans", "capability_key": "demo.loan.list",
             "arguments": {"customer_id": _STEP("customer", "data.customers.0.customer_id")}, "depends_on": ["customer"]},
            {"step_id": "balances", "capability_key": "demo.loan.balance", "arguments": {"loan_id": _ITEM("loan_id")},
             "depends_on": ["loans"], "foreach": {"step": "loans", "path": "data.loans", "item_key": "loan_id"}},
            {"step_id": "schedules", "capability_key": "demo.loan.schedule", "arguments": {"loan_id": _ITEM("loan_id")},
             "depends_on": ["loans"], "foreach": {"step": "loans", "path": "data.loans", "item_key": "loan_id"}},
            {"step_id": "overdue", "capability_key": "demo.loan.overdue", "arguments": {"loan_id": _ITEM("loan_id")},
             "depends_on": ["loans"], "foreach": {"step": "loans", "path": "data.loans", "item_key": "loan_id"}},
        ],
    },
    {
        "key": "demo.skill.abnormal_account", "name": "异常账户核查", "version": 1,
        "description": "查询账户状态；仅冻结时在用户确认后为所属客户创建模拟核查工单。",
        "triggers": ["异常账户核查", "冻结账户建工单", "冻结账户核查", "账户状态核查"],
        "input_fields": ["customer_id", "account_id", "reason"],
        "steps": [
            {"step_id": "status", "capability_key": "demo.account.status", "arguments": {"account_id": _INPUT("account_id")}, "depends_on": []},
            {"step_id": "ticket", "capability_key": "demo.ticket.create",
             "arguments": {"customer_id": _INPUT("customer_id"), "account_id": _INPUT("account_id"),
                           "reason": _INPUT("reason")}, "depends_on": ["status"],
             "when": {"step": "status", "path": "data.status", "operator": "eq", "value": "FROZEN"}},
        ],
    },
    {
        "key": "demo.skill.customer_panorama", "name": "客户业务全景", "version": 1,
        "description": "定位客户后并行读取资料、账户、贷款和持有产品。",
        "triggers": ["客户业务全景", "客户综合概览", "客户整体情况", "客户全貌"],
        "input_fields": ["customer_query"],
        "steps": [
            {"step_id": "customer", "capability_key": "demo.customer.search", "arguments": {"query": _INPUT("customer_query")}, "depends_on": []},
            {"step_id": "detail", "capability_key": "demo.customer.detail",
             "arguments": {"customer_id": _STEP("customer", "data.customers.0.customer_id")}, "depends_on": ["customer"]},
            {"step_id": "accounts", "capability_key": "demo.account.list",
             "arguments": {"customer_id": _STEP("customer", "data.customers.0.customer_id")}, "depends_on": ["customer"]},
            {"step_id": "loans", "capability_key": "demo.loan.list",
             "arguments": {"customer_id": _STEP("customer", "data.customers.0.customer_id")}, "depends_on": ["customer"]},
            {"step_id": "holdings", "capability_key": "demo.holding.list",
             "arguments": {"customer_id": _STEP("customer", "data.customers.0.customer_id")}, "depends_on": ["customer"]},
        ],
    },
]


def business_skill_templates():
    return deepcopy(_SKILLS)


def match_skill(text, skills=None):
    """Only exact trigger phrases are eligible in fixture preview; no guessing."""
    candidates = []
    for skill in skills if skills is not None else _SKILLS:
        for trigger in skill.get("triggers", []):
            if trigger and trigger.casefold() in text.casefold():
                candidates.append((len(trigger), skill["key"], skill))
    return deepcopy(sorted(candidates, key=lambda x: (-x[0], x[1]))[0][2]) if candidates else None


def extract_demo_inputs(text, today=None):
    """Extract only explicit IDs, known synthetic names and bounded date phrases.

    This helper supports visibly labelled fixture preview. It cannot replace a
    real model or resolve a name that matches several customer records.
    """
    if not isinstance(text, str):
        raise ValueError("Text required")
    result = {}
    for label, prefix in (("customer_id", "C"), ("account_id", "A"),
                          ("loan_id", "L"), ("transaction_id", "T"),
                          ("product_id", "P"), ("ticket_id", "K")):
        matches = set(re.findall(rf"(?<![A-Za-z0-9]){prefix}\d{{3}}(?![A-Za-z0-9])", text, flags=re.I))
        if len(matches) == 1:
            result[label] = next(iter(matches)).upper()
    if "customer_id" in result:
        result["customer_query"] = result["customer_id"]
    else:
        names = {row["name"] for row in initial_state()["customers"].values() if row["name"] in text}
        if len(names) == 1:
            result["customer_query"] = next(iter(names))
    dates = re.findall(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", text)
    if len(dates) >= 2:
        result["start_date"], result["end_date"] = dates[:2]
    elif "近30天" in text or "近三十天" in text:
        end = today or date.today()
        result["start_date"] = (end - timedelta(days=29)).isoformat()
        result["end_date"] = end.isoformat()
    reason = re.search(r"(?:原因|因为)[：:是]?([^，。；;]{2,80})", text)
    if reason:
        result["reason"] = reason.group(1).strip()
    return result
