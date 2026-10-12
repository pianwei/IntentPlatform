"""Verify 0011 -> 0013 on an isolated restoration, comparing historical values directly."""
import json
import os
import re
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
from intent_platform.config import settings
from intent_platform.migrate import main as migrate

def main():
 cfg=settings(); urls=[make_url(u) for u in (cfg.database_url,cfg.migration_database_url,os.environ["INTENT_LOCAL_ADMIN_DATABASE_URL"])]
 if cfg.env!="test" or not re.fullmatch(r"intent_[a-z0-9_]+_test",urls[0].database or "") or len({(u.host,u.port,u.database) for u in urls})!=1:
  raise RuntimeError("Use only the isolated identity upgrade database")
 admin=create_engine(urls[2]);app=create_engine(urls[0])
 tables=["agent_releases","function_versions","business_skill_versions","model_profile_versions","conversations","runs","events","calls"]
 canonical=lambda rows: sorted(json.dumps(r,sort_keys=True,default=str) for r in rows)
 with admin.connect() as db:
  assert db.scalar(text("SELECT version_num FROM alembic_version"))=="0011"
  before={t:db.execute(text(f'SELECT to_jsonb(t) FROM "{t}" t')).scalars().all() for t in tables}
  assert before["agent_releases"] and before["conversations"]
  old_tokens=db.execute(text("SELECT id,user_id FROM auth_tokens WHERE NOT revoked LIMIT 3")).mappings().all()
 migrate()
 with admin.connect() as db:
  assert db.scalar(text("SELECT version_num FROM alembic_version"))=="0013"
  for t,rows in before.items():
   assert canonical(db.execute(text(f'SELECT to_jsonb(t) FROM "{t}" t')).scalars().all())==canonical(rows),t
  assert not db.execute(text("SELECT workspace_id,name FROM agents GROUP BY workspace_id,name HAVING count(*)>1")).all()
  assert db.scalar(text("SELECT count(*) FROM auth_tokens WHERE source='local'"))==0
  assert db.scalar(text("SELECT count(*) FROM local_accounts"))==0
 with app.begin() as db:
  assert db.scalar(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")) is False
  db.execute(text("SELECT set_config('intent.user_id','unrelated-upgrade-user',true)"))
  assert db.scalar(text("SELECT count(*) FROM conversations"))==0
  assert db.scalar(text("SELECT count(*) FROM agent_releases"))==0
 from sqlalchemy.orm import Session
 from intent_platform.db import engine,set_identity
 from intent_platform.auth import token_valid
 from intent_platform.models import AuthToken
 with Session(engine) as db:
  for row in old_tokens:
   set_identity(db,row["user_id"]);assert token_valid(db,db.get(AuthToken,row["id"])) is False
 print(json.dumps({"status":"passed","migration":"0011->0012->0013","restored_historical_rows":sum(map(len,before.values())),"preserved_tables":tables,"preserved_releases":len(before["agent_releases"]),"preserved_conversations":len(before["conversations"]),"historical_values_unchanged":True,"legacy_tokens_rejected":True,"restricted_role_isolation":True,"acceptance_evidence":False}))
if __name__=="__main__":main()
