import json
import httpx
import pytest
from intent_platform.shadow_cli import ShadowDataset, ShadowBindings, ShadowGold, observation, collect, summarize, commit_shadow, main
from intent_platform.evaluation_cli import reserve_trial


def dataset():
    return ShadowDataset.model_validate({"dataset_id": "synthetic-shadow", "version": "1", "purpose": "development",
        "cases": [{"case_id": "one", "conversation_id": "conv-1", "release_id": "release-1", "text": "SYNTHETIC prompt", "expected": {"decision": "READY", "capability_ids": ["cap-1"]}},
                  {"case_id": "two", "conversation_id": "conv-1", "release_id": "release-1", "text": "SYNTHETIC timeout", "expected": {"decision": "OUT_OF_SCOPE", "capability_ids": []}}]})


def response():
    return {"release_id": "release-1", "conversation_id": "conv-1", "reference_time": "2026-09-22T00:00:00+00:00", "profile": {},
            "candidate_ids": ["cap-1"], "context": [], "context_stats": {},
            "variants": [{"variant": v, "policy_version": "test", "outcome": "completed", "decision": "READY", "proposal": {"requests": [{"capability_id": "cap-1"}]},
                          "validated_requests": [{"capability_id": "cap-1"}], "fixture_mode": True} for v in ("A", "B", "C")]}


def test_collector_never_sends_gold_keeps_timeout_denominator_and_no_retry(monkeypatch, tmp_path):
    token = "synthetic-private-token-value"
    monkeypatch.setenv("SHADOW_TEST_TOKEN", token)
    seen = []
    def handle(request):
        body = json.loads(request.content); seen.append(body)
        assert set(body) == {"release_id", "text"} and "expected" not in body
        assert request.headers["Authorization"] == "Bearer " + token
        if len(seen) == 2: raise httpx.ReadTimeout("do not echo secret " + token)
        return httpx.Response(200, json=response())
    bindings = ShadowBindings.model_validate({"bindings": [{"conversation_id": "conv-1", "env_var": "SHADOW_TEST_TOKEN"}]})
    with httpx.Client(base_url="http://test.local", transport=httpx.MockTransport(handle)) as client:
        observed, evidence, secrets = collect(dataset(), bindings, "http://test.local", client=client)
    assert len(seen) == 2 and observed[1]["variants"][0]["outcome"] == "timeout"
    report = summarize(dataset(), observed)
    assert all(value["total"] == 2 and value["correct"] == 1 and value["rate"] == 0.5 for value in report["variants"].values())
    folder = reserve_trial(tmp_path, "shadow-local")
    path = commit_shadow(folder, dataset(), observed, evidence, secrets)
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["report"] == report and body["acceptance_evidence"] is False
    assert token not in path.read_text(encoding="utf-8")
    with pytest.raises(ValueError): commit_shadow(folder, dataset(), observed, evidence, secrets)
    with pytest.raises(FileExistsError): reserve_trial(tmp_path, "shadow-local")


@pytest.mark.parametrize("kind", ["403", "wrong_release", "dispatched", "incomplete"])
def test_invalid_server_evidence_never_becomes_route_success(monkeypatch, kind):
    monkeypatch.setenv("SHADOW_TEST_TOKEN", "synthetic-token")
    body = response()
    if kind == "wrong_release": body["release_id"] = "other"
    if kind == "dispatched": body["business_execution"] = {"status": "SUCCEEDED", "count": 1}
    if kind == "incomplete": body["variants"] = body["variants"][:1]
    with httpx.Client(base_url="http://test.local", transport=httpx.MockTransport(lambda request: httpx.Response(403 if kind == "403" else 200, json=body))) as client:
        observed, _, _ = collect(dataset(), ShadowBindings.model_validate({"bindings": [{"conversation_id": "conv-1", "env_var": "SHADOW_TEST_TOKEN"}]}), "http://test.local", client=client)
    assert all(arm["outcome"] == "error" for case in observed for arm in case["variants"])
    assert summarize(dataset(), observed)["variants"]["B"]["correct"] == 0


