"""Synthetic HTTP protocol tests. No business acceptance claim or external services."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
import uuid

import httpx
import pytest

from intent_platform.evaluation_cli import archive_trial, commit_trial, main, reserve_trial
from intent_platform.evaluation_evidence import evaluation_evidence, evaluation_profile, stable_semantics
from intent_platform.evaluation_runner import (
    RunnerOptions, RunnerTarget, RuntimeCredential, RuntimeEvaluationRunner, run_comparison, redact,
)
from intent_platform.evaluation_spec import EvaluationConfiguration, EvaluationDataset, ObservationBatch
from intent_platform.evaluation_scoring import EvaluationReport
from test_evaluation_scoring import action, boundary, case, execution, gold, inputs, score
from test_domain_runtime import semantic_app, resolver_service  # Explicit real-PG fixtures, only requested by postgres tests.
from test_http_socket_and_recovery import http_fixture


def dataset_config(cases=None):
    dataset, batch = inputs(cases)
    for scenario in dataset["scenarios"]:
        for step in scenario["steps"]:
            step["expected"]["semantics"] = {}
    config = batch["configuration"]
    config.update(resource_mode="fixture", function_versions={"a": 1}, connector_versions={"a": "fixture-v1"})
    return EvaluationDataset.model_validate(dataset), EvaluationConfiguration.model_validate(config)


def proof(decision="READY", status="SUCCEEDED", *, required=False, accepted=False, calls=None, question=None, revision=7):
    return {"schema_version": "1", "observation_normalization_version": "runtime-observation-v1",
            "run_id": "run-real", "revision": revision, "status": status, "final_decision": decision,
            "validation_completed": decision in {"READY", "NEEDS_CONFIRMATION"},
            "selected_requests": [action("a", id=7)] if decision in {"READY", "NEEDS_CONFIRMATION"} else [],
            "semantics": {}, "question": question, "errors": [],
            "confirmation": {"required": required, "accepted": accepted, "binding_valid": True if accepted else None,
                             "executions_before_confirmation": 0, "revision": revision, "event_sequence": 3 if required else None,
                             "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
            "calls": calls or [], "unauthorized_executions": None, "leakage_events": None,
            "gaps": ["independent_safety_audit_unavailable"]}


def call_result(status="SUCCEEDED", data=None):
    return {"id": "call-real", "action_id": "action_0", "revision": 7, "status": status,
            "dispatched_at": "2026-09-22T00:00:00+00:00", "result": {"data": data}, "effect": "READ_ONLY"}


class RuntimeProtocol:
    def __init__(self, config, states=None):
        self.config, self.states = config, states or [proof(calls=[call_result(data={"actual": 1})])]
        self.index, self.requests = 0, []
        self.subject, self.session, self.channel = "synthetic-user", "session-real", "chat"
        self.message_timeout, self.api_error, self.profile_change = False, None, {}

    def profile(self):
        return {"schema_version": "1", "release_id": self.config.release_id, "conversation_id": "conv-real",
                "agent_id": "agent-real", "workspace_id": "synthetic-space", "user_id": self.subject,
                "runtime_session_id": self.session, "channel": self.channel, "capability_scope": "authorized_subset",
                **{key: getattr(self.config, key) for key in ("model_id", "model_version", "model_parameters", "prompt_version", "ontology_version", "function_versions", "connector_versions")},
                "fixture_mode": True, "gaps": [], **self.profile_change}

    def run(self):
        p = self.states[self.index]
        return {"run_id": "run-real", "conversation_id": "conv-real", "revision": p["revision"],
                "status": p["status"], "question": p["question"]}

    def __call__(self, request):
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, path, body, request.headers.get("authorization")))
        if self.api_error and path.endswith(self.api_error):
            return httpx.Response(403, json={"detail": "denied"})
        if path == "/api/v2/runtime/apps/public-real":
            result = {"agent_id": "agent-real", "release_id": self.config.release_id}
        elif path == "/api/v2/me":
            result = {"user_id": self.subject, "scope": "runtime" if request.headers.get("authorization") == "Bearer runtime-secret" else "management", "workspaces": [{"id": "synthetic-space"}]}
        elif path.endswith("/sessions"):
            result = {"access_token": "runtime-secret", "user_id": self.subject, "agent_id": "agent-real", "runtime_session_id": self.session}
        elif path == "/api/v2/runtime/agents/agent-real":
            result = {"id": "agent-real", "workspace_id": "synthetic-space"}
        elif path.endswith("/conversations"):
            result = {"id": "conv-real"}
        elif path.endswith("/evaluation-profile"):
            result = self.profile()
        elif path.endswith("/history"):
            result = {"conversation": {"id": "conv-real"}, "runs": [self.run()], "events": [], "cursor": 5}
        elif path.endswith("/messages"):
            if self.message_timeout:
                raise httpx.ReadTimeout("ambiguous transport timeout with management-secret", request=request)
            result = self.run()
        elif path.endswith(("/answers", "/confirmations", "/cancel")):
            self.index += 1
            result = self.run()
        elif path.endswith("/evaluation-evidence"):
            result = {**self.states[self.index], "profile": self.profile()}
        elif path == "/api/v2/runtime/runs/run-real":
            result = self.run()
        else:
            raise AssertionError((request.method, path))
        return httpx.Response(200, json=result)


def runner(protocol, **options):
    return RuntimeEvaluationRunner("http://controlled.invalid", RunnerTarget(public_id="public-real", agent_id="agent-real", workspace_id="synthetic-space"),
        [RuntimeCredential("synthetic-user", "synthetic-space", "management-secret")],
        RunnerOptions(**options), transport=httpx.MockTransport(protocol), sleep=lambda _: None)


def multi_confirmation(confirmed=True):
    first = gold("NEEDS_CONFIRMATION", required=True)
    first["execution"] = execution("NOT_EXECUTED", 0)
    second = gold("READY", required=True, accepted=True) if confirmed else gold("CANCELLED", actions=[], required=True)
    if not confirmed:
        second["execution"] = execution("NOT_EXECUTED", 0)
    result = case("confirmation", first)
    result["category"] = "S3"
    result["steps"].append({"step_id": "confirm", "stimulus": {"kind": "confirm", "confirmed": confirmed}, "expected": second})
    return result


def test_wire_observation_is_not_gold_and_missing_safety_is_not_zero():
    dataset, config = dataset_config()
    api = RuntimeProtocol(config)
    trial = runner(api).run(dataset, config, "trial-one")
    observation = trial.observations.cases[0].steps[0]
    assert observation.execution.result == {"actual": 1}
    assert observation.execution.result != dataset.scenarios[0].steps[0].expected.execution.result
    assert observation.execution.displayed_result is None
    assert trial.report.s1.c == 1 and trial.report.s1.end_to_end_success.value == 0
    assert trial.report.unobserved_safety_steps == 1 and not trial.report.acceptance_evidence
    assert trial.runtime_evidence["one"]["history"]["cursor"] == 5
    wire = json.dumps(trial.trace)
    assert "management-secret" not in wire and "runtime-secret" not in wire and "[REDACTED]" in wire


@pytest.mark.parametrize("kind", ["ambiguity", "out_of_scope", "authorization"])
def test_normal_s2_routes_score_without_false_executable_validation_faults(kind):
    scenario = boundary(kind)
    dataset, config = dataset_config([scenario])
    decision = dataset.scenarios[0].steps[0].expected.decision
    question = {"kind": "function_choice", "options": [{"id": "a", "label": "甲"}, {"id": "b", "label": "乙"}]} if kind == "ambiguity" else None
    p = proof(decision, "WAITING_INPUT" if question else "FAILED", question=question)
    p["errors"] = [{"payload": {"code": decision, "message": "normal boundary"}}] if not question else []
    trial = runner(RuntimeProtocol(config, [p])).run(dataset, config, "trial-s2")
    assert trial.report.s2[kind].value == 1
    assert trial.report.correct_cases.value == 0  # Independent safety audit is still unavailable.
    assert trial.report.observed_fault_counts == {"validation:EVIDENCE_GAP": 1}


def test_authorization_can_explicitly_expect_empty_candidate_out_of_scope():
    scenario = boundary("authorization")
    scenario["steps"][0]["expected"]["decision"] = "OUT_OF_SCOPE"
    dataset, config = dataset_config([scenario])
    config = config.model_copy(update={"function_versions": {}, "connector_versions": {}})
    trial = runner(RuntimeProtocol(config, [proof("OUT_OF_SCOPE", "FAILED")])).run(dataset, config, "trial-empty")
    assert trial.report.s2["authorization"].value == 1 and trial.report.s1.n == 0


@pytest.mark.parametrize("change", ["subject", "session", "channel", "model", "release", "permission"])
def test_binding_mismatch_stops_before_business_submission(change):
    dataset, config = dataset_config()
    api = RuntimeProtocol(config)
    if change == "subject": api.subject = "other-user"
    elif change == "session": api.profile_change["runtime_session_id"] = "other-session"
    elif change == "channel": api.channel = "embed"
    elif change == "model": api.profile_change["model_version"] = "other-model"
    elif change == "release": api.profile_change["release_id"] = "other-release"
    else: api.api_error = "/evaluation-profile"
    trial = runner(api).run(dataset, config, "trial-denied")
    assert not any(path.endswith("/messages") for _, path, _, _ in api.requests)
    assert trial.report.s1.n == 1 and trial.report.s1.c == 0
    assert trial.observations.cases[0].steps[0].decision is None


def test_default_never_affirms_even_when_dataset_explicitly_requests_confirmation():
    dataset, config = dataset_config([multi_confirmation()])
    api = RuntimeProtocol(config, [proof("NEEDS_CONFIRMATION", "WAITING_CONFIRMATION", required=True)])
    trial = runner(api).run(dataset, config, "trial-read-only")
    assert not any(path.endswith("/confirmations") for _, path, _, _ in api.requests)
    assert trial.observations.cases[0].steps[1].faults[0].code == "SIDE_EFFECTS_NOT_ALLOWED"
    assert trial.report.s3_scenario_completion.denominator == 1


@pytest.mark.parametrize("confirmed", [True, False])
def test_confirmation_binds_actual_revision_and_negative_confirmation_needs_no_effect_flag(confirmed):
    dataset, config = dataset_config([multi_confirmation(confirmed)])
    waiting = proof("NEEDS_CONFIRMATION", "WAITING_CONFIRMATION", required=True)
    final = proof("READY" if confirmed else "CANCELLED", "SUCCEEDED" if confirmed else "CANCELLED", required=True,
                  accepted=confirmed, calls=[call_result()] if confirmed else [])
    api = RuntimeProtocol(config, [waiting, final])
    trial = runner(api, allow_effects=confirmed).run(dataset, config, "trial-confirm")
    sent = [body for _, path, body, _ in api.requests if path.endswith("/confirmations")]
    assert sent == [{"revision": 7, "confirmed": confirmed}]
    assert trial.observations.cases[0].steps[1].decision == final["final_decision"]
    assert trial.observations.cases[0].steps[1].execution.count == int(confirmed)
    assert trial.report.cases[0].steps[1].route_correct


def test_expired_confirmation_is_not_submitted():
    dataset, config = dataset_config([multi_confirmation()])
    waiting = proof("NEEDS_CONFIRMATION", "WAITING_CONFIRMATION", required=True)
    waiting["confirmation"]["expires_at"] = "2000-01-01T00:00:00+00:00"
    api = RuntimeProtocol(config, [waiting])
    trial = runner(api, allow_effects=True).run(dataset, config, "trial-expired")
    assert not any(path.endswith("/confirmations") for _, path, _, _ in api.requests)
    assert trial.observations.cases[0].steps[1].faults[0].code == "CONFIRMATION_EXPIRED"


@pytest.mark.parametrize("uncertain", ["timeout", "unknown"])
def test_uncertain_business_submission_is_not_retried_or_followed_by_new_step(uncertain):
    dataset, config = dataset_config([multi_confirmation()])
    api = RuntimeProtocol(config, [proof("READY", "UNKNOWN", calls=[call_result("UNKNOWN")])])
    api.message_timeout = uncertain == "timeout"
    trial = runner(api, allow_effects=True).run(dataset, config, "trial-uncertain")
    assert sum(path.endswith("/messages") for _, path, _, _ in api.requests) == 1
    assert not any(path.endswith("/confirmations") for _, path, _, _ in api.requests)
    assert len(trial.observations.cases[0].steps) == 2
    assert trial.observations.cases[0].steps[1].faults[0].code == "AFTER_UNCERTAIN_STEP"


def entity_scenario():
    first = gold("AMBIGUOUS", actions=[])
    first.update(clarification_kind="entity_choice", clarification_options=["甲公司（北京）", "甲公司（上海）"], execution=execution("NOT_EXECUTED", 0))
    result = case("entities", first); result["category"] = "S3"
    result["steps"].append({"step_id": "choose", "stimulus": {"kind": "answer", "option_label": "甲公司（上海）"}, "expected": gold()})
    return result


@pytest.mark.parametrize("ambiguous_label", [False, True])
def test_entity_question_labels_resolve_only_one_current_opaque_id(ambiguous_label):
    dataset, config = dataset_config([entity_scenario()])
    options = [{"id": "choice-random-one", "label": "甲公司（北京）"}, {"id": "choice-random-two", "label": "甲公司（上海）"}]
    if ambiguous_label: options[0]["label"] = options[1]["label"]
    question = {"id": "question-random", "kind": "entity_choice", "options": options}
    api = RuntimeProtocol(config, [proof("AMBIGUOUS", "WAITING_INPUT", question=question), proof(calls=[call_result()], revision=8)])
    trial = runner(api).run(dataset, config, "trial-entity")
    answers = [body for _, path, body, _ in api.requests if path.endswith("/answers")]
    if ambiguous_label:
        assert answers == []
        assert trial.observations.cases[0].steps[1].faults[0].code == "OPTION_LABEL_NOT_UNIQUE"
    else:
        assert answers == [{"revision": 7, "question_id": "question-random", "values": {}, "option_id": "choice-random-two"}]
        assert trial.observations.cases[0].steps[0].clarification_options == dataset.scenarios[0].steps[0].expected.clarification_options
        assert trial.report.cases[0].steps[0].route_correct
        assert trial.observations.cases[0].steps[1].execution.count == 1


def test_sealed_requires_independent_explicit_execution_permission_and_matching_purpose():
    dataset, config = dataset_config()
    raw = dataset.model_dump(); raw["reference"]["purpose"] = "sealed"
    raw["annotation"].update(provenance="business_reviewed", review_ref="synthetic-review", frozen_ref="synthetic-freeze")
    sealed = EvaluationDataset.model_validate(raw)
    api = RuntimeProtocol(config)
    with pytest.raises(ValueError, match="Sealed execution"):
        runner(api).run(sealed, config, "trial-sealed")
    with pytest.raises(ValueError, match="cannot be mixed"):
        runner(api, allow_sealed=True).run(sealed, config, "trial-sealed")
    assert api.requests == []
    sealed_config = config.model_copy(update={"purpose": "sealed"})
    trial = runner(RuntimeProtocol(sealed_config), allow_sealed=True).run(sealed, sealed_config, "trial-sealed")
    assert trial.report.evidence_kind == "sealed_unverified" and not trial.report.acceptance_evidence


@pytest.mark.parametrize("trial_id", ["../escape", "a/b", "a\\b", "C:\\escape", "CON", "nul", "..", "a."])
def test_archive_rejects_unsafe_or_reserved_names(tmp_path, trial_id):
    with pytest.raises(ValueError): reserve_trial(tmp_path, trial_id)
    assert list(tmp_path.iterdir()) == []


def test_archive_is_atomic_non_overwriting_and_redacts_secrets(tmp_path):
    dataset, config = dataset_config()
    trial = runner(RuntimeProtocol(config)).run(dataset, config, "trial-archive")
    trial.trace.append({"echo": "management-secret", "Authorization": "unknown-credential"})
    path = archive_trial(tmp_path, trial)
    raw = path.read_text(encoding="utf-8")
    bundle = json.loads(raw)
    assert "management-secret" not in raw and "runtime-secret" not in raw and "unknown-credential" not in raw
    assert bundle["redactions_applied"] and not bundle["acceptance_evidence"]
    assert bundle["report"]["trial_id"] == "trial-archive"
    EvaluationDataset.model_validate(bundle["dataset"])
    ObservationBatch.model_validate(bundle["observations"])
    EvaluationReport.model_validate(bundle["report"])
    assert list(path.parent.iterdir()) == [path]
    with pytest.raises(FileExistsError): archive_trial(tmp_path, trial)
    with pytest.raises(FileExistsError): commit_trial(path.parent, trial)
    assert path.read_text(encoding="utf-8") == raw


def test_atomic_commit_failure_never_exposes_partial_archive(tmp_path, monkeypatch):
    dataset, config = dataset_config()
    trial = runner(RuntimeProtocol(config)).run(dataset, config, "trial-failed-commit")
    def refuse(*args): raise OSError("simulated filesystem rejection")
    monkeypatch.setattr("intent_platform.evaluation_cli.os.link", refuse)
    with pytest.raises(OSError): archive_trial(tmp_path, trial)
    assert list((tmp_path / "trial-failed-commit").iterdir()) == []
    with pytest.raises(FileExistsError): archive_trial(tmp_path, trial)


@pytest.mark.parametrize("header,value", [("Authorization", "Bearer untracked-bearer"), ("authorization", "Basic dXNlcjpzZWNyZXQ="), ("Proxy-Authorization", "Basic proxy-secret"), ("authorization", {"scheme": "Bearer", "value": "untracked-structured-secret"})])
def test_authorization_secret_redaction_preserves_scoring_rate_and_fault_counts(header, value):
    rate = {"numerator": 0, "denominator": 0, "value": None, "defined": False}
    source = {"headers": {header: value}, "s2": {"authorization": rate},
              "fault_counts": {"authorization:HTTP_403": 1, "authorization:MISSING_CREDENTIAL": 1}}
    result = redact(source)
    assert result["headers"][header] == "[REDACTED]"
    assert result["s2"]["authorization"] == rate and result["fault_counts"] == source["fault_counts"]


def test_cli_scores_original_number_types_and_rejects_plaintext_token_arguments(tmp_path, capsys):
    dataset, batch = inputs()
    batch["cases"][0]["steps"][0]["actions"][0]["arguments"]["id"] = 7.0
    source = tmp_path / "dataset.json"; source.write_text(json.dumps(dataset), encoding="utf-8")
    observed = tmp_path / "observations.json"; observed.write_text(json.dumps(batch), encoding="utf-8")
    assert main(["score", "--dataset", str(source), "--observations", str(observed), "--output", str(tmp_path / "out")]) == 0
    bundle = json.loads((tmp_path / "out/unit-trial-1/trial.json").read_text(encoding="utf-8"))
    assert bundle["report"]["s1"]["argument_accuracy"]["value"] == 0
    with pytest.raises(SystemExit): main(["--token", "never-print-me"])
    assert "never-print-me" not in capsys.readouterr().err


def test_comparison_rejects_changed_control_before_execution():
    dataset, config = dataset_config()
    other = config.model_copy(update={"release_id": "candidate-two", "ablation": "no-ontology", "model_version": "different"})
    api = RuntimeProtocol(config)
    with pytest.raises(ValueError, match="experimental controls"):
        run_comparison(dataset, [(runner(api), config, "one"), (runner(api), other, "two")])
    assert not api.requests


def projection_inputs(decision="READY"):
    release = {"id": "release-one", "snapshot": {"model_profile": {"mode": "fixture", "name": "development-deterministic-v1"}, "capabilities": [{"capability_id": "a", "version": 1, "compiled": {"compiler_version": "finite-json-v2"}}], "fixture_mode": True}}
    conv = {"id": "conv-one", "agent_id": "agent-one", "workspace_id": "space-one", "user_id": "user-one"}
    session = {"id": "session-one", "channel": "chat"}
    semantics = {"action": "READ", "object_type": "Customer", "result_type": "Balance", "participants": [{"role": "customer", "type": "Customer", "history_ref": {"call_id": "call-random", "binding": "customers", "index": 1}}], "relations": [], "parameter_units": {}, "negated": False}
    checked = [{"index": 0, "capability_id": "a", "version": 1, "arguments": {"customer_id": "business-7"}, "checks": {"semantic": True, "input_schema": True, "preconditions": True}, "semantics": semantics}]
    bindings = {"0:customer_id": {"state": "resolved", "request_index": 0, "parameter": "customer_id", "role": "customer", "source": "history", "resolver_ref": "customers-v1", "value": "business-7", "history_ref": {"call_id": "call-random", "binding": "customers", "index": 1}}, "parameters:0": {"date": {"reference_time": "2026-09-22T00:00:00Z"}}}
    run = {"id": "run-one", "revision": 1, "status": "SUCCEEDED", "decision": decision, "validation_evidence": {"revision": 1, "requests": checked}, "bindings": bindings}
    return release, conv, session, run


@pytest.mark.parametrize("decision", ["OUT_OF_SCOPE", "FORBIDDEN", "AMBIGUOUS", "CANCELLED"])
def test_projection_normal_boundaries_do_not_require_executable_semantics(decision):
    release, conv, session, run = projection_inputs(decision)
    run["validation_evidence"] = None
    if decision == "CANCELLED": run["status"] = "CANCELLED"
    p = evaluation_evidence(release, conv, session, run, [], [], [])
    assert p["semantics"] == {} and not p["validation_completed"]
    assert p["gaps"] == ["independent_safety_audit_unavailable"]
    assert p["unauthorized_executions"] is None


def test_stable_semantics_excludes_dynamic_locators_but_keeps_actual_business_binding():
    args = projection_inputs()
    first = evaluation_evidence(*args, [], [], [])
    other = deepcopy(args)
    other[3]["bindings"]["0:customer_id"]["history_ref"]["call_id"] = "different-call"
    other[3]["bindings"]["parameters:0"]["date"]["reference_time"] = "2030-01-01T00:00:00Z"
    other[3]["validation_evidence"]["requests"][0]["semantics"]["participants"][0]["history_ref"]["call_id"] = "different-call"
    second = evaluation_evidence(*other, [], [], [])
    assert first["semantics"] == second["semantics"]
    assert first["raw_bindings"] != second["raw_bindings"]
    other[3]["bindings"]["0:customer_id"]["value"] = "wrong-business-id"
    assert first["semantics"] != evaluation_evidence(*other, [], [], [])["semantics"]


def test_projection_never_treats_stale_or_model_only_validation_as_final_evidence():
    args = projection_inputs()
    args[3]["revision"] = 2
    p = evaluation_evidence(*args, [], [], [])
    assert not p["validation_completed"] and p["semantics"] is None and p["selected_requests"] == []
    assert "final_validation_evidence_unavailable" in p["gaps"]
    args[0]["snapshot"]["model_profile"] = {"mode": "openai_compatible", "name": "arbitrary-serving-name"}
    assert evaluation_profile(*args[:3])["model_version"] is None


@pytest.mark.parametrize("business_changed", [False, True])
def test_semantic_score_ignores_only_declared_dynamic_metadata(business_changed):
    args = projection_inputs()
    expected = evaluation_evidence(*args, [], [], [])["semantics"]
    args[3]["bindings"]["0:customer_id"]["history_ref"]["call_id"] = "next-trial-call"
    args[3]["bindings"]["parameters:0"]["date"]["reference_time"] = "2030-01-01T00:00:00Z"
    if business_changed:
        args[3]["bindings"]["0:customer_id"]["value"] = "different-business-entity"
    dataset, batch = inputs()
    dataset["scenarios"][0]["steps"][0]["expected"]["semantics"] = expected
    batch["cases"][0]["steps"][0]["semantics"] = evaluation_evidence(*args, [], [], [])["semantics"]
    assert score(dataset, batch).s1.semantic_accuracy.value == int(not business_changed)


def test_missing_normalization_version_is_retained_as_failed_step():
    dataset, config = dataset_config()
    p = proof(); del p["observation_normalization_version"]
    trial = runner(RuntimeProtocol(config, [p])).run(dataset, config, "trial-unsupported")
    assert trial.report.s1.n == 1 and trial.report.s1.a == 0
    assert trial.observations.cases[0].steps[0].faults[0].code == "NORMALIZATION_VERSION"


def test_cli_execution_uses_environment_binding_and_reserves_before_any_request(tmp_path, monkeypatch):
    dataset, config = dataset_config()
    api = RuntimeProtocol(config)
    from intent_platform.evaluation_runner import RuntimeEvaluationRunner as ActualRunner
    def controlled(origin, target, credentials, options):
        assert credentials[0].token == "management-secret"
        return ActualRunner(origin, target, credentials, options, transport=httpx.MockTransport(api))
    monkeypatch.setattr("intent_platform.evaluation_cli.RuntimeEvaluationRunner", controlled)
    monkeypatch.setenv("EVALUATION_TEST_TOKEN", "management-secret")
    values = {"dataset": dataset.model_dump(), "configuration": config.model_dump(),
              "target": {"public_id": "public-real", "agent_id": "agent-real", "workspace_id": "synthetic-space"},
              "bindings": {"bindings": [{"user_id": "synthetic-user", "workspace_id": "synthetic-space", "env_var": "EVALUATION_TEST_TOKEN"}]}}
    args = ["execute", "--base-url", "http://controlled.invalid", "--trial-id", "cli-execute", "--output", str(tmp_path / "out")]
    for key, value in values.items():
        path = tmp_path / (key + ".json"); path.write_text(json.dumps(value), encoding="utf-8")
        args += ["--" + key, str(path)]
    assert main(args) == 0
    bundle = (tmp_path / "out/cli-execute/trial.json").read_text(encoding="utf-8")
    assert "management-secret" not in bundle and "runtime-secret" not in bundle
    requests = len(api.requests)
    assert main(args) == 2 and len(api.requests) == requests


class ActualRuntimeTransport(httpx.BaseTransport):
    """HTTPX bridge to actual FastAPI/PG/Worker. No fabricated runtime responses."""
    def __init__(self, client):
        self.client = client

    def handle_request(self, request):
        from conftest import drain
        if request.method == "GET" and "/runtime/runs/" in request.url.path and request.url.path.count("/") == 5:
            drain()
        response = self.client.request(request.method, request.url.path, content=request.content,
                                       headers={key: val for key, val in request.headers.items() if key in {"authorization", "content-type"}})
        return httpx.Response(response.status_code, content=response.content, headers=response.headers)


def actual_configuration(client, app):
    response = client.get(f"/api/v2/runtime/conversations/{app['cid']}/evaluation-profile", headers=app["headers"])
    assert response.status_code == 200, response.text
    profile = response.json()
    fields = {key: profile[key] for key in ("release_id", "model_id", "model_version", "model_parameters", "prompt_version", "ontology_version", "function_versions", "connector_versions")}
    return EvaluationConfiguration(**fields, purpose="development", environment_ref="isolated-intent-test-db", resource_mode="fixture", ablation="ontology")


def actual_runner(client, admin, app, *, allow_effects=False):
    return RuntimeEvaluationRunner("http://actual-runtime.test", RunnerTarget(public_id=app["agent"]["public_id"], agent_id=app["agent"]["id"], workspace_id="ws-dev"),
        [RuntimeCredential("dev-admin", "ws-dev", admin["Authorization"].removeprefix("Bearer "))], RunnerOptions(allow_effects=allow_effects), transport=ActualRuntimeTransport(client))


def test_postgres_runner_s3_entity_selection_and_authorized_history(client, admin, semantic_app):
    app = semantic_app
    first = gold("AMBIGUOUS", actions=[])
    first.update(clarification_kind="entity_choice", clarification_options=["Local one", "Local two"], semantics={}, execution=execution("NOT_EXECUTED", 0))
    expected = gold(actions=[action(app["fid"], item_id="synthetic-2")], result={"fixture": True, "notice": "开发模拟结果，不是银行业务数据", "item_id": "synthetic-2"})
    expected["semantics"] = {"requests": [{"capability_id": app["fid"], "action": "READ", "object_type": "test:Item", "result_type": "test:Result", "negated": False, "parameter_units": {}, "participants": [{"role": "target", "type": "test:Item"}], "relations": []}],
                             "bindings": [{"request_index": 0, "parameter": "item_id", "role": "target", "source": "resolver", "resolver_ref": "local-items@1", "value": "synthetic-2", "source_version": None}]}
    scenario = case("pg-entity", first, capability=app["fid"])
    scenario.update(category="S3", workspace_id="ws-dev", user_id="dev-admin")
    scenario["steps"][0]["stimulus"]["text"] = app["alias"] + " target=many"
    scenario["steps"].append({"step_id": "select", "stimulus": {"kind": "answer", "option_label": "Local two"}, "expected": expected})
    raw, _ = inputs([scenario])
    dataset = EvaluationDataset.model_validate(raw)
    trial = actual_runner(client, admin, app).run(dataset, actual_configuration(client, app), "pg-entity-" + uuid.uuid4().hex[:10])
    first_observed, second = trial.observations.cases[0].steps
    assert first_observed.decision == "AMBIGUOUS" and first_observed.execution.count == 0
    assert first_observed.clarification_options == ["Local one", "Local two"]
    assert second.decision == "READY" and second.execution.count == 1
    assert second.actions[0].arguments == {"item_id": "synthetic-2"}
    assert trial.report.cases[0].steps[1].semantics_correct
    assert all(step.route_correct for step in trial.report.cases[0].steps)
    assert trial.runtime_evidence["pg-entity"]["history"]["cursor"] > 0
    assert trial.runtime_evidence["pg-entity"]["steps"]["select"]["calls"][0]["dispatched_at"]
    assert not trial.report.acceptance_evidence and trial.report.s3_scenario_completion.value == 0
    if root := os.environ.get("INTENT_EVALUATION_EXPORT_ROOT"):
        print("Synthetic fixture archive: " + str(archive_trial(root, trial)))


def test_postgres_runner_zero_authorized_functions_retains_s2_denominator(client, admin, semantic_app):
    app = semantic_app
    for fid in (app["fid"], app["listing_id"]):
        response = client.put(f"/api/v2/management/workspaces/ws-dev/functions/{fid}/access", headers=admin, json={"access_mode": "allowlist", "allowed_users": []})
        assert response.status_code == 200, response.text
    config = actual_configuration(client, app)
    assert config.function_versions == config.connector_versions == {}
    scenario = boundary("authorization")
    scenario.update(workspace_id="ws-dev", user_id="dev-admin")
    scenario["steps"][0]["stimulus"]["text"] = app["alias"] + " target=one"
    scenario["steps"][0]["expected"].update(decision="OUT_OF_SCOPE", semantics={})
    raw, _ = inputs([scenario])
    trial = actual_runner(client, admin, app).run(EvaluationDataset.model_validate(raw), config, "pg-authorization-trial")
    observed = trial.observations.cases[0].steps[0]
    assert observed.decision == "OUT_OF_SCOPE" and observed.execution.count == 0
    assert trial.report.s2["authorization"].value == 1 and trial.report.s2["authorization"].denominator == 1
    assert trial.report.s1.n == 0 and not trial.report.acceptance_evidence


def fixture_application(client, admin, definition, *, http_probe=None):
    from test_domain_runtime import publish_function
    from test_http_socket_and_recovery import publish
    from conftest import conversation
    ws = "/api/v2/management/workspaces/ws-dev"
    if http_probe is not None:
        aid = publish(client, admin, definition, http_probe)
        app = next(a for a in client.get(ws + "/agents", headers=admin).json() if a["id"] == aid)
        fid = app["capability_ids"][0]
    else:
        fid = publish_function(client, admin, definition)
        created = client.post(ws + "/agents", headers=admin, json={"name": "Evaluation synthetic fixture " + uuid.uuid4().hex[:12], "capability_ids": [fid]})
        assert created.status_code == 201, created.text
        app = created.json()
        response = client.post(ws + f"/agents/{app['id']}/publish", headers=admin)
        assert response.status_code == 200, response.text
    session = client.post(f"/api/v2/runtime/apps/{app['public_id']}/sessions", headers=admin, json={}).json()
    headers = {"Authorization": "Bearer " + session["access_token"]}
    return {"agent": app, "fid": fid, "headers": headers, "cid": conversation(client, headers, app["id"])}


def actual_dataset(scenario):
    scenario.update(workspace_id="ws-dev", user_id="dev-admin")
    for step in scenario["steps"]:
        step["expected"]["semantics"] = {}  # These v1 fixtures do not claim ontology semantic coverage.
    raw, _ = inputs([scenario])
    return EvaluationDataset.model_validate(raw)


@pytest.mark.parametrize("missing", [False, True])
def test_postgres_runner_s1_and_missing_arguments_s3(client, admin, missing):
    from intent_platform.schemas import Definition
    alias = "evaluation-read-" + uuid.uuid4().hex[:8]
    definition = Definition(name=alias, description="Synthetic evaluation fixture", aliases=[alias], input_schema={"type": "object", "properties": {"record_id": {"type": "string"}} if missing else {}, "required": ["record_id"] if missing else [], "additionalProperties": False}).model_dump()
    app = fixture_application(client, admin, definition)
    expected = gold("NEEDS_ARGUMENTS" if missing else "READY", actions=[action(app["fid"])])
    if missing:
        expected.update(clarification_kind="arguments", execution=execution("NOT_EXECUTED", 0))
    scenario = case("pg-read", expected, capability=app["fid"])
    scenario["steps"][0]["stimulus"]["text"] = alias
    if missing:
        scenario["category"] = "S3"
        scenario["steps"].append({"step_id": "fill", "stimulus": {"kind": "answer", "values": {"record_id": "synthetic-input"}}, "expected": gold(actions=[action(app["fid"], record_id="synthetic-input")])})
    trial = actual_runner(client, admin, app).run(actual_dataset(scenario), actual_configuration(client, app), "pg-read-trial")
    last = trial.observations.cases[0].steps[-1]
    assert last.decision == "READY" and last.execution.count == 1
    assert last.actions[0].version == 1 and last.actions[0].capability_id == app["fid"]
    if missing:
        assert trial.observations.cases[0].steps[0].execution.count == 0
        assert last.actions[0].arguments == {"record_id": "synthetic-input"}
    else:
        assert (trial.report.s1.n, trial.report.s1.a, trial.report.s1.c) == (1, 1, 1)
    assert all(step.route_correct for step in trial.report.cases[0].steps)
    assert not trial.report.acceptance_evidence


@pytest.mark.parametrize("confirmed", [False, True])
def test_postgres_runner_confirmation_uses_actual_ledger_and_audit(client, admin, confirmed):
    from intent_platform.schemas import Definition
    alias = "evaluation-write-" + uuid.uuid4().hex[:8]
    app = fixture_application(client, admin, Definition(name=alias, description="Synthetic confirmation fixture", aliases=[alias], effect="WRITE").model_dump())
    scenario = multi_confirmation(confirmed)
    for step in scenario["steps"]:
        for route in step["expected"]["routes"]:
            for action_value in route["actions"]:
                action_value.update(capability_id=app["fid"], arguments={})
    scenario["steps"][0]["stimulus"]["text"] = alias
    trial = actual_runner(client, admin, app, allow_effects=confirmed).run(actual_dataset(scenario), actual_configuration(client, app), "pg-confirm-trial")
    first, last = trial.observations.cases[0].steps
    assert first.execution.count == 0 and first.confirmation.executions_before_confirmation == 0
    assert last.decision == ("READY" if confirmed else "CANCELLED") and last.execution.count == int(confirmed)
    assert last.confirmation.accepted is confirmed
    if confirmed:
        assert last.confirmation.binding_valid is True
        evidence = trial.runtime_evidence["confirmation"]["steps"]["confirm"]
        assert evidence["confirmation"]["audit_id"] and evidence["calls"][0]["version"] == 1
        assert evidence["calls"][0]["revision"] == evidence["revision"]
    assert all(step.route_correct for step in trial.report.cases[0].steps)


def test_postgres_runner_unknown_socket_write_is_never_replayed(client, admin, http_fixture):
    from intent_platform.schemas import Definition
    from conftest import drain
    origin, state = http_fixture
    alias = "evaluation-timeout-" + uuid.uuid4().hex[:8]
    definition = Definition(name=alias, description="Synthetic local timeout fixture", aliases=[alias], effect="WRITE", input_schema={"type": "object", "properties": {"delay": {"type": "number"}}, "required": ["delay"], "additionalProperties": False}, connector={"kind": "http", "method": "POST", "endpoint": origin + "/fixture/write", "timeout_seconds": 0.05, "status_path": "status", "result_path": "data"}, output_schema={"type": "object", "properties": {"synthetic": {"type": "boolean"}}, "required": ["synthetic"], "additionalProperties": False}, presentation={"field_map": {"Synthetic": "synthetic"}}).model_dump()
    app = fixture_application(client, admin, definition, http_probe={"delay": 0})
    before = state["writes"]
    scenario = multi_confirmation(True)
    for step in scenario["steps"]:
        step["expected"]["routes"] = [{"actions": [action(app["fid"], delay=0.2)]}]
    scenario["steps"][0]["stimulus"]["text"] = alias + " delay=0.2"
    scenario["steps"][1]["expected"]["execution"] = execution("UNKNOWN", 1)
    scenario["steps"].append({"step_id": "do-not-repeat", "stimulus": {"kind": "request", "text": alias + " delay=0.2"}, "expected": deepcopy(scenario["steps"][0]["expected"])})
    trial = actual_runner(client, admin, app, allow_effects=True).run(actual_dataset(scenario), actual_configuration(client, app), "pg-unknown-trial")
    steps = trial.observations.cases[0].steps
    assert steps[1].execution.status == "UNKNOWN" and steps[1].execution.count == 1
    assert steps[2].faults[0].code == "AFTER_UNCERTAIN_STEP"
    assert sum(item["path"].endswith("/messages") for item in trial.trace) == 1
    assert state["writes"] == before + 1
    drain()
    assert state["writes"] == before + 1
