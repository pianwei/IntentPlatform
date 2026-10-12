"""Local account and short-lived runtime entry points. Secrets are returned once."""
import secrets
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, Response, Header
from sqlalchemy import select, text, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .auth import authenticate, workspaces
from .config import settings
from .db import session, now, uid, set_identity
from .models import AuthToken, Agent, Release, Membership, RuntimeSession, Ticket, Handshake
from .identity_models import LocalAccount, LocalCredential, IntegrationKey, AnonymousPrincipal, LoginThrottle, IdentityAudit
from .identity_schemas import (PasswordLogin, PasswordChange, PasswordSetup, ProfilePatch, AccountCreate, AccountPatch,
                               AccountOut, AccountSetupOut, KeyCreate, KeyOut, KeySecretOut, AnonymousSessionBody, KeySessionRevoke)
from .api_models import TokenOut, RevisionBody, ReleaseOut
from .local_identity import (secret_hash, verify_secret, platform_ids, identity_audit, local_account, account_view,
                             key_valid, local_token_valid, mint_password_token, credential, read_credential,
                             invalidate_account, local_profile, assert_local_release, mint_runtime, locked_account,
                             locked_key, new_runtime, _DUMMY_PASSWORD_HASH)

router = APIRouter()
COOKIE = "intent_refresh"


def local_only():
    if settings().identity_mode != "local":
        raise HTTPException(404, "Local identity unavailable")


def origin_check(request, cookie=False):
    origin = request.headers.get("origin")
    approved = {item.strip() for item in settings().cors_origins.split(",")}
    if (origin and origin not in approved) or (cookie and not origin and not request.headers.get("authorization")):
        raise HTTPException(403, "Untrusted request origin")


def set_refresh(response, value):
    response.set_cookie(COOKIE, value, max_age=settings().refresh_seconds, httponly=True,
                        secure=settings().env == "production", samesite="strict", path="/api/v2/auth")
    response.headers["Cache-Control"] = "no-store"


def login_view(token):
    return {"access_token": token.id, "user_id": token.user_id, "expires_at": token.expires_at,
            "token_type": "bearer", "fixture_mode": settings().fixture_mode}


@router.post("/api/v2/auth/login", response_model=TokenOut)
def login(body: PasswordLogin, request: Request, response: Response, db: Session = Depends(session)):
    local_only(); origin_check(request)
    username = body.username.strip().lower()
    # Database serialization works across API workers and unknown usernames.
    db.execute(text("SELECT pg_advisory_xact_lock(9102219,hashtext(:bucket))"), {"bucket": username})
    throttle = db.get(LoginThrottle, username)
    if throttle and throttle.failures >= 5 and throttle.blocked_until > now():
        raise HTTPException(429, "Too many login attempts; try again later", headers={"Retry-After": "300"})
    account = db.scalar(select(LocalAccount).where(LocalAccount.username == username).with_for_update())
    password_ok = verify_secret(account.password_hash if account and account.password_hash else _DUMMY_PASSWORD_HASH, body.password)
    accepted = account and account.active and not account.must_change_password and password_ok
    if not accepted:
        if throttle is None:
            throttle = LoginThrottle(bucket=username); db.add(throttle)
        throttle.failures = (throttle.failures or 0) + 1 if throttle.blocked_until and throttle.blocked_until > now() else 1
        throttle.blocked_until = now() + timedelta(minutes=5)
        db.commit()
        raise HTTPException(401, "Invalid username or password")
    if throttle:
        throttle.failures = 0
    token = mint_password_token(db, account)
    refresh = credential(db, account, "refresh", settings().refresh_seconds, token.id)
    identity_audit(db, account.user_id, account.user_id, "account.login")
    db.commit(); set_refresh(response, refresh)
    return login_view(token)


@router.post("/api/v2/auth/refresh", response_model=TokenOut)
def refresh(request: Request, response: Response, db: Session = Depends(session)):
    local_only(); origin_check(request, cookie=True)
    row, account = read_credential(db, request.cookies.get(COOKIE), "refresh")
    if account.must_change_password:
        raise HTTPException(401, "Password setup required")
    row.revoked = True
    previous = db.get(AuthToken, row.token_id) if row.token_id else None
    if previous:
        previous.revoked = True
    token = mint_password_token(db, account)
    value = credential(db, account, "refresh", settings().refresh_seconds, token.id)
    db.commit(); set_refresh(response, value)
    return login_view(token)


