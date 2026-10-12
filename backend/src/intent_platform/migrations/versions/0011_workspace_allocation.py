"""Record platform allocation without giving allocators tenant data access."""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE workspaces ADD COLUMN created_by text")
    # INSERT-only policy; ordinary tenant SELECT/UPDATE policies remain intact.
    op.execute("""CREATE POLICY workspace_creation_audit ON audit FOR INSERT WITH CHECK (
        action = 'workspace.create' AND user_id = current_setting('intent.user_id',true)
        AND resource_id = workspace_id
        AND EXISTS(SELECT 1 FROM workspaces w WHERE w.id = audit.workspace_id
                   AND w.created_by = current_setting('intent.user_id',true)))""")


def downgrade():
    raise RuntimeError("Use an authorized backup; preserve allocation and audit history")
