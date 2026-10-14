"""Local socket evidence for bounded transport and versioned connector mappings.

All workflow replies are synthetic protocol fixtures, not bank platform evidence.
"""
import gzip
import json
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import httpx
import pytest

from intent_platform.config import settings
from intent_platform.network import MAX_JSON_RESPONSE_BYTES, request_json, request_json_parts
from intent_platform import ports
from intent_platform.ports import connector_checks, execute_connector
from intent_platform.schemas import Definition


@pytest.fixture
def workflow_socket(monkeypatch):
    observed = {"posts": [], "gets": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, value, *, status=200, encoding=None, chunked=False):
            body = json.dumps(value).encode()
            if encoding == "gzip":
                body = gzip.compress(body)
            elif encoding == "deflate":
                body = zlib.compress(body)
            elif encoding == "raw-deflate":
                compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
                body = compressor.compress(body) + compressor.flush()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if encoding:
                self.send_header("Content-Encoding", "deflate" if encoding == "raw-deflate" else encoding)
            if chunked:
                self.send_header("Transfer-Encoding", "chunked")
            else:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                if chunked:
                    for offset in range(0, len(body), 17):
                        chunk = body[offset:offset + 17]
                        self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def do_GET(self):
            path = urlsplit(self.path).path
            observed["gets"].append(path)
            if path == "/no-content":
                self.send_response(204)
                self.end_headers()
            elif path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/json")
                self.end_headers()
            elif path == "/oversized":
                self.send_json({"large": "x" * MAX_JSON_RESPONSE_BYTES})
            elif path == "/gzip-bomb":
                self.send_json({"large": "x" * (MAX_JSON_RESPONSE_BYTES * 4)}, encoding="gzip")
            elif path == "/chunked":
                self.send_json({"ok": True}, chunked=True)
            elif path == "/gzip":
                self.send_json({"ok": True}, encoding="gzip")
            elif path == "/gzip-chunked":
                self.send_json({"ok": True}, encoding="gzip", chunked=True)
            elif path == "/deflate":
                self.send_json({"ok": True}, encoding="deflate")
            elif path == "/raw-deflate":
                self.send_json({"ok": True}, encoding="raw-deflate")
            elif path == "/http/business-fail":
                self.send_json({"reason": "synthetic-not-found"}, status=404)
            elif path == "/http/oversized-fail":
                self.send_json({"large": "x" * MAX_JSON_RESPONSE_BYTES}, status=404)
            elif path.startswith("/dify/status/"):
                operation = path.rsplit("/", 1)[-1]
                state = "succeeded" if operation == "op-running" else operation.removeprefix("op-")
                self.send_json({"id": operation, "status": state, "outputs": {"source": "dify-status"}})
            elif path.startswith("/hiagent/status/"):
                operation = path.rsplit("/", 1)[-1]
                state = "DONE" if operation == "op-WORKING" else operation.removeprefix("op-")
                self.send_json({"payload": {"op": operation, "state": state, "result": {"source": "hiagent-status"}}})
            else:
                self.send_json({"ok": True})

        def do_POST(self):
            path = urlsplit(self.path).path
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            observed["posts"].append((path, body))
            if path.endswith("/slow"):
                time.sleep(0.2)
            if path.startswith("/dify/"):
                state = path.rsplit("/", 1)[-1]
                self.send_json({"workflow_run_id": "op-" + state, "data": {"id": "op-" + state,
                               "status": state if state != "missing" else None,
                               "outputs": {"source": "dify-execute"}}})
            elif path.startswith("/hiagent/"):
                state = path.rsplit("/", 1)[-1]
                self.send_json({"payload": {"op": "op-" + state, "state": state,
                                "result": {"source": "hiagent-execute"}}})
            else:
                self.send_json({"echo": body})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    cfg = settings()
    monkeypatch.setattr(cfg, "object_authorization_url", "")
    monkeypatch.setattr(cfg, "connector_targets_json", json.dumps({"ws-fixture": [
        {"origin": origin, "path_prefix": "/", "kinds": ["dify", "hiagent", "http"]}]}))
    yield origin, observed
    server.shutdown()
    server.server_close()
    thread.join(3)


