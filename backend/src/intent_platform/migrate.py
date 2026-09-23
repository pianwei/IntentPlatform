"""Bootstrap the initial PostgreSQL schema using the separate owner credential."""
from sqlalchemy import create_engine, text, inspect
from pathlib import Path
from .config import settings
from .db import Base
from . import models  # noqa: F401

TENANT_TABLES = ["functions", "function_versions", "agents", "agent_releases", "conversations", "runs", "calls", "events", "audit", "documents", "evaluations"]


def apply_schema(connection):
    Base.metadata.create_all(connection)
    connection.execute(text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS revoked_origins jsonb NOT NULL DEFAULT '[]'::jsonb"))
    connection.execute(text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS allowed_users jsonb NOT NULL DEFAULT '[]'::jsonb"))
    connection.execute(text("ALTER TABLE calls ADD COLUMN IF NOT EXISTS dispatched_at timestamptz"))
    for table in TENANT_TABLES:
        connection.execute(text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
        connection.execute(text(f'DROP POLICY IF EXISTS tenant_membership ON "{table}"'))
        clause = "EXISTS (SELECT 1 FROM memberships m WHERE m.workspace_id = workspace_id AND m.workspace_id = " + table + ".workspace_id AND m.user_id = current_setting('intent.user_id', true) AND m.active)"
        if table in {"conversations", "runs"}:
            clause += " AND user_id = current_setting('intent.user_id', true)"
        elif table in {"events", "calls"}:
            clause += " AND EXISTS (SELECT 1 FROM runs r WHERE r.id = " + table + ".run_id AND r.user_id = current_setting('intent.user_id', true))"
        connection.execute(text(f'CREATE POLICY tenant_membership ON "{table}" USING ({clause}) WITH CHECK ({clause})'))
    connection.execute(text("""CREATE OR REPLACE FUNCTION intent_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'Published versions are immutable'; END $$"""))
    for table in ["function_versions", "agent_releases"]:
        connection.execute(text(f'DROP TRIGGER IF EXISTS immutable_version ON "{table}"'))
        connection.execute(text(f'CREATE TRIGGER immutable_version BEFORE UPDATE OR DELETE ON "{table}" FOR EACH ROW EXECUTE FUNCTION intent_immutable()'))
    # Only public embedding metadata is exposed before login, never definitions or conversation data.
    connection.execute(text("""CREATE OR REPLACE FUNCTION intent_embed_agent(requested_id text)
    RETURNS TABLE(workspace_id text, allowed_origins jsonb)
    LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT a.workspace_id::text, COALESCE((SELECT jsonb_agg(o) FROM jsonb_array_elements(r.snapshot->'allowed_origins') o
    WHERE NOT a.revoked_origins @> jsonb_build_array(o)), '[]'::jsonb)
    FROM agents a JOIN agent_releases r ON r.id = a.current_release_id
    WHERE a.id = requested_id $$"""))
    connection.execute(text("CREATE TABLE IF NOT EXISTS intent_schema_versions (version text PRIMARY KEY, installed_at timestamptz NOT NULL DEFAULT now())"))
    connection.execute(text("INSERT INTO intent_schema_versions(version) VALUES ('0001') ON CONFLICT DO NOTHING"))


def main():
    from alembic import command
    from alembic.config import Config
    engine = create_engine(settings().migration_database_url)
    adopt_bootstrap = False
    with engine.begin() as conn:
        if conn.dialect.name != "postgresql":
            raise RuntimeError("PostgreSQL is required")
        inspector = inspect(conn)
        if inspector.has_table("intent_schema_versions") and not inspector.has_table("alembic_version"):
            if settings().env == "production":
                raise RuntimeError("A pre-Alembic bootstrap requires explicit migration adoption before production")
            # One-time local transition for the initial prototype, never the upgrade strategy.
            apply_schema(conn)
            adopt_bootstrap = True
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent / "migrations"))
    if adopt_bootstrap:
        command.stamp(cfg, "0001")
    command.upgrade(cfg, "head")
    print("Alembic head ready (owner migration, runtime app RLS)")


if __name__ == "__main__":
    main()
