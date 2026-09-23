"""One real 0001 -> 0002 upgrade in a dedicated initially empty PostgreSQL test DB.

Run from backend: python scripts/verify_upgrade.py
Requires an owner/app provisioned database named intent_upgrade_test. Refuses existing schema.
The fixture is preserved after success for inspection; no database or table is deleted.
"""
import os
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("INTENT_ENV", "test")
from sqlalchemy import create_engine, inspect, text, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from intent_platform.config import settings
from intent_platform.schemas import Definition
from intent_platform.ontology import compile_definition
from intent_platform.db import set_identity
from intent_platform.models import Agent, Function, Release, RuntimeSession, Conversation
from intent_platform.auth import issue_token
from intent_platform.runtime_sessions import scoped_session


def main():
    cfg = settings()
    target = os.environ.get("INTENT_UPGRADE_TEST_DATABASE", "intent_upgrade_test")
    if not target.endswith("_test"): raise RuntimeError("Dedicated upgrade database must end in _test")
    owner_url = make_url(cfg.migration_database_url).set(database=target)
    app_url = make_url(cfg.database_url).set(database=target)
    if (owner_url.host, owner_url.port) != (app_url.host, app_url.port):
        raise RuntimeError("Owner and app must use the same dedicated upgrade test server")
    owner = create_engine(owner_url); app = create_engine(app_url)
    with owner.connect() as conn:
        if inspect(conn).has_table("alembic_version") or inspect(conn).has_table("agents"):
            raise RuntimeError("Upgrade test requires an empty intent_upgrade_test; existing evidence is preserved")
    alembic = Config(); alembic.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "src/intent_platform/migrations"))
    definition = Definition(name="upgrade fixture", description="Local migration fixture", allowed_users=["upgrade-user"]).model_dump()
    snapshot = {"schema_version": "1", "capabilities": [{"capability_id": "upgrade-function", "version": 1, "definition": definition, "compiled": compile_definition(definition)}],
                "model_profile": {"mode": cfg.model_mode, "name": cfg.model_name, "url": cfg.model_url, "temperature": 0},
                "prompt_policy_version": "router-v1", "ontology_compiler_version": "finite-json-v1", "ui_schema_version": "1", "connector_versions": {"upgrade-function": "1"},
                "runtime_ui": {"name": "old published title", "suggested_questions": []}, "allowed_origins": ["http://localhost:5173"], "fixture_mode": True}
    with owner.begin() as conn:
        alembic.attributes["connection"] = conn; command.upgrade(alembic, "0001")
        conn.execute(text("INSERT INTO workspaces VALUES ('upgrade-ws','Upgrade fixture')"))
        conn.execute(text("INSERT INTO memberships VALUES ('upgrade-user','upgrade-ws', '[\"admin\"]',true)"))
        conn.execute(text("INSERT INTO functions(id,workspace_id,definition,document_text,revision,status,latest_version,disabled) VALUES ('upgrade-function','upgrade-ws',CAST(:d AS jsonb),'',1,'published',1,false)"), {"d": json.dumps(definition)})
        conn.execute(text("INSERT INTO agents(id,workspace_id,name,capability_ids,allowed_origins,suggested_questions,current_release_id,revision,revoked_origins,allowed_users) VALUES ('upgrade-agent','upgrade-ws','draft old','[\"upgrade-function\"]','[\"http://localhost:5173\"]','[]','upgrade-release',1,'[]','[\"upgrade-user\"]')"))
        conn.execute(text("INSERT INTO agent_releases(id,workspace_id,agent_id,version,snapshot,created_at) VALUES ('upgrade-release','upgrade-ws','upgrade-agent',1,CAST(:s AS jsonb),now())"), {"s": json.dumps(snapshot)})
        for identifier, scope, revoked, expired, parent in [
            ("old-parent", "management", False, False, None), ("revoked-parent", "management", True, False, None),
            ("expired-parent", "management", False, True, None), ("old-active", "runtime", False, False, "old-parent"),
            ("old-expired", "runtime", False, True, "expired-parent"), ("old-parent-revoked", "runtime", False, False, "revoked-parent"),
            ("old-child-revoked", "runtime", True, False, "old-parent"), ("old-conv-revoked", "runtime", False, False, "old-parent")]:
            conn.execute(text("INSERT INTO auth_tokens(id,user_id,scope,agent_id,expires_at,revoked,source,delegation,parent_token_id) VALUES (:id,'upgrade-user',:scope,:agent,now()+(:days * interval '1 day'),:revoked,'development',CAST(:delegation AS jsonb),:parent)"),
                         {"id": identifier, "scope": scope, "agent": "upgrade-agent" if scope == "runtime" else None, "days": -1 if expired else 1,
                          "revoked": revoked, "parent": parent, "delegation": json.dumps({"host_origin": "http://localhost:5173"} if scope == "runtime" else {})})
        for token in ("old-active", "old-expired", "old-parent-revoked", "old-child-revoked", "old-conv-revoked", "expired-parent", "revoked-parent"):
            conn.execute(text("INSERT INTO conversations(id,workspace_id,agent_id,release_id,user_id,token_id,sequence,status) VALUES (:id,'upgrade-ws','upgrade-agent','upgrade-release','upgrade-user',:token,0,:status)"),
                         {"id": "conv-" + token, "token": token, "status": "revoked" if token == "old-conv-revoked" else "active"})
        before = {table: {row["id"]: row for row in conn.execute(text(f"SELECT to_jsonb(t) FROM {table} t")).scalars()} for table in ("agents", "functions", "agent_releases", "auth_tokens", "conversations")}
        command.upgrade(alembic, "0002")
        for table, expected in before.items():
            for row in conn.execute(text(f"SELECT to_jsonb(t) FROM {table} t")).scalars():
                assert {key: row[key] for key in expected[row["id"]]} == expected[row["id"]], table
        saved = conn.execute(text("SELECT snapshot FROM agent_releases WHERE id='upgrade-release'")).scalar()
        assert saved == snapshot
        assert conn.execute(text("SELECT access_mode FROM functions WHERE id='upgrade-function'")).scalar() == "allowlist"
        # Original migration evidence above is distinct from current-runtime compatibility.
        command.upgrade(alembic, "head")
    # Application-role validation after owner migration commits; no owner/RLS bypass here.
    with Session(app) as db, db.begin():
        assert db.execute(text("SELECT current_user")).scalar() == "intent_app"
        assert db.execute(text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname=current_user")).scalar() is False
        assert not list(db.scalars(select(Agent)))
        set_identity(db, "upgrade-user")
        a = db.get(Agent, "upgrade-agent")
        # Read only this historical migration's columns, not future Function ORM fields.
        f = db.execute(text("SELECT access_mode, allowed_users FROM functions WHERE id='upgrade-function'")).mappings().one()
        assert a.access_mode == f["access_mode"] == "allowlist" and a.allowed_users == f["allowed_users"] == ["upgrade-user"]
        assert db.get(Release, "upgrade-release").snapshot == snapshot
        parent = issue_token(db, "upgrade-user")
        for token_id, revoked in [("old-active", False), ("old-expired", False), ("old-parent-revoked", True), ("old-child-revoked", True), ("old-conv-revoked", True)]:
            conv = db.get(Conversation, "conv-" + token_id)
            runtime = db.get(RuntimeSession, conv.runtime_session_id)
            assert runtime.revoked is revoked
            try:
                scoped_session(db, parent, a, "embed", runtime.id, "http://localhost:5173", "renewal-fixture")
                assert not revoked
            except HTTPException as exc:
                assert revoked and exc.status_code == 403
            assert conv.release_id == "upgrade-release"
        legacy = db.get(Conversation, "conv-expired-parent")
        renewed = scoped_session(db, parent, a, "chat")
        assert renewed.runtime_session_id == legacy.runtime_session_id
        assert db.get(Conversation, "conv-revoked-parent").runtime_session_id is None
    print("PASS: real PostgreSQL 0001->0002 preservation then head runtime compatibility; restricted app, ACL preservation, immutable snapshot, active/expired renewal, parent/child/conversation revocation, legacy management history")


if __name__ == "__main__":
    main()
