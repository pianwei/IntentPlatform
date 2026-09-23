"""Pure synthetic business executor. The caller owns workspace-scoped persistence.

For writes, load the workspace's state with a database row lock, call ``execute``,
store the returned state, and commit before acknowledging a business result. The
``operation_id`` journal permits status reconciliation without replaying a write.
"""

from copy import deepcopy
from datetime import date

from ..ontology import input_errors
from .catalog import function_packages


_DEFINITIONS = {p["key"]: p["definition"] for p in function_packages()}
_WRITES = {key for key, definition in _DEFINITIONS.items() if definition["effect"] == "WRITE"}


def initial_state():
    """Every value here is fictional and suitable only for a local demonstration."""
    return {
        "customers": {
            "C001": {"customer_id": "C001", "name": "张晨", "city": "北京", "masked_id": "演示证件-001",
                     "segment": "个人客户", "risk_level": "中", "risk_as_of": "2026-08-31"},
            "C002": {"customer_id": "C002", "name": "李宁", "city": "上海", "masked_id": "演示证件-002",
                     "segment": "个人客户", "risk_level": "低", "risk_as_of": "2026-08-31"},
            "C003": {"customer_id": "C003", "name": "张晨", "city": "深圳", "masked_id": "演示证件-003",
                     "segment": "个人客户", "risk_level": "高", "risk_as_of": "2026-08-31"},
        },
        "accounts": {
            "A001": {"account_id": "A001", "customer_id": "C001", "label": "演示活期账户", "currency": "CNY",
                     "balance_cents": 1200000, "available_cents": 1200000, "status": "ACTIVE", "reason": "正常"},
            "A002": {"account_id": "A002", "customer_id": "C001", "label": "演示备用账户", "currency": "CNY",
                     "balance_cents": 250000, "available_cents": 0, "status": "FROZEN", "reason": "演示既有冻结"},
            "A003": {"account_id": "A003", "customer_id": "C002", "label": "演示活期账户", "currency": "CNY",
                     "balance_cents": 830000, "available_cents": 830000, "status": "ACTIVE", "reason": "正常"},
            "A004": {"account_id": "A004", "customer_id": "C003", "label": "演示活期账户", "currency": "CNY",
                     "balance_cents": 660000, "available_cents": 660000, "status": "ACTIVE", "reason": "正常"},
        },
        "transactions": {
            "T001": {"transaction_id": "T001", "account_id": "A001", "posted_on": "2026-09-01", "direction": "IN",
                     "amount_cents": 200000, "description": "演示工资", "status": "POSTED"},
            "T002": {"transaction_id": "T002", "account_id": "A001", "posted_on": "2026-09-05", "direction": "OUT",
                     "amount_cents": 30000, "description": "演示转账", "status": "POSTED"},
            "T003": {"transaction_id": "T003", "account_id": "A001", "posted_on": "2026-09-20", "direction": "OUT",
                     "amount_cents": 8000, "description": "演示消费", "status": "POSTED"},
            "T004": {"transaction_id": "T004", "account_id": "A002", "posted_on": "2026-09-12", "direction": "IN",
                     "amount_cents": 50000, "description": "演示入账", "status": "POSTED"},
            "T005": {"transaction_id": "T005", "account_id": "A003", "posted_on": "2026-09-10", "direction": "IN",
                     "amount_cents": 180000, "description": "演示入账", "status": "POSTED"},
            "T006": {"transaction_id": "T006", "account_id": "A004", "posted_on": "2026-09-11", "direction": "OUT",
                     "amount_cents": 6000, "description": "演示消费", "status": "POSTED"},
        },
        "loans": {
            "L001": {"loan_id": "L001", "customer_id": "C001", "product_name": "演示经营贷款", "currency": "CNY",
                     "outstanding_cents": 12500000},
            "L002": {"loan_id": "L002", "customer_id": "C002", "product_name": "演示消费贷款", "currency": "CNY",
                     "outstanding_cents": 4500000},
        },
        "payments": [
            {"loan_id": "L001", "due_on": "2026-10-10", "amount_cents": 300000, "status": "DUE"},
            {"loan_id": "L001", "due_on": "2026-11-10", "amount_cents": 300000, "status": "DUE"},
            {"loan_id": "L002", "due_on": "2026-10-15", "amount_cents": 150000, "status": "DUE"},
        ],
        "overdue": [
            {"loan_id": "L001", "due_on": "2026-08-10", "days": 5, "amount_cents": 50000},
        ],
        "products": {
            "P001": {"product_id": "P001", "name": "演示储蓄产品", "category": "储蓄", "description": "模拟存款产品，仅用于页面与路由演示。"},
            "P002": {"product_id": "P002", "name": "演示贷款产品", "category": "贷款", "description": "模拟贷款产品，不提供实际授信。"},
            "P003": {"product_id": "P003", "name": "演示基金产品", "category": "投资", "description": "模拟持仓产品，不构成投资建议。"},
            "P004": {"product_id": "P004", "name": "演示保险产品", "category": "保障", "description": "模拟保障产品。"},
        },
        "holdings": [
            {"holding_id": "H001", "customer_id": "C001", "product_id": "P003", "units": 120.5, "status": "HELD"},
            {"holding_id": "H002", "customer_id": "C002", "product_id": "P001", "units": 2, "status": "HELD"},
        ],
        "tickets": {
            "K001": {"ticket_id": "K001", "customer_id": "C001", "account_id": "A002",
                     "reason": "演示账户状态核查", "status": "OPEN", "history": "2026-09-01 创建"},
        },
        "_operations": {},
        "_customer_permissions": {"dev-admin": ["*"], "dev-reader": ["C001"], "dev-other": ["*"]},
    }


