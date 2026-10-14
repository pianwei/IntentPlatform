"""Finite, explicitly approved synthetic business packages for fourteen scenarios."""
from copy import deepcopy

CATALOG_ID = "zhiyu-v1"
CATALOG_VERSION = 1
SCENARIOS = ["查客户", "问客户", "查产品", "问产品", "AI陪练", "联络地图", "授信政策问答",
             "授信全流程常见问题", "问待办、提醒", "业绩查询", "查看营销任务", "客户拜访", "生成尽调报告", "贷后管理"]

def s(n=200, minimum=1):
    return {"type": "string", "minLength": minimum, "maxLength": n}

def obj(properties, required=None):
    return {"type": "object", "properties": properties, "required": list(properties) if required is None else required,
            "additionalProperties": False}

def rows(properties, limit=100):
    return {"type": "array", "items": obj(properties), "maxItems": limit}

N = {"type": "number"}
I = {"type": "integer"}
B = {"type": "boolean"}
ID = s(40)
CUST = {"customer_id": ID}
CITATIONS = rows({"source_id": ID, "title": s(), "version": s(30), "excerpt": s(1000), "synthetic": B}, 20)
DRAFT = {"customer_id": ID, "title": s(), "markdown": s(12000), "draft_only": B,
         "missing_materials": s(1000, 0), "citations": CITATIONS}

def domain_definition():
    labels = {"Customer": "客户", "Product": "产品", "Manager": "客户经理", "Contact": "业务联系人",
              "Policy": "授信政策", "CreditFAQ": "授信流程问题", "Todo": "待办提醒", "Performance": "业绩",
              "Segment": "客群包", "MarketingTask": "营销任务", "Visit": "客户拜访", "Report": "尽调报告",
              "PostLoan": "贷后事项", "Roleplay": "模拟陪练"}
    rels = [("manages", "Manager", "Customer"), ("usesProduct", "Customer", "Product"),
            ("hasVisit", "Customer", "Visit"), ("hasReport", "Customer", "Report"),
            ("hasPostLoan", "Customer", "PostLoan"), ("hasRoleplay", "Customer", "Roleplay"),
            ("hasTask", "Customer", "MarketingTask"), ("belongsTo", "Customer", "Segment"),
            ("hasTodo", "Manager", "Todo"), ("hasPerformance", "Manager", "Performance"),
            ("governedBy", "Product", "Policy"), ("explainedBy", "Product", "CreditFAQ"),
            ("supportedBy", "Product", "Contact")]
    return {"name": "智驭模拟业务实体", "description": "合成客户与资料目录；不得作为真实客户信息或银行政策。",
            "source": {"reference": CATALOG_ID, "owner": "intent-platform-zhiyu"},
            "concepts": [{"id": "zhiyu:" + key + suffix, "label": label + ("结果" if suffix else ""),
                          "kind": "result" if suffix else "object", "parents": []}
                         for key, label in labels.items() for suffix in ("", "Result")],
            "relations": [{"id": "zhiyu:" + name, "source_type": "zhiyu:" + source, "target_type": "zhiyu:" + target}
                          for name, source, target in rels],
            "units": [{"id": "CNY", "dimension": "currency"}, {"id": "PERCENT", "dimension": "ratio"}], "conversions": []}

def action_definitions():
    return [{"key": "ZY_" + key, "name": name, "description": description, "effect": "WRITE" if key == "CREATE" else "READ_ONLY"}
            for key, name, description in [
                ("LOCATE", "查找", "按条件筛选获准业务实体，名称重复时保留候选。"),
                ("READ", "查询", "读取已绑定实体的获准字段和资料。"),
                ("LIST", "列出", "列出当前模拟角色权限内的关系记录。"),
                ("SUMMARIZE", "汇总", "使用确定性规则统计、比较和排名。"),
                ("DRAFT", "生成草稿", "根据获准事实生成非正式草稿，不提交银行业务。"),
                ("PRACTICE", "陪练", "开启独立模拟对话，不将其内容当成真实业务事实。"),
                ("CREATE", "创建", "确认后创建模拟营销任务，按操作编号幂等。")]]

def _p(key, name, scenario, entity, action, inputs, outputs, sample, aliases, purpose, required=None,
       response_mode="structured", component="detail"):
    return dict(key="zhiyu." + key, name=name, scenario=scenario, entity=entity, action=action,
                inputs=inputs, outputs=outputs, sample=sample, aliases=aliases, purpose=purpose,
                required=list(inputs) if required is None else required,
                response_mode=response_mode, component=component)

