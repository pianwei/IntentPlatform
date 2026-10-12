"""Finite, versioned planning contract. No generated code or connector targets."""
from typing import Any, Literal
import re
import math
import json
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictPlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanCondition(StrictPlanModel):
    step: str
    path: str
    operator: Literal["eq", "ne", "exists", "empty", "not_empty"]
    value: Any = None


class PlanForeach(StrictPlanModel):
    step: str
    path: str
    item_key: str = Field(min_length=1, max_length=80)


class PlanStepSpec(StrictPlanModel):
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    capability_id: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    parameter_units: dict[str, str] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    when: PlanCondition | None = None
    foreach: PlanForeach | None = None


class ExecutionPlanSpec(StrictPlanModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    steps: list[PlanStepSpec] = Field(min_length=1, max_length=8)
    result_steps: list[str] | None = Field(default=None, min_length=1, max_length=8)
    final_output_mapping: dict[str, dict[str, str]] = Field(default_factory=dict, max_length=40)

    @model_validator(mode="after")
    def ordered_acyclic(self):
        def finite(value, depth=0):
            if depth > 4:
                return False
            if isinstance(value, str):
                return len(value) <= 16000
            if type(value) in {int, float}:
                return math.isfinite(value)
            if type(value) is bool:
                return True
            if isinstance(value, list):
                return len(value) <= 100 and all(finite(v, depth + 1) for v in value)
            if isinstance(value, dict):
                return len(value) <= 40 and all(isinstance(k, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,79}", k) and finite(v, depth + 1) for k, v in value.items())
            return False
        if len(self.inputs) > 20 or any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", key) or not finite(value) for key, value in self.inputs.items()) or len(json.dumps(self.inputs, ensure_ascii=False).encode("utf-8")) > 16000:
            raise ValueError("Plan inputs must be finite approved JSON values")
        seen = set()
        for step in self.steps:
            if step.step_id in seen:
                raise ValueError("Duplicate plan step")
            referenced = set(step.depends_on)
            if step.when:
                referenced.add(step.when.step)
            if step.foreach:
                referenced.add(step.foreach.step)
            for value in step.arguments.values():
                if isinstance(value, dict) and "$step" in value:
                    referenced.add(value["$step"])
            if not referenced <= seen or not referenced <= set(step.depends_on):
                raise ValueError("Plan references require earlier declared dependencies")
            seen.add(step.step_id)
        if self.result_steps is not None and (len(set(self.result_steps)) != len(self.result_steps) or not set(self.result_steps) <= seen):
            raise ValueError("Result steps must uniquely identify existing steps")
        for ref in self.final_output_mapping.values():
            if set(ref) != {"$step", "path"}:
                raise ValueError("Final mapping requires exactly dollar-step and path keys")
            if ref["$step"] not in seen:
                raise ValueError("Final mapping step must name an existing step_id")
            if not re.fullmatch(r"data(?:\.[A-Za-z0-9_]+)*", ref["path"]):
                raise ValueError("Final mapping path must use data or dot-separated approved fields")
        return self


class ModelPlanProposal(StrictPlanModel):
    decision: Literal["SKILL", "PLAN", "NEEDS_INPUT", "OUT_OF_SCOPE"]
    skill_key: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    plan: ExecutionPlanSpec | None = None
    question: str | None = None

    @model_validator(mode="after")
    def coherent(self):
        if self.decision == "SKILL" and (not self.skill_key or self.plan):
            raise ValueError("Skill selection requires only a registered skill key")
        if self.decision == "PLAN" and self.skill_key:
            raise ValueError("PLAN cannot also select a Skill key")
        if self.decision == "PLAN" and not self.plan:
            raise ValueError("Fallback requires a finite plan")
        if self.decision in {"NEEDS_INPUT", "OUT_OF_SCOPE"} and (self.skill_key or self.plan):
            raise ValueError("Non-execution decision cannot contain a plan")
        return self
