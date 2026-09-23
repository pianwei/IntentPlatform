"""Exercise the local E2 API and separate intake worker with synthetic sources."""
import json
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:5173"
WORKSPACE = "/api/v2/management/workspaces/ws-dev"
SOURCE = b"# Synthetic intake source\nA bounded local example.\n"


def request(path, token=None, data=None, content_type=None, key=None):
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    if content_type:
        headers["Content-Type"] = content_type
    if key:
        headers["Idempotency-Key"] = key
    with urlopen(Request(BASE + path, data=data, headers=headers), timeout=15) as response:
        return response.status, json.loads(response.read())


def document(token, filename, content, key):
    boundary = "intent-local-" + uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return request(WORKSPACE + "/ingest-jobs/documents", token, body, f"multipart/form-data; boundary={boundary}", key)


def finished(token, job_id):
    deadline = time.monotonic() + 30
    observed = []
    while time.monotonic() < deadline:
        _, job = request(WORKSPACE + "/ingest-jobs/" + job_id, token)
        observed.append(job["status"])
        if job["status"] in {"SUCCEEDED", "FAILED"}:
            return job, observed
        time.sleep(0.2)
    raise AssertionError("Synthetic intake job did not finish within 30 seconds")


def main():
    _, health = request("/health")
    assert health["status"] == "ok" and health["fixture_mode"] is True
    _, login = request("/api/v2/dev/login", data=json.dumps({"user_id": "dev-admin"}).encode(), content_type="application/json")
    token = login["access_token"]
    key = str(uuid.uuid4())
    accepted_status, accepted = document(token, "synthetic-intake.md", SOURCE, key)
    assert accepted_status == 202 and accepted["status"] == "PENDING"
    job, observed = finished(token, accepted["job_id"])
    assert job["status"] == "SUCCEEDED" and job["result"]["revision"] == 1, job.get("error")
    document_id = job["result"]["document_id"]
    _, version = request(WORKSPACE + f"/documents/{document_id}/versions/1", token)
    assert version["text"].encode() == SOURCE
    duplicate_status, duplicate = document(token, "synthetic-intake.md", SOURCE, key)
    assert duplicate_status == 202 and duplicate["job_id"] == accepted["job_id"]
    try:
        document(token, "synthetic-intake.md", SOURCE + b"changed", key)
    except HTTPError as exc:
        assert exc.code == 409
    else:
        raise AssertionError("Conflicting idempotency reuse was accepted")

    spec = {"openapi": "3.0.3", "info": {"title": "Synthetic intake", "version": "1"},
            "servers": [{"url": "http://127.0.0.1:8999"}], "paths": {"/items": {"get": {
                "operationId": "listSyntheticItems", "summary": "List synthetic items",
                "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {
                    "type": "object", "properties": {"message": {"type": "string", "maxLength": 80}},
                    "required": ["message"], "additionalProperties": False}}}}}}}}}
    _, source = document(token, "synthetic-openapi.json", json.dumps(spec).encode(), str(uuid.uuid4()))
    source_job, _ = finished(token, source["job_id"])
    assert source_job["status"] == "SUCCEEDED", source_job.get("error")
    payload = json.dumps({"document_id": source_job["result"]["document_id"], "revision": 1}).encode()
    parse_status, parsed = request(WORKSPACE + "/ingest-jobs/openapi-imports", token, payload, "application/json", str(uuid.uuid4()))
    assert parse_status == 202
    parse_job, parse_observed = finished(token, parsed["job_id"])
    assert parse_job["status"] == "SUCCEEDED", parse_job.get("error")
    _, imported = request(WORKSPACE + "/openapi-imports/" + parse_job["result"]["import_id"], token)
    assert imported["valid"] and imported["operations"][0]["supported"]
    return {"status": "passed", "environment": "local-compose", "identity": "development",
            "business": "synthetic", "document_job_id": accepted["job_id"], "document_id": document_id,
            "document_statuses": observed, "duplicate_reused_job": True, "conflicting_key_denied": True,
            "openapi_statuses": parse_observed, "openapi_supported_operations": 1,
            "acceptance_evidence": False}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
