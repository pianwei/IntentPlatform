"""Finite shadow unit and real loopback model-wire tests; all payloads synthetic."""
import copy
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
import pytest
from intent_platform.config import settings
from intent_platform.domains import compile_domain
from intent_platform.ontology import compile_definition
from intent_platform.routing_v3 import model_body, domain_projection, route_v3
from intent_platform.shadow_evaluation import model_attempt, validate_attempt
from test_domain_constraints import domain_definition, semantic_definition, proposal


def candidate(identifier="semantic-fixture"):
    definition = semantic_definition()
    domain = domain_definition(); projection = compile_domain(domain)
    projection["versions"] = [{"id": "version-local", "package_id": "package-local", "version": 1, "definition": domain}]
    definition["domain_refs"] = [{"package_id": "package-local", "version": 1}]
    return {"capability_id": identifier, "version": 1, "definition": definition, "compiled": compile_definition(definition, projection=projection)}


def request(identifier="semantic-fixture"):
    return {"decision": "READY", "requests": [{**proposal(), "capability_id": identifier}]}


def data(body):
    return json.loads(body["messages"][0]["content"].split("\nAuthorized data:\n", 1)[1])


def test_complete_deduplicated_projection_and_joint_relation_removal():
    first, second = candidate(), candidate("second")
    body = model_body("synthetic question", [first, second], [{"label": "authorized local row"}])
    projected = data(body)
    assert len(projected["capabilities"]) == 2 and len(projected["domain_projection"]["versions"]) == 1
    assert projected["domain_projection"]["concepts"]["test:SpecialItem"]["parents"] == ["test:Item"]
    assert projected["domain_projection"]["closure"]["test:SpecialItem"] == ["test:Item", "test:SpecialItem"]
    assert projected["domain_projection"]["conversions"]["mm->m"] == 0.001
    assert projected["domain_projection"]["versions"][0]["definition"]["source"]["reference"] == "test-fixture"
    c = data(model_body("same", [first], [], "C"))
    assert c["capabilities"][0]["relations"] == [] and c["domain_projection"]["relations"] == {}
    assert c["domain_projection"]["versions"][0]["definition"]["relations"] == []
    assert c["domain_projection"]["units"] == projected["domain_projection"]["units"]
    a = data(model_body("same", [first], [], "A"))
    assert "domain_projection" not in a
    assert set(a["capabilities"][0]) == {"capability_id", "name", "description", "input_schema"}
    assert first["definition"]["relations"] and first["compiled"]["domain_projection"]["relations"]


def test_projection_conflict_fails_without_candidate_truncation():
    first, second = candidate(), candidate("second")
    second["compiled"]["domain_projection"]["concepts"]["test:Item"]["label"] = "conflict"
    with pytest.raises(ValueError, match="Conflicting"): model_body("q", [first, second], [])
    second = candidate("second"); second["compiled"]["domain_projection"]["versions"][0]["version"] = 2
    with pytest.raises(ValueError, match="Conflicting"): domain_projection([first, second])
    first["compiled"]["short_card"]["description"] = "x" * 2_000_001
    with pytest.raises(ValueError, match="no candidates were truncated"): model_body("q", [first], [])


def test_actual_model_socket_captures_body_response_and_elapsed(monkeypatch):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"choices": [{"message": {"content": json.dumps(request())}}]}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    monkeypatch.setattr(settings(), "model_mode", "openai")
    monkeypatch.setattr(settings(), "model_url", f"http://127.0.0.1:{server.server_port}/completion")
    monkeypatch.setattr(settings(), "model_name", "synthetic-wire-model")
    monkeypatch.setattr(settings(), "model_api_key", "local-wire-secret")
    try:
        trace = {}; result = route_v3("wire text", [candidate()], [{"label": "visible"}], trace=trace)
        assert result["requests"][0]["capability_id"] == "semantic-fixture"
        assert received == [trace["model_request"]] and trace["model_response"]["choices"]
        assert trace["model_ms"] > 0 and not trace["fixture_mode"]
        assert "local-wire-secret" not in json.dumps(trace)
        assert data(received[0])["domain_projection"]["relations"]
    finally:
        server.shutdown(); server.server_close(); thread.join(3)


