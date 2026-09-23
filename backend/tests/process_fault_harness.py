"""Opt-in process-fault harness. Checkpoints exist only in this test process.

No production module is edited, no lease timestamp is changed, and no business
endpoint outside an explicitly granted loopback fixture is used.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import re
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import uuid

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
TARGET_DATABASE = "intent_process_faults_test"
CHECKPOINT = "PROCESS_FAULT_CHECKPOINT "
sys.path.insert(0, str(BACKEND / "src"))


def stamp():
    return datetime.now(timezone.utc).isoformat()


def guard_environment():
    from sqlalchemy.engine import make_url
    if os.environ.get("INTENT_PROCESS_FAULTS_ENABLED") != "1":
        raise RuntimeError("Use the dedicated process-fault launcher explicitly")
    runtime = make_url(os.environ["INTENT_DATABASE_URL"])
    owner = make_url(os.environ["INTENT_MIGRATION_DATABASE_URL"])
    if (runtime.database, owner.database) != (TARGET_DATABASE, TARGET_DATABASE):
        raise RuntimeError("Only the dedicated process-fault database is allowed")
    if (runtime.host, runtime.port) != (owner.host, owner.port) or runtime.host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Both database URLs must identify the same local server")
    if runtime.username != "intent_app" or owner.username != "intent_owner":
        raise RuntimeError("Restricted runtime and separate migration roles required")
    if os.environ.get("INTENT_ENV") != "test":
        raise RuntimeError("Process faults require the test environment")
    report_dir = Path(os.environ["INTENT_PROCESS_FAULT_REPORT_DIR"]).resolve()
    if not report_dir.is_relative_to((PROJECT / "output" / "process-faults").resolve()) or not report_dir.is_dir():
        raise RuntimeError("A new launcher-owned report directory is required")
    return runtime, owner, report_dir


def safe_text(value):
    # No request headers or tokens are intentionally logged. These are a final
    # safeguard for exception text from subprocesses or connection libraries.
    text = str(value)
    text = re.sub(r"(?i)(Bearer|Basic)\s+\S+", r"\1 [REDACTED]", text)
    text = re.sub(r"(postgresql(?:\+psycopg)?://[^:/\s]+:)[^@\s]+@", r"\1[REDACTED]@", text)
    for secret in ("local-app-only", "local-owner-only"):
        text = text.replace(secret, "[REDACTED]")
    return text


def wait_for(predicate, *, seconds=35, description="condition"):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        result = predicate()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError("Timed out waiting for " + description)


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def checkpoint(phase, **data):
    print(CHECKPOINT + json.dumps({"phase": phase, "pid": os.getpid(), "at": stamp(), **data}), flush=True)
    # Parent must terminate the real OS process; this never releases normally.
    threading.Event().wait()


def child_main(role, port, phase, run_id, conversation_id):
    guard_environment()
    if role == "worker":
        from intent_platform import worker
        if phase == "before-dispatch":
            original = worker.mark_dispatch
            def before(work):
                if work["run_id"] == run_id:
                    checkpoint(phase, run_id=run_id, call_id=work["id"], attempt_id=work.get("attempt_id"))
                return original(work)
            worker.mark_dispatch = before
        elif phase == "after-downstream":
            original = worker.finish_call
            def after(work, result):
                if work["run_id"] == run_id:
                    if result.get("status") != "SUCCEEDED":
                        raise RuntimeError("Synthetic downstream did not actually succeed")
                    checkpoint(phase, run_id=run_id, call_id=work["id"], attempt_id=work.get("attempt_id"), actual_result_status=result["status"])
                return original(work, result)
            worker.finish_call = after
        worker.main()
        return
    import uvicorn
    from intent_platform.main import app
    served_app = app
    if phase == "before-events-delivery":
        async def served_app(scope, receive, send):
            async def gated(message):
                if (scope.get("path") == f"/api/v2/runtime/conversations/{conversation_id}/events"
                        and message["type"] == "http.response.body" and message.get("body")):
                    batch = json.loads(message["body"])
                    if isinstance(batch, list) and any(e.get("run_id") == run_id and e.get("event_type") == "RESULT" for e in batch):
                        checkpoint(phase, run_id=run_id, conversation_id=conversation_id,
                                   sequences=[e["sequence"] for e in batch])
                await send(message)
            await app(scope, receive, gated)
    uvicorn.run(served_app, host="127.0.0.1", port=port, access_log=False, log_level="warning")


class Child:
    def __init__(self, env, role, *, port=0, phase="normal", run_id="", conversation_id=""):
        self.lines = []
        self.checkpoints = queue.Queue()
        self.started = stamp()
        args = [sys.executable, "-u", str(Path(__file__).resolve()), "child", role, "--port", str(port),
                "--phase", phase, "--run-id", run_id, "--conversation-id", conversation_id]
        self.process = subprocess.Popen(args, cwd=BACKEND, env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.role, self.phase = role, phase
        def read():
            for line in self.process.stdout:
                line = line.rstrip()
                self.lines.append(safe_text(line))
                self.lines[:] = self.lines[-100:]
                if line.startswith(CHECKPOINT):
                    self.checkpoints.put(json.loads(line[len(CHECKPOINT):]))
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def barrier(self, seconds=25):
        try:
            return self.checkpoints.get(timeout=seconds)
        except queue.Empty:
            raise AssertionError("Child missed checkpoint: " + safe_text(self.lines[-12:])) from None

    def stop(self):
        alive = self.process.poll() is None
        if alive:
            self.process.kill()  # TerminateProcess on Windows; SIGKILL on POSIX.
        self.process.wait(timeout=10)
        self.reader.join(timeout=3)
        self.process.stdout.close()
        return {"role": self.role, "phase": self.phase, "pid": self.process.pid,
                "started_at": self.started, "stopped_at": stamp(), "kill_sent": alive,
                "exit_code": self.process.returncode, "mechanism": "TerminateProcess" if os.name == "nt" else "SIGKILL"}


class FaultCase:
    def __init__(self, name):
        _, _, self.report_dir = guard_environment()
        self.name, self.identifier = name, uuid.uuid4().hex[:12]
        self.children = []
        self.ledger = []
        self.ledger_lock = threading.Lock()
        self.evidence = {"schema_version": "1", "case": name, "trial_id": self.identifier, "started_at": stamp(),
                         "database": TARGET_DATABASE, "fixture_mode": True, "business_data": "synthetic-loopback",
                         "checkpoint_injection": "test-only wrappers; actual OS process termination",
                         "lease_clock": "natural wall-clock expiry; no timestamp mutation",
                         "frontend_dom_tested": False, "acceptance_evidence": False, "processes": []}

    def __enter__(self):
        try:
            return self._start_case()
        except BaseException as error:
            self.__exit__(type(error), error, error.__traceback__)
            raise

    def _start_case(self):
        case = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                with case.ledger_lock:
                    case.ledger.append({"at": stamp(), "method": "POST", "path": self.path})
                body = json.dumps({"status": "succeeded", "data": {"synthetic": True, "fixture_case": case.name},
                                   "operation_id": "synthetic-" + case.identifier}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("INTENT_")}
        for key in ("INTENT_DATABASE_URL", "INTENT_MIGRATION_DATABASE_URL", "INTENT_PROCESS_FAULTS_ENABLED", "INTENT_PROCESS_FAULT_REPORT_DIR"):
            self.env[key] = os.environ[key]
        self.env.update({"INTENT_ENV": "test", "INTENT_MODEL_MODE": "fixture", "INTENT_ALLOW_DEV_AUTH": "true",
                         "INTENT_WORKER_LEASE_SECONDS": "1", "INTENT_WORKER_POLL_SECONDS": "0.1", "INTENT_MODEL_TIMEOUT": "1",
                         "INTENT_CONFIRMATION_SECONDS": "300", "INTENT_TOKEN_SECONDS": "3600",
                         "INTENT_CONNECTOR_TARGETS_JSON": json.dumps({"ws-dev": [{"origin": self.origin, "path_prefix": "/fixture", "kinds": ["http"]}]}),
                         "INTENT_CREDENTIALS_JSON": "{}", "INTENT_INTEGRATIONS_JSON": "{}", "INTENT_RESOLVERS_JSON": "{}",
                         "INTENT_MEMBERSHIP_GRANTS_JSON": "{}", "INTENT_WORKSPACE_LIMITS_JSON": "{}",
                         "INTENT_OBJECT_AUTHORIZATION_URL": "", "INTENT_IDENTITY_INTROSPECTION_URL": ""})
        self.port = unused_port()
        import httpx
        self.http = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=20, trust_env=False)
        self.api = self.start_api()
        self.prepare()
        return self

    def start(self, role, **kwargs):
        child = Child(self.env, role, **kwargs)
        self.children.append(child)
        return child

    def stop(self, child):
        detail = child.stop()
        self.evidence["processes"].append(detail)
        self.children.remove(child)
        return detail

    def start_api(self, phase="normal"):
        child = self.start("api", port=self.port, phase=phase,
                           run_id=getattr(self, "rid", ""), conversation_id=getattr(self, "cid", ""))
        def ready():
            if child.process.poll() is not None:
                raise AssertionError("API process failed: " + safe_text(child.lines[-12:]))
            try:
                return self.http.get("/health", timeout=0.5).status_code == 200
            except Exception:
                return False
        wait_for(ready, description="real HTTP API readiness")
        return child

    def request(self, method, path, *, runtime=False, expected=200, **kwargs):
        headers = self.runtime_headers if runtime else getattr(self, "admin_headers", {})
        result = self.http.request(method, path, headers=headers, **kwargs)
        if result.status_code != expected:
            raise AssertionError(f"{method} {path}: expected {expected}, received {result.status_code}")
        return result.json()

    def prepare(self):
        login = self.request("POST", "/api/v2/dev/login", json={"user_id": "dev-admin"})
        self.admin_headers = {"Authorization": "Bearer " + login["access_token"]}
        mg = "/api/v2/management/workspaces/ws-dev"
        self.alias = "process-fault-" + self.identifier
        definition = {"name": self.alias, "description": "Synthetic local process-fault write; not a bank API", "aliases": [self.alias],
                      "effect": "WRITE", "requires_confirmation": True,
                      "connector": {"kind": "http", "version": "1", "method": "POST", "endpoint": self.origin + "/fixture/" + self.identifier,
                                    "timeout_seconds": 1, "status_path": "status", "result_path": "data", "operation_id_path": "operation_id"}}
        function = self.request("POST", mg + "/functions/import", expected=201, json={"definition": definition})
        fid = function["id"]
        validation = self.request("POST", mg + f"/functions/{fid}/validate", json={"revision": function["revision"], "probe": True, "allow_effects": True, "arguments": {}})
        assert validation["valid"], "Synthetic connector validation failed"
        self.request("POST", mg + f"/functions/{fid}/publish", json={"revision": function["revision"]})
        app = self.request("POST", mg + "/agents", expected=201, json={"name": self.alias, "capability_ids": [fid]})
        release = self.request("POST", mg + f"/agents/{app['id']}/publish", json={"revision": app["revision"]})
        runtime = self.request("POST", f"/api/v2/runtime/apps/{app['public_id']}/sessions", json={})
        self.runtime_headers = {"Authorization": "Bearer " + runtime["access_token"]}
        self.cid = self.request("POST", f"/api/v2/runtime/agents/{app['id']}/conversations", runtime=True, expected=201, json={})["id"]
        self.rid = self.request("POST", f"/api/v2/runtime/conversations/{self.cid}/messages", runtime=True, expected=202,
                                json={"text": self.alias, "client_message_id": self.identifier})["run_id"]
        self.baseline = self.count()
        assert self.baseline == 1, "Exactly one explicit validation probe expected before runtime"
        self.evidence.update({"agent_id": app["id"], "function_id": fid, "release_id": release["release_id"],
                              "runtime_session_id": runtime["runtime_session_id"], "conversation_id": self.cid, "run_id": self.rid,
                              "explicit_setup_probe_calls": self.baseline})

    def count(self):
        with self.ledger_lock:
            return len(self.ledger)

    def run(self):
        return self.request("GET", f"/api/v2/runtime/runs/{self.rid}", runtime=True)

    def worker_and_confirm(self, phase="normal"):
        worker = self.start("worker", phase=phase, run_id=self.rid)
        run = wait_for(lambda: (r if (r := self.run())["status"] == "WAITING_CONFIRMATION" else None), description="normal confirmation gate")
        assert self.count() == self.baseline and not run["calls"]
        events = self.request("GET", f"/api/v2/runtime/conversations/{self.cid}/events?after=0", runtime=True)
        self.cursor = max(e["sequence"] for e in events)
        self.evidence.update({"confirmed_revision": run["revision"], "confirmation_zero_runtime_dispatch": True, "client_cursor_before_execution": self.cursor})
        self.request("POST", f"/api/v2/runtime/runs/{self.rid}/confirmations", runtime=True,
                     json={"revision": run["revision"], "confirmed": True})
        return worker

    def snapshot(self):
        from sqlalchemy import select, text
        from sqlalchemy.orm import Session
        from intent_platform.db import engine, set_identity
        from intent_platform.models import Run, Call, Event, ProcessingAttempt
        with Session(engine) as db:
            assert db.execute(text("SELECT current_user")).scalar() == "intent_app"
            assert db.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")).scalar() is False
            set_identity(db, "dev-admin")
            run = db.get(Run, self.rid)
            calls = list(db.scalars(select(Call).where(Call.run_id == self.rid)))
            events = list(db.scalars(select(Event).where(Event.run_id == self.rid).order_by(Event.sequence)))
            attempts = list(db.scalars(select(ProcessingAttempt).where(ProcessingAttempt.run_id == self.rid)))
            return {"at": stamp(), "run_status": run.status, "run_revision": run.revision,
                    "calls": [{"id": c.id, "status": c.status, "revision": c.revision, "lease_until": c.lease_until.isoformat() if c.lease_until else None,
                               "dispatched_at": c.dispatched_at.isoformat() if c.dispatched_at else None, "result_present": bool(c.result)} for c in calls],
                    "events": [{"sequence": e.sequence, "type": e.event_type} for e in events],
                    "attempts": [{"id": a.id, "kind": a.kind, "status": a.status, "lease_until": a.lease_until.isoformat()} for a in attempts],
                    "runtime_downstream_posts": self.count() - self.baseline}

    def __exit__(self, kind, error, tb):
        self.evidence["status"] = "passed" if error is None else "failed"
        if error is not None:
            self.evidence["failure"] = {"type": kind.__name__, "message": safe_text(error)}
        for child in list(self.children):
            self.stop(child)
        if hasattr(self, "http"):
            self.http.close()
        if hasattr(self, "server"):
            self.server.shutdown(); self.server.server_close(); self.server_thread.join(timeout=3)
        self.evidence.update({"finished_at": stamp(), "downstream_ledger": self.ledger,
                              "runtime_downstream_posts": self.count() - self.baseline if hasattr(self, "baseline") else None})
        target = self.report_dir / f"{self.name}-{self.identifier}.json"
        with target.open("x", encoding="utf-8") as stream:
            json.dump(self.evidence, stream, ensure_ascii=False, indent=2)
            stream.flush(); os.fsync(stream.fileno())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["child"])
    parser.add_argument("role", choices=["api", "worker"])
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--phase", default="normal", choices=["normal", "before-dispatch", "after-downstream", "before-events-delivery"])
    parser.add_argument("--run-id", default="")
    parser.add_argument("--conversation-id", default="")
    args = parser.parse_args()
    child_main(args.role, args.port, args.phase, args.run_id, args.conversation_id)
