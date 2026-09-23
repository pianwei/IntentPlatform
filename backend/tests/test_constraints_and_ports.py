import json
import pytest
from pydantic import ValidationError
from intent_platform.config import Settings, settings
from intent_platform.ontology import compile_definition, validate_pair
from intent_platform.schemas import Definition, Proposal
from intent_platform.ports import connector_checks, execute_connector, credential, target_authorized, route_model


@pytest.mark.parametrize("value,ok", [(1, True), (1.0, True), (1.2, False), (True, False), (None, False), ("1", False)])
def test_integer_constraints_match(value, ok):
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"], "additionalProperties": False}
    py, rdf, _ = validate_pair(schema, {"count": value})
    assert py is ok and rdf is ok


def test_null_missing_array_duplicates_and_unsupported_constraints():
    schema = {"type": "object", "properties": {"name": {"type": ["string", "null"], "minLength": 2},
              "values": {"type": "array", "items": {"type": "integer"}, "minItems": 2}}, "required": ["name", "values"], "additionalProperties": False}
    for case, expected in [({"name": None, "values": [1, 1]}, True), ({"values": [1, 1]}, False),
                           ({"name": "xx", "values": [1]}, False), ({"name": "x", "values": [1, 1]}, False),
                           ({"name": "xx", "values": 1}, False)]:
        assert validate_pair(schema, case)[:2] == (expected, expected)
    definition = Definition(name="case", description="test", input_schema={"type": "object", "$ref": "http://untrusted"}).model_dump()
    with pytest.raises(ValueError):
        compile_definition(definition)


def test_production_rejects_development_and_sqlite():
    with pytest.raises(ValidationError): Settings(env="production", allow_dev_auth=True, model_mode="fixture")
    with pytest.raises(ValidationError): Settings(database_url="sqlite://")


def test_real_business_authorization_denies_before_connector(monkeypatch):
    monkeypatch.setattr(settings(), "object_authorization_url", "http://127.0.0.1/authorization")
    monkeypatch.setattr(settings(), "connector_targets_json", json.dumps({"ws-a": [{"origin": "http://127.0.0.1:12345", "path_prefix": "/", "kinds": ["http"]}]}))
    outbound = []
    def fake(method, url, **kwargs):
        outbound.append(url)
        return {"allowed": False}
    monkeypatch.setattr("intent_platform.ports.request_json", fake)
    d = Definition(name="x", description="x", connector={"kind": "http", "endpoint": "http://127.0.0.1:12345/business"}).model_dump()
    with pytest.raises(PermissionError): execute_connector(d, {}, "call", "user", "ws-a")
    assert outbound == ["http://127.0.0.1/authorization"]


def test_credentials_and_endpoint_are_workspace_scoped(monkeypatch):
    cfg = settings()
    connector = Definition(name="x", description="x", connector={"kind": "http", "endpoint": "http://127.0.0.1:12345/approved/item"}).connector.model_dump()
    monkeypatch.setattr(cfg, "credentials_json", json.dumps({"ref": {"value": "test-only", "workspace_ids": ["ws-a"], "connector_kind": "http", "endpoint": connector["endpoint"]}}))
    assert credential("ref", "ws-a", connector) == "test-only"
    with pytest.raises(ValueError): credential("ref", "ws-a", {**connector, "status_endpoint": "http://127.0.0.1:12345/collector/{operation_id}"})
    with pytest.raises(ValueError): credential("ref", "ws-b", connector)
    with pytest.raises(ValueError): credential("ref", "ws-a", {**connector, "endpoint": "http://127.0.0.1:12345/approved/other"})
    monkeypatch.setattr(cfg, "connector_targets_json", json.dumps({"ws-a": [{"origin": "http://127.0.0.1:12345", "path_prefix": "/approved", "kinds": ["http"]}]}))
    target_authorized("ws-a", connector, connector["endpoint"])
    for ws, endpoint in [("ws-b", connector["endpoint"]), ("ws-a", "http://127.0.0.1:12346/approved/item"), ("ws-a", "http://127.0.0.1:12345/admin"), ("ws-a", "http://127.0.0.1:12345/approved/%2e%2e/admin")]:
        with pytest.raises(ValueError): target_authorized(ws, connector, endpoint)


def test_pending_success_condition_and_failure_status(monkeypatch):
    cfg = settings()
    monkeypatch.setattr(cfg, "connector_targets_json", json.dumps({"ws-a": [{"origin": "http://127.0.0.1:12345", "path_prefix": "/", "kinds": ["http"]}]}))
    d = Definition(name="x", description="x", connector={"kind": "http", "endpoint": "http://127.0.0.1:12345/run", "status_path": "status", "operation_id_path": "operation"}, success_condition={"field": "done", "operator": "eq", "value": True}).model_dump()
    monkeypatch.setattr("intent_platform.ports.request_json", lambda *a, **k: {"status": "running", "operation": "op-1"})
    assert execute_connector(d, {}, "call", "user", "ws-a")["status"] == "UNKNOWN"
    monkeypatch.setattr("intent_platform.ports.request_json", lambda *a, **k: {"status": "failed", "operation": "op-1"})
    assert execute_connector(d, {}, "call", "user", "ws-a")["status"] == "FAILED"


def test_malformed_model_proposal_is_rejected(monkeypatch):
    monkeypatch.setattr(settings(), "model_mode", "openai")
    monkeypatch.setattr("intent_platform.ports.request_json", lambda *a, **k: {"choices": [{"message": {"content": '{"decision":"READY","requests":["not-an-object"]}'}}]})
    with pytest.raises(ValidationError): route_model("hello", [])


def test_local_document_parsers():
    from io import BytesIO
    from docx import Document
    from intent_platform.documents import extract_document
    assert extract_document("source.txt", "开发材料".encode()) == "开发材料"
    doc = Document(); doc.add_paragraph("Local document evidence"); stream = BytesIO(); doc.save(stream)
    assert "Local document evidence" in extract_document("source.docx", stream.getvalue())
    with pytest.raises(ValueError): extract_document("unknown.html", b"<script/>")
