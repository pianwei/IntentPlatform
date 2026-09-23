"""Synthetic, explicitly labelled gold; these tests are not agent acceptance data."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from intent_platform.evaluation_spec import EvaluationDataset, ObservationBatch, validate_dataset_separation
from intent_platform.evaluation_scoring import EvaluationReport, score_evaluation


def action(capability="a", **arguments):
    return {"capability_id": capability, "version": 1, "arguments": arguments}


def execution(status="SUCCEEDED", count=1, result=None):
    return {"status": status, "count": count, "result": result, "displayed_result": result}


def gold(decision="READY", actions=None, *, required=False, accepted=False, result=None):
    return {"decision": decision, "routes": [{"actions": [action("a", id=7)] if actions is None else actions}],
            "semantics": {"action": "read", "negated": False}, "clarification_options": [],
            "clarification_kind": "function_choice" if decision == "AMBIGUOUS" else None,
            "confirmation": {"required": required, "accepted": accepted},
            "execution": execution(result=result)}


def case(case_id="one", expected=None, *, capability="a"):
    return {"case_id": case_id, "category": "S1", "primary_capability_id": capability,
            "workspace_id": "synthetic-space", "user_id": "synthetic-user", "split_group": case_id,
            "steps": [{"step_id": "request", "stimulus": {"kind": "request", "text": "合成评分样本"},
                       "expected": expected or gold()}]}


def observed(case_value):
    steps = []
    for step in case_value["steps"]:
        g = step["expected"]
        steps.append({"step_id": step["step_id"], "routing_outcome": "completed", "decision": g["decision"],
                      "validation_completed": True, "actions": deepcopy(g["routes"][0]["actions"]),
                      "semantics": deepcopy(g["semantics"]), "clarification_options": g["clarification_options"],
                      "clarification_kind": g["clarification_kind"],
                      "confirmation": {**g["confirmation"], "binding_valid": True if g["confirmation"]["accepted"] else None,
                                       "executions_before_confirmation": 0}, "execution": deepcopy(g["execution"]),
                      "unauthorized_executions": 0, "leakage_events": 0})
    return {"case_id": case_value["case_id"], "steps": steps}


def inputs(cases=None):
    cases = cases or [case()]
    reference = {"dataset_id": "synthetic-scorer-tests", "version": "1", "purpose": "development"}
    dataset = {"reference": reference, "annotation": {"provenance": "synthetic", "author": "unit-test"}, "scenarios": cases}
    batch = {"dataset": deepcopy(reference), "trial_id": "unit-trial-1", "configuration": {
        "purpose": "development", "release_id": "synthetic-release", "model_id": "no-model-invoked",
        "model_version": "synthetic-v1", "model_parameters": {"temperature": 0},
        "prompt_version": "synthetic-prompt-v1", "ontology_version": "synthetic-ontology-v1",
        "function_versions": {key: 1 for key in "abcd"}, "connector_versions": {key: "fixture-v1" for key in "abcd"},
        "environment_ref": "pure-unit-test", "resource_mode": "synthetic", "ablation": "ontology", "tags": ["synthetic"]},
        "cases": [observed(c) for c in cases]}
    return dataset, batch


def score(dataset, batch):
    return score_evaluation(EvaluationDataset.model_validate(dataset), ObservationBatch.model_validate(batch))


def boundary(kind):
    decision = {"ambiguity": "AMBIGUOUS", "out_of_scope": "OUT_OF_SCOPE", "authorization": "FORBIDDEN"}[kind]
    g = gold(decision, actions=[])
    g["execution"] = execution("NOT_EXECUTED", 0)
    if kind == "ambiguity":
        g["clarification_options"] = ["a", "b"]
    c = case(kind, g)
    c.update(category="S2", boundary=kind, primary_capability_id=None)
    return c


def multi_case():
    g = gold("NEEDS_ARGUMENTS", actions=[action("a")])
    g["execution"] = execution("NOT_EXECUTED", 0)
    c = case("multi", g)
    c["category"] = "S3"
    c["steps"].append({"step_id": "answer", "stimulus": {"kind": "answer", "values": {"id": 7}}, "expected": gold()})
    return c


def test_s1_denominators_retain_timeout_downstream_and_missing_cases():
    cases = [case("good"), case("timeout"), case("downstream"), case("missing")]
    dataset, batch = inputs(cases)
    batch["cases"][1]["steps"][0]["routing_outcome"] = "timeout"  # A correct proposal is not a final decision.
    failed = batch["cases"][2]["steps"][0]
    failed["execution"] = execution("FAILED", 1, {"error": "service unavailable"})
    failed["faults"] = [{"stage": "downstream", "code": "HTTP_503", "detail": "Synthetic unavailable service", "evidence_ref": "synthetic-trace"}]
    batch["cases"].pop()
    report = score(dataset, batch)
    assert (report.s1.n, report.s1.a, report.s1.c) == (4, 2, 2)
    assert report.s1.correct_route_rate.value == .5
    assert report.s1.decided_accuracy.value == 1
    assert report.s1.decision_coverage.value == .5
    assert report.s1.end_to_end_success.numerator == 1
    assert report.s1.end_to_end_success.denominator == 4
    assert report.total_cases == 4 and report.observed_cases == 3
    assert report.observed_fault_counts == {"downstream:HTTP_503": 1}
    assert report.cases[2].steps[0].observation.faults[0].evidence_ref == "synthetic-trace"
    assert report.difference_counts["missing_step"] == 1
    assert {d.field for d in report.cases[2].steps[0].differences} == {"execution"}


def test_a_zero_is_undefined_and_cannot_look_like_perfect_accuracy():
    dataset, batch = inputs()
    batch["cases"] = []
    report = score(dataset, batch)
    assert report.s1.n == 1 and report.s1.a == report.s1.c == 0
    assert report.s1.decided_accuracy.value is None and not report.s1.decided_accuracy.defined
    assert report.s1.correct_route_rate.value == report.s1.decision_coverage.value == 0
    assert any("cannot satisfy" in warning for warning in report.warnings)


def test_confirmation_gold_counts_as_correct_route_and_completed_confirmed_execution():
    dataset, batch = inputs([case(expected=gold("NEEDS_CONFIRMATION", required=True, accepted=True))])
    report = score(dataset, batch)
    assert (report.s1.n, report.s1.a, report.s1.c) == (1, 1, 1)
    assert report.s1.confirmation_zero_execution.value == 1
    assert report.s1.confirmation_binding.value == 1
    assert report.s1.post_confirmation_success.value == 1
    assert report.s1.automatic_execution_coverage.value is None


@pytest.mark.parametrize("change", ["skipped", "early_execution", "wrong_binding"])
def test_confirmation_policy_violations_never_count_as_correct_routes(change):
    dataset, batch = inputs([case(expected=gold("NEEDS_CONFIRMATION", required=True, accepted=True))])
    step = batch["cases"][0]["steps"][0]
    if change == "skipped":
        step["decision"] = "READY"
        step["confirmation"].update(required=False, accepted=False, binding_valid=None)
    elif change == "early_execution":
        step["confirmation"]["executions_before_confirmation"] = 1
    else:
        step["confirmation"]["binding_valid"] = False
    report = score(dataset, batch)
    assert report.s1.a == 1 and report.s1.c == 0
    assert report.s1.end_to_end_success.value == 0
    assert report.s1.argument_accuracy.value == 1  # Confirmation failure does not alter correct parameters.


def test_unnecessary_confirmation_is_wrong_and_route_only_confirmation_is_not_business_completion():
    dataset, batch = inputs()
    batch["cases"][0]["steps"][0].update(decision="NEEDS_CONFIRMATION")
    batch["cases"][0]["steps"][0]["confirmation"]["required"] = True
    assert score(dataset, batch).s1.c == 0
    g = gold("NEEDS_CONFIRMATION", required=True)
    g["execution"] = execution("NOT_EXECUTED", 0)
    dataset, batch = inputs([case(expected=g)])
    report = score(dataset, batch)
    assert report.s1.c == 1 and report.s1.end_to_end_success.value == 0


def test_arguments_semantics_and_display_are_separate_from_route_success():
    dataset, batch = inputs([case("args"), case("semantics"), case("display")])
    batch["cases"][0]["steps"][0]["actions"][0]["arguments"]["id"] = True
    batch["cases"][1]["steps"][0]["semantics"]["negated"] = True
    batch["cases"][2]["steps"][0]["execution"]["displayed_result"] = "wrong result"
    report = score(dataset, batch)
    assert report.s1.c == 3 and report.s1.end_to_end_success.value == 0
    assert report.s1.argument_accuracy.numerator == 2
    assert report.s1.semantic_accuracy.numerator == 2
    assert all(not c.correct for c in report.cases)


@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_argument_json_comparison_preserves_boolean_number_and_string_types(value):
    g = gold(actions=[action("a", count=1)])
    dataset, batch = inputs([case(expected=g)])
    batch["cases"][0]["steps"][0]["actions"][0]["arguments"]["count"] = value
    assert score(dataset, batch).s1.argument_accuracy.value == 0


def test_s2_groups_do_not_depend_on_s1_decided_coverage():
    dataset, batch = inputs([boundary(g) for g in ("ambiguity", "out_of_scope", "authorization")])
    for c in batch["cases"]:
        c["steps"][0]["validation_completed"] = False
    report = score(dataset, batch)
    assert report.s1.n == 0
    assert all(metric.numerator == metric.denominator == 1 for metric in report.s2.values())
    assert report.out_of_scope_false_acceptance.value == 0
    assert report.correct_cases.value == 1


def test_s2_wrong_clarification_false_acceptance_and_permission_leak_are_counted():
    dataset, batch = inputs([boundary(g) for g in ("ambiguity", "out_of_scope", "authorization")])
    batch["cases"][0]["steps"][0]["clarification_options"] = ["a", "c"]
    batch["cases"][1]["steps"][0].update(decision="READY", actions=[action("a")], validation_completed=False)
    batch["cases"][2]["steps"][0].update(unauthorized_executions=1, leakage_events=2)
    report = score(dataset, batch)
    assert all(metric.value == 0 for metric in report.s2.values())
    assert report.out_of_scope_false_acceptance.numerator == 1
    assert report.unauthorized_executions == 1 and report.leakage_events == 2


@pytest.mark.parametrize("change", ["missing", "wrong", "reordered"])
def test_s3_is_one_scenario_and_every_critical_step_must_match(change):
    dataset, batch = inputs([multi_case()])
    assert score(dataset, batch).s3_scenario_completion.value == 1
    steps = batch["cases"][0]["steps"]
    if change == "missing":
        steps.pop()
    elif change == "wrong":
        steps[1]["actions"][0]["arguments"]["id"] = 8
    else:
        steps.reverse()
    report = score(dataset, batch)
    assert report.s3_scenario_completion.denominator == 1
    assert report.s3_scenario_completion.numerator == 0
    assert len(report.cases[0].steps) == 2


def test_equivalent_routes_are_complete_alternatives_and_use_fixed_reporting_cohort():
    first = case("equivalent")
    first["steps"][0]["expected"]["routes"].append({"actions": [action("b", code="x")]})
    second = case("second", gold(actions=[action("b", id=7)]), capability="b")
    dataset, batch = inputs([first, second])
    batch["cases"][0]["steps"][0]["actions"] = [action("b", code="x")]
    batch["cases"][1]["steps"][0]["decision"] = "OUT_OF_SCOPE"
    report = score(dataset, batch)
    assert report.per_function["a"].n == report.per_function["b"].n == 1
    assert report.per_function["a"].c == 1 and report.per_function["b"].c == 0
    assert report.per_function_macro_route_accuracy == .5
    assert report.confusion["a"] == {'["b"]': 1}


def test_cannot_combine_parts_of_different_allowed_routes():
    scenario = multi_case()
    scenario["steps"][0]["expected"]["routes"] = [{"actions": [action("a"), action("b")]}, {"actions": [action("c"), action("d")]}]
    dataset, batch = inputs([scenario])
    batch["cases"][0]["steps"][0]["actions"] = [action("a"), action("d")]
    report = score(dataset, batch)
    assert not report.cases[0].steps[0].route_correct
    assert report.s3_scenario_completion.value == 0


@pytest.mark.parametrize("field,value", [("dataset_id", "other"), ("version", "2"), ("purpose", "sealed")])
def test_reference_mismatch_is_rejected_before_scoring(field, value):
    dataset, batch = inputs()
    batch["dataset"][field] = value
    if field == "purpose":
        batch["configuration"]["purpose"] = value
    with pytest.raises(ValueError, match="match exactly"):
        score(dataset, batch)


def test_sealed_purpose_needs_frozen_business_gold_and_never_creates_acceptance_evidence():
    dataset, batch = inputs()
    dataset["reference"]["purpose"] = batch["dataset"]["purpose"] = batch["configuration"]["purpose"] = "sealed"
    with pytest.raises(ValidationError, match="Sealed datasets require"):
        score(dataset, batch)
    dataset["annotation"].update(provenance="business_reviewed", review_ref="synthetic-review-reference", frozen_ref="synthetic-freeze-reference")
    report = score(dataset, batch)
    assert report.evidence_kind == "sealed_unverified" and report.acceptance_evidence is False
    with pytest.raises(ValidationError):
        EvaluationReport.model_validate({**report.model_dump(), "acceptance_evidence": True})
    batch["configuration"]["purpose"] = "development"
    with pytest.raises(ValidationError, match="cannot be mixed"):
        score(dataset, batch)


@pytest.mark.parametrize("problem", ["duplicate_case", "duplicate_step", "unknown_case", "unknown_step", "scorer", "extra", "coercion", "configuration"])
def test_invalid_or_ambiguous_evidence_cannot_be_silently_scored(problem):
    dataset, batch = inputs()
    if problem == "duplicate_case":
        batch["cases"].append(deepcopy(batch["cases"][0]))
    elif problem == "duplicate_step":
        batch["cases"][0]["steps"].append(deepcopy(batch["cases"][0]["steps"][0]))
    elif problem == "unknown_case":
        batch["cases"][0]["case_id"] = "unregistered"
    elif problem == "unknown_step":
        batch["cases"][0]["steps"][0]["step_id"] = "unregistered"
    elif problem == "scorer":
        batch["scorer_version"] = "new-unknown-scorer"
    elif problem == "extra":
        dataset["acceptance_evidence"] = True
    elif problem == "coercion":
        batch["cases"][0]["steps"][0]["validation_completed"] = "true"
    else:
        batch["configuration"]["function_versions"]["a"] = 9
    with pytest.raises(ValueError):
        score(dataset, batch)


def test_unexpected_observed_capability_is_a_scored_error_not_a_dropped_case():
    dataset, batch = inputs()
    batch["cases"][0]["steps"][0]["actions"] = [action("unregistered", id=7)]
    report = score(dataset, batch)
    assert report.s1.n == report.s1.a == 1 and report.s1.c == 0
    assert report.difference_counts["release_function_versions"] == 1


def test_report_preserves_ablation_versions_and_is_deterministic_without_mutating_inputs():
    dataset, batch = inputs()
    original = deepcopy((dataset, batch))
    report = score(dataset, batch)
    assert report == score(dataset, batch)
    assert (dataset, batch) == original
    assert report.configuration.ablation == "ontology"
    assert report.configuration.release_id == "synthetic-release"
    assert report.configuration.model_parameters == {"temperature": 0}
    assert report.scorer_version == "gold-exact-v1" and report.dataset.version == "1"
    assert report.evidence_kind == "development" and report.acceptance_evidence is False
    assert report.model_dump_json()


def test_declared_development_and_sealed_families_cannot_overlap():
    development, _ = inputs()
    sealed, _ = inputs([case("sealed-case")])
    sealed["reference"].update(dataset_id="sealed-synthetic-contract-example", purpose="sealed")
    sealed["annotation"].update(provenance="business_reviewed", review_ref="synthetic-review-ref", frozen_ref="synthetic-freeze-ref")
    dev = EvaluationDataset.model_validate(development)
    validate_dataset_separation(dev, EvaluationDataset.model_validate(sealed))
    sealed["scenarios"][0]["split_group"] = "one"
    with pytest.raises(ValueError, match="families overlap"):
        validate_dataset_separation(dev, EvaluationDataset.model_validate(sealed))


@pytest.mark.parametrize("problem", ["nan", "unsafe_gold", "missing_gold", "empty_dataset"])
def test_strict_gold_cannot_assume_or_invent_missing_expectations(problem):
    dataset, batch = inputs()
    if problem == "nan":
        dataset["scenarios"][0]["steps"][0]["expected"]["routes"][0]["actions"][0]["arguments"]["id"] = float("nan")
    elif problem == "unsafe_gold":
        dataset["scenarios"] = [boundary("authorization")]
        dataset["scenarios"][0]["steps"][0]["expected"]["execution"] = execution()
    elif problem == "missing_gold":
        del dataset["scenarios"][0]["steps"][0]["expected"]["execution"]
    else:
        dataset["scenarios"] = []
    with pytest.raises(ValidationError):
        score(dataset, batch)


def test_json_contract_round_trip_and_model_revalidation():
    dataset, batch = inputs()
    data = EvaluationDataset.model_validate(dataset)
    observations = ObservationBatch.model_validate(batch)
    expected = score_evaluation(data, observations)
    actual = score_evaluation(EvaluationDataset.model_validate_json(data.model_dump_json()),
                              ObservationBatch.model_validate_json(observations.model_dump_json()))
    assert actual == expected
    assert EvaluationReport.model_validate_json(actual.model_dump_json()) == actual
    data.scenarios.append(data.scenarios[0])
    with pytest.raises(ValidationError, match="Duplicate case ID"):
        score_evaluation(data, observations)


def test_configured_functions_without_gold_are_visible_and_not_filled_with_samples():
    dataset, batch = inputs()
    report = score(dataset, batch)
    assert report.total_cases == report.s1.n == 1
    assert set(report.per_function) == {"a", "b", "c", "d"}
    assert report.per_function["b"].n == 0
    assert report.per_function["b"].correct_route_rate.value is None
    assert report.per_function_macro_route_accuracy == 1
    assert any("without an S1 scoring cohort: b, c, d" in warning for warning in report.warnings)


def test_unknown_safety_observations_are_not_assumed_to_be_zero():
    dataset, batch = inputs()
    del batch["cases"][0]["steps"][0]["unauthorized_executions"]
    batch["cases"][0]["steps"][0]["leakage_events"] = None
    batch["configuration"]["model_version"] = None
    report = score(dataset, batch)
    assert report.unobserved_safety_steps == 1 and report.correct_cases.value == 0
    assert report.cases[0].steps[0].observation.unauthorized_executions is None
    assert report.s1.c == 1 and report.s1.end_to_end_success.value == 0
    assert report.configuration.model_version is None
    assert any("not zero incidents" in warning for warning in report.warnings)