@pytest.mark.parametrize("transport", [request_json, request_json_parts])
def test_bounded_stream_handles_chunks_gzip_errors_and_redirects(workflow_socket, transport):
    origin, observed = workflow_socket
    assert transport("GET", origin + "/chunked") == {"ok": True}
    assert transport("GET", origin + "/gzip") == {"ok": True}
    assert transport("GET", origin + "/gzip-chunked") == {"ok": True}
    assert transport("GET", origin + "/deflate") == {"ok": True}
    assert transport("GET", origin + "/raw-deflate") == {"ok": True}
    for path in ("/oversized", "/gzip-bomb", "/http/oversized-fail"):
        with pytest.raises(ValueError, match="exceeds permitted size"):
            transport("GET", origin + path)
    with pytest.raises(ValueError, match="redirects"):
        transport("GET", origin + "/redirect")
    with pytest.raises(httpx.HTTPStatusError) as exc:
        transport("GET", origin + "/http/business-fail")
    assert exc.value.response.status_code == 404
    assert exc.value.response.json() == {"reason": "synthetic-not-found"}
    assert "/json" not in observed["gets"]


def test_explicit_function_authorization_does_not_call_global_object_authorizer(monkeypatch):
    cfg = settings()
    monkeypatch.setattr(cfg, "object_authorization_url", "http://127.0.0.1:9/global-acl")
    calls = []

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return {"allowed": True}

    monkeypatch.setattr(ports, "request_json", request)
    definition = {"business_authorization": {"mode": "function"}, "action": "READ",
                  "object_type": "Customer", "arguments": {"customer_id": "C-1"}}

    ports.legacy_object_authorization(definition, "user-1", "workspace-1", "http://feature.local/read")
    assert calls == []
    ports.legacy_object_authorization({k: v for k, v in definition.items() if k != "business_authorization"},
                                      "user-1", "workspace-1", "http://feature.local/read")
    assert len(calls) == 1 and calls[0][1]["payload"]["user_id"] == "user-1"


def test_trusted_identity_headers_are_only_added_for_function_owned_policy():
    definition = {"business_authorization": {"mode": "function", "user_id_header": "X-Actor",
                                               "workspace_id_header": "X-Tenant"}}
    assert ports.configured_identity_headers(definition, "u-7", "ws-4") == {
        "X-Actor": "u-7", "X-Tenant": "ws-4"}
    assert ports.configured_identity_headers({"business_authorization": {"mode": "none"}}, "u-7", "ws-4") == {}


def dify_definition(origin, state, *, timeout=1):
    return Definition(name="synthetic Dify", description="Loopback workflow", effect="WRITE",
                      connector={"kind": "dify", "version": "1", "method": "POST",
                                 "endpoint": origin + "/dify/" + state,
                                 "status_endpoint": origin + "/dify/status/{operation_id}",
                                 "timeout_seconds": timeout, "immutable_remote": True}).model_dump()


def hiagent_definition(origin, state):
    return Definition(name="synthetic HiAgent", description="Loopback workflow", effect="WRITE",
                      connector={"kind": "hiagent", "version": "1", "method": "POST",
                                 "endpoint": origin + "/hiagent/" + state,
                                 "status_endpoint": origin + "/hiagent/status/{operation_id}",
                                 "request_mapping": {"args": "$arguments", "caller": "$user_id", "call": "$call_id"},
                                 "status_path": "payload.state", "result_path": "payload.result",
                                 "operation_id_path": "payload.op", "success_values": ["DONE"],
                                 "failure_values": ["ERROR"], "pending_values": ["WORKING"]}).model_dump()


@pytest.mark.parametrize("remote,expected", [("succeeded", "SUCCEEDED"), ("failed", "FAILED"),
                                          ("stopped", "FAILED"), ("running", "UNKNOWN"),
                                          ("paused", "UNKNOWN"), ("pending", "UNKNOWN"),
                                          ("unrecognized", "UNKNOWN"), ("missing", "UNKNOWN")])
def test_dify_explicit_remote_status_mapping(workflow_socket, remote, expected):
    origin, observed = workflow_socket
    result = execute_connector(dify_definition(origin, remote), {"record": "synthetic"},
                               "call-1", "user-1", "ws-fixture")
    assert result["status"] == expected
    assert result["operation_id"] == "op-" + remote
    assert observed["posts"] == [("/dify/" + remote, {"inputs": {"record": "synthetic"},
                                                       "response_mode": "blocking", "user": "user-1"})]


