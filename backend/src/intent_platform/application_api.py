"""Published application delivery and authorized conversation recovery."""
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Body
from fastapi.responses import Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .db import session, engine, set_identity
from .models import Agent, Function, Release, Asset, ResultFile, Conversation, Run, Call, AuthToken
from .auth import authenticate, manager, audit
from .schemas import AccessPolicy, Channels
from .api_models import AgentOut, FunctionOut, PublicAppOut, TokenOut, SessionBody, ConversationOut, HistoryOut, AssetOut
from .services import get_resource, agent_view, function_view, normalized_ui, conversation_view, owned_conversation, run_view
from .policies import effective_channels, permitted
from .runtime_sessions import application, scoped_session, token_view
from .origins import exact_origin
from .resolvers import bounded_resolution

router = APIRouter()


@router.put("/api/v2/management/workspaces/{ws}/agents/{aid}/access", response_model=AgentOut)
def agent_access(ws: str, aid: str, body: AccessPolicy, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "publisher")
    a = get_resource(db, Agent, aid, ws, lock=True)
    a.access_mode = body.access_mode; a.allowed_users = body.allowed_users
    audit(db, token.user_id, ws, "agent.access", aid, body.model_dump())
    result = agent_view(a, db); db.commit(); return result


@router.put("/api/v2/management/workspaces/{ws}/functions/{fid}/access", response_model=FunctionOut)
def function_access(ws: str, fid: str, body: AccessPolicy, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    f = get_resource(db, Function, fid, ws, lock=True)
    f.access_mode = body.access_mode; f.allowed_users = body.allowed_users
    audit(db, token.user_id, ws, "function.access", fid, body.model_dump())
    result = function_view(f); db.commit(); return result


@router.put("/api/v2/management/workspaces/{ws}/agents/{aid}/channels", response_model=AgentOut)
def channels(ws: str, aid: str, body: Channels, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "publisher")
    a = get_resource(db, Agent, aid, ws, lock=True)
    r = db.get(Release, a.current_release_id) if a.current_release_id else None
    published = r.snapshot.get("channels", {"chat": True, "embed": True}) if r else {}
    if any(enabled and not published.get(k) for k, enabled in body.model_dump().items()):
        raise HTTPException(409, "Publish an enabled channel before activating it")
    a.live_channels = body.model_dump()
    audit(db, token.user_id, ws, "agent.channels", aid, a.live_channels)
    result = agent_view(a, db); db.commit(); return result


def public_app(db, public_id):
    row = db.execute(text("SELECT * FROM intent_public_app(:id)"), {"id": public_id}).mappings().first()
    if not row or not any(row["channels"].values()):
        raise HTTPException(404, "Application unavailable")
    return {**dict(row), "runtime_ui": normalized_ui(row["runtime_ui"])}


@router.get("/api/v2/runtime/apps/{public_id}", response_model=PublicAppOut)
def app_config(public_id: str, db: Session = Depends(session)):
    return public_app(db, public_id)


def channel_policy(public_id, channel, db):
    try:
        config = public_app(db, public_id)
    except HTTPException:
        raise HTTPException(403, "Application channel unavailable")
    if not config["channels"][channel]:
        raise HTTPException(403, "Application channel unavailable")
    ancestors = "'none'"
    if channel == "embed":
        policy = db.execute(text("SELECT * FROM intent_embed_agent(:id)"), {"id": public_id}).mappings().first()
        if not policy:
            raise HTTPException(403, "Application channel unavailable")
        # Origin strings were strictly validated on the write path; never reflect request headers.
        try:
            origins = [exact_origin(o) for o in policy["allowed_origins"]]
        except ValueError:
            raise HTTPException(403, "Published embedding policy is invalid")
        ancestors = " ".join(["'self'", *origins])
    csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors " + ancestors
    return Response(status_code=200, headers={"Content-Security-Policy": csp, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/api/v2/runtime/apps/{public_id}/embedding-policy", response_class=Response)
def embedding_policy(public_id: str, db: Session = Depends(session)):
    return channel_policy(public_id, "embed", db)


@router.get("/api/v2/runtime/apps/{public_id}/chat-policy", response_class=Response)
def chat_policy(public_id: str, db: Session = Depends(session)):
    return channel_policy(public_id, "chat", db)


@router.post("/api/v2/runtime/apps/{public_id}/sessions", response_model=TokenOut)
def start_session(public_id: str, body: SessionBody = Body(default=SessionBody()), token=Depends(authenticate), db: Session = Depends(session)):
    result = token_view(scoped_session(db, token, application(db, public_id), "chat", body.runtime_session_id))
    db.commit(); return result


@router.get("/api/v2/runtime/conversations", response_model=list[ConversationOut])
def conversations(token=Depends(authenticate), db: Session = Depends(session)):
    if token.scope != "runtime" or not token.runtime_session_id:
        raise HTTPException(403, "A scoped runtime session is required")
    rows = db.scalars(select(Conversation).where(Conversation.runtime_session_id == token.runtime_session_id,
                     Conversation.user_id == token.user_id, Conversation.status == "active").order_by(Conversation.updated_at.desc())).all()
    return [conversation_view(db, c) for c in rows]


@router.get("/api/v2/runtime/conversations/{cid}/history", response_model=HistoryOut)
@bounded_resolution()
def history(cid: str, token=Depends(authenticate)):
    from .main import read_events
    # Authentication, run state, messages and cursor are re-read in one consistent snapshot.
    with Session(engine.execution_options(isolation_level="REPEATABLE READ")) as db, db.begin():
        set_identity(db, token.user_id)
        token = db.get(AuthToken, token.id)
        conv = owned_conversation(db, cid, token)
        runs = list(db.scalars(select(Run).where(Run.conversation_id == cid).order_by(Run.created_at)))
        events, cursor = [], 0
        while cursor < conv.sequence:
            batch = read_events(db, cid, token, cursor)
            if not batch:
                break
            events.extend(batch); cursor = batch[-1]["sequence"]
        return {"conversation": conversation_view(db, conv), "messages": [{"run_id": r.id, "text": r.text, "revision": r.revision, "status": r.status, "created_at": r.created_at} for r in runs],
                "runs": [run_view(db, r) for r in runs], "events": events, "cursor": conv.sequence}


def binary_response(data, media_type, filename=None):
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store", "Content-Security-Policy": "default-src 'none'; sandbox"}
    if filename:
        headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(filename, safe="")
    return Response(data, media_type=media_type, headers=headers)


@router.post("/api/v2/management/workspaces/{ws}/assets", response_model=AssetOut, status_code=201)
async def upload_icon(ws: str, file: UploadFile = File(...), token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "publisher")
    data = await file.read(2_000_001)
    if len(data) > 2_000_000:
        raise HTTPException(413, "Icon exceeds 2 MB")
    mime = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if data.startswith(b"\xff\xd8\xff") else "image/webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else None
    if not mime:
        raise HTTPException(422, "Only PNG, JPEG and WebP icons are supported")
    a = Asset(workspace_id=ws, media_type=mime, data=data); db.add(a); db.flush()
    audit(db, token.user_id, ws, "asset.upload", a.id)
    result = {"id": a.id, "media_type": mime, "size": len(data)}; db.commit(); return result


@router.get("/api/v2/management/workspaces/{ws}/assets/{asset_id}")
def draft_icon(ws: str, asset_id: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "publisher")
    asset = get_resource(db, Asset, asset_id, ws)
    return binary_response(asset.data, asset.media_type)


@router.get("/api/v2/runtime/apps/{public_id}/icon")
def published_icon(public_id: str, db: Session = Depends(session)):
    row = db.execute(text("SELECT * FROM intent_public_icon(:id)"), {"id": public_id}).mappings().first()
    if not row:
        raise HTTPException(404, "Published icon unavailable")
    return binary_response(row["data"], row["media_type"])


@router.get("/api/v2/runtime/assets/{asset_id}")
def historic_icon(asset_id: str, token=Depends(authenticate), db: Session = Depends(session)):
    if token.scope != "runtime" or not token.runtime_session_id:
        raise HTTPException(403, "A scoped runtime session is required")
    a = get_resource(db, Agent, token.agent_id)
    release_ids = set(db.scalars(select(Conversation.release_id).where(Conversation.runtime_session_id == token.runtime_session_id, Conversation.status == "active")))
    release_ids.add(a.current_release_id)
    releases = db.scalars(select(Release).where(Release.id.in_(release_ids), Release.agent_id == a.id))
    if not any(r.snapshot.get("runtime_ui", {}).get("icon") == {"kind": "asset", "value": asset_id} for r in releases):
        raise HTTPException(404, "Icon is not referenced by an authorized release")
    asset = get_resource(db, Asset, asset_id, a.workspace_id)
    return binary_response(asset.data, asset.media_type)


@router.get("/api/v2/runtime/files/{file_id}")
def download_file(file_id: str, token=Depends(authenticate), db: Session = Depends(session)):
    f = get_resource(db, ResultFile, file_id)
    owned_conversation(db, f.conversation_id, token)
    call = db.get(Call, f.call_id)
    capability = db.get(Function, call.capability_id) if call else None
    if not capability or capability.disabled or not permitted(capability, token.user_id):
        raise HTTPException(403, "Originating capability is unavailable")
    return binary_response(f.data, f.media_type, f.filename)
