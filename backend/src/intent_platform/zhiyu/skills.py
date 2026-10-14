"""Six finite, versionable business rules; no text-trigger dispatcher."""
from copy import deepcopy
from .catalog import obj

def _input(name):
    return {"$input": name}

def _step(step, path):
    return {"$step": step, "path": "data." + path}

def _call(step, key, arguments, *deps):
    return {"step_id": step, "capability_key": "zhiyu." + key, "arguments": arguments, "depends_on": list(deps)}

def _customer():
    return _call("customer", "customer.search", {"query": _input("customer_query")})

def _cid():
    # Names remain unresolved scalar inputs until each direct function's trusted
    # resolver binds one authorized object. Never index an ambiguous search list.
    return _input("customer_id")

_SKILLS = [
    {"key": "zhiyu.skill.product_comparison", "name": "产品对比", "entity": "Product", "version": 1,
     "description": "仅在明确要求比较两个产品时适用；单项产品说明不使用此规则。两个名称须先定位和消歧，再按资料逐项比较。",
     "triggers": ["对比两个产品", "比较产品差异", "产品对比"],
     "input_fields": ["product_a", "product_b"], "result_steps": ["detail_a", "detail_b"],
     "steps": [_call("detail_a", "product.detail", {"product_id": _input("product_a")}),
               _call("detail_b", "product.detail", {"product_id": _input("product_b")})]},
    {"key": "zhiyu.skill.visit_preparation", "name": "访前准备", "entity": "Customer", "version": 1,
     "description": "仅在需要客户访前一页纸或综合准备时适用；单查画像、指标、合作或记录不匹配。定位后合并获准画像和合作记录生成草稿。",
     "triggers": ["访前一页纸", "访前准备", "准备拜访材料"], "input_fields": ["customer_id"], "result_steps": ["brief"],
     "steps": [_call("profile", "customer.profile", {"customer_id": _cid()}),
               _call("cooperation", "customer.cooperation", {"customer_id": _cid()}),
               _call("brief", "visit.brief", {"customer_id": _cid(), "profile_text": _step("profile", "profile_text"),
                    "cooperation_text": _step("cooperation", "cooperation_text")}, "profile", "cooperation")]},
    {"key": "zhiyu.skill.visit_roleplay", "name": "拜访准备并开启陪练", "entity": "Customer", "version": 1,
     "description": "仅在要求拜访话术并启动模拟客户陪练时适用。只要话术或只要历史拜访记录均不适用。",
     "triggers": ["拜访话术然后启动陪练", "准备并开启陪练", "拜访准备加陪练"], "input_fields": ["customer_id"], "result_steps": ["script", "roleplay"],
     "steps": [_call("script", "visit.script", {"customer_id": _cid()}),
               _call("roleplay", "roleplay.start", {"customer_id": _cid(), "script": _step("script", "script")}, "script")]},
    {"key": "zhiyu.skill.marketing_creation", "name": "营销任务创建", "entity": "Customer", "version": 1,
     "description": "仅明确要求创建一个客户营销任务时适用，查询客群或任务进度不适用。确认对象、标题及截止日期后才能执行最后的写步骤。",
     "triggers": ["创建客户营销任务", "发起营销任务", "安排营销任务"], "input_fields": ["customer_id", "title", "due_on"], "result_steps": ["task"],
     "steps": [_call("task", "marketing.create", {"customer_id": _cid(), "title": _input("title"), "due_on": _input("due_on")})]},
    {"key": "zhiyu.skill.diligence_report", "name": "尽调报告生成", "entity": "Customer", "version": 1,
     "description": "仅要求生成客户尽调报告草稿时适用；单查客户资料不匹配。定位、收集事实、列出缺口、生成草稿，不做审批决定。",
     "triggers": ["生成尽调报告", "客户尽调报告草稿", "整理尽调报告"], "input_fields": ["customer_id"], "result_steps": ["draft"],
     "steps": [_call("facts", "diligence.facts", {"customer_id": _cid()}),
               _call("draft", "diligence.draft", {"customer_id": _cid(), "facts_text": _step("facts", "facts_text")}, "facts")]},
    {"key": "zhiyu.skill.postloan_overview", "name": "贷后概览", "entity": "Customer", "version": 1,
     "description": "仅用户要求客户贷后整体情况或任务与检查汇总时适用；单查任务或检查记录不匹配。不变更贷后业务。",
     "triggers": ["贷后管理概览", "贷后整体情况", "贷后任务和检查汇总"], "input_fields": ["customer_id"], "result_steps": ["tasks", "checks", "summary"],
     "steps": [_call("tasks", "postloan.tasks", {"customer_id": _cid()}),
               _call("checks", "postloan.checks", {"customer_id": _cid()}),
               _call("summary", "postloan.summary", {"customer_id": _cid()}, "tasks", "checks")]},
]

