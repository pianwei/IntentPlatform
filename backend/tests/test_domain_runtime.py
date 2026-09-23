"""Actual PostgreSQL + loopback HTTP; synthetic entities are never bank evidence."""
import copy
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import timedelta
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from intent_platform.config import settings
from intent_platform.db import engine, set_identity, now
from intent_platform.models import DomainVersion, Release, Run, Call
from intent_platform.schemas import Definition
from intent_platform.worker import claim_run, prepare_run, claim_call, process_call, authorize_reconciliation
from conftest import conversation, message, drain
from test_domain_constraints import domain_definition

WS = "/api/v2/management/workspaces/ws-dev"


@pytest.fixture
def resolver_service(monkeypatch):
    state = {"queries": [], "denied": set(), "failure": None, "business": 0, "rows": [{"id": "synthetic-1", "label": "Local one"}, {"id": "synthetic-2", "label": "Local two"}]}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def respond(self, body, status=200):
            data = json.dumps(body).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers()
            try: self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["queries"].append(body)
            if state["failure"] == "timeout": time.sleep(0.15)
            if state["failure"] == "http": return self.respond({"error": "unavailable"}, 503)
            if state["failure"] == "shape": return self.respond({"candidates": [{"id": "bad", "label": "Bad", "type": "test:Item", "authorized": "true"}]})
            rows = [r for r in state["rows"] if r["id"] == body["selected_id"]] if body.get("selected_id") else state["rows"] if body["query"] == "many" else state["rows"][:1]
            self.respond({"candidates": [{**r, "type": "test:Item", "authorized": r["id"] not in state["denied"]} for r in rows]})
        def do_GET(self):
            state["business"] += 1
            self.respond({"status": "succeeded", "data": {"items": state["rows"], "synthetic": True}, "operation_id": "local-operation"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(settings(), "connector_targets_json", json.dumps({"ws-dev": [{"origin": origin, "path_prefix": "/test", "kinds": ["http"]}]}))
    profile = {"version": "1", "workspace_ids": ["ws-dev"], "entity_type": "test:Item", "endpoint": origin + "/test/resolve", "credential_ref": None, "timeout_seconds": 0.05}
    monkeypatch.setattr(settings(), "resolvers_json", json.dumps({"local-items@1": profile}))
    monkeypatch.setattr(settings(), "workspace_timezones_json", json.dumps({"ws-dev": "Asia/Shanghai"}))
    yield origin, state
    server.shutdown(); server.server_close(); thread.join(3)


def published_domain(client, admin):
    response = client.post(WS + "/domains", headers=admin, json={"key": "fixture-" + uuid.uuid4().hex[:10], "definition": domain_definition()})
    assert response.status_code == 201, response.text
    domain = response.json()
    response = client.post(WS + f"/domains/{domain['id']}/validate", headers=admin)
    assert response.json()["valid"], response.text
    response = client.post(WS + f"/domains/{domain['id']}/publish", headers=admin, json={"revision": 1})
    assert response.status_code == 200, response.text
    return domain, response.json()


def publish_function(client, admin, definition, probe=None):
    response = client.post(WS + "/functions/import", headers=admin, json={"definition": definition})
    assert response.status_code == 201, response.text
    fid = response.json()["id"]
    response = client.post(WS + f"/functions/{fid}/validate", headers=admin, json={"probe": probe is not None, "arguments": probe or {}})
    assert response.json()["valid"], response.text
    response = client.post(WS + f"/functions/{fid}/publish", headers=admin)
    assert response.status_code == 200, response.text
    return fid


@pytest.fixture
def semantic_app(client, admin, resolver_service):
    origin, state = resolver_service
    domain, version = published_domain(client, admin)
    alias = "entity-" + uuid.uuid4().hex[:8]
    base = Definition(name=alias, description="Synthetic entity test only", aliases=[alias], compiler_version="finite-json-v2",
                      object_type="test:Item", result_type="test:Result", domain_refs=[{"package_id": domain["id"], "version": 1}],
                      participant_roles=[{"role": "target", "type": "test:Item", "required": True, "min_count": 1, "max_count": 1}],
                      argument_bindings={"item_id": {"role": "target", "resolver_ref": "local-items@1"}},
                      input_schema={"type": "object", "properties": {"item_id": {"type": "string"}}, "required": ["item_id"], "additionalProperties": False},
                      trusted_preconditions={"fact": "entity.target.authorized", "operator": "eq", "value": True}).model_dump()
    fid = publish_function(client, admin, base)
    listing = copy.deepcopy(base)
    listing.update({"name": "listing-" + uuid.uuid4().hex[:8], "aliases": [], "participant_roles": [], "argument_bindings": {}, "trusted_preconditions": None,
                    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                    "connector": {"kind": "http", "endpoint": origin + "/test/list", "status_path": "status", "result_path": "data", "operation_id_path": "operation_id"},
                    "output_schema": {"type": "object", "properties": {"items": {"type": "array", "maxItems": 100,
                                      "items": {"type": "object", "properties": {"id": {"type": "string", "maxLength": 256},
                                                                              "label": {"type": "string", "maxLength": 200}},
                                                "required": ["id", "label"], "additionalProperties": False}}},
                                      "required": ["items"], "additionalProperties": False},
                    "presentation": {"field_map": {"Items": "items"}},
                    "result_bindings": [{"name": "items", "entity_type": "test:Item", "collection_path": "items", "id_field": "id", "label_field": "label", "resolver_ref": "local-items@1"}]})
    listing_id = publish_function(client, admin, listing, {})
    response = client.post(WS + "/agents", headers=admin, json={"name": "Domain test app", "capability_ids": [fid, listing_id]})
    agent = response.json()
    response = client.post(WS + f"/agents/{agent['id']}/publish", headers=admin)
    assert response.status_code == 200, response.text
    runtime = client.post(f"/api/v2/runtime/apps/{agent['public_id']}/sessions", headers=admin, json={}).json()
    headers = {"Authorization": "Bearer " + runtime["access_token"]}
    cid = conversation(client, headers, agent["id"])
    return {"headers": headers, "cid": cid, "agent": agent, "alias": alias, "fid": fid, "listing": listing, "listing_id": listing_id, "state": state, "domain": domain, "release_id": response.json()["release_id"]}


def read(client, app, rid):
    response = client.get(f"/api/v2/runtime/runs/{rid}", headers=app["headers"])
    assert response.status_code == 200, response.text
    result = response.json()
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        result["proposal"] = db.get(Run, rid).proposal
    return result


def send(client, app, suffix="target=one"):
    rid = message(client, app["headers"], app["cid"], app["alias"] + " " + suffix)
    drain(); return rid, read(client, app, rid)


def test_domain_versions_rls_revision_and_release_pinning(client, admin, semantic_app):
    app = semantic_app; domain = app["domain"]
    with Session(engine) as db:
        assert db.scalar(select(DomainVersion).where(DomainVersion.package_id == domain["id"])) is None
        set_identity(db, "dev-other")
        assert db.scalar(select(DomainVersion).where(DomainVersion.package_id == domain["id"])) is None
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        with pytest.raises(DBAPIError): db.execute(text("UPDATE domain_versions SET version=999 WHERE package_id=:id"), {"id": domain["id"]})
    changed = domain_definition(); changed["name"] = "new draft"
    body = {"revision": 1, "definition": changed}
    assert client.patch(WS + f"/domains/{domain['id']}/draft", headers=admin, json=body).status_code == 200
    assert client.patch(WS + f"/domains/{domain['id']}/draft", headers=admin, json=body).status_code == 409
    assert client.post(WS + f"/domains/{domain['id']}/publish", headers=admin, json={"revision": 2}).status_code == 409
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        snapshot = db.get(Release, app["release_id"]).snapshot
        assert snapshot["domain_packages"][0]["version"] == 1
        assert snapshot["context_policy"]["timezone"] == "Asia/Shanghai"
        assert snapshot["model_profile"]["version"] == "development-deterministic-v1"
    rid, run = send(client, app)
    assert run["status"] == "SUCCEEDED"
    assert run["proposal"]["requests"][0]["arguments"] == {"item_id": "synthetic-1"}
    evidence = client.get(f"/api/v2/runtime/runs/{rid}/evaluation-evidence", headers=app["headers"])
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()["validation_completed"] is True
    assert evidence.json()["semantics"]["requests"][0]["action"] == "READ"
    assert client.get(f"/api/v2/runtime/runs/{rid}/evaluation-evidence", headers=admin).status_code == 403


def test_entity_question_selection_revision_and_forged_id(client, admin, semantic_app):
    app = semantic_app
    rid, run = send(client, app, "")
    assert run["question"]["kind"] == "entity_mention"
    q = {"revision": run["revision"], "question_id": run["question"]["id"], "values": {"mention": "many"}}
    assert client.post(f"/api/v2/runtime/runs/{rid}/answers", headers=app["headers"], json=q).status_code == 200
    drain(); run = read(client, app, rid)
    assert run["question"]["kind"] == "entity_choice" and len(run["question"]["options"]) == 2
    assert client.get(f"/api/v2/runtime/conversations/{app['cid']}/history", headers=app["headers"]).status_code == 200
    assert client.get(f"/api/v2/runtime/conversations/{app['cid']}/events", headers=app["headers"]).status_code == 200
    cursor = client.get(f"/api/v2/runtime/conversations/{app['cid']}/history", headers=app["headers"]).json()["cursor"]
    before = len(app["state"]["queries"])
    response = client.get(f"/api/v2/runtime/conversations/{app['cid']}/events?after={cursor}", headers=app["headers"])
    assert response.json() == [] and len(app["state"]["queries"]) == before
    answer = {"revision": run["revision"], "question_id": run["question"]["id"], "option_id": "synthetic-2"}
    assert client.post(f"/api/v2/runtime/runs/{rid}/answers", headers=app["headers"], json=answer).status_code == 422
    answer["option_id"] = run["question"]["options"][1]["id"]
    assert client.post(f"/api/v2/runtime/runs/{rid}/answers", headers=app["headers"], json=answer).status_code == 200
    assert client.post(f"/api/v2/runtime/runs/{rid}/answers", headers=app["headers"], json=answer).status_code == 409
    drain(); run = read(client, app, rid)
    assert run["status"] == "SUCCEEDED" and run["proposal"]["requests"][0]["arguments"]["item_id"] == "synthetic-2"
    _, forged = send(client, app, "target=one item_id=synthetic-1")
    assert forged["status"] == "FAILED" and not forged["calls"]
    rid, run = send(client, app, "target=many")
    message(client, app["headers"], app["cid"], "new unsupported request")
    assert client.post(f"/api/v2/runtime/runs/{rid}/answers", headers=app["headers"], json={"revision": run["revision"], "question_id": run["question"]["id"], "option_id": run["question"]["options"][0]["id"]}).status_code == 409
    drain()


def test_historical_selection_is_explicit_same_conversation_and_revocable(client, admin, semantic_app):
    app = semantic_app
    source = message(client, app["headers"], app["cid"], app["listing"]["name"]); drain()
    call_id = read(client, app, source)["calls"][0]["id"]
    rid, run = send(client, app, f"history_target={call_id}|items")
    assert run["question"]["kind"] == "history_choice"
    assert client.get(f"/api/v2/runtime/conversations/{app['cid']}/history", headers=app["headers"]).status_code == 200
    answer = {"revision": run["revision"], "question_id": run["question"]["id"], "option_id": run["question"]["options"][1]["id"]}
    assert client.post(f"/api/v2/runtime/runs/{rid}/answers", headers=app["headers"], json=answer).status_code == 200
    drain(); run = read(client, app, rid)
    assert run["status"] == "SUCCEEDED" and run["proposal"]["requests"][0]["arguments"]["item_id"] == "synthetic-2"
    assert run["bindings"][0]["source_call_id"] == call_id and run["bindings"][0]["index"] == 1
    cid = conversation(client, app["headers"], app["agent"]["id"])
    other = {**app, "cid": cid}
    _, cross = send(client, other, f"history_target={call_id}|items|0")
    assert cross["status"] == "FAILED" and not cross["calls"]
    app["state"]["denied"].add("synthetic-1")
    _, revoked = send(client, app, f"history_target={call_id}|items|0")
    assert revoked["status"] == "FAILED" and not revoked["calls"]
    assert client.put(WS + f"/functions/{app['listing_id']}/access", headers=admin, json={"access_mode": "allowlist", "allowed_users": []}).status_code == 200
    _, removed = send(client, app, f"history_target={call_id}|items|1")
    assert removed["status"] == "FAILED" and not removed["calls"]


@pytest.mark.parametrize("failure", ["timeout", "http", "shape"])
def test_actual_resolver_failures_finish_without_business_call(client, admin, semantic_app, failure):
    app = semantic_app; app["state"]["failure"] = failure
    _, run = send(client, app)
    assert run["status"] == "FAILED" and not run["calls"]
    if failure != "shape": assert run["decision"] == "ERROR"


def test_entity_revocation_at_dispatch_and_reconciliation(client, admin, semantic_app):
    app = semantic_app
    rid = message(client, app["headers"], app["cid"], app["alias"] + " target=one")
    prepare_run(claim_run("dev-admin")); work = claim_call("dev-admin")
    assert work["run_id"] == rid
    app["state"]["denied"].add("synthetic-1")
    process_call(work)
    assert read(client, app, rid)["calls"][0]["status"] == "FAILED"
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin"); call = db.get(Call, work["id"])
        assert call.dispatched_at is None
        call.status = "RECONCILING"
    with pytest.raises(ValueError): authorize_reconciliation(work)


def test_resolver_version_drift_blocks_old_release(client, admin, semantic_app, monkeypatch):
    app = semantic_app
    profile = json.loads(settings().resolvers_json); profile["local-items@1"]["timeout_seconds"] = 1
    monkeypatch.setattr(settings(), "resolvers_json", json.dumps(profile))
    response = client.post(f"/api/v2/runtime/agents/{app['agent']['id']}/conversations", headers=app["headers"])
    assert response.status_code == 503
    _, run = send(client, app)
    assert run["status"] == "FAILED" and not run["calls"]


def test_history_context_budget_preserves_authorized_prefix(client, admin, semantic_app):
    from intent_platform.context_binding import history_context
    from intent_platform.resolvers import resolver_budget
    app = semantic_app
    app["state"]["rows"] = [{"id": "synthetic-" + str(i), "label": "Local " + str(i)} for i in range(60)]
    app["state"]["denied"] = {"synthetic-" + str(i) for i in range(0, 40, 2)}
    source = message(client, app["headers"], app["cid"], app["listing"]["name"]); drain()
    assert read(client, app, source)["status"] == "SUCCEEDED"
    target = message(client, app["headers"], app["cid"], app["alias"] + " target=one")
    before = len(app["state"]["queries"])
    with Session(engine) as db, resolver_budget(calls=40, seconds=20):
        set_identity(db, "dev-admin"); stats = {}
        rows = history_context(db, db.get(Run, target), db.get(Release, app["release_id"]), stats)
    assert len(app["state"]["queries"]) - before == 40
    assert len(rows) == 20 and [r["history_ref"]["index"] for r in rows] == list(range(1, 40, 2))
    assert stats["truncated"] and stats["checked"] == 40 and stats["included"] == 20
    drain()


@pytest.mark.parametrize("negated", [True, None, "false", 0, "missing"])
def test_v2_model_semantic_negation_cannot_dispatch(client, admin, semantic_app, monkeypatch, negated):
    app = semantic_app
    response = {"decision": "READY", "requests": [{"capability_id": app["fid"], "arguments": {}, "action": "READ", "object_type": "test:Item", "result_type": "test:Result", "participants": [{"role": "target", "type": "test:Item", "mention": "one"}], "relations": [], "parameter_units": {}, "negated": True}]}
    if negated == "missing": response["requests"][0].pop("negated")
    else: response["requests"][0]["negated"] = negated
    monkeypatch.setattr("intent_platform.worker.route_model", lambda *args, **kwargs: response)
    _, run = send(client, app)
    assert run["status"] == "FAILED" and run["decision"] in {"OUT_OF_SCOPE", "ERROR"} and not run["calls"]
    assert not app["state"]["queries"]


def test_resolver_directory_only_exposes_current_authorized_references(client, admin, semantic_app):
    app = semantic_app
    response = client.get(WS + "/resolvers", headers=admin)
    assert response.json() == [{"reference": "local-items@1", "version": "1", "entity_type": "test:Item"}]
    assert client.get(WS + "/resolvers", headers=app["headers"]).status_code == 403


def test_nested_resolver_budgets_cannot_reset_outer_limit(resolver_service):
    from intent_platform.resolvers import resolver_profile, resolver_budget, resolve, ResolverUnavailable
    origin, state = resolver_service
    profile = resolver_profile("local-items@1", "ws-dev")
    with resolver_budget(calls=1, seconds=5):
        with resolver_budget(calls=40, seconds=20):
            assert resolve("local-items@1", profile, "ws-dev", "dev-admin", "one")
        with pytest.raises(ResolverUnavailable, match="budget"):
            with resolver_budget(calls=40, seconds=20):
                resolve("local-items@1", profile, "ws-dev", "dev-admin", "one")
    assert len(state["queries"]) == 1


def test_duplicate_candidate_labels_fail_without_exposing_ids(client, admin, semantic_app):
    app = semantic_app
    for row in app["state"]["rows"]: row["label"] = "Indistinguishable"
    _, run = send(client, app, "target=many")
    assert run["status"] == "FAILED" and run["question"] is None and not run["calls"]


def test_entity_question_reads_recheck_live_object_authorization(client, admin, semantic_app):
    app = semantic_app
    rid, run = send(client, app, "target=many")
    assert run["question"]["kind"] == "entity_choice"
    app["state"]["denied"].add("synthetic-1")
    for path in (f"/runs/{rid}", f"/runs/{rid}/evaluation-evidence", f"/conversations/{app['cid']}/history", f"/conversations/{app['cid']}/events"):
        assert client.get("/api/v2/runtime" + path, headers=app["headers"]).status_code == 403


def test_evaluation_profile_filters_revoked_capabilities_but_preserves_s2_evidence(client, admin, semantic_app):
    app = semantic_app
    prior, run = send(client, app)
    assert run["status"] == "SUCCEEDED"
    for fid in (app["fid"], app["listing_id"]):
        assert client.put(WS + f"/functions/{fid}/access", headers=admin, json={"access_mode": "allowlist", "allowed_users": []}).status_code == 200
    response = client.get(f"/api/v2/runtime/conversations/{app['cid']}/evaluation-profile", headers=app["headers"])
    assert response.status_code == 200, response.text
    assert response.json()["capability_scope"] == "authorized_subset"
    assert not response.json()["function_versions"] and not response.json()["connector_versions"]
    assert client.get(f"/api/v2/runtime/runs/{prior}/evaluation-evidence", headers=app["headers"]).status_code == 403
    rid, run = send(client, app)
    assert run["decision"] == "OUT_OF_SCOPE" and not run["calls"]
    response = client.get(f"/api/v2/runtime/runs/{rid}/evaluation-evidence", headers=app["headers"])
    assert response.status_code == 200 and response.json()["final_decision"] == "OUT_OF_SCOPE"


def test_mixed_release_retains_old_v1_conversation_and_checks_negation(client, admin, semantic_app, seeded, monkeypatch):
    app = semantic_app
    response = client.post(WS + "/agents", headers=admin, json={"name": "Independent mixed-release fixture", "capability_ids": [f["id"] for f in seeded["functions"]], "suggested_questions": seeded["agent"]["suggested_questions"]})
    old_agent = response.json()
    assert client.post(WS + f"/agents/{old_agent['id']}/publish", headers=admin).status_code == 200
    old_cid = conversation(client, admin, old_agent["id"])
    old_release = client.get(f"/api/v2/runtime/conversations/{old_cid}/history", headers=admin).json()["conversation"]["release_id"]
    current = next(a for a in client.get(WS + "/agents", headers=admin).json() if a["id"] == old_agent["id"])
    changed = {"name": current["name"], "capability_ids": [*current["capability_ids"], app["fid"]], "revision": current["revision"]}
    assert client.patch(WS + f"/agents/{old_agent['id']}/draft", headers=admin, json=changed).status_code == 200
    response = client.post(WS + f"/agents/{old_agent['id']}/publish", headers=admin)
    assert response.status_code == 200, response.text
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        assert db.get(Release, old_release).snapshot["prompt_policy_version"] == "router-v1"
        snapshot = db.get(Release, response.json()["release_id"]).snapshot
        assert snapshot["prompt_policy_version"] == "router-v3"
    rid = message(client, admin, old_cid, old_agent["suggested_questions"][0]); drain()
    assert client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()["status"] == "SUCCEEDED"
    new_cid = conversation(client, admin, old_agent["id"])
    rid = message(client, admin, new_cid, old_agent["suggested_questions"][0]); drain()
    assert client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()["status"] == "SUCCEEDED"
    capability = snapshot["capabilities"][0]; d = capability["definition"]
    negative = {"decision": "READY", "requests": [{"capability_id": capability["capability_id"], "arguments": {}, "action": d["action"], "object_type": d["object_type"], "result_type": d["result_type"], "participants": [], "relations": [], "negated": True}]}
    monkeypatch.setattr("intent_platform.worker.route_model", lambda *a, **k: negative)
    rid = message(client, admin, new_cid, "explicit negative"); drain()
    run = client.get(f"/api/v2/runtime/runs/{rid}", headers=admin).json()
    assert run["status"] == "FAILED" and run["decision"] == "OUT_OF_SCOPE" and not run["calls"]
