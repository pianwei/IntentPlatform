"""Real local HTTP + restricted PostgreSQL, synthetic OpenAPI/source facts only."""
import copy
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from intent_platform.config import settings
from intent_platform.db import engine, set_identity
from intent_platform.models import DocumentVersion, OpenAPIImport, FunctionVersion, Release
from intent_platform.ports import execute_connector
from test_openapi_parser import specification
from conftest import conversation, message, drain

WS = "/api/v2/management/workspaces/ws-dev"


@pytest.fixture
def imported_service(monkeypatch):
    state = {"requests": []}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            payload = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.loads(payload) if payload else None
            record = {"path": urlsplit(self.path).path, "query": parse_qs(urlsplit(self.path).query), "headers": dict(self.headers), "body": body}
            state["requests"].append(record)
            failed = (body or {}).get("fail")
            data = json.dumps({"status": "failed" if failed else "succeeded", "data": record, "operation_id": "synthetic-op"}).encode()
            if failed: time.sleep(state.get("failure_delay", 0))
            self.send_response(state.get("failure_code", 200) if failed else 200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers()
            try: self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(settings(), "connector_targets_json", json.dumps({"ws-dev": [{"origin": origin, "path_prefix": "/test/items", "kinds": ["http"]}]}))
    yield origin, state
    server.shutdown(); server.server_close(); thread.join(3)


def upload(client, admin, content, name="fixture.json"):
    response = client.post(WS + "/documents", headers=admin, files={"file": (name, content.encode(), "application/octet-stream")})
    assert response.status_code == 201, response.text
    return response.json()


def create_import(client, admin, origin):
    document = upload(client, admin, json.dumps(specification(origin), indent=2))
    result = client.post(WS + "/openapi-imports", headers=admin, json={"document_id": document["id"], "revision": 1})
    assert result.status_code == 201 and result.json()["valid"], result.text
    response = client.post(WS + f"/openapi-imports/{result.json()['id']}/functions", headers=admin, json={"operation_key": "POST /items/{item}"})
    assert response.status_code == 201, response.text
    return document, result.json(), response.json()


def confirmed(client, admin, function):
    url = WS + f"/functions/{function['id']}/source-review"
    response = client.get(url, headers=admin)
    assert response.status_code == 200, response.text
    review = response.json()
    response = client.post(url + "/confirm", headers=admin, json={"revision": review["revision"], "resolutions": [{"field_path": i["field_path"], "reason": "Reviewed synthetic protocol contract"} for i in review["issues"]]})
    assert response.status_code == 200 and response.json()["ready"], response.text
    return response.json()


def mapped_function(client, admin, origin):
    document, imported, function = create_import(client, admin, origin)
    d = copy.deepcopy(function["definition"])
    d["connector"].update({"status_path": "status", "result_path": "data", "operation_id_path": "operation_id"})
    response = client.patch(WS + f"/functions/{function['id']}/draft", headers=admin, json={"revision": function["revision"], "definition": d})
    assert response.status_code == 200, response.text
    return document, imported, response.json()


def probe_body(revision, fail=False):
    good = {"path_item": "local-1", "query_category": "synthetic", "header_X-Trace": "fixture", "body_amount": 12.5, "body_fail": fail}
    return {"revision": revision, "probe": True, "arguments": good, "failure_arguments": {**good, "body_fail": True}, "allow_effects": True}


def test_document_revisions_originals_sources_and_rls(client, admin):
    document = upload(client, admin, "# Stable\nfirst source\n\n# Changed\nold description", "source.md")
    doc_id = document["id"]
    response = client.post(WS + f"/documents/{doc_id}/versions", headers=admin, data={"expected_revision": 1}, files={"file": ("source.md", b"# Stable\nfirst source\n\n# Changed\nnew description")})
    assert response.status_code == 201, response.text
    current = response.json()
    assert current["revision"] == 2 and current["sections"][0]["id"] == document["sections"][0]["id"] and current["sections"][1]["id"] == document["sections"][1]["id"]
    assert client.get(WS + f"/documents/{doc_id}/versions/1", headers=admin).json()["text"] == document["text"]
    assert client.get(WS + f"/documents/{doc_id}/versions/1/original", headers=admin).content == document["text"].encode()
    assert client.post(WS + f"/documents/{doc_id}/versions", headers=admin, data={"expected_revision": 1}, files={"file": ("x.txt", b"stale")}).status_code == 409
    other = client.post("/api/v2/dev/login", json={"user_id": "dev-other"}).json()["access_token"]
    assert client.get(WS + f"/documents/{doc_id}/versions/1", headers={"Authorization": "Bearer " + other}).status_code == 403
    with Session(engine) as db:
        assert db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == doc_id)) is None
        set_identity(db, "dev-admin")
        with pytest.raises(DBAPIError): db.execute(text("UPDATE document_versions SET text='changed' WHERE document_id=:id"), {"id": doc_id})


