"""Compile an agent's selected functions into a finite immutable three-layer view."""
from copy import deepcopy
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from .models import Function, FunctionVersion, BusinessSkill, BusinessSkillVersion, ActionDefinition, ActionDefinitionVersion
from .policies import permitted


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Dependency(Strict):
    function_id: str = Field(min_length=1)
    version: int = Field(ge=1)


class OutputReference(Strict):
    step: str = Field(alias="$step", min_length=1)
    path: str = Field(pattern=r"^data(?:\.[A-Za-z0-9_]+)*$")


class CompositeDefinition(Strict):
    mode: str = Field(default="template", pattern=r"^(template|document)$")
    skill_version_id: str = Field(min_length=1)
    dependencies: list[Dependency] = Field(min_length=1, max_length=8)
    final_output_mapping: dict[str, OutputReference] = Field(default_factory=dict, max_length=40)

    @model_validator(mode="after")
    def unique(self):
        if len({d.function_id for d in self.dependencies}) != len(self.dependencies):
            raise ValueError("Duplicate composite dependency")
        if self.mode == "document" and self.final_output_mapping:
            raise ValueError("Document composite output mapping is generated and validated per run")
        return self


def versioned_capability(db, workspace_id, function_id, version=None, user_id=None):
    function = db.scalar(select(Function).where(Function.id == function_id, Function.workspace_id == workspace_id))
    if not function or function.disabled or not function.latest_version or user_id and not permitted(function, user_id):
        raise ValueError("Function dependency is unavailable or unauthorized: " + function_id)
    row = db.scalar(select(FunctionVersion).where(FunctionVersion.function_id == function_id,
                                                 FunctionVersion.workspace_id == workspace_id,
                                                 FunctionVersion.version == (version or function.latest_version)))
    if not row:
        raise ValueError("Pinned function version is unavailable: " + function_id)
    return {"capability_id": function_id, "version": row.version,
            "definition": deepcopy(row.definition), "compiled": deepcopy(row.compiled)}


def composite_parts(db, workspace_id, definition, user_id=None):
    from .planning import _skill_spec, validate_plan, _result_path_allowed
    spec = CompositeDefinition.model_validate(definition["composite"])
    version = db.scalar(select(BusinessSkillVersion).where(BusinessSkillVersion.id == spec.skill_version_id,
                                                          BusinessSkillVersion.workspace_id == workspace_id))
    skill = db.get(BusinessSkill, version.skill_id) if version else None
    if not skill or skill.disabled:
        raise ValueError("Pinned business Skill is unavailable")
    dependencies = [versioned_capability(db, workspace_id, item.function_id, item.version, user_id) for item in spec.dependencies]
    if any(c["definition"].get("execution_kind", "direct") != "direct" for c in dependencies):
        raise ValueError("Composite functions may reference direct functions only")
    if spec.mode != version.definition.get("mode", "template"):
        raise ValueError("Composite mode differs from the pinned Skill")
    rule = {"skill_id": skill.id, "version_id": version.id, "version": version.version,
            "definition": deepcopy(version.definition)}
    if spec.mode == "document":
        from .planning_api import SkillBody
        from .output_contract import check_output_contract
        SkillBody.model_validate(version.definition)
        check_output_contract(definition)
        if not set(version.definition.get("input_fields", [])) <= set(definition["input_schema"].get("properties", {})):
            raise ValueError("Document Skill inputs must be declared by the composite")
        has_write = any(c["definition"]["effect"] == "WRITE" for c in dependencies)
        if (definition["effect"] == "WRITE") != has_write:
            raise ValueError("Composite effect must match its actual operations")
        return dependencies, rule, None
    by_key = {c["definition"]["connector"].get("workflow_id"): c for c in dependencies}
    raw = _skill_spec(version.definition, {}, by_key)
    plan = validate_plan(raw, dependencies, "")
    if {s.capability_id for s in plan.steps} != {c["capability_id"] for c in dependencies}:
        raise ValueError("Composite dependencies must exactly match the rule steps")
    input_fields = {v["$input"] for s in plan.steps for v in s.arguments.values() if isinstance(v, dict) and "$input" in v}
    if not input_fields <= set(definition["input_schema"].get("properties", {})):
        raise ValueError("Composite rule inputs must be declared by the function")
    required = set(definition.get("output_schema", {}).get("required", []))
    fields = set(definition.get("output_schema", {}).get("properties", {}))
    no_output = definition.get("output_mode") == "none"
    if no_output:
        from .output_contract import check_output_contract
        check_output_contract(definition)
    if (not fields and not no_output) or not required <= set(spec.final_output_mapping) or set(spec.final_output_mapping) - fields:
        raise ValueError("Composite outputs require an explicit finite field mapping")
    caps = {c["capability_id"]: c for c in dependencies}
    steps = {s.step_id: s for s in plan.steps}
    for field, ref in spec.final_output_mapping.items():
        step = steps.get(ref.step)
        if not step:
            raise ValueError("Composite output references an unknown step")
        path = ref.path
        if caps[step.capability_id]["definition"].get("output_mode") == "none":
            raise ValueError("A no-output function cannot supply composite result fields")
        node = caps[step.capability_id]["definition"].get("output_schema", {})
        if step.foreach: node = {"type": "array", "items": node, "maxItems": 5}
        for part in path.split(".")[1:]:
            node = node.get("items", {}) if part.isdigit() and node.get("type") == "array" else node.get("properties", {}).get(part, {})
        if not node or node.get("type") != definition["output_schema"]["properties"][field].get("type"):
            raise ValueError("Composite output field type differs from its source")
        # A batch returns data as an array; whole batch data is supported below.
        if path == "data":
            continue
        if step.foreach:
            parts = path.split(".")
            if len(parts) < 3 or not parts[1].isdigit():
                raise ValueError("Batch output references require a numeric row or whole data")
            path = "data." + ".".join(parts[2:])
        if not _result_path_allowed(caps[step.capability_id]["definition"], path):
            raise ValueError("Composite output references an unapproved field")
    has_write = any(c["definition"]["effect"] == "WRITE" for c in dependencies)
    if (definition["effect"] == "WRITE") != has_write:
        raise ValueError("Composite effect must match its actual operations")
    return dependencies, {"skill_id": skill.id, "version_id": version.id, "version": version.version,
                          "definition": deepcopy(version.definition)}, raw


