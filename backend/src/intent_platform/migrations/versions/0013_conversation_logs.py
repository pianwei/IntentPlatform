"""Bounded administrator conversation views; runtime ownership policies are unchanged."""
from alembic import op
from sqlalchemy.engine import make_url
from intent_platform.config import settings

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

CHECK = """EXISTS (SELECT 1 FROM public.memberships m WHERE m.workspace_id=requested_ws
 AND m.user_id=current_setting('intent.user_id',true) AND m.active
 AND m.roles @> '["admin"]'::jsonb AND m.role_ceiling @> '["admin"]'::jsonb)"""
TITLE = """LEFT(COALESCE(NULLIF(TRIM((SELECT mt.detail->>'goal' FROM public.management_traces mt
 JOIN public.runs gr ON gr.id=mt.run_id WHERE gr.conversation_id=c.id AND mt.kind='goal_recognized'
 ORDER BY gr.created_at,mt.created_at LIMIT 1)),''),
 NULLIF((SELECT r.text FROM public.runs r WHERE r.conversation_id=c.id ORDER BY r.created_at,r.id LIMIT 1),''),c.title,'新会话'),60)"""

def upgrade():
    op.execute(f"""CREATE FUNCTION public.intent_admin_conversations(requested_ws text,
 selected_agent text, selected_status text, query_text text, lower_time timestamptz,
 upper_time timestamptz, max_rows integer, skip_rows integer) RETURNS jsonb
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 WITH candidates AS (
 SELECT c.id,c.agent_id,a.name AS agent_name,{TITLE} AS title,c.created_at,
 GREATEST(c.updated_at,COALESCE((SELECT max(e.created_at) FROM public.events e WHERE e.conversation_id=c.id),c.updated_at)) AS updated_at,
 (SELECT count(*) FROM public.runs r WHERE r.conversation_id=c.id) AS turn_count,
 COALESCE((SELECT r.status FROM public.runs r WHERE r.conversation_id=c.id ORDER BY r.created_at DESC,r.id DESC LIMIT 1),'EMPTY') AS status
 FROM public.conversations c JOIN public.agents a ON a.id=c.agent_id
 WHERE c.workspace_id=requested_ws AND a.workspace_id=requested_ws AND ({CHECK})
 AND (selected_agent IS NULL OR c.agent_id=selected_agent)), filtered AS (
 SELECT * FROM candidates WHERE (selected_status IS NULL OR status=selected_status)
 AND (query_text IS NULL OR position(lower(query_text) in lower(title))>0)
 AND (lower_time IS NULL OR updated_at>=lower_time) AND (upper_time IS NULL OR updated_at<=upper_time))
 SELECT jsonb_build_object('total',(SELECT count(*) FROM filtered),'items',COALESCE((SELECT jsonb_agg(to_jsonb(p))
 FROM (SELECT * FROM filtered ORDER BY updated_at DESC,id DESC LIMIT LEAST(GREATEST(max_rows,1),50)
 OFFSET LEAST(GREATEST(skip_rows,0),100000)) p),'[]'::jsonb)) $$""")
    op.execute(f"""CREATE FUNCTION public.intent_admin_conversation_detail(requested_ws text, requested_conv text,
 max_rows integer, skip_rows integer, requested_run text DEFAULT NULL) RETURNS jsonb LANGUAGE sql SECURITY DEFINER
 SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT jsonb_build_object('id',c.id,'agent_id',c.agent_id,'agent_name',a.name,'title',{TITLE},
 'turn_count',(SELECT count(*) FROM public.runs r WHERE r.conversation_id=c.id),
 'turns',COALESCE((SELECT jsonb_agg(jsonb_build_object('run_id',r.id,'question',r.text,
 'status',r.status,'revision',r.revision,'created_at',r.created_at,'finished_at',r.finished_at,
 'has_plan',EXISTS(SELECT 1 FROM public.execution_plans p WHERE p.run_id=r.id),
 'events',COALESCE((SELECT jsonb_agg(to_jsonb(e)) FROM (SELECT ev.event_type,ev.payload,ev.sequence,ev.created_at
 FROM public.events ev WHERE ev.run_id=r.id AND ev.workspace_id=requested_ws ORDER BY ev.sequence DESC LIMIT 1000) e),'[]'::jsonb),
 'calls',COALESCE((SELECT jsonb_agg(to_jsonb(k)) FROM (SELECT ca.id,ca.definition,ca.result,ca.status FROM public.calls ca
 WHERE ca.run_id=r.id AND ca.workspace_id=requested_ws ORDER BY ca.id LIMIT 100) k),'[]'::jsonb)) ORDER BY r.created_at,r.id)
 FROM (SELECT * FROM public.runs ru WHERE ru.conversation_id=c.id AND ru.workspace_id=requested_ws
 AND (requested_run IS NULL OR ru.id=requested_run)
 ORDER BY ru.created_at DESC,ru.id DESC LIMIT LEAST(GREATEST(max_rows,1),50) OFFSET LEAST(GREATEST(skip_rows,0),100000)) r),'[]'::jsonb))
 FROM public.conversations c JOIN public.agents a ON a.id=c.agent_id WHERE c.id=requested_conv
 AND c.workspace_id=requested_ws AND a.workspace_id=requested_ws AND ({CHECK}) $$""")
    op.execute(f"""CREATE FUNCTION public.intent_admin_log_file(requested_ws text, requested_conv text, requested_file text)
 RETURNS TABLE(data bytea,media_type text,filename text,call_id text,run_id text) LANGUAGE sql SECURITY DEFINER
 SET search_path=pg_catalog,public,pg_temp AS $$ SELECT f.data,f.media_type::text,f.filename::text,f.call_id::text,f.run_id::text
 FROM public.result_files f WHERE f.id=requested_file AND f.conversation_id=requested_conv
 AND f.workspace_id=requested_ws AND ({CHECK}) $$""")
    role = make_url(settings().database_url).username
    if not role:
        raise RuntimeError("Runtime database role is required")
    quoted = op.get_bind().dialect.identifier_preparer.quote(role)
    for signature in ("intent_admin_conversations(text,text,text,text,timestamptz,timestamptz,integer,integer)",
                      "intent_admin_conversation_detail(text,text,integer,integer,text)", "intent_admin_log_file(text,text,text)"):
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {quoted}")

def downgrade():
    raise RuntimeError("Use an authorized backup; retain historical conversations")
