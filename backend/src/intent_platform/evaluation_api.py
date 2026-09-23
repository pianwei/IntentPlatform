"""Authorized, internally consistent evidence snapshots for the evaluation runner."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from .auth import authenticate
from .db import engine, set_identity
from .models import AuthToken, RuntimeSession, Conversation, Release, Run, Call, Event, Audit, Function
from .services import owned_conversation, owned_run
from .services import visible_candidates
from types import SimpleNamespace
from .policies import permitted
from .evaluation_evidence import evaluation_profile, evaluation_evidence
from .resolvers import bounded_resolution

router = APIRouter()


def scoped(token):
    if token.scope != "runtime" or not token.runtime_session_id:
        raise HTTPException(403, "A scoped runtime session is required")


def check_functions(db, token, identifiers):
    for identifier in identifiers:
        function = db.get(Function, identifier)
        if not function or function.disabled or not permitted(function, token.user_id):
            raise HTTPException(403, "Evaluation evidence includes a capability no longer authorized")


def authorized_release(db, release, token):
    capabilities = visible_candidates(db, release, token)
    ids = {c["capability_id"] for c in capabilities}
    snapshot = {**release.snapshot, "capabilities": capabilities,
                "connector_versions": {key: version for key, version in release.snapshot.get("connector_versions", {}).items() if key in ids}}
    return SimpleNamespace(id=release.id, snapshot=snapshot)


@router.get("/api/v2/runtime/conversations/{cid}/evaluation-profile", response_model=dict)
def profile(cid: str, token=Depends(authenticate)):
    scoped(token)
    with Session(engine.execution_options(isolation_level="REPEATABLE READ")) as db, db.begin():
        set_identity(db, token.user_id); token = db.get(AuthToken, token.id)
        conv = owned_conversation(db, cid, token)
        release = db.get(Release, conv.release_id)
        result = evaluation_profile(authorized_release(db, release, token), conv, db.get(RuntimeSession, conv.runtime_session_id))
        return {**result, "capability_scope": "authorized_subset"}


@router.get("/api/v2/runtime/runs/{rid}/evaluation-evidence", response_model=dict)
@bounded_resolution()
def evidence(rid: str, token=Depends(authenticate)):
    scoped(token)
    with Session(engine.execution_options(isolation_level="REPEATABLE READ")) as db, db.begin():
        set_identity(db, token.user_id); token = db.get(AuthToken, token.id)
        run = owned_run(db, rid, token)
        conv = db.get(Conversation, run.conversation_id)
        calls = list(db.scalars(select(Call).where(Call.run_id == rid)))
        events = list(db.scalars(select(Event).where(Event.run_id == rid).order_by(Event.sequence)))
        ids = {c.capability_id for c in calls}
        ids.update(r["capability_id"] for r in (run.validation_evidence or {}).get("requests", []))
        for event in events:
            payload = event.payload
            ids.update(a["capability_id"] for a in payload.get("actions", []))
            if payload.get("capability_id"):
                ids.add(payload["capability_id"])
            if event.event_type == "QUESTION":
                if payload.get("kind") in {None, "function_choice"}:
                    ids.update(o["id"] for o in payload.get("options", []))
                elif payload.get("kind") in {"entity_mention", "entity_choice", "history_choice"}:
                    from .context_binding import authorize_question
                    try:
                        authorize_question(db, run, payload["question_id"])
                    except (ValueError, PermissionError):
                        raise HTTPException(403, "Question entity or source access was revoked")
        check_functions(db, token, ids)
        audits = list(db.scalars(select(Audit).where(Audit.resource_id == rid, Audit.action.in_(["run.confirm", "run.reject", "run.prepared"])).order_by(Audit.created_at)))
        result = evaluation_evidence(authorized_release(db, db.get(Release, conv.release_id), token), conv, db.get(RuntimeSession, conv.runtime_session_id), run, calls, events, audits)
        result["profile"]["capability_scope"] = "authorized_subset"
        return result