def test_secret_in_authoritative_input_refuses_archive_and_wire_is_redacted(tmp_path):
    source = dataset(); source.cases[0].text = "known-secret"
    folder = reserve_trial(tmp_path, "contaminated")
    with pytest.raises(ValueError, match="Known secret"): commit_shadow(folder, source, [], [], ["known-secret"])
    assert not (folder / "shadow.json").exists()
    source.cases[0].text = "safe"
    folder = reserve_trial(tmp_path, "wire")
    path = commit_shadow(folder, source, [], [{"Authorization": "Bearer known-secret", "note": "known-secret"}], ["known-secret"])
    assert "known-secret" not in path.read_text(encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["report"]["denominator"] == 2


def test_cli_sealed_refusal_and_safe_errors_never_echo_tokens(tmp_path, capsys):
    source = dataset().model_dump(); source.update(purpose="sealed", review_ref="review", frozen_ref="freeze")
    source_path = tmp_path / "dataset.json"; source_path.write_text(json.dumps(source))
    binding_path = tmp_path / "bindings.json"; binding_path.write_text(json.dumps({"bindings": [{"conversation_id": "conv-1", "env_var": "MISSING_TOKEN"}]}))
    result = main(["--dataset", str(source_path), "--bindings", str(binding_path), "--base-url", "http://127.0.0.1:1", "--trial-id", "sealed", "--output", str(tmp_path / "out")])
    assert result == 2 and not (tmp_path / "out").exists()
    assert "MISSING_TOKEN" not in capsys.readouterr().err


def test_route_metric_is_a_unique_capability_set_for_independent_requests():
    actual = observation({"variant": "B", "outcome": "completed", "decision": "READY", "validated_requests": [{"capability_id": "cap-1"}, {"capability_id": "cap-1"}]})
    assert actual["capability_ids"] == ["cap-1"]
    assert summarize(dataset(), [{"case_id": "one", "variants": [actual]}])["variants"]["B"]["correct"] == 1
    with pytest.raises(ValueError, match="duplicates"):
        ShadowGold.model_validate({"decision": "READY", "capability_ids": ["cap-1", "cap-1"]})


@pytest.mark.parametrize("options,correct", [(["cap-1", "cap-2"], 1), (["cap-1", "wrong"], 0)])
def test_function_ambiguity_compares_actual_stable_candidate_ids(options, correct):
    source = dataset(); source.cases[0].expected = ShadowGold(decision="AMBIGUOUS", capability_ids=["cap-1", "cap-2"])
    actual = observation({"variant": "B", "outcome": "completed", "decision": "AMBIGUOUS", "proposal": {"requests": []},
                          "question": {"kind": "function_choice", "options": [{"id": item, "label": item} for item in options]}})
    assert summarize(source, [{"case_id": "one", "variants": [actual]}])["variants"]["B"]["correct"] == correct


@pytest.mark.parametrize("kind", ["entity_choice", "history_choice"])
def test_entity_history_ambiguity_never_scores_opaque_ids_as_capabilities(kind):
    source = dataset(); source.cases[0].expected = ShadowGold(decision="AMBIGUOUS", capability_ids=["cap-1"])
    actual = observation({"variant": "B", "outcome": "completed", "decision": "AMBIGUOUS", "proposal": {"requests": [{"capability_id": "cap-1"}]},
                          "question": {"kind": kind, "options": [{"id": "choice-opaque-one", "label": "one"}, {"id": "choice-opaque-two", "label": "two"}]}})
    assert actual["capability_ids"] == ["cap-1"]
    assert summarize(source, [{"case_id": "one", "variants": [actual]}])["variants"]["B"]["correct"] == 1


@pytest.mark.parametrize("decision,question", [("AMBIGUOUS", {"kind": "function_choice", "options": []}), ("AMBIGUOUS", {}), ("READY", None)])
def test_missing_selected_or_ambiguity_candidates_cannot_match_empty_gold(decision, question):
    source = dataset(); source.cases[0].expected = ShadowGold(decision=decision, capability_ids=[])
    actual = observation({"variant": "B", "outcome": "completed", "decision": decision, "question": question, "proposal": {"requests": []}, "validated_requests": []})
    assert actual["outcome"] == "error"
    assert summarize(source, [{"case_id": "one", "variants": [actual]}])["variants"]["B"]["correct"] == 0
