"""Local identity authority shared by HTTP, stream recovery and workers."""
import json
import secrets
from datetime import timedelta
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException
from sqlalchemy import select, update
from .config import settings
from .db import now, uid, set_identity
from .identity_models import LocalAccount, LocalCredential, IntegrationKey, AnonymousPrincipal, IdentityAudit
from .identity_schemas import Preferences

HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
# Unknown names still perform the same password work; this value grants no access.
_DUMMY_PASSWORD_HASH = HASHER.hash(secrets.token_urlsafe(36))


def secret_hash(value):
    return HASHER.hash(value)


def verify_secret(stored, supplied):
    if not stored or not supplied:
        return False
    try:
        return HASHER.verify(stored, supplied)
    except (VerificationError, InvalidHashError):
        return False


def platform_ids():
    return set(json.loads(settings().platform_admins_json or "[]"))


def identity_audit(db, actor, subject, action):
    db.add(IdentityAudit(actor_id=actor, subject_id=subject, action=action))


def locked_account(db, user_id):
    return db.scalar(select(LocalAccount).where(LocalAccount.user_id == user_id).with_for_update().execution_options(populate_existing=True))


def local_account(db, token, platform=False):
    if token.source != "local" or token.scope != "management" or token.auth_method != "password":
        raise HTTPException(403, "Local account login required")
    # Credential issuance and revocation take this same lock. Recheck after a
    # waiter wakes up, rather than trusting authenticate's earlier snapshot.
    account = locked_account(db, token.user_id)
    db.refresh(token)
    if token.revoked or token.expires_at <= now() or not account or not account.active or account.must_change_password or token.credential_version != account.credential_version:
        raise HTTPException(401, "Account session expired")
    if platform and account.user_id not in platform_ids():
        raise HTTPException(403, "Platform account administrator required")
    return account


def account_view(account):
    return {key: getattr(account, key) for key in ("user_id", "username", "display_name", "active", "must_change_password", "revision", "created_at")} | {"preferences": Preferences.model_validate(account.preferences or {}).model_dump(), "is_platform_admin": account.user_id in platform_ids()}


def key_valid(db, key, version=None):
    if not key or key.revoked or (key.expires_at and key.expires_at <= now()) or (version is not None and version != key.version):
        return False
    account = db.get(LocalAccount, key.user_id)
    return bool(account and account.active and not account.must_change_password)


def local_token_valid(db, token):
    """Every credential descendant is rechecked, including detached key sessions."""
    from .models import RuntimeSession
    if settings().identity_mode != "local" or token.source != "local":
        return False
    if token.auth_method == "anonymous":
        guest = db.get(AnonymousPrincipal, token.user_id)
        runtime = db.get(RuntimeSession, token.runtime_session_id) if token.runtime_session_id else None
        return bool(token.scope == "runtime" and guest and not guest.revoked and guest.expires_at > now()
                    and runtime and runtime.auth_method == "anonymous" and runtime.user_id == token.user_id
                    and runtime.agent_id == token.agent_id and runtime.current_token_id == token.id)
    account = db.get(LocalAccount, token.user_id)
    if not account or not account.active or account.must_change_password:
        return False
    if token.auth_method == "api-key":
        key = db.get(IntegrationKey, token.source_key_id) if token.source_key_id else None
        runtime = db.get(RuntimeSession, token.runtime_session_id) if token.runtime_session_id else None
        return bool(token.scope == "runtime" and key and key.user_id == token.user_id and key_valid(db, key, token.credential_version)
                    and runtime and runtime.auth_method == "api-key" and runtime.source_key_id == key.id
                    and runtime.key_version == key.version and runtime.current_token_id == token.id)
    if token.auth_method != "password" or token.credential_version != account.credential_version:
        return False
    if token.parent_token_id:
        from .models import AuthToken
        parent = db.get(AuthToken, token.parent_token_id)
        if not parent or parent.source != "local" or parent.auth_method != "password" or parent.credential_version != account.credential_version:
            return False
    return True


def guest_allowed(db, user_id, workspace_id=None, agent_id=None):
    guest = db.get(AnonymousPrincipal, user_id)
    return bool(guest and not guest.revoked and guest.expires_at > now()
                and (workspace_id is None or guest.workspace_id == workspace_id)
                and (agent_id is None or guest.agent_id == agent_id))


def mint_password_token(db, account):
    from .auth import issue_token
    token = issue_token(db, account.user_id, source="local")
    token.auth_method = "password"; token.credential_version = account.credential_version
    return token


