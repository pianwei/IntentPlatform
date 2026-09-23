"""Preserve 0004 records through 0005 in an initially empty dedicated test database."""
import os
import json
import sys
from pathlib import Path
from datetime import timedelta
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("INTENT_ENV", "test")
from sqlalchemy import create_engine, inspect, text, select, MetaData, Table
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from intent_platform.config import settings
from intent_platform.db import now, set_identity
from intent_platform.schemas import Definition
from intent_platform.ontology import compile_definition
from intent_platform.models import Agent, Release, Membership, RuntimeSession, Run, Call, WorkspaceGovernance, ProcessingAttempt, AuthToken
from intent_platform.auth import issue_token, token_valid
from intent_platform.runtime_sessions import scoped_session
from intent_platform.policies import dependency_error


def main():
    cfg = settings(); target = os.environ.get("INTENT_UPGRADE_TEST_DATABASE", "intent_governance_upgrade_test")
    owner_url = make_url(cfg.migration_database_url).set(database=target); app_url = make_url(cfg.database_url).set(database=target)
    if (owner_url.host, owner_url.port, owner_url.database) != (app_url.host, app_url.port, app_url.database) or not target.endswith("_test"):
        raise RuntimeError("Both URLs must identify the same dedicated upgrade test database")
    owner, app = create_engine(owner_url), create_engine(app_url)
    with owner.connect() as conn:
        if inspect(conn).has_table("alembic_version") or inspect(conn).has_table("workspaces"):
            raise RuntimeError("Existing governance upgrade evidence is preserved; provision an empty target")
    alembic = Config(); alembic.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "src/intent_platform/migrations"))
    definition = Definition(name="Governance upgrade fixture", description="Synthetic legacy v1 fixture").model_dump()
    snapshot = {"capabilities": [{"capability_id": "gov-upgrade-fn", "version": 1, "definition": definition, "compiled": compile_definition(definition)}],
                "model_profile": {"mode": cfg.model_mode, "name": cfg.model_name, "url": cfg.model_url, "temperature": 0},
                "prompt_policy_version": "router-v1", "ontology_compiler_version": "finite-json-v1", "ui_schema_version": "1",
                "connector_versions": {"gov-upgrade-fn": "1"}, "allowed_origins": ["http://localhost:5173"], "fixture_mode": True}
    stamp = now(); metadata = MetaData()
    with owner.begin() as conn:
        alembic.attributes["connection"] = conn; command.upgrade(alembic, "0004")
        # Reflection binds this fixture to the actual 0004 schema, not future ORM fields.
        def insert(name, values):
            table = Table(name, metadata, autoload_with=conn, extend_existing=True)
            conn.execute(table.insert().values(**values))
        insert("workspaces", {"id": "gov-upgrade-ws", "name": "Synthetic governance upgrade"})
        insert("memberships", {"workspace_id": "gov-upgrade-ws", "user_id": "gov-upgrade-user", "roles": ["admin", "user"], "active": True})
        insert("functions", {"id": "gov-upgrade-fn", "workspace_id": "gov-upgrade-ws", "definition": definition, "document_text": "", "revision": 1, "status": "published", "latest_version": 1, "disabled": False})
        insert("function_versions", {"id": "gov-upgrade-fv", "workspace_id": "gov-upgrade-ws", "function_id": "gov-upgrade-fn", "version": 1, "definition": definition, "compiled": compile_definition(definition), "created_at": stamp})
        insert("agents", {"id": "gov-upgrade-agent", "workspace_id": "gov-upgrade-ws", "name": "Old synthetic application", "capability_ids": ["gov-upgrade-fn"], "allowed_origins": ["http://localhost:5173"], "suggested_questions": [], "current_release_id": "gov-upgrade-release", "revision": 1, "revoked_origins": [], "allowed_users": []})
        insert("agent_releases", {"id": "gov-upgrade-release", "workspace_id": "gov-upgrade-ws", "agent_id": "gov-upgrade-agent", "version": 1, "snapshot": snapshot, "created_at": stamp})
        insert("auth_tokens", {"id": "gov-old-parent", "user_id": "gov-upgrade-user", "scope": "management", "expires_at": stamp + timedelta(days=1), "revoked": False, "source": "development", "delegation": {}})
        for suffix, revoked in (("active", False), ("revoked", True)):
            insert("runtime_sessions", {"id": "gov-session-" + suffix, "workspace_id": "gov-upgrade-ws", "agent_id": "gov-upgrade-agent", "user_id": "gov-upgrade-user", "channel": "chat", "revoked": revoked, "legacy_claimable": False, "current_token_id": "gov-token-" + suffix, "created_at": stamp})
            insert("auth_tokens", {"id": "gov-token-" + suffix, "user_id": "gov-upgrade-user", "scope": "runtime", "agent_id": "gov-upgrade-agent", "expires_at": stamp + timedelta(days=1), "revoked": False, "source": "development", "delegation": {}, "parent_token_id": "gov-old-parent", "runtime_session_id": "gov-session-" + suffix})
        insert("conversations", {"id": "gov-upgrade-conv", "workspace_id": "gov-upgrade-ws", "agent_id": "gov-upgrade-agent", "release_id": "gov-upgrade-release", "user_id": "gov-upgrade-user", "token_id": "gov-token-active", "runtime_session_id": "gov-session-active", "sequence": 0, "status": "active"})
        insert("runs", {"id": "gov-upgrade-run", "workspace_id": "gov-upgrade-ws", "conversation_id": "gov-upgrade-conv", "user_id": "gov-upgrade-user", "client_message_id": "retained-message", "text": "retained text", "revision": 7, "status": "UNKNOWN", "decision": "READY", "answers": {}, "confirmed": True, "created_at": stamp})
        insert("calls", {"id": "gov-upgrade-call", "workspace_id": "gov-upgrade-ws", "run_id": "gov-upgrade-run", "action_id": "action_0", "revision": 7, "capability_id": "gov-upgrade-fn", "version": 1, "definition": definition, "arguments": {}, "status": "UNKNOWN", "result": {}, "operation_id": "retained-operation", "dispatched_at": stamp})
        insert("embed_tickets", {"id": "gov-old-ticket", "user_id": "gov-upgrade-user", "agent_id": "gov-upgrade-agent", "workspace_id": "gov-upgrade-ws", "host_origin": "http://localhost:5173", "instance_nonce": "retained-nonce", "expires_at": stamp + timedelta(minutes=1), "consumed": True, "source_token_id": "gov-old-parent"})
        tables = ("memberships", "functions", "function_versions", "agents", "agent_releases", "runtime_sessions", "auth_tokens", "conversations", "runs", "calls", "embed_tickets")
        before = {name: conn.execute(text(f"SELECT to_jsonb(t) FROM {name} t ORDER BY to_jsonb(t)::text")).scalars().all() for name in tables}
        command.upgrade(alembic, "0005")
        for name, expected in before.items():
            rows = conn.execute(text(f"SELECT to_jsonb(t) FROM {name} t")).scalars().all()
            for row in rows:
                if name == "memberships": assert row.pop("revision") == 1 and row.pop("role_ceiling") == row["roles"]
                if name == "agents": assert row.pop("integration_refs") == row.pop("revoked_integration_refs") == []
                if name == "runtime_sessions": assert row.pop("integration_id") == "development" and row.pop("identity_policy") is None
                if name == "runs": assert row.pop("queued_at") == row["created_at"]
                if name == "embed_tickets": assert row.pop("integration_id") == "development"
            assert sorted(rows, key=lambda x: json.dumps(x, sort_keys=True)) == sorted(expected, key=lambda x: json.dumps(x, sort_keys=True)), name
    with Session(app) as db, db.begin():
        assert db.execute(text("SELECT current_user")).scalar() == "intent_app"
        assert db.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")).scalar() is False
        set_identity(db, "gov-upgrade-user")
        assert db.get(Release, "gov-upgrade-release").snapshot == snapshot and dependency_error(snapshot) is None
        assert token_valid(db, db.get(AuthToken, "gov-token-active"))
        parent = issue_token(db, "gov-upgrade-user")
        renewed = scoped_session(db, parent, db.get(Agent, "gov-upgrade-agent"), "chat", "gov-session-active")
        assert renewed.runtime_session_id == "gov-session-active"
        try:
            scoped_session(db, parent, db.get(Agent, "gov-upgrade-agent"), "chat", "gov-session-revoked")
            raise AssertionError("Revoked legacy session was renewed")
        except HTTPException as exc: assert exc.status_code == 403
        assert db.get(Run, "gov-upgrade-run").revision == 7 and db.get(Call, "gov-upgrade-call").status == "UNKNOWN"
        db.add(WorkspaceGovernance(workspace_id="gov-upgrade-ws", limits={"max_queued_runs": 1, "max_routing": 1, "max_execution": 1}))
        db.add(ProcessingAttempt(workspace_id="gov-upgrade-ws", run_id="gov-upgrade-run", revision=7, kind="reconciliation", queued_at=None, lease_until=now(), status="lease_expired", timings={}))
    for account in (app, owner):
        with Session(account) as db:
            assert not list(db.scalars(select(WorkspaceGovernance))) and not list(db.scalars(select(ProcessingAttempt)))
            assert db.execute(text("SELECT intent_queue_count('gov-upgrade-ws')")).scalar() == 0
    print("PASS: PostgreSQL 0004->0005; immutable snapshots and old record fields preserved; role ceilings and identity bindings backfilled; active old session renews, revoked session denied; UNKNOWN/operation/revision retained; new FORCE RLS holds for app and owner")


if __name__ == "__main__": main()
