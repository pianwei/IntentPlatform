import asyncio
import copy
import json
import secrets
from datetime import timedelta
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Body, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse, Response, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .config import settings
from .db import engine, session, set_identity, now, uid
from .models import (AuthToken, Workspace, Membership, Function, FunctionVersion, Agent, Release, Conversation, Run, Call, Event,
                     Handshake, Ticket, Audit, Document, Evaluation, RuntimeSession)
from .schemas import ImportBody, DraftBody, ValidateBody, AgentBody, MessageBody, AnswerBody, ConfirmationBody, EmbedBody
from .auth import authenticate, manager, authorize, bootstrap_identity, issue_token, workspaces, token_valid, audit
from .services import (function_view, agent_view, release_view, get_resource, validate_function, publish_function, publish_agent,
                       runtime_agent, visible_candidates, owned_conversation, owned_run, emit, status_event, event_view, run_view, normalized_ui, conversation_view,
                       authorize_fixture_result_access)
from .policies import permitted, dependency_error, effective_channels
from .runtime_sessions import scoped_session, application, check_session_binding, token_view
from .documents import extract_document
from .ports import request_json, route_model, execute_connector
from .resolvers import bounded_resolution
from .source_schemas import DocumentOut
from .api_models import (LoginBody, IdentityBody, PublishBody, RollbackBody, RevisionBody, OriginBody, EvaluationBody,
                         TokenOut, MeOut, WorkspaceOut, FunctionOut, ValidationOut, AgentOut, ReleaseOut, RuntimeAgentOut,
                         ConversationOut, MessageOut, RunOut, EventOut, HandshakeOut, TicketOut, SeedOut, RuntimeAgentSummaryOut)

app = FastAPI(title="意图中台本地工程", version="0.1.0", description="Development fixtures are not bank acceptance evidence", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in settings().cors_origins.split(",")],
                   allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "PUT"], allow_headers=["Authorization", "Content-Type"])


