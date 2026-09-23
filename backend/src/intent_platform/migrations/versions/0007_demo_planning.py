"""Versioned demo planning assets and workspace-isolated execution records."""
from alembic import op
from sqlalchemy import text

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    if not op.get_bind().scalar(text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='pgcrypto')")):
        raise RuntimeError("pgcrypto must be installed by a database administrator before demo migration")
    op.execute("ALTER TABLE runs ADD COLUMN finished_at timestamptz")
    op.execute("""CREATE TABLE action_definitions (
        id text PRIMARY KEY, workspace_id text NOT NULL REFERENCES workspaces(id),
        key text NOT NULL, definition jsonb NOT NULL, revision integer NOT NULL DEFAULT 1,
        latest_version integer NOT NULL DEFAULT 0, UNIQUE(workspace_id,key))""")
    op.execute("""CREATE TABLE action_definition_versions (
        id text PRIMARY KEY, workspace_id text NOT NULL REFERENCES workspaces(id),
        action_id text NOT NULL REFERENCES action_definitions(id), version integer NOT NULL,
        definition jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(action_id,version))""")
    op.execute("""CREATE TABLE business_skills (
        id text PRIMARY KEY, workspace_id text NOT NULL REFERENCES workspaces(id),
        key text NOT NULL, definition jsonb NOT NULL, revision integer NOT NULL DEFAULT 1,
        latest_version integer NOT NULL DEFAULT 0, disabled boolean NOT NULL DEFAULT false,
        UNIQUE(workspace_id,key))""")
    op.execute("""CREATE TABLE business_skill_versions (
        id text PRIMARY KEY, workspace_id text NOT NULL REFERENCES workspaces(id),
        skill_id text NOT NULL REFERENCES business_skills(id), version integer NOT NULL,
        definition jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(skill_id,version))""")
    op.execute("""CREATE TABLE model_profiles (
        id text PRIMARY KEY, workspace_id text NOT NULL REFERENCES workspaces(id),
        name text NOT NULL, revision integer NOT NULL DEFAULT 1,
        latest_version integer NOT NULL DEFAULT 0, disabled boolean NOT NULL DEFAULT false)""")
    op.execute("""CREATE TABLE model_profile_versions (
        id text PRIMARY KEY, workspace_id text NOT NULL REFERENCES workspaces(id),
        profile_id text NOT NULL REFERENCES model_profiles(id), version integer NOT NULL,
        config jsonb NOT NULL, secret_ciphertext bytea,
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(profile_id,version))""")
    op.execute("ALTER TABLE agents ADD COLUMN planning_enabled boolean NOT NULL DEFAULT false")
    op.execute("ALTER TABLE agents ADD COLUMN business_skill_ids jsonb NOT NULL DEFAULT '[]'::jsonb")
    op.execute("ALTER TABLE agents ADD COLUMN model_profile_id text REFERENCES model_profiles(id)")
    op.execute("""CREATE TABLE execution_plans (
        id text PRIMARY KEY, workspace_id text NOT NULL REFERENCES workspaces(id),
        run_id text NOT NULL UNIQUE REFERENCES runs(id),
        release_id text NOT NULL REFERENCES agent_releases(id), source text NOT NULL,
        skill_version_id text REFERENCES business_skill_versions(id), status text NOT NULL DEFAULT 'PENDING',
        inputs jsonb NOT NULL DEFAULT '{}'::jsonb, snapshot jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz)""")
    op.execute("""CREATE TABLE plan_steps (
        id text PRIMARY KEY, workspace_id text NOT NULL REFERENCES workspaces(id),
        plan_id text NOT NULL REFERENCES execution_plans(id), step_key text NOT NULL,
        capability_id text NOT NULL REFERENCES functions(id), version integer NOT NULL,
        definition jsonb NOT NULL, spec jsonb NOT NULL, status text NOT NULL DEFAULT 'PENDING',
        arguments jsonb, result jsonb, call_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
        started_at timestamptz, finished_at timestamptz, UNIQUE(plan_id,step_key))""")
    op.execute("""CREATE TABLE management_traces (
        id text PRIMARY KEY, workspace_id text NOT NULL REFERENCES workspaces(id),
        run_id text NOT NULL REFERENCES runs(id), layer text NOT NULL, kind text NOT NULL,
        detail jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX ix_management_traces_run_id ON management_traces(run_id,created_at)")
    op.execute("""CREATE TABLE demo_states (
        workspace_id text PRIMARY KEY REFERENCES workspaces(id), state jsonb NOT NULL,
        revision integer NOT NULL DEFAULT 1)""")
    for table in ("action_definitions", "action_definition_versions", "business_skills", "business_skill_versions",
                  "model_profiles", "model_profile_versions", "execution_plans", "plan_steps", "management_traces", "demo_states"):
        if table != "demo_states":
            op.execute(f"CREATE INDEX ix_{table}_workspace_id ON {table}(workspace_id)")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        clause = f"EXISTS(SELECT 1 FROM memberships m WHERE m.workspace_id={table}.workspace_id AND m.user_id=current_setting('intent.user_id',true) AND m.active)"
        op.execute(f"CREATE POLICY tenant_membership ON {table} USING ({clause}) WITH CHECK ({clause})")


def downgrade():
    raise RuntimeError("Restore an authorized backup; do not discard planning or demo state")