def test_import_review_probe_publish_and_actual_query_body_worker_chain(client, admin, imported_service):
    origin, state = imported_service
    document, imported, function = mapped_function(client, admin, origin); fid = function["id"]
    assert not state["requests"]
    response = client.post(WS + f"/functions/{fid}/validate", headers=admin, json=probe_body(function["revision"]))
    assert response.json()["valid"] is False and not state["requests"]
    assert client.post(WS + f"/functions/{fid}/publish", headers=admin, json={"revision": function["revision"]}).status_code == 409
    confirmed(client, admin, function)
    no_failure = probe_body(function["revision"]); no_failure.pop("failure_arguments")
    assert client.post(WS + f"/functions/{fid}/validate", headers=admin, json=no_failure).json()["valid"] is False
    denied_write = probe_body(function["revision"]); denied_write["allow_effects"] = False
    assert client.post(WS + f"/functions/{fid}/validate", headers=admin, json=denied_write).status_code == 409
    result = client.post(WS + f"/functions/{fid}/validate", headers=admin, json=probe_body(function["revision"]))
    assert result.json()["valid"] and result.json()["checks"]["failure_probe"]["status"] == "FAILED", result.text
    assert state["requests"][-2]["query"] == {"category": ["synthetic"]} and state["requests"][-2]["body"] == {"amount": 12.5, "fail": False}
    assert client.post(WS + f"/functions/{fid}/publish", headers=admin, json={"revision": function["revision"]}).status_code == 200
    agent = client.post(WS + "/agents", headers=admin, json={"name": "Synthetic imported API", "capability_ids": [fid]}).json()
    released = client.post(WS + f"/agents/{agent['id']}/publish", headers=admin)
    assert released.status_code == 200, released.text
    cid = conversation(client, admin, agent["id"])
    rid = message(client, admin, cid, function["name"] + " path_item=local-2 query_category=runtime header_X-Trace=trace body_amount=2 body_fail=false")
    drain(); run = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    assert run["status"] == "WAITING_CONFIRMATION"
    before = len(state["requests"])
    assert client.post(f"/api/v2/runtime/runs/{rid}/confirmations", headers=admin, json={"revision": run["revision"], "confirmed": True}).status_code == 200
    drain(); run = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    assert run["status"] == "SUCCEEDED" and len(state["requests"]) == before + 1
    actual = state["requests"][-1]
    assert actual["path"] == "/test/items/local-2" and actual["query"] == {"category": ["runtime"]} and actual["body"] == {"amount": 2, "fail": False}
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        snapshot = db.get(Release, released.json()["release_id"]).snapshot
        manifest = snapshot["source_manifests"][fid]
        assert manifest["documents"][0]["document_id"] == document["id"] and manifest["documents"][0]["revision"] == 1
        assert manifest["api_source"]["import_id"] == imported["id"] and snapshot["connector_versions"][fid] == "2"


def test_conflicts_reasons_stale_validation_and_source_version_pin(client, admin, imported_service):
    origin, state = imported_service
    document, imported, function = mapped_function(client, admin, origin); fid = function["id"]
    prose = upload(client, admin, "# Contract\nThis synthetic operation reads only.", "contract.md")
    citation = {"field_path": "effect", "document_id": prose["id"], "revision": 1, "section_id": prose["sections"][0]["id"], "quote": "reads only", "proposed_value": "READ_ONLY"}
    invalid = {**citation, "quote": "not present"}
    assert client.put(WS + f"/functions/{fid}/sources", headers=admin, json={"revision": function["revision"], "citations": [invalid]}).status_code == 422
    response = client.put(WS + f"/functions/{fid}/sources", headers=admin, json={"revision": function["revision"], "citations": [citation]})
    review = response.json(); revision = review["revision"]
    assert response.status_code == 200 and any(i["kind"] == "conflict" and i["field_path"] == "effect" for i in review["issues"])
    assert review["citations"][0]["claimed_by"] == "dev-admin" and review["api_facts"]
    confirmed(client, admin, function)
    assert client.post(WS + f"/functions/{fid}/validate", headers=admin, json=probe_body(revision)).json()["valid"]
    definition = copy.deepcopy(function["definition"]); definition["description"] = "Changed draft invalidates prior review"
    changed = client.patch(WS + f"/functions/{fid}/draft", headers=admin, json={"revision": revision, "definition": definition})
    assert changed.status_code == 200
    assert client.post(WS + f"/functions/{fid}/validate", headers=admin, json=probe_body(revision)).status_code == 409
    assert client.post(WS + f"/functions/{fid}/publish", headers=admin, json={"revision": revision}).status_code == 409
    assert not client.get(WS + f"/functions/{fid}/source-review", headers=admin).json()["ready"]
    new_doc = client.post(WS + f"/documents/{prose['id']}/versions", headers=admin, data={"expected_revision": 1}, files={"file": ("contract.md", b"# Contract\nNew changed source")})
    assert new_doc.status_code == 201
    saved = client.get(WS + f"/functions/{fid}/source-review", headers=admin).json()
    assert saved["citations"][0]["revision"] == 1 and saved["citations"][0]["quote"] == "reads only"


