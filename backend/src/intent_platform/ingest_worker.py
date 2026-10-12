"""Separate PostgreSQL intake worker with leased, fenced document commits."""
import logging
import threading
import time
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session
from .auth import audit, manager
from .capacity import lock_workspace
from .config import settings
from .db import engine, now, set_identity, uid
from .document_versions import create_document, get_version, sections
from .documents import extract_document
from .models import AuthToken, Document, DocumentVersion, IngestJob, Membership, OpenAPIImport
from .openapi_import import PARSER_VERSION, parse_openapi
from .services import get_resource

log = logging.getLogger(__name__)


def _authorized(db, job):
    from .workspace_lifecycle import workspace_available
    if not workspace_available(db, job.workspace_id, job.workspace_epoch):
        return False
    token = db.get(AuthToken, job.token_id)
    try:
        if not token or token.user_id != job.created_by:
            return False
        manager(db, token, job.workspace_id)
        return True
    except HTTPException:
        return False


def _claim(user_id, ws):
    with Session(engine, expire_on_commit=False) as db, db.begin():
        set_identity(db, user_id)
        lock_workspace(db, ws)
        active = db.scalar(select(func.count(IngestJob.id)).where(IngestJob.workspace_id == ws,
                           IngestJob.status == "RUNNING", IngestJob.lease_until > now())) or 0
        if active >= settings().ingest_max_concurrency_per_workspace:
            return None
        row = db.scalar(select(IngestJob).where(IngestJob.workspace_id == ws, IngestJob.created_by == user_id,
                        or_(IngestJob.status == "PENDING",
                            (IngestJob.status == "RUNNING") & (IngestJob.lease_until <= now())))
                        .order_by(IngestJob.created_at, IngestJob.id).with_for_update(skip_locked=True).limit(1))
        if not row:
            return None
        if not _authorized(db, row):
            row.status = "FAILED"; row.error = {"code": "AUTH_REVOKED", "message": "Intake authorization is no longer valid"}
            row.lease_until = None; row.attempt_id = None; row.updated_at = now()
            return {"done": True}
        row.status = "RUNNING"; row.attempt_id = uid("ingest_attempt")
        row.started_at = now(); row.updated_at = row.started_at
        row.lease_until = now() + timedelta(seconds=settings().ingest_lease_seconds)
        return {"id": row.id, "user_id": user_id, "workspace_id": ws, "attempt_id": row.attempt_id,
                "kind": row.kind, "filename": row.filename, "payload": row.payload,
                "document_id": row.document_id, "source_revision": row.source_revision}


def _sweep_revoked(user_id, ws):
    """Fail inaccessible submitters using another current member of that workspace."""
    with Session(engine) as db, db.begin():
        set_identity(db, user_id)
        lock_workspace(db, ws)
        active_submitter = exists(select(Membership.user_id).where(
            Membership.workspace_id == IngestJob.workspace_id,
            Membership.user_id == IngestJob.created_by,
            Membership.active.is_(True)))
        row = db.scalar(select(IngestJob).where(IngestJob.workspace_id == ws,
                        IngestJob.status.in_(["PENDING", "RUNNING"]), ~active_submitter)
                        .order_by(IngestJob.created_at).with_for_update(skip_locked=True).limit(1))
        if not row:
            return False
        row.status = "FAILED"
        row.error = {"code": "AUTH_REVOKED", "message": "Intake authorization is no longer valid"}
        row.attempt_id = None; row.lease_until = None; row.updated_at = now()
        return True


def claim_one():
    # Memberships are global worker scheduling metadata. Normal claims use
    # their submitter's identity; revoked-submitter cleanup uses another active
    # member of the same workspace. Both obey this table's FORCE RLS.
    with Session(engine) as db:
        users = list(db.scalars(select(Membership.user_id).where(Membership.active.is_(True)).distinct()))
    for user_id in users:
        with Session(engine) as db:
            set_identity(db, user_id)
            spaces = list(db.scalars(select(Membership.workspace_id).where(
                Membership.user_id == user_id, Membership.active.is_(True))))
        for ws in sorted(spaces):
            if _sweep_revoked(user_id, ws):
                return {"done": True}
            work = _claim(user_id, ws)
            if work:
                return work
    return None


def heartbeat(work):
    with Session(engine) as db, db.begin():
        set_identity(db, work["user_id"])
        row = db.scalar(select(IngestJob).where(IngestJob.id == work["id"]).with_for_update(skip_locked=True))
        if not row or row.status != "RUNNING" or row.attempt_id != work["attempt_id"] or row.lease_until <= now():
            return False
        row.lease_until = now() + timedelta(seconds=settings().ingest_lease_seconds)
        row.updated_at = now()
        return True


def _prepare(work):
    if work["kind"] in {"document.create", "document.revise"}:
        content = extract_document(work["filename"], work["payload"])
        # Run the finite text/section budget before opening a write transaction.
        sections(content)
        return content
    with Session(engine) as db:
        set_identity(db, work["user_id"])
        version = get_version(db, work["workspace_id"], work["document_id"], work["source_revision"])
        content = version.text
    return parse_openapi(content)