def compile_agent(db, agent, user_id, authoring_capability=None):
    explicit = [deepcopy(authoring_capability) if authoring_capability and fid == authoring_capability["capability_id"] else
                versioned_capability(db, agent.workspace_id, fid, user_id=user_id) for fid in agent.capability_ids]
    all_caps = {c["capability_id"]: c for c in explicit}
    skills = []
    composites = {}
    for capability in explicit:
        d = capability["definition"]
        if d.get("execution_kind", "direct") == "composite":
            dependencies, rule, plan = composite_parts(db, agent.workspace_id, d, user_id)
            for dep in dependencies:
                prior = all_caps.get(dep["capability_id"])
                if prior and prior["version"] != dep["version"]:
                    raise ValueError("Selected functions pin conflicting dependency versions")
                all_caps[dep["capability_id"]] = dep
            rule["function_id"] = capability["capability_id"]
            skills.append(rule)
            composites[capability["capability_id"]] = {"plan": plan, "skill_version_id": rule["version_id"],
                "mode": d["composite"].get("mode", "template"),
                "dependency_ids": [c["capability_id"] for c in dependencies], "final_output_mapping": d["composite"]["final_output_mapping"]}
    for sid in agent.business_skill_ids:
        skill = db.scalar(select(BusinessSkill).where(BusinessSkill.id == sid, BusinessSkill.workspace_id == agent.workspace_id))
        version = db.scalar(select(BusinessSkillVersion).where(BusinessSkillVersion.skill_id == sid,
                           BusinessSkillVersion.version == skill.latest_version)) if skill and not skill.disabled else None
        if not version:
            raise ValueError("Selected business Skill is unavailable")
        from .planning import _skill_spec, validate_plan
        # Agent-level rules may only use explicitly selected direct functions.
        available = [c for c in explicit if c["definition"].get("execution_kind", "direct") == "direct"]
        if version.definition.get("mode") == "document":
            raise ValueError("Document Skills must be attached to a composite with pinned dependencies")
        validate_plan(_skill_spec(version.definition, {}, {c["definition"]["connector"].get("workflow_id"): c for c in available}), available, "")
        skills.append({"skill_id": sid, "version_id": version.id, "version": version.version, "definition": deepcopy(version.definition)})
    entities, actions = {}, {}
    for c in all_caps.values():
        d = c["definition"]
        source = c["compiled"].get("domain_projection", {}).get("concepts", {})
        types = {d["object_type"], d["result_type"]} | {r["type"] for r in d.get("participant_roles", [])}
        for eid in sorted(types):
            label = source.get(eid, {}).get("label", eid.split(":")[-1])
            row = entities.setdefault(eid, {"id": eid, "label": label, "function_ids": []})
            row["function_ids"].append(c["capability_id"])
        action = db.scalar(select(ActionDefinition).where(ActionDefinition.workspace_id == agent.workspace_id, ActionDefinition.key == d["action"]))
        version = db.scalar(select(ActionDefinitionVersion).where(ActionDefinitionVersion.action_id == action.id,
                             ActionDefinitionVersion.version == action.latest_version)) if action and action.latest_version else None
        ad = version.definition if version else {"name": d["action"], "effect": d["effect"]}
        if ad["effect"] != d["effect"]:
            raise ValueError("Action effect conflicts with the function: " + d["name"])
        row = actions.setdefault(d["action"], {"key": d["action"], "name": ad["name"], "effect": d["effect"], "function_ids": []})
        if row["effect"] != d["effect"]:
            raise ValueError("Conflicting action effects")
        row["function_ids"].append(c["capability_id"])
    from .routing_v3 import domain_projection
    projection = {"version": "agent-projection-v1", "entities": list(entities.values()), "actions": list(actions.values()),
        "domain": domain_projection(list(all_caps.values())),
        "function_semantics": {c["capability_id"]: {key: deepcopy(c["definition"].get(key)) for key in
             ("object_type", "action", "result_type", "participant_roles", "relations", "argument_bindings",
              "parameter_semantics", "trusted_preconditions", "preconditions", "result_bindings")} for c in all_caps.values()},
        "skills": [{"skill_id": s["skill_id"], "version_id": s["version_id"], "name": s["definition"]["name"],
                    **({"function_id": s["function_id"]} if s.get("function_id") else {})} for s in skills],
        "explicit_function_ids": list(agent.capability_ids),
        "dependency_function_ids": sorted(set(all_caps) - set(agent.capability_ids)), "composites": composites}
    return list(all_caps.values()), skills, projection


def public_projection(projection):
    return {key: value for key, value in projection.items() if key != "composites"}
