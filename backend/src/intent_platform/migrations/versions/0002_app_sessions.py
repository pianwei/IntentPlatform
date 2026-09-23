"""Application configuration, explicit access and renewable runtime sessions.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    statements = [
        "ALTER TABLE agents ADD COLUMN access_mode text NOT NULL DEFAULT 'workspace'",
        "UPDATE agents SET access_mode='allowlist' WHERE jsonb_array_length(allowed_users)>0",
        "ALTER TABLE agents ADD COLUMN public_id text NOT NULL DEFAULT gen_random_uuid()::text UNIQUE",
        "ALTER TABLE agents ADD COLUMN runtime_ui jsonb NOT NULL DEFAULT '{}'::jsonb",
        "ALTER TABLE agents ADD COLUMN channels jsonb NOT NULL DEFAULT '{\"chat\": true,\"embed\": true}'::jsonb",
        "ALTER TABLE agents ADD COLUMN live_channels jsonb NOT NULL DEFAULT '{\"chat\": true,\"embed\": true}'::jsonb",
        "ALTER TABLE functions ADD COLUMN access_mode text NOT NULL DEFAULT 'workspace'",
        "ALTER TABLE functions ADD COLUMN allowed_users jsonb NOT NULL DEFAULT '[]'::jsonb",
        "UPDATE functions SET allowed_users=COALESCE(definition->'allowed_users','[]'::jsonb)",
        "UPDATE functions SET access_mode='allowlist' WHERE jsonb_array_length(allowed_users)>0",
        "CREATE TABLE runtime_sessions(id text PRIMARY KEY,workspace_id text NOT NULL REFERENCES workspaces(id),agent_id text NOT NULL REFERENCES agents(id),user_id text NOT NULL,channel text NOT NULL CHECK(channel IN ('chat','embed')),host_origin text,revoked boolean NOT NULL DEFAULT false,legacy_claimable boolean NOT NULL DEFAULT false,current_token_id text,created_at timestamptz NOT NULL DEFAULT now())",
        "CREATE INDEX ix_runtime_sessions_workspace_id ON runtime_sessions(workspace_id)",
        "CREATE INDEX ix_runtime_sessions_user_id ON runtime_sessions(user_id)",
        "ALTER TABLE auth_tokens ADD COLUMN runtime_session_id text REFERENCES runtime_sessions(id)",
        "INSERT INTO runtime_sessions(id,workspace_id,agent_id,user_id,channel,host_origin,revoked,current_token_id) SELECT 'session_'||gen_random_uuid()::text,a.workspace_id,t.agent_id,t.user_id,CASE WHEN t.delegation->>'host_origin' IS NULL THEN 'chat' ELSE 'embed' END,t.delegation->>'host_origin',t.revoked OR COALESCE(p.revoked,false) OR EXISTS(SELECT 1 FROM conversations c WHERE c.token_id=t.id AND c.status='revoked'),t.id FROM auth_tokens t JOIN agents a ON a.id=t.agent_id LEFT JOIN auth_tokens p ON p.id=t.parent_token_id WHERE t.scope='runtime'",
        "UPDATE auth_tokens t SET runtime_session_id=s.id FROM runtime_sessions s WHERE s.current_token_id=t.id",
        "ALTER TABLE conversations ADD COLUMN runtime_session_id text REFERENCES runtime_sessions(id)",
        "CREATE INDEX ix_conversations_runtime_session_id ON conversations(runtime_session_id)",
        "UPDATE conversations c SET runtime_session_id=t.runtime_session_id FROM auth_tokens t WHERE c.token_id=t.id",
        "INSERT INTO runtime_sessions(id,workspace_id,agent_id,user_id,channel,legacy_claimable) SELECT 'session_'||gen_random_uuid()::text,c.workspace_id,c.agent_id,c.user_id,'chat',true FROM conversations c JOIN auth_tokens t ON t.id=c.token_id LEFT JOIN auth_tokens p ON p.id=t.parent_token_id WHERE t.scope='management' AND NOT t.revoked AND NOT COALESCE(p.revoked,false) AND c.status='active' GROUP BY c.workspace_id,c.agent_id,c.user_id",
        "UPDATE conversations c SET runtime_session_id=s.id FROM runtime_sessions s,auth_tokens t WHERE c.token_id=t.id AND t.scope='management' AND NOT t.revoked AND c.status='active' AND s.legacy_claimable AND s.user_id=c.user_id AND s.agent_id=c.agent_id AND s.workspace_id=c.workspace_id AND NOT EXISTS(SELECT 1 FROM auth_tokens p WHERE p.id=t.parent_token_id AND p.revoked)",
        "ALTER TABLE conversations ADD COLUMN title text NOT NULL DEFAULT '新会话'",
        "ALTER TABLE conversations ADD COLUMN created_at timestamptz NOT NULL DEFAULT now()",
        "ALTER TABLE conversations ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now()",
        "ALTER TABLE embed_tickets ADD COLUMN runtime_session_id text",
        "CREATE TABLE assets(id text PRIMARY KEY,workspace_id text NOT NULL REFERENCES workspaces(id),media_type text NOT NULL,data bytea NOT NULL)",
        "CREATE INDEX ix_assets_workspace_id ON assets(workspace_id)",
        "CREATE TABLE result_files(id text PRIMARY KEY,workspace_id text NOT NULL REFERENCES workspaces(id),conversation_id text NOT NULL REFERENCES conversations(id),run_id text NOT NULL REFERENCES runs(id),call_id text NOT NULL REFERENCES calls(id),filename text NOT NULL,media_type text NOT NULL,data bytea NOT NULL)",
        "CREATE INDEX ix_result_files_workspace_id ON result_files(workspace_id)",
    ]
    for statement in statements:
        op.execute(statement)
    for table in ("runtime_sessions", "assets", "result_files"):
        clause = f"EXISTS (SELECT 1 FROM memberships m WHERE m.workspace_id={table}.workspace_id AND m.user_id=current_setting('intent.user_id',true) AND m.active)"
        if table == "runtime_sessions":
            clause += " AND user_id=current_setting('intent.user_id',true)"
        if table == "result_files":
            clause += " AND EXISTS (SELECT 1 FROM runs r WHERE r.id=result_files.run_id AND r.user_id=current_setting('intent.user_id',true))"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_membership ON {table} USING ({clause}) WITH CHECK ({clause})")
    # Deliberately narrow public projection; no definitions, origins, access lists, or user data.
    op.execute("""CREATE FUNCTION intent_public_app(requested_public_id text)
    RETURNS TABLE(agent_id text, public_id text, release_id text, runtime_ui jsonb, channels jsonb, fixture_mode boolean)
    LANGUAGE sql SECURITY DEFINER SET search_path=public,pg_temp AS $$
    SELECT a.id::text,a.public_id::text,r.id::text,
      COALESCE(r.snapshot->'runtime_ui','{}'::jsonb),
      jsonb_build_object('chat',COALESCE((r.snapshot->'channels'->>'chat')::boolean,true) AND (a.live_channels->>'chat')::boolean,
                         'embed',COALESCE((r.snapshot->'channels'->>'embed')::boolean,true) AND (a.live_channels->>'embed')::boolean),
      (r.snapshot->>'fixture_mode')::boolean
    FROM agents a JOIN agent_releases r ON r.id=a.current_release_id WHERE a.public_id=requested_public_id $$""")
    op.execute("""CREATE OR REPLACE FUNCTION intent_embed_agent(requested_id text)
    RETURNS TABLE(workspace_id text,allowed_origins jsonb)
    LANGUAGE sql SECURITY DEFINER SET search_path=public,pg_temp AS $$
    SELECT a.workspace_id::text,COALESCE((SELECT jsonb_agg(o) FROM jsonb_array_elements(r.snapshot->'allowed_origins') o
      WHERE NOT a.revoked_origins @> jsonb_build_array(o)),'[]'::jsonb)
    FROM agents a JOIN agent_releases r ON r.id=a.current_release_id
    WHERE (a.id=requested_id OR a.public_id=requested_id)
      AND COALESCE((r.snapshot->'channels'->>'embed')::boolean,true) AND (a.live_channels->>'embed')::boolean $$""")
    op.execute("""CREATE FUNCTION intent_public_icon(requested_public_id text)
    RETURNS TABLE(media_type text,data bytea)
    LANGUAGE sql SECURITY DEFINER SET search_path=public,pg_temp AS $$
    SELECT i.media_type::text,i.data FROM agents a JOIN agent_releases r ON r.id=a.current_release_id
    JOIN assets i ON i.id=r.snapshot->'runtime_ui'->'icon'->>'value' AND i.workspace_id=a.workspace_id
    WHERE a.public_id=requested_public_id AND r.snapshot->'runtime_ui'->'icon'->>'kind'='asset'
      AND ((COALESCE((r.snapshot->'channels'->>'chat')::boolean,true) AND (a.live_channels->>'chat')::boolean)
        OR (COALESCE((r.snapshot->'channels'->>'embed')::boolean,true) AND (a.live_channels->>'embed')::boolean)) $$""")


def downgrade():
    raise RuntimeError("This migration preserves sessions and artifacts; restore a verified backup to downgrade")