@pytest.mark.parametrize("failure", ["timeout", "malformed"])
def test_model_failure_preserves_attempt_trace_and_unknown_validation(monkeypatch, failure):
    import httpx
    monkeypatch.setattr(settings(), "model_mode", "openai")
    def fake(*args, **kwargs):
        if failure == "timeout": raise httpx.ReadTimeout("synthetic")
        return {"choices": [{"message": {"content": "not-json"}}]}
    monkeypatch.setattr("intent_platform.ports.request_json", fake)
    row = model_attempt("question", [candidate()], [], "B")
    assert row["outcome"] == ("timeout" if failure == "timeout" else "error")
    assert row["model_request"] is not None and row["model_ms"] >= 0 and row["validation_ms"] is None
    assert row["proposal"] is None and row["decision"] is None


def test_fixture_has_no_claimed_wire_evidence(monkeypatch):
    monkeypatch.setattr(settings(), "model_mode", "fixture")
    cap = candidate(); row = model_attempt(cap["definition"]["name"], [cap], [], "B")
    assert row["outcome"] == "completed" and row["fixture_mode"]
    assert row["model_request"] is None and row["model_response"] is None and row["model_ms"] is None


def test_old_v1_v2_model_payloads_keep_their_original_projection(monkeypatch):
    from intent_platform.ports import route_model
    bodies = []; monkeypatch.setattr(settings(), "model_mode", "openai")
    def respond(*args, **kwargs):
        body = kwargs["payload"]; bodies.append(body)
        value = request()
        if len(bodies) == 1: value["requests"] = [{"capability_id": "semantic-fixture", "arguments": {}}]
        return {"choices": [{"message": {"content": json.dumps(value)}}]}
    monkeypatch.setattr("intent_platform.ports.request_json", respond)
    for version in ("router-v1", "router-v2"): route_model("same", [candidate()], policy_version=version, context=[{"label": "old context"}])
    assert all("Authorized data:" not in b["messages"][0]["content"] and "domain_projection" not in b["messages"][0]["content"] for b in bodies)
    assert "Authorized result context:" not in bodies[0]["messages"][0]["content"]
    assert "Authorized result context:" in bodies[1]["messages"][0]["content"]


def test_only_c_removes_relation_check_and_all_arms_forbid_untrusted_ids():
    cap = candidate(); broken = request(); broken["requests"][0]["relations"][0].update(source="owner", target="target")
    release = SimpleNamespace(snapshot={"resolver_profiles": {}, "context_policy": {"timezone": "UTC"}})
    run = SimpleNamespace(id="shadow-test", user_id="test-user", workspace_id="test-ws", conversation_id="test-cid", bindings={}, created_at=datetime.now(timezone.utc))
    def row(variant, proposal):
        return {"variant": variant, "outcome": "completed", "proposal": proposal, "faults": []}
    assert validate_attempt(None, copy.deepcopy(run), release, [cap], row("B", broken))["decision"] == "OUT_OF_SCOPE"
    assert validate_attempt(None, copy.deepcopy(run), release, [cap], row("C", broken))["decision"] == "READY"
    cap["definition"]["argument_bindings"] = {"item_id": {"role": "target", "resolver_ref": "items@1"}}
    release.snapshot["resolver_profiles"] = {"items@1": {"entity_type": "test:SpecialItem"}}
    for variant in ("A", "B", "C"):
        forged = request(); forged["requests"][0]["arguments"] = {"item_id": "forged-business-id"}
        # Use a matching server-declared participant for A's deliberately missing mention.
        cap["definition"]["participant_roles"][0]["type"] = "test:SpecialItem"
        result = validate_attempt(None, copy.deepcopy(run), release, [cap], row(variant, forged))
        assert result["decision"] == "FORBIDDEN", result
