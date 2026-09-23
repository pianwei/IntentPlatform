"""Durable background document intake; the original synchronous API remains available."""
import re
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .auth import authenticate, audit, manager
from .capacity import lock_workspace
from .config import settings
from .db import session
from .document_versions import get_version
from .models import Document, IngestJob
from .services import get_resource
from .source_schemas import ImportRequest, IngestAccepted, IngestJobOut

router = APIRouter()
PREFIX = "/api/v2/management/workspaces/{ws}/ingest-jobs"
KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


def _key(value):
    if not KEY_PATTERN.fullmatch(value):
        raise HTTPException(422, "Idempotency-Key must be 8-128 simple characters")
    return value


def _same(row, kind, filename, payload, document_id, source_revision):
    return (row.kind == kind and row.filename == filename and row.payload == payload
            and row.document_id == document_id and row.source_revision == source_revision)


def _accepted(row):
    return {"job_id": row.id, "status": row.status}


def _enqueue(db, token, ws, key, kind, filename=None, payload=None, document_id=None, source_revision=None):
    manager(db, token, ws)
    _key(key)
    lock_workspace(db, ws)
    existing = db.scalar(select(IngestJob).where(IngestJob.workspace_id == ws,
                          IngestJob.created_by == token.user_id, IngestJob.idempotency_key == key))
    if existing:
        if not _same(existing, kind, filename, payload, document_id, source_revision):
            raise HTTPException(409, "Idempotency-Key was already used for different intake content")
        return _accepted(existing)
    if kind == "document.revise":
        document = get_resource(db, Document, document_id, ws)
        if source_revision != document.latest_revision:
            raise HTTPException(409, "Document revision changed")
    elif kind == "openapi.parse":
        get_version(db, ws, document_id, source_revision)
    queued = db.scalar(select(func.count(IngestJob.id)).where(IngestJob.workspace_id == ws,
                         IngestJob.status == "PENDING")) or 0
    if queued >= settings().ingest_max_queued_per_workspace:
        raise HTTPException(429, "Workspace intake queue is full", headers={"Retry-After": "1"})
    row = IngestJob(workspace_id=ws, created_by=token.user_id, token_id=token.id, idempotency_key=key,
                    kind=kind, filename=filename, payload=payload, document_id=document_id,
                    source_revision=source_revision)
    db.add(row); db.flush()
    audit(db, token.user_id, ws, "ingest.enqueue", row.id, {"kind": kind})
    result = _accepted(row)
    db.commit()
    return result


async def _payload(file):
    data = await file.read(settings().max_upload_bytes + 1)
    if len(data) > settings().max_upload_bytes:
        raise HTTPException(413, "Document exceeds upload size limit")
    return Path(file.filename or "upload.txt").name, data


@router.post(PREFIX + "/documents", status_code=202, response_model=IngestAccepted)
async def create(ws: str, file: UploadFile = File(...), idempotency_key: str = Header(alias="Idempotency-Key"),
                 token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    filename, data = await _payload(file)
    return _enqueue(db, token, ws, idempotency_key, "document.create", filename, data)


@router.post(PREFIX + "/documents/{document_id}/versions", status_code=202, response_model=IngestAccepted)
async def revise(ws: str, document_id: str, file: UploadFile = File(...), expected_revision: int = Form(ge=1),
                 idempotency_key: str = Header(alias="Idempotency-Key"), token=Depends(authenticate),
                 db: Session = Depends(session)):
    manager(db, token, ws)
    filename, data = await _payload(file)
    return _enqueue(db, token, ws, idempotency_key, "document.revise", filename, data,
                    document_id, expected_revision)


@router.post(PREFIX + "/openapi-imports", status_code=202, response_model=IngestAccepted)
def parse(ws: str, body: ImportRequest, idempotency_key: str = Header(alias="Idempotency-Key"),
          token=Depends(authenticate), db: Session = Depends(session)):
    return _enqueue(db, token, ws, idempotency_key, "openapi.parse", document_id=body.document_id,
                    source_revision=body.revision)


@router.get(PREFIX + "/{job_id}", response_model=IngestJobOut)
def status(ws: str, job_id: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    row = get_resource(db, IngestJob, job_id, ws)
    return {"job_id": row.id, "kind": row.kind, "status": row.status, "created_at": row.created_at,
            "updated_at": row.updated_at, "result": row.result, "error": row.error}
