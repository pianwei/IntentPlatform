"""Local credentials are distinct from workspace membership and visitor ownership."""
from datetime import datetime
from sqlalchemy import String, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base, uid, now


class LocalAccount(Base):
    __tablename__ = "local_accounts"
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True)
    display_name: Mapped[str]
    password_hash: Mapped[str | None]
    active: Mapped[bool] = mapped_column(default=True)
    must_change_password: Mapped[bool] = mapped_column(default=True)
    credential_version: Mapped[int] = mapped_column(default=1)
    revision: Mapped[int] = mapped_column(default=1)
    preferences: Mapped[dict] = mapped_column(JSONB, default=lambda: {"density": "comfortable"})
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LocalCredential(Base):
    __tablename__ = "local_credentials"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("cred"))
    user_id: Mapped[str] = mapped_column(index=True)
    kind: Mapped[str]
    secret_hash: Mapped[str]
    version: Mapped[int]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(default=False)
    token_id: Mapped[str | None]


class IntegrationKey(Base):
    __tablename__ = "integration_keys"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("key"))
    user_id: Mapped[str] = mapped_column(index=True)
    name: Mapped[str]
    secret_hash: Mapped[str]
    version: Mapped[int] = mapped_column(default=1)
    revoked: Mapped[bool] = mapped_column(default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AnonymousPrincipal(Base):
    __tablename__ = "anonymous_principals"
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(index=True)
    agent_id: Mapped[str]
    release_id: Mapped[str]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(default=False)


class IdentityAudit(Base):
    __tablename__ = "identity_audit"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uid("identity_audit"))
    actor_id: Mapped[str]
    subject_id: Mapped[str]
    action: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoginThrottle(Base):
    __tablename__ = "login_throttles"
    bucket: Mapped[str] = mapped_column(String, primary_key=True)
    failures: Mapped[int] = mapped_column(default=0)
    blocked_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
