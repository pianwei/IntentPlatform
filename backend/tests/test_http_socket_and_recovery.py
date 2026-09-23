"""A real loopback HTTP service: network I/O is real, business data is explicitly synthetic."""
import json
import base64
import threading
import time
import uuid
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from intent_platform.config import settings
from intent_platform.db import engine, set_identity, now
from intent_platform.models import Call, Run, ResultFile, Function, FunctionVersion
from intent_platform.ports import execute_connector
from intent_platform.schemas import Definition
from intent_platform.worker import claim_run, prepare_run, claim_call, process_call, tick
from conftest import conversation, message, drain


@pytest.fixture
def http_fixture(monkeypatch):
    state = {"writes": 0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def reply(self, body):
            data = json.dumps(body).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers()
            try: self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass
        def do_GET(self):
            path = urlsplit(self.path).path
            self.reply({"status": "failed" if path == "/fixture/fail" else "succeeded", "data": {"synthetic": True, "transport": "actual-loopback-http", "unapproved_secret": "must-not-persist"}, "operation_id": "operation-test"})
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            state["writes"] += 1
            time.sleep(body.get("delay", 0))
            self.reply({"status": "succeeded", "data": {"synthetic": True}, "operation_id": "operation-test"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(settings(), "connector_targets_json", json.dumps({"ws-dev": [{"origin": origin, "path_prefix": "/fixture", "kinds": ["http"]}]}))
    yield origin, state
    server.shutdown(); server.server_close(); thread.join(3)


def publish(client, headers, definition, probe_arguments):
    ws = "/api/v2/management/workspaces/ws-dev"
    f = client.post(ws + "/functions/import", headers=headers, json={"definition": definition})
    assert f.status_code == 201, f.text
    fid = f.json()["id"]
    val = client.post(ws + f"/functions/{fid}/validate", headers=headers, json={"probe": True, "arguments": probe_arguments, "allow_effects": True})
    assert val.json()["valid"], val.text
    assert client.post(ws + f"/functions/{fid}/publish", headers=headers).status_code == 200
    a = client.post(ws + "/agents", headers=headers, json={"name": "HTTP integration fixture " + uuid.uuid4().hex[:6], "capability_ids": [fid]}).json()
    r = client.post(ws + f"/agents/{a['id']}/publish", headers=headers)
    assert r.status_code == 200, r.text
    return a["id"]


def test_real_http_socket_read_and_known_failure(client, admin, http_fixture):
    origin, state = http_fixture
    alias = "http-read-" + uuid.uuid4().hex[:6]
    d = Definition(name=alias, description="Synthetic local HTTP service; not real bank API", aliases=[alias],
                   connector={"kind": "http", "endpoint": origin + "/fixture/ok", "status_path": "status", "result_path": "data", "operation_id_path": "operation_id"},
                   output_schema={"type": "object", "properties": {"synthetic": {"type": "boolean"}, "transport": {"type": "string", "maxLength": 100}}, "required": ["synthetic", "transport"], "additionalProperties": False},
                   presentation={"field_map": {"Synthetic": "synthetic", "Transport": "transport"}}).model_dump()
    aid = publish(client, admin, d, {})
    cid = conversation(client, admin, aid); rid = message(client, admin, cid, alias); drain()
    run = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    assert run["status"] == "SUCCEEDED"
    results = [e for e in client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=admin).json() if e["event_type"] == "RESULT"]
    assert results[0]["payload"]["data"]["transport"] == "actual-loopback-http"
    assert "must-not-persist" not in str(results)
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        assert "must-not-persist" not in str(db.scalar(select(Call).where(Call.run_id == rid)).result)
    failed = {**d, "connector": {**d["connector"], "endpoint": origin + "/fixture/fail"}}
    assert execute_connector(failed, {}, "local-check", "dev-admin", "ws-dev")["status"] == "FAILED"


def test_write_timeout_is_unknown_and_never_blindly_replayed(client, admin, http_fixture):
    origin, state = http_fixture
    alias = "http-write-" + uuid.uuid4().hex[:6]
    d = Definition(name=alias, description="Synthetic write timeout fixture", aliases=[alias], effect="WRITE",
                   input_schema={"type": "object", "properties": {"delay": {"type": "number", "minimum": 0}}, "required": ["delay"], "additionalProperties": False},
                   connector={"kind": "http", "method": "POST", "endpoint": origin + "/fixture/write", "timeout_seconds": 0.05, "status_path": "status", "result_path": "data"},
                   output_schema={"type": "object", "properties": {"synthetic": {"type": "boolean"}}, "required": ["synthetic"], "additionalProperties": False},
                   presentation={"field_map": {"Synthetic": "synthetic"}}).model_dump()
    aid = publish(client, admin, d, {"delay": 0}); before = state["writes"]
    cid = conversation(client, admin, aid); rid = message(client, admin, cid, alias + " delay=0.2"); drain()
    run = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    assert run["status"] == "WAITING_CONFIRMATION" and state["writes"] == before
    assert client.post(f"/api/v2/runtime/runs/{rid}/confirmations", headers=admin, json={"revision": run["revision"], "confirmed": True}).status_code == 200
    drain(); run = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    assert run["status"] == "UNKNOWN" and run["calls"][0]["status"] == "UNKNOWN"
    assert state["writes"] == before + 1
    drain(); assert state["writes"] == before + 1


def test_invalid_read_output_is_explicit_error_without_raw_data(client, admin, http_fixture, monkeypatch):
    origin, _ = http_fixture
    alias = "bad-output-read-" + uuid.uuid4().hex[:6]
    definition = Definition(name=alias, description="Synthetic output boundary", aliases=[alias],
        connector={"kind": "http", "endpoint": origin + "/fixture/ok", "status_path": "status", "result_path": "data"},
        output_schema={"type": "object", "properties": {"transport": {"type": "string", "maxLength": 100}},
                       "required": ["transport"], "additionalProperties": False},
        presentation={"field_map": {"Transport": "transport"}}).model_dump()
    aid = publish(client, admin, definition, {})
    monkeypatch.setattr("intent_platform.worker.execute_connector", lambda *args, **kwargs:
                        {"status": "SUCCEEDED", "data": {"transport": 42, "secret": "private-response-marker"}})
    cid = conversation(client, admin, aid); rid = message(client, admin, cid, alias); drain()
    run = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    events = client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=admin).json()
    assert run["status"] == "FAILED" and run["calls"][0]["status"] == "FAILED"
    assert any(e["event_type"] == "ERROR" and e["payload"]["code"] == "OUTPUT_CONTRACT_ERROR" for e in events)
    assert not any(e["event_type"] == "RESULT" for e in events)
    assert "private-response-marker" not in str(events)
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        call = db.scalar(select(Call).where(Call.run_id == rid))
        assert call.result["data"] == {} and "private-response-marker" not in str(call.result)


def test_invalid_confirmed_write_output_keeps_success_without_replay(client, admin, http_fixture, monkeypatch):
    origin, state = http_fixture
    alias = "bad-output-write-" + uuid.uuid4().hex[:6]
    definition = Definition(name=alias, description="Synthetic confirmed write output boundary", aliases=[alias], effect="WRITE",
        connector={"kind": "http", "method": "POST", "endpoint": origin + "/fixture/write", "status_path": "status", "result_path": "data"},
        output_schema={"type": "object", "properties": {"synthetic": {"type": "boolean"}},
                       "required": ["synthetic"], "additionalProperties": False},
        presentation={"field_map": {"Synthetic": "synthetic"}}).model_dump()
    aid = publish(client, admin, definition, {}); baseline = state["writes"]
    sends = []
    def response(*args, **kwargs):
        sends.append(1)
        return {"status": "SUCCEEDED", "data": {"synthetic": "wrong", "secret": "write-response-marker"}}
    monkeypatch.setattr("intent_platform.worker.execute_connector", response)
    cid = conversation(client, admin, aid); rid = message(client, admin, cid, alias); drain()
    waiting = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    assert waiting["status"] == "WAITING_CONFIRMATION" and not sends
    assert client.post(f"/api/v2/runtime/runs/{rid}/confirmations", headers=admin,
                       json={"revision": waiting["revision"], "confirmed": True}).status_code == 200
    drain(); drain()
    run = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    events = client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=admin).json()
    assert run["status"] == "SUCCEEDED" and run["calls"][0]["status"] == "SUCCEEDED"
    assert len(sends) == 1 and state["writes"] == baseline
    result = next(e["payload"] for e in events if e["event_type"] == "RESULT")
    assert result["result_unavailable"] is True and result["data"] is None
    assert any(e["event_type"] == "ERROR" and e["payload"]["code"] == "RESULT_UNAVAILABLE" for e in events)
    assert "write-response-marker" not in str(events)
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        call = db.scalar(select(Call).where(Call.run_id == rid))
        assert call.result["result_unavailable"] is True and "write-response-marker" not in str(call.result)


