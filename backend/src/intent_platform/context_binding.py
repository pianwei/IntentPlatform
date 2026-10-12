"""Trusted entity binding and explicitly referenced successful conversation results."""
import copy
from sqlalchemy import select
from .models import Call, Run, Function, Conversation, Release
from .policies import permitted
from .ports import at
from .db import uid
from .resolvers import resolve
from .synthetic_runtime import resolver_kwargs


class BindingDenied(ValueError):
    pass


def source_rows(db, run, reference, expected, resolver_ref):
    call = db.get(Call, reference["call_id"])
    source_run = db.get(Run, call.run_id) if call else None
    function = db.get(Function, call.capability_id) if call else None
    if not call or call.status != "SUCCEEDED" or not source_run or source_run.conversation_id != run.conversation_id or source_run.id == run.id or not function or function.disabled or not permitted(function, run.user_id):
        raise BindingDenied("Historical result is unavailable in this authorized conversation")
    binding = next((b for b in call.definition.get("result_bindings", []) if b["name"] == reference["binding"]), None)
    if not binding or binding["entity_type"] != expected or binding["resolver_ref"] != resolver_ref:
        raise BindingDenied("Historical result has no compatible registered entity binding")
    rows = at((call.result or {}).get("data", {}), binding["collection_path"])
    if not isinstance(rows, list) or len(rows) > 100:
        raise BindingDenied("Historical collection exceeds the supported reference boundary")
    index = reference.get("index")
    if index is not None and (type(index) is not int or not 0 <= index < len(rows)):
        raise BindingDenied("Historical row does not exist")
    selected = [(index, rows[index])] if index is not None else list(enumerate(rows))
    result = []
    for index, row in selected:
        identifier, label = at(row, binding["id_field"]), at(row, binding["label_field"])
        if not isinstance(identifier, str) or not identifier or len(identifier) > 256 or not isinstance(label, str) or not label or len(label) > 200:
            raise BindingDenied("Historical entity ID/label does not meet its registered contract")
        result.append({"id": identifier, "label": label, "type": expected,
                       "history_ref": {"call_id": call.id, "binding": binding["name"], "index": index}, "source_version": call.version})
    return result


def authorized_history(db, run, reference, expected, resolver_ref, profile, *, max_checks=40):
    result = []
    rows = source_rows(db, run, reference, expected, resolver_ref)
    if len(rows) > max_checks:
        raise BindingDenied("Reference an explicit row in collections exceeding the finite authorization budget")
    for row in rows:
        matches = resolve(resolver_ref, profile, run.workspace_id, run.user_id, row["label"], selected_id=row["id"], **resolver_kwargs(db, run, profile))
        if matches:
            result.append({**row, "label": matches[0]["label"]})
    return result


def history_context(db, run, release, stats=None):
    stats = stats if stats is not None else {}
    stats.update({"checked": 0, "included": 0, "truncated": False})
    policy = release.snapshot.get("context_policy")
    if not policy:
        return []
    calls = db.scalars(select(Call).join(Run, Run.id == Call.run_id).where(Run.conversation_id == run.conversation_id,
                       Run.id != run.id, Call.status == "SUCCEEDED").order_by(Run.created_at.desc()).limit(policy["max_calls"])).all()
    result = []
    checked = 0
    for call in calls:
        for binding in call.definition.get("result_bindings", []):
            reference = binding["resolver_ref"]
            profile = release.snapshot.get("resolver_profiles", {}).get(reference)
            if not profile:
                continue
            try:
                source = source_rows(db, run, {"call_id": call.id, "binding": binding["name"]}, binding["entity_type"], reference)
                for item in source:
                    if checked >= policy["max_items"]:
                        stats["truncated"] = True
                        return result
                    checked += 1
                    stats["checked"] = checked
                    rows = authorized_history(db, run, item["history_ref"], binding["entity_type"], reference, profile, max_checks=1)
                    for row in rows:
                        result.append({"history_ref": row["history_ref"], "type": row["type"], "label": row["label"]})
                        stats["included"] = len(result)
            except (ValueError, PermissionError):
                stats["excluded_or_unavailable"] = True
                continue
    return result


