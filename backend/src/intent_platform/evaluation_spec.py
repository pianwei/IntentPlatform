"""Versioned evaluation data contracts, independent of API, storage and execution."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

SCORER_VERSION = "gold-exact-v1"
OBSERVATION_NORMALIZATION_VERSION = "runtime-observation-v1"
Label = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
Purpose = Literal["development", "sealed"]
Decision = Literal["READY", "NEEDS_CONFIRMATION", "NEEDS_ARGUMENTS", "AMBIGUOUS",
                   "OUT_OF_SCOPE", "FORBIDDEN", "CANCELLED", "ERROR"]
ExecutionStatus = Literal["NOT_EXECUTED", "SUCCEEDED", "FAILED", "UNKNOWN", "CANCELLED"]


class SpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False,
                              revalidate_instances="always")


def unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label}")


class DatasetRef(SpecModel):
    dataset_id: Label
    version: Label
    purpose: Purpose


class Annotation(SpecModel):
    provenance: Literal["synthetic", "business_reviewed"]
    author: Label
    review_ref: Label | None = None
    frozen_ref: Label | None = None

    @model_validator(mode="after")
    def reviewed_source(self):
        if self.provenance == "business_reviewed" and not self.review_ref:
            raise ValueError("Business-reviewed gold needs its review reference")
        return self


class Action(SpecModel):
    capability_id: Label
    version: Annotated[int, Field(ge=1)]
    arguments: dict[str, JsonValue]


class RouteGold(SpecModel):
    """One complete acceptable combination; alternatives are not a union of tools."""
    actions: list[Action]


class ConfirmationGold(SpecModel):
    required: bool
    accepted: bool

    @model_validator(mode="after")
    def coherent(self):
        if self.accepted and not self.required:
            raise ValueError("An unnecessary confirmation cannot be a gold acceptance")
        return self


class ConfirmationObserved(SpecModel):
    required: bool
    accepted: bool
    binding_valid: bool | None
    executions_before_confirmation: Annotated[int, Field(ge=0)]


class Execution(SpecModel):
    status: ExecutionStatus
    count: Annotated[int, Field(ge=0)]
    result: JsonValue
    displayed_result: JsonValue


class StepGold(SpecModel):
    decision: Decision
    routes: Annotated[list[RouteGold], Field(min_length=1)]
    semantics: dict[str, JsonValue]
    clarification_options: list[Label] = Field(default_factory=list)
    clarification_kind: Literal["arguments", "function_choice", "entity_mention", "entity_choice", "history_choice"] | None = None
    confirmation: ConfirmationGold
    execution: Execution

    @model_validator(mode="after")
    def coherent(self):
        unique(self.clarification_options, "gold clarification option")
        if self.decision in {"READY", "NEEDS_CONFIRMATION", "NEEDS_ARGUMENTS"}:
            if any(not route.actions for route in self.routes):
                raise ValueError("Routable gold needs a complete capability combination")
        elif any(route.actions for route in self.routes):
            raise ValueError("Boundary/cancel/error gold must not select executable capabilities")
        if self.decision == "NEEDS_CONFIRMATION" and not self.confirmation.required:
            raise ValueError("NEEDS_CONFIRMATION requires confirmation gold")
        if self.decision == "AMBIGUOUS" and (len(self.clarification_options) < 2 or self.clarification_kind not in {"function_choice", "entity_choice", "history_choice"}):
            raise ValueError("Ambiguity gold needs distinguishing alternatives")
        if self.clarification_options and self.clarification_kind not in {"function_choice", "entity_choice", "history_choice"}:
            raise ValueError("Clarification alternatives need an explicit choice kind")
        if self.clarification_kind in {"entity_choice", "history_choice"} and (self.decision != "AMBIGUOUS" or len(self.clarification_options) < 2):
            raise ValueError("Entity/history choice gold needs AMBIGUOUS and distinguishing labels")
        if self.clarification_kind and self.decision not in {"AMBIGUOUS", "NEEDS_ARGUMENTS"}:
            raise ValueError("Only clarification decisions carry a question kind")
        if self.execution.status == "NOT_EXECUTED" and (
            self.execution.count or self.execution.result is not None or self.execution.displayed_result is not None
        ):
            raise ValueError("NOT_EXECUTED gold has zero calls and no business result")
        if self.execution.status in {"SUCCEEDED", "FAILED", "UNKNOWN"} and not self.execution.count:
            raise ValueError("Executed gold needs a positive execution count")
        if self.confirmation.required and not self.confirmation.accepted and self.execution.count:
            raise ValueError("Unconfirmed gold must not execute")
        if self.decision in {"NEEDS_ARGUMENTS", "AMBIGUOUS", "OUT_OF_SCOPE", "FORBIDDEN"} and self.execution.count:
            raise ValueError("Clarification or denial gold must not execute")
        return self


class Stimulus(SpecModel):
    kind: Literal["request", "answer", "confirm", "cancel"]
    text: Annotated[str, Field(min_length=1)] | None = None
    values: dict[str, JsonValue] = Field(default_factory=dict)
    option_id: Label | None = None
    option_label: Label | None = None
    confirmed: bool | None = None

    @model_validator(mode="after")
    def coherent(self):
        if self.kind == "request":
            valid = self.text is not None and not self.values and self.option_id is None and self.option_label is None and self.confirmed is None
        elif self.kind == "answer":
            valid = (self.text is None and self.confirmed is None and bool(self.values or self.option_id or self.option_label)
                     and not (self.option_id and self.option_label) and not (self.values and (self.option_id or self.option_label)))
        elif self.kind == "confirm":
            valid = self.confirmed is not None and self.text is None and not self.values and self.option_id is None and self.option_label is None
        else:
            valid = self.text is None and not self.values and self.option_id is None and self.option_label is None and self.confirmed is None
        if not valid:
            raise ValueError("Stimulus fields do not match its kind")
        return self


class ScenarioStep(SpecModel):
    step_id: Label
    stimulus: Stimulus
    expected: StepGold


class Scenario(SpecModel):
    case_id: Label
    category: Literal["S1", "S2", "S3"]
    boundary: Literal["ambiguity", "out_of_scope", "authorization"] | None = None
    primary_capability_id: Label | None = None
    workspace_id: Label
    user_id: Label
    split_group: Label  # Conversation/expression family; assigned by the dataset owner.
    tags: list[Label] = Field(default_factory=list)
    steps: Annotated[list[ScenarioStep], Field(min_length=1)]

    @model_validator(mode="after")
    def coherent(self):
        unique([step.step_id for step in self.steps], "step ID")
        unique(self.tags, "scenario tag")
        if self.category in {"S1", "S2"} and len(self.steps) != 1:
            raise ValueError("S1/S2 are single-request cases; use S3 for multiple steps")
        if self.category == "S3" and len(self.steps) < 2:
            raise ValueError("S3 needs at least two critical steps")
        first = self.steps[0]
        if first.stimulus.kind != "request":
            raise ValueError("A scenario starts with a request")
        if self.category == "S1":
            gold = first.expected
            if not self.primary_capability_id or gold.decision not in {"READY", "NEEDS_CONFIRMATION"}:
                raise ValueError("S1 needs a primary scoring capability and decided gold")
            if any(len(route.actions) != 1 for route in gold.routes):
                raise ValueError("S1 is a single-goal routing set")
            if self.primary_capability_id not in {r.actions[0].capability_id for r in gold.routes}:
                raise ValueError("S1 primary capability must occur in an allowed route")
            if gold.confirmation.required != (gold.decision == "NEEDS_CONFIRMATION"):
                raise ValueError("S1 READY and NEEDS_CONFIRMATION must match confirmation policy")
        if self.category == "S2":
            expected = {"ambiguity": {"AMBIGUOUS"}, "out_of_scope": {"OUT_OF_SCOPE"}, "authorization": {"FORBIDDEN", "OUT_OF_SCOPE"}}
            if self.boundary not in expected or first.expected.decision not in expected[self.boundary]:
                raise ValueError("S2 needs the matching boundary group and decision gold")
        elif self.boundary is not None:
            raise ValueError("Boundary group is only applicable to S2")
        return self


class EvaluationDataset(SpecModel):
    schema_version: Literal["1"] = "1"
    observation_normalization_version: Literal["runtime-observation-v1"] = OBSERVATION_NORMALIZATION_VERSION
    reference: DatasetRef
    scorer_version: Literal["gold-exact-v1"] = SCORER_VERSION
    annotation: Annotation
    scenarios: Annotated[list[Scenario], Field(min_length=1)]

    @model_validator(mode="after")
    def coherent(self):
        unique([case.case_id for case in self.scenarios], "case ID")
        if self.reference.purpose == "sealed" and (
            self.annotation.provenance != "business_reviewed" or not self.annotation.review_ref or not self.annotation.frozen_ref
        ):
            raise ValueError("Sealed datasets require business review and an explicit frozen reference")
        return self


class EvaluationConfiguration(SpecModel):
    purpose: Purpose
    release_id: Label
    model_id: Label
    model_version: Label | None
    model_parameters: dict[str, JsonValue]
    prompt_version: Label
    ontology_version: Label
    function_versions: dict[Label, Annotated[int, Field(ge=1)]]
    connector_versions: dict[Label, Label]
    environment_ref: Label
    resource_mode: Literal["synthetic", "fixture", "real"]
    ablation: Label
    tags: list[Label] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent(self):
        unique(self.tags, "configuration tag")
        if set(self.function_versions) != set(self.connector_versions):
            raise ValueError("Each configured function needs its connector implementation version")
        return self


class ObservedFault(SpecModel):
    stage: Literal["input", "authorization", "model", "validation", "routing", "confirmation",
                   "downstream", "display", "timeout", "infrastructure", "unknown"]
    code: Label
    detail: str
    evidence_ref: Label | None = None


class StepObservation(SpecModel):
    step_id: Label
    routing_outcome: Literal["completed", "timeout", "error", "not_run"]
    decision: Decision | None = None
    validation_completed: bool = False
    actions: list[Action] = Field(default_factory=list)
    semantics: dict[str, JsonValue] | None = None
    clarification_options: list[Label] = Field(default_factory=list)
    clarification_kind: Literal["arguments", "function_choice", "entity_mention", "entity_choice", "history_choice"] | None = None
    confirmation: ConfirmationObserved | None = None
    execution: Execution | None = None
    unauthorized_executions: Annotated[int, Field(ge=0)] | None = None
    leakage_events: Annotated[int, Field(ge=0)] | None = None
    faults: list[ObservedFault] = Field(default_factory=list)


class CaseObservation(SpecModel):
    case_id: Label
    steps: list[StepObservation]

    @model_validator(mode="after")
    def coherent(self):
        unique([step.step_id for step in self.steps], "observed step ID")
        return self


class ObservationBatch(SpecModel):
    schema_version: Literal["1"] = "1"
    observation_normalization_version: Literal["runtime-observation-v1"] = OBSERVATION_NORMALIZATION_VERSION
    dataset: DatasetRef
    scorer_version: Literal["gold-exact-v1"] = SCORER_VERSION
    configuration: EvaluationConfiguration
    trial_id: Label
    cases: list[CaseObservation]

    @model_validator(mode="after")
    def coherent(self):
        unique([case.case_id for case in self.cases], "observed case ID")
        if self.configuration.purpose != self.dataset.purpose:
            raise ValueError("Development and sealed configurations cannot be mixed")
        return self


def validate_dataset_separation(development: EvaluationDataset, sealed: EvaluationDataset) -> None:
    """Check declared expression/conversation families before a controlled handoff."""
    development = EvaluationDataset.model_validate(development.model_dump(mode="python"))
    sealed = EvaluationDataset.model_validate(sealed.model_dump(mode="python"))
    if development.reference.purpose != "development" or sealed.reference.purpose != "sealed":
        raise ValueError("Expected one development dataset and one sealed dataset")
    if {c.split_group for c in development.scenarios} & {c.split_group for c in sealed.scenarios}:
        raise ValueError("Development and sealed expression/conversation families overlap")
    if {c.case_id for c in development.scenarios} & {c.case_id for c in sealed.scenarios}:
        raise ValueError("Development and sealed case IDs overlap")
