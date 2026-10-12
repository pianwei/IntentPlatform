"""Workspace metadata, monotonic admission epochs and archive fencing."""
from alembic import op
from sqlalchemy.engine import make_url
from sqlalchemy import text
from intent_platform.config import settings

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE workspaces ADD COLUMN status text NOT NULL DEFAULT 'active'
        CHECK(status IN ('active','archived')), ADD COLUMN revision integer NOT NULL DEFAULT 1,
        ADD COLUMN archived_at timestamptz, ADD COLUMN archived_by text,
        ADD COLUMN access_epoch integer NOT NULL DEFAULT 0 CHECK(access_epoch>=0)""")
    for table in ("runtime_sessions", "handshakes", "embed_tickets", "ingest_jobs"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN workspace_epoch integer NOT NULL DEFAULT 0")
    op.execute("CREATE INDEX ix_workspaces_directory ON workspaces(status,lower(name),id)")
    op.execute("""CREATE FUNCTION public.intent_public_workspace(requested_id text) RETURNS text
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT a.workspace_id::text FROM public.agents a JOIN public.workspaces w ON w.id=a.workspace_id
    WHERE (a.id=requested_id OR a.public_id=requested_id) AND w.status='active'
      AND a.current_release_id IS NOT NULL $$""")
    # Preserve the narrow public shapes while fencing every unauthenticated read.
    op.execute("""CREATE OR REPLACE FUNCTION public.intent_public_app(requested_public_id text)
    RETURNS TABLE(agent_id text,public_id text,release_id text,runtime_ui jsonb,channels jsonb,fixture_mode boolean)
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT a.id::text,a.public_id::text,r.id::text,COALESCE(r.snapshot->'runtime_ui','{}'::jsonb),
      jsonb_build_object('chat',COALESCE((r.snapshot->'channels'->>'chat')::boolean,true) AND (a.live_channels->>'chat')::boolean,
                         'embed',COALESCE((r.snapshot->'channels'->>'embed')::boolean,true) AND (a.live_channels->>'embed')::boolean),
      (r.snapshot->>'fixture_mode')::boolean
    FROM public.agents a JOIN public.agent_releases r ON r.id=a.current_release_id
    JOIN public.workspaces w ON w.id=a.workspace_id AND w.status='active'
    WHERE a.public_id=requested_public_id $$""")
    op.execute("""CREATE OR REPLACE FUNCTION public.intent_embed_agent(requested_id text)
    RETURNS TABLE(workspace_id text,allowed_origins jsonb)
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT a.workspace_id::text,COALESCE((SELECT jsonb_agg(o) FROM jsonb_array_elements(r.snapshot->'allowed_origins') o
      WHERE NOT a.revoked_origins @> jsonb_build_array(o)),'[]'::jsonb)
    FROM public.agents a JOIN public.agent_releases r ON r.id=a.current_release_id
    JOIN public.workspaces w ON w.id=a.workspace_id AND w.status='active'
    WHERE (a.id=requested_id OR a.public_id=requested_id)
      AND COALESCE((r.snapshot->'channels'->>'embed')::boolean,true) AND (a.live_channels->>'embed')::boolean $$""")
    op.execute("""CREATE OR REPLACE FUNCTION public.intent_public_icon(requested_public_id text)
    RETURNS TABLE(media_type text,data bytea)
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT i.media_type::text,i.data FROM public.agents a JOIN public.agent_releases r ON r.id=a.current_release_id
    JOIN public.workspaces w ON w.id=a.workspace_id AND w.status='active'
    JOIN public.assets i ON i.id=r.snapshot->'runtime_ui'->'icon'->>'value' AND i.workspace_id=a.workspace_id
    WHERE a.public_id=requested_public_id AND r.snapshot->'runtime_ui'->'icon'->>'kind'='asset'
    AND ((COALESCE((r.snapshot->'channels'->>'chat')::boolean,true) AND (a.live_channels->>'chat')::boolean)
      OR (COALESCE((r.snapshot->'channels'->>'embed')::boolean,true) AND (a.live_channels->>'embed')::boolean)) $$""")
    op.execute("""CREATE OR REPLACE FUNCTION public.intent_anonymous_session_owner(sid text,aid text)
    RETURNS TABLE(user_id text) LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT s.user_id FROM public.runtime_sessions s JOIN public.anonymous_principals g ON g.user_id=s.user_id
    JOIN public.workspaces w ON w.id=s.workspace_id AND w.status='active' AND s.workspace_epoch=w.access_epoch
    WHERE s.id=sid AND s.agent_id=aid AND s.auth_method='anonymous' AND NOT s.revoked AND NOT g.revoked AND g.expires_at>now() $$""")
    owner = op.get_bind().execute(text("SELECT current_user")).scalar()
    escaped_owner = owner.replace("'", "''")
    # These tables use FORCE RLS. Only the migration owner inside the narrow
    # archive definer may cancel their work; the app role receives no such policy.
    for table in ("ingest_jobs", "execution_plans", "plan_steps"):
        clause = f"""current_user='{escaped_owner}' AND EXISTS(SELECT 1 FROM public.workspaces w
          WHERE w.id={table}.workspace_id AND w.status='archived'
          AND w.archived_by=current_setting('intent.user_id',true))"""
        op.execute(f"CREATE POLICY workspace_archive_cleanup ON {table} USING ({clause}) WITH CHECK ({clause})")
    op.execute("""CREATE FUNCTION public.intent_archive_workspace_work(requested_ws text) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM public.workspaces w WHERE w.id=requested_ws AND w.status='archived'
          AND w.archived_by=current_setting('intent.user_id',true)) THEN
        RAISE EXCEPTION 'Workspace archive transition is not authorized';
      END IF;
      UPDATE public.runtime_sessions SET revoked=true WHERE workspace_id=requested_ws;
      UPDATE public.auth_tokens SET revoked=true WHERE scope='runtime'
        AND (runtime_session_id IN (SELECT id FROM public.runtime_sessions WHERE workspace_id=requested_ws)
          OR agent_id IN (SELECT id FROM public.agents WHERE workspace_id=requested_ws));
      UPDATE public.embed_tickets SET consumed=true WHERE workspace_id=requested_ws;
      UPDATE public.handshakes SET expires_at=LEAST(expires_at,now()) WHERE workspace_id=requested_ws;
      UPDATE public.calls SET status='CANCELLED',lease_until=NULL WHERE workspace_id=requested_ws
        AND status IN ('PENDING','EXECUTING') AND dispatched_at IS NULL;
      UPDATE public.runs r SET question=NULL,confirmed=false,confirmation_expires_at=NULL,
        status=CASE WHEN EXISTS(SELECT 1 FROM public.calls c WHERE c.run_id=r.id AND c.status IN ('EXECUTING','RECONCILING') AND c.dispatched_at IS NOT NULL) THEN r.status
                    WHEN EXISTS(SELECT 1 FROM public.calls c WHERE c.run_id=r.id AND c.status='UNKNOWN') THEN 'UNKNOWN' ELSE 'CANCELLED' END,
        revision=CASE WHEN EXISTS(SELECT 1 FROM public.calls c WHERE c.run_id=r.id AND c.status IN ('EXECUTING','RECONCILING') AND c.dispatched_at IS NOT NULL) THEN revision ELSE revision+1 END,
        finished_at=CASE WHEN EXISTS(SELECT 1 FROM public.calls c WHERE c.run_id=r.id AND c.status IN ('EXECUTING','RECONCILING','UNKNOWN') AND c.dispatched_at IS NOT NULL) THEN finished_at ELSE now() END
        WHERE r.workspace_id=requested_ws AND r.status IN ('RECEIVED','ROUTING','WAITING_INPUT','WAITING_CONFIRMATION','EXECUTING');
      UPDATE public.execution_plans SET status='CANCELLED',finished_at=now() WHERE workspace_id=requested_ws
        AND status NOT IN ('SUCCEEDED','FAILED','CANCELLED','UNKNOWN');
      UPDATE public.plan_steps SET status='SKIPPED',finished_at=now() WHERE workspace_id=requested_ws AND status='PENDING';
      UPDATE public.ingest_jobs SET status='FAILED',attempt_id=NULL,lease_until=NULL,updated_at=now(),
        error='{"code":"WORKSPACE_ARCHIVED","message":"Space archived before intake completed"}'::jsonb
        WHERE workspace_id=requested_ws AND status IN ('PENDING','RUNNING');
    END $$""")
    role = make_url(settings().database_url).username
    if not role:
        raise RuntimeError("Runtime database role is required")
    quoted = op.get_bind().dialect.identifier_preparer.quote(role)
    op.execute("REVOKE ALL ON FUNCTION public.intent_archive_workspace_work(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.intent_archive_workspace_work(text) TO {quoted}")
    op.execute("REVOKE ALL ON FUNCTION public.intent_public_workspace(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.intent_public_workspace(text) TO {quoted}")
    # Platform allocation/lifecycle auditing grants INSERT only, never tenant reads.
    op.execute("""CREATE POLICY workspace_lifecycle_audit ON audit FOR INSERT WITH CHECK (
      action IN ('workspace.rename','workspace.archive','workspace.restore','workspace.assign_administrator')
      AND user_id=current_setting('intent.user_id',true) AND resource_id=workspace_id)""")


def downgrade():
    raise RuntimeError("Use an authorized backup; never revive archived session credentials")
