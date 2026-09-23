"""Non-destructive real PostgreSQL 0003 -> 0004 source upgrade evidence.

Requires an initially empty intent_import_upgrade_test. Refuses existing evidence.
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
from intent_platform.models import Workspace, Membership, Agent, Release, Conversation, Run, Call, DocumentVersion, OpenAPIImport
from intent_platform.auth import issue_token
from intent_platform.schemas import Definition
from intent_platform.ontology import compile_definition
from intent_platform.policies import dependency_error


def main():
    cfg = settings()
    target = os.environ.get("INTENT_UPGRADE_TEST_DATABASE", "intent_import_upgrade_test")
    if not target.endswith("_test"): raise RuntimeError("Dedicated upgrade database must end in _test")
    owner_url = make_url(cfg.migration_database_url).set(database=target)
    app_url = make_url(cfg.database_url).set(database=target)
    if (owner_url.host, owner_url.port) != (app_url.host, app_url.port):
        raise RuntimeError("Both URLs must identify the same dedicated upgrade test server")
    owner, app = create_engine(owner_url), create_engine(app_url)
    with owner.connect() as connection:
        if inspect(connection).has_table("alembic_version") or inspect(connection).has_table("workspaces"):
            raise RuntimeError("Existing intent_import_upgrade_test evidence is preserved; provision a fresh empty database")
    alembic = Config(); alembic.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "src/intent_platform/migrations"))
    with owner.begin() as connection:
        alembic.attributes["connection"] = connection; command.upgrade(alembic, "0003")
    definition = Definition(name="Import upgrade fixture", description="Synthetic legacy fixture", document_refs=[{"document_id": "import-upgrade-document", "revision": 1}]).model_dump()
    snapshot = {"capabilities": [{"capability_id": "import-upgrade-fn", "version": 1, "definition": definition, "compiled": compile_definition(definition)}],
                "model_profile": {"mode": cfg.model_mode, "name": cfg.model_name, "url": cfg.model_url, "temperature": 0},
                "prompt_policy_version": "router-v1", "ontology_compiler_version": "finite-json-v1", "ui_schema_version": "1",
                "connector_versions": {"import-upgrade-fn": "1"}, "fixture_mode": True}
    with Session(owner) as db, db.begin():
        db.add(Workspace(id="import-upgrade-ws", name="Synthetic upgrade")); db.flush()
        db.execute(text("INSERT INTO memberships(user_id,workspace_id,roles,active) VALUES ('import-upgrade-user','import-upgrade-ws','[\"admin\"]',true)"))
        db.execute(text("INSERT INTO documents(id,workspace_id,filename,text) VALUES ('import-upgrade-document','import-upgrade-ws','legacy.txt','Legacy retained text without original bytes')"))
        db.execute(text("INSERT INTO functions(id,workspace_id,definition,document_text,revision,status,latest_version,disabled,access_mode,allowed_users) VALUES ('import-upgrade-fn','import-upgrade-ws',CAST(:definition AS jsonb),'',1,'published',1,false,'workspace','[]')"), {"definition": json.dumps(definition)})
        db.execute(text("INSERT INTO function_versions(id,workspace_id,function_id,version,definition,compiled,created_at) VALUES ('import-upgrade-fv','import-upgrade-ws','import-upgrade-fn',1,CAST(:definition AS jsonb),CAST(:compiled AS jsonb),now())"), {"definition": json.dumps(definition), "compiled": json.dumps(compile_definition(definition))})
        db.execute(text("INSERT INTO agents(id,workspace_id,name,capability_ids,allowed_origins,suggested_questions,current_release_id,revision,revoked_origins,allowed_users) VALUES ('import-upgrade-agent','import-upgrade-ws','Old fixture','[\"import-upgrade-fn\"]','[]','[]','import-upgrade-release',1,'[]','[]')"))
        db.add(Release(id="import-upgrade-release", workspace_id="import-upgrade-ws", agent_id="import-upgrade-agent", version=1, snapshot=snapshot)); db.flush()
        token = issue_token(db, "import-upgrade-user")
        db.add(Conversation(id="import-upgrade-conv", workspace_id="import-upgrade-ws", agent_id="import-upgrade-agent", release_id="import-upgrade-release", user_id=token.user_id, token_id=token.id)); db.flush()
        db.execute(text("INSERT INTO runs(id,workspace_id,conversation_id,user_id,client_message_id,text,revision,status,decision,answers,confirmed,created_at) VALUES ('import-upgrade-run','import-upgrade-ws','import-upgrade-conv','import-upgrade-user','retained-message','Retained input',7,'UNKNOWN','READY','{}',false,now())"))
        db.add(Call(id="import-upgrade-call", workspace_id="import-upgrade-ws", run_id="import-upgrade-run", action_id="action_0", revision=7, capability_id="import-upgrade-fn", version=1, definition=definition, arguments={}, status="UNKNOWN", operation_id="retained-operation"))
    tables = ("documents", "functions", "function_versions", "agent_releases", "conversations", "runs", "calls")
    with owner.begin() as connection:
        before = {table: connection.execute(text(f"SELECT to_jsonb(t) FROM {table} t")).scalars().all() for table in tables}
        alembic.attributes["connection"] = connection; command.upgrade(alembic, "0004")
        for table, expected in before.items():
            actual = connection.execute(text(f"SELECT to_jsonb(t) FROM {table} t")).scalars().all()
            for row in actual:
                if table == "documents": assert row.pop("latest_revision") == 1
                if table == "functions": assert row.pop("source_state") == {}
            assert actual == expected, table
        command.upgrade(alembic, "head")
    with Session(app) as db, db.begin():
        assert db.execute(text("SELECT current_user")).scalar() == "intent_app"
        assert db.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")).scalar() is False
        assert not list(db.scalars(select(DocumentVersion)))
        set_identity(db, "import-upgrade-user")
        assert db.get(Release, "import-upgrade-release").snapshot == snapshot and dependency_error(snapshot) is None
        document = db.scalar(select(DocumentVersion))
        assert document.revision == 1 and document.original is None and document.text == "Legacy retained text without original bytes"
        assert document.sections[0]["id"] == "legacy-text" and document.created_by == "legacy-unknown"
        run, call = db.get(Run, "import-upgrade-run"), db.get(Call, "import-upgrade-call")
        assert run.revision == call.revision == 7 and run.status == call.status == "UNKNOWN" and call.operation_id == "retained-operation"
        imported = OpenAPIImport(workspace_id="import-upgrade-ws", document_id=document.document_id, document_revision=1, report={"valid": False, "diagnostics": [], "operations": []}, created_by="import-upgrade-user"); db.add(imported); db.flush()
        for table, identifier in (("document_versions", document.id), ("openapi_imports", imported.id)):
            try:
                with db.begin_nested(): db.execute(text(f"DELETE FROM {table} WHERE id=:id"), {"id": identifier})
                raise AssertionError("Source immutability was not enforced")
            except DBAPIError: pass
    with Session(app) as db:
        set_identity(db, "unrelated-user")
        assert not list(db.scalars(select(DocumentVersion))) and not list(db.scalars(select(OpenAPIImport)))
    print("PASS: PostgreSQL 0003->0004 preservation then head runtime compatibility; old source/function/release/conversation/run/call values preserved; legacy original absent; UNKNOWN/operation/revision retained; restricted source RLS and immutability")


if __name__ == "__main__": main()