def test_dify_pinned_v1_unknown_is_safe_and_status_query_does_not_post(workflow_socket):
    origin, observed = workflow_socket
    old = execute_connector(dify_definition(origin, "unrecognized"), {},
                            "call-1", "user-1", "ws-fixture")
    assert old["status"] == "UNKNOWN"
    posts_before = len(observed["posts"])
    new = execute_connector(dify_definition(origin, "running"), {}, "call-1", "user-1",
                            "ws-fixture", reconcile_operation="op-running")
    assert new["status"] == "SUCCEEDED" and new["data"] == {"source": "dify-status"}
    assert len(observed["posts"]) == posts_before
    assert observed["gets"][-1] == "/dify/status/op-running"


@pytest.mark.parametrize("remote,expected", [("DONE", "SUCCEEDED"), ("ERROR", "FAILED"),
                                          ("WORKING", "UNKNOWN"), ("NEW_REMOTE_STATE", "UNKNOWN")])
def test_hiagent_explicit_mapping_and_query(workflow_socket, remote, expected):
    origin, observed = workflow_socket
    definition = hiagent_definition(origin, remote)
    result = execute_connector(definition, {"record": "synthetic"}, "call-1", "user-1", "ws-fixture")
    assert result["status"] == expected and result["operation_id"] == "op-" + remote
    assert observed["posts"] == [("/hiagent/" + remote, {"args": {"record": "synthetic"},
                                                     "caller": "user-1", "call": "call-1"})]
    posts_before = len(observed["posts"])
    queried = execute_connector(definition, {}, "call-1", "user-1", "ws-fixture",
                                reconcile_operation="op-WORKING")
    assert queried["status"] == "SUCCEEDED" and queried["data"] == {"source": "hiagent-status"}
    assert len(observed["posts"]) == posts_before


def test_timed_out_write_is_sent_once_and_remains_unresolved(workflow_socket):
    origin, observed = workflow_socket
    with pytest.raises(httpx.TimeoutException):
        execute_connector(dify_definition(origin, "slow", timeout=0.05), {},
                          "call-once", "user-1", "ws-fixture")
    assert len(observed["posts"]) == 1


def test_http_v2_reviewed_failure_uses_only_bounded_body(workflow_socket):
    origin, _ = workflow_socket
    connector = {"kind": "http", "version": "2", "method": "GET",
                 "endpoint": origin + "/http/business-fail",
                 "http_mapping": {"path": {}, "query": {}, "headers": {}, "body": {}},
                 "http_failure_statuses": [404], "result_path": "reason"}
    definition = Definition(name="synthetic HTTP", description="Reviewed local negative response",
                            connector=connector).model_dump()
    result = execute_connector(definition, {}, "call-1", "user-1", "ws-fixture")
    assert result["status"] == "FAILED" and result["data"] == "synthetic-not-found"
    definition["connector"]["endpoint"] = origin + "/http/oversized-fail"
    with pytest.raises(ValueError, match="exceeds permitted size"):
        execute_connector(definition, {}, "call-1", "user-1", "ws-fixture")


def test_http_v2_cannot_publish_accept_encoding_mapping(workflow_socket):
    origin, _ = workflow_socket
    definition = Definition(
        name="synthetic header", description="Transport framing stays under server control",
        input_schema={"type": "object", "properties": {"hint": {"type": "string"}},
                      "required": ["hint"], "additionalProperties": False},
        connector={"kind": "http", "version": "2", "method": "GET",
                   "endpoint": origin + "/json",
                   "http_mapping": {"path": {}, "query": {}, "headers": {"Accept-Encoding": "hint"}, "body": {}}},
    ).model_dump()
    with pytest.raises(ValueError, match="Mapped headers"):
        connector_checks(definition, "ws-fixture")


def test_http_204_is_success_only_for_explicit_status_only_output(workflow_socket):
    origin, _ = workflow_socket
    definition = Definition(
        authoring_version="function-v2", name="状态通知", description="无业务数据",
        output_mode="none",
        input_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        output_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        presentation={"field_map": {}},
        connector={"kind": "http", "version": "2", "method": "GET", "endpoint": origin + "/no-content",
                   "http_mapping": {"path": {}, "query": {}, "headers": {}, "body": {}}},
    ).model_dump()
    result = execute_connector(definition, {}, "call-204", "user-1", "ws-fixture")
    assert result["status"] == "SUCCEEDED" and result["data"] == {}

    unsafe = {**definition, "success_condition": {"field": "status", "operator": "eq", "value": "ok"}}
    with pytest.raises(ValueError):
        execute_connector(unsafe, {}, "call-204-unsafe", "user-1", "ws-fixture")