@router.post("/api/v2/auth/logout")
def account_logout(request: Request, response: Response, token=Depends(authenticate), db: Session = Depends(session)):
    account = local_account(db, token); origin_check(request)
    token.revoked = True
    for row in db.scalars(select(LocalCredential).where(LocalCredential.user_id == account.user_id, LocalCredential.token_id == token.id)):
        row.revoked = True
    identity_audit(db, account.user_id, account.user_id, "account.logout")
    db.commit(); response.delete_cookie(COOKIE, path="/api/v2/auth")
    return {"revoked": True}


@router.patch("/api/v2/me")
def edit_profile(body: ProfilePatch, request: Request, token=Depends(authenticate), db: Session = Depends(session)):
    account = local_account(db, token); origin_check(request)
    if body.display_name is not None:
        if not body.display_name.strip(): raise HTTPException(422, "Display name is required")
        account.display_name = body.display_name.strip()
    if body.preferences is not None:
        account.preferences = {**(account.preferences or {}), **body.preferences.model_dump(exclude_unset=True)}
    account.revision += 1
    identity_audit(db, account.user_id, account.user_id, "account.profile")
    db.commit(); return local_profile(db, token)


@router.post("/api/v2/me/password")
def change_password(body: PasswordChange, request: Request, token=Depends(authenticate), db: Session = Depends(session)):
    current = local_account(db, token); origin_check(request)
    account = db.scalar(select(LocalAccount).where(LocalAccount.user_id == current.user_id).with_for_update())
    if not verify_secret(account.password_hash, body.current_password):
        raise HTTPException(403, "Current password is incorrect")
    account.password_hash = secret_hash(body.new_password)
    invalidate_account(db, account, revoke_keys=False)
    identity_audit(db, account.user_id, account.user_id, "account.password_changed")
    db.commit(); return {"changed": True, "reauthenticate": True}


@router.get("/api/v2/management/accounts", response_model=list[AccountOut])
def accounts(token=Depends(authenticate), db: Session = Depends(session)):
    local_account(db, token, platform=True)
    return [account_view(row) for row in db.scalars(select(LocalAccount).order_by(LocalAccount.created_at, LocalAccount.user_id).limit(1000))]


@router.get("/api/v2/management/accounts/{user_id}/operations")
def account_operations(user_id: str, token=Depends(authenticate), db: Session = Depends(session)):
    local_account(db, token, platform=True)
    if not db.get(LocalAccount, user_id): raise HTTPException(404, "Account unavailable")
    rows = db.scalars(select(IdentityAudit).where(or_(IdentityAudit.subject_id == user_id, IdentityAudit.actor_id == user_id)).order_by(IdentityAudit.created_at.desc(), IdentityAudit.id.desc()).limit(100))
    return [{key: getattr(row, key) for key in ("id", "actor_id", "subject_id", "action", "created_at")} for row in rows]


@router.post("/api/v2/management/workspaces/{ws}/agents/{aid}/migrate-identity", response_model=ReleaseOut)
def migrate_identity(ws: str, aid: str, body: RevisionBody, token=Depends(authenticate), db: Session = Depends(session)):
    """Create a new release changing only identity policy, never historical snapshots."""
    from copy import deepcopy
    from sqlalchemy import func
    from .auth import manager, audit
    from .services import get_resource, release_view
    from .trust import selected_profiles
    local_account(db, token); manager(db, token, ws, "publisher")
    agent = get_resource(db, Agent, aid, ws, lock=True)
    if agent.revision != body.revision: raise HTTPException(409, "Agent draft revision changed")
    original = db.get(Release, agent.current_release_id) if agent.current_release_id else None
    if not original: raise HTTPException(409, "Publish the application before identity migration")
    if original.snapshot.get("identity_policy", {}).get("version") == "local-v1": return release_view(original)
    snapshot = deepcopy(original.snapshot)
    snapshot["identity_policy"] = {"version": "local-v1", "profiles": selected_profiles(agent)}
    next_version = (db.scalar(select(func.max(Release.version)).where(Release.agent_id == aid)) or 0) + 1
    release = Release(workspace_id=ws, agent_id=aid, version=next_version, snapshot=snapshot)
    db.add(release); db.flush()
    agent.current_release_id = release.id; agent.integration_refs = ["local"]; agent.revision += 1
    audit(db, token.user_id, ws, "agent.identity_migration", aid, {"source_release_id": original.id, "release_id": release.id})
    result = release_view(release); db.commit(); return result


