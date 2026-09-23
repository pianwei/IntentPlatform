"""Immutable scoped domains and trusted binding provenance."""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE runs ADD COLUMN bindings jsonb NOT NULL DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE runs ADD COLUMN validation_evidence jsonb")
    op.execute("ALTER TABLE calls ADD COLUMN bindings jsonb NOT NULL DEFAULT '{}'::jsonb")
    op.execute("CREATE TABLE domain_packages(id text PRIMARY KEY,workspace_id text NOT NULL REFERENCES workspaces(id),key text NOT NULL,revision integer NOT NULL DEFAULT 1,latest_version integer NOT NULL DEFAULT 0,definition jsonb NOT NULL,validation jsonb,validated_revision integer,UNIQUE(workspace_id,key))")
    op.execute("CREATE TABLE domain_versions(id text PRIMARY KEY,workspace_id text NOT NULL REFERENCES workspaces(id),package_id text NOT NULL REFERENCES domain_packages(id),version integer NOT NULL,definition jsonb NOT NULL,compiled jsonb NOT NULL,published_by text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(package_id,version))")
    for table in ("domain_packages", "domain_versions"):
        op.execute(f"CREATE INDEX ix_{table}_workspace_id ON {table}(workspace_id)")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        clause = f"EXISTS(SELECT 1 FROM memberships m WHERE m.workspace_id={table}.workspace_id AND m.user_id=current_setting('intent.user_id',true) AND m.active)"
        op.execute(f"CREATE POLICY tenant_membership ON {table} USING ({clause}) WITH CHECK ({clause})")
    op.execute("CREATE TRIGGER immutable_version BEFORE UPDATE OR DELETE ON domain_versions FOR EACH ROW EXECUTE FUNCTION intent_immutable()")


def downgrade():
    raise RuntimeError("Restore an authorized backup; do not discard immutable domain/binding evidence")
