"""Local identity, account-owned integration keys and isolated public visitors."""
from alembic import op
from sqlalchemy.engine import make_url
from intent_platform.config import settings

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    for ddl in (
        "CREATE TABLE local_accounts(user_id text PRIMARY KEY,username text UNIQUE NOT NULL,display_name text NOT NULL,password_hash text,active boolean NOT NULL DEFAULT true,must_change_password boolean NOT NULL DEFAULT true,credential_version integer NOT NULL DEFAULT 1,revision integer NOT NULL DEFAULT 1,preferences jsonb NOT NULL DEFAULT '{\"density\":\"comfortable\"}'::jsonb,created_at timestamptz NOT NULL DEFAULT now())",
        "CREATE TABLE local_credentials(id text PRIMARY KEY,user_id text NOT NULL,kind text NOT NULL,secret_hash text NOT NULL,version integer NOT NULL,expires_at timestamptz NOT NULL,revoked boolean NOT NULL DEFAULT false,token_id text)",
        "CREATE INDEX ix_local_credentials_user_id ON local_credentials(user_id)",
        "CREATE TABLE integration_keys(id text PRIMARY KEY,user_id text NOT NULL REFERENCES local_accounts(user_id),name text NOT NULL,secret_hash text NOT NULL,version integer NOT NULL DEFAULT 1,revoked boolean NOT NULL DEFAULT false,expires_at timestamptz,created_at timestamptz NOT NULL DEFAULT now())",
        "CREATE INDEX ix_integration_keys_user_id ON integration_keys(user_id)",
        "CREATE TABLE anonymous_principals(user_id text PRIMARY KEY,workspace_id text NOT NULL,agent_id text NOT NULL,release_id text NOT NULL,expires_at timestamptz NOT NULL,revoked boolean NOT NULL DEFAULT false)",
        "CREATE INDEX ix_anonymous_principals_workspace_id ON anonymous_principals(workspace_id)",
        "CREATE TABLE identity_audit(id text PRIMARY KEY,actor_id text NOT NULL,subject_id text NOT NULL,action text NOT NULL,created_at timestamptz NOT NULL DEFAULT now())",
        "CREATE TABLE login_throttles(bucket text PRIMARY KEY,failures integer NOT NULL DEFAULT 0,blocked_until timestamptz NOT NULL DEFAULT now())",
        "ALTER TABLE auth_tokens ADD COLUMN auth_method text NOT NULL DEFAULT 'legacy',ADD COLUMN credential_version integer,ADD COLUMN source_key_id text",
        "ALTER TABLE runtime_sessions ADD COLUMN auth_method text NOT NULL DEFAULT 'legacy',ADD COLUMN source_key_id text,ADD COLUMN key_version integer,ADD COLUMN visitor_id text,ADD COLUMN recovery_hash text",
        "ALTER TABLE embed_tickets ADD COLUMN source_key_id text,ADD COLUMN key_version integer,ADD COLUMN visitor_id text",
    ):
        op.execute(ddl)
    # Preserve release snapshots and all historical identity values. Only mutable
    # draft names are disambiguated, in a deterministic per-space order.
    op.execute("""DO $$ DECLARE a record; candidate text; base text; suffix integer;
    BEGIN FOR a IN SELECT id,workspace_id,name FROM agents ORDER BY workspace_id,id LOOP
      base := COALESCE(NULLIF(btrim(a.name),''),'智能体'); candidate := left(base,160); suffix := 2;
      WHILE EXISTS(SELECT 1 FROM agents b WHERE b.workspace_id=a.workspace_id AND b.id<>a.id AND lower(btrim(b.name))=lower(candidate)) LOOP
        candidate := left(base,140)||' ('||suffix::text||')'; suffix := suffix+1;
      END LOOP;
      UPDATE agents SET name=candidate WHERE id=a.id AND name<>candidate;
    END LOOP; END $$""")
    op.execute("ALTER TABLE agents ADD CONSTRAINT uq_agent_workspace_name UNIQUE(workspace_id,name)")
    op.execute("CREATE UNIQUE INDEX uq_agent_workspace_normalized_name ON agents(workspace_id,lower(btrim(name)))")
    # Guests are NOT members. This checked, read-only predicate supplies only a
    # time-bounded tenant context; owner clauses below continue isolating rows.
    op.execute("""CREATE FUNCTION public.intent_guest_workspace(requested_ws text) RETURNS boolean
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
      SELECT EXISTS(SELECT 1 FROM public.anonymous_principals g WHERE g.user_id=current_setting('intent.user_id',true)
         AND g.workspace_id=requested_ws AND NOT g.revoked AND g.expires_at>now()) $$""")
    op.execute("REVOKE ALL ON FUNCTION public.intent_guest_workspace(text) FROM PUBLIC")
    role = op.get_bind().dialect.identifier_preparer.quote(make_url(settings().database_url).username)
    op.execute(f"GRANT EXECUTE ON FUNCTION public.intent_guest_workspace(text) TO {role}")
    op.execute("""CREATE FUNCTION public.intent_anonymous_session_owner(sid text,aid text) RETURNS TABLE(user_id text)
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
      SELECT s.user_id FROM public.runtime_sessions s JOIN public.anonymous_principals g ON g.user_id=s.user_id
      WHERE s.id=sid AND s.agent_id=aid AND s.auth_method='anonymous' AND NOT s.revoked AND NOT g.revoked AND g.expires_at>now() $$""")
    op.execute("REVOKE ALL ON FUNCTION public.intent_anonymous_session_owner(text,text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.intent_anonymous_session_owner(text,text) TO {role}")
    op.execute("""CREATE OR REPLACE FUNCTION public.intent_queue_count(requested_ws text,excluded_conversation text DEFAULT NULL)
      RETURNS bigint LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
      SELECT count(*) FROM public.runs r WHERE r.workspace_id=requested_ws AND r.status='RECEIVED'
      AND (excluded_conversation IS NULL OR r.conversation_id<>excluded_conversation OR r.user_id<>current_setting('intent.user_id',true))
      AND (public.intent_guest_workspace(requested_ws) OR EXISTS(SELECT 1 FROM public.memberships m WHERE m.workspace_id=requested_ws AND m.user_id=current_setting('intent.user_id',true) AND m.active)) $$""")
    op.execute("""CREATE FUNCTION public.intent_attempt_usage(requested_ws text) RETURNS TABLE(kind text,total bigint)
      LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
      SELECT p.kind,count(*) FROM public.processing_attempts p WHERE p.workspace_id=requested_ws AND p.status='active' AND p.lease_until>now()
      AND (public.intent_guest_workspace(requested_ws) OR EXISTS(SELECT 1 FROM public.memberships m WHERE m.workspace_id=requested_ws AND m.user_id=current_setting('intent.user_id',true) AND m.active)) GROUP BY p.kind $$""")
    op.execute("REVOKE ALL ON FUNCTION public.intent_attempt_usage(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.intent_attempt_usage(text) TO {role}")
    tables = ["functions","function_versions","agents","agent_releases","conversations","runs","calls","events",
              "runtime_sessions","assets","result_files","execution_plans","plan_steps","processing_attempts",
              "management_traces","demo_states","workspace_governance","documents","document_versions",
              "domain_packages","domain_versions","action_definitions","action_definition_versions",
              "business_skills","business_skill_versions","model_profiles","model_profile_versions"]
    from sqlalchemy import inspect
    present = set(inspect(op.get_bind()).get_table_names())
    for table in tables:
        if table not in present:
            continue
        clause = f"public.intent_guest_workspace({table}.workspace_id)"
        if table in {"conversations", "runs", "runtime_sessions"}:
            clause += " AND user_id=current_setting('intent.user_id',true)"
        elif table in {"calls", "events", "execution_plans", "processing_attempts", "management_traces"}:
            clause += f" AND EXISTS(SELECT 1 FROM runs r WHERE r.id={table}.run_id AND r.user_id=current_setting('intent.user_id',true))"
        elif table == "plan_steps":
            clause += " AND EXISTS(SELECT 1 FROM execution_plans p JOIN runs r ON r.id=p.run_id WHERE p.id=plan_steps.plan_id AND r.user_id=current_setting('intent.user_id',true))"
        elif table == "result_files":
            clause += " AND EXISTS(SELECT 1 FROM conversations c WHERE c.id=result_files.conversation_id AND c.user_id=current_setting('intent.user_id',true))"
        if table == "management_traces":
            op.execute(f"CREATE POLICY anonymous_write_trace ON {table} FOR INSERT WITH CHECK ({clause})")
        elif table in {"functions","function_versions","agents","agent_releases","assets","documents","document_versions","domain_packages","domain_versions","workspace_governance",
                       "action_definitions","action_definition_versions","business_skills","business_skill_versions","model_profiles","model_profile_versions"}:
            op.execute(f"CREATE POLICY anonymous_read ON {table} FOR SELECT USING ({clause})")
        else:
            op.execute(f"CREATE POLICY anonymous_owner ON {table} USING ({clause}) WITH CHECK ({clause})")
    op.execute("CREATE POLICY anonymous_audit_insert ON audit FOR INSERT WITH CHECK (public.intent_guest_workspace(workspace_id) AND user_id=current_setting('intent.user_id',true))")


def downgrade():
    raise RuntimeError("Use an authorized backup; preserve credentials, identity audits and historical records")