def _diagnostics(report):
    rows = report.get("diagnostics", [])
    if not rows:
        rows = [d for operation in report.get("operations", []) for d in operation.get("diagnostics", [])]
    return [{"code": str(d.get("code", "unsupported"))[:80],
             "message": str(d.get("message", "Unsupported operation"))[:500],
             "pointer": str(d.get("pointer", "#"))[:300]}
            for d in rows[:20]]


def finish(work, prepared=None, error=None):
    """Commit the result and terminal state once, only for the current lease."""
    with Session(engine, expire_on_commit=False) as db, db.begin():
        set_identity(db, work["user_id"])
        lock_workspace(db, work["workspace_id"])
        row = db.scalar(select(IngestJob).where(IngestJob.id == work["id"]).with_for_update())
        if not row or row.status != "RUNNING" or row.attempt_id != work["attempt_id"] or row.lease_until <= now():
            return False
        if error:
            row.status = "FAILED"; row.error = error
        elif not _authorized(db, row):
            row.status = "FAILED"; row.error = {"code": "AUTH_REVOKED", "message": "Intake authorization is no longer valid"}
        elif row.kind == "openapi.parse" and not prepared["valid"]:
            row.status = "FAILED"
            row.error = {"code": "OPENAPI_UNSUPPORTED", "message": "No supported OpenAPI operation was found",
                         "diagnostics": _diagnostics(prepared)}
        elif row.kind == "document.create":
            try:
                document, version = create_document(db, row.workspace_id, row.filename, prepared,
                                                    row.payload, row.created_by)
            except ValueError:
                row.status = "FAILED"
                row.error = {"code": "DOCUMENT_PARSE_ERROR", "message": "Document exceeds the supported text or section boundary"}
            else:
                row.result = {"document_id": document.id, "revision": version.revision}
                row.status = "SUCCEEDED"
                audit(db, row.created_by, row.workspace_id, "document.parse", document.id, {"revision": version.revision})
        elif row.kind == "document.revise":
            document = get_resource(db, Document, row.document_id, row.workspace_id, lock=True)
            if document.latest_revision != row.source_revision:
                row.status = "FAILED"
                row.error = {"code": "SOURCE_REVISION_CHANGED", "message": "Document revision changed before intake completed"}
            else:
                previous = get_version(db, row.workspace_id, row.document_id, row.source_revision)
                try:
                    parts = sections(prepared, previous.sections)
                except ValueError:
                    row.status = "FAILED"
                    row.error = {"code": "DOCUMENT_PARSE_ERROR", "message": "Document exceeds the supported text or section boundary"}
                else:
                    document.latest_revision += 1
                    version = DocumentVersion(workspace_id=row.workspace_id, document_id=document.id,
                                              revision=document.latest_revision, filename=row.filename,
                                              text=prepared, original=row.payload, sections=parts,
                                              created_by=row.created_by)
                    db.add(version); db.flush()
                    row.result = {"document_id": document.id, "revision": version.revision}
                    row.status = "SUCCEEDED"
                    audit(db, row.created_by, row.workspace_id, "document.revise", document.id, {"revision": version.revision})
        elif row.kind == "openapi.parse":
            version = get_version(db, row.workspace_id, row.document_id, row.source_revision)
            item = OpenAPIImport(workspace_id=row.workspace_id, document_id=version.document_id,
                                 document_revision=version.revision, parser_version=PARSER_VERSION,
                                 report=prepared, created_by=row.created_by)
            db.add(item); db.flush()
            row.result = {"import_id": item.id, "document_id": version.document_id, "revision": version.revision}
            row.status = "SUCCEEDED"
            audit(db, row.created_by, row.workspace_id, "openapi.parse", item.id, {"valid": True})
        row.lease_until = None; row.attempt_id = None; row.updated_at = now()
        return True


def process(work):
    if work.get("done"):
        return
    stop = threading.Event()

    def renew():
        while not stop.wait(max(1, settings().ingest_lease_seconds // 3)):
            try:
                if not heartbeat(work):
                    return
            except Exception:
                log.exception("Intake heartbeat failed; the fenced attempt may be recovered")

    keeper = threading.Thread(target=renew, daemon=True)
    keeper.start()
    try:
        try:
            prepared = _prepare(work)
            failure = None
        except Exception:
            prepared = None
            failure = {"code": "DOCUMENT_PARSE_ERROR" if work["kind"] != "openapi.parse" else "OPENAPI_PARSE_ERROR",
                       "message": "Source cannot be parsed within the supported format and size boundary"}
    finally:
        stop.set(); keeper.join(timeout=2)
    try:
        finish(work, prepared, failure)
    except Exception:
        # An unexpected database failure never becomes an invented success or
        # permanent business failure. The expired lease allows a fenced retry.
        log.exception("Intake commit failed; lease recovery will retry")


def tick():
    work = claim_one()
    if not work:
        return False
    process(work)
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    log.info("Intake worker ready: PostgreSQL")
    while True:
        try:
            if not tick():
                time.sleep(settings().worker_poll_seconds)
        except KeyboardInterrupt:
            return
        except Exception:
            log.exception("Intake worker iteration failed")
            time.sleep(2)


if __name__ == "__main__":
    main()
