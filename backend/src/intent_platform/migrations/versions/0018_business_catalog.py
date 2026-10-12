"""Versioned platform defaults and space-owned authoring metadata."""
import json
from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import make_url
from intent_platform.config import settings

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE business_catalog_items (
        id text PRIMARY KEY, workspace_id text REFERENCES workspaces(id), kind text NOT NULL CHECK(kind IN ('object','action')),
        key text NOT NULL, definition jsonb NOT NULL, revision integer NOT NULL DEFAULT 1,
        latest_version integer NOT NULL DEFAULT 1, origin text NOT NULL,
        default_id text REFERENCES business_catalog_items(id), adopted_version integer,
        domain_package_id text REFERENCES domain_packages(id), action_definition_id text REFERENCES action_definitions(id),
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(workspace_id,default_id))""")
    op.execute("""CREATE TABLE business_catalog_versions (
        id text PRIMARY KEY, workspace_id text REFERENCES workspaces(id), item_id text NOT NULL REFERENCES business_catalog_items(id),
        version integer NOT NULL, definition jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(item_id,version))""")
    for table in ("business_catalog_items", "business_catalog_versions"):
        op.execute(f"CREATE INDEX ix_{table}_workspace_id ON {table}(workspace_id)")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        membership = f"EXISTS(SELECT 1 FROM memberships m WHERE m.workspace_id={table}.workspace_id AND m.user_id=current_setting('intent.user_id',true) AND m.active)"
        read = f"(workspace_id IS NULL AND NULLIF(current_setting('intent.user_id',true),'') IS NOT NULL) OR {membership}"
        write = f"(workspace_id IS NULL AND current_setting('intent.catalog_admin',true)='true') OR {membership}"
        op.execute(f"CREATE POLICY catalog_read ON {table} FOR SELECT USING ({read})")
        for operation in ("INSERT", "UPDATE", "DELETE"):
            policy = f"CREATE POLICY catalog_{operation.lower()} ON {table} FOR {operation} "
            policy += f"WITH CHECK ({write})" if operation == "INSERT" else f"USING ({write})" + (f" WITH CHECK ({write})" if operation == "UPDATE" else "")
            op.execute(policy)
    op.execute("CREATE TRIGGER immutable_version BEFORE UPDATE OR DELETE ON business_catalog_versions FOR EACH ROW EXECUTE FUNCTION intent_immutable()")
    op.execute("""CREATE POLICY platform_catalog_audit ON audit
        USING(workspace_id='@platform' AND current_setting('intent.catalog_admin',true)='true')
        WITH CHECK(workspace_id='@platform' AND current_setting('intent.catalog_admin',true)='true')""")
    bind = op.get_bind()
    defaults = [("object", "business:" + key, name, None) for key, name in
                [("Customer", "客户"), ("Account", "账户"), ("Product", "产品"), ("Task", "任务"), ("Document", "文档"), ("Department", "部门"), ("Employee", "员工")]]
    defaults += [("action", key, name, effect) for key, name, effect in
                 [("READ", "查询", "READ_ONLY"), ("LIST", "列表筛选", "READ_ONLY"), ("COMPARE", "比较", "READ_ONLY"),
                  ("SUMMARIZE", "汇总", "READ_ONLY"), ("GENERATE", "生成草稿", "READ_ONLY"), ("CREATE", "创建", "WRITE")]]
    for kind, key, name, effect in defaults:
        identifier = "catalog_default_" + kind + "_" + key.replace(":", "_")
        definition = json.dumps({"name": name, "description": "默认业务" + ("对象：" if kind == "object" else "动作：") + name,
                                 "effect": effect, "enabled": True}, ensure_ascii=False)
        bind.execute(text("INSERT INTO business_catalog_items(id,kind,key,definition,origin) VALUES(:id,:kind,:key,CAST(:definition AS jsonb),'platform')"),
                     {"id": identifier, "kind": kind, "key": key, "definition": definition})
    # Preserve stable IDs and original dependency packages. No merge by label;
    # imported concepts remain distinct even when human names coincide.
    op.execute("""INSERT INTO business_catalog_items(id,workspace_id,kind,key,definition,origin,domain_package_id,created_at)
      SELECT 'catalog_legacy_object_'||p.id||'_'||encode(convert_to(c->>'id','UTF8'),'hex'),p.workspace_id,'object',c->>'id',
        jsonb_build_object('name',c->>'label','description',p.definition->>'description','effect',NULL,'enabled',true),
        'legacy',p.id,v.created_at FROM domain_packages p JOIN domain_versions v ON v.package_id=p.id AND v.version=p.latest_version,
        LATERAL jsonb_array_elements(v.definition->'concepts') c WHERE c->>'kind'='object'""")
    op.execute("""INSERT INTO business_catalog_items(id,workspace_id,kind,key,definition,origin,action_definition_id,created_at)
      SELECT 'catalog_legacy_action_'||a.id,a.workspace_id,'action',a.key,
        jsonb_build_object('name',v.definition->>'name','description',v.definition->>'description','effect',v.definition->>'effect','enabled',true),
        'legacy',a.id,v.created_at FROM action_definitions a JOIN action_definition_versions v ON v.action_id=a.id AND v.version=a.latest_version""")
    op.execute("""INSERT INTO business_catalog_versions(id,workspace_id,item_id,version,definition,created_at)
       SELECT 'catalog_version_'||id,workspace_id,id,1,definition,created_at FROM business_catalog_items""")
    app_role = make_url(settings().database_url).username
    if app_role:
        if not app_role.replace("_", "").isalnum(): raise RuntimeError("Invalid runtime role")
        op.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON business_catalog_items,business_catalog_versions TO "{app_role}"')


def downgrade():
    raise RuntimeError("Restore an authorized backup; catalogue adoption history must be preserved")
