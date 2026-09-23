"""Finite output projection and OpenAPI response inference (synthetic data)."""
import json

import pytest

from intent_platform.openapi_import import parse_openapi
from intent_platform.output_contract import check_output_contract, project_output, OutputContractError
from test_openapi_parser import specification


def definition():
    return {"output_schema": {"type": "object", "properties": {
                "summary": {"type": "string", "maxLength": 100},
                "rows": {"type": "array", "maxItems": 2, "items": {"type": "object", "properties": {
                    "id": {"type": "string", "maxLength": 40}, "label": {"type": "string", "maxLength": 80}},
                    "required": ["id", "label"], "additionalProperties": False}}},
                "required": ["summary", "rows"], "additionalProperties": False},
            "presentation": {"field_map": {"Summary": "summary", "Rows": "rows"}, "files_field": "files"},
            "result_bindings": [{"collection_path": "rows", "id_field": "id", "label_field": "label"}]}


def test_approved_projection_discards_unmapped_nested_fields_and_keeps_bindings():
    d = definition()
    assert check_output_contract(d)
    data, files = project_output(d, {"summary": "approved", "rows": [{"id": "local-1", "label": "visible", "secret": "hidden"}],
                                     "secret": "hidden", "files": [{"filename": "a.txt", "content_base64": "YQ=="}]})
    assert data == {"summary": "approved", "rows": [{"id": "local-1", "label": "visible"}]}
    assert files == [{"filename": "a.txt", "content_base64": "YQ=="}]


@pytest.mark.parametrize("bad", [
    {"summary": "x"},
    {"summary": 4, "rows": []},
    {"summary": "x", "rows": [{"id": "a", "label": "b"}] * 3},
    {"summary": "x", "rows": [{"id": "a", "label": 3}]},
])
def test_missing_wrong_type_oversized_list_and_invalid_row_have_no_raw_error(bad):
    bad["private"] = "secret-marker"
    with pytest.raises(OutputContractError) as exc:
        project_output(definition(), bad)
    assert "secret-marker" not in str(exc.value)


def test_empty_contract_and_unapproved_binding_are_not_publishable():
    d = definition()
    d["output_schema"]["properties"] = {}; d["output_schema"]["required"] = []
    d["presentation"] = {"field_map": {}}
    d["result_bindings"] = []
    with pytest.raises(ValueError, match="at least one"):
        check_output_contract(d)
    d = definition(); d["output_schema"]["properties"]["rows"]["items"]["properties"].pop("id")
    d["output_schema"]["properties"]["rows"]["items"]["required"] = ["label"]
    with pytest.raises(ValueError, match="Result bindings"):
        check_output_contract(d)
    d = definition(); d["output_schema"]["required"] = [{"bad": "unhashable"}]
    with pytest.raises(ValueError, match="properties/required"):
        check_output_contract(d)
    d = definition(); d["result_bindings"] = ["bad-binding"]
    with pytest.raises(ValueError, match="Result bindings"):
        check_output_contract(d)
    d = definition(); d["output_schema"]["properties"]["summary"]["enum"] = [{"not": "a scalar"}]
    with pytest.raises(ValueError, match="enum"):
        check_output_contract(d)


def test_response_schema_creates_bounded_draft_and_review_facts():
    source = specification(); response = source["paths"]["/items/{item}"]["post"]["responses"]["200"]
    response["content"] = {"application/json": {"schema": {"type": "object", "properties": {
        "message": {"type": "string"}, "count": {"type": "integer"}}, "required": ["message"], "additionalProperties": False}}}
    operation = parse_openapi(json.dumps(source))["operations"][0]
    assert operation["supported"] and not any(d["code"] == "output_review_required" for d in operation["diagnostics"])
    assert operation["definition"]["output_schema"]["properties"]["message"]["maxLength"] == 4096
    assert operation["definition"]["presentation"]["field_map"] == {"message": "message", "count": "count"}
    assert operation["facts"]["output_schema"]["source_pointer"].endswith("/responses/200/content/application~1json/schema")


def test_missing_or_unsupported_response_is_reviewable_draft_not_fabricated_schema():
    source = specification()
    operation = parse_openapi(json.dumps(source))["operations"][0]
    assert operation["supported"] and operation["definition"]["output_schema"] is None
    assert any(d["code"] == "output_review_required" and d["severity"] == "review" for d in operation["diagnostics"])
    source["paths"]["/items/{item}"]["post"]["responses"]["200"]["content"] = {"application/json": {"schema": {"oneOf": [{"type": "object"}]}}}
    unsupported = parse_openapi(json.dumps(source))["operations"][0]
    assert unsupported["supported"] and unsupported["definition"]["output_schema"] is None
    source["paths"]["/items/{item}"]["post"]["responses"]["200"]["content"] = {"application/json": {"schema": {"type": "object", "properties": ["wrong container"]}}}
    malformed = parse_openapi(json.dumps(source))["operations"][0]
    assert malformed["supported"] and malformed["definition"]["output_schema"] is None


def test_file_field_is_explicit_and_cannot_be_display_data():
    d = definition(); d["output_schema"]["properties"]["files"] = {"type": "string", "maxLength": 100}
    d["presentation"]["field_map"]["Files"] = "files"
    with pytest.raises(ValueError, match="Inline files"):
        check_output_contract(d)