def business_skill_templates():
    return deepcopy([{**{k: v for k, v in skill.items() if k not in {"entity", "result_steps"}},
                      "applicable_entities": ["zhiyu:" + skill["entity"]], "applicable_actions": []}
                     for skill in _SKILLS])

def composite_definition(skill, skill_version_id, dependencies, domain_ref):
    metadata = next(s for s in _SKILLS if s["key"] == skill["key"])
    by_key = {c["definition"]["connector"]["workflow_id"]: c for c in dependencies}
    steps = {s["step_id"]: s for s in skill["steps"]}
    required_dependencies = {step["capability_key"] for step in skill["steps"]}
    if set(by_key) != required_dependencies:
        raise ValueError("Composite requires exactly its published direct dependencies")
    inputs = {}
    for step in skill["steps"]:
        for field, ref in step["arguments"].items():
            if isinstance(ref, dict) and "$input" in ref:
                inputs[ref["$input"]] = deepcopy(by_key[step["capability_key"]]["definition"]["input_schema"]["properties"][field])
    outputs = {name: deepcopy(by_key[steps[name]["capability_key"]]["definition"]["output_schema"]) for name in metadata["result_steps"]}
    from ..result_presentation import markdown_paths
    nested_markdown = markdown_paths(obj(outputs))
    presentation = {"component": "markdown" if len(nested_markdown) == 1 else "detail",
                    "title": skill["name"], "field_map": {name: name for name in outputs},
                    "response_mode": "content" if len(nested_markdown) == 1 else "summary"}
    if len(nested_markdown) == 1:
        presentation["content_path"] = nested_markdown[0]
    effect = "WRITE" if any(c["definition"]["effect"] == "WRITE" for c in dependencies) else "READ_ONLY"
    entity = "zhiyu:" + metadata["entity"]
    return {"name": skill["name"], "description": "【智驭模拟组合】" + skill["description"],
        "object_type": entity, "result_type": entity + "Result", "action": "ZY_CREATE" if effect == "WRITE" else "ZY_READ", "aliases": skill["triggers"],
        "execution_kind": "composite", "composite": {"skill_version_id": skill_version_id,
            "dependencies": [{"function_id": c["capability_id"], "version": c["version"]} for c in dependencies],
            "final_output_mapping": {name: {"$step": name, "path": "data"} for name in metadata["result_steps"]}},
        "input_schema": obj(inputs, sorted(inputs)), "output_schema": obj(outputs, sorted(outputs)),
        "business_authorization": {"mode": "none"},
        "connector": {"kind": "fixture", "version": "3", "workflow_id": skill["key"].replace("zhiyu.skill.", "zhiyu.composite.")},
        "effect": effect, "requires_confirmation": effect == "WRITE",
        "presentation": presentation,
        "positive_examples": [], "negative_examples": [],
        "participant_roles": [{"role": "target", "type": entity, "required": True, "min_count": 1, "max_count": 1}],
        "relations": [], "success_condition": {"field": "status", "operator": "eq", "value": "SUCCEEDED"},
        "compiler_version": "finite-json-v2", "domain_refs": [domain_ref]}
