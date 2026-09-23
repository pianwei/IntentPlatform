"""Finite OpenAPI documents are synthetic protocol fixtures, not bank definitions."""
import copy
import json
import pytest
import yaml
from intent_platform.openapi_import import parse_openapi
from intent_platform.http_v2 import wire_request, validate_mapping


def specification(origin="http://127.0.0.1:9999"):
    return {"openapi": "3.0.3", "info": {"title": "Local source fixture", "version": "1"}, "servers": [{"url": origin + "/test"}],
            "components": {"schemas": {"Amount": {"type": "number", "minimum": 0}}},
            "paths": {"/items/{item}": {"parameters": [{"name": "item", "in": "path", "required": True, "schema": {"type": "string"}}],
                     "post": {"operationId": "localUpdate", "summary": "Synthetic API operation",
                     "parameters": [{"name": "category", "in": "query", "required": True, "schema": {"type": "string"}}, {"name": "X-Trace", "in": "header", "schema": {"type": "string"}}],
                     "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "properties": {"amount": {"$ref": "#/components/schemas/Amount"}, "fail": {"type": "boolean"}}, "required": ["amount", "fail"], "additionalProperties": False}}}},
                     "responses": {"200": {"description": "Business result requires explicit mapping"}}}}}}


@pytest.mark.parametrize("encoding", ["json", "yaml"])
def test_local_refs_and_all_explicit_positions(encoding):
    source = specification(); content = json.dumps(source) if encoding == "json" else yaml.safe_dump(source, sort_keys=False)
    report = parse_openapi(content)
    assert report["valid"], report
    operation = report["operations"][0]; definition = operation["definition"]
    assert operation["source_pointer"] == "#/paths/~1items~1{item}/post"
    assert operation["facts"]["input_schema"]["ref_targets"] == ["#/components/schemas/Amount"]
    url, query, headers, body = wire_request(definition, {"path_item": "safe item", "query_category": "local", "header_X-Trace": "trace", "body_amount": 3.5, "body_fail": False})
    assert url.endswith("/items/safe%20item") and query == {"category": "local"} and headers == {"X-Trace": "trace"} and body == {"amount": 3.5, "fail": False}


@pytest.mark.parametrize("bad", ["remote", "cycle", "missing", "sibling", "depth", "polymorphism", "cookie", "header", "nested", "media", "path_required", "version", "server_variable"])
def test_unsupported_source_or_operation_has_diagnostics(bad):
    doc = specification(); operation = doc["paths"]["/items/{item}"]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    if bad == "remote": doc["components"]["schemas"]["Amount"] = {"$ref": "https://example.test/schema"}
    if bad == "cycle": doc["components"]["schemas"]["Amount"] = {"$ref": "#/components/schemas/Amount"}
    if bad == "missing": doc["components"]["schemas"]["Amount"] = {"$ref": "#/missing"}
    if bad == "sibling": doc["components"]["schemas"]["Amount"] = {"$ref": "#/info", "description": "ignored sibling"}
    if bad == "depth":
        value = 0
        for _ in range(45): value = {"level": value}
        doc["x-deep"] = value
    if bad == "polymorphism": schema["properties"]["amount"] = {"oneOf": [{"type": "string"}, {"type": "number"}]}
    if bad == "cookie": operation["parameters"][0]["in"] = "cookie"
    if bad == "header": operation["parameters"][1]["name"] = "Authorization"
    if bad == "nested": schema["properties"]["amount"] = {"type": "object", "properties": {"x": {"type": "string"}}}
    if bad == "media": operation["requestBody"]["content"] = {"text/plain": {"schema": {"type": "string"}}}
    if bad == "path_required": doc["paths"]["/items/{item}"]["parameters"][0]["required"] = False
    if bad == "version": doc["openapi"] = "3.1.0"
    if bad == "server_variable": doc["servers"][0]["variables"] = {"host": {"default": "x"}}
    report = parse_openapi(json.dumps(doc))
    assert not report["valid"] and (report["diagnostics"] or report["operations"][0]["diagnostics"])