@app.middleware("http")
async def local_request_policy(request: Request, call_next):
    path = request.url.path
    protected = path.startswith(("/api/v2/auth/", "/api/v2/me", "/api/v2/management/"))
    if settings().identity_mode == "local" and protected and request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin not in {item.strip() for item in settings().cors_origins.split(",")}:
            return JSONResponse({"detail": "Untrusted request origin"}, status_code=403)
    response = await call_next(request)
    if path.startswith("/api/v2/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request: Request, exc: RequestValidationError):
    # Never echo a submitted password, recovery secret or ticket in error bodies.
    sensitive = {"password", "current_password", "new_password", "setup_token", "recovery_secret", "ticket", "identity_token"}
    errors = exc.errors()
    if any(any(part in sensitive for part in error.get("loc", ())) for error in errors):
        safe = [{key: error[key] for key in ("type", "loc", "msg") if key in error} for error in errors]
        return JSONResponse({"detail": safe}, status_code=422)
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(IntegrityError)
async def unique_agent_name(request: Request, exc: IntegrityError):
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if constraint in {"uq_agent_workspace_name", "uq_agent_workspace_normalized_name"}:
        return JSONResponse({"detail": "Agent name is already used in this workspace"}, status_code=409)
    raise exc


@app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
def api_documentation():
    from .api_docs import OPENAPI_PAGE
    return HTMLResponse(OPENAPI_PAGE)


@app.get("/docs/app.js", include_in_schema=False)
def api_documentation_script():
    from .api_docs import OPENAPI_SCRIPT
    return Response(OPENAPI_SCRIPT, media_type="application/javascript", headers={"X-Content-Type-Options": "nosniff"})


@app.get("/health")
def health(db: Session = Depends(session)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "postgresql", "environment": settings().env, "fixture_mode": settings().fixture_mode}


@app.post("/api/v2/dev/login", response_model=TokenOut)
def dev_login(body: LoginBody = Body(default=LoginBody()), db: Session = Depends(session)):
    token, ws, roles = bootstrap_identity(db, body.user_id)
    db.commit()
    return {"access_token": token.id, "token_type": "bearer", "user_id": token.user_id, "workspace_id": ws, "roles": roles, "fixture_mode": True}


@app.post("/api/v2/auth/exchange", response_model=TokenOut)
def auth_exchange(body: IdentityBody, db: Session = Depends(session)):
    cfg = settings()
    if cfg.identity_mode != "legacy-test": raise HTTPException(404, "Historical identity exchange is disabled")
    if not cfg.identity_introspection_url:
        raise HTTPException(503, "Trusted identity introspection is not configured")
    try:
        identity = request_json("POST", cfg.identity_introspection_url, payload={"token": body.identity_token},
                                headers={"Authorization": "Bearer " + cfg.identity_service_key} if cfg.identity_service_key else {}, timeout=10)
    except Exception:
        raise HTTPException(401, "Identity validation failed")
    if not isinstance(identity, dict) or identity.get("active") is not True or not isinstance(identity.get("sub"), str) or not identity["sub"].strip() or identity["sub"] != identity["sub"].strip():
        raise HTTPException(401, "Inactive identity")
    user = identity["sub"]
    if not db.scalar(select(Membership).where(Membership.user_id == user, Membership.active.is_(True))):
        raise HTTPException(403, "No provisioned workspace membership")
    # Integration delegation comes only from trusted introspection, never browser input.
    from .trust import Delegation, integration_catalog, member_authority
    try:
        delegation = Delegation.model_validate(identity.get("delegation")).model_dump()
        eligible = [m for m in db.scalars(select(Membership).where(Membership.user_id == user)) if member_authority(m)[1] and m.workspace_id in delegation["workspace_ids"]]
        if user not in delegation["user_ids"] or not any(user in integration_catalog(m.workspace_id).get(delegation["integration_id"], {}).get("user_ids", []) for m in eligible):
            raise ValueError("No approved integration/user binding")
    except (ValueError, TypeError):
        raise HTTPException(403, "Trusted identity delegation is invalid or not approved")
    token = issue_token(db, user, source="introspection", delegation=delegation)
    set_identity(db, user)
    if body.public_id:
        token = scoped_session(db, token, application(db, body.public_id), "chat", body.runtime_session_id)
        result = token_view(token); db.commit(); return result
    db.commit()
    return {"access_token": token.id, "token_type": "bearer", "user_id": user, "fixture_mode": False}


@app.get("/api/v2/me", response_model=MeOut)
def me(token=Depends(authenticate), db: Session = Depends(session)):
    if settings().identity_mode == "local":
        from .local_identity import local_profile
        return local_profile(db, token)
    return {"user_id": token.user_id, "scope": token.scope, "workspaces": workspaces(db, token), "fixture_mode": settings().fixture_mode}


@app.get("/api/v2/management/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(token=Depends(authenticate), db: Session = Depends(session)):
    if token.scope != "management":
        raise HTTPException(403, "Runtime token cannot access management APIs")
    return workspaces(db, token)


@app.get("/api/v2/management/workspaces/{ws}/functions", response_model=list[FunctionOut])
def list_functions(ws: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    return [function_view(f) for f in db.scalars(select(Function).where(Function.workspace_id == ws).order_by(Function.id))]


@app.post("/api/v2/management/workspaces/{ws}/functions/import", status_code=201, response_model=FunctionOut)
def import_function(ws: str, body: ImportBody, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    if settings().env == "production" and body.definition.connector.kind == "fixture":
        raise HTTPException(422, "Fixture connector forbidden in production")
    from .function_authoring import normalize_draft
    definition = normalize_draft(db, ws, body.definition.model_dump())
    definition["output_examples"] = []
    f = Function(workspace_id=ws, definition=definition, document_text=body.document_text,
                 allowed_users=body.definition.allowed_users, access_mode="allowlist" if body.definition.allowed_users else "workspace")
    db.add(f); db.flush(); audit(db, token.user_id, ws, "function.import", f.id)
    response = function_view(f); db.commit(); return response


@app.patch("/api/v2/management/workspaces/{ws}/functions/{fid}/draft", response_model=FunctionOut)
def edit_function(ws: str, fid: str, body: DraftBody, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    f = get_resource(db, Function, fid, ws, lock=True)
    if f.revision != body.revision:
        raise HTTPException(409, "Draft revision changed")
    if settings().env == "production" and body.definition.connector.kind == "fixture":
        raise HTTPException(422, "Fixture connector forbidden in production")
    from .function_authoring import normalize_draft
    definition = normalize_draft(db, ws, body.definition.model_dump())
    # Approved cases are populated exclusively from server-side current tests.
    definition["output_examples"] = f.definition.get("output_examples", [])
    f.definition = definition; f.revision += 1; f.validated_revision = None; f.validation = None; f.status = "draft"
    from .source_review import invalidate
    invalidate(f)
    audit(db, token.user_id, ws, "function.edit", fid, {"revision": f.revision})
    result = function_view(f); db.commit(); return result


@app.post("/api/v2/management/workspaces/{ws}/functions/{fid}/validate", response_model=ValidationOut)
def check_function(ws: str, fid: str, body: ValidateBody = Body(default=ValidateBody()), token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    f = get_resource(db, Function, fid, ws, lock=True)
    if body.revision is not None and body.revision != f.revision:
        raise HTTPException(409, "Function revision changed")
    result = validate_function(db, f, token, body); db.commit(); return result


@app.post("/api/v2/management/workspaces/{ws}/functions/{fid}/publish", response_model=FunctionOut)
def function_publish(ws: str, fid: str, body: PublishBody = Body(default=PublishBody()), token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    f = get_resource(db, Function, fid, ws, lock=True)
    if body.revision is not None and body.revision != f.revision:
        raise HTTPException(409, "Function revision changed")
    result = publish_function(db, f, token); db.commit(); return result


@app.get("/api/v2/management/workspaces/{ws}/functions/{fid}/versions")
def function_versions(ws: str, fid: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    function = get_resource(db, Function, fid, ws)
    return [{"id": version.id, "function_id": version.function_id, "version": version.version,
             "definition": version.definition, "created_at": version.created_at,
             "current": version.version == function.latest_version}
            for version in db.scalars(select(FunctionVersion).where(
                FunctionVersion.workspace_id == ws, FunctionVersion.function_id == fid,
                FunctionVersion.version > 0
            ).order_by(FunctionVersion.version.desc()))]


@app.post("/api/v2/management/workspaces/{ws}/functions/{fid}/disable", response_model=FunctionOut)
def function_disable(ws: str, fid: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    f = get_resource(db, Function, fid, ws, lock=True); f.disabled = True
    audit(db, token.user_id, ws, "function.disable", fid)
    result = function_view(f); db.commit(); return result


@app.post("/api/v2/management/workspaces/{ws}/documents", status_code=201, response_model=DocumentOut)
async def upload_document(ws: str, file: UploadFile = File(...), token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    data = await file.read(settings().max_upload_bytes + 1)
    if len(data) > settings().max_upload_bytes:
        raise HTTPException(413, "Document exceeds upload size limit")
    filename = Path(file.filename or "upload.txt").name
    try:
        content = await asyncio.to_thread(extract_document, filename, data)
    except Exception as exc:
        raise HTTPException(422, str(exc))
    from .document_versions import create_document, view
    try:
        d, version = create_document(db, ws, filename, content, data, token.user_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    audit(db, token.user_id, ws, "document.parse", d.id)
    result = view(version)
    db.commit(); return result


@app.get("/api/v2/management/workspaces/{ws}/agents", response_model=list[AgentOut])
def list_agents(ws: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "publisher")
    return [agent_view(a, db) for a in db.scalars(select(Agent).where(Agent.workspace_id == ws)) if not (a.runtime_ui or {}).get("authoring_test")]


@app.post("/api/v2/management/workspaces/{ws}/agents", status_code=201, response_model=AgentOut)
def create_agent(ws: str, body: AgentBody, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "publisher")
    if db.execute(text("SELECT id FROM agents WHERE workspace_id=:ws AND lower(btrim(name))=lower(:name)"), {"ws": ws, "name": body.name}).first():
        raise HTTPException(409, "Agent name already exists in this workspace")
    for fid in body.capability_ids:
        get_resource(db, Function, fid, ws)
    values = body.model_dump(exclude={"revision"}, exclude_none=True)
    if body.integration_refs is not None:
        from .trust import integration_catalog
        if set(body.integration_refs) - set(integration_catalog(ws)):
            raise HTTPException(422, "Select only server-approved integration references")
    users = values.pop("allowed_users", [])
    a = Agent(workspace_id=ws, **values, allowed_users=users, access_mode="allowlist" if users else "workspace")
    db.add(a); db.flush(); result = agent_view(a, db); db.commit(); return result


@app.patch("/api/v2/management/workspaces/{ws}/agents/{aid}/draft", response_model=AgentOut)
def edit_agent(ws: str, aid: str, body: AgentBody, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "publisher")
    a = get_resource(db, Agent, aid, ws, lock=True)
    if db.execute(text("SELECT id FROM agents WHERE workspace_id=:ws AND id<>:id AND lower(btrim(name))=lower(:name)"), {"ws": ws, "id": aid, "name": body.name}).first():
        raise HTTPException(409, "Agent name already exists in this workspace")
    if body.revision != a.revision:
        raise HTTPException(409, "Current agent draft revision required")
    for fid in body.capability_ids:
        get_resource(db, Function, fid, ws)
    if "allowed_users" in body.model_fields_set:
        raise HTTPException(422, "Use the explicit access policy endpoint; drafts cannot change authorization")
    if body.integration_refs is not None:
        from .trust import integration_catalog
        if set(body.integration_refs) - set(integration_catalog(ws)):
            raise HTTPException(422, "Select only server-approved integration references")
    for key, value in body.model_dump(exclude={"revision", "allowed_users"}, exclude_unset=True).items():
        setattr(a, key, value)
    a.revision += 1
    result = agent_view(a, db); db.commit(); return result


@app.post("/api/v2/management/workspaces/{ws}/agents/{aid}/publish", response_model=ReleaseOut)
def agent_publish(ws: str, aid: str, body: PublishBody = Body(default=PublishBody()), token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "publisher")
    a = get_resource(db, Agent, aid, ws, lock=True)
    result = publish_agent(db, a, token, body.revision); db.commit(); return result


@app.get("/api/v2/management/workspaces/{ws}/agents/{aid}/releases", response_model=list[ReleaseOut])
def agent_releases(ws: str, aid: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "publisher"); get_resource(db, Agent, aid, ws)
    return [release_view(r) for r in db.scalars(select(Release).where(Release.agent_id == aid).order_by(Release.version.desc()))]


@app.post("/api/v2/management/workspaces/{ws}/agents/{aid}/rollback", response_model=AgentOut)
def rollback(ws: str, aid: str, body: RollbackBody, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "publisher")
    a = get_resource(db, Agent, aid, ws, lock=True)
    r = get_resource(db, Release, body.release_id, ws)
    if r.agent_id != aid:
        raise HTTPException(409, "Release belongs to a different agent")
    if error := dependency_error(r.snapshot, db):
        raise HTTPException(409, error)
    a.current_release_id = r.id
    audit(db, token.user_id, ws, "agent.rollback", aid, {"release_id": r.id})
    result = agent_view(a, db); db.commit(); return result


@app.get("/api/v2/management/workspaces/{ws}/audit")
def audits(ws: str, resource_id: str | None = None, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "admin", archived_history=True)
    from .workspace_admin import identity_directory
    names = identity_directory()
    return [{"id": a.id, "user_id": a.user_id, "actor_name": names.get(a.user_id), "action": a.action, "resource_id": a.resource_id, "detail": a.detail, "created_at": a.created_at}
            for a in db.scalars(select(Audit).where(Audit.workspace_id == ws,
                *([Audit.resource_id == resource_id] if resource_id else [])).order_by(Audit.created_at.desc()).limit(200))]


@app.post("/api/v2/management/workspaces/{ws}/evaluations")
def evaluate(ws: str, body: EvaluationBody, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "publisher")
    a = get_resource(db, Agent, body.agent_id, ws)
    if not a.current_release_id:
        raise HTTPException(409, "Publish agent first")
    cases = body.cases
    if not cases or len(cases) > 1000:
        raise HTTPException(422, "Provide 1 to 1000 diagnostic cases")
    release = db.get(Release, a.current_release_id)
    if release.snapshot.get("prompt_policy_version") == "three-layer-v1":
        raise HTTPException(409, "Three-layer agents require actual runtime evaluation with management traces")
    if error := dependency_error(release.snapshot, db):
        raise HTTPException(409, error)
    candidates = visible_candidates(db, release, token)
    results = []
    for case in cases:
        try:
            proposal = route_model(case.text, candidates, policy_version=release.snapshot["prompt_policy_version"])
            actual = [x["capability_id"] for x in proposal.get("requests", [])]
            results.append({"correct": actual == [case.expected_capability_id], "actual": actual})
        except Exception:
            results.append({"correct": False, "error": "Routing diagnostic failed"})
    correct = sum(x["correct"] for x in results)
    data = {"label": body.label, "total": len(results), "correct": correct, "accuracy": correct / len(results),
            "results": results, "fixture_mode": settings().fixture_mode, "release_id": release.id, "acceptance_evidence": False}
    e = Evaluation(workspace_id=ws, result=data); db.add(e); db.flush(); result = {"id": e.id, **data}; db.commit(); return result


@app.get("/api/v2/runtime/agents", response_model=list[RuntimeAgentSummaryOut])
def runtime_agents(token=Depends(authenticate), db: Session = Depends(session)):
    q = select(Agent).where(Agent.current_release_id.is_not(None))
    if token.scope == "runtime":
        q = q.where(Agent.id == token.agent_id)
    result = []
    for a in db.scalars(q):
        if (a.runtime_ui or {}).get("authoring_test"):
            continue
        if not permitted(a, token.user_id):
            continue
        release = db.get(Release, a.current_release_id)
        if not any(effective_channels(a, release).values()):
            continue
        from .trust import integration_allowed
        bound = db.get(RuntimeSession, token.runtime_session_id) if token.runtime_session_id else None
        channel = bound.channel if bound else "chat" if effective_channels(a, release)["chat"] else "embed"
        if not integration_allowed(a, release, token, channel, bound.host_origin if bound else None, bound.integration_id if bound else None):
            continue
        ui = normalized_ui(release.snapshot.get("runtime_ui"), a.name, a.suggested_questions)
        item = {"id": a.id, "public_id": a.public_id, "workspace_id": a.workspace_id, "name": ui["title"],
                "current_release_id": release.id, "suggested_questions": ui["suggested_questions"], "fixture_mode": release.snapshot["fixture_mode"]}
        item["capability_ids"] = [c["capability_id"] for c in visible_candidates(db, release, token)]
        result.append(item)
    return result


@app.get("/api/v2/runtime/agents/{aid}", response_model=RuntimeAgentOut)
def runtime_agent_info(aid: str, token=Depends(authenticate), db: Session = Depends(session)):
    a = runtime_agent(db, aid, token); r = db.get(Release, a.current_release_id)
    from .trust import integration_allowed
    session_row = db.get(RuntimeSession, token.runtime_session_id) if token.runtime_session_id else None
    if not integration_allowed(a, r, token, session_row.channel if session_row else "chat", session_row.host_origin if session_row else None, session_row.integration_id if session_row else None):
        raise HTTPException(403, "Current release identity integration differs")
    ui = normalized_ui(r.snapshot.get("runtime_ui"), a.name, a.suggested_questions)
    return {"id": a.id, "name": ui["title"], "workspace_id": a.workspace_id, "release_id": r.id,
            "capabilities": [{"id": c["capability_id"], "name": c["definition"]["name"], "description": c["definition"]["description"]} for c in visible_candidates(db, r, token)],
            "suggested_questions": ui["suggested_questions"], "fixture_mode": r.snapshot["fixture_mode"]}


@app.post("/api/v2/runtime/agents/{aid}/conversations", status_code=201, response_model=ConversationOut)
def new_conversation(aid: str, token=Depends(authenticate), db: Session = Depends(session)):
    if settings().identity_mode == "local" and token.scope != "runtime": raise HTTPException(403, "Use the application runtime session")
    a = runtime_agent(db, aid, token)
    from .workspace_lifecycle import require_workspace
    require_workspace(db, a.workspace_id, lock=True)
    if not token_valid(db, token): raise HTTPException(401, "Runtime identity changed")
    r = db.get(Release, a.current_release_id)
    if token.scope == "management" and not effective_channels(a, r)["chat"]:
        raise HTTPException(403, "Chat channel disabled")
    from .trust import integration_allowed
    bound = db.get(RuntimeSession, token.runtime_session_id) if token.runtime_session_id else None
    if not integration_allowed(a, r, token, bound.channel if bound else "chat", bound.host_origin if bound else None, bound.integration_id if bound else None):
        raise HTTPException(403, "Published identity integration unavailable")
    if error := dependency_error(r.snapshot, db):
        raise HTTPException(503, error)
    c = Conversation(workspace_id=a.workspace_id, agent_id=aid, release_id=a.current_release_id, user_id=token.user_id, token_id=token.id, runtime_session_id=token.runtime_session_id)
    db.add(c); db.flush(); result = conversation_view(db, c)
    db.commit(); return result


@app.post("/api/v2/runtime/conversations/{cid}/messages", status_code=202, response_model=MessageOut)
def send_message(cid: str, body: MessageBody, token=Depends(authenticate), db: Session = Depends(session)):
    conv = owned_conversation(db, cid, token)
    # Serialize deduplication without locking conversation ahead of run/event rows.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:cid))"), {"cid": cid})
    existing = db.scalar(select(Run).where(Run.conversation_id == cid, Run.client_message_id == body.client_message_id))
    if existing:
        if existing.text != body.text:
            raise HTTPException(409, "Client message ID already belongs to different text")
        return {"run_id": existing.id, "conversation_id": cid, "revision": existing.revision, "status": existing.status, "duplicate": True}
    from .capacity import admit
    admit(db, conv.workspace_id, excluding_conversation=cid)
    # An explicit new message invalidates earlier pending questions/confirmations.
    pending = db.scalars(select(Run).where(Run.conversation_id == cid, Run.status.in_(["RECEIVED", "ROUTING", "WAITING_INPUT", "WAITING_CONFIRMATION", "EXECUTING"])).with_for_update()).all()
    for old in pending:
        calls = db.scalars(select(Call).where(Call.run_id == old.id).with_for_update()).all()
        for call in calls:
            if call.status in {"PENDING", "EXECUTING"} and call.dispatched_at is None:
                call.status = "CANCELLED"
        sent = any(c.status == "EXECUTING" and c.dispatched_at is not None for c in calls)
        if not sent:
            old.status = "CANCELLED"; old.revision += 1
        old.question = None; old.confirmed = False
        status_event(db, old, "Unsent actions cancelled by new request; already dispatched calls retain original attribution")
    if conv.title == "新会话":
        conv.title = body.text[:100]
    conv.updated_at = now()
    run = Run(workspace_id=conv.workspace_id, conversation_id=cid, user_id=token.user_id, text=body.text, client_message_id=body.client_message_id)
    db.add(run); db.flush(); status_event(db, run)
    result = {"run_id": run.id, "conversation_id": cid, "revision": run.revision, "status": run.status, "duplicate": False}
    db.commit(); return result


@app.get("/api/v2/runtime/runs/{rid}", response_model=RunOut)
def get_run(rid: str, token=Depends(authenticate), db: Session = Depends(session)):
    return run_view(db, owned_run(db, rid, token))


@app.post("/api/v2/runtime/runs/{rid}/answers", response_model=RunOut)
def answer(rid: str, body: AnswerBody, token=Depends(authenticate), db: Session = Depends(session)):
    from .capacity import admit
    initial = owned_run(db, rid, token)
    admit(db, initial.workspace_id)
    run = owned_run(db, rid, token, lock=True)
    if run.status != "WAITING_INPUT" or run.revision != body.revision or not run.question or run.question["id"] != body.question_id:
        raise HTTPException(409, "Question is stale")
    if run.question.get("kind") == "planner_clarification":
        detail = body.values.get("detail")
        if set(body.values) != {"detail"} or not isinstance(detail, str) or not detail.strip() or len(detail) > 2000:
            raise HTTPException(422, "Provide a short clarification")
        run.text += "\n补充说明：" + detail.strip()
        run.proposal = {**(run.proposal or {}), "planner_clarification_count": 1}
    elif run.question.get("kind") in {"planner_arguments", "planner_choice"}:
        from .models import ExecutionPlan, PlanStep
        from .planning import trace
        plan = db.scalar(select(ExecutionPlan).where(ExecutionPlan.run_id == run.id).with_for_update())
        if not plan or plan.workspace_id != run.workspace_id:
            raise HTTPException(409, "Planning state is unavailable")
        if run.question["kind"] == "planner_arguments":
            allowed = {field["name"] for field in run.question["fields"]}
            if set(body.values) != allowed:
                raise HTTPException(422, "Provide all requested planning inputs")
            from .ontology import input_errors
            schema = {"type": "object", "properties": {field["name"]: field.get("schema") or {"type": "string", "minLength": 1, "maxLength": 500} for field in run.question["fields"]},
                      "required": list(allowed), "additionalProperties": False}
            if input_errors(schema, body.values):
                raise HTTPException(422, "Planning inputs do not match the declared field types")
            from .planning_schemas import ExecutionPlanSpec
            try:
                ExecutionPlanSpec.model_validate({**{k: plan.snapshot[k] for k in ("steps", "result_steps", "final_output_mapping") if k in plan.snapshot}, "inputs": {**plan.inputs, **body.values}})
            except ValueError:
                raise HTTPException(422, "Planning inputs exceed the finite input budget")
            plan.inputs = {**plan.inputs, **body.values}
            trace(db, run, "entity", "input_supplied", {"fields": sorted(body.values)})
        else:
            allowed = {option["id"] for option in run.question["options"]}
            if body.option_id not in allowed:
                raise HTTPException(422, "Select a current candidate")
            step = db.scalar(select(PlanStep).where(PlanStep.plan_id == plan.id, PlanStep.step_key == run.question["step_key"]).with_for_update())
            collection_field = run.question.get("collection_field", "customers")
            id_field = run.question.get("id_field", "customer_id")
            rows = (step.result or {}).get("data", {}).get(collection_field, []) if step else []
            selected = [row for row in rows if row.get(id_field) == body.option_id]
            if len(selected) != 1:
                raise HTTPException(409, "Candidate is unavailable")
            step.result = {"data": {**step.result["data"], collection_field: selected}}
            plan.inputs = {**plan.inputs, "__chosen:" + step.step_key: body.option_id}
            trace(db, run, "entity", "candidate_selected", {"step": step.step_key,
                                                               "bound_object_type": next((b["entity_type"] for b in step.definition.get("result_bindings", []) if b["collection_path"] == collection_field), "entity") if plan.snapshot.get("three_layer") else "customer", "binding_source": "user_choice"})
    elif run.question.get("kind") in {"entity_mention", "entity_choice", "history_choice"}:
        from .context_binding import apply_binding_answer
        try:
            apply_binding_answer(run, body)
        except (ValueError, KeyError):
            raise HTTPException(422, "Select or describe the current authorized participant")
    elif run.decision == "AMBIGUOUS":
        if body.option_id not in [o["id"] for o in run.question["options"]]:
            raise HTTPException(422, "Select a current authorized option")
        release = db.get(Release, db.get(Conversation, run.conversation_id).release_id)
        request = {"capability_id": body.option_id, "arguments": body.values}
        if release.snapshot["prompt_policy_version"] == "router-v2":
            capability = next((c for c in visible_candidates(db, release, token) if c["capability_id"] == body.option_id), None)
            if not capability:
                raise HTTPException(403, "Selected capability is no longer authorized")
            d = capability["definition"]
            request.update({"action": d["action"], "object_type": d["object_type"], "result_type": d["result_type"],
                            "participants": [{"role": r["role"], "type": r["type"]} for r in d.get("participant_roles", []) for _ in range(r.get("min_count", 1))],
                            "relations": d.get("relations", []), "parameter_units": {}, "negated": False})
        run.proposal = {"decision": "READY", "requests": [request]}
    else:
        allowed = {f["name"] for f in run.question["fields"]}
        if set(body.values) - allowed:
            raise HTTPException(422, "Unexpected answer fields")
        proposal = copy.deepcopy(run.proposal)
        proposal["requests"][run.question["request_index"]]["arguments"].update(body.values)
        units = {f["name"]: f["unit"] for f in run.question["fields"] if f.get("unit") and f["name"] in body.values}
        if units:
            proposal["requests"][run.question["request_index"]].setdefault("parameter_units", {}).update(units)
        run.proposal = proposal
    from .conversation_logs import record_interaction
    record_interaction(db, run, "answer", body.model_dump())
    run.revision += 1; run.status = "RECEIVED"; run.queued_at = now(); run.confirmed = False; run.question = None; run.validation_evidence = None
    status_event(db, run); result = run_view(db, run); db.commit(); return result


@app.post("/api/v2/runtime/runs/{rid}/confirmations", response_model=RunOut)
def confirm(rid: str, body: ConfirmationBody, token=Depends(authenticate), db: Session = Depends(session)):
    from .capacity import admit
    initial = owned_run(db, rid, token)
    if body.confirmed: admit(db, initial.workspace_id)
    run = owned_run(db, rid, token, lock=True)
    if run.status != "WAITING_CONFIRMATION" or run.revision != body.revision or not run.confirmation_expires_at or run.confirmation_expires_at <= now():
        raise HTTPException(409, "Confirmation is stale or expired")
    from .conversation_logs import record_interaction
    record_interaction(db, run, "confirmation", body.model_dump())
    run.confirmed = body.confirmed; run.status = "RECEIVED" if body.confirmed else "CANCELLED"
    if body.confirmed: run.queued_at = now()
    audit(db, token.user_id, run.workspace_id, "run.confirm" if body.confirmed else "run.reject", rid, {"revision": run.revision})
    status_event(db, run); result = run_view(db, run); db.commit(); return result


@app.post("/api/v2/runtime/runs/{rid}/cancel", response_model=RunOut)
def cancel(rid: str, body: RevisionBody, token=Depends(authenticate), db: Session = Depends(session)):
    run = owned_run(db, rid, token, lock=True)
    if run.revision != body.revision:
        raise HTTPException(409, "Revision changed")
    if run.status in {"SUCCEEDED", "FAILED", "UNKNOWN", "CANCELLED"}:
        return run_view(db, run)
    calls = db.scalars(select(Call).where(Call.run_id == rid).with_for_update()).all()
    if any(c.status == "EXECUTING" and c.dispatched_at is not None for c in calls):
        raise HTTPException(409, "Call already dispatched; cancellation requires downstream confirmation")
    for c in calls:
        if c.status in {"PENDING", "EXECUTING"} and c.dispatched_at is None: c.status = "CANCELLED"
    run.status = "CANCELLED"; run.revision += 1; run.question = None; run.confirmed = False
    status_event(db, run); result = run_view(db, run); db.commit(); return result


@bounded_resolution()
def read_events(db, cid, token, after):
    owned_conversation(db, cid, token)
    batch = list(db.scalars(select(Event).where(Event.conversation_id == cid, Event.sequence > after).order_by(Event.sequence).limit(1000)))
    from .services import allowed_definition
    from .models import ExecutionPlan, PlanStep
    run_ids = {event.run_id for event in batch}
    if run_ids:
        from .services import authorize_run_capabilities
        for run_id in run_ids:
            authorize_run_capabilities(db, db.get(Run, run_id))
        # Every event of a plan, including its final text answer, inherits the
        # current permission of every capability whose result contributed to it.
        used = set(db.scalars(select(Call.capability_id).where(Call.run_id.in_(run_ids))))
        used.update(db.scalars(select(PlanStep.capability_id).join(ExecutionPlan, ExecutionPlan.id == PlanStep.plan_id)
                               .where(ExecutionPlan.run_id.in_(run_ids))))
        used.update(p.snapshot["composite_function_id"] for p in db.scalars(select(ExecutionPlan).where(ExecutionPlan.run_id.in_(run_ids)))
                    if p.snapshot.get("composite_function_id"))
        for capability_id in used:
            function = db.get(Function, capability_id)
            if not function or function.disabled or not permitted(function, token.user_id):
                raise HTTPException(403, "Result access was revoked")
        for call in db.scalars(select(Call).where(Call.run_id.in_(run_ids))):
            authorize_fixture_result_access(db, call, token.user_id)
    for event in batch:
        fid = event.payload.get("provenance", {}).get("capability_id") or event.payload.get("capability_id")
        ids = ([fid] if fid else []) + [a["capability_id"] for a in event.payload.get("actions", [])]
        if event.event_type == "QUESTION" and event.payload.get("kind") in {None, "function_choice"}:
            ids += [o["id"] for o in event.payload.get("options", [])]
        elif event.event_type == "QUESTION" and event.payload.get("kind") in {"entity_mention", "entity_choice", "history_choice"}:
            from .context_binding import authorize_question
            try:
                authorize_question(db, db.get(Run, event.run_id), event.payload["question_id"])
            except (ValueError, PermissionError):
                raise HTTPException(403, "Question entity or source access was revoked")
        elif event.event_type == "QUESTION" and event.payload.get("kind") == "planner_choice":
            if not db.scalar(select(PlanStep.id).join(ExecutionPlan, ExecutionPlan.id == PlanStep.plan_id)
                             .where(ExecutionPlan.run_id == event.run_id)):
                raise HTTPException(403, "Question source is unavailable")
        for fid in ids:
            f = db.get(Function, fid)
            if not f or f.disabled or not permitted(f, token.user_id):
                raise HTTPException(403, "Result access was revoked")
    return [event_view(e) for e in batch]


@app.post("/api/v2/management/workspaces/{ws}/agents/{aid}/revoke-origin")
def revoke_origin(ws: str, aid: str, body: OriginBody, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "publisher")
    a = get_resource(db, Agent, aid, ws, lock=True)
    origin = body.origin
    if not isinstance(origin, str):
        raise HTTPException(422, "origin required")
    a.revoked_origins = list(set(a.revoked_origins + [origin]))
    audit(db, token.user_id, ws, "agent.revoke_origin", aid, {"origin": origin})
    db.commit(); return {"revoked": True}


@app.get("/api/v2/runtime/conversations/{cid}/events", response_model=list[EventOut])
def events(cid: str, after: int = 0, stream: bool = False, token=Depends(authenticate), db: Session = Depends(session)):
    if after < 0:
        raise HTTPException(422, "Cursor cannot be negative")
    initial = read_events(db, cid, token, after)
    if not stream:
        return initial
    token_id = token.id; user_id = token.user_id
    async def generator():
        cursor = after
        while True:
            with Session(engine) as fresh:
                set_identity(fresh, user_id)
                current = fresh.get(AuthToken, token_id)
                if not token_valid(fresh, current):
                    return
                try:
                    batch = read_events(fresh, cid, current, cursor)
                except HTTPException:
                    return
            for event in batch:
                cursor = event["sequence"]
                yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if not batch:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)
    return StreamingResponse(generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/v2/runtime/sessions/logout")
def logout(token=Depends(authenticate), db: Session = Depends(session)):
    # Canonical ordering agrees with session establishment: parent -> session -> token.
    parent_id = token.parent_token_id if token.runtime_session_id else token.id
    if parent_id:
        db.scalar(select(AuthToken).where(AuthToken.id == parent_id).with_for_update().execution_options(populate_existing=True))
    if token.runtime_session_id:
        runtime = db.scalar(select(RuntimeSession).where(RuntimeSession.id == token.runtime_session_id).with_for_update().execution_options(populate_existing=True))
        runtime.revoked = True
    else:
        child_ids = list(db.scalars(select(AuthToken.runtime_session_id).where(AuthToken.parent_token_id == token.id, AuthToken.runtime_session_id.is_not(None))))
        for runtime in db.scalars(select(RuntimeSession).where(RuntimeSession.id.in_(child_ids)).order_by(RuntimeSession.id).with_for_update()):
            runtime.revoked = True
    token = db.scalar(select(AuthToken).where(AuthToken.id == token.id).with_for_update().execution_options(populate_existing=True))
    token.revoked = True
    predicate = Conversation.runtime_session_id == token.runtime_session_id if token.runtime_session_id else Conversation.token_id == token.id
    for c in db.scalars(select(Conversation).where(predicate)):
        c.status = "revoked"
    db.commit(); return {"revoked": True}


@app.post("/api/v2/runtime/embed/handshakes", response_model=HandshakeOut)
def handshake(body: EmbedBody, db: Session = Depends(session)):
    row = db.execute(text("SELECT * FROM intent_embed_agent(:id)"), {"id": body.agent_id}).mappings().first()
    if not row or body.host_origin not in row["allowed_origins"]:
        raise HTTPException(403, "Host origin is not allowed")
    from .workspace_lifecycle import require_workspace
    epoch = require_workspace(db, row["workspace_id"], lock=True)
    h = Handshake(id=secrets.token_urlsafe(24), agent_id=body.agent_id, workspace_id=row["workspace_id"], host_origin=body.host_origin,
                  workspace_epoch=epoch, expires_at=now() + timedelta(seconds=settings().ticket_seconds))
    db.add(h); db.commit(); return {"instance_nonce": h.id, "expires_at": h.expires_at, "protocol_version": "1"}


@app.post("/api/v2/integrations/embed-tickets", response_model=TicketOut)
def ticket(body: EmbedBody, authorization: str | None = Header(default=None), db: Session = Depends(session)):
    if settings().identity_mode == "local":
        from .identity_api import key_ticket
        return key_ticket(db, body, authorization)
    token = authenticate(authorization, db)
    a = runtime_agent(db, body.agent_id, token)
    from .workspace_lifecycle import require_workspace
    epoch = require_workspace(db, a.workspace_id, lock=True)
    if token.scope != "management":
        raise HTTPException(403, "Runtime sessions cannot mint embed tickets")
    from .trust import integration_allowed, token_integration
    inherited = check_session_binding(db, body.runtime_session_id, token, a, "embed", body.host_origin) if body.runtime_session_id else None
    if not integration_allowed(a, db.get(Release, a.current_release_id), token, "embed", body.host_origin,
                               inherited.integration_id if inherited else None, {"identity_policy": inherited.identity_policy} if inherited else None):
        raise HTTPException(403, "Identity integration does not permit this host binding")
    if settings().env == "production":
        delegation = token.delegation
        if token.source != "introspection" or not delegation.get("integration_id") or body.host_origin not in delegation.get("origins", []) or a.id not in delegation.get("agent_ids", []) or a.workspace_id not in delegation.get("workspace_ids", []) or token.user_id not in delegation.get("user_ids", []):
            raise HTTPException(403, "Trusted integration delegation does not permit this binding")
    h = db.get(Handshake, body.instance_nonce or "")
    active_origins = db.get(Release, a.current_release_id).snapshot["allowed_origins"]
    if not h or h.workspace_epoch != epoch or h.expires_at <= now() or h.agent_id != a.id or h.host_origin != body.host_origin or body.host_origin not in active_origins or body.host_origin in a.revoked_origins:
        raise HTTPException(403, "Invalid or expired handshake")
    if not effective_channels(a, db.get(Release, a.current_release_id))["embed"]:
        raise HTTPException(403, "Embed channel disabled")
    if body.runtime_session_id:
        check_session_binding(db, body.runtime_session_id, token, a, "embed", body.host_origin)
    t = Ticket(id=secrets.token_urlsafe(36), user_id=token.user_id, workspace_id=a.workspace_id, agent_id=a.id,
               workspace_epoch=epoch,
               host_origin=body.host_origin, instance_nonce=h.id, expires_at=now() + timedelta(seconds=settings().ticket_seconds), source_token_id=token.id, runtime_session_id=body.runtime_session_id, integration_id=token_integration(token))
    db.add(t); db.commit(); return {"ticket": t.id, "expires_at": t.expires_at}


@app.post("/api/v2/runtime/embed/exchange", response_model=TokenOut)
def exchange(body: EmbedBody, db: Session = Depends(session)):
    if settings().identity_mode == "local":
        from .identity_api import key_exchange
        return key_exchange(db, body)
    initial = db.get(Ticket, body.ticket or "")
    from .workspace_lifecycle import require_workspace
    if initial: require_workspace(db, initial.workspace_id, epoch=initial.workspace_epoch, lock=True)
    t = db.scalar(select(Ticket).where(Ticket.id == (body.ticket or "")).with_for_update().execution_options(populate_existing=True))
    if not t or t.consumed or t.expires_at <= now() or (t.agent_id, t.host_origin, t.instance_nonce, t.runtime_session_id) != (body.agent_id, body.host_origin, body.instance_nonce, body.runtime_session_id):
        raise HTTPException(403, "Invalid, expired or consumed ticket")
    parent = db.get(AuthToken, t.source_token_id)
    if not token_valid(db, parent):
        raise HTTPException(403, "Issuing identity was revoked")
    from .trust import token_integration
    if not t.integration_id or t.integration_id != token_integration(parent):
        raise HTTPException(403, "Ticket identity integration differs")
    set_identity(db, t.user_id)
    a = runtime_agent(db, t.agent_id, parent)
    if t.host_origin not in db.get(Release, a.current_release_id).snapshot["allowed_origins"] or t.host_origin in a.revoked_origins:
        raise HTTPException(403, "Host authorization was revoked")
    h = db.get(Handshake, t.instance_nonce)
    if not h or h.expires_at <= now():
        raise HTTPException(403, "Handshake expired")
    t.consumed = True
    token = scoped_session(db, parent, a, "embed", t.runtime_session_id, t.host_origin, t.instance_nonce)
    result = token_view(token); db.commit(); return result


@app.post("/api/v2/dev/seed", response_model=SeedOut)
def seed(token=Depends(authenticate), db: Session = Depends(session)):
    if settings().env == "production" or not settings().allow_dev_auth or settings().identity_mode != "legacy-test":
        raise HTTPException(404, "Not found")
    # A development subject has one server-owned fixture space. Additional
    # memberships and unordered directory results cannot redirect this seed.
    ws = {"dev-admin": "ws-dev", "dev-reader": "ws-dev", "dev-other": "ws-other"}.get(token.user_id) if token.source == "development" else None
    if ws is None:
        raise HTTPException(403, "Registered development identity required")
    manager(db, token, ws)
    db.execute(text("SELECT pg_advisory_xact_lock(9102202)"))
    fixtures = []
    for name, aliases, field in [("开发夹具：资料查询", ["查询资料", "资料查询"], "record_id"), ("开发夹具：记录列表", ["记录列表", "查询记录"], "category")]:
        f = db.scalar(select(Function).where(Function.workspace_id == ws, Function.definition["name"].astext == name))
        if not f:
            from .schemas import Definition
            d = Definition(name=name, description="明确的本地演示夹具，不计入真实20功能验收", object_type="dev:Record", result_type="dev:Detail", aliases=aliases,
                           input_schema={"type": "object", "properties": {field: {"type": "string", "minLength": 1}}, "required": [field], "additionalProperties": False})
            f = Function(workspace_id=ws, definition=d.model_dump()); db.add(f); db.flush()
            validate_function(db, f, token, ValidateBody(arguments={field: "demo"}, probe=True)); publish_function(db, f, token)
        fixtures.append(f)
    a = db.scalar(select(Agent).where(Agent.workspace_id == ws, Agent.name == "开发验证助手"))
    if not a:
        a = Agent(workspace_id=ws, name="开发验证助手", capability_ids=[f.id for f in fixtures],
                  allowed_origins=settings().cors_origins.split(","), suggested_questions=["查询资料 record_id=demo-001", "查询资料", "记录列表 category=demo"])
        db.add(a); db.flush(); publish_agent(db, a, token)
    result = {"workspace_id": ws, "functions": [function_view(f) for f in fixtures], "agent": agent_view(a, db), "fixture_mode": True}
    db.commit(); return result


from .application_api import router as application_router
app.include_router(application_router)
from .domains import router as domain_router
app.include_router(domain_router)
from .evaluation_api import router as evaluation_router
app.include_router(evaluation_router)
from .source_api import router as source_router
app.include_router(source_router)
from .ingest_api import router as ingest_router
app.include_router(ingest_router)
from .governance_api import router as governance_router
app.include_router(governance_router)
from .shadow_api import router as shadow_router
app.include_router(shadow_router)
from .planning_api import router as planning_router
app.include_router(planning_router)
from .model_profiles_api import router as model_profiles_router
app.include_router(model_profiles_router)
from .demo_api import router as demo_router
app.include_router(demo_router)
from .zhiyu_api import router as zhiyu_router
app.include_router(zhiyu_router)
from .trace_api import router as trace_router
app.include_router(trace_router)
from .workspace_admin import router as workspace_admin_router
app.include_router(workspace_admin_router)

from .identity_api import router as identity_router
app.include_router(identity_router)
from .business_catalog import router as business_catalog_router
app.include_router(business_catalog_router)
from .conversation_logs import router as conversation_logs_router
app.include_router(conversation_logs_router)
