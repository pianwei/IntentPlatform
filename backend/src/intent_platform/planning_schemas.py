"""Finite, versioned planning contract. No generated code or connector targets."""
from typing import Any, Literal
import re
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
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    when: PlanCondition | None = None
    foreach: PlanForeach | None = None


class ExecutionPlanSpec(StrictPlanModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    steps: list[PlanStepSpec] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def ordered_acyclic(self):
        if len(self.inputs) > 20 or any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", key)
                                        or not isinstance(value, (str, int, float, bool))
                                        or isinstance(value, str) and len(value) > 500
                                        for key, value in self.inputs.items()):
            raise ValueError("Plan inputs must be finite scalar values")
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
        if self.decision == "PLAN" and (self.skill_key or not self.plan):
            raise ValueError("Fallback requires a finite plan")
        if self.decision in {"NEEDS_INPUT", "OUT_OF_SCOPE"} and (self.skill_key or self.plan):
            raise ValueError("Non-execution decision cannot contain a plan")
        return self
