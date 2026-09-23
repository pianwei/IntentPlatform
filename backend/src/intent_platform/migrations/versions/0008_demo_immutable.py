"""Keep published action, Skill and model profile versions immutable."""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("action_definition_versions", "business_skill_versions", "model_profile_versions"):
        op.execute(f"CREATE TRIGGER immutable_version BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION intent_immutable()")


def downgrade():
    raise RuntimeError("Restore an authorized backup; do not discard immutable demo versions")
