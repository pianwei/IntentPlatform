"""Immutable document sources and retained API import diagnostics."""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE functions ADD COLUMN source_state jsonb NOT NULL DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE documents ADD COLUMN latest_revision integer NOT NULL DEFAULT 1")
    op.execute("CREATE TABLE document_versions(id text PRIMARY KEY,workspace_id text NOT NULL REFERENCES workspaces(id),document_id text NOT NULL REFERENCES documents(id),revision integer NOT NULL,filename text NOT NULL,text text NOT NULL,original bytea,sections jsonb NOT NULL,created_by text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(document_id,revision))")
    op.execute("INSERT INTO document_versions(id,workspace_id,document_id,revision,filename,text,original,sections,created_by) SELECT 'legacy_'||id,workspace_id,id,1,filename,text,NULL,jsonb_build_array(jsonb_build_object('id','legacy-text','heading','Legacy extracted text','start_line',1,'end_line',array_length(string_to_array(text,E'\\n'),1),'text',text)),'legacy-unknown' FROM documents")
    op.execute("CREATE TABLE openapi_imports(id text PRIMARY KEY,workspace_id text NOT NULL REFERENCES workspaces(id),document_id text NOT NULL REFERENCES documents(id),document_revision integer NOT NULL,parser_version text NOT NULL,report jsonb NOT NULL,created_by text NOT NULL,created_at timestamptz NOT NULL DEFAULT now())")
    for table in ("document_versions", "openapi_imports"):
        op.execute(f"CREATE INDEX ix_{table}_workspace_id ON {table}(workspace_id)")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        clause = f"EXISTS(SELECT 1 FROM memberships m WHERE m.workspace_id={table}.workspace_id AND m.user_id=current_setting('intent.user_id',true) AND m.active)"
        op.execute(f"CREATE POLICY tenant_membership ON {table} USING ({clause}) WITH CHECK ({clause})")
        op.execute(f"CREATE TRIGGER immutable_version BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION intent_immutable()")


def downgrade():
    raise RuntimeError("Restore an authorized backup; do not discard immutable source evidence")