_PACKAGES = [
    _p("customer.search", "筛选与定位客户", 1, "Customer", "LOCATE",
       {"query": s(100), "tag": s(80), "city": s(60)},
       {"customers": rows({"customer_id": ID, "name": s(), "city": s(60), "manager_name": s(60), "tags": s(80, 0)})},
       {"tag": "代发高潜"}, ["查客户", "我名下的客户", "客户筛选"], "按名称、编号、城市或业务标签查询模拟客户目录；不是客户画像问答，对象权限由本功能版本配置。", []),
    _p("customer.profile", "客户画像与指标", 2, "Customer", "READ", {**CUST, "metric": s(80)},
       {"customer_id": ID, "name": s(), "industry": s(), "deposit": N, "credit_used": N,
        "payroll_count": I, "currency": s(10), "profile_text": s(1800), "metric": s(80, 0), "metric_value": s(200, 0)},
       {"customer_id": "ZY-C001", "metric": "存款余额"}, ["客户画像", "客户指标", "KYC"], "查询客户基础画像、存款、授信使用或代发人数；未知指标明确返回缺失。", ["customer_id"]),
    _p("customer.cooperation", "查询客户合作情况", 2, "Customer", "READ", CUST,
       {"customer_id": ID, "cooperation_text": s(1800), "products": s(800)},
       {"customer_id": "ZY-C001"}, ["与我行合作", "客户合作情况", "合作产品"], "查询已登记合作产品和合作概要。"),
    _p("customer.feedback", "查询客户营销反馈", 2, "Customer", "LIST", CUST,
       {"feedback": rows({"customer_id": ID, "date": s(10), "content": s(1000)})},
       {"customer_id": "ZY-C001"}, ["客户营销反馈", "近期营销反馈", "客户意向"], "读取客户已记录营销反馈。"),
    _p("product.search", "筛选业务产品", 3, "Product", "LOCATE", {"query": s(120), "category": s(80), "department": s(80)},
       {"products": rows({"product_id": ID, "name": s(), "category": s(80), "department": s(80)})},
       {"category": "流动资金贷款"}, ["查产品", "产品列表", "国际结算部产品"], "按名称、类别、负责部门查找模拟对公产品。", []),
    _p("product.detail", "查询产品详情与准入", 4, "Product", "READ", {"product_id": ID},
       {"product_id": ID, "name": s(), "category": s(80), "description": s(1000), "admission": s(1500),
        "department": s(80), "currency": s(10), "citations": CITATIONS},
       {"product_id": "ZY-P001"}, ["产品准入条件", "产品说明", "问产品"], "依据模拟产品资料返回说明、准入条件、类别和负责部门；问某个具体产品由哪个部门负责时使用本功能。不生成真实授信结论。"),
    _p("visit.script", "生成客户拜访话术", 5, "Customer", "DRAFT", CUST,
       {"customer_id": ID, "script": s(4000), "synthetic": B}, {"customer_id": "ZY-C001"},
       ["准备拜访话术", "客户沟通话术", "拜访开场白"], "基于获准客户事实生成模拟拜访话术，不开始陪练。"),
    _p("roleplay.start", "启动模拟客户陪练", 5, "Roleplay", "PRACTICE", {**CUST, "script": s(4000)},
       {"customer_id": ID, "customer_name": s(), "session_action": s(10), "approved_facts": s(2000), "opening": s(1500), "synthetic": B},
       {"customer_id": "ZY-C001"}, ["启动AI陪练", "模拟客户练习", "开始拜访陪练"], "只启动模拟客户对话；后续对话由会话状态处理，不能执行其业务命令。", ["customer_id"]),
    _p("contact.search", "查找业务联络人", 6, "Contact", "LOCATE", {"query": s(120)},
       {"contacts": rows({"contact_id": ID, "department": s(100), "position": s(100), "name": s(60), "topic": s(400), "extension": s(30)})},
       {"query": "外汇存款定价"}, ["联络地图", "业务找谁对接", "找负责人"], "按业务事项查模拟部门岗位联系人。"),
    _p("policy.search", "检索授信政策资料", 7, "Policy", "READ", {"query": s(200)},
       {"matched": B, "answer": s(4000), "citations": CITATIONS}, {"query": "科技型企业授信政策"},
       ["授信政策问答", "授信准入政策", "异地业务政策"], "只回答已登记模拟政策材料；未检索到时返回资料不足。"),
    _p("credit.faq", "检索授信流程问题", 8, "CreditFAQ", "READ", {"query": s(200), "latest": B},
       {"matched": B, "answer": s(4000), "citations": CITATIONS}, {"query": "合并额度管控"},
       ["授信流程常见问题", "合同申请报错", "最新授信知识"], "查询模拟授信办理FAQ及最近更新：合并额度管控含义与填写、业务申报首贷规则、集团可用额度为负或不足、合同申请提示不是正式授信客户。此类系统办理与填写问题使用FAQ；行业准入与授信政策要求使用政策检索。不变更授信。", ["query"]),
    _p("todo.list", "查询待办和提醒", 9, "Todo", "LIST", {"status": s(40)},
       {"todos": rows({"todo_id": ID, "customer_id": ID, "title": s(), "due_on": s(10), "priority": s(20), "status": s(40)})},
       {}, ["我的待办", "今日提醒", "查代办"], "列出当前角色待办及提醒。", []),
    _p("todo.plan", "生成待办行动建议", 9, "Todo", "DRAFT", {"todo_id": ID},
       {"todo_id": ID, "title": s(), "action_plan": s(2000), "draft_only": B}, {"todo_id": "ZY-TD001"},
       ["待办行动方案", "提醒该怎么处理", "安排待办步骤"], "根据一条获准待办生成建议，不直接完成任务。"),
    _p("performance.summary", "查询本人业绩完成情况", 10, "Performance", "SUMMARIZE", {"period": s(7)},
       {"manager_id": ID, "manager_name": s(60), "period": s(7), "actual": N, "target": N, "completion_percent": N, "currency": s(10)},
       {"period": "2026-09"}, ["我的业绩", "月末考核完成情况", "业绩达标率"], "客户经理查看本人模拟月度业绩；主管使用辖内排名功能。"),
    _p("performance.ranking", "查询辖内客户经理排名", 10, "Performance", "SUMMARIZE", {"period": s(7)},
       {"ranking": rows({"manager_id": ID, "manager_name": s(60), "actual": N, "target": N, "completion_percent": N, "rank": I}), "period": s(7)},
       {"period": "2026-09"}, ["辖内业绩排名", "客户经理排名", "主管业绩汇总"], "仅模拟主管可按完成率查看辖内排名；不允许普通经理查看其他经理业绩。"),
    _p("segment.list", "查看营销客群包", 11, "Segment", "LIST", {"query": s(100)},
       {"segments": rows({"segment_id": ID, "name": s(), "customer_count": I, "description": s(1000)})},
       {}, ["查看客群包", "营销客群", "可用客群包"], "列出模拟目录中的营销客群及关联人数，对象权限由本功能版本配置。", []),
    _p("marketing.list", "查询营销任务与进度", 11, "MarketingTask", "LIST", {"customer_id": ID, "status": s(40)},
       {"tasks": rows({"task_id": ID, "customer_id": ID, "title": s(), "owner_name": s(60), "due_on": s(10), "status": s(40)})},
       {}, ["查看营销任务", "任务完成情况", "我的营销任务"], "查询模拟目录中的营销任务和进度，对象权限由本功能版本配置。", []),
    _p("marketing.create", "创建模拟营销任务", 11, "MarketingTask", "CREATE",
       {**CUST, "title": s(160), "due_on": s(10)},
       {"task_id": ID, "customer_id": ID, "title": s(), "owner_name": s(60), "due_on": s(10), "status": s(40)},
       {"customer_id": "ZY-C001", "title": "代发服务回访", "due_on": "2026-10-20"},
       ["创建营销任务", "发起营销任务", "安排客户营销"], "经明确确认后给客户所属客户经理创建模拟营销任务；不向外部系统提交。"),
    _p("visit.list", "查询客户拜访记录", 12, "Visit", "LIST", CUST,
       {"visits": rows({"visit_id": ID, "customer_id": ID, "visited_on": s(10), "status": s(40), "minutes": s(3000, 0)})},
       {"customer_id": "ZY-C001"}, ["查看拜访记录", "已有拜访纪要", "客户拜访历史"], "查询客户拜访及已保存纪要；不自动录入。"),
    _p("visit.brief", "生成访前一页纸", 12, "Visit", "DRAFT", {**CUST, "profile_text": s(1800), "cooperation_text": s(1800)},
       DRAFT, {"customer_id": "ZY-C001"}, ["访前一页纸", "访前准备材料", "拜访客户准备"], "根据获准画像与合作事实生成拜访草稿，标明资料来源及缺口。", ["customer_id"]),
    _p("visit.minutes", "整理拜访纪要草稿", 12, "Visit", "DRAFT", {**CUST, "transcript": s(4000)},
       DRAFT, {"customer_id": "ZY-C001", "transcript": "客户希望了解代发服务，约定下周提供资料。"},
       ["润色拜访纪要", "整理对话记录", "生成纪要草稿"], "把客户经理提供的记录整理为草稿，保留来源，不当成已登记业务事实。"),
    _p("diligence.facts", "收集尽调获准事实", 13, "Report", "READ", CUST,
       {"customer_id": ID, "facts_text": s(4000), "missing_materials": s(1000), "citations": CITATIONS},
       {"customer_id": "ZY-C001"}, ["尽调材料", "客户尽调事实", "尽调数据准备"], "收集当前模拟资料中已有事实并列出缺失材料，不代替正式尽调。"),
    _p("diligence.draft", "生成尽调报告草稿", 13, "Report", "DRAFT", {**CUST, "facts_text": s(4000)}, DRAFT,
       {"customer_id": "ZY-C001"}, ["生成尽调报告", "尽调报告草稿", "整理客户尽调报告"], "依据获准资料形成可下载草稿，不生成审批意见或授信决定。", ["customer_id"]),
    _p("postloan.tasks", "查询贷后任务", 14, "PostLoan", "LIST", CUST,
       {"tasks": rows({"postloan_id": ID, "customer_id": ID, "title": s(), "due_on": s(10), "status": s(40)})},
       {"customer_id": "ZY-C001"}, ["贷后任务", "待处理贷后事项", "贷后检查安排"], "读取客户已登记的模拟贷后任务。"),
    _p("postloan.checks", "查询贷后检查与风险提示", 14, "PostLoan", "LIST", CUST,
       {"checks": rows({"postloan_id": ID, "customer_id": ID, "checked_on": s(10), "finding": s(1500), "risk_label": s(60)})},
       {"customer_id": "ZY-C001"}, ["贷后检查记录", "贷后风险提示", "最近检查发现"], "返回已有模拟检查发现，不自行判定新的信用风险等级。"),
    _p("postloan.summary", "汇总贷后管理事项", 14, "PostLoan", "SUMMARIZE", CUST,
       {"customer_id": ID, "pending_count": I, "completed_count": I, "summary": s(3000), "citations": CITATIONS},
       {"customer_id": "ZY-C001"}, ["贷后管理概览", "贷后情况汇总", "客户贷后整体情况"], "确定性汇总待办和已有检查结果，不自动变更贷后任务。"),
]

