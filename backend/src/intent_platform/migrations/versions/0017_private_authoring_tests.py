"""Exclude private authoring runs from public applications and ordinary logs."""
from alembic import op
from sqlalchemy import text

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    # Keep the already deployed narrow signatures, grants and archive gates.
    # Replace only a known WHERE anchor in each immutable migration-era body.
    changes = {
        "intent_public_workspace(text)": ("AND a.current_release_id IS NOT NULL", "AND a.current_release_id IS NOT NULL AND NOT (a.runtime_ui ? 'authoring_test')"),
        "intent_public_app(text)": ("WHERE a.public_id=requested_public_id", "WHERE a.public_id=requested_public_id AND NOT (a.runtime_ui ? 'authoring_test') AND NOT (r.snapshot ? 'authoring_test')"),
        "intent_embed_agent(text)": ("WHERE (a.id=requested_id OR a.public_id=requested_id)", "WHERE (a.id=requested_id OR a.public_id=requested_id) AND NOT (a.runtime_ui ? 'authoring_test') AND NOT (r.snapshot ? 'authoring_test')"),
        "intent_public_synthetic_catalog(text)": ("WHERE a.public_id=pid", "WHERE a.public_id=pid AND NOT (a.runtime_ui ? 'authoring_test') AND NOT (r.snapshot ? 'authoring_test')"),
        "intent_admin_conversations(text,text,text,text,timestamptz,timestamptz,integer,integer)": ("WHERE c.workspace_id=requested_ws", "WHERE c.workspace_id=requested_ws AND NOT (a.runtime_ui ? 'authoring_test')"),
        "intent_admin_conversation_detail(text,text,integer,integer,text)": ("WHERE c.id=requested_conv", "WHERE c.id=requested_conv AND NOT (a.runtime_ui ? 'authoring_test')"),
        "intent_admin_trace_runs(text,text,text,text,timestamptz,timestamptz,integer)": ("WHERE r.workspace_id = requested_ws AND c.workspace_id = requested_ws", "WHERE r.workspace_id = requested_ws AND c.workspace_id = requested_ws AND NOT EXISTS (SELECT 1 FROM public.agents ta WHERE ta.id=c.agent_id AND ta.runtime_ui ? 'authoring_test')"),
        "intent_admin_trace_detail(text,text)": ("WHERE r.workspace_id = requested_ws AND c.workspace_id = requested_ws", "WHERE r.workspace_id = requested_ws AND c.workspace_id = requested_ws AND NOT EXISTS (SELECT 1 FROM public.agents ta WHERE ta.id=c.agent_id AND ta.runtime_ui ? 'authoring_test')"),
        "intent_admin_log_file(text,text,text)": ("WHERE f.id=requested_file", "WHERE f.id=requested_file AND NOT EXISTS (SELECT 1 FROM public.conversations tc JOIN public.agents ta ON ta.id=tc.agent_id WHERE tc.id=f.conversation_id AND ta.runtime_ui ? 'authoring_test')"),
    }
    bind = op.get_bind()
    for signature, (old, new) in changes.items():
        ddl = bind.scalar(text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"), {"signature": "public." + signature})
        if old not in ddl:
            raise RuntimeError("Private test projection anchor differs: " + signature)
        op.execute(ddl.replace(old, new))


def downgrade():
    raise RuntimeError("Restore an authorized backup; private test records must remain inaccessible")