def bind_arguments(db, run, request_index, request, capability, release):
    definition = capability["definition"]
    records = copy.deepcopy(run.bindings or {})
    facts = {"identity.authenticated": True}
    roles = {r["role"]: r for r in definition.get("participant_roles", [])}
    required_parameters = set(definition.get("input_schema", {}).get("required", []))
    for parameter, binding in definition.get("argument_bindings", {}).items():
        key = str(request_index) + ":" + parameter
        role = binding["role"]; resolver_ref = binding["resolver_ref"]
        saved = records.get(key, {})
        participant = next((p for p in request["participants"] if p["role"] == role), None)
        if not participant:
            role_spec = roles.get(role, {})
            optional = role_spec.get("required") is False and role_spec.get("min_count") == 0 and parameter not in required_parameters
            if optional and parameter not in request["arguments"] and not saved:
                continue
            raise BindingDenied("Required participant is missing")
        profile = release.snapshot["resolver_profiles"][resolver_ref]
        if participant["type"] != profile["entity_type"]:
            raise BindingDenied("Claimed participant type does not match the trusted resolver")
        if parameter in request["arguments"] and ("value" not in saved or request["arguments"][parameter] != saved["value"]):
            raise BindingDenied("Business identifiers cannot originate from model or form arguments")
        reference = saved.get("history_ref") or participant.get("history_ref")
        query = saved.get("query") or participant.get("mention")
        record = {**saved, "parameter": parameter, "role": role, "resolver_ref": resolver_ref,
                  "request_index": request_index, "source": "history" if reference else "resolver"}
        if reference:
            choices = authorized_history(db, run, reference, profile["entity_type"], resolver_ref, profile)
        elif query:
            choices = resolve(resolver_ref, profile, run.workspace_id, run.user_id, query, saved.get("selected_id"), **resolver_kwargs(db, run, profile))
        else:
            record["state"] = "mention"
            records[key] = record; run.bindings = records
            return facts, {"kind": "entity_mention", "binding_key": key, "request_index": request_index,
                           "fields": [{"name": "mention", "type": "string", "required": True, "label": role + "（名称或描述）"}], "options": []}
        if not choices:
            raise BindingDenied("No authorized entity matches the current binding")
        if len(choices) > 1:
            if len({candidate["label"] for candidate in choices}) != len(choices):
                raise BindingDenied("Resolver must provide distinguishable masked labels for authorized candidates")
            options = {uid("choice"): candidate for candidate in choices}
            record.update({"state": "choice", "options": options, "query": query, "history_ref": reference})
            records[key] = record; run.bindings = records
            return facts, {"kind": "history_choice" if reference else "entity_choice", "binding_key": key, "request_index": request_index,
                           "fields": [], "options": [{"id": option_id, "label": candidate["label"]} for option_id, candidate in options.items()]}
        selected = choices[0]
        record.update({"state": "resolved", "value": selected["id"], "selected_id": selected["id"], "query": selected["label"] if reference else query,
                       "history_ref": selected.get("history_ref"), "source_version": selected.get("source_version")})
        record.pop("options", None)
        records[key] = record
        request["arguments"][parameter] = selected["id"]
        facts["entity." + role + ".authorized"] = True
        if reference:
            facts["history." + role + ".authorized"] = True
    run.bindings = records
    return facts, None


def apply_binding_answer(run, body):
    question = run.question
    records = copy.deepcopy(run.bindings)
    record = records[question["binding_key"]]
    if question["kind"] == "entity_mention":
        if set(body.values) != {"mention"} or not isinstance(body.values["mention"], str) or not 1 <= len(body.values["mention"].strip()) <= 500 or body.option_id:
            raise ValueError("Provide a participant name or description")
        record["query"] = body.values["mention"].strip()
    else:
        if body.values or body.option_id not in record.get("options", {}):
            raise ValueError("Select one current authorized entity option")
        selected = record["options"][body.option_id]
        record["selected_id"] = selected["id"]
        if selected.get("history_ref"):
            record["history_ref"] = selected["history_ref"]
        record.pop("options", None)
    record["state"] = "selected"; run.bindings = records


