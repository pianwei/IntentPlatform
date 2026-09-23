"""Revision-bound, attributable source conflicts; imported prose is not authority."""
import copy
from fastapi import HTTPException
from .document_versions import get_version
from .ports import at

REVIEW_FIELDS = {"name", "description", "object_type", "action", "result_type", "effect", "requires_confirmation", "input_schema", "output_schema", "participant_roles", "relations", "preconditions", "trusted_preconditions", "parameter_semantics", "argument_bindings", "result_bindings", "success_condition", "presentation", "connector.endpoint", "connector.method", "connector.http_mapping", "connector.status_path", "connector.result_path", "connector.credential_ref"}
BUSINESS_FIELDS = {"object_type", "action", "result_type", "effect", "requires_confirmation", "success_condition", "output_schema", "presentation", "connector.status_path", "connector.result_path", "connector.credential_ref"}
REVIEW_FIELDS.update({"connector.http_body_required", "connector.http_failure_statuses"})
BUSINESS_FIELDS.add("connector.http_failure_statuses")


def validate_citation(db, ws, citation):
    if citation["field_path"] not in REVIEW_FIELDS:
        raise ValueError("Source claim field is outside the finite review contract")
    version = get_version(db, ws, citation["document_id"], citation["revision"])
    section = next((s for s in version.sections if s["id"] == citation["section_id"]), None)
    if not section or citation["quote"] not in section["text"]:
        raise ValueError("Source excerpt is absent from the pinned section")
    return version


def source_manifest(db, function):
    state = function.source_state or {}
    references = {(r["document_id"], r["revision"]) for r in function.definition.get("document_refs", [])}
    if state.get("api_source"):
        references.add((state["api_source"]["document_id"], state["api_source"]["revision"]))
    for claim in state.get("citations", []):
        validate_citation(db, function.workspace_id, claim)
        references.add((claim["document_id"], claim["revision"]))
    sources = []
    for identifier, revision in sorted(references):
        version = get_version(db, function.workspace_id, identifier, revision)
        sources.append({"document_id": identifier, "revision": revision, "version_id": version.id, "filename": version.filename})
    return {"documents": sources, "api_source": state.get("api_source"), "api_facts": state.get("api_facts", {}),
            "citations": state.get("citations", []), "confirmations": state.get("confirmations", {}), "function_revision": function.revision}


def review(function):
    state = function.source_state or {}
    if not state.get("required"):
        return {"revision": function.revision, "required": False, "ready": True, "issues": [], "api_source": None, "api_facts": {}, "citations": []}
    candidates = {}
    for field, fact in state.get("api_facts", {}).items():
        candidates.setdefault(field, []).append({"kind": "api", **fact})
    for claim in state.get("citations", []):
        candidates.setdefault(claim["field_path"], []).append({"kind": "document", "value": claim["proposed_value"], "document_id": claim["document_id"], "revision": claim["revision"], "section_id": claim["section_id"], "quote": claim["quote"], "claimed_by": claim["claimed_by"]})
    fields = set(candidates) | (BUSINESS_FIELDS if state.get("api_source") else set())
    issues = []
    for field in sorted(fields):
        current = at(function.definition, field)
        options = candidates.get(field, [])
        conflict = any(option["value"] != current for option in options)
        # Cited interpretations require confirmation even if they happen to equal the draft.
        requires = conflict or field in BUSINESS_FIELDS and state.get("api_source") or any(o["kind"] == "document" for o in options)
        if not requires: continue
        saved = state.get("confirmations", {}).get(field, {})
        resolved = saved.get("revision") == function.revision and saved.get("value") == current and saved.get("candidates") == options and bool(saved.get("reason"))
        issues.append({"field_path": field, "kind": "conflict" if conflict else "review", "current_value": current, "candidates": options, "resolved": resolved, "reason": saved.get("reason") if resolved else None})
    return {"revision": function.revision, "required": True, "ready": all(i["resolved"] for i in issues), "issues": issues, "api_source": state.get("api_source"), "api_facts": state.get("api_facts", {}), "citations": state.get("citations", [])}


def assert_ready(db, function):
    manifest = source_manifest(db, function)
    result = review(function)
    if not result["ready"]:
        raise ValueError("Current source conflicts and business interpretations require confirmation with reasons")
    return manifest


def invalidate(function):
    function.validated_revision = None; function.validation = None; function.status = "draft"
    if function.source_state:
        function.source_state = {**function.source_state, "confirmations": {}}