@router.post("/api/v2/management/accounts", response_model=AccountSetupOut, status_code=201)
def create_account(body: AccountCreate, request: Request, token=Depends(authenticate), db: Session = Depends(session)):
    actor = local_account(db, token, platform=True); origin_check(request)
    user = body.user_id or uid("user")
    if user.startswith("guest_"):
        raise HTTPException(422, "Reserved identity namespace")
    if body.user_id and user not in platform_ids() and not db.scalar(select(Membership.user_id).where(Membership.user_id == user)):
        raise HTTPException(422, "Explicit identity binding requires an existing provisioned subject")
    account = LocalAccount(user_id=user, username=body.username.lower(), display_name=body.display_name.strip(),
                           preferences={"density": "comfortable"})
    db.add(account)
    try:
        db.flush()
    except IntegrityError:
        db.rollback(); raise HTTPException(409, "Username or identity is already registered") from None
    value = credential(db, account, "setup", 3600)
    identity_audit(db, actor.user_id, user, "account.bind_legacy" if body.user_id else "account.create")
    db.commit(); return {"account": account_view(account), "setup_token": value}


@router.patch("/api/v2/management/accounts/{user_id}", response_model=AccountOut)
def edit_account(user_id: str, body: AccountPatch, request: Request, token=Depends(authenticate), db: Session = Depends(session)):
    # Serialize cross-account administrator operations before taking actor/target
    # locks; two administrators must not disable each other simultaneously.
    db.execute(text("SELECT pg_advisory_xact_lock(9102220)"))
    actor = local_account(db, token, platform=True); origin_check(request)
    account = locked_account(db, user_id)
    if not account: raise HTTPException(404, "Account unavailable")
    if body.revision != account.revision: raise HTTPException(409, "Account revision changed")
    if body.active is False and user_id in platform_ids():
        others = db.scalar(select(LocalAccount.user_id).where(LocalAccount.user_id.in_(platform_ids()), LocalAccount.user_id != user_id,
                                                           LocalAccount.active.is_(True), LocalAccount.must_change_password.is_(False)))
        if not others: raise HTTPException(409, "Keep at least one active platform administrator")
    if body.display_name is not None:
        if not body.display_name.strip(): raise HTTPException(422, "Display name is required")
        account.display_name = body.display_name.strip()
    if body.preferences is not None:
        account.preferences = {**(account.preferences or {}), **body.preferences.model_dump(exclude_unset=True)}
    if body.active is not None and body.active != account.active:
        account.active = body.active
        invalidate_account(db, account, revoke_keys=True)
    else: account.revision += 1
    identity_audit(db, actor.user_id, user_id, "account.update")
    db.commit(); return account_view(account)


@router.post("/api/v2/management/accounts/{user_id}/reset-password", response_model=AccountSetupOut)
def reset_password(user_id: str, request: Request, token=Depends(authenticate), db: Session = Depends(session)):
    db.execute(text("SELECT pg_advisory_xact_lock(9102220)"))
    actor = local_account(db, token, platform=True); origin_check(request)
    account = locked_account(db, user_id)
    if not account or not account.active: raise HTTPException(404, "Active account unavailable")
    invalidate_account(db, account, revoke_keys=True)
    account.must_change_password = True; account.password_hash = None
    value = credential(db, account, "setup", 3600)
    identity_audit(db, actor.user_id, user_id, "account.password_reset")
    db.commit(); return {"account": account_view(account), "setup_token": value}


@router.post("/api/v2/auth/set-password")
def setup_password(body: PasswordSetup, request: Request, db: Session = Depends(session)):
    local_only(); origin_check(request)
    row, account = read_credential(db, body.setup_token, "setup")
    row.revoked = True; account.password_hash = secret_hash(body.new_password); account.must_change_password = False
    invalidate_account(db, account, revoke_keys=False)
    identity_audit(db, account.user_id, account.user_id, "account.password_set")
    db.commit(); return {"changed": True}


def key_view(row):
    return {key: getattr(row, key) for key in KeyOut.model_fields}


@router.get("/api/v2/me/api-keys", response_model=list[KeyOut])
def keys(token=Depends(authenticate), db: Session = Depends(session)):
    account = local_account(db, token)
    return [key_view(row) for row in db.scalars(select(IntegrationKey).where(IntegrationKey.user_id == account.user_id).order_by(IntegrationKey.created_at.desc()))]


