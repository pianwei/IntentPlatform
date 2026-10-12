"""Opt-in publisher-owned shadow evaluation, with no runtime ledger mutation."""
import copy
from types import SimpleNamespace
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sql
from sqlalchemy.orm import Session
from .auth import authenticate
from .config import settings
from .db import engine, set_identity, uid, now
from .models import AuthToken, Release, Membership, RuntimeSession
from .services import owned_conversation, visible_candidates
from .policies import dependency_error
from .trust import member_authority
from .context_binding import history_context, authorized_history, revalidate_binding_evidence
from .evaluation_api import authorized_release
from .evaluation_evidence import evaluation_profile
from .resolvers import resolver_budget, resolve
from .shadow_evaluation import ShadowBody, ShadowOut, model_attempt, validate_attempt

router = APIRouter()


def load_state(db, cid, token_id, expected_release):
    token = db.get(AuthToken, token_id)
    if not token or token.scope != "runtime" or not token.runtime_session_id:
        raise HTTPException(403, "A scoped runtime owner is required")
    set_identity(db, token.user_id)
    conv = owned_conversation(db, cid, token)
    roles, active = member_authority(db.get(Membership, (token.user_id, conv.workspace_id)))
    if not active or not {"admin", "publisher"}.intersection(roles):
        raise HTTPException(403, "Shadow evaluation requires a publisher or admin owner")
    if conv.release_id != expected_release: raise HTTPException(409, "Conversation release differs")
    release = db.get(Release, conv.release_id)
    if dependency_error(release.snapshot): raise HTTPException(409, "Pinned execution dependencies unavailable")
    candidates = copy.deepcopy(visible_candidates(db, release, token))
    if not any(c["compiled"]["compiler_version"] == "finite-json-v2" for c in release.snapshot["capabilities"]):
        raise HTTPException(422, "Shadow comparison requires a domain-enabled release")
    profile = evaluation_profile(authorized_release(db, release, token), conv, db.get(RuntimeSession, conv.runtime_session_id))
    return token, conv, release, candidates, profile


def check_state(db, cid, token_id, body, baseline):
    state = load_state(db, cid, token_id, body.release_id)
    if state[1].sequence != baseline["sequence"] or state[3] != baseline["candidates"] or state[4] != baseline["profile"]:
        raise HTTPException(409, "Conversation or authorization changed during comparison")
    return state


def verify_binding_evidence(db, scratch, release, rows):
    for row in rows:
        run = row["run"]
        for record in run.bindings.values():
            if not isinstance(record, dict) or "parameter" not in record: continue
            profile = release.snapshot["resolver_profiles"][record["resolver_ref"]]
            if record.get("state") == "resolved":
                revalidate_binding_evidence(db, run, {"item": record}, {record["parameter"]: record["value"]}, release)
            for candidate in record.get("options", {}).values():
                if candidate.get("history_ref"):
                    current = authorized_history(db, run, candidate["history_ref"], profile["entity_type"], record["resolver_ref"], profile, max_checks=1)
                else:
                    current = resolve(record["resolver_ref"], profile, run.workspace_id, run.user_id, candidate["label"], candidate["id"])
                if len(current) != 1 or current[0]["id"] != candidate["id"] or current[0]["label"] != candidate["label"]:
                    raise PermissionError("Shadow entity evidence was revoked")


@router.post("/api/v2/runtime/conversations/{cid}/shadow-comparison", response_model=ShadowOut)
def compare(cid: str, body: ShadowBody, token=Depends(authenticate)):
    if not getattr(settings(), "allow_shadow_evaluation", False):
        raise HTTPException(403, "Shadow evaluation is disabled by the server")
    with Session(engine) as db:
        _, conv, release, candidates, profile = load_state(db, cid, token.id, body.release_id)
        workspace_id, user_id = conv.workspace_id, conv.user_id
        baseline = {"sequence": conv.sequence, "candidates": candidates, "profile": profile}
    # A separate session-level advisory lock bounds concurrent experimental model use.
    # It cannot lock a Run, occupy a dispatch slot, or enqueue business work.
    with engine.connect() as lock:
        acquired = lock.execute(sql("SELECT pg_try_advisory_lock(9102214, hashtext(:ws))"), {"ws": workspace_id}).scalar()
        if not acquired: raise HTTPException(429, "Workspace shadow comparison is busy", headers={"Retry-After": "1"})
        try:
            return execute_comparison(cid, body, token.id, workspace_id, user_id, baseline)
        finally:
            lock.execute(sql("SELECT pg_advisory_unlock(9102214, hashtext(:ws))"), {"ws": workspace_id})


def execute_comparison(cid, body, token_id, workspace_id, user_id, baseline):
    reference = now()
    scratch = SimpleNamespace(id=uid("shadow"), workspace_id=workspace_id, user_id=user_id, conversation_id=cid, created_at=reference, bindings={})
    stats = {}; rows = []
    try:
        with resolver_budget(calls=200, seconds=settings().model_timeout * 3 + 100):
            with Session(engine) as db, resolver_budget(calls=40, seconds=20):
                _, _, release, candidates, profile = check_state(db, cid, token_id, body, baseline)
                context = history_context(db, scratch, release, stats)
            for variant in ("A", "B", "C"):
                with Session(engine) as db, resolver_budget(calls=40, seconds=20):
                    _, _, release, candidates, _ = check_state(db, cid, token_id, body, baseline)
                    if history_context(db, scratch, release) != context:
                        raise HTTPException(409, "Authorized history changed during comparison")
                row = model_attempt(body.text, candidates, context, variant)
                arm = copy.deepcopy(scratch)
                with Session(engine) as db, resolver_budget(calls=40, seconds=20):
                    _, _, release, candidates, _ = check_state(db, cid, token_id, body, baseline)
                    validate_attempt(db, arm, release, candidates, row)
                rows.append({"result": row, "run": arm})
            with Session(engine) as db, resolver_budget(calls=40, seconds=20):
                _, _, release, _, _ = check_state(db, cid, token_id, body, baseline)
                if history_context(db, scratch, release) != context:
                    raise HTTPException(409, "Authorized history changed during comparison")
                verify_binding_evidence(db, scratch, release, rows)
            return ShadowOut(release_id=body.release_id, conversation_id=cid, reference_time=reference.isoformat(),
                             profile=profile, candidate_ids=[c["capability_id"] for c in baseline["candidates"]],
                             context=context, context_stats=stats, variants=[row["result"] for row in rows])
    except (ValueError, PermissionError):
        raise HTTPException(403, "Trusted shadow context or entity evidence is unavailable")
