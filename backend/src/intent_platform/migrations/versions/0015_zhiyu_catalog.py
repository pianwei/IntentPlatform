"""Versioned synthetic catalogs, trusted personas and conversation practice state."""
from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import make_url
from intent_platform.config import settings

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE agents ADD COLUMN synthetic_catalog jsonb")
    op.execute("ALTER TABLE conversations ADD COLUMN business_context jsonb NOT NULL DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE runtime_sessions ADD COLUMN demo_persona text")
    op.execute("""CREATE TABLE synthetic_states (
        workspace_id varchar NOT NULL REFERENCES workspaces(id), catalog_id varchar NOT NULL,
        catalog_version integer NOT NULL DEFAULT 1, state jsonb NOT NULL,
        revision integer NOT NULL DEFAULT 1, PRIMARY KEY(workspace_id,catalog_id))""")
    # Reuse the established synthetic-state RLS expressions, including anonymous
    # app access. The new table adds catalog isolation without broadening identity.
    policies = op.get_bind().execute(text("SELECT policyname,cmd,qual,with_check FROM pg_policies WHERE schemaname='public' AND tablename='demo_states'")).mappings().all()
    if not policies:
        raise RuntimeError("Existing synthetic-state isolation policy is required")
    quote = op.get_bind().dialect.identifier_preparer.quote
    for policy in policies:
        statement = f"CREATE POLICY {quote(policy['policyname'])} ON synthetic_states FOR {policy['cmd']}"
        if policy["qual"]: statement += " USING (" + policy["qual"].replace("demo_states.", "synthetic_states.") + ")"
        if policy["with_check"]: statement += " WITH CHECK (" + policy["with_check"].replace("demo_states.", "synthetic_states.") + ")"
        op.execute(statement)
    op.execute("ALTER TABLE synthetic_states ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE synthetic_states FORCE ROW LEVEL SECURITY")
    role = quote(make_url(settings().database_url).username)
    op.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON synthetic_states TO {role}")
    op.execute("""CREATE FUNCTION public.intent_public_synthetic_catalog(pid text) RETURNS jsonb
      LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
      SELECT r.snapshot->'synthetic_catalog' FROM public.agents a
      JOIN public.agent_releases r ON r.id=a.current_release_id
      JOIN public.workspaces w ON w.id=a.workspace_id AND w.status='active'
      WHERE a.public_id=pid AND ((a.live_channels->>'chat')::boolean OR (a.live_channels->>'embed')::boolean) $$""")
    op.execute("REVOKE ALL ON FUNCTION public.intent_public_synthetic_catalog(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.intent_public_synthetic_catalog(text) TO {role}")


def downgrade():
    raise RuntimeError("Restore an authorized backup; do not revive retired runtime credentials")