@router.post("/api/v2/me/api-keys", response_model=KeySecretOut, status_code=201)
def create_key(body: KeyCreate, request: Request, token=Depends(authenticate), db: Session = Depends(session)):
    account = local_account(db, token); origin_check(request)
    if body.expires_at and (not body.expires_at.tzinfo or body.expires_at <= now()): raise HTTPException(422, "Expiry must be a future timezone-aware date")
    raw = secrets.token_urlsafe(36)
    row = IntegrationKey(user_id=account.user_id, name=body.name.strip(), secret_hash=secret_hash(raw), expires_at=body.expires_at)
    db.add(row); db.flush(); identity_audit(db, account.user_id, row.id, "key.create")
    db.commit(); return {"key": key_view(row), "secret": row.id + "." + raw}


def owned_key(db, token, key_id):
    account = local_account(db, token)
    row = db.scalar(select(IntegrationKey).where(IntegrationKey.id == key_id, IntegrationKey.user_id == account.user_id).with_for_update())
    if not row: raise HTTPException(404, "Key unavailable")
    return row


@router.post("/api/v2/me/api-keys/{key_id}/revoke", response_model=KeyOut)
def revoke_key(key_id: str, request: Request, token=Depends(authenticate), db: Session = Depends(session)):
    origin_check(request); row = owned_key(db, token, key_id); row.revoked = True
    identity_audit(db, token.user_id, row.id, "key.revoke"); db.commit(); return key_view(row)


@router.post("/api/v2/me/api-keys/{key_id}/rotate", response_model=KeySecretOut)
def rotate_key(key_id: str, request: Request, token=Depends(authenticate), db: Session = Depends(session)):
    origin_check(request); row = owned_key(db, token, key_id)
    if row.revoked: raise HTTPException(409, "Create a new key after revocation")
    raw = secrets.token_urlsafe(36); row.secret_hash = secret_hash(raw); row.version += 1
    identity_audit(db, token.user_id, row.id, "key.rotate"); db.commit()
    return {"key": key_view(row), "secret": row.id + "." + raw}


def authenticate_key(db, authorization):
    value = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
    identifier, sep, raw = value.partition(".")
    key = locked_key(db, identifier) if sep else None
    if not key_valid(db, key) or not verify_secret(key.secret_hash, raw):
        raise HTTPException(401, "Integration key expired or unavailable")
    set_identity(db, key.user_id)
    return key


@router.post("/api/v2/integrations/runtime-sessions/{sid}/revoke")
def key_session_revoke(sid: str, body: KeySessionRevoke, authorization: str | None = Header(default=None), db: Session = Depends(session)):
    local_only()
    key = authenticate_key(db, authorization)
    runtime = db.scalar(select(RuntimeSession).where(RuntimeSession.id == sid).with_for_update())
    if not runtime or (runtime.auth_method, runtime.user_id, runtime.source_key_id, runtime.visitor_id) != ("api-key", key.user_id, key.id, body.visitor_id):
        raise HTTPException(403, "Visitor session cannot be revoked by this key")
    related = list(db.scalars(select(RuntimeSession).where(RuntimeSession.source_key_id == key.id,
        RuntimeSession.visitor_id == body.visitor_id, RuntimeSession.agent_id == runtime.agent_id)))
    for item in related: item.revoked = True
    db.execute(update(AuthToken).where(AuthToken.runtime_session_id.in_([item.id for item in related])).values(revoked=True))
    identity_audit(db, key.user_id, sid, "key.visitor_logout")
    db.commit()
    return {"revoked": True}


