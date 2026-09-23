"""Preserve 0005 source records through 0006 in a fresh local test database.

Run from backend after provisioning an empty intent_*_test database for the
restricted owner/app roles. The database and its synthetic evidence remain.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("INTENT_ENV", "test")

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from intent_platform.config import settings
from intent_platform.db import set_identity
from intent_platform.models import IngestJob


def main():
    cfg = settings()
    target = os.environ.get("INTENT_UPGRADE_TEST_DATABASE", "intent_ingest_upgrade_test")
    if not target.startswith("intent_") or not target.endswith("_test"):
        raise RuntimeError("Only a dedicated intent_*_test database is allowed")
    owner_url = make_url(cfg.migration_database_url).set(database=target)
    app_url = make_url(cfg.database_url).set(database=target)
    if (owner_url.host, owner_url.port, owner_url.database) != (app_url.host, app_url.port, app_url.database):
        raise RuntimeError("Owner and app must use the same dedicated test database")
    owner, app = create_engine(owner_url), create_engine(app_url)
    with owner.connect() as db:
        if inspect(db).has_table("alembic_version") or inspect(db).has_table("workspaces"):
            raise RuntimeError("Existing upgrade evidence is preserved; provision an empty database")

    alembic = Config()
    alembic.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "src/intent_platform/migrations"))
    with owner.begin() as db:
        alembic.attributes["connection"] = db
        command.upgrade(alembic, "0005")

    metadata = MetaData()
    with owner.begin() as db:
        def insert(table_name, values):
            table = Table(table_name, metadata, autoload_with=db, extend_existing=True)
            db.execute(table.insert().values(**values))

        insert("workspaces", {"id": "ingest-upgrade-ws", "name": "Synthetic intake upgrade"})
        insert("memberships", {"user_id": "ingest-upgrade-user", "workspace_id": "ingest-upgrade-ws",
                               "roles": ["admin", "maintainer"], "active": True,
                               "revision": 1, "role_ceiling": ["admin", "maintainer"]})
        # 0005 already forces RLS on governance records, including for owner.
        set_identity(db, "ingest-upgrade-user")
        insert("documents", {"id": "ingest-upgrade-doc", "workspace_id": "ingest-upgrade-ws",
                             "filename": "legacy.md", "text": "# Legacy source\nSynthetic only", "latest_revision": 1})
        insert("document_versions", {"id": "ingest-upgrade-version", "workspace_id": "ingest-upgrade-ws",
                                     "document_id": "ingest-upgrade-doc", "revision": 1,
                                     "filename": "legacy.md", "text": "# Legacy source\nSynthetic only",
                                     "original": b"# Legacy source\nSynthetic only",
                                     "sections": [{"id": "legacy", "heading": "Legacy source", "start_line": 1,
                                                   "end_line": 2, "text": "# Legacy source\nSynthetic only"}],
                                     "created_by": "ingest-upgrade-user"})
        insert("openapi_imports", {"id": "ingest-upgrade-import", "workspace_id": "ingest-upgrade-ws",
                                   "document_id": "ingest-upgrade-doc", "document_revision": 1,
                                   "parser_version": "synthetic-legacy", "report": {"valid": False, "operations": []},
                                   "created_by": "ingest-upgrade-user"})
        insert("workspace_governance", {"workspace_id": "ingest-upgrade-ws", "revision": 1,
                                        "limits": {"max_queued_runs": 2, "max_routing": 1, "max_execution": 1}})
        tables = ("workspaces", "memberships", "documents", "document_versions",
                  "openapi_imports", "workspace_governance")
        before = {name: db.execute(text(f"SELECT to_jsonb(t) FROM {name} t")).scalars().all()
                  for name in tables}
        alembic.attributes["connection"] = db
        command.upgrade(alembic, "0006")
        for name, expected in before.items():
            actual = db.execute(text(f"SELECT to_jsonb(t) FROM {name} t")).scalars().all()
            assert actual == expected, name
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0006"
        assert db.execute(text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname='ingest_jobs'")).scalar() is True

    with Session(app) as db, db.begin():
        assert db.execute(text("SELECT current_user")).scalar() == "intent_app"
        assert db.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")).scalar() is False
        assert db.get(IngestJob, "ingest-upgrade-job") is None
        set_identity(db, "ingest-upgrade-user")
        assert db.execute(text("SELECT text FROM document_versions WHERE id='ingest-upgrade-version'")).scalar() == "# Legacy source\nSynthetic only"
        db.add(IngestJob(id="ingest-upgrade-job", workspace_id="ingest-upgrade-ws",
                         created_by="ingest-upgrade-user", token_id="synthetic-legacy-token",
                         idempotency_key="synthetic-upgrade-key", kind="document.create",
                         status="PENDING", filename="new.md", payload=b"# New synthetic source"))
        db.flush()
        assert db.get(IngestJob, "ingest-upgrade-job").status == "PENDING"
    with Session(app) as db:
        set_identity(db, "unrelated-user")
        assert db.get(IngestJob, "ingest-upgrade-job") is None
    with Session(owner) as db:
        assert db.get(IngestJob, "ingest-upgrade-job") is None
    print(json.dumps({"status": "passed", "migration": "0005->0006", "preserved_tables": len(tables),
                      "restricted_role": True, "ingest_force_rls": True, "other_space_denied": True,
                      "database": target, "source": "synthetic", "acceptance_evidence": False}))


if __name__ == "__main__":
    main()
