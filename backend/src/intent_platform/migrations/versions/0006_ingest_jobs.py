"""Durable, workspace-isolated document intake queue."""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE ingest_jobs (
        id text PRIMARY KEY,
        workspace_id text NOT NULL REFERENCES workspaces(id),
        created_by text NOT NULL,
        token_id text NOT NULL,
        idempotency_key text NOT NULL,
        kind text NOT NULL CHECK (kind IN ('document.create','document.revise','openapi.parse')),
        status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')),
        filename text,
        payload bytea,
        document_id text,
        source_revision integer,
        result jsonb,
        error jsonb,
        attempt_id text,
        lease_until timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        started_at timestamptz,
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(workspace_id,created_by,idempotency_key)
    )""")
    op.execute("CREATE INDEX ix_ingest_jobs_workspace_id ON ingest_jobs(workspace_id)")
    op.execute("CREATE INDEX ix_ingest_jobs_claim ON ingest_jobs(workspace_id,status,lease_until,created_at)")
    op.execute("ALTER TABLE ingest_jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ingest_jobs FORCE ROW LEVEL SECURITY")
    clause = "EXISTS(SELECT 1 FROM memberships m WHERE m.workspace_id=ingest_jobs.workspace_id AND m.user_id=current_setting('intent.user_id',true) AND m.active)"
    op.execute(f"CREATE POLICY tenant_membership ON ingest_jobs USING ({clause}) WITH CHECK ({clause})")


def downgrade():
    raise RuntimeError("Restore an authorized backup; do not discard intake jobs")
