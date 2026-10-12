from copy import deepcopy
import pytest
from intent_platform.planning import validate_plan, consumed_results
from intent_platform.planning_api import SkillBody
from intent_platform.composite_planning import expand


def test_quantity_units_are_configurable_and_checked_before_any_execution():
    definition = {"name": "numeric", "effect": "READ_ONLY", "requires_confirmation": False,
        "input_schema": {"type": "object", "properties": {"amount": {"type": "number", "minimum": 1000}}, "required": ["amount"], "additionalProperties": False},
        "compiler_version": "finite-json-v2", "parameter_semantics": {"amount": {"kind": "quantity", "unit": "CNY", "allow_relative": False}}}
    candidate = {"capability_id": "quantity", "definition": definition, "compiled": {"domain_projection": {"conversions": {"WAN_CNY->CNY": 10000}}}}
    raw = {"inputs": {}, "steps": [{"step_id": "read", "capability_id": "quantity", "arguments": {"amount": 2}, "parameter_units": {"amount": "WAN_CNY"}, "depends_on": []}]}
    assert validate_plan(raw, [candidate], "2万元").steps[0].parameter_units == {"amount": "WAN_CNY"}
    for units in ({}, {"amount": "USD"}, {"amount": "CNY", "undeclared": "CNY"}):
        bad = deepcopy(raw); bad["steps"][0]["parameter_units"] = units
        with pytest.raises(ValueError): validate_plan(bad, [candidate], "2")
    body = SkillBody(key="quantity", name="单位读取", description="数字单位规则", steps=raw["steps"])
    assert body.steps[0].parameter_units == {"amount": "WAN_CNY"}


def test_order_dependencies_keep_outputs_and_expanded_composites_finite():
    spec = {"inputs": {}, "steps": [
        {"step_id": "first", "capability_id": "one", "arguments": {}, "depends_on": []},
        {"step_id": "second", "capability_id": "two", "arguments": {}, "depends_on": ["first"]}]}
    assert consumed_results(spec) == set()
    spec["steps"][1]["arguments"] = {"customer": {"$step": "first", "path": "data.customer_id"}}
    assert consumed_results(spec) == {"first"}
    projection = {"composites": {"one": {"plan": {"steps": [{"step_id": "leaf", "capability_id": "hidden", "arguments": {}, "depends_on": []}]}, "final_output_mapping": {"answer": {"$step": "leaf", "path": "data"}}}}}
    spec["steps"][1]["arguments"] = {}
    expanded, groups = expand(spec, projection)
    assert groups[0]["visible"] and expanded["steps"][1]["depends_on"] == ["c0_leaf"]
    assert groups[0]["mapping"] == {"answer": {"$step": "c0_leaf", "path": "data"}}


def test_explicit_result_steps_keep_requested_consumed_output_without_exposing_macro_internals():
    from intent_platform.planning import visible_results, public_result_rows
    from intent_platform.planning_schemas import ExecutionPlanSpec
    spec = {"inputs": {}, "steps": [
        {"step_id": "first", "capability_id": "one", "arguments": {}, "depends_on": []},
        {"step_id": "second", "capability_id": "two", "arguments": {"x": {"$step": "first", "path": "data.answer"}}, "depends_on": ["first"]}]}
    assert visible_results(spec) == {"second"}
    assert visible_results({**spec, "result_steps": None}) == {"second"}
    spec["result_steps"] = ["first", "second"]
    assert visible_results(ExecutionPlanSpec.model_validate(spec).model_dump()) == {"first", "second"}
    projection = {"composites": {"one": {"plan": {"steps": [{"step_id": "leaf", "capability_id": "hidden", "arguments": {}, "depends_on": []}]}, "final_output_mapping": {"answer": {"$step": "leaf", "path": "data.allowed"}}}}}
    expanded, groups = expand(spec, projection)
    assert groups[0]["visible"] and expanded["result_steps"] == ["c0_leaf", "second"]
    assert groups[0]["mapping"] == {"answer": {"$step": "c0_leaf", "path": "data.allowed"}}
    for selected in (["missing"], ["first", "first"], []):
        with pytest.raises(ValueError): ExecutionPlanSpec.model_validate({**spec, "result_steps": selected})
    assert public_result_rows([{"capability_id": "hidden-id", "step": "hidden-step", "name": "title", "data": {"ok": 1}}]) == [{"name": "title", "data": {"ok": 1}}]


def test_same_name_results_use_exact_versioned_presentation(monkeypatch):
    from types import SimpleNamespace
    from intent_platform.services import final_components
    definitions = [{"capability_id": key, "definition": {"name": "same", "presentation": {"component": "detail", "title": key, "field_map": {key: key}}}} for key in ("first", "second")]
    db = SimpleNamespace(scalar=lambda query: SimpleNamespace(release_id="r"), get=lambda cls, key: SimpleNamespace(snapshot={"capabilities": definitions}))
    monkeypatch.setattr("intent_platform.planning.partial_result_rows", lambda *args: [{"capability_id": key, "name": "same", "data": {key: value}} for key, value in (("first", 1), ("second", 2))])
    results = final_components(db, "run")
    assert results[0]["fields"] == [{"label": "first", "value": 1}]
    assert results[1]["fields"] == [{"label": "second", "value": 2}]
    assert all("capability_id" not in row for row in results)


def test_markdown_composite_component_receives_the_declared_string_not_its_json_wrapper(monkeypatch):
    from types import SimpleNamespace
    from intent_platform.services import final_components
    definitions = [{"capability_id": "visit", "definition": {
        "name": "访前准备", "execution_kind": "composite",
        "output_schema": {"type": "object", "properties": {"brief": {"type": "object", "properties": {
            "markdown": {"type": "string"}}, "required": ["markdown"]}}},
        "presentation": {"component": "markdown", "response_mode": "content", "content_path": "brief.markdown",
                         "title": "访前准备", "field_map": {"brief": "brief"}},
    }}]
    db = SimpleNamespace(scalar=lambda query: SimpleNamespace(release_id="r"),
                         get=lambda cls, key: SimpleNamespace(snapshot={"capabilities": definitions}))
    markdown = "# 星澜科技访前一页纸\n\n## 客户事实"
    monkeypatch.setattr("intent_platform.planning.partial_result_rows", lambda *args: [
        {"capability_id": "visit", "name": "访前准备", "data": {"brief": {"markdown": markdown}}}])
    result = final_components(db, "run")
    assert result == [{"title": "访前准备", "component": "markdown", "data": markdown, "fields": []}]