class BusinessFailure(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


_DEFAULT_CUSTOMER_PERMISSIONS = {"dev-admin": ["*"], "dev-reader": ["C001"], "dev-other": ["*"]}


def authorized_customers(user_id, state):
    """Local fixture object permissions; never a substitute for bank authority."""
    grants = state.get("_customer_permissions", _DEFAULT_CUSTOMER_PERMISSIONS).get(user_id, [])
    return set(state["customers"]) if "*" in grants else set(grants) & set(state["customers"])


def authorize_operation(key, args, state, user_id):
    """Check the requested synthetic object before dispatch and again under lock."""
    allowed = authorized_customers(user_id, state)
    owners = []
    if "customer_id" in args:
        owners.append(args["customer_id"])
    for field, collection in (("account_id", "accounts"), ("loan_id", "loans"),
                              ("ticket_id", "tickets")):
        row = state[collection].get(args.get(field)) if args.get(field) else None
        if row:
            owners.append(row["customer_id"])
    if args.get("transaction_id"):
        transaction = state["transactions"].get(args["transaction_id"])
        account = state["accounts"].get(transaction["account_id"]) if transaction else None
        if account:
            owners.append(account["customer_id"])
    if any(owner not in allowed for owner in owners):
        raise PermissionError("Synthetic object authorization denied")
    if key == "demo.customer.search" and not allowed:
        raise PermissionError("Synthetic object authorization denied")
    return allowed


def _required(state, collection, identifier):
    row = state[collection].get(identifier)
    if row is None:
        raise BusinessFailure("NOT_FOUND")
    return row


def _money(cents):
    return cents / 100


def _valid_dates(arguments):
    for key in ("start_date", "end_date"):
        if key in arguments:
            value = arguments[key]
            if len(value) != 10:
                raise BusinessFailure("INVALID_DATE")
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            except ValueError:
                raise BusinessFailure("INVALID_DATE") from None
    if arguments.get("start_date", "0000") > arguments.get("end_date", "9999"):
        raise BusinessFailure("INVALID_DATE_RANGE")


def _within(row_date, arguments):
    return (row_date >= arguments.get("start_date", "0000")
            and row_date <= arguments.get("end_date", "9999"))


def _transaction_view(row, detail=False):
    value = {key: row[key] for key in ("transaction_id", "account_id", "posted_on", "direction", "description")}
    value["amount"] = _money(row["amount_cents"])
    if detail:
        value["status"] = row["status"]
    return value


def _read(key, args, state):
    if key == "demo.customer.search":
        query = args["query"].strip().casefold()
        rows = [row for row in state["customers"].values()
                if row["customer_id"].casefold() == query or query in row["name"].casefold()]
        return {"customers": [{k: row[k] for k in ("customer_id", "name", "city", "masked_id")} for row in rows]}
    if key == "demo.customer.detail":
        row = _required(state, "customers", args["customer_id"])
        return {k: row[k] for k in ("customer_id", "name", "city", "segment")}
    if key == "demo.customer.risk":
        row = _required(state, "customers", args["customer_id"])
        return {"customer_id": row["customer_id"], "risk_level": row["risk_level"], "as_of": row["risk_as_of"]}
    if key == "demo.account.list":
        _required(state, "customers", args["customer_id"])
        rows = [r for r in state["accounts"].values() if r["customer_id"] == args["customer_id"]]
        return {"accounts": [{k: r[k] for k in ("account_id", "customer_id", "label", "currency")} for r in rows]}
    if key == "demo.account.balance":
        row = _required(state, "accounts", args["account_id"])
        return {"account_id": row["account_id"], "balance": _money(row["balance_cents"]),
                "available": _money(row["available_cents"]), "currency": row["currency"]}
    if key == "demo.account.status":
        row = _required(state, "accounts", args["account_id"])
        return {k: row[k] for k in ("account_id", "status", "reason")}
    if key == "demo.transaction.list":
        _required(state, "accounts", args["account_id"])
        _valid_dates(args)
        if args.get("direction") not in {None, "IN", "OUT"}:
            raise BusinessFailure("INVALID_DIRECTION")
        rows = [r for r in state["transactions"].values() if r["account_id"] == args["account_id"]
                and _within(r["posted_on"], args) and ("direction" not in args or r["direction"] == args["direction"])]
        return {"transactions": [_transaction_view(r) for r in rows]}
    if key == "demo.transaction.detail":
        return _transaction_view(_required(state, "transactions", args["transaction_id"]), detail=True)
    if key == "demo.transaction.summary":
        _required(state, "accounts", args["account_id"])
        _valid_dates(args)
        rows = [r for r in state["transactions"].values() if r["account_id"] == args["account_id"]
                and _within(r["posted_on"], args)]
        incoming = sum(r["amount_cents"] for r in rows if r["direction"] == "IN")
        outgoing = sum(r["amount_cents"] for r in rows if r["direction"] == "OUT")
        return {"account_id": args["account_id"], "start_date": args["start_date"], "end_date": args["end_date"],
                "income": _money(incoming), "expense": _money(outgoing), "net": _money(incoming - outgoing), "count": len(rows)}
    if key == "demo.loan.list":
        _required(state, "customers", args["customer_id"])
        rows = [r for r in state["loans"].values() if r["customer_id"] == args["customer_id"]]
        return {"loans": [{k: r[k] for k in ("loan_id", "customer_id", "product_name", "currency")} for r in rows]}
    if key == "demo.loan.balance":
        row = _required(state, "loans", args["loan_id"])
        return {"loan_id": row["loan_id"], "outstanding": _money(row["outstanding_cents"]), "currency": row["currency"]}
    if key == "demo.loan.schedule":
        _required(state, "loans", args["loan_id"])
        _valid_dates(args)
        rows = [r for r in state["payments"] if r["loan_id"] == args["loan_id"] and _within(r["due_on"], args)]
        return {"payments": [{"loan_id": r["loan_id"], "due_on": r["due_on"], "amount": _money(r["amount_cents"]),
                              "status": r["status"]} for r in rows]}
    if key == "demo.loan.overdue":
        _required(state, "loans", args["loan_id"])
        return {"overdue": [{"loan_id": r["loan_id"], "due_on": r["due_on"], "days": r["days"],
                             "amount": _money(r["amount_cents"])} for r in state["overdue"] if r["loan_id"] == args["loan_id"]]}
    if key == "demo.product.search":
        query = args.get("query", "").strip().casefold()
        category = args.get("category", "").strip().casefold()
        if not query and not category:
            raise BusinessFailure("SEARCH_CONDITION_REQUIRED")
        rows = [r for r in state["products"].values()
                if (not query or query in r["name"].casefold() or query in r["description"].casefold())
                and (not category or category == r["category"].casefold())]
        return {"products": [{k: r[k] for k in ("product_id", "name", "category")} for r in rows]}
    if key == "demo.product.detail":
        row = _required(state, "products", args["product_id"])
        return {k: row[k] for k in ("product_id", "name", "category", "description")}
    if key == "demo.holding.list":
        _required(state, "customers", args["customer_id"])
        rows = [r for r in state["holdings"] if r["customer_id"] == args["customer_id"]]
        return {"holdings": [{**r, "product_name": state["products"][r["product_id"]]["name"]} for r in rows]}
    if key == "demo.ticket.list":
        _required(state, "customers", args["customer_id"])
        rows = [r for r in state["tickets"].values() if r["customer_id"] == args["customer_id"]
                and ("status" not in args or r["status"] == args["status"])]
        return {"tickets": [{k: r[k] for k in ("ticket_id", "customer_id", "account_id", "reason", "status")}
                            for r in rows]}
    if key == "demo.ticket.detail":
        row = _required(state, "tickets", args["ticket_id"])
        return {k: row[k] for k in ("ticket_id", "customer_id", "account_id", "reason", "status", "history")}
    raise BusinessFailure("UNKNOWN_OPERATION")


def _write(key, args, state):
    if key == "demo.ticket.create":
        _required(state, "customers", args["customer_id"])
        account = _required(state, "accounts", args["account_id"])
        if account["customer_id"] != args["customer_id"]:
            raise BusinessFailure("ACCOUNT_CUSTOMER_MISMATCH")
        identifier = f"K{max(int(k[1:]) for k in state['tickets']) + 1:03d}"
        row = {"ticket_id": identifier, "customer_id": args["customer_id"], "account_id": args["account_id"],
               "reason": args["reason"], "status": "OPEN", "history": "演示核查工单已创建"}
        state["tickets"][identifier] = row
        return {k: row[k] for k in ("ticket_id", "customer_id", "account_id", "reason", "status")}
    if key == "demo.account.freeze":
        row = _required(state, "accounts", args["account_id"])
        if row["status"] == "FROZEN":
            raise BusinessFailure("ALREADY_FROZEN")
        row["status"] = "FROZEN"
        row["available_cents"] = 0
        row["reason"] = args["reason"]
        return {k: row[k] for k in ("account_id", "status", "reason")}
    raise BusinessFailure("UNKNOWN_OPERATION")


def _envelope(status, operation_id, data=None, error_code=None):
    value = {"status": status, "operation_id": operation_id, "data": data or {}, "fixture_mode": True}
    if error_code:
        value["error_code"] = error_code
    return value


def execute(operation_key, arguments, state, operation_id, fault=None, user_id=None):
    """Run one synthetic capability; return ``(connector_envelope, next_state)``.

    ``fault`` is an internal test-only switch, never a published business input.
    Accepted values are ``definite_failure``, ``timeout_before_commit`` and
    ``unknown_after_commit``. The last is meaningful for writes and proves that
    a committed operation can be reconciled by ID without being replayed.
    """
    if not isinstance(state, dict) or not isinstance(arguments, dict) or not operation_id:
        raise ValueError("Valid demonstration state, arguments and operation ID required")
    if operation_key not in _DEFINITIONS:
        return _envelope("FAILED", operation_id, error_code="UNKNOWN_OPERATION"), state
    allowed = authorize_operation(operation_key, arguments, state, user_id) if user_id is not None else None
    errors = input_errors(_DEFINITIONS[operation_key]["input_schema"], arguments)
    if errors:
        return _envelope("FAILED", operation_id, error_code="INVALID_ARGUMENTS"), state
    if fault not in {None, "definite_failure", "timeout_before_commit", "unknown_after_commit"}:
        raise ValueError("Unknown demonstration fault")
    if fault == "definite_failure":
        return _envelope("FAILED", operation_id, error_code="SIMULATED_FAILURE"), state
    if fault == "timeout_before_commit":
        return _envelope("UNKNOWN", operation_id, error_code="SIMULATED_TIMEOUT"), state
    if operation_key in _WRITES:
        existing = state.get("_operations", {}).get(operation_id)
        if existing:
            if existing["operation_key"] != operation_key or existing["arguments"] != arguments:
                return _envelope("FAILED", operation_id, error_code="OPERATION_ID_CONFLICT"), state
            return _envelope("SUCCEEDED", operation_id, existing["data"]), state
    next_state = deepcopy(state) if operation_key in _WRITES else state
    try:
        data = _write(operation_key, arguments, next_state) if operation_key in _WRITES else _read(operation_key, arguments, next_state)
    except BusinessFailure as exc:
        return _envelope("FAILED", operation_id, error_code=exc.code), state
    if operation_key == "demo.customer.search" and allowed is not None:
        data["customers"] = [row for row in data["customers"] if row["customer_id"] in allowed]
    if operation_key in _WRITES:
        next_state.setdefault("_operations", {})[operation_id] = {
            "operation_key": operation_key, "arguments": deepcopy(arguments), "data": deepcopy(data),
        }
    if fault == "unknown_after_commit":
        return _envelope("UNKNOWN", operation_id, error_code="SIMULATED_RESPONSE_LOSS"), next_state
    return _envelope("SUCCEEDED", operation_id, data), next_state


def reconcile(operation_id, state):
    """Read a write's committed outcome; never invokes the business handler."""
    record = state.get("_operations", {}).get(operation_id)
    return (_envelope("SUCCEEDED", operation_id, deepcopy(record["data"])) if record
            else _envelope("UNKNOWN", operation_id, error_code="OPERATION_NOT_COMMITTED"))
