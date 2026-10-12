"""Opt-in per-agent three-layer strategy; immutable historical JSON remains intact."""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE agents ADD COLUMN routing_strategy text NOT NULL DEFAULT 'legacy'")


def downgrade():
    raise RuntimeError("Restore an authorized backup; retain published strategies")
