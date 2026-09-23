"""Preserve real 0002 data through 0003 in an initially empty dedicated test DB.

Never clears data and refuses a pre-existing schema. Keep the resulting evidence.
"""
import os
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("INTENT_ENV", "test")
from sqlalchemy import create_engine, inspect, text, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from alembic import command
from alembic.config import Config
from intent_platform.config import settings
from intent_platform.db import set_identity
from intent_platform.models import Workspace, Membership, Function, Agent, Release, Conversation, Run, Call, DomainPackage, DomainVersion
from intent_platform.auth import issue_token
from intent_platform.schemas import Definition
from intent_platform.ontology import compile_definition
from intent_platform.policies import dependency_error
from intent_platform.domains import compile_domain


def main():
    cfg = settings()
    target = os.environ.get("INTENT_UPGRADE_TEST_DATABASE", "intent_domain_upgrade_test")
    if not target.endswith("_test"): raise RuntimeError("Dedicated upgrade database must end in _test")
    owner_url = make_url(cfg.migration_database_url).set(database=target)
    app_url = make_url(cfg.database_url).set(database=target)
    if (owner_url.host, owner_url.port) != (app_url.host, app_url.port):
        raise RuntimeError("Both upgrade URLs must identify the same dedicated test database")
    owner, app = create_engine(owner_url), create_engine(app_url)
    with owner.connect() as conn:
        if inspect(conn).has_table("alembic_version") or inspect(conn).has_table("workspaces"):
            raise RuntimeError("Existing intent_domain_upgrade_test evidence is preserved; provision a fresh empty target")
    alembic = Config(); alembic.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "src/intent_platform/migrations"))
    with owner.begin() as conn:
        alembic.attributes["connection"] = conn; command.upgrade(alembic, "0002")
    definition = Definition(name="Domain upgrade fixture", description="Synthetic migration evidence").model_dump()
    snapshot = {"capabilities": [{"capability_id": "domain-upgrade-fn", "version": 1, "definition": definition, "compiled": compile_definition(definition)}],
                "model_profile": {"mode": cfg.model_mode, "name": cfg.model_name, "url": cfg.model_url, "temperature": 0},
                "prompt_policy_version": "router-v1", "ontology_compiler_version": "finite-json-v1", "ui_schema_version": "1",
                "connector_versions": {"domain-upgrade-fn": "1"}, "fixture_mode": True}
    with Session(owner) as db, db.begin():
        db.add(Workspace(id="domain-upgrade-ws", name="Synthetic upgrade")); db.flush()
        db.execute(text("INSERT INTO memberships(user_id,workspace_id,roles,active) VALUES ('domain-upgrade-user','domain-upgrade-ws','[\"admin\"]',true)"))
        # Seed the frozen 0002 schema without future Function ORM columns.
        db.execute(text("INSERT INTO functions(id,workspace_id,definition,document_text,revision,status,latest_version,disabled,access_mode,allowed_users) VALUES ('domain-upgrade-fn','domain-upgrade-ws',CAST(:definition AS jsonb),'',1,'published',1,false,'workspace','[]')"), {"definition": json.dumps(definition)})
        db.execute(text("INSERT INTO agents(id,workspace_id,name,capability_ids,allowed_origins,suggested_questions,current_release_id,revision,revoked_origins,allowed_users) VALUES ('domain-upgrade-agent','domain-upgrade-ws','Old fixture','[\"domain-upgrade-fn\"]','[]','[]','domain-upgrade-release',1,'[]','[]')"))
        db.add(Release(id="domain-upgrade-release", workspace_id="domain-upgrade-ws", agent_id="domain-upgrade-agent", version=1, snapshot=snapshot)); db.flush()
        token = issue_token(db, "domain-upgrade-user")
        db.add(Conversation(id="domain-upgrade-conv", workspace_id="domain-upgrade-ws", agent_id="domain-upgrade-agent", release_id="domain-upgrade-release", user_id=token.user_id, token_id=token.id)); db.flush()
        db.execute(text("INSERT INTO runs(id,workspace_id,conversation_id,user_id,client_message_id,text,revision,status,decision,answers,confirmed,created_at) VALUES ('domain-upgrade-run','domain-upgrade-ws','domain-upgrade-conv','domain-upgrade-user','retained-message','retained text',7,'UNKNOWN','READY','{}',true,now())"))
        db.execute(text("INSERT INTO calls(id,workspace_id,run_id,action_id,revision,capability_id,version,definition,arguments,status,result,operation_id,dispatched_at) VALUES ('domain-upgrade-call','domain-upgrade-ws','domain-upgrade-run','action_0',7,'domain-upgrade-fn',1,CAST(:definition AS jsonb),'{}','UNKNOWN','{}','retained-operation',now())"), {"definition": json.dumps(definition)})
    with owner.begin() as conn:
        before = {table: conn.execute(text(f"SELECT to_jsonb(t) FROM {table} t")).scalars().all() for table in ("agent_releases", "conversations", "runs", "calls")}
        alembic.attributes["connection"] = conn; command.upgrade(alembic, "0003")
        for table, expected in before.items():
            after = conn.execute(text(f"SELECT to_jsonb(t) FROM {table} t")).scalars().all()
            for row in after:
                if table in {"runs", "calls"}: assert row.pop("bindings") == {}
                if table == "runs": assert row.pop("validation_evidence") is None
            assert after == expected, table
        command.upgrade(alembic, "head")
    domain = {"name": "Upgrade domain", "description": "Synthetic only", "source": {"reference": "upgrade-test", "owner": "local"}, "concepts": [{"id": "upgrade:Object", "label": "Object", "kind": "object", "parents": []}]}
    with Session(app) as db, db.begin():
        assert db.execute(text("SELECT current_user")).scalar() == "intent_app"
        assert db.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")).scalar() is False
        assert not list(db.scalars(select(Run)))
        set_identity(db, "domain-upgrade-user")
        assert db.get(Release, "domain-upgrade-release").snapshot == snapshot and dependency_error(snapshot) is None
        run, call = db.get(Run, "domain-upgrade-run"), db.get(Call, "domain-upgrade-call")
        assert run.revision == 7 and run.status == call.status == "UNKNOWN" and call.operation_id == "retained-operation"
        assert run.bindings == call.bindings == {} and run.validation_evidence is None
        package = DomainPackage(workspace_id="domain-upgrade-ws", key="synthetic", definition=domain); db.add(package); db.flush()
        version = DomainVersion(workspace_id=package.workspace_id, package_id=package.id, version=1, definition=domain, compiled=compile_domain(domain), published_by="domain-upgrade-user")
        db.add(version); db.flush(); version_id = version.id
        try:
            with db.begin_nested():
                db.execute(text("UPDATE domain_versions SET version=2 WHERE id=:id"), {"id": version_id})
            raise AssertionError("Immutable domain accepted an update")
        except DBAPIError:
            pass
    with Session(app) as db:
        set_identity(db, "unrelated-user")
        assert not list(db.scalars(select(DomainVersion)))
    print("PASS: PostgreSQL 0002->0003 preservation then head runtime compatibility; unchanged old release/conversation/run/call values, v1 dependencies, UNKNOWN/IDs/revision, restricted app RLS and immutable domains")


if __name__ == "__main__":
    main()