# These handlers read or mutate customer-owned records. Their object ACL is
# deliberately part of each function's fixed version, not the platform router.
_FUNCTION_AUTHORIZED = {
    "zhiyu.customer.search", "zhiyu.customer.profile", "zhiyu.customer.cooperation", "zhiyu.customer.feedback",
    "zhiyu.visit.script", "zhiyu.roleplay.start", "zhiyu.todo.list", "zhiyu.todo.plan",
    "zhiyu.segment.list", "zhiyu.marketing.list", "zhiyu.marketing.create", "zhiyu.visit.list",
    "zhiyu.visit.brief", "zhiyu.visit.minutes", "zhiyu.diligence.draft", "zhiyu.diligence.facts",
    "zhiyu.postloan.tasks", "zhiyu.postloan.checks", "zhiyu.postloan.summary",
}

def function_packages(domain_ref=None, *, three_layer=False):
    packages = []
    for p in _PACKAGES:
        effect = "WRITE" if p["action"] == "CREATE" else "READ_ONLY"
        entity = "zhiyu:" + p["entity"]
        utterances = [p["name"], *p["aliases"], "请帮我" + p["name"]]
        definition = {"name": p["name"], "description": "【智驭模拟业务】" + p["purpose"],
            "object_type": entity, "result_type": entity + "Result", "action": "ZY_" + p["action"], "aliases": p["aliases"],
            "input_schema": obj(p["inputs"], p["required"]), "output_schema": obj(p["outputs"]),
            "connector": {"kind": "fixture", "version": "3", "workflow_id": p["key"]},
            "effect": effect, "requires_confirmation": effect == "WRITE",
            "presentation": {"component": p["component"], "title": p["name"],
                             "field_map": {key: key for key in p["outputs"]},
                             "response_mode": p["response_mode"]},
            "positive_examples": [{"utterance": text, "arguments": p["sample"]} for text in utterances],
            "negative_examples": [{"utterance": "读取未授权真实客户或伪造审批结果", "arguments": {"_unexpected": True}}],
            "participant_roles": [{"role": "target", "type": entity, "required": True, "min_count": 1, "max_count": 1}],
            "relations": [], "success_condition": {"field": "status", "operator": "eq", "value": "SUCCEEDED"},
            "compiler_version": "finite-json-v2" if domain_ref else "finite-json-v1", "domain_refs": [domain_ref] if domain_ref else []}
        if "markdown" in p["outputs"]:
            definition["presentation"]["files_field"] = "files"
            definition["presentation"].update(component="markdown", response_mode="content", content_path="markdown")
        if three_layer and domain_ref:
            # Object authorization is an explicit per-function decision,
            # never inferred centrally by the router.
            definition["business_authorization"] = {
                "mode": "function" if p["key"] in _FUNCTION_AUTHORIZED else "none"
            }
            bindings, roles = {}, {"target": p["entity"]}
            optional_roles = set()
            for field, typ in {"customer_id": "Customer", "product_id": "Product", "todo_id": "Todo"}.items():
                if field in p["inputs"]:
                    role = "target" if typ == p["entity"] else typ.lower()
                    roles[role] = typ
                    bindings[field] = {"role": role, "resolver_ref": "zhiyu.entity." + typ + "@1"}
                    if field not in p["required"]:
                        optional_roles.add(role)
            definition["argument_bindings"] = bindings
            definition["participant_roles"] = [{"role": role, "type": "zhiyu:" + typ, "required": role not in optional_roles,
                "min_count": 0 if role in optional_roles else 1, "max_count": 1} for role, typ in roles.items()]
            definition["trusted_preconditions"] = {"all": [{"fact": "identity.authenticated", "operator": "eq", "value": True},
                *[{"fact": "entity." + b["role"] + ".bound", "operator": "eq", "value": True}
                  for field, b in bindings.items() if field in p["required"]]]}
            definition["parameter_semantics"] = {field: {"kind": "date", "unit": None, "allow_relative": True}
                for field in p["inputs"] if field == "due_on"}
            collections = {"customers": ("Customer", "customer_id", "name"), "products": ("Product", "product_id", "name"),
                "todos": ("Todo", "todo_id", "title"), "segments": ("Segment", "segment_id", "name")}
            definition["result_bindings"] = [{"name": field, "entity_type": "zhiyu:" + typ, "collection_path": field,
                "id_field": ident, "label_field": label, "resolver_ref": "zhiyu.entity." + typ + "@1"}
                for field, (typ, ident, label) in collections.items()
                if p["outputs"].get(field, {}).get("type") == "array"]
        packages.append({"key": p["key"], "intent": p["name"], "scenario_id": p["scenario"],
            "scenario": SCENARIOS[p["scenario"] - 1], "entity_type": entity, "action_key": "ZY_" + p["action"],
            "sample_arguments": p["sample"], "definition": definition, "evaluation_utterances": utterances,
            "operation_description": "本地纯模拟处理器；不访问真实接口或网络。",
            "status_semantics": {"SUCCEEDED": "模拟业务完成", "FAILED": "已知失败", "UNKNOWN": "结果未知，写操作须对账"},
            "idempotency": "operation_id" if effect == "WRITE" else None, "fixture_only": True,
            "catalog_id": CATALOG_ID, "catalog_version": CATALOG_VERSION,
            "real_integration_requirements": ["业务及接口负责人", "权威接口和字段契约", "对象授权规则", "成功/失败及状态查询样例",
                "测试数据与调用限额", "资料版本及引用地址", *( ["确认文案、幂等键和对账接口"] if effect == "WRITE" else [])]})
    return deepcopy(packages)