def key_ticket(db, body, authorization):
    from .policies import permitted, effective_channels
    from .trust import member_authority
    from .workspace_lifecycle import require_workspace
    ws = db.scalar(text("SELECT intent_public_workspace(:aid)"), {"aid": body.agent_id})
    epoch = require_workspace(db, ws, lock=True)
    key = authenticate_key(db, authorization)
    agent = db.get(Agent, body.agent_id)
    release = db.get(Release, agent.current_release_id) if agent else None
    assert_local_release(release)
    if not member_authority(db.get(Membership, (key.user_id, agent.workspace_id)))[1] or not permitted(agent, key.user_id):
        raise HTTPException(403, "Key owner cannot access this application")
    if not effective_channels(agent, release)["embed"] or body.host_origin not in release.snapshot["allowed_origins"] or body.host_origin in agent.revoked_origins or "local" in agent.revoked_integration_refs:
        raise HTTPException(403, "Embedding is unavailable for this host")
    if not body.visitor_id or len(body.visitor_id) < 16:
        raise HTTPException(422, "A host-server visitor binding of at least 16 characters is required")
    handshake = db.get(Handshake, body.instance_nonce or "")
    if not handshake or handshake.workspace_epoch != epoch or handshake.expires_at <= now() or handshake.agent_id != agent.id or handshake.host_origin != body.host_origin:
        raise HTTPException(403, "Invalid or expired handshake")
    if body.runtime_session_id:
        runtime = db.get(RuntimeSession, body.runtime_session_id)
        if not runtime or runtime.revoked or runtime.workspace_epoch != epoch or (runtime.auth_method, runtime.user_id, runtime.source_key_id, runtime.key_version,
                runtime.visitor_id, runtime.agent_id, runtime.host_origin) != ("api-key", key.user_id, key.id, key.version,
                body.visitor_id, agent.id, body.host_origin):
            raise HTTPException(403, "Visitor session cannot be inherited")
        visited = set()
        while runtime.demo_successor_id:
            if runtime.id in visited or len(visited) >= 32:
                raise HTTPException(403, "Visitor session lineage unavailable")
            visited.add(runtime.id)
            successor = db.get(RuntimeSession, runtime.demo_successor_id)
            if not successor or successor.revoked or successor.workspace_epoch != epoch or (
                successor.auth_method, successor.user_id, successor.source_key_id, successor.key_version,
                successor.visitor_id, successor.agent_id, successor.host_origin) != (
                "api-key", key.user_id, key.id, key.version, body.visitor_id, agent.id, body.host_origin):
                raise HTTPException(403, "Visitor session lineage unavailable")
            runtime = successor
    else:
        # The host records this authoritative visitor locator before delivering
        # its one-time ticket. No runtime bearer exists before exchange.
        runtime = new_runtime(db, key.user_id, agent, "api-key", key=key, visitor_id=body.visitor_id, origin=body.host_origin)
    broker = AuthToken(id=secrets.token_urlsafe(36), user_id=key.user_id, scope="integration", source="local",
                       auth_method="api-key", source_key_id=key.id, credential_version=key.version,
                       expires_at=now()+timedelta(seconds=settings().ticket_seconds))
    db.add(broker); db.flush()
    ticket = Ticket(id=secrets.token_urlsafe(36), user_id=key.user_id, workspace_id=agent.workspace_id, workspace_epoch=epoch, agent_id=agent.id,
                    host_origin=body.host_origin, instance_nonce=body.instance_nonce, expires_at=broker.expires_at,
                    source_token_id=broker.id, runtime_session_id=runtime.id, integration_id="local",
                    source_key_id=key.id, key_version=key.version, visitor_id=body.visitor_id)
    db.add(ticket); db.commit()
    return {"ticket": ticket.id, "expires_at": ticket.expires_at, "runtime_session_id": runtime.id}


