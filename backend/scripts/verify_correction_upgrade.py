"""Check restored pre-correction records through 0011, without printing row values.

The target must already be restored to 0009 in an isolated local intent_*_test
database. Existing records are preserved. No source database is modified.
"""
import json
import os
import re
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from intent_platform.config import settings


def main():
    cfg = settings()
    owner_url, app_url = make_url(cfg.migration_database_url), make_url(cfg.database_url)
    admin_url = make_url(os.environ["INTENT_LOCAL_ADMIN_DATABASE_URL"])
    if not re.fullmatch(r"intent_[a-z0-9_]+_test", owner_url.database or ""):
        raise RuntimeError("Use an isolated intent_*_test restoration")
    if any((url.host, url.port, url.database) != (owner_url.host, owner_url.port, owner_url.database)
           for url in (app_url, admin_url)) or owner_url.host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("All roles must use the same local restoration")
    if app_url.username != "intent_app" or owner_url.username != "intent_owner":
        raise RuntimeError("Use the restricted runtime and separate migration owner")
    admin, owner, app = map(create_engine, (admin_url, owner_url, app_url))
    with admin.connect() as db:
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0009"
        tables = db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")).scalars().all()
        if any(not re.fullmatch(r"[a-z_][a-z_0-9]*", name) for name in tables):
            raise RuntimeError("Unexpected table identifier")
        before = {name: db.execute(text(f'SELECT to_jsonb(t) FROM "{name}" t')).scalars().all()
                  for name in tables if name != "alembic_version"}
        assert before.get("agent_releases"), "Restore must include historical published snapshots"
        flags = db.execute(text("SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c "
                                "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
                                "AND c.relkind='r' ORDER BY c.relname")).all()
    alembic = Config()
    alembic.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "src/intent_platform/migrations"))
    with owner.begin() as db:
        alembic.attributes["connection"] = db
        command.upgrade(alembic, "0011")
    with admin.connect() as db:
        for name, expected in before.items():
            actual = db.execute(text(f'SELECT to_jsonb(t) FROM "{name}" t')).scalars().all()
            if name == "agents":
                assert all(row.pop("routing_strategy") == "legacy" for row in actual)
            if name == "workspaces":
                assert all(row.pop("created_by") is None for row in actual)
            canonical = lambda rows: sorted(json.dumps(row, sort_keys=True) for row in rows)
            assert canonical(actual) == canonical(expected), f"Historical rows changed in {name}"
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0011"
        assert flags == db.execute(text("SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c "
                                      "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
                                      "AND c.relkind='r' ORDER BY c.relname")).all()
        assert db.execute(text("SELECT cmd FROM pg_policies WHERE tablename='audit' "
                               "AND policyname='workspace_creation_audit'")).scalar_one() == "INSERT"
    with app.begin() as db:
        assert db.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")).scalar_one() is False
        db.execute(text("SELECT set_config('intent.user_id','int019-unrelated-upgrade-user',true)"))
        assert db.execute(text("SELECT count(*) FROM agent_releases")).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM audit")).scalar_one() == 0
    print(json.dumps({"status": "passed", "database": owner_url.database, "migration": "0009->0010->0011",
                      "preserved_tables": len(before), "preserved_rows": sum(map(len, before.values())),
                      "preserved_releases": len(before["agent_releases"]), "default_strategy": "legacy",
                      "restricted_role_isolation": True, "historical_values_unchanged": True,
                      "acceptance_evidence": False}))


if __name__ == "__main__":
    main()
