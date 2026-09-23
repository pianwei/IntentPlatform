"""Bounded local-candidate load diagnosis with synthetic development resources.

The script changes ws-dev's queue limit for one admission race and restores the
original limit in ``finally``. It creates only synthetic conversations and intake
documents. Its latency numbers are engineering observations, not bank acceptance.
"""

import argparse
import json
import math
import statistics
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.request import Request, urlopen


WORKSPACE = "/api/v2/management/workspaces/ws-dev"
MESSAGE = "查询资料 record_id=demo-001"
TERMINAL = {"SUCCEEDED", "FAILED", "UNKNOWN", "CANCELLED"}


@dataclass
class Reply:
    status: int
    value: dict | list | None
    headers: dict
    elapsed_ms: float


class Client:
    def __init__(self, base, token=None):
        self.base = base.rstrip("/")
        self.token = token

    def call(self, path, body=None, *, method=None, content_type="application/json", key=None,
             expected=(200, 201, 202)):
        headers = {"Content-Type": content_type}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if key:
            headers["Idempotency-Key"] = key
        data = None if body is None else (json.dumps(body).encode() if content_type == "application/json" else body)
        operation = method or ("POST" if data is not None else "GET")
        started = time.monotonic()
        try:
            with urlopen(Request(self.base + path, data=data, headers=headers, method=operation), timeout=20) as response:
                status, raw, reply_headers = response.status, response.read(2_000_001), dict(response.headers)
        except HTTPError as error:
            status, raw, reply_headers = error.code, error.read(2_000_001), dict(error.headers)
        elapsed = (time.monotonic() - started) * 1000
        if len(raw) > 2_000_000:
            raise AssertionError("Candidate response exceeded diagnostic read cap")
        if status not in expected:
            raise AssertionError(f"{operation} {path} returned HTTP {status}; expected {expected}")
        parsed = json.loads(raw) if raw and "application/json" in reply_headers.get("Content-Type", "") else None
        return Reply(status, parsed, reply_headers, elapsed)


def percentile(values, percent):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * percent / 100) - 1)], 2)


def timing(values):
    return {"count": len(values), "min_ms": round(min(values), 2) if values else None,
            "mean_ms": round(statistics.mean(values), 2) if values else None,
            "p50_ms": percentile(values, 50), "p95_ms": percentile(values, 95),
            "p99_ms": percentile(values, 99), "max_ms": round(max(values), 2) if values else None}


def conversation(client, agent_id):
    return client.call(f"/api/v2/runtime/agents/{agent_id}/conversations", {}).value["id"]


def submit_and_wait(client, cid, *, text=MESSAGE, timeout=40):
    started = time.monotonic()
    reply = client.call(f"/api/v2/runtime/conversations/{cid}/messages",
                        {"text": text, "client_message_id": str(uuid.uuid4())})
    run_id = reply.value["run_id"]
    deadline = started + timeout
    while time.monotonic() < deadline:
        status = client.call(f"/api/v2/runtime/runs/{run_id}").value["status"]
        if status in TERMINAL or status in {"WAITING_INPUT", "WAITING_CONFIRMATION"}:
            return {"status": status, "elapsed_ms": (time.monotonic() - started) * 1000,
                    "submit_ms": reply.elapsed_ms}
        time.sleep(0.12)
    return {"status": "TIMEOUT", "elapsed_ms": (time.monotonic() - started) * 1000,
            "submit_ms": reply.elapsed_ms}


def summarize_runs(rows, extra=None):
    counts = dict(sorted(Counter(row["status"] for row in rows).items()))
    summary = {"sent": len(rows), "statuses": counts,
               "end_to_end": timing([row["elapsed_ms"] for row in rows]),
               "submit_http": timing([row["submit_ms"] for row in rows]),
               "passed": counts == {"SUCCEEDED": len(rows)}}
    if extra:
        summary.update(extra)
    return summary


def sustained(client, agent_id, workers=5, turns=2):
    cids = [conversation(client, agent_id) for _ in range(workers)]
    def one(cid):
        return [submit_and_wait(client, cid) for _ in range(turns)]
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = [row for batch in pool.map(one, cids) for row in batch]
    return summarize_runs(rows, {"concurrent_flows": workers, "turns_per_flow": turns,
                                 "wall_ms": round((time.monotonic() - started) * 1000, 2)})


def fixed_arrival(client, agent_id, count=8, interval=0.2):
    cids = [conversation(client, agent_id) for _ in range(count)]
    epoch = time.monotonic() + 0.2
    def one(index):
        due = epoch + index * interval
        time.sleep(max(0, due - time.monotonic()))
        slipped_ms = max(0, (time.monotonic() - due) * 1000)
        return submit_and_wait(client, cids[index]) | {"schedule_slip_ms": slipped_ms}
    with ThreadPoolExecutor(max_workers=count) as pool:
        rows = list(pool.map(one, range(count)))
    return summarize_runs(rows, {"target_interval_ms": round(interval * 1000, 2),
                                 "schedule_slip": timing([row["schedule_slip_ms"] for row in rows])})


def long_history(client, agent_id, turns=12):
    cid = conversation(client, agent_id)
    rows = [submit_and_wait(client, cid) for _ in range(turns)]
    history = client.call(f"/api/v2/runtime/conversations/{cid}/history")
    value = history.value
    summary = summarize_runs(rows, {"history_http_ms": round(history.elapsed_ms, 2),
                                   "history_messages": len(value["messages"]),
                                   "history_events": len(value["events"]),
                                   "history_response_bytes": len(json.dumps(value, ensure_ascii=False).encode()),
                                   "turns": turns})
    summary["passed"] = summary["passed"] and len(value["messages"]) == turns
    return summary


