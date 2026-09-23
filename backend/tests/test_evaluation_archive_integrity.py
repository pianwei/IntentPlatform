"""An archive must preserve scoring meaning while redacting only wire evidence."""
import json
from datetime import datetime, timezone
import pytest
from intent_platform.evaluation_cli import archive_trial
from intent_platform.evaluation_runner import EvaluationTrial
from intent_platform.evaluation_scoring import score_evaluation, EvaluationReport
from intent_platform.evaluation_spec import EvaluationDataset, ObservationBatch
from test_evaluation_scoring import inputs, case, action, gold


def trial(dataset, observations, secrets=()):
    d, o = EvaluationDataset.model_validate(dataset), ObservationBatch.model_validate(observations)
    return EvaluationTrial(d, o, score_evaluation(d, o), [{"headers": {"Authorization": "Bearer wire-only", "Proxy-Authorization": "Basic opaque-wire"}}], {}, [], datetime.now(timezone.utc).isoformat(), secrets)


def load_and_rescore(path):
    saved = json.loads(path.read_text(encoding="utf-8"))
    d = EvaluationDataset.model_validate(saved["dataset"])
    o = ObservationBatch.model_validate(saved["observations"])
    r = EvaluationReport.model_validate(saved["report"])
    assert score_evaluation(d, o) == r
    return saved, r


def test_token_count_business_mismatch_survives_archive(tmp_path):
    dataset, batch = inputs([case(expected=gold(actions=[action("a", token_count=1)]))])
    batch["cases"][0]["steps"][0]["actions"][0]["arguments"]["token_count"] = 2
    original = trial(dataset, batch)
    saved, report = load_and_rescore(archive_trial(tmp_path, original))
    assert saved["dataset"]["scenarios"][0]["steps"][0]["expected"]["routes"][0]["actions"][0]["arguments"]["token_count"] == 1
    assert saved["observations"]["cases"][0]["steps"][0]["actions"][0]["arguments"]["token_count"] == 2
    assert report == original.report and report.s1.end_to_end_success.numerator == 0
    assert "wire-only" not in json.dumps(saved) and "opaque-wire" not in json.dumps(saved)
    assert saved["sanitization"]["unknown_secrets_checked"] is False


def test_public_credential_reference_id_remains_typed(tmp_path):
    dataset, batch = inputs([case(capability="credential_ref", expected=gold(actions=[action("credential_ref", api_key_count=2)]))])
    batch["configuration"]["function_versions"] = {"credential_ref": 1}
    batch["configuration"]["connector_versions"] = {"credential_ref": "fixture-v1"}
    saved, report = load_and_rescore(archive_trial(tmp_path, trial(dataset, batch)))
    assert saved["observations"]["configuration"]["function_versions"] == {"credential_ref": 1}
    assert report.s1.end_to_end_success.numerator == 1


def test_known_secret_in_business_input_refuses_unmodified_archive(tmp_path):
    secret = "known-runtime-credential"
    dataset, batch = inputs([case(expected=gold(actions=[action("a", value=secret)]))])
    value = trial(dataset, batch, (secret,))
    with pytest.raises(ValueError) as error: archive_trial(tmp_path, value)
    assert secret not in str(error.value)
    assert list((tmp_path / batch["trial_id"]).iterdir()) == []


def test_supplied_report_mismatch_refuses_archive(tmp_path):
    dataset, batch = inputs(); value = trial(dataset, batch)
    value.report = value.report.model_copy(update={"total_cases": 999})
    with pytest.raises(ValueError, match="report"): archive_trial(tmp_path, value)
    assert list((tmp_path / batch["trial_id"]).iterdir()) == []
