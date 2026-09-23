"""Stable, non-secret session identity; bearer credentials rotate independently."""
from fastapi import HTTPException
from sqlalchemy import select
from .models import Agent, Release, RuntimeSession, AuthToken
from .auth import authorize, issue_token, token_valid, audit
from .policies import permitted, effective_channels, dependency_error


def application(db, public_id):
    a = db.scalar(select(Agent).where(Agent.public_id == public_id))
    if not a or not a.current_release_id:
        raise HTTPException(404, "Application unavailable")
    return a


def check_session_binding(db, session_id, parent, agent, channel, origin=None, lock=False):
    q = select(RuntimeSession).where(RuntimeSession.id == session_id)
    if lock:
        q = q.with_for_update()
    existing = db.scalar(q)
    if not existing or existing.revoked or (existing.user_id, existing.agent_id, existing.workspace_id, existing.channel, existing.host_origin) != (parent.user_id, agent.id, agent.workspace_id, channel, origin):
        raise HTTPException(403, "Runtime session cannot be inherited or resumed")
    from .trust import token_integration
    try:
        if existing.integration_id != token_integration(parent): raise ValueError("Integration differs")
    except (ValueError, TypeError):
        raise HTTPException(403, "Runtime session identity integration differs")
    return existing


def scoped_session(db, parent, agent, channel, session_id=None, origin=None, nonce=None):
    # Logout takes this same parent lock. No child can be committed after logout's scan.
    parent = db.scalar(select(AuthToken).where(AuthToken.id == parent.id).with_for_update().execution_options(populate_existing=True))
    if parent.scope != "management" or not token_valid(db, parent):
        raise HTTPException(403, "A trusted identity is required to establish a runtime session")
    authorize(db, parent, agent.workspace_id)
    release = db.get(Release, agent.current_release_id) if agent.current_release_id else None
    if not permitted(agent, parent.user_id) or not release or not effective_channels(agent, release).get(channel):
        raise HTTPException(403, "Application channel or authorization unavailable")
    error = dependency_error(release.snapshot, db)
    if error:
        raise HTTPException(503, error)
    if channel == "embed" and (origin not in release.snapshot["allowed_origins"] or origin in agent.revoked_origins):
        raise HTTPException(403, "Host authorization unavailable")
    if not session_id and channel == "chat":
        legacy = db.scalar(select(RuntimeSession).where(RuntimeSession.user_id == parent.user_id, RuntimeSession.agent_id == agent.id,
                           RuntimeSession.channel == "chat", RuntimeSession.legacy_claimable.is_(True), RuntimeSession.revoked.is_(False)).with_for_update())
        if legacy:
            session_id = legacy.id
    runtime = check_session_binding(db, session_id, parent, agent, channel, origin, lock=True) if session_id else RuntimeSession(
        workspace_id=agent.workspace_id, agent_id=agent.id, user_id=parent.user_id, channel=channel, host_origin=origin)
    from .trust import token_integration, integration_allowed
    if not integration_allowed(agent, release, parent, channel, origin, runtime.integration_id if session_id else None,
                               {"identity_policy": runtime.identity_policy} if session_id else None):
        raise HTTPException(403, "Published identity integration or current delegation unavailable")
    if not session_id:
        runtime.integration_id = token_integration(parent)
        runtime.identity_policy = release.snapshot.get("identity_policy")
        db.add(runtime); db.flush()
    runtime.legacy_claimable = False
    old = db.scalar(select(AuthToken).where(AuthToken.id == runtime.current_token_id).with_for_update().execution_options(populate_existing=True)) if runtime.current_token_id else None
    token = issue_token(db, parent.user_id, scope="runtime", agent_id=agent.id, source=parent.source, parent=parent.id,
                        runtime_session_id=runtime.id,
                        delegation={**parent.delegation, "host_origin": origin, "instance_nonce": nonce, "integration_id": runtime.integration_id})
    runtime.current_token_id = token.id
    if old:
        old.revoked = True  # close previous SSE; queued work resolves the session's current token
    audit(db, parent.user_id, agent.workspace_id, "session.renew" if session_id else "session.create", runtime.id, {"channel": channel})
    return token


def token_view(token):
    from .config import settings
    return {"access_token": token.id, "token_type": "bearer", "user_id": token.user_id, "agent_id": token.agent_id,
            "runtime_session_id": token.runtime_session_id, "expires_at": token.expires_at, "fixture_mode": settings().fixture_mode}