def upload(client, index, content):
    boundary = "candidate-" + uuid.uuid4().hex
    filename = f"candidate-synthetic-{uuid.uuid4().hex[:12]}-{index}.md"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            "Content-Type: text/markdown\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return client.call(WORKSPACE + "/ingest-jobs/documents", body,
                       content_type=f"multipart/form-data; boundary={boundary}", key=str(uuid.uuid4()))


def parallel_ingest(client, count=6):
    content = b"# Synthetic candidate document\n" + b"Local test content only.\n" * 1000
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=count) as pool:
        accepted = list(pool.map(lambda index: upload(client, index, content), range(count)))
    ids = [reply.value["job_id"] for reply in accepted]
    statuses = {}
    health_times = []
    deadline = time.monotonic() + 40
    while len(statuses) < count and time.monotonic() < deadline:
        # Exercise online API while the separate intake worker parses documents.
        health = client.call("/health")
        health_times.append(health.elapsed_ms)
        for job_id in ids:
            if job_id in statuses:
                continue
            status = client.call(WORKSPACE + f"/ingest-jobs/{job_id}").value["status"]
            if status in {"SUCCEEDED", "FAILED"}:
                statuses[job_id] = status
        if len(statuses) < count:
            time.sleep(0.15)
    counts = dict(sorted(Counter(statuses.values()).items()))
    return {"submitted": count, "accepted_202": sum(reply.status == 202 for reply in accepted),
            "terminal_statuses": counts, "unfinished": count - len(statuses),
            "upload_http": timing([reply.elapsed_ms for reply in accepted]),
            "online_health_http": timing(health_times),
            "wall_ms": round((time.monotonic() - started) * 1000, 2),
            "passed": len(statuses) == count and counts == {"SUCCEEDED": count}}


def limit_race(manager, runtime, agent_id, count=8):
    before = manager.call(WORKSPACE + "/limits").value
    if before["usage"]["queued_runs"]:
        raise AssertionError("Cannot temporarily lower a workspace limit with active queued runs")
    original = dict(before["limits"])
    reduced = original | {"max_queued_runs": 1}
    changed = False
    restored = None
    result = None
    try:
        lowered = manager.call(WORKSPACE + "/limits", {"revision": before["revision"], "limits": reduced},
                              method="PUT").value
        changed = True
        cids = [conversation(runtime, agent_id) for _ in range(count)]
        def send(cid):
            return runtime.call(f"/api/v2/runtime/conversations/{cid}/messages",
                                {"text": MESSAGE, "client_message_id": str(uuid.uuid4())}, expected=(202, 429))
        with ThreadPoolExecutor(max_workers=count) as pool:
            replies = list(pool.map(send, cids))
        counts = dict(sorted(Counter(reply.status for reply in replies).items()))
        retry_after = all(any(key.lower() == "retry-after" and value for key, value in reply.headers.items())
                          for reply in replies if reply.status == 429)
        result = {"attempted": count, "http_statuses": counts, "retry_after_present": retry_after,
                  "admission_http": timing([reply.elapsed_ms for reply in replies]),
                  "original_limits": original, "temporary_limits": lowered["limits"],
                  "original_revision": before["revision"],
                  "passed": counts.get(202, 0) >= 1 and counts.get(429, 0) >= 1 and retry_after}
    finally:
        if changed:
            current = manager.call(WORKSPACE + "/limits").value
            restored = manager.call(WORKSPACE + "/limits",
                                    {"revision": current["revision"], "limits": original}, method="PUT").value
            if restored["limits"] != original:
                raise AssertionError("Original workspace limits were not restored")
    if result is None:
        raise AssertionError("Admission race did not run")
    result["final_limits"] = restored["limits"]
    result["final_revision"] = restored["revision"]
    result["persistent_audit_operations"] = 2
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:5173")
    args = parser.parse_args()
    anonymous = Client(args.base)
    health = anonymous.call("/health").value
    if health.get("environment") != "development" or health.get("fixture_mode") is not True:
        raise AssertionError("Only a local development fixture environment is allowed")
    manager = anonymous.call("/api/v2/dev/login", {"user_id": "dev-admin"}).value
    client = Client(args.base, manager["access_token"])
    seeded = client.call("/api/v2/dev/seed", {}).value
    agent = seeded["agent"]
    runtime = client.call(f"/api/v2/runtime/apps/{agent['public_id']}/sessions", {}).value
    run_client = Client(args.base, runtime["access_token"])
    result = {"status": "passed", "environment": "local-development-compose",
              "identity": "development", "model": "fixture", "business": "synthetic",
              "acceptance_evidence": False, "cases": {}}
    result["cases"]["sustained_inflight"] = sustained(run_client, agent["id"])
    result["cases"]["fixed_arrival"] = fixed_arrival(run_client, agent["id"])
    result["cases"]["long_history"] = long_history(run_client, agent["id"])
    result["cases"]["parallel_ingest"] = parallel_ingest(client)
    result["cases"]["rate_limit"] = limit_race(client, run_client, agent["id"])
    if not all(case["passed"] for case in result["cases"].values()):
        result["status"] = "failed"
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