@pytest.mark.parametrize("source", ['{"openapi":"3.0.3","openapi":"3.0.3"}', "openapi: 3.0.3\nopenapi: 3.0.3", "x: &anchor [1,2]\ny: *anchor", "x: !!python/object:os.system {}", "x: .inf"])
def test_duplicate_keys_aliases_tags_and_nonfinite_rejected(source):
    assert parse_openapi(source)["valid"] is False


@pytest.mark.parametrize("segment", ["..", ".", "a/b", "%2fadmin", "%252fadmin", "a\\b", "a\r\nHost:x", ""])
def test_path_substitution_cannot_escape_segment(segment):
    definition = parse_openapi(json.dumps(specification()))["operations"][0]["definition"]
    with pytest.raises(ValueError): wire_request(definition, {"path_item": segment})


def test_mapping_rejects_silent_argument_loss_or_dangerous_headers():
    definition = parse_openapi(json.dumps(specification()))["operations"][0]["definition"]
    missing = copy.deepcopy(definition); missing["connector"]["http_mapping"]["query"] = {}
    with pytest.raises(ValueError, match="exactly one"): validate_mapping(missing)
    for name in ("Authorization", "Host", "Cookie", "X-Forwarded-For", "Content-Length", "Content-Type", "Origin"):
        bad = copy.deepcopy(definition); bad["connector"]["http_mapping"]["headers"] = {name: "header_X-Trace"}
        with pytest.raises(ValueError): validate_mapping(bad)


@pytest.mark.parametrize("version", ["1", "2"])
def test_shared_server_headers_and_transport_cannot_override_host(version):
    from intent_platform.ports import connector_checks
    from intent_platform.network import request_json, request_json_parts
    definition = parse_openapi(json.dumps(specification()))["operations"][0]["definition"]
    definition["connector"]["version"] = version
    for key in ("authorization_header", "idempotency_header"):
        for value in ("Host", "Content-Length", "X-Forwarded-Host", "Connection", "Transfer-Encoding", "Proxy-Authorization"):
            bad = copy.deepcopy(definition); bad["connector"][key] = value
            with pytest.raises(ValueError, match="Server header"): connector_checks(bad, "ws-dev")
    for transport in (request_json, request_json_parts):
        with pytest.raises(ValueError, match="transport"): transport("GET", "https://not-authorized.invalid", headers={"hOsT": "other"})


def test_ref_text_expansion_and_report_budget_are_bounded(monkeypatch):
    import intent_platform.openapi_import as parser
    doc = specification(); doc["components"]["schemas"]["Amount"]["description"] = "large description " * 600
    body = doc["paths"]["/items/{item}"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    body["properties"] = {f"value_{i}": {"$ref": "#/components/schemas/Amount"} for i in range(20)}; body["required"] = []
    operation = doc["paths"]["/items/{item}"]
    doc["paths"] = {f"/items{i}/{{item}}": copy.deepcopy(operation) for i in range(20)}
    for value in doc["paths"].values(): value["post"].pop("operationId")
    assert not parse_openapi(json.dumps(doc))["valid"]
    monkeypatch.setattr(parser, "MAX_REPORT_BYTES", 1)
    report = parse_openapi(json.dumps(specification()))
    assert report["valid"] is False and report["diagnostics"][0]["code"] == "expansion_limit"


def test_long_source_lines_and_array_pointer_refs_remain_exact():
    from intent_platform.document_versions import sections
    text = "x" * 13_000
    chunks = sections(text)
    assert "".join(s["text"] for s in chunks) == text and all(s["start_line"] == s["end_line"] == 1 for s in chunks)
    doc = specification(); doc["x-schemas"] = [{"type": "number"}]
    doc["components"]["schemas"]["Amount"] = {"$ref": "#/x-schemas/0"}
    assert parse_openapi(json.dumps(doc))["valid"]