def test_mixed_inline_file_requires_approval_and_rejects_bad_bytes(client, admin, http_fixture, monkeypatch):
    origin, _ = http_fixture
    alias = "file-output-" + uuid.uuid4().hex[:6]
    definition = Definition(name=alias, description="Synthetic bounded mixed result", aliases=[alias],
        connector={"kind": "http", "endpoint": origin + "/fixture/ok", "status_path": "status", "result_path": "data"},
        output_schema={"type": "object", "properties": {"transport": {"type": "string", "maxLength": 100}},
                       "required": ["transport"], "additionalProperties": False},
        presentation={"field_map": {"Transport": "transport"}, "files_field": "files"}).model_dump()
    aid = publish(client, admin, definition, {})
    encoded = base64.b64encode(b"approved synthetic file").decode()
    payload = {"status": "SUCCEEDED", "data": {"transport": "approved", "secret": "mixed-secret-marker",
               "files": [{"filename": "approved.txt", "media_type": "text/plain", "content_base64": encoded}]}}
    monkeypatch.setattr("intent_platform.worker.execute_connector", lambda *args, **kwargs: payload)
    cid = conversation(client, admin, aid); rid = message(client, admin, cid, alias); drain()
    events = client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=admin).json()
    result = next(e["payload"] for e in events if e["event_type"] == "RESULT")
    assert result["data"] == {"transport": "approved"} and len(result["files"]) == 1
    assert "mixed-secret-marker" not in str(events) and encoded not in str(events)
    file_id = result["files"][0]["id"]
    assert client.get(f"/api/v2/runtime/files/{file_id}", headers=admin).content == b"approved synthetic file"
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        assert "mixed-secret-marker" not in str(db.scalar(select(Call).where(Call.run_id == rid)).result)
    payload["data"]["files"] = [{"filename": "bad.txt", "content_base64": "not base64"}]
    rid2 = message(client, admin, cid, alias); drain()
    assert client.get(f"/api/v2/runtime/runs/{rid2}", headers=admin).json()["status"] == "FAILED"
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        assert db.scalar(select(ResultFile).where(ResultFile.run_id == rid2)) is None