def credential(db, account, kind, seconds, token_id=None):
    raw = secrets.token_urlsafe(36)
    row = LocalCredential(user_id=account.user_id, kind=kind, secret_hash=secret_hash(raw), version=account.credential_version,
                          expires_at=now() + timedelta(seconds=seconds), token_id=token_id)
    db.add(row); db.flush()
    return row.id + "." + raw


def read_credential(db, value, kind):
    identifier, sep, raw = (value or "").partition(".")
    initial = db.get(LocalCredential, identifier) if sep else None
    account = locked_account(db, initial.user_id) if initial else None
    row = db.scalar(select(LocalCredential).where(LocalCredential.id == identifier).with_for_update().execution_options(populate_existing=True)) if initial else None
    if not row or row.kind != kind or row.revoked or row.expires_at <= now() or not verify_secret(row.secret_hash, raw):
        raise HTTPException(401, "Credential expired or unavailable")
    if not account or not account.active or account.credential_version != row.version:
        raise HTTPException(401, "Credential expired or unavailable")
    return row, account


def locked_key(db, identifier):
    initial = db.get(IntegrationKey, identifier)
    if not initial:
        return None
    locked_account(db, initial.user_id)
    return db.scalar(select(IntegrationKey).where(IntegrationKey.id == identifier).with_for_update().execution_options(populate_existing=True))


def invalidate_account(db, account, revoke_keys=False):
    from .models import AuthToken
    account.credential_version += 1; account.revision += 1
    db.execute(update(LocalCredential).where(LocalCredential.user_id == account.user_id).values(revoked=True))
    methods = ["password", "api-key"] if revoke_keys else ["password"]
    db.execute(update(AuthToken).where(AuthToken.user_id == account.user_id, AuthToken.source == "local", AuthToken.auth_method.in_(methods)).values(revoked=True))
    if revoke_keys:
        db.execute(update(IntegrationKey).where(IntegrationKey.user_id == account.user_id).values(revoked=True))


def local_profile(db, token):
    from .auth import workspaces
    account = local_account(db, token)
    return {"user_id": account.user_id, "username": account.username, "display_name": account.display_name,
            "preferences": Preferences.model_validate(account.preferences or {}).model_dump(), "scope": token.scope, "workspaces": workspaces(db, token),
            "is_platform_admin": account.user_id in platform_ids(), "must_change_password": account.must_change_password,
            "auth_method": token.auth_method, "fixture_mode": settings().fixture_mode}


def assert_local_release(release):
    if not release or release.snapshot.get("identity_policy", {}).get("version") != "local-v1":
        raise HTTPException(403, "Legacy release is available only in management history; publish a local identity release")


def new_runtime(db, user_id, agent, method, *, key=None, visitor_id=None, origin=None):
    from .models import RuntimeSession, Release
    release = db.get(Release, agent.current_release_id)
    assert_local_release(release)
    from .workspace_lifecycle import require_workspace
    epoch = require_workspace(db, agent.workspace_id, lock=True)
    runtime = RuntimeSession(id=uid("session"), workspace_id=agent.workspace_id, workspace_epoch=epoch, agent_id=agent.id, user_id=user_id,
                             channel="embed" if method == "api-key" else "chat", host_origin=origin,
                             integration_id="local", auth_method=method, source_key_id=key.id if key else None,
                             key_version=key.version if key else None, visitor_id=visitor_id,
                             identity_policy=release.snapshot["identity_policy"])
    from .synthetic_runtime import validate_persona
    runtime.demo_persona = validate_persona(None, release.snapshot)
    db.add(runtime); db.flush()
    return runtime


def mint_runtime(db, user_id, agent, method, *, runtime=None, key=None, visitor_id=None, origin=None, parent=None):
    from .models import Release, AuthToken
    from .auth import issue_token
    assert_local_release(db.get(Release, agent.current_release_id))
    from .workspace_lifecycle import require_workspace
    require_workspace(db, agent.workspace_id, epoch=runtime.workspace_epoch if runtime else None, lock=True)
    if runtime is None:
        runtime = new_runtime(db, user_id, agent, method, key=key, visitor_id=visitor_id, origin=origin)
    elif runtime.revoked or runtime.user_id != user_id or runtime.agent_id != agent.id or runtime.auth_method != method:
        raise HTTPException(403, "Runtime session cannot be inherited")
    old = db.get(AuthToken, runtime.current_token_id) if runtime.current_token_id else None
    if old:
        old.revoked = True
    token = issue_token(db, user_id, scope="runtime", agent_id=agent.id, source="local", parent=parent.id if parent else None,
                        runtime_session_id=runtime.id)
    token.auth_method = method; token.source_key_id = key.id if key else None
    token.credential_version = key.version if key else parent.credential_version if parent else None
    runtime.current_token_id = token.id
    db.flush()
    return token, runtime