def key_exchange(db, body):
    from .policies import permitted, effective_channels
    from .trust import member_authority
    from .runtime_sessions import token_view
    initial = db.get(Ticket, body.ticket or "")
    from .workspace_lifecycle import require_workspace
    if initial: require_workspace(db, initial.workspace_id, epoch=initial.workspace_epoch, lock=True)
    key = locked_key(db, initial.source_key_id) if initial and initial.source_key_id else None
    ticket = db.scalar(select(Ticket).where(Ticket.id == (body.ticket or "")).with_for_update().execution_options(populate_existing=True))
    if not ticket or ticket.integration_id != "local" or not ticket.source_key_id or ticket.consumed or ticket.expires_at <= now() or (
            ticket.agent_id, ticket.host_origin, ticket.instance_nonce, ticket.runtime_session_id) != (
            body.agent_id, body.host_origin, body.instance_nonce, body.runtime_session_id):
        raise HTTPException(403, "Invalid, expired or consumed ticket")
    if not key_valid(db, key, ticket.key_version) or key.user_id != ticket.user_id:
        raise HTTPException(403, "Issuing key was revoked")
    set_identity(db, key.user_id)
    agent = db.get(Agent, ticket.agent_id); release = db.get(Release, agent.current_release_id) if agent else None
    assert_local_release(release)
    if not member_authority(db.get(Membership, (key.user_id, agent.workspace_id)))[1] or not permitted(agent, key.user_id):
        raise HTTPException(403, "Key owner authorization changed")
    handshake = db.get(Handshake, ticket.instance_nonce)
    if not handshake or handshake.expires_at <= now() or not effective_channels(agent, release)["embed"] or ticket.host_origin not in release.snapshot["allowed_origins"] or ticket.host_origin in agent.revoked_origins or "local" in agent.revoked_integration_refs:
        raise HTTPException(403, "Host authorization changed")
    runtime = db.scalar(select(RuntimeSession).where(RuntimeSession.id == ticket.runtime_session_id).with_for_update().execution_options(populate_existing=True)) if ticket.runtime_session_id else None
    if ticket.runtime_session_id and (not runtime or runtime.revoked or runtime.demo_successor_id or (runtime.auth_method, runtime.source_key_id, runtime.key_version,
               runtime.visitor_id, runtime.user_id, runtime.agent_id, runtime.host_origin) != ("api-key", key.id, key.version,
               ticket.visitor_id, key.user_id, agent.id, ticket.host_origin)):
        raise HTTPException(403, "Visitor session cannot be inherited")
    token, runtime = mint_runtime(db, key.user_id, agent, "api-key", runtime=runtime, key=key,
                                  visitor_id=ticket.visitor_id, origin=ticket.host_origin)
    ticket.consumed = True; result = token_view(token); db.commit(); return result


@router.post("/api/v2/runtime/apps/{public_id}/anonymous-sessions", response_model=TokenOut)
def anonymous_session(public_id: str, body: AnonymousSessionBody, db: Session = Depends(session)):
    local_only()
    from .application_api import public_app
    from .policies import effective_channels
    from .runtime_sessions import token_view
    config = public_app(db, public_id)
    if not config["channels"]["chat"]: raise HTTPException(403, "Public chat is disabled")
    from .workspace_lifecycle import require_workspace
    # This bootstrap projection exposes no business data or member identity.
    ws = db.scalar(text("SELECT intent_public_workspace(:aid)"), {"aid": config["agent_id"]})
    require_workspace(db, ws, lock=True)
    runtime = None
    if body.runtime_session_id or body.recovery_secret:
        if not body.runtime_session_id or not body.recovery_secret: raise HTTPException(403, "Recovery credential required")
        # Query only the unprivileged bootstrap projection, then set owner RLS.
        owner = db.execute(text("SELECT user_id FROM intent_anonymous_session_owner(:sid,:aid)"), {"sid": body.runtime_session_id, "aid": config["agent_id"]}).scalar()
        if not owner: raise HTTPException(403, "Anonymous session unavailable")
        set_identity(db, owner)
        runtime = db.scalar(select(RuntimeSession).where(RuntimeSession.id == body.runtime_session_id).with_for_update())
        guest = db.get(AnonymousPrincipal, owner)
        if not runtime or runtime.revoked or runtime.auth_method != "anonymous" or not guest or guest.revoked or guest.expires_at <= now() or not verify_secret(runtime.recovery_hash, body.recovery_secret):
            raise HTTPException(403, "Anonymous recovery credential unavailable")
    else:
        guest = AnonymousPrincipal(user_id=uid("guest"), workspace_id=ws, agent_id=config["agent_id"], release_id=config["release_id"], expires_at=now()+timedelta(seconds=settings().anonymous_seconds))
        db.add(guest); db.flush(); set_identity(db, guest.user_id)
    agent = db.get(Agent, guest.agent_id); release = db.get(Release, agent.current_release_id) if agent else None
    assert_local_release(release)
    if not effective_channels(agent, release)["chat"] or "local" in agent.revoked_integration_refs:
        raise HTTPException(403, "Public chat is unavailable")
    token, runtime = mint_runtime(db, guest.user_id, agent, "anonymous", runtime=runtime)
    from .synthetic_runtime import validate_persona
    persona = validate_persona(body.demo_persona, release.snapshot)
    if body.runtime_session_id:
        if body.demo_persona is not None and persona != runtime.demo_persona:
            raise HTTPException(409, "切换模拟角色需要创建新会话")
    else:
        runtime.demo_persona = persona
    recovery = None
    if not runtime.recovery_hash:
        recovery = secrets.token_urlsafe(36); runtime.recovery_hash = secret_hash(recovery)
    result = token_view(token) | {"recovery_secret": recovery}
    db.commit(); return result
