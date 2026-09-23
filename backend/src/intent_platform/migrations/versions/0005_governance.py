"""Server-bounded governance and durable per-attempt concurrency observations."""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

QUEUE_COUNT_SQL = """CREATE OR REPLACE FUNCTION intent_queue_count(requested_ws text, excluded_conversation text DEFAULT NULL)
RETURNS bigint LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
SELECT count(*) FROM runs r WHERE r.workspace_id=requested_ws AND r.status='RECEIVED'
AND (excluded_conversation IS NULL OR r.conversation_id<>excluded_conversation OR r.user_id<>current_setting('intent.user_id',true))
AND EXISTS(SELECT 1 FROM memberships m WHERE m.workspace_id=requested_ws AND m.user_id=current_setting('intent.user_id',true) AND m.active)
$$"""


def upgrade():
    op.execute(QUEUE_COUNT_SQL)
    op.execute("ALTER TABLE memberships ADD COLUMN revision integer NOT NULL DEFAULT 1, ADD COLUMN role_ceiling jsonb NOT NULL DEFAULT '[]'::jsonb")
    op.execute("UPDATE memberships SET role_ceiling=roles")
    op.execute("ALTER TABLE agents ADD COLUMN integration_refs jsonb NOT NULL DEFAULT '[]'::jsonb, ADD COLUMN revoked_integration_refs jsonb NOT NULL DEFAULT '[]'::jsonb")
    # Existing drafts are legacy selections, not retroactively published identity policy.
    op.execute("ALTER TABLE runtime_sessions ADD COLUMN integration_id text, ADD COLUMN identity_policy jsonb")
    op.execute("UPDATE runtime_sessions s SET integration_id=CASE WHEN t.source='development' THEN 'development' ELSE nullif(t.delegation->>'integration_id','') END FROM auth_tokens t WHERE t.id=s.current_token_id")
    op.execute("UPDATE runtime_sessions s SET integration_id=CASE WHEN t.source='development' THEN 'development' ELSE nullif(t.delegation->>'integration_id','') END FROM conversations c JOIN auth_tokens t ON t.id=c.token_id WHERE c.runtime_session_id=s.id AND s.integration_id IS NULL")
    op.execute("ALTER TABLE embed_tickets ADD COLUMN integration_id text")
    op.execute("UPDATE embed_tickets e SET integration_id=CASE WHEN t.source='development' THEN 'development' ELSE nullif(t.delegation->>'integration_id','') END FROM auth_tokens t WHERE t.id=e.source_token_id")
    op.execute("ALTER TABLE runs ADD COLUMN queued_at timestamptz")
    op.execute("UPDATE runs SET queued_at=created_at")
    op.execute("ALTER TABLE runs ALTER COLUMN queued_at SET NOT NULL, ALTER COLUMN queued_at SET DEFAULT now()")
    op.execute("CREATE TABLE workspace_governance(workspace_id text PRIMARY KEY REFERENCES workspaces(id),revision integer NOT NULL DEFAULT 1,limits jsonb NOT NULL)")
    op.execute("CREATE TABLE processing_attempts(id text PRIMARY KEY,workspace_id text NOT NULL REFERENCES workspaces(id),run_id text NOT NULL REFERENCES runs(id),revision integer NOT NULL,call_id text,kind text NOT NULL,status text NOT NULL DEFAULT 'active',queued_at timestamptz,started_at timestamptz NOT NULL DEFAULT now(),finished_at timestamptz,lease_until timestamptz NOT NULL,timings jsonb NOT NULL DEFAULT '{}'::jsonb,error_stage text)")
    op.execute("CREATE INDEX ix_processing_attempts_workspace_id ON processing_attempts(workspace_id)")
    op.execute("CREATE INDEX ix_processing_attempts_run_id ON processing_attempts(run_id)")
    op.execute("CREATE INDEX ix_processing_attempts_capacity ON processing_attempts(workspace_id,status,kind,lease_until)")
    for table in ("workspace_governance", "processing_attempts"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        clause = f"EXISTS(SELECT 1 FROM memberships m WHERE m.workspace_id={table}.workspace_id AND m.user_id=current_setting('intent.user_id',true) AND m.active)"
        op.execute(f"CREATE POLICY tenant_membership ON {table} USING ({clause}) WITH CHECK ({clause})")


def downgrade():
    raise RuntimeError("Restore an authorized backup; do not discard governance or execution observations")
