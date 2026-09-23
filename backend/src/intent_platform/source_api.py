import asyncio
import copy
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import session
from .auth import authenticate, manager, audit
from .config import settings
from .models import Document, DocumentVersion, OpenAPIImport, Function
from .documents import extract_document
from .document_versions import get_version, sections, view
from .source_schemas import DocumentOut, DocumentSummary, ImportRequest, ImportOut, OperationSelection, SourcesBody, ReviewBody, SourceReviewOut
from .api_models import FunctionOut
from .services import get_resource, function_view
from .source_review import review, validate_citation, invalidate
from .openapi_import import parse_openapi, PARSER_VERSION

router = APIRouter()
PREFIX = "/api/v2/management/workspaces/{ws}"


@router.get(PREFIX + "/documents", response_model=list[DocumentSummary])
def documents(ws: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    return [{"id": d.id, "filename": d.filename, "latest_revision": d.latest_revision} for d in db.scalars(select(Document).where(Document.workspace_id == ws).order_by(Document.id))]


@router.get(PREFIX + "/documents/{document_id}/versions", response_model=list[DocumentOut])
def versions(ws: str, document_id: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws); get_resource(db, Document, document_id, ws)
    return [view(v) for v in db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == document_id).order_by(DocumentVersion.revision.desc()))]


@router.get(PREFIX + "/documents/{document_id}/versions/{revision}", response_model=DocumentOut)
def document_version(ws: str, document_id: str, revision: int, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws); return view(get_version(db, ws, document_id, revision))


@router.get(PREFIX + "/documents/{document_id}/versions/{revision}/original")
def original(ws: str, document_id: str, revision: int, token=Depends(authenticate), db: Session = Depends(session)):
    from .application_api import binary_response
    manager(db, token, ws); version = get_version(db, ws, document_id, revision)
    if version.original is None: raise HTTPException(404, "Original bytes were not retained for this legacy source")
    return binary_response(version.original, "application/octet-stream", version.filename)


@router.post(PREFIX + "/documents/{document_id}/versions", status_code=201, response_model=DocumentOut)
async def revise_document(ws: str, document_id: str, file: UploadFile = File(...), expected_revision: int = Form(...), token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    data = await file.read(settings().max_upload_bytes + 1)
    if len(data) > settings().max_upload_bytes: raise HTTPException(413, "Document exceeds upload size limit")
    filename = Path(file.filename or "upload.txt").name
    try:
        content = await asyncio.to_thread(extract_document, filename, data)
        document = get_resource(db, Document, document_id, ws, lock=True)
        if expected_revision != document.latest_revision: raise HTTPException(409, "Document revision changed")
        previous = get_version(db, ws, document_id, document.latest_revision)
        parts = sections(content, previous.sections)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "Document cannot be parsed within the supported format and size boundary")
    document.latest_revision += 1
    version = DocumentVersion(workspace_id=ws, document_id=document_id, revision=document.latest_revision, filename=filename, text=content, original=data, sections=parts, created_by=token.user_id)
    db.add(version); db.flush(); audit(db, token.user_id, ws, "document.revise", document_id, {"revision": version.revision})
    result = view(version); db.commit(); return result


def import_view(item):
    return {"id": item.id, "document_id": item.document_id, "document_revision": item.document_revision, "parser_version": item.parser_version, **item.report}


@router.post(PREFIX + "/openapi-imports", status_code=201, response_model=ImportOut)
def import_api(ws: str, body: ImportRequest, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws); version = get_version(db, ws, body.document_id, body.revision)
    item = OpenAPIImport(workspace_id=ws, document_id=version.document_id, document_revision=version.revision,
                         parser_version=PARSER_VERSION, report=parse_openapi(version.text), created_by=token.user_id)
    db.add(item); db.flush(); audit(db, token.user_id, ws, "openapi.parse", item.id, {"valid": item.report["valid"]})
    result = import_view(item); db.commit(); return result


@router.get(PREFIX + "/openapi-imports/{import_id}", response_model=ImportOut)
def get_import(ws: str, import_id: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws); return import_view(get_resource(db, OpenAPIImport, import_id, ws))


@router.post(PREFIX + "/openapi-imports/{import_id}/functions", status_code=201, response_model=FunctionOut)
def select_operation(ws: str, import_id: str, body: OperationSelection, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws); item = get_resource(db, OpenAPIImport, import_id, ws)
    operation = next((o for o in item.report["operations"] if o["key"] == body.operation_key and o["supported"]), None)
    if not item.report["valid"] or not operation: raise HTTPException(422, "Select a supported operation from this import")
    definition = copy.deepcopy(operation["definition"])
    if body.name: definition["name"] = body.name
    if body.description: definition["description"] = body.description
    reference = {"document_id": item.document_id, "revision": item.document_revision}
    definition["document_refs"] = [reference]
    function = Function(workspace_id=ws, definition=definition, source_state={"required": True, "api_source": {**reference, "import_id": item.id, "operation_key": operation["key"], "pointer": operation["source_pointer"], "parser_version": item.parser_version}, "api_facts": operation["facts"], "citations": [], "confirmations": {}, "require_failure_probe": True})
    db.add(function); db.flush(); audit(db, token.user_id, ws, "openapi.select", function.id, {"import_id": item.id, "operation_key": operation["key"]})
    result = function_view(function); db.commit(); return result


@router.put(PREFIX + "/functions/{fid}/sources", response_model=SourceReviewOut)
def set_sources(ws: str, fid: str, body: SourcesBody, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws); function = get_resource(db, Function, fid, ws, lock=True)
    if function.revision != body.revision: raise HTTPException(409, "Function revision changed")
    citations = [c.model_dump() for c in body.citations]
    try:
        for citation in citations: validate_citation(db, ws, citation)
    except ValueError as exc: raise HTTPException(422, str(exc))
    function.source_state = {**function.source_state, "required": True, "citations": [{**c, "claimed_by": token.user_id} for c in citations], "confirmations": {}}
    function.revision += 1; invalidate(function)
    audit(db, token.user_id, ws, "function.sources", fid, {"revision": function.revision, "citation_count": len(citations)})
    result = review(function); db.commit(); return result


@router.get(PREFIX + "/functions/{fid}/source-review", response_model=SourceReviewOut)
def source_review(ws: str, fid: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws); return review(get_resource(db, Function, fid, ws))


@router.post(PREFIX + "/functions/{fid}/source-review/confirm", response_model=SourceReviewOut)
def confirm_sources(ws: str, fid: str, body: ReviewBody, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws); function = get_resource(db, Function, fid, ws, lock=True)
    if function.revision != body.revision: raise HTTPException(409, "Function revision changed")
    issues = {i["field_path"]: i for i in review(function)["issues"]}
    confirmations = copy.deepcopy(function.source_state.get("confirmations", {})); seen = set()
    for resolution in body.resolutions:
        if resolution.field_path not in issues or resolution.field_path in seen or not resolution.reason.strip(): raise HTTPException(422, "Confirm distinct current source-review fields with reasons")
        seen.add(resolution.field_path); issue = issues[resolution.field_path]
        confirmations[resolution.field_path] = {"revision": function.revision, "value": issue["current_value"], "candidates": issue["candidates"], "reason": resolution.reason.strip(), "confirmed_by": token.user_id}
    function.source_state = {**function.source_state, "confirmations": confirmations}
    function.validation = None; function.validated_revision = None; function.status = "draft"
    audit(db, token.user_id, ws, "function.sources.confirm", fid, {"revision": function.revision, "fields": sorted(seen)})
    result = review(function); db.commit(); return result