def test_final_target_and_template_credentials_are_checked(client, admin, imported_service, monkeypatch):
    origin, state = imported_service
    _, _, function = mapped_function(client, admin, origin)
    definition = function["definition"]
    c = definition["connector"]; c["credential_ref"] = "service-key"; c["authorization_header"] = "X-Service-Key"
    monkeypatch.setattr(settings(), "credentials_json", json.dumps({"service-key": {"value": "local-service-secret", "workspace_ids": ["ws-dev"], "connector_kind": "http", "endpoint": c["endpoint"]}}))
    execute_connector(definition, probe_body(1)["arguments"], "local-call", "dev-admin", "ws-dev")
    assert state["requests"][-1]["headers"]["X-Service-Key"] == "local-service-secret"
    before = len(state["requests"])
    for bad in ("../admin", "%252fadmin"):
        args = {**probe_body(1)["arguments"], "path_item": bad}
        with pytest.raises(ValueError): execute_connector(definition, args, "local-call", "dev-admin", "ws-dev")
    monkeypatch.setattr(settings(), "connector_targets_json", "{}")
    with pytest.raises(ValueError): execute_connector(definition, probe_body(1)["arguments"], "local-call", "dev-admin", "ws-dev")
    assert len(state["requests"]) == before


@pytest.mark.parametrize("code", [400, 404, 409, 422, 401, 403, 500])
def test_only_explicit_business_http_failures_validate(client, admin, imported_service, code):
    origin, state = imported_service
    _, _, function = mapped_function(client, admin, origin)
    definition = copy.deepcopy(function["definition"])
    definition["connector"]["http_failure_statuses"] = [code] if code in {400, 404, 409, 422} else [404]
    function = client.patch(WS + f"/functions/{function['id']}/draft", headers=admin, json={"revision": function["revision"], "definition": definition}).json()
    confirmed(client, admin, function); state["failure_code"] = code
    result = client.post(WS + f"/functions/{function['id']}/validate", headers=admin, json=probe_body(function["revision"]))
    assert result.status_code == 200, result.text
    assert result.json()["valid"] is (code in {400, 404, 409, 422}), result.text
    if code in {400, 404, 409, 422}: assert result.json()["checks"]["failure_probe"]["http_status_code"] == code
    assert len(state["requests"]) == 2


def test_failure_timeout_does_not_validate(client, admin, imported_service):
    origin, state = imported_service
    _, _, function = mapped_function(client, admin, origin)
    definition = copy.deepcopy(function["definition"]); definition["connector"].update({"timeout_seconds": 0.05, "http_failure_statuses": [404]})
    function = client.patch(WS + f"/functions/{function['id']}/draft", headers=admin, json={"revision": function["revision"], "definition": definition}).json()
    confirmed(client, admin, function); state.update({"failure_delay": 0.15, "failure_code": 404})
    result = client.post(WS + f"/functions/{function['id']}/validate", headers=admin, json=probe_body(function["revision"]))
    assert result.status_code == 200 and result.json()["valid"] is False


def test_empty_required_body_and_no_body_stay_distinct(imported_service):
    from intent_platform.openapi_import import parse_openapi
    origin, state = imported_service
    doc = specification(origin); operation = doc["paths"]["/items/{item}"]["post"]
    operation["requestBody"]["content"]["application/json"]["schema"] = {"type": "object", "properties": {}, "additionalProperties": False}
    definition = parse_openapi(json.dumps(doc))["operations"][0]["definition"]
    execute_connector(definition, {"path_item": "local", "query_category": "a"}, "local-call", "dev-admin", "ws-dev")
    assert state["requests"][-1]["body"] == {}
    operation.pop("requestBody")
    definition = parse_openapi(json.dumps(doc))["operations"][0]["definition"]
    execute_connector(definition, {"path_item": "local", "query_category": "a"}, "local-call", "dev-admin", "ws-dev")
    assert state["requests"][-1]["body"] is None


def test_bad_revision_document_is_422_without_version_write(client, admin):
    document = upload(client, admin, "original", "source.txt")
    response = client.post(WS + f"/documents/{document['id']}/versions", headers=admin, data={"expected_revision": 1}, files={"file": ("broken.docx", b"not a zip")})
    assert response.status_code == 422
    versions = client.get(WS + f"/documents/{document['id']}/versions", headers=admin).json()
    assert len(versions) == 1 and versions[0]["text"] == "original"


def test_domain_source_accepts_actual_pinned_revision_and_section(client, admin):
    from intent_platform.domains import domain_source
    document = upload(client, admin, "# Meaning\nFirst source", "source.md")
    revision = client.post(WS + f"/documents/{document['id']}/versions", headers=admin, data={"expected_revision": 1}, files={"file": ("source.md", b"# Meaning\nSecond source")}).json()
    source = {"document_id": document["id"], "revision": 2, "section": revision["sections"][0]["id"]}
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        domain_source(db, {"source": source}, "ws-dev")
        with pytest.raises(ValueError): domain_source(db, {"source": {**source, "section": "invented"}}, "ws-dev")
