"""Read-only shadow route checks. This module cannot create calls or dispatch work."""
import copy
import time
from typing import Literal
import httpx
from pydantic import Field, model_validator
from .schemas import StrictModel
from .context_binding import bind_arguments, BindingDenied
from .semantic_v2 import semantic_errors, normalize_parameters, truth
from .ontology import input_errors
from .ports import condition
from .resolvers import ResolverUnavailable
from .routing_v3 import POLICIES, route_v3


class ShadowBody(StrictModel):
    release_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=8000)


class ShadowVariant(StrictModel):
    variant: Literal["A", "B", "C"]
    policy_version: str
    outcome: Literal["completed", "timeout", "error"]
    decision: str | None = None
    proposal: dict | None = None
    validated_requests: list[dict] = Field(default_factory=list)
    question: dict | None = None
    validation: list[dict] = Field(default_factory=list)
    model_request: dict | None = None
    model_response: dict | None = None
    model_ms: float | None = None
    validation_ms: float | None = None
    faults: list[dict] = Field(default_factory=list)
    fixture_mode: bool


class ShadowOut(StrictModel):
    schema_version: Literal["1"] = "1"
    kind: Literal["intent-shadow-comparison"] = "intent-shadow-comparison"
    acceptance_evidence: Literal[False] = False
    release_id: str
    conversation_id: str
    reference_time: str
    profile: dict
    candidate_ids: list[str]
    context: list[dict]
    context_stats: dict
    variants: list[ShadowVariant]
    business_execution: dict = Field(default_factory=lambda: {"status": "NOT_EXECUTED", "count": 0})

    @model_validator(mode="after")
    def finite_comparison(self):
        if [row.variant for row in self.variants] != ["A", "B", "C"]:
            raise ValueError("Exactly one ordered A/B/C comparison is required")
        if self.business_execution != {"status": "NOT_EXECUTED", "count": 0}:
            raise ValueError("Shadow evidence cannot contain business execution")
        return self


def check_proposal(db, run, release, candidates, proposal, variant):
    """Mirror the finite routing gates on a detached scratch run, never a ledger row."""
    capabilities = {c["capability_id"]: c for c in candidates}
    checked, validation = [], []
    def result(decision, question=None):
        return {"decision": decision, "validated_requests": checked, "validation": validation, "question": question}
    if proposal["decision"] == "OUT_OF_SCOPE": return result("OUT_OF_SCOPE")
    if proposal["decision"] == "AMBIGUOUS":
        if any(option["id"] not in capabilities for option in proposal["options"]): return result("FORBIDDEN")
        return result("AMBIGUOUS", {"kind": "function_choice", "options": proposal["options"]})
    for index, source in enumerate(proposal["requests"]):
        request = copy.deepcopy(source)
        capability = capabilities.get(request["capability_id"])
        if not capability: return result("FORBIDDEN")
        definition = copy.deepcopy(capability["definition"])
        is_v2 = capability["compiled"]["compiler_version"] == "finite-json-v2"
        checks = {"capability_id": request["capability_id"], "semantic": None, "relations": None, "input_schema": None, "preconditions": None, "entity_binding": None}
        validation.append(checks)
        if len(proposal["requests"]) > 1 and definition["effect"] != "READ_ONLY": return result("OUT_OF_SCOPE")
        facts = {"identity.authenticated": True}
        if variant != "A":
            if request["negated"] is not False: return result("OUT_OF_SCOPE")
            if variant == "C": definition["relations"] = []; request["relations"] = []
            if is_v2:
                errors = semantic_errors(definition, request, capability["compiled"]["domain_projection"])
                checks["semantic"] = not errors
                checks["relations"] = not errors if variant == "B" else None
                if errors: return result("OUT_OF_SCOPE")
            elif any(request[key] != definition[key] for key in ("action", "object_type", "result_type")):
                return result("OUT_OF_SCOPE")
        if is_v2:
            if variant == "A":
                # A has no semantic extraction. Server-required bindings remain missing,
                # and any model-supplied business ID is still rejected by bind_arguments.
                request["participants"] = [{"role": r["role"], "type": r["type"], "mention": None} for r in definition["participant_roles"]]
            facts, question = bind_arguments(db, run, index, request, capability, release)
            if question:
                return result("AMBIGUOUS" if question.get("options") else "NEEDS_ARGUMENTS", question)
            checks["entity_binding"] = True if definition["argument_bindings"] else None
            if variant != "A":
                args, units, _ = normalize_parameters(definition, request["arguments"], request["parameter_units"], capability["compiled"]["domain_projection"], run.created_at, release.snapshot["context_policy"]["timezone"])
                request["arguments"] = args; request["parameter_units"] = units
            if truth(definition["trusted_preconditions"], facts) is not True:
                checks["preconditions"] = False; return result("FORBIDDEN")
        args = request["arguments"]
        for field, value in {"user_id": run.user_id, "workspace_id": run.workspace_id}.items():
            if field in definition["input_schema"].get("properties", {}): args[field] = value
        errors = input_errors(definition["input_schema"], args)
        checks["input_schema"] = not errors
        if errors: return result("NEEDS_ARGUMENTS", {"kind": "arguments", "errors": errors, "options": []})
        checks["preconditions"] = all(condition(item, args) is True for item in definition.get("preconditions", []))
        if not checks["preconditions"]: return result("FORBIDDEN")
        checked.append({"capability_id": capability["capability_id"], "version": capability["version"], "arguments": args})
    needs_confirmation = any(capabilities[r["capability_id"]]["definition"]["requires_confirmation"] for r in checked)
    return result("NEEDS_CONFIRMATION" if needs_confirmation else "READY")


def model_attempt(text, candidates, context, variant):
    trace = {}; outcome = "completed"; faults = []; proposal = None
    try:
        proposal = route_v3(text, candidates, context, variant=variant, trace=trace)
    except httpx.TimeoutException:
        outcome = "timeout"; faults = [{"stage": "model", "code": "MODEL_TIMEOUT"}]
    except Exception:
        outcome = "error"; faults = [{"stage": "model", "code": "MODEL_RESPONSE_UNAVAILABLE_OR_INVALID"}]
    return ShadowVariant(variant=variant, policy_version=POLICIES[variant], outcome=outcome, proposal=proposal, faults=faults,
                         **trace).model_dump()


def validate_attempt(db, run, release, candidates, row):
    if row["outcome"] != "completed": return row
    started = time.monotonic()
    try:
        row.update(check_proposal(db, run, release, candidates, row["proposal"], row["variant"]))
    except ResolverUnavailable:
        row.update(outcome="error", decision=None, faults=[{"stage": "authorization", "code": "RESOLVER_UNAVAILABLE"}])
    except (BindingDenied, PermissionError):
        row.update(decision="FORBIDDEN", faults=[{"stage": "authorization", "code": "TRUSTED_BINDING_DENIED"}])
    except (ValueError, TypeError, OverflowError, KeyError):
        row.update(outcome="error", decision=None, faults=[{"stage": "validation", "code": "DETERMINISTIC_VALIDATION_FAILED"}])
    finally:
        row["validation_ms"] = (time.monotonic() - started) * 1000
    return row
