"""E2 local PostgreSQL contract tests; all source content is synthetic."""
import json
import subprocess
import sys
import threading
import uuid
from datetime import timedelta
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from intent_platform.config import settings
from intent_platform.db import engine, now, set_identity
from intent_platform.ingest_worker import claim_one, finish, process, tick
from intent_platform.models import AuthToken, Document, DocumentVersion, IngestJob, Membership, OpenAPIImport
from test_openapi_parser import specification

WS = "/api/v2/management/workspaces/ws-dev"
JOBS = WS + "/ingest-jobs"


def headers(admin, key=None):
    return {**admin, "Idempotency-Key": key or str(uuid.uuid4())}


def enqueue_file(client, admin, content=b"# Finite\nexample", filename="source.md", key=None):
    result = client.post(JOBS + "/documents", headers=headers(admin, key), files={"file": (filename, content)})
    assert result.status_code == 202, result.text
    return result.json()


def status(client, admin, job_id):
    response = client.get(JOBS + "/" + job_id, headers=admin)
    assert response.status_code == 200, response.text
    return response.json()


def test_queued_document_space_rls_and_synchronous_compatibility(client, admin):
    queued = enqueue_file(client, admin)
    job_id = queued["job_id"]
    assert queued["status"] == "PENDING"
    other = client.post("/api/v2/dev/login", json={"user_id": "dev-other"}).json()["access_token"]
    assert client.get(JOBS + "/" + job_id, headers={"Authorization": "Bearer " + other}).status_code == 403
    with Session(engine) as db:
        row = db.execute(text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='ingest_jobs'")).one()
        assert row == (True, True)
        assert db.get(IngestJob, job_id) is None
        set_identity(db, "dev-other")
        assert db.get(IngestJob, job_id) is None
    assert tick()
    completed = status(client, admin, job_id)
    assert completed["status"] == "SUCCEEDED" and completed["error"] is None
    document_id = completed["result"]["document_id"]
    assert completed["result"]["revision"] == 1
    response = client.get(WS + f"/documents/{document_id}/versions/1", headers=admin)
    assert response.status_code == 200 and response.json()["text"] == "# Finite\nexample"
    legacy = client.post(WS + "/documents", headers=admin, files={"file": ("legacy.txt", b"legacy")})
    assert legacy.status_code == 201 and legacy.json()["revision"] == 1


def test_runtime_token_cannot_submit_or_read_management_job(client, admin, seeded):
    queued = enqueue_file(client, admin, b"management only", "management.txt")
    app = seeded["agent"]
    session = client.post(f"/api/v2/runtime/apps/{app['public_id']}/sessions", headers=admin, json={})
    assert session.status_code == 200, session.text
    runtime = {"Authorization": "Bearer " + session.json()["access_token"]}
    denied_post = client.post(JOBS + "/documents", headers=headers(runtime), files={"file": ("no.txt", b"no")})
    assert denied_post.status_code == 403
    assert client.get(JOBS + "/" + queued["job_id"], headers=runtime).status_code == 403
    assert tick()


def test_exact_idempotency_and_conflicting_reuse(client, admin):
    key = str(uuid.uuid4())
    first = enqueue_file(client, admin, b"same content", "same.txt", key)
    second = enqueue_file(client, admin, b"same content", "same.txt", key)
    assert second == first
    conflicting = client.post(JOBS + "/documents", headers=headers(admin, key), files={"file": ("same.txt", b"changed")})
    assert conflicting.status_code == 409
    assert tick()
    done = enqueue_file(client, admin, b"same content", "same.txt", key)
    assert done["job_id"] == first["job_id"] and done["status"] == "SUCCEEDED"
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        rows = db.scalars(select(IngestJob).where(IngestJob.idempotency_key == key)).all()
        assert len(rows) == 1


def test_failure_has_no_version_and_revision_is_idempotent(client, admin):
    bad = enqueue_file(client, admin, b"bad format", "bad.exe")
    assert tick()
    failed = status(client, admin, bad["job_id"])
    assert failed["status"] == "FAILED" and failed["result"] is None
    assert failed["error"]["code"] == "DOCUMENT_PARSE_ERROR"
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        assert db.scalar(select(Document).where(Document.filename == "bad.exe")) is None
    document = client.post(WS + "/documents", headers=admin, files={"file": ("current.md", b"# Original\ntext")}).json()
    key = str(uuid.uuid4())
    path = JOBS + f"/documents/{document['id']}/versions"
    request = {"expected_revision": "1"}
    queued = client.post(path, headers=headers(admin, key), data=request, files={"file": ("current.md", b"# Revised\ntext")})
    assert queued.status_code == 202
    assert tick()
    done = status(client, admin, queued.json()["job_id"])
    assert done["status"] == "SUCCEEDED" and done["result"]["revision"] == 2
    repeated = client.post(path, headers=headers(admin, key), data=request, files={"file": ("current.md", b"# Revised\ntext")})
    assert repeated.status_code == 202 and repeated.json()["job_id"] == queued.json()["job_id"]
    stale = client.post(path, headers=headers(admin), data=request, files={"file": ("current.md", b"new")})
    assert stale.status_code == 409
    invalid = client.post(JOBS + f"/documents/{document['id']}/versions", headers=headers(admin),
                          data={"expected_revision": "2"}, files={"file": ("bad.exe", b"cannot parse")})
    assert invalid.status_code == 202
    assert tick()
    assert status(client, admin, invalid.json()["job_id"])["status"] == "FAILED"
    versions = client.get(WS + f"/documents/{document['id']}/versions", headers=admin).json()
    assert [v["revision"] for v in versions] == [2, 1]


def test_openapi_job_success_and_invalid_report(client, admin):
    source = client.post(WS + "/documents", headers=admin,
                         files={"file": ("api.json", json.dumps(specification("http://127.0.0.1:8999")).encode())}).json()
    body = {"document_id": source["id"], "revision": 1}
    request = client.post(JOBS + "/openapi-imports", headers=headers(admin), json=body)
    assert request.status_code == 202
    assert tick()
    result = status(client, admin, request.json()["job_id"])
    assert result["status"] == "SUCCEEDED"
    imported = client.get(WS + "/openapi-imports/" + result["result"]["import_id"], headers=admin)
    assert imported.status_code == 200 and imported.json()["valid"]
    invalid_source = client.post(WS + "/documents", headers=admin, files={"file": ("invalid.json", b"{}")}).json()
    invalid = client.post(JOBS + "/openapi-imports", headers=headers(admin),
                          json={"document_id": invalid_source["id"], "revision": 1})
    assert invalid.status_code == 202
    assert tick()
    failed = status(client, admin, invalid.json()["job_id"])
    assert failed["status"] == "FAILED" and failed["result"] is None
    assert failed["error"]["code"] == "OPENAPI_UNSUPPORTED"
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        assert db.scalar(select(OpenAPIImport).where(OpenAPIImport.document_id == invalid_source["id"])) is None


def test_workspace_cap_and_expired_attempt_fence(client, admin, monkeypatch):
    monkeypatch.setattr(settings(), "ingest_max_concurrency_per_workspace", 1)
    first = enqueue_file(client, admin, b"first", "one.txt")
    second = enqueue_file(client, admin, b"second", "two.txt")
    old = claim_one()
    assert old and old["id"] == first["job_id"]
    assert claim_one() is None  # second queued while first holds the workspace slot
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin")
        row = db.get(IngestJob, old["id"])
        row.lease_until = now() - timedelta(seconds=1)
    recovered = claim_one()
    assert recovered and recovered["id"] == first["job_id"] and recovered["attempt_id"] != old["attempt_id"]
    assert finish(old, "first") is False
    process(recovered)
    assert status(client, admin, first["job_id"])["status"] == "SUCCEEDED"
    assert tick()
    assert status(client, admin, second["job_id"])["status"] == "SUCCEEDED"
    document_id = status(client, admin, first["job_id"])["result"]["document_id"]
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        rows = db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == document_id)).all()
        assert len(rows) == 1


