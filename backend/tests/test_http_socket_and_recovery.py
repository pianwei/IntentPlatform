"""A real loopback HTTP service: network I/O is real, business data is explicitly synthetic."""
import json
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
from intent_platform.models import Call, Run
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
            self.reply({"status": "failed" if path == "/fixture/fail" else "succeeded", "data": {"synthetic": True, "transport": "actual-loopback-http"}, "operation_id": "operation-test"})
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
                   connector={"kind": "http", "endpoint": origin + "/fixture/ok", "status_path": "status", "result_path": "data", "operation_id_path": "operation_id"}).model_dump()
    aid = publish(client, admin, d, {})
    cid = conversation(client, admin, aid); rid = message(client, admin, cid, alias); drain()
    run = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    assert run["status"] == "SUCCEEDED"
    results = [e for e in client.get(f"/api/v2/runtime/conversations/{cid}/events", headers=admin).json() if e["event_type"] == "RESULT"]
    assert results[0]["payload"]["data"]["transport"] == "actual-loopback-http"
    failed = {**d, "connector": {**d["connector"], "endpoint": origin + "/fixture/fail"}}
    assert execute_connector(failed, {}, "local-check", "dev-admin", "ws-dev")["status"] == "FAILED"


def test_write_timeout_is_unknown_and_never_blindly_replayed(client, admin, http_fixture):
    origin, state = http_fixture
    alias = "http-write-" + uuid.uuid4().hex[:6]
    d = Definition(name=alias, description="Synthetic write timeout fixture", aliases=[alias], effect="WRITE",
                   input_schema={"type": "object", "properties": {"delay": {"type": "number", "minimum": 0}}, "required": ["delay"], "additionalProperties": False},
                   connector={"kind": "http", "method": "POST", "endpoint": origin + "/fixture/write", "timeout_seconds": 0.05, "status_path": "status", "result_path": "data"}).model_dump()
    aid = publish(client, admin, d, {"delay": 0}); before = state["writes"]
    cid = conversation(client, admin, aid); rid = message(client, admin, cid, alias + " delay=0.2"); drain()
    run = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    assert run["status"] == "WAITING_CONFIRMATION" and state["writes"] == before
    assert client.post(f"/api/v2/runtime/runs/{rid}/confirmations", headers=admin, json={"revision": run["revision"], "confirmed": True}).status_code == 200
    drain(); run = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    assert run["status"] == "UNKNOWN" and run["calls"][0]["status"] == "UNKNOWN"
    assert state["writes"] == before + 1
    drain(); assert state["writes"] == before + 1


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
