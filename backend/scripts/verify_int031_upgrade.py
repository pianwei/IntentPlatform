"""Verify the real 0017 -> 0018 upgrade on a disposable restored database.

Only catalog counts and schema facts are inspected. No row values or credentials
are written to the report.
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


EXPECTED_DATABASE = "intent_int031_upgrade_test"


def require_same_disposable_database(urls):
    parsed = [make_url(url) for url in urls]
    first = parsed[0]
    if first.database != EXPECTED_DATABASE:
        raise RuntimeError("Refusing to inspect a non-disposable INT-031 upgrade database")
    if any((item.host, item.port, item.database) != (first.host, first.port, first.database) for item in parsed):
        raise RuntimeError("All database roles must target the same disposable restoration")
    if parsed[2].username != "intent_test_admin":
        raise RuntimeError("The inspection role must be the isolated test administrator")
    if not first.host or not re.fullmatch(r"intent-int031-upgrade-[a-z0-9-]+-pg", first.host):
        raise RuntimeError("The database host must be this run's isolated test container")
    return parsed


def table_snapshots(conn):
    names = conn.execute(text("""
        SELECT tablename FROM pg_tables
        WHERE schemaname='public' AND tablename <> 'alembic_version'
        ORDER BY tablename
    """)).scalars().all()
    snapshots = {}
    for name in names:
        if not re.fullmatch(r"[a-z_][a-z_0-9]*", name):
            raise RuntimeError("Unexpected table identifier in restored database")
        rows = conn.execute(text(f'SELECT to_jsonb(t) FROM public."{name}" t')).scalars().all()
        # Keep exact snapshots in memory only; never write business row values
        # to logs or evidence files.
        snapshots[name] = sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)
    return snapshots


def main():
    cfg = settings()
    urls = [cfg.database_url, cfg.migration_database_url, os.environ["INTENT_LOCAL_ADMIN_DATABASE_URL"]]
    parsed = require_same_disposable_database(urls)
    app_url, owner_url, admin_url = parsed
    if app_url.username != "intent_app" or owner_url.username != "intent_owner":
        raise RuntimeError("Use the restricted runtime role and migration owner")

    admin, owner, app = map(create_engine, urls[::-1])
    try:
        with admin.begin() as db:
            before_revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            if before_revision != "0017":
                raise RuntimeError(f"Expected restored revision 0017, got {before_revision}")
            before_rows = table_snapshots(db)
            if "agent_releases" not in before_rows:
                raise RuntimeError("Restored snapshot lacks agent release history")
            release_count = len(before_rows["agent_releases"])
            old_tables = set(before_rows)
            rls_before = db.execute(text("""
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relkind IN ('r','p')
                ORDER BY c.relname
            """)).all()

        migration = Config()
        migration.set_main_option(
            "script_location",
            str(Path(__file__).resolve().parents[1] / "src/intent_platform/migrations"),
        )
        with owner.begin() as connection:
            migration.attributes["connection"] = connection
            command.upgrade(migration, "head")

        with admin.begin() as db:
            after_revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            if after_revision != "0018":
                raise RuntimeError(f"Expected revision 0018 after upgrade, got {after_revision}")
            after_rows = table_snapshots(db)
            for table in old_tables:
                if after_rows.get(table) != before_rows[table]:
                    raise RuntimeError(f"Existing rows changed during upgrade: {table}")
            defaults = db.execute(text("""
                SELECT kind, count(*) FROM business_catalog_items
                WHERE workspace_id IS NULL AND origin='platform'
                GROUP BY kind ORDER BY kind
            """)).all()
            expected_defaults = [("action", 6), ("object", 7)]
            if defaults != expected_defaults:
                raise RuntimeError(f"Unexpected platform default catalog totals: {defaults}")
            versions = db.execute(text("SELECT count(*) FROM business_catalog_versions")).scalar_one()
            if versions < 13:
                raise RuntimeError("Platform defaults do not have immutable version records")
            rls_after = db.execute(text("""
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relname IN ('business_catalog_items','business_catalog_versions')
                ORDER BY c.relname
            """)).all()
            if len(rls_after) != 2 or any(not row.relrowsecurity for row in rls_after):
                raise RuntimeError("Catalog tables are missing row-level security")
            if db.execute(text("SELECT count(*) FROM agent_releases")).scalar_one() != release_count:
                raise RuntimeError("Published release history was not preserved")
            # Keep before/after RLS facts in the report without exposing row data.
            rls_before_count = len(rls_before)

        with app.begin() as db:
            if db.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")).scalar_one():
                raise RuntimeError("Runtime role unexpectedly bypasses row security")
            db.execute(text("SELECT set_config('intent.user_id', 'int031-upgrade-unrelated-user', true)"))
            foreign = db.execute(text("SELECT count(*) FROM business_catalog_items WHERE workspace_id IS NOT NULL")).scalar_one()
            visible_defaults = db.execute(text("SELECT count(*) FROM business_catalog_items WHERE workspace_id IS NULL")).scalar_one()
            if foreign != 0 or visible_defaults != 13:
                raise RuntimeError("Runtime role did not preserve workspace isolation for the catalog")

        print(json.dumps({
            "status": "passed",
            "database": EXPECTED_DATABASE,
            "migration": "0017->0018",
            "existing_tables_preserved": len(old_tables),
            "existing_rows_preserved": sum(map(len, before_rows.values())),
            "published_releases_preserved": release_count,
            "preexisting_rls_tables": rls_before_count,
            "platform_defaults": {kind: count for kind, count in defaults},
            "runtime_workspace_isolation": True,
            "acceptance_evidence": False,
        }, ensure_ascii=False))
    finally:
        admin.dispose()
        owner.dispose()
        app.dispose()


if __name__ == "__main__":
    main()
