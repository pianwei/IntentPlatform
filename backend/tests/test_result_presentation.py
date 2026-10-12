from copy import deepcopy

from intent_platform.result_presentation import (
    content_value,
    remove_result_components,
    response_intro,
    response_mode,
    response_mode_for_rows,
)


def test_legacy_markdown_wrapper_is_unwrapped_from_the_unique_approved_field():
    definition = {
        "execution_kind": "composite",
        "output_schema": {"type": "object", "properties": {
            "brief": {"type": "object", "properties": {
                "title": {"type": "string"}, "markdown": {"type": "string"}},
                "required": ["markdown"], "additionalProperties": False}},
            "required": ["brief"], "additionalProperties": False},
        "presentation": {"component": "markdown"},
    }
    data = {"brief": {"title": "访前准备", "markdown": "# 访前准备\n\n## 客户事实"}}
    assert response_mode(definition) == "content"
    assert content_value(data, definition) == data["brief"]["markdown"]


def test_structured_rows_are_not_sent_through_model_summary_and_are_displayed_once():
    capabilities = [{"capability_id": "search", "definition": {
        "name": "筛选与定位客户", "execution_kind": "direct",
        "output_schema": {"type": "object", "properties": {
            "customers": {"type": "array", "items": {"type": "object"}}}},
        "presentation": {"component": "detail"}}}]
    rows = [{"capability_id": "search", "name": "筛选与定位客户", "data": {"customers": [{"name": "星澜科技"}, {"name": "远川制造"}]}}]
    assert response_mode_for_rows(rows, capabilities) == "structured"
    assert response_intro(rows, "structured") == "查询完成，共找到 2 条记录，结果如下。"


def test_summary_mode_uses_only_the_summary_and_never_duplicates_result_cards():
    components = [{"title": "结果", "component": "detail", "data": {"value": 1}}]
    assert remove_result_components("summary", components) == []
    assert remove_result_components("structured", components) == components


def test_final_payload_wires_summary_and_structured_modes_without_duplicate_answers(monkeypatch):
    from types import SimpleNamespace
    import intent_platform.services as services
    import intent_platform.planning as planning

    components = [{"title": "筛选与定位客户", "component": "detail", "data": {"customers": [{"name": "星澜科技"}]}}]
    rows = [{"capability_id": "search", "name": "筛选与定位客户", "data": {"customers": [{"name": "星澜科技"}]}}]
    definition = {"name": "筛选与定位客户", "execution_kind": "direct",
                  "output_schema": {"type": "object", "properties": {
                      "customers": {"type": "array", "items": {"type": "object"}}}},
                  "presentation": {"component": "detail"}}
    plan = SimpleNamespace(release_id="release")
    release = SimpleNamespace(snapshot={"capabilities": [{"capability_id": "search", "definition": definition}]})
    db = SimpleNamespace(scalar=lambda _query: plan, get=lambda _cls, _key: release)
    monkeypatch.setattr(services, "final_components", lambda *_args: components)
    monkeypatch.setattr(services, "final_files", lambda *_args: [])
    monkeypatch.setattr(planning, "partial_result_rows", lambda *_args: rows)
    run = SimpleNamespace(id="run", status="SUCCEEDED")

    payload = services.final_payload(db, run, "查询完成，共找到1条。", False)
    assert payload["data"] == "查询完成，共找到1条。"
    assert payload["components"] == components

    definition["presentation"]["response_mode"] = "summary"
    payload = services.final_payload(db, run, "查询完成，共找到1条。", False)
    assert payload["components"] == []


def test_public_zhiyu_model_allowlist_keeps_old_response_shapes_and_rejects_policy_edits():
    from intent_platform.public_demo import assert_synthetic_snapshot
    from intent_platform.synthetic_runtime import catalog_metadata
    from intent_platform.zhiyu import function_packages

    ref = {"package_id": "approved-domain", "version": 1}
    packages = function_packages(ref, three_layer=True)
    snapshot = {"synthetic_catalog": catalog_metadata(),
                "capabilities": [{"capability_id": f"cap-{i}", "definition": item["definition"]}
                                 for i, item in enumerate(packages)],
                "business_skills": [], "action_definitions": [], "domain_packages": []}
    assert_synthetic_snapshot(snapshot)

    legacy = deepcopy(snapshot)
    for capability in legacy["capabilities"]:
        definition = capability["definition"]
        presentation = definition["presentation"]
        presentation.pop("response_mode", None)
        presentation.pop("content_path", None)
        if "markdown" in (definition.get("output_schema") or {}).get("properties", {}):
            presentation["component"] = "detail"
    assert_synthetic_snapshot(legacy)

    tampered = deepcopy(legacy)
    tampered["capabilities"][0]["definition"]["presentation"]["response_mode"] = "summary"
    import pytest
    with pytest.raises(ValueError, match="unchanged approved synthetic definitions"):
        assert_synthetic_snapshot(tampered)
