"""Pure synthetic execution; caller supplies the trusted persona and persists writes.

All collections and policy texts are invented. No prompt can change a persona.
Fault injection is a test-only argument, never a published function parameter.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import base64
import re

from ..ontology import input_errors
from .catalog import CATALOG_ID, CATALOG_VERSION, function_packages

PERSONAS = [
    {"id": "manager_a", "name": "客户经理甲", "role": "manager", "manager_id": "ZY-M001"},
    {"id": "manager_b", "name": "客户经理乙", "role": "manager", "manager_id": "ZY-M002"},
    {"id": "supervisor", "name": "模拟主管", "role": "supervisor", "manager_id": "ZY-M003"},
]
_PERSONAS = {p["id"]: p for p in PERSONAS}
_DEFINITIONS = {p["key"]: p["definition"] for p in function_packages()}

class BusinessFailure(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code

def persona(persona_id):
    if persona_id not in _PERSONAS:
        raise PermissionError("Registered synthetic persona is required")
    return deepcopy(_PERSONAS[persona_id])

def initial_state():
    customers = [
        ("ZY-C001", "星澜科技", "杭州", "ZY-M001", "科技制造", ["代发高潜", "科创贷款"], 360000000, 800000000, 128),
        ("ZY-C002", "海岚供应链", "宁波", "ZY-M002", "物流", ["供应链核心", "国际结算"], 510000000, 1200000000, 85),
        ("ZY-C003", "远川制造", "杭州", "ZY-M001", "制造", ["代发高潜", "供应链核心"], 180000000, 500000000, 62),
        ("ZY-C004", "远川制造", "绍兴", "ZY-M001", "制造", ["结算"], 90000000, 200000000, 31),
        ("ZY-C005", "云禾商贸", "宁波", "ZY-M002", "商贸", ["结算"], 60000000, 0, 19),
    ]
    return {
        "catalog_id": CATALOG_ID, "catalog_version": CATALOG_VERSION,
        "customers": {cid: {"customer_id": cid, "name": name, "city": city, "manager_id": manager,
            "industry": industry, "tags": tags, "deposit_cents": deposit, "credit_used_cents": credit,
            "payroll_count": payroll} for cid, name, city, manager, industry, tags, deposit, credit, payroll in customers},
        "managers": {"ZY-M001": {"manager_id": "ZY-M001", "name": "客户经理甲"},
                     "ZY-M002": {"manager_id": "ZY-M002", "name": "客户经理乙"}},
        "products": {
            "ZY-P001": {"product_id": "ZY-P001", "name": "星航科创贷", "category": "流动资金贷款", "department": "公司金融部",
                "description": "为模拟科技制造企业提供流动资金周转的演示产品。", "admission": "模拟准入：具备合成科技企业标签、完整用途资料；不代表任何银行实际准入。", "currency": "CNY"},
            "ZY-P002": {"product_id": "ZY-P002", "name": "远帆经营贷", "category": "流动资金贷款", "department": "公司金融部",
                "description": "面向模拟普通经营企业的周转演示产品。", "admission": "模拟准入：经营记录及资金用途材料完整；不含真实银行评分阈值。", "currency": "CNY"},
            "ZY-P003": {"product_id": "ZY-P003", "name": "云汇结算", "category": "结算", "department": "国际结算部",
                "description": "模拟跨境结算服务，仅演示产品查询。", "admission": "模拟准入：提交交易背景资料并登记模拟结算账户。", "currency": "CNY"},
            "ZY-P004": {"product_id": "ZY-P004", "name": "星薪代发", "category": "结算", "department": "交易银行部",
                "description": "模拟企业代发工资服务。", "admission": "模拟准入：企业授权及代发清单完整，不处理真实员工信息。", "currency": "CNY"}},
        "cooperation": {"ZY-C001": ["ZY-P001", "ZY-P004"], "ZY-C002": ["ZY-P002", "ZY-P003"],
                        "ZY-C003": ["ZY-P002"], "ZY-C004": [], "ZY-C005": ["ZY-P003"]},
        "feedback": [{"customer_id": "ZY-C001", "date": "2026-09-25", "content": "模拟反馈：希望了解代发批次和办理材料。"},
                     {"customer_id": "ZY-C002", "date": "2026-09-26", "content": "模拟反馈：关注跨境结算服务。"}],
        "contacts": {
            "ZY-CT001": {"contact_id": "ZY-CT001", "department": "机构业务部", "position": "机构营销岗", "name": "模拟联系人林工", "topic": "政府及国资国企客户营销管理", "extension": "演示-1001"},
            "ZY-CT002": {"contact_id": "ZY-CT002", "department": "风险管理部", "position": "监测排查岗", "name": "模拟联系人周工", "topic": "重点业务风险监测与排查", "extension": "演示-1002"},
            "ZY-CT003": {"contact_id": "ZY-CT003", "department": "国际结算部", "position": "外汇定价岗", "name": "模拟联系人陈工", "topic": "外汇存款定价管理", "extension": "演示-1003"}},
        "policies": {
            "ZY-POL001": {"source_id": "ZY-POL001", "title": "模拟异地业务政策", "keywords": ["异地"], "text": "演示规则：异地业务先登记经营地与资金用途，再由模拟负责人复核资料。此条仅为合成知识，不代表真实银行制度。", "version": "2026.1"},
            "ZY-POL002": {"source_id": "ZY-POL002", "title": "模拟科技型企业授信政策", "keywords": ["科技", "科创"], "text": "演示规则：科技型企业提交合成科技标签、经营与用途资料；缺项标记待补充，不直接生成授信审批意见。", "version": "2026.1"},
            "ZY-POL003": {"source_id": "ZY-POL003", "title": "模拟房地产授信资料规则", "keywords": ["房地产", "经营性物业"], "text": "演示规则：区分房地产开发与经营性物业场景，核对项目用途及收入资料；额度、利率和准入阈值未提供。", "version": "2026.1"},
            "ZY-POL004": {"source_id": "ZY-POL004", "title": "模拟评级准入资料规则", "keywords": ["评级", "评级准入"], "text": "演示规则：评级材料缺失时仅标记资料缺口；本目录没有真实银行评级阈值或审批权限。", "version": "2026.1"}},
        "faqs": {
            "ZY-FAQ001": {"source_id": "ZY-FAQ001", "title": "模拟合并额度管控填写说明", "keywords": ["合并额度", "管控"], "text": "模拟说明：先查看额度所属主体及关联集团，再核对合成额度清单；不可填造不存在的额度。", "version": "2026.1", "updated_on": "2026-09-20"},
            "ZY-FAQ002": {"source_id": "ZY-FAQ002", "title": "模拟业务申报首贷问题", "keywords": ["首贷", "业务申报"], "text": "模拟说明：首贷标记依据已登记历史贷款记录核对；未提供历史材料时标记待核实。", "version": "2026.1", "updated_on": "2026-09-22"},
            "ZY-FAQ003": {"source_id": "ZY-FAQ003", "title": "模拟集团可用额度不足问题", "keywords": ["集团", "负", "不够", "不足"], "text": "模拟说明：核对占用与释放记录并联系额度管理岗；不通过修改数值绕过额度检查。", "version": "2026.1", "updated_on": "2026-09-24"},
            "ZY-FAQ004": {"source_id": "ZY-FAQ004", "title": "模拟合同申请客户状态问题", "keywords": ["合同", "正式授信客户"], "text": "模拟说明：核对客户主体及授信状态；状态未满足时补全资料并等待有权人员处理。", "version": "2026.2", "updated_on": "2026-09-28"}},
        "todos": {
            "ZY-TD001": {"todo_id": "ZY-TD001", "customer_id": "ZY-C001", "manager_id": "ZY-M001", "title": "补充科创客户经营材料", "due_on": "2026-10-12", "priority": "高", "status": "PENDING"},
            "ZY-TD002": {"todo_id": "ZY-TD002", "customer_id": "ZY-C003", "manager_id": "ZY-M001", "title": "预约代发服务回访", "due_on": "2026-10-15", "priority": "中", "status": "PENDING"},
            "ZY-TD003": {"todo_id": "ZY-TD003", "customer_id": "ZY-C002", "manager_id": "ZY-M002", "title": "核对结算需求", "due_on": "2026-10-16", "priority": "中", "status": "DONE"}},
        "performance": [
            {"manager_id": "ZY-M001", "period": "2026-09", "actual_cents": 125000000, "target_cents": 100000000},
            {"manager_id": "ZY-M002", "period": "2026-09", "actual_cents": 90000000, "target_cents": 100000000},
            {"manager_id": "ZY-M001", "period": "2026-08", "actual_cents": 95000000, "target_cents": 100000000},
            {"manager_id": "ZY-M002", "period": "2026-08", "actual_cents": 105000000, "target_cents": 100000000}],
        "segments": {"ZY-S001": {"segment_id": "ZY-S001", "name": "代发高潜客群", "tag": "代发高潜", "description": "已登记代发高潜标签的模拟客户。"},
                     "ZY-S002": {"segment_id": "ZY-S002", "name": "供应链核心客群", "tag": "供应链核心", "description": "已登记供应链核心标签的模拟客户。"}},
        "marketing_tasks": {"ZY-MT001": {"task_id": "ZY-MT001", "customer_id": "ZY-C001", "title": "星澜代发需求跟进", "owner_name": "客户经理甲", "due_on": "2026-10-15", "status": "PENDING"},
                            "ZY-MT002": {"task_id": "ZY-MT002", "customer_id": "ZY-C002", "title": "海岚结算方案沟通", "owner_name": "客户经理乙", "due_on": "2026-10-18", "status": "DONE"}},
        "visits": {"ZY-V001": {"visit_id": "ZY-V001", "customer_id": "ZY-C001", "visited_on": "2026-09-20", "status": "RECORDED", "minutes": "模拟纪要：客户希望优化代发安排；已约定提供材料清单。"},
                   "ZY-V002": {"visit_id": "ZY-V002", "customer_id": "ZY-C002", "visited_on": "2026-09-22", "status": "AWAITING_MINUTES", "minutes": ""}},
        "postloan_tasks": {"ZY-PL001": {"postloan_id": "ZY-PL001", "customer_id": "ZY-C001", "title": "季度经营材料核验", "due_on": "2026-10-20", "status": "PENDING"},
                           "ZY-PL002": {"postloan_id": "ZY-PL002", "customer_id": "ZY-C001", "title": "资金用途检查", "due_on": "2026-09-15", "status": "DONE"},
                           "ZY-PL003": {"postloan_id": "ZY-PL003", "customer_id": "ZY-C002", "title": "结算回款核验", "due_on": "2026-10-22", "status": "PENDING"}},
        "postloan_checks": [{"postloan_id": "ZY-PL002", "customer_id": "ZY-C001", "checked_on": "2026-09-15", "finding": "模拟检查：用途资料已核对，季度财务材料待更新。", "risk_label": "资料待补充"}],
        "_operations": {},
    }

def _required(state, collection, identifier):
    value = state[collection].get(identifier)
    if value is None:
        raise BusinessFailure("NOT_FOUND")
    return value

def authorized_customers(persona_id, state):
    profile = persona(persona_id)
    return {cid for cid, row in state["customers"].items()
            if profile["role"] == "supervisor" or row["manager_id"] == profile["manager_id"]}

def authorize_operation(key, args, state, persona_id):
    profile = authorize_function_role(key, args, state, persona_id)
    allowed = authorized_customers(persona_id, state)
    owners = [args["customer_id"]] if args.get("customer_id") else []
    for field, collection in (("todo_id", "todos"), ("task_id", "marketing_tasks"), ("visit_id", "visits"), ("postloan_id", "postloan_tasks")):
        row = state[collection].get(args.get(field))
        if row:
            owners.append(row["customer_id"])
    if any(owner not in allowed for owner in owners):
        raise PermissionError("Synthetic object authorization denied")
    return allowed

def _check_catalog_state(state):
    if state.get("catalog_id") != CATALOG_ID or state.get("catalog_version") != CATALOG_VERSION:
        raise PermissionError("Synthetic catalog version is unavailable")

def authorize_function_role(key, args, state, persona_id):
    """Apply function-specific role gates without a platform object ACL."""
    profile = persona(persona_id)
    _check_catalog_state(state)
    if key == "zhiyu.performance.ranking" and profile["role"] != "supervisor":
        raise PermissionError("Synthetic supervisor role is required")
    if key == "zhiyu.performance.summary" and profile["role"] != "manager":
        raise PermissionError("Use the authorized supervisor ranking capability")
    return profile

def _business_authorization_mode(value):
    if value not in {"function", "none"}:
        raise ValueError("An explicit function authorization mode is required")
    return value

def _money(cents):
    return float(Decimal(cents) / 100)

def _percent(actual, target):
    if not target:
        raise BusinessFailure("PERFORMANCE_TARGET_MISSING")
    return float((Decimal(actual) * 100 / Decimal(target)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def _citation(identifier, title, text, version="2026.1"):
    return {"source_id": identifier, "title": title, "version": version, "excerpt": text[:1000], "synthetic": True}

def _profile(state, cid):
    row = _required(state, "customers", cid)
    summary = f"【模拟资料】{row['name']}，{row['city']}，{row['industry']}；存款余额{_money(row['deposit_cents']):.2f}元，授信使用{_money(row['credit_used_cents']):.2f}元，代发人数{row['payroll_count']}人。"
    return row, summary

def _cooperation(state, cid):
    _required(state, "customers", cid)
    names = [state["products"][pid]["name"] for pid in state["cooperation"].get(cid, [])]
    value = "、".join(names) if names else "暂无已登记合作产品"
    return {"customer_id": cid, "cooperation_text": "【模拟合作记录】" + value, "products": value}

def _facts(state, cid):
    _, profile = _profile(state, cid)
    text = profile + "\n" + _cooperation(state, cid)["cooperation_text"]
    return {"customer_id": cid, "facts_text": text,
            "missing_materials": "真实营业资质、经核验财务报表、经营流水、担保材料均未提供。",
            "citations": [_citation(cid, "模拟客户画像及合作记录", text)]}

def _draft(state, args, kind):
    cid = args["customer_id"]
    row, profile = _profile(state, cid)
    cooperation = _cooperation(state, cid)["cooperation_text"]
    facts = _facts(state, cid)
    for name, expected in (("profile_text", profile), ("cooperation_text", cooperation), ("facts_text", facts["facts_text"])):
        if name in args and args[name] != expected:
            raise BusinessFailure("SOURCE_FACTS_MISMATCH")
    if kind == "brief":
        title = row["name"] + "访前一页纸（模拟草稿）"
        body = (f"## 客户事实\n{profile}\n{cooperation}\n## 沟通事项\n核对近期经营变化与服务需求；确认下次资料交接安排。"
                f"\n\n## 缺失材料\n{facts['missing_materials']}")
    elif kind == "minutes":
        title = row["name"] + "拜访纪要（模拟草稿）"
        body = "## 客户经理提供的记录（未经核实）\n" + args["transcript"] + "\n## 待复核\n拜访时间、参与人员、约定事项及责任人待客户经理确认。"
    else:
        title = row["name"] + "尽调报告（模拟草稿）"
        body = "## 已有资料\n" + facts["facts_text"] + "\n## 缺失材料\n" + facts["missing_materials"] + "\n## 结论边界\n本草稿仅整理资料，不形成真实尽调结论、审批意见或授信决定。"
    if kind in {"brief", "diligence"}:
        sources = "\n".join(f"- {item['title']}（版本 {item['version']}，来源编号 {item['source_id']}，模拟资料）"
                            for item in facts["citations"])
        if sources:
            body += "\n\n## 资料来源\n" + sources
    markdown = "# " + title + "\n\n" + body
    return {"customer_id": cid, "title": title, "markdown": markdown,
            "draft_only": True, "missing_materials": facts["missing_materials"], "citations": facts["citations"],
            "files": [{"filename": cid + "-" + kind + "-draft.md", "media_type": "text/plain",
                       "content_base64": base64.b64encode(markdown.encode("utf-8")).decode("ascii")}]}

def _knowledge(collection, query, latest=False):
    rows = list(collection.values())
    query = query.casefold()
    if latest:
        rows = sorted(rows, key=lambda r: r.get("updated_on", ""), reverse=True)[:3]
    else:
        rows = [row for row in rows if any(word.casefold() in query for word in row["keywords"])
                or query in row["title"].casefold()]
    def content(row):
        if latest:
            return f"最近更新：{row.get('updated_on', '未记录')}，{row['title']}（版本{row['version']}）：{row['text']}"
        return row['text']
    return {"matched": bool(rows), "answer": "\n".join(content(row) for row in rows) if rows else "未找到对应模拟资料，不能据此推断真实银行规则。",
            "citations": [_citation(row["source_id"], row["title"], row["text"], row["version"]) for row in rows]}

def _registered_filter(value, registered, suffix):
    """Normalize only an exact registered value followed by its UI noun suffix."""
    value = value.strip().casefold()
    known = {item.casefold() for item in registered}
    if value in known:
        return value
    if suffix and value.endswith(suffix) and value[:-len(suffix)] in known:
        return value[:-len(suffix)]
    return value

def _read(key, args, state, persona_id, authorization_mode="function"):
    authorization_mode = _business_authorization_mode(authorization_mode)
    allowed = (authorized_customers(persona_id, state) if authorization_mode == "function"
               else set(state["customers"]))
    if key == "zhiyu.customer.search":
        query, tag, city = (args.get(k, "").strip().casefold() for k in ("query", "tag", "city"))
        tag = _registered_filter(tag, {t for row in state["customers"].values() for t in row["tags"]}, "客户")
        return {"customers": [{"customer_id": r["customer_id"], "name": r["name"], "city": r["city"],
            "manager_name": state["managers"][r["manager_id"]]["name"], "tags": "、".join(r["tags"])}
            for r in state["customers"].values() if r["customer_id"] in allowed
            and (not query or query in (r["name"] + r["customer_id"] + "、".join(r["tags"])).casefold())
            and (not tag or tag in {t.casefold() for t in r["tags"]}) and (not city or city == r["city"].casefold())]}
    if key == "zhiyu.customer.profile":
        r, summary = _profile(state, args["customer_id"])
        metrics = {"存款余额": f"{_money(r['deposit_cents']):.2f}元", "授信使用": f"{_money(r['credit_used_cents']):.2f}元", "代发人数": str(r["payroll_count"]) + "人"}
        return {"customer_id": r["customer_id"], "name": r["name"], "industry": r["industry"],
            "deposit": _money(r["deposit_cents"]), "credit_used": _money(r["credit_used_cents"]), "payroll_count": r["payroll_count"],
            "currency": "CNY", "profile_text": summary, "metric": args.get("metric", ""),
            "metric_value": metrics.get(args.get("metric", ""), "未提供该指标" if args.get("metric") else "")}
    if key == "zhiyu.customer.cooperation":
        return _cooperation(state, args["customer_id"])
    if key == "zhiyu.customer.feedback":
        _required(state, "customers", args["customer_id"])
        return {"feedback": [deepcopy(r) for r in state["feedback"] if r["customer_id"] == args["customer_id"]]}
    if key == "zhiyu.product.search":
        filters = dict(args)
        if "category" in filters:
            filters["category"] = _registered_filter(filters["category"], {r["category"] for r in state["products"].values()}, "类")
        return {"products": [{k: r[k] for k in ("product_id", "name", "category", "department")} for r in state["products"].values()
            if all(not filters.get(k) or (filters[k].casefold() == r[k].casefold() if k == "category" else
                   filters[k].casefold() in (r["name"] + r["description"] + r["category"] + r["department"] if k == "query" else r[k]).casefold())
                   for k in ("query", "category", "department"))]}
    if key == "zhiyu.product.detail":
        r = deepcopy(_required(state, "products", args["product_id"]))
        return {**r, "citations": [_citation(r["product_id"], r["name"] + "模拟产品说明", r["description"] + r["admission"])]}
    if key == "zhiyu.visit.script":
        r, summary = _profile(state, args["customer_id"])
        return {"customer_id": r["customer_id"], "synthetic": True,
                "script": f"【模拟拜访话术】您好，今天想了解贵公司的近期经营和服务需求。已登记信息：{summary} 请您核实这些信息，并介绍当前最希望改进的服务事项。结束时确认后续材料与沟通时间。"}
    if key == "zhiyu.roleplay.start":
        r, summary = _profile(state, args["customer_id"])
        return {"customer_id": r["customer_id"], "customer_name": r["name"], "session_action": "START", "approved_facts": summary,
                "opening": f"【模拟陪练】我是{r['name']}的模拟联系人。我们想先了解服务方案和需要准备的材料，请开始你的拜访沟通。", "synthetic": True}
    if key == "zhiyu.contact.search":
        q = args["query"].casefold()
        return {"contacts": [deepcopy(r) for r in state["contacts"].values()
            if q in (r["topic"] + r["department"] + r["position"]).casefold()
            or any(token in q for token in ("外汇", "国资", "风险") if token in r["topic"])]}
    if key == "zhiyu.policy.search":
        return _knowledge(state["policies"], args["query"])
    if key == "zhiyu.credit.faq":
        return _knowledge(state["faqs"], args["query"], args.get("latest", False))
    if key == "zhiyu.todo.list":
        return {"todos": [{k: v for k, v in r.items() if k != "manager_id"} for r in state["todos"].values()
            if r["customer_id"] in allowed and (not args.get("status") or args["status"] == r["status"])]}
    if key == "zhiyu.todo.plan":
        r = _required(state, "todos", args["todo_id"])
        return {"todo_id": r["todo_id"], "title": r["title"], "action_plan": f"1. 核对事项：{r['title']}。\n2. 联系获准客户补齐材料。\n3. 在{r['due_on']}前复核并记录处理结果。", "draft_only": True}
    if key.startswith("zhiyu.performance."):
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", args["period"]):
            raise BusinessFailure("INVALID_PERIOD")
        found = [r for r in state["performance"] if r["period"] == args["period"]]
        values = [{"manager_id": r["manager_id"], "manager_name": state["managers"][r["manager_id"]]["name"],
            "actual": _money(r["actual_cents"]), "target": _money(r["target_cents"]),
            "completion_percent": _percent(r["actual_cents"], r["target_cents"])} for r in found]
        if key == "zhiyu.performance.summary":
            mine = next((r for r in values if r["manager_id"] == persona(persona_id)["manager_id"]), None)
            if not mine:
                raise BusinessFailure("PERFORMANCE_NOT_RECORDED")
            return {**mine, "period": args["period"], "currency": "CNY"}
        ordered = sorted(values, key=lambda r: (-r["completion_percent"], r["manager_id"]))
        return {"period": args["period"], "ranking": [{**r, "rank": i + 1} for i, r in enumerate(ordered)]}
    if key == "zhiyu.segment.list":
        result = []
        for r in state["segments"].values():
            count = sum(c["customer_id"] in allowed and r["tag"] in c["tags"] for c in state["customers"].values())
            if count and (not args.get("query") or args["query"] in r["name"]):
                result.append({k: r[k] for k in ("segment_id", "name", "description")} | {"customer_count": count})
        return {"segments": result}
    if key == "zhiyu.marketing.list":
        return {"tasks": [deepcopy(r) for r in state["marketing_tasks"].values() if r["customer_id"] in allowed
            and (not args.get("customer_id") or r["customer_id"] == args["customer_id"])
            and (not args.get("status") or r["status"] == args["status"])]}
    if key == "zhiyu.visit.list":
        _required(state, "customers", args["customer_id"])
        return {"visits": [deepcopy(r) for r in state["visits"].values() if r["customer_id"] == args["customer_id"]]}
    if key in {"zhiyu.visit.brief", "zhiyu.visit.minutes", "zhiyu.diligence.draft"}:
        return _draft(state, args, key.rsplit(".", 1)[-1])
    if key == "zhiyu.diligence.facts":
        return _facts(state, args["customer_id"])
    if key == "zhiyu.postloan.tasks":
        return {"tasks": [deepcopy(r) for r in state["postloan_tasks"].values() if r["customer_id"] == args["customer_id"]]}
    if key == "zhiyu.postloan.checks":
        return {"checks": [deepcopy(r) for r in state["postloan_checks"] if r["customer_id"] == args["customer_id"]]}
    if key == "zhiyu.postloan.summary":
        cid = args["customer_id"]
        tasks = [r for r in state["postloan_tasks"].values() if r["customer_id"] == cid]
        checks = [r for r in state["postloan_checks"] if r["customer_id"] == cid]
        pending = sum(r["status"] == "PENDING" for r in tasks)
        completed = sum(r["status"] == "DONE" for r in tasks)
        summary = f"【模拟贷后记录】待处理{pending}项，已完成{completed}项。" + ("；".join(r["finding"] for r in checks) or "暂无已登记检查发现。")
        return {"customer_id": cid, "pending_count": pending, "completed_count": completed, "summary": summary,
            "citations": [_citation("ZY-PL-" + cid, "模拟贷后任务与检查记录", summary)]}
    raise BusinessFailure("UNKNOWN_OPERATION")

def _write(key, args, state):
    if key != "zhiyu.marketing.create":
        raise BusinessFailure("UNKNOWN_OPERATION")
    try:
        if date.fromisoformat(args["due_on"]).isoformat() != args["due_on"]:
            raise ValueError()
    except ValueError:
        raise BusinessFailure("INVALID_DATE") from None
    customer = _required(state, "customers", args["customer_id"])
    # Hybrid evaluation preserves source task identifiers under a namespace
    # such as ``SWB-ZY-MT001-L01``. Only canonical simulator IDs participate
    # in the local sequence; source IDs must remain untouched and must not
    # make a confirmed simulator write UNKNOWN while parsing a numeric suffix.
    numbers = [int(key[5:]) for key in state["marketing_tasks"]
               if key.startswith("ZY-MT") and key[5:].isascii() and key[5:].isdigit()]
    identifier = "ZY-MT" + str(max(numbers, default=0) + 1).zfill(3)
    row = {"task_id": identifier, **args, "owner_name": state["managers"][customer["manager_id"]]["name"], "status": "PENDING"}
    state["marketing_tasks"][identifier] = row
    return deepcopy(row)

def _envelope(status, operation_id, data=None, error_code=None):
    result = {"status": status, "operation_id": operation_id, "data": data or {}, "fixture_mode": True,
              "catalog_id": CATALOG_ID, "catalog_version": CATALOG_VERSION}
    if error_code:
        result["error_code"] = error_code
    return result

def execute(operation_key, arguments, state, operation_id, fault=None, *, persona_id, authorization_mode="function"):
    """Execute after caller's confirmation gate. A write never mutates input state."""
    if not isinstance(arguments, dict) or not isinstance(state, dict) or not operation_id:
        raise ValueError("Valid arguments, state and operation ID required")
    persona(persona_id)
    authorization_mode = _business_authorization_mode(authorization_mode)
    definition = _DEFINITIONS.get(operation_key)
    if not definition:
        return _envelope("FAILED", operation_id, error_code="UNKNOWN_OPERATION"), state
    if input_errors(definition["input_schema"], arguments):
        return _envelope("FAILED", operation_id, error_code="INVALID_ARGUMENTS"), state
    if authorization_mode == "function":
        authorize_operation(operation_key, arguments, state, persona_id)
    else:
        authorize_function_role(operation_key, arguments, state, persona_id)
    if fault not in {None, "definite_failure", "timeout_before_commit", "unknown_after_commit"}:
        raise ValueError("Unknown synthetic test fault")
    write = definition["effect"] == "WRITE"
    old = state.get("_operations", {}).get(operation_id) if write else None
    if old:
        if old["operation_key"] != operation_key or old["arguments"] != arguments or old["persona_id"] != persona_id:
            return _envelope("FAILED", operation_id, error_code="OPERATION_ID_CONFLICT"), state
        return _envelope("SUCCEEDED", operation_id, deepcopy(old["data"])), state
    if fault in {"definite_failure", "timeout_before_commit"}:
        return _envelope("FAILED" if fault == "definite_failure" else "UNKNOWN", operation_id,
                         error_code="SIMULATED_FAILURE" if fault == "definite_failure" else "SIMULATED_TIMEOUT"), state
    changed = deepcopy(state) if write else state
    try:
        data = _write(operation_key, arguments, changed) if write else _read(operation_key, arguments, state, persona_id, authorization_mode)
    except BusinessFailure as exc:
        return _envelope("FAILED", operation_id, error_code=exc.code), state
    if write:
        changed.setdefault("_operations", {})[operation_id] = {"operation_key": operation_key, "arguments": deepcopy(arguments),
            "data": deepcopy(data), "persona_id": persona_id}
    if fault == "unknown_after_commit":
        return _envelope("UNKNOWN", operation_id, error_code="SIMULATED_RESPONSE_LOSS"), changed
    return _envelope("SUCCEEDED", operation_id, data), changed

