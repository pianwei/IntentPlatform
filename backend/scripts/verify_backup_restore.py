"""Read-only local backup/restore comparison without exposing record values.

Set INTENT_LOCAL_ADMIN_DATABASE_URL to the local administrative PostgreSQL URL,
then pass distinct source/target intent_* database names. This never writes.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from intent_platform.config import settings


def snapshot(engine):
    with engine.connect() as db:
        tables = db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")).scalars().all()
        data = {}
        for table in tables:
            if not re.fullmatch(r"[a-z_][a-z_0-9]*", table):
                raise RuntimeError("Unexpected table identifier")
            data[table] = db.execute(text(f'SELECT to_jsonb(t)::text FROM "{table}" t ORDER BY to_jsonb(t)::text')).scalars().all()
        rls = db.execute(text("SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                              "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                              "WHERE n.nspname='public' AND c.relkind='r' ORDER BY c.relname")).all()
        policies = db.execute(text("SELECT tablename, policyname, permissive, roles::text, cmd, qual, with_check "
                                   "FROM pg_policies WHERE schemaname='public' ORDER BY tablename, policyname")).all()
        sequences = db.execute(text("SELECT sequencename, last_value FROM pg_sequences "
                                    "WHERE schemaname='public' ORDER BY sequencename")).all()
        return data, rls, policies, sequences


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    if args.source == args.target or any(name != "intent" and not re.fullmatch(r"intent_[a-z0-9_]+", name)
                                         for name in (args.source, args.target)):
        raise RuntimeError("Use distinct local intent databases")
    raw_url = os.environ.get("INTENT_LOCAL_ADMIN_DATABASE_URL")
    if not raw_url:
        raise RuntimeError("Set INTENT_LOCAL_ADMIN_DATABASE_URL for this local read-only comparison")
    url = make_url(raw_url)
    if url.drivername != "postgresql+psycopg" or url.host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Only the local PostgreSQL administration endpoint is supported")
    source = create_engine(url.set(database=args.source))
    target = create_engine(url.set(database=args.target))
    left, left_rls, left_policies, left_sequences = snapshot(source)
    right, right_rls, right_policies, right_sequences = snapshot(target)
    if left.keys() != right.keys():
        raise AssertionError("Restored table set differs")
    for table in left:
        if left[table] != right[table]:
            raise AssertionError(f"Restored rows differ in {table}")
    if left_rls != right_rls:
        raise AssertionError("Restored row-security flags differ")
    if left_policies != right_policies:
        raise AssertionError("Restored row-security policies differ")
    if left_sequences != right_sequences:
        raise AssertionError("Restored sequence state differs")
    app_url = make_url(settings().database_url)
    if app_url.host not in {"127.0.0.1", "localhost"} or app_url.username != "intent_app":
        raise RuntimeError("Restricted app smoke must use the local application role")
    app = create_engine(app_url.set(database=args.target))
    with target.connect() as db:
        sample = db.execute(text("SELECT j.id, j.created_by FROM ingest_jobs j "
                                 "JOIN memberships m ON m.workspace_id=j.workspace_id AND m.user_id=j.created_by "
                                 "WHERE m.active ORDER BY j.created_at LIMIT 1")).first()
    if not sample:
        raise AssertionError("Restored candidate has no active synthetic intake job to check")
    with app.begin() as db:
        assert db.execute(text("SELECT current_user")).scalar() == "intent_app"
        assert db.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")).scalar() is False
        assert db.execute(text("SELECT count(*) FROM ingest_jobs WHERE id=:id"), {"id": sample.id}).scalar() == 0
        db.execute(text("SELECT set_config('intent.user_id', :user_id, true)"), {"user_id": sample.created_by})
        assert db.execute(text("SELECT count(*) FROM ingest_jobs WHERE id=:id"), {"id": sample.id}).scalar() == 1
    with app.begin() as db:
        db.execute(text("SELECT set_config('intent.user_id', 'unrelated-restore-user', true)"))
        assert db.execute(text("SELECT count(*) FROM ingest_jobs WHERE id=:id"), {"id": sample.id}).scalar() == 0
    print(json.dumps({"status": "passed", "source": args.source, "target": args.target,
                      "tables": len(left), "rows": sum(len(rows) for rows in left.values()),
                      "rls_tables": sum(1 for _, enabled, _ in left_rls if enabled),
                      "force_rls_tables": sum(1 for _, _, forced in left_rls if forced),
                      "rls_policies": len(left_policies), "sequences": len(left_sequences),
                      "schema_version": json.loads(left["alembic_version"][0])["version_num"],
                      "restricted_restore_smoke": True,
                      "comparison": "full row JSON values, RLS policies and sequence state",
                      "acceptance_evidence": False}))


if __name__ == "__main__":
    main()
