"""Allow a workspace administrator to inspect bounded cross-user run diagnostics.

The original run, conversation, call and event tables keep their owner-scoped
RLS policies. These read-only functions expose only the rows needed by the
management trace API after checking the caller's active admin membership.
"""
from alembic import op
from sqlalchemy.engine import make_url

from intent_platform.config import settings


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


ADMIN_CHECK = """EXISTS (
    SELECT 1 FROM public.memberships m
    WHERE m.workspace_id = requested_ws
      AND m.user_id = current_setting('intent.user_id', true)
      AND m.active
      AND m.roles @> '["admin"]'::jsonb
      AND m.role_ceiling @> '["admin"]'::jsonb
)"""


def upgrade():
    op.execute(f"""CREATE FUNCTION public.intent_admin_trace_runs(
        requested_ws text, selected_agent text, selected_status text,
        selected_run text, lower_time timestamptz, upper_time timestamptz,
        max_rows integer)
    RETURNS TABLE(run_id text, agent_id text, status text, decision text,
                  created_at timestamptz, finished_at timestamptz)
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
      SELECT r.id::text, c.agent_id::text, r.status::text, r.decision::text,
             r.created_at, r.finished_at
      FROM public.runs r JOIN public.conversations c ON c.id = r.conversation_id
      WHERE r.workspace_id = requested_ws AND c.workspace_id = requested_ws
        AND ({ADMIN_CHECK})
        AND (selected_agent IS NULL OR c.agent_id = selected_agent)
        AND (selected_status IS NULL OR r.status = selected_status)
        AND (selected_run IS NULL OR r.id = selected_run)
        AND (lower_time IS NULL OR r.created_at >= lower_time)
        AND (upper_time IS NULL OR r.created_at <= upper_time)
      ORDER BY r.created_at DESC, r.id DESC
      LIMIT LEAST(GREATEST(max_rows, 1), 100)
    $$""")
    op.execute(f"""CREATE FUNCTION public.intent_admin_trace_detail(requested_ws text, requested_run text)
    RETURNS TABLE(run_id text, agent_id text, status text, decision text,
                  created_at timestamptz, finished_at timestamptz,
                  calls jsonb, status_events jsonb)
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
      SELECT r.id::text, c.agent_id::text, r.status::text, r.decision::text,
             r.created_at, r.finished_at,
             COALESCE((
               SELECT jsonb_agg(jsonb_build_object(
                 'id', q.id, 'status', q.status, 'definition', q.definition,
                 'arguments', q.arguments, 'result', q.result) ORDER BY q.id)
               FROM (SELECT k.id, k.status, k.definition, k.arguments, k.result
                     FROM public.calls k WHERE k.run_id = r.id AND k.workspace_id = requested_ws
                     ORDER BY k.id LIMIT 100) q
             ), '[]'::jsonb),
             COALESCE((
               SELECT jsonb_agg(jsonb_build_object('created_at', e.created_at,
                                                    'status', e.payload->>'status') ORDER BY e.sequence)
               FROM (SELECT ev.sequence, ev.created_at, ev.payload FROM public.events ev
                     WHERE ev.run_id = r.id AND ev.workspace_id = requested_ws
                       AND ev.event_type = 'STATUS'
                     ORDER BY ev.sequence LIMIT 1000) e
             ), '[]'::jsonb)
      FROM public.runs r JOIN public.conversations c ON c.id = r.conversation_id
      WHERE r.workspace_id = requested_ws AND c.workspace_id = requested_ws
        AND r.id = requested_run AND ({ADMIN_CHECK})
    $$""")
    # PostgreSQL grants PUBLIC execute on functions by default. Restrict these
    # definer entry points to the configured application database role.
    op.execute("REVOKE ALL ON FUNCTION public.intent_admin_trace_runs(text,text,text,text,timestamptz,timestamptz,integer) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.intent_admin_trace_detail(text,text) FROM PUBLIC")
    app_role = make_url(settings().database_url).username
    if not app_role:
        raise RuntimeError("Runtime database role is required for administrator trace grants")
    quoted_role = op.get_bind().dialect.identifier_preparer.quote(app_role)
    op.execute(f"GRANT EXECUTE ON FUNCTION public.intent_admin_trace_runs(text,text,text,text,timestamptz,timestamptz,integer) TO {quoted_role}")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.intent_admin_trace_detail(text,text) TO {quoted_role}")


def downgrade():
    raise RuntimeError("Restore an authorized backup; retain administrator trace evidence")
