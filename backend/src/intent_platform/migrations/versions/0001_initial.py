"""Initial local PostgreSQL implementation; immutable releases and application RLS."""
from alembic import op
import json
from pathlib import Path
from sqlalchemy import text

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Frozen DDL, independent of future edits to ORM metadata.
    statements = json.loads(Path(__file__).with_name("0001_schema.json").read_text(encoding="utf-8"))
    for statement in statements:
        op.get_bind().execute(text(statement))


def downgrade():
    raise RuntimeError("Automatic destructive downgrade is disabled; use an authorized backup/restore procedure")
