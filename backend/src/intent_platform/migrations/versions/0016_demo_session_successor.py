"""Server-owned visitor successor locators; never accept a browser-supplied owner."""
from alembic import op
revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("ALTER TABLE runtime_sessions ADD COLUMN demo_successor_id text")

def downgrade():
    raise RuntimeError("Use authorized restore without reviving revoked visitor credentials")
