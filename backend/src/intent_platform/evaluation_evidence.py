"""Read-only evidence projections. Callers must enforce runtime owner/session ACLs.

These helpers perform no queries and never treat a capability definition as an
observed model understanding. Missing historic evidence stays explicitly unknown.
"""
from datetime import datetime
import json
from .evaluation_spec import OBSERVATION_NORMALIZATION_VERSION

KNOWN_FIXTURE_MODELS = {"development-deterministic-v1": "development-deterministic-v1"}


def value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def json_value(item):
    if isinstance(item, datetime):
        return item.isoformat()
    if isinstance(item, dict):
        return {key: json_value(val) for key, val in item.items()}
    if isinstance(item, (tuple, list)):
        return [json_value(val) for val in item]
    return item


def evaluation_profile(release, conversation, runtime_session):
    snapshot = value(release, "snapshot", {})
    model = snapshot.get("model_profile", {})
    capabilities = snapshot.get("capabilities", [])
    version = model.get("version")  # Arbitrary serving names do not prove weights versions.
    if version is None and model.get("mode") == "fixture":
        version = KNOWN_FIXTURE_MODELS.get(model.get("name"))
    parameters = model.get("parameters")
    if parameters is None:
        parameters = {key: model[key] for key in ("temperature", "top_p", "seed") if key in model}
    gaps = []
    if not version:
        gaps.append("model_version_unrecorded")
    return {
        "schema_version": "1", "release_id": value(release, "id"),
        "conversation_id": value(conversation, "id"), "workspace_id": value(conversation, "workspace_id"),
        "agent_id": value(conversation, "agent_id"), "user_id": value(conversation, "user_id"),
        "runtime_session_id": value(runtime_session, "id"), "channel": value(runtime_session, "channel"),
        "model_id": model.get("name"), "model_version": version, "model_mode": model.get("mode"),
        "model_parameters": parameters, "prompt_version": snapshot.get("prompt_policy_version"),
        "ontology_version": snapshot.get("ontology_compiler_version"),
        "function_versions": {c["capability_id"]: c["version"] for c in capabilities},
        "connector_versions": snapshot.get("connector_versions", {}),
        "compiler_versions": {c["capability_id"]: c.get("compiled", {}).get("compiler_version") for c in capabilities},
        "fixture_mode": snapshot.get("fixture_mode"), "gaps": gaps,
    }


def _signature(actions):
    return sorted((a.get("capability_id"), a.get("version"),
                   json.dumps(a.get("arguments"), sort_keys=True, ensure_ascii=False, allow_nan=False)) for a in actions)


def stable_semantics(checked, bindings):
    """Fixed, gold-independent semantic projection; raw dynamic metadata stays separate."""
    requests = []
    for request in sorted(checked, key=lambda row: row["index"]):
        source = request["semantics"]
        item = {key: source[key] for key in ("action", "object_type", "result_type", "negated", "parameter_units") if key in source}
        item["capability_id"] = request["capability_id"]
        item["participants"] = sorted([{key: participant[key] for key in ("role", "type") if key in participant}
                                       for participant in source.get("participants", [])], key=lambda row: json.dumps(row, sort_keys=True))
        item["relations"] = sorted([{key: relation[key] for key in ("name", "source", "target") if key in relation}
                                    for relation in source.get("relations", [])], key=lambda row: json.dumps(row, sort_keys=True))
        requests.append(item)
    stable_bindings = []
    for record in (bindings or {}).values():
        if not isinstance(record, dict) or record.get("state") != "resolved" or "parameter" not in record:
            continue
        item = {key: record[key] for key in ("request_index", "parameter", "role", "source", "resolver_ref", "value", "source_version") if key in record}
        reference = record.get("history_ref")
        if reference:
            item["history_ref"] = {key: reference[key] for key in ("binding", "index") if key in reference}
        stable_bindings.append(item)
    stable_bindings.sort(key=lambda row: json.dumps(row, sort_keys=True))
    return {"requests": requests, "bindings": stable_bindings}