def test_exited_claiming_process_is_recovered(client, admin):
    queued = enqueue_file(client, admin, b"after exit", "exit.txt")
    child = subprocess.run([sys.executable, "-c", "from intent_platform.ingest_worker import claim_one; print(claim_one()['id'])"],
                           capture_output=True, text=True, timeout=10, check=True)
    assert child.stdout.strip() == queued["job_id"]
    assert status(client, admin, queued["job_id"])["status"] == "RUNNING"
    with Session(engine) as db, db.begin():
        set_identity(db, "dev-admin")
        row = db.get(IngestJob, queued["job_id"])
        row.lease_until = now() - timedelta(seconds=1)
    assert tick()
    done = status(client, admin, queued["job_id"])
    assert done["status"] == "SUCCEEDED" and done["result"]["revision"] == 1
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        rows = db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == done["result"]["document_id"])).all()
        assert len(rows) == 1


def test_membership_revocation_ends_pending_job_without_processing(client, admin):
    # A second active member is enough for the trusted worker to mark the
    # submitter's job terminal while FORCE RLS keeps the revoked user out.
    reader = client.post("/api/v2/dev/login", json={"user_id": "dev-reader"})
    assert reader.status_code == 200
    queued = enqueue_file(client, admin, b"must not publish", "revoked.txt")
    try:
        with Session(engine) as db, db.begin():
            member = db.get(Membership, ("dev-admin", "ws-dev"))
            member.active = False
        assert tick()
    finally:
        with Session(engine) as db, db.begin():
            member = db.get(Membership, ("dev-admin", "ws-dev"))
            member.active = True
    failed = status(client, admin, queued["job_id"])
    assert failed["status"] == "FAILED" and failed["error"]["code"] == "AUTH_REVOKED"
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        assert db.scalar(select(Document).where(Document.filename == "revoked.txt")) is None