def reconcile(operation_id, state, *, persona_id, authorization_mode="function"):
    persona(persona_id)
    authorization_mode = _business_authorization_mode(authorization_mode)
    record = state.get("_operations", {}).get(operation_id)
    if not record:
        return _envelope("UNKNOWN", operation_id, error_code="OPERATION_NOT_COMMITTED")
    if record["persona_id"] != persona_id:
        raise PermissionError("Synthetic operation belongs to another persona")
    if authorization_mode == "function":
        authorize_operation(record["operation_key"], record["arguments"], state, persona_id)
    else:
        authorize_function_role(record["operation_key"], record["arguments"], state, persona_id)
    return _envelope("SUCCEEDED", operation_id, deepcopy(record["data"]))

def resolve(entity_type, state, persona_id, query, selected_id=None, *, authorization_mode=None):
    """Resolve approved mock objects; object ACL is optional and function-owned."""
    persona(persona_id)
    if authorization_mode not in {None, "function", "none"}:
        raise ValueError("Pinned function authorization mode is unavailable")
    _check_catalog_state(state)
    collections = {"Customer": ("customers", "customer_id"), "Product": ("products", "product_id"),
        "Todo": ("todos", "todo_id"), "Segment": ("segments", "segment_id"), "MarketingTask": ("marketing_tasks", "task_id"),
        "Visit": ("visits", "visit_id"), "PostLoan": ("postloan_tasks", "postloan_id"), "Contact": ("contacts", "contact_id")}
    typ = entity_type.split(":")[-1]
    if typ not in collections or not entity_type.startswith("zhiyu:"):
        return []
    collection, field = collections[typ]
    # ``None`` means legacy and keeps the old manager-object boundary. Any
    # explicit new policy binds first; object decisions happen in the function.
    allowed = (authorized_customers(persona_id, state) if authorization_mode is None
               else set(state["customers"]))
    output = []
    for row in state[collection].values():
        if row.get("customer_id") and row["customer_id"] not in allowed:
            continue
        if typ == "Segment" and not any(c["customer_id"] in allowed and row["tag"] in c["tags"] for c in state["customers"].values()):
            continue
        label = " · ".join(str(row[k]) for k in ("name", "title", "city", field) if row.get(k))
        if selected_id and row[field] != selected_id:
            continue
        if not selected_id and str(query).casefold() not in label.casefold():
            continue
        output.append({"id": row[field], "label": label, "type": entity_type, "authorized": True})
    return output[:20]