def binding_summaries(bindings):
    return [{"parameter": value["parameter"], "source": value["source"], "role": value["role"], "resolver_ref": value["resolver_ref"],
             "source_call_id": (value.get("history_ref") or {}).get("call_id"), "binding": (value.get("history_ref") or {}).get("binding"),
             "index": (value.get("history_ref") or {}).get("index")} for value in (bindings or {}).values() if isinstance(value, dict) and "parameter" in value]


def authorize_question(db, run, question_id):
    """Retained masked labels are still subject to current object/source grants."""
    from .resolvers import resolver_budget, question_cache
    cache = question_cache()
    key = (run.id, question_id)
    if key in cache:
        return
    record = (run.bindings or {}).get("question:" + question_id)
    function = db.get(Function, record["capability_id"]) if record else None
    if not record or not function or function.disabled or not permitted(function, run.user_id):
        raise BindingDenied("Question capability access was revoked")
    release = db.get(Release, db.get(Conversation, run.conversation_id).release_id)
    reference = record["resolver_ref"]
    profile = release.snapshot.get("resolver_profiles", {}).get(reference)
    if not profile:
        raise BindingDenied("Question resolver is unavailable")
    with resolver_budget(calls=40, seconds=20):
        for candidate in record["candidates"]:
            if candidate.get("history_ref"):
                rows = authorized_history(db, run, candidate["history_ref"], profile["entity_type"], reference, profile, max_checks=1)
            else:
                rows = resolve(reference, profile, run.workspace_id, run.user_id, candidate["label"], candidate["id"], **resolver_kwargs(db, run, profile))
            if len(rows) != 1 or rows[0]["id"] != candidate["id"] or rows[0]["label"] != candidate["label"]:
                raise BindingDenied("Question candidate access changed")
    cache.add(key)


def revalidate_binding_evidence(db, run, bindings, arguments, release):
    """Recheck existing evidence without pretending a read-only probe is a Call."""
    facts = {"identity.authenticated": True}
    for record in bindings.values():
        if not isinstance(record, dict) or "parameter" not in record:
            continue
        profile = release.snapshot.get("resolver_profiles", {}).get(record["resolver_ref"])
        if not profile or arguments.get(record["parameter"]) != record["value"]:
            raise BindingDenied("Persisted binding is unavailable")
        if record.get("history_ref"):
            rows = authorized_history(db, run, record["history_ref"], profile["entity_type"], record["resolver_ref"], profile)
            if len(rows) != 1 or rows[0]["id"] != record["value"]:
                raise BindingDenied("Historical source authorization changed")
        elif len(resolve(record["resolver_ref"], profile, run.workspace_id, run.user_id, record["query"], record["value"], **resolver_kwargs(db, run, profile))) != 1:
            raise BindingDenied("Entity authorization changed before dispatch")
        facts["entity." + record["role"] + ".authorized"] = True
        if record.get("history_ref"):
            facts["history." + record["role"] + ".authorized"] = True
    return facts


def revalidate_call_bindings(db, run, call, release):
    # An optional filter is optional only while absent. If present at dispatch,
    # it needs the same trusted resolver evidence as a required identifier.
    roles = {r["role"]: r for r in call.definition.get("participant_roles", [])}
    required = set(call.definition.get("input_schema", {}).get("required", []))
    for parameter, binding in call.definition.get("argument_bindings", {}).items():
        role = roles.get(binding["role"], {})
        must_bind = parameter in call.arguments or parameter in required or role.get("min_count", 1) > 0
        evidence = [record for record in call.bindings.values() if isinstance(record, dict) and record.get("parameter") == parameter]
        if must_bind and (len(evidence) != 1 or evidence[0].get("role") != binding["role"] or
                         evidence[0].get("resolver_ref") != binding["resolver_ref"]):
            raise BindingDenied("Supplied business identifier has no unique trusted binding")
    facts = revalidate_binding_evidence(db, run, call.bindings, call.arguments, release)
    if release.snapshot.get("prompt_policy_version") == "three-layer-v1":
        from .semantic_v2 import truth
        from .ports import condition
        if truth(call.definition.get("trusted_preconditions"), facts) is not True or any(
                condition(c, call.arguments) is not True for c in call.definition.get("preconditions", [])):
            raise BindingDenied("Execution preconditions changed before dispatch")
