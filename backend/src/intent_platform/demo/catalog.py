"""Twenty distinct, parameter-sensitive synthetic business capability packages.

The current finite Definition and output-contract schemas are the source of the
wire shape. A caller must publish these as fixture versions in a development
workspace; this module deliberately has no database or production side effects.
"""

from copy import deepcopy


def _string(length=120):
    return {"type": "string", "minLength": 1, "maxLength": length}


def _number():
    return {"type": "number"}


def _integer():
    return {"type": "integer"}


def _object(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


def _rows(properties):
    return {"type": "array", "items": _object(properties), "maxItems": 100}


def _schema(properties, required=None):
    return _object(properties, required)


_S = _string
_N = _number
_I = _integer


def domain_definition():
    """Definition accepted by DomainDefinition and the existing domain compiler."""
    objects = {
        "Customer": "客户", "Account": "账户", "Transaction": "交易",
        "Loan": "贷款", "Product": "产品", "Holding": "持仓", "Ticket": "工单",
    }
    concepts = []
    for name, label in objects.items():
        concepts.append({"id": f"demo:{name}", "label": f"演示{label}", "kind": "object", "parents": []})
        concepts.append({"id": f"demo:{name}Result", "label": f"演示{label}结果", "kind": "result", "parents": []})
    relations = [
        ("hasAccount", "Customer", "Account"),
        ("hasTransaction", "Account", "Transaction"),
        ("hasLoan", "Customer", "Loan"),
        ("hasHolding", "Customer", "Holding"),
        ("holdsProduct", "Holding", "Product"),
        ("hasTicket", "Customer", "Ticket"),
        ("concernsAccount", "Ticket", "Account"),
    ]
    return {
        "name": "演示银行业务实体", "description": "仅供本地 Demo 使用的关联模拟实体；不是银行主数据或真实业务定义。",
        "source": {"reference": "demo-catalog-v1", "owner": "intent-platform-demo"},
        "concepts": concepts,
        "relations": [{"id": f"demo:{name}", "source_type": f"demo:{source}", "target_type": f"demo:{target}"}
                      for name, source, target in relations],
        "units": [{"id": "CNY", "dimension": "currency"}], "conversions": [],
    }


def action_definitions():
    return deepcopy([
        {"key": "LOCATE", "name": "定位", "description": "按可信业务标识或名称寻找实体候选；多候选须消歧。", "effect": "READ_ONLY"},
        {"key": "READ", "name": "查询", "description": "读取一个已定位实体的获准字段。", "effect": "READ_ONLY"},
        {"key": "LIST", "name": "列出", "description": "按关系或条件列出有限数量的实体。", "effect": "READ_ONLY"},
        {"key": "SUMMARIZE", "name": "统计", "description": "由获准原始记录进行确定性统计。", "effect": "READ_ONLY"},
        {"key": "CREATE", "name": "创建", "description": "经用户确认后创建模拟业务记录。", "effect": "WRITE"},
        {"key": "FREEZE", "name": "冻结", "description": "经用户确认后修改模拟账户状态。", "effect": "WRITE"},
    ])


# key, title, object, result, action, input properties, required inputs,
# output properties, example arguments, aliases, business purpose.
_PACKAGES = [
    ("demo.customer.search", "定位客户", "Customer", "Customer", "LOCATE",
     {"query": _S(80)}, ["query"],
     {"customers": _rows({"customer_id": _S(20), "name": _S(80), "city": _S(80), "masked_id": _S(40)})},
     {"query": "张晨"}, ["查找客户", "寻找客户", "客户是谁"], "按编号或姓名定位演示客户；同名返回候选供用户选择。"),
    ("demo.customer.detail", "查询客户资料", "Customer", "Customer", "READ",
     {"customer_id": _S(20)}, ["customer_id"],
     {"customer_id": _S(20), "name": _S(80), "city": _S(80), "segment": _S(80)},
     {"customer_id": "C001"}, ["客户基本资料", "客户信息"], "读取已定位客户的模拟基础资料。"),
    ("demo.customer.risk", "查询客户风险等级记录", "Customer", "Customer", "READ",
     {"customer_id": _S(20)}, ["customer_id"],
     {"customer_id": _S(20), "risk_level": _S(40), "as_of": _S(10)},
     {"customer_id": "C001"}, ["客户风险等级", "风险评级记录"], "读取已登记风险等级；不产生风险建议。"),
    ("demo.account.list", "查询客户账户列表", "Customer", "Account", "LIST",
     {"customer_id": _S(20)}, ["customer_id"],
     {"accounts": _rows({"account_id": _S(20), "customer_id": _S(20), "label": _S(80), "currency": _S(8)})},
     {"customer_id": "C001"}, ["客户有哪些账户", "账户清单"], "按客户关系列出其模拟账户。"),
    ("demo.account.balance", "查询账户余额", "Account", "Account", "READ",
     {"account_id": _S(20)}, ["account_id"],
     {"account_id": _S(20), "balance": _N(), "available": _N(), "currency": _S(8)},
     {"account_id": "A001"}, ["余额多少", "可用余额"], "查询一个演示账户的余额和可用余额。"),
    ("demo.account.status", "查询账户状态", "Account", "Account", "READ",
     {"account_id": _S(20)}, ["account_id"],
     {"account_id": _S(20), "status": _S(40), "reason": _S(160)},
     {"account_id": "A002"}, ["账户是否冻结", "账户状态"], "查询一个演示账户的当前状态及原因。"),
    ("demo.transaction.list", "查询交易流水", "Account", "Transaction", "LIST",
     {"account_id": _S(20), "start_date": _S(10), "end_date": _S(10), "direction": _S(8)}, ["account_id"],
     {"transactions": _rows({"transaction_id": _S(20), "account_id": _S(20), "posted_on": _S(10),
                              "direction": _S(8), "amount": _N(), "description": _S(160)})},
     {"account_id": "A001", "start_date": "2026-09-01", "end_date": "2026-09-30"},
     ["账户流水", "交易记录"], "按账户、日期与收支方向筛选模拟交易。"),
    ("demo.transaction.detail", "查询交易详情", "Transaction", "Transaction", "READ",
     {"transaction_id": _S(20)}, ["transaction_id"],
     {"transaction_id": _S(20), "account_id": _S(20), "posted_on": _S(10), "direction": _S(8),
      "amount": _N(), "description": _S(160), "status": _S(40)},
     {"transaction_id": "T001"}, ["流水详情", "交易明细"], "读取一笔模拟交易的获准字段。"),
    ("demo.transaction.summary", "统计期间收支", "Account", "Transaction", "SUMMARIZE",
     {"account_id": _S(20), "start_date": _S(10), "end_date": _S(10)},
     ["account_id", "start_date", "end_date"],
     {"account_id": _S(20), "start_date": _S(10), "end_date": _S(10),
      "income": _N(), "expense": _N(), "net": _N(), "count": _I()},
     {"account_id": "A001", "start_date": "2026-09-01", "end_date": "2026-09-30"},
     ["收支汇总", "这段时间收入支出"], "以整数分为单位确定性计算模拟收支。"),
    ("demo.loan.list", "查询贷款合同", "Customer", "Loan", "LIST",
     {"customer_id": _S(20)}, ["customer_id"],
     {"loans": _rows({"loan_id": _S(20), "customer_id": _S(20), "product_name": _S(80), "currency": _S(8)})},
     {"customer_id": "C001"}, ["客户贷款", "贷款合同列表"], "列出客户的模拟贷款合同。"),
    ("demo.loan.balance", "查询贷款余额", "Loan", "Loan", "READ",
     {"loan_id": _S(20)}, ["loan_id"],
     {"loan_id": _S(20), "outstanding": _N(), "currency": _S(8)},
     {"loan_id": "L001"}, ["剩余本金", "贷款还剩多少"], "查询演示贷款的剩余本金。"),
    ("demo.loan.schedule", "查询还款计划", "Loan", "Loan", "LIST",
     {"loan_id": _S(20), "start_date": _S(10), "end_date": _S(10)}, ["loan_id"],
     {"payments": _rows({"loan_id": _S(20), "due_on": _S(10), "amount": _N(), "status": _S(40)})},
     {"loan_id": "L001"}, ["还款安排", "下一期还款"], "查询演示合同的还款计划。"),
    ("demo.loan.overdue", "查询逾期记录", "Loan", "Loan", "LIST",
     {"loan_id": _S(20)}, ["loan_id"],
     {"overdue": _rows({"loan_id": _S(20), "due_on": _S(10), "days": _I(), "amount": _N()})},
     {"loan_id": "L001"}, ["贷款有没有逾期", "逾期历史"], "读取演示合同的逾期记录。"),
    ("demo.product.search", "搜索业务产品", "Product", "Product", "LOCATE",
     {"query": _S(80), "category": _S(40)}, [],
     {"products": _rows({"product_id": _S(20), "name": _S(80), "category": _S(40)})},
     {"query": "储蓄"}, ["产品搜索", "有哪些产品"], "按关键词或类别搜索模拟业务产品。"),
    ("demo.product.detail", "查询产品详情", "Product", "Product", "READ",
     {"product_id": _S(20)}, ["product_id"],
     {"product_id": _S(20), "name": _S(80), "category": _S(40), "description": _S(500)},
     {"product_id": "P001"}, ["产品说明", "产品规则"], "读取一个演示产品的说明。"),
    ("demo.holding.list", "查询客户持有产品", "Customer", "Holding", "LIST",
     {"customer_id": _S(20)}, ["customer_id"],
     {"holdings": _rows({"holding_id": _S(20), "customer_id": _S(20), "product_id": _S(20),
                         "product_name": _S(80), "units": _N(), "status": _S(40)})},
     {"customer_id": "C001"}, ["客户持仓", "持有哪些产品"], "列出客户持有的模拟产品。"),
    ("demo.ticket.list", "查询客户工单", "Customer", "Ticket", "LIST",
     {"customer_id": _S(20), "status": _S(40)}, ["customer_id"],
     {"tickets": _rows({"ticket_id": _S(20), "customer_id": _S(20), "account_id": _S(20),
                        "reason": _S(200), "status": _S(40)})},
     {"customer_id": "C001"}, ["工单列表", "处理中工单"], "按客户与状态列出模拟核查工单。"),
    ("demo.ticket.detail", "查询工单详情", "Ticket", "Ticket", "READ",
     {"ticket_id": _S(20)}, ["ticket_id"],
     {"ticket_id": _S(20), "customer_id": _S(20), "account_id": _S(20),
      "reason": _S(200), "status": _S(40), "history": _S(500)},
     {"ticket_id": "K001"}, ["核查工单详情", "工单处理记录"], "读取演示工单内容及处理记录。"),
    ("demo.ticket.create", "创建核查工单", "Ticket", "Ticket", "CREATE",
     {"customer_id": _S(20), "account_id": _S(20), "reason": _S(200)},
     ["customer_id", "account_id", "reason"],
     {"ticket_id": _S(20), "customer_id": _S(20), "account_id": _S(20),
      "reason": _S(200), "status": _S(40)},
     {"customer_id": "C001", "account_id": "A002", "reason": "演示状态核查"},
     ["新建核查工单", "发起账户核查"], "确认后创建一张模拟核查工单。"),
    ("demo.account.freeze", "临时冻结演示账户", "Account", "Account", "FREEZE",
     {"account_id": _S(20), "reason": _S(200)}, ["account_id", "reason"],
     {"account_id": _S(20), "status": _S(40), "reason": _S(200)},
     {"account_id": "A001", "reason": "演示临时冻结"},
     ["冻结账户", "临时止付"], "确认后更改隔离模拟账户的状态。"),
]


def function_packages(domain_ref=None):
    """Return 20 fresh packages with stable operation keys and full Definitions.

    Pass an existing published ``domain_ref`` to compile with finite-json-v2.
    A caller may omit it for a v1 bootstrap; the entity IDs and action semantics
    remain explicit in either case. No resolver is claimed for synthetic IDs.
    """
    packages = []
    for key, title, entity, result, action, inputs, required, outputs, sample, aliases, purpose in _PACKAGES:
        effect = "WRITE" if action in {"CREATE", "FREEZE"} else "READ_ONLY"
        utterances = [title, *aliases[:2], "请帮我" + title, "我想了解" + title]
        definition = {
            "name": title, "description": "【模拟业务功能】" + purpose,
            "object_type": f"demo:{entity}", "action": action, "result_type": f"demo:{result}Result",
            "aliases": aliases,
            "input_schema": _schema(inputs, required),
            "output_schema": _schema(outputs),
            "connector": {"kind": "fixture", "version": "2", "workflow_id": key},
            "effect": effect, "requires_confirmation": effect == "WRITE",
            "presentation": {"component": "detail" if not any(v.get("type") == "array" for v in outputs.values()) else "table",
                             "title": title, "field_map": {field: field for field in outputs}},
            "positive_examples": [{"utterance": utterance, "arguments": sample} for utterance in utterances],
            "negative_examples": [{"utterance": "非法参数", "arguments": {"_unexpected": True}}],
            "participant_roles": [{"role": "target", "type": f"demo:{entity}", "required": True, "min_count": 1, "max_count": 1}],
            "relations": [],
            "success_condition": {"field": "status", "operator": "eq", "value": "SUCCEEDED"},
            "compiler_version": "finite-json-v2" if domain_ref else "finite-json-v1",
            "domain_refs": [domain_ref] if domain_ref else [],
        }
        packages.append({"key": key, "intent": title, "entity_type": f"demo:{entity}",
                         "action_key": action, "sample_arguments": sample, "definition": definition,
                         "evaluation_utterances": utterances,
                         "operation_description": "本地模拟处理器；不请求外部接口",
                         "status_semantics": {"SUCCEEDED": "模拟业务已完成", "FAILED": "确定性业务失败",
                                              "UNKNOWN": "超时或回执丢失，写操作先按 operation_id 对账"},
                         "idempotency": "operation_id" if effect == "WRITE" else None,
                         "fixture_only": True})
    return deepcopy(packages)
