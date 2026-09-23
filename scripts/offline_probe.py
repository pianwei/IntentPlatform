"""Run inside the isolated API container; real HTTP and PG, synthetic identity/model/business."""
import json
import re
import socket
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request(path, token=None, body=None, method=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = Request("http://web:5173" + path, headers=headers,
                  data=json.dumps(body).encode() if body is not None else None, method=method)
    with urlopen(req, timeout=10) as response:
        data = response.read()
        return json.loads(data) if "application/json" in response.headers.get("Content-Type", "") else data.decode()


def upload_synthetic_document(token, key):
    boundary = "intent-offline-" + uuid.uuid4().hex
    content = b"# Offline synthetic intake\nLocal worker recovery probe.\n"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"offline.md\"\r\n"
            "Content-Type: text/markdown\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
    req = Request("http://web:5173/api/v2/management/workspaces/ws-dev/ingest-jobs/documents",
                  data=body, headers={"Authorization": "Bearer " + token,
                                      "Content-Type": f"multipart/form-data; boundary={boundary}",
                                      "Idempotency-Key": key})
    with urlopen(req, timeout=10) as response:
        assert response.status == 202
        return json.loads(response.read())


def main():
    # No token, ticket, arguments or business response is written to the report.
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=2):
            raise AssertionError("External TCP unexpectedly reachable")
    except OSError:
        pass
    health = request("/health")
    assert health["status"] == "ok" and health["fixture_mode"] is True
    manager = request("/api/v2/dev/login", body={"user_id": "dev-admin"})["access_token"]
    seeded = request("/api/v2/dev/seed", manager, {})
    agent = seeded["agent"]
    public_id = agent["public_id"]
    assets = set()
    for path in ("/console/", "/chat/" + public_id, "/embed/" + public_id):
        html = request(path)
        assert '<div id="root"' in html, path
        urls = re.findall(r'(?:src|href)="(/(?:console|runtime)/assets/[^\"]+)"', html)
        assert urls, path
        for url in urls:
            assert request(url), url
            assets.add(url)
    runtime = request(f"/api/v2/runtime/apps/{public_id}/sessions", manager, {})
    token = runtime["access_token"]
    try:
        request("/api/v2/management/workspaces", token)
    except HTTPError as exc:
        assert exc.code == 403
    else:
        raise AssertionError("Runtime token reached management API")
    conv = request(f"/api/v2/runtime/agents/{agent['id']}/conversations", token, {})
    text = agent["suggested_questions"][0]
    body = {"text": text, "client_message_id": str(uuid.uuid4())}
    accepted = request(f"/api/v2/runtime/conversations/{conv['id']}/messages", token, body)
    deadline = time.monotonic() + 30
    while True:
        run = request(f"/api/v2/runtime/runs/{accepted['run_id']}", token)
        if run["status"] == "SUCCEEDED":
            break
        assert time.monotonic() < deadline, "Synthetic Worker run did not finish"
        assert run["status"] not in {"FAILED", "UNKNOWN", "CANCELLED"}, run["status"]
        time.sleep(0.2)
    assert len(run["calls"]) == 1
    duplicate = request(f"/api/v2/runtime/conversations/{conv['id']}/messages", token, body)
    assert duplicate["run_id"] == accepted["run_id"]
    events = request(f"/api/v2/runtime/conversations/{conv['id']}/events?after=0", token)
    assert events == request(f"/api/v2/runtime/conversations/{conv['id']}/events?after=0", token)
    assert len(request(f"/api/v2/runtime/runs/{accepted['run_id']}", token)["calls"]) == 1
    request("/api/v2/runtime/sessions/logout", token, {})
    intake = upload_synthetic_document(manager, str(uuid.uuid4()))
    intake_path = f"/api/v2/management/workspaces/ws-dev/ingest-jobs/{intake['job_id']}"
    deadline = time.monotonic() + 30
    while True:
        job = request(intake_path, manager)
        if job["status"] == "SUCCEEDED":
            break
        assert job["status"] not in {"FAILED"} and time.monotonic() < deadline, "Synthetic intake worker did not finish"
        time.sleep(0.2)
    version = request(f"/api/v2/management/workspaces/ws-dev/documents/{job['result']['document_id']}/versions/1", manager)
    assert version["revision"] == 1 and version["text"].startswith("# Offline synthetic intake")
    return {"status": "passed", "environment": "isolated-local-development", "external_tcp_reachable": False,
            "identity": "development", "model": "fixture", "business": "synthetic", "static_assets_checked": len(assets),
            "entry_paths": ["console", "chat", "embed"], "run_id": accepted["run_id"], "call_count": 1,
            "events_count": len(events), "duplicate_message_reused_run": True, "event_recovery_equal": True,
            "ingest_worker_completed": True,
            "runtime_management_denied": True, "acceptance_evidence": False}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