def test_probe_invalid_output_does_not_return_raw_business_data(client, admin, http_fixture):
    origin, _ = http_fixture
    definition = Definition(name="Synthetic probe rejection", description="Result differs from declared field",
        connector={"kind": "http", "endpoint": origin + "/fixture/ok", "status_path": "status", "result_path": "data"},
        output_schema={"type": "object", "properties": {"missing": {"type": "string", "maxLength": 20}},
                       "required": ["missing"], "additionalProperties": False},
        presentation={"field_map": {"Missing": "missing"}}).model_dump()
    ws = "/api/v2/management/workspaces/ws-dev"
    created = client.post(ws + "/functions/import", headers=admin, json={"definition": definition}).json()
    response = client.post(ws + f"/functions/{created['id']}/validate", headers=admin, json={"probe": True})
    assert response.status_code == 200 and response.json()["valid"] is False
    assert response.json()["probe"]["result_unavailable"] is True
    assert "must-not-persist" not in response.text


def test_pinned_legacy_http_version_without_output_contract_still_runs(client, admin, http_fixture):
    from intent_platform.ontology import compile_definition
    origin, _ = http_fixture
    alias = "legacy-http-" + uuid.uuid4().hex[:6]
    old = Definition(name=alias, description="Pinned synthetic pre-E1 HTTP release", aliases=[alias],
        connector={"kind": "http", "endpoint": origin + "/fixture/ok", "status_path": "status", "result_path": "data"}).model_dump()
    old.pop("output_schema"); old["presentation"].pop("files_field")
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin")
        function = Function(workspace_id="ws-dev", definition=old, latest_version=1, status="published")
        db.add(function); db.flush()
        db.add(FunctionVersion(workspace_id="ws-dev", function_id=function.id, version=1, definition=old, compiled=compile_definition(old)))
        fid = function.id
    ws = "/api/v2/management/workspaces/ws-dev"
    agent = client.post(ws + "/agents", headers=admin, json={"name": alias, "capability_ids": [fid]}).json()
    assert client.post(ws + f"/agents/{agent['id']}/publish", headers=admin).status_code == 200
    cid = conversation(client, admin, agent["id"]); rid = message(client, admin, cid, alias); drain()
    assert client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()["status"] == "SUCCEEDED"


def test_crash_windows_pending_and_lost_external_ack(client, admin, seeded):
    cid = conversation(client, admin, seeded["agent"]["id"])
    rid = message(client, admin, cid, seeded["agent"]["suggested_questions"][0])
    prepared = claim_run("dev-admin"); assert prepared["id"] == rid; prepare_run(prepared)
    # Crash before dispatch: durable PENDING call is recoverable and only executed once.
    claimed = claim_call("dev-admin"); assert claimed["run_id"] == rid
    # Simulate an external success with a lost local acknowledgement by expiring the dispatch lease.
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin")
        call = db.get(Call, claimed["id"]); call.lease_until = now() - timedelta(seconds=1); call.dispatched_at = now() - timedelta(seconds=2)
    recovered = claim_call("dev-admin"); assert recovered["done"]
    run = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    assert run["status"] == "UNKNOWN" and len(run["calls"]) == 1
    # No downstream idempotency/status contract: do not replay the action.
    assert claim_call("dev-admin") is None


def test_outbox_not_delivered_is_recoverable_without_new_call(client, admin, seeded):
    cid = conversation(client, admin, seeded["agent"]["id"])
    rid = message(client, admin, cid, seeded["agent"]["suggested_questions"][0]); drain()
    first = client.get(f"/api/v2/runtime/conversations/{cid}/events?after=0", headers=admin).json()
    second = client.get(f"/api/v2/runtime/conversations/{cid}/events?after=0", headers=admin).json()
    assert first == second
    assert len(client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()["calls"]) == 1
