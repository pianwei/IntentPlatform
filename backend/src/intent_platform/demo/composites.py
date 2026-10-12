"""Reviewed synthetic one-level wrappers; dependencies are precise published versions."""
from copy import deepcopy
from .catalog import _schema, _S


def composite_definition(skill, skill_version_id, dependencies, domain_ref):
    by_key = {c["definition"]["connector"]["workflow_id"]: c for c in dependencies}
    steps = {s["step_id"]: s for s in skill["steps"]}
    leaf_names = {
        "demo.skill.customer_funds": ["balances"],
        "demo.skill.account_cashflow": ["transactions", "summary"],
        "demo.skill.customer_loans": ["balances", "schedules", "overdue"],
        "demo.skill.abnormal_account": ["status", "ticket"],
        "demo.skill.customer_panorama": ["detail", "accounts", "loans", "holdings"],
    }[skill["key"]]
    properties, inputs = {}, {}
    for step in steps.values():
        definition = by_key[step["capability_key"]]["definition"]
        for field, ref in step["arguments"].items():
            if isinstance(ref, dict) and "$input" in ref:
                inputs[ref["$input"]] = deepcopy(definition["input_schema"]["properties"][field])
    for key in leaf_names:
        step = steps[key]
        schema = deepcopy(by_key[step["capability_key"]]["definition"]["output_schema"])
        properties[key] = {"type": "array", "items": schema, "maxItems": 5} if step.get("foreach") else schema
    effect = "WRITE" if any(c["definition"]["effect"] == "WRITE" for c in dependencies) else "READ_ONLY"
    entity = "Account" if skill["key"] in {"demo.skill.account_cashflow", "demo.skill.abnormal_account"} else "Customer"
    return {
        "name": skill["name"], "description": "【模拟组合功能】" + skill["description"],
        "object_type": "demo:" + entity, "action": "CREATE" if effect == "WRITE" else "READ",
        "result_type": "demo:" + entity + "Result", "aliases": skill["triggers"],
        "execution_kind": "composite", "composite": {"skill_version_id": skill_version_id,
            "dependencies": [{"function_id": c["capability_id"], "version": c["version"]} for c in dependencies],
            "final_output_mapping": {key: {"$step": key, "path": "data"} for key in leaf_names}},
        "input_schema": _schema(inputs), "output_schema": _schema(properties, [key for key in leaf_names if not steps[key].get("when")]),
        "connector": {"kind": "fixture", "version": "2", "workflow_id": skill["key"].replace("demo.skill.", "demo.composite.")},
        "effect": effect, "requires_confirmation": effect == "WRITE",
        "presentation": {"component": "detail", "title": skill["name"], "field_map": {k: k for k in leaf_names}},
        "positive_examples": [], "negative_examples": [],
        "participant_roles": [{"role": "target", "type": "demo:" + entity, "required": True, "min_count": 1, "max_count": 1}],
        "relations": [], "success_condition": {"field": "status", "operator": "eq", "value": "SUCCEEDED"},
        "compiler_version": "finite-json-v2", "domain_refs": [domain_ref],
    }