def evaluation_evidence(release, conversation, runtime_session, run, calls, events, audits):
    """Project one consistent DB snapshot supplied by the authorized API handler."""
    profile = evaluation_profile(release, conversation, runtime_session)
    rid, revision = value(run, "id"), value(run, "revision")
    calls = [c for c in calls if value(c, "run_id") == rid]
    events = [e for e in events if value(e, "run_id") == rid]
    audits = [a for a in audits if value(a, "resource_id") == rid]
    current_events = [e for e in events if value(e, "revision") == revision]
    current_calls = [c for c in calls if value(c, "revision") == revision]
    confirmations = [e for e in current_events if value(e, "event_type") == "CONFIRMATION"]
    confirmation_event = max(confirmations, key=lambda e: value(e, "sequence", 0)) if confirmations else None
    accepted = [a for a in audits if value(a, "action") == "run.confirm" and value(a, "detail", {}).get("revision") == revision]
    accepted_audit = max(accepted, key=lambda a: value(a, "created_at")) if accepted else None
    cancelled = value(run, "status") == "CANCELLED"
    boundary = cancelled or value(run, "decision") in {"OUT_OF_SCOPE", "FORBIDDEN", "AMBIGUOUS"}
    validation = value(run, "validation_evidence") or {}
    checked = validation.get("requests", []) if validation.get("revision") == revision else []
    validated = bool(not cancelled and checked and all(r.get("checks", {}).get("input_schema") is True
                     and r.get("checks", {}).get("preconditions") is True for r in checked))
    capabilities = {c["capability_id"]: c for c in value(release, "snapshot", {}).get("capabilities", [])}
    proposed = (value(run, "proposal") or {}).get("requests", [])
    actions = []
    gaps = list(profile["gaps"])
    if not validated and not boundary:
        gaps.append("final_validation_evidence_unavailable")
    if not cancelled:
        if validated:
            actions = [{"capability_id": r["capability_id"], "version": r["version"], "arguments": r["arguments"]}
                       for r in checked]
        elif current_calls:
            actions = [{"capability_id": value(c, "capability_id"), "version": value(c, "version"), "arguments": value(c, "arguments")}
                       for c in sorted(current_calls, key=lambda c: value(c, "action_id"))]
        elif confirmation_event:
            actions = [{key: a.get(key) for key in ("capability_id", "version", "arguments")}
                       for a in value(confirmation_event, "payload", {}).get("actions", [])]
        elif value(run, "decision") == "NEEDS_ARGUMENTS":
            # Selected requests remain partial and validation_completed is false.
            actions = [{"capability_id": a["capability_id"], "version": capabilities[a["capability_id"]]["version"], "arguments": a.get("arguments", {})}
                       for a in proposed if a.get("capability_id") in capabilities]
    semantics = None
    if validated and all(r.get("checks", {}).get("semantic") is True and r.get("semantics") is not None for r in checked):
        semantics = stable_semantics(checked, value(run, "bindings", {}))
    elif boundary:
        semantics = {}  # No executable semantic frame is applicable to this boundary decision.
    else:
        gaps.append("validated_semantics_unavailable")
    required = bool(confirmation_event or any(value(c, "definition", {}).get("requires_confirmation") for c in current_calls))
    accepted_flag = bool(value(run, "confirmed") and accepted_audit)
    dispatched = [c for c in calls if value(c, "dispatched_at") is not None]
    before_confirmation = None
    if not required:
        before_confirmation = 0  # Counter is applicable only to a required-confirmation run.
    elif accepted_audit:
        before_confirmation = sum(value(c, "dispatched_at") < value(accepted_audit, "created_at") for c in dispatched)
    elif confirmation_event:
        before_confirmation = len(dispatched)
    binding = None
    if accepted_flag and confirmation_event:
        expected = value(confirmation_event, "payload", {}).get("actions", [])
        binding = _signature(expected) == _signature(actions)
    if required and (before_confirmation is None or (accepted_flag and binding is None)):
        gaps.append("confirmation_binding_unavailable")
    ledger = [{"id": value(c, "id"), "action_id": value(c, "action_id"), "revision": value(c, "revision"),
               "capability_id": value(c, "capability_id"), "version": value(c, "version"), "arguments": value(c, "arguments"),
               "status": value(c, "status"), "dispatched_at": json_value(value(c, "dispatched_at")),
               "effect": value(c, "definition", {}).get("effect"),
               "result": json_value(value(c, "result"))} for c in sorted(calls, key=lambda c: value(c, "action_id"))]
    components = [{"sequence": value(e, "sequence"), "revision": value(e, "revision"), "payload": json_value(value(e, "payload"))}
                  for e in events if value(e, "event_type") == "RESULT"]
    errors = [{"sequence": value(e, "sequence"), "revision": value(e, "revision"), "payload": json_value(value(e, "payload"))}
              for e in current_events if value(e, "event_type") == "ERROR"]
    return {
        "schema_version": "1", "profile": profile, "run_id": rid, "revision": revision,
        "observation_normalization_version": OBSERVATION_NORMALIZATION_VERSION,
        "status": value(run, "status"), "final_decision": "CANCELLED" if cancelled else value(run, "decision"),
        "validation_completed": validated, "selected_requests": actions, "semantics": semantics,
        "validation_evidence": json_value(validation) if validation else None,
        "raw_bindings": json_value(value(run, "bindings", {})),
        "question": json_value(value(run, "question")),
        "confirmation": {"required": required, "accepted": accepted_flag, "binding_valid": binding,
                         "executions_before_confirmation": before_confirmation,
                         "revision": revision, "expires_at": json_value(value(run, "confirmation_expires_at")),
                         "event_sequence": value(confirmation_event, "sequence") if confirmation_event else None,
                         "audit_id": value(accepted_audit, "id") if accepted_audit else None},
        "calls": ledger, "result_components": components, "errors": errors,
        "browser_display_observed": False, "unauthorized_executions": None, "leakage_events": None,
        "gaps": sorted(set(gaps + (["browser_display_unobserved"] if components else []) + ["independent_safety_audit_unavailable"])),
    }
