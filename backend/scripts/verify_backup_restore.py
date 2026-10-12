"""Local backup/restore comparison without exposing record values.

Set INTENT_LOCAL_ADMIN_DATABASE_URL to the local administrative PostgreSQL URL,
then pass distinct source/target intent_* database names. Source is read-only;
restricted-role behavior probes on the restored target always roll back.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from intent_platform.config import settings


def snapshot(engine):
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as db, db.begin():
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


_LITERAL = r"'(?:[^']|'')*'"
_ARRAY_CAST = re.compile(r"\(ARRAY\[(" + _LITERAL + r"::character varying(?:, " + _LITERAL + r"::character varying)*)\]\)::text\[\]")
_ELEMENT_CAST = re.compile(r"\((" + _LITERAL + r")::character varying\)::text")


def canonical_lifecycle_policy(expression):
    """Normalize only pg_dump's lossless literal varchar[] -> text[] spelling.

    No variables, typmods, operators, literal values or other casts are ignored.
    The caller additionally limits this to one reviewed policy and evaluates its
    original expressions against PostgreSQL, including SQL NULL behavior.
    """
    if expression is None:
        return None
    expression = _ARRAY_CAST.sub(lambda m: "ARRAY[" + re.sub("(" + _LITERAL + r")::character varying", r"\1::text", m.group(1)) + "]", expression)
    return _ELEMENT_CAST.sub(r"\1::text", expression)


def compare_policies(left, right):
    if len(left) != len(right):
        raise AssertionError("Restored row-security policy count differs")
    equivalent = []
    for before, after in zip(left, right, strict=True):
        if tuple(before) == tuple(after):
            continue
        if tuple(before[:6]) != tuple(after[:6]) or tuple(before[:2]) != ("audit", "workspace_lifecycle_audit"):
            raise AssertionError("Restored row-security policies differ")
        if canonical_lifecycle_policy(before[6]) != canonical_lifecycle_policy(after[6]):
            raise AssertionError("Restored lifecycle policy semantics differ")
        equivalent.append((before[6], after[6]))
    return equivalent


def verify_cast_equivalence(source, target, expressions):
    count = 0
    for before, after in expressions:
        actions = ["workspace.rename", "workspace.archive", "workspace.restore", "workspace.assign_administrator", "function.publish", None]
        cases = [(action, user, resource, workspace) for action in actions
                 for user in ("restore-probe", "different-user", "", None)
                 for resource, workspace in (("scope", "scope"), ("other", "scope"), (None, "scope"), ("scope", None))]
        for engine in (source, target):
            with engine.connect() as db, db.begin():
                for current in ("restore-probe", "different-user", ""):
                    db.execute(text("SELECT set_config('intent.user_id', :value, true)"), {"value": current})
                    statement = text("SELECT " + before + " AS before, " + after + " AS after FROM "
                        "(SELECT CAST(:action AS varchar) action, CAST(:user_id AS varchar) user_id, "
                        "CAST(:resource_id AS varchar) resource_id, CAST(:workspace_id AS varchar) workspace_id) sample")
                    for action, user, resource, workspace in cases:
                        result = db.execute(statement, dict(action=action, user_id=user, resource_id=resource, workspace_id=workspace)).one()
                        if result[0] is not result[1]:
                            raise AssertionError("pg_dump cast expressions differ on a boundary value")
                        count += 1
    return count


def verify_lifecycle_rls(app, workspace_id):
    """Exercise the restored INSERT policy with an unrelated, nonmember identity."""
    actions = ("workspace.rename", "workspace.archive", "workspace.restore", "workspace.assign_administrator")
    cases = [(action, "restore-policy-probe", workspace_id, True) for action in actions]
    cases += [("function.publish", "restore-policy-probe", workspace_id, False),
              ("workspace.rename", "other-user", workspace_id, False),
              ("workspace.rename", "restore-policy-probe", "other-space", False)]
    for index, (action, user_id, resource_id, allowed) in enumerate(cases):
        with app.connect() as db:
            transaction = db.begin()
            try:
                db.execute(text("SELECT set_config('intent.user_id', 'restore-policy-probe', true)"))
                db.execute(text("INSERT INTO audit(id,user_id,workspace_id,action,resource_id,detail,created_at) "
                    "VALUES (:id,:user_id,:workspace_id,:action,:resource_id,'{}'::jsonb,now())"),
                    {"id": f"restore-policy-probe-{index}", "user_id": user_id, "workspace_id": workspace_id,
                     "action": action, "resource_id": resource_id})
                if not allowed:
                    raise AssertionError("Restored lifecycle policy allowed a forbidden insert")
            except DBAPIError as exc:
                if allowed or getattr(exc.orig, "sqlstate", None) != "42501":
                    raise AssertionError("Restored lifecycle policy failed its behavior probe") from None
            finally:
                transaction.rollback()
    return len(cases)


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
    equivalent_casts = compare_policies(left_policies, right_policies)
    cast_cases = verify_cast_equivalence(source, target, equivalent_casts)
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
        workspace_id = db.execute(text("SELECT id FROM workspaces ORDER BY id LIMIT 1")).scalar_one()
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
    lifecycle_cases = verify_lifecycle_rls(app, workspace_id)
    if snapshot(target) != (right, right_rls, right_policies, right_sequences):
        raise AssertionError("Restore probes changed persistent target data")
    print(json.dumps({"status": "passed", "restored": True, "source": args.source, "target": args.target,
                      "tables": len(left), "rows": sum(len(rows) for rows in left.values()),
                      "rls_tables": sum(1 for _, enabled, _ in left_rls if enabled),
                      "force_rls_tables": sum(1 for _, _, forced in left_rls if forced),
                      "rls_policies": len(left_policies), "sequences": len(left_sequences),
                      "schema_version": json.loads(left["alembic_version"][0])["version_num"],
                      "restricted_restore_smoke": True,
                      "equivalent_pg_dump_casts": len(equivalent_casts), "postgres_cast_boundary_cases": cast_cases,
                      "restricted_lifecycle_policy_cases": lifecycle_cases, "probe_changes_rolled_back": True,
                      "comparison": "full row JSON values, exact RLS policies except verified lossless literal casts, sequences",
                      "acceptance_evidence": False}))


if __name__ == "__main__":
    main()