def test_final_section_failure_is_terminal_without_revision(client, admin, monkeypatch):
    from intent_platform import ingest_worker
    document = client.post(WS + "/documents", headers=admin, files={"file": ("base.md", b"# Base\ntext")}).json()
    path = JOBS + f"/documents/{document['id']}/versions"
    queued = client.post(path, headers=headers(admin), data={"expected_revision": "1"},
                         files={"file": ("base.md", b"# Base\nnew text")})
    assert queued.status_code == 202
    original = ingest_worker.sections

    def invalid_reuse(content, previous=()):
        if previous:
            raise ValueError("synthetic final section boundary")
        return original(content, previous)

    monkeypatch.setattr(ingest_worker, "sections", invalid_reuse)
    assert tick()
    failed = status(client, admin, queued.json()["job_id"])
    assert failed["status"] == "FAILED" and failed["error"]["code"] == "DOCUMENT_PARSE_ERROR"
    versions = client.get(WS + f"/documents/{document['id']}/versions", headers=admin).json()
    assert [v["revision"] for v in versions] == [1]


def test_backpressure_upload_cap_and_revoked_identity(client, admin, monkeypatch):
    monkeypatch.setattr(settings(), "ingest_max_queued_per_workspace", 1)
    monkeypatch.setattr(settings(), "max_upload_bytes", 8)
    too_large = client.post(JOBS + "/documents", headers=headers(admin), files={"file": ("large.txt", b"123456789")})
    assert too_large.status_code == 413
    first = enqueue_file(client, admin, b"one", "short.txt")
    full = client.post(JOBS + "/documents", headers=headers(admin), files={"file": ("other.txt", b"two")})
    assert full.status_code == 429
    with Session(engine) as db, db.begin():
        token = db.get(AuthToken, admin["Authorization"][7:])
        token.revoked = True
    assert tick()
    fresh = client.post("/api/v2/dev/login", json={"user_id": "dev-admin"})
    assert fresh.status_code == 200
    fresh_headers = {"Authorization": "Bearer " + fresh.json()["access_token"]}
    failed = status(client, fresh_headers, first["job_id"])
    assert failed["status"] == "FAILED" and failed["error"]["code"] == "AUTH_REVOKED"


def test_slow_parse_does_not_block_online_api(client, admin, monkeypatch):
    from intent_platform import ingest_worker
    pending = enqueue_file(client, admin, b"slow", "slow.txt")
    started = threading.Event(); release = threading.Event(); completed = threading.Event()
    real_extract = ingest_worker.extract_document

    def slow_extract(filename, data):
        started.set()
        assert release.wait(5)
        return real_extract(filename, data)

    monkeypatch.setattr(ingest_worker, "extract_document", slow_extract)

    def work():
        try:
            assert tick()
        finally:
            completed.set()

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    assert started.wait(5)
    try:
        assert client.get(WS + "/documents", headers=admin).status_code == 200
        assert not completed.is_set()
        assert status(client, admin, pending["job_id"])["status"] == "RUNNING"
    finally:
        release.set(); thread.join(5)
    assert completed.is_set()
    assert status(client, admin, pending["job_id"])["status"] == "SUCCEEDED"
