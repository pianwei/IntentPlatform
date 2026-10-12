"""Inventory and transactionally retire explicitly proven local synthetic data.

No network targets or credentials are written to the manifest. Main application
must be stopped and backup restore verified before --apply is used.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import create_engine, MetaData, select, text, or_, and_, delete
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from intent_platform.demo.catalog import function_packages
from intent_platform.demo.skills import business_skill_templates

HTTP_FIXTURE_ID = "fn_c3346cce7b0442bcb2953937e8d33b5c"  # INT-011-source-import.md
REVIEWED_FIXTURES = {
    'fn_369d232417b549e8908484e0a15eca5c': 'legacy seed; fixture validation audit',
    'fn_683ed975535d451d84d42b76a9a584a5': 'legacy seed; fixture validation audit',
    'fn_a33647534c514b6096c30a857ec5196f': 'INT-023 interaction fixture; validation audit',
}
IMMUTABLE_TABLES = {'function_versions', 'agent_releases', 'business_skill_versions', 'domain_versions', 'action_definition_versions'}
PROTECTED = {"local_accounts", "local_credentials", "integration_keys", "memberships", "workspaces",
             "model_profiles", "model_profile_versions", "workspace_governance", "assets", "login_throttles"}


def _references(value, identifiers):
    if isinstance(value, str):
        return value in identifiers or bool(set(re.split(r'[^A-Za-z0-9_-]+', value)) & identifiers)
    if isinstance(value, list): return any(_references(v, identifiers) for v in value)
    if isinstance(value, dict): return any(_references(k, identifiers) or _references(v, identifiers) for k,v in value.items())
    return False


def _resource_ids(targets):
    # demo_states uses the retained workspace ID as PK, not a retired resource.
    return set().union(*(values for name,values in targets.items() if name != 'demo_states')) if targets else set()


def _rows(conn, name):
    name = conn.dialect.identifier_preparer.quote(name)
    return conn.execute(text(f'SELECT to_jsonb(t) FROM {name} t')).scalars().all()


def retained_rows(conn, tables, targets):
    """Compare full values in memory; do not print configuration or credentials."""
    result = {}
    for name,table in tables.items():
        selected = targets.get(name, set())
        primary = list(table.primary_key.columns)[0].name if selected else None
        values = [r for r in _rows(conn,name) if not primary or r[primary] not in selected]
        result[name] = sorted(json.dumps(r,sort_keys=True,ensure_ascii=False) for r in values)
    return result


def check_reverse_dependencies(conn, tables, targets):
    identifiers = _resource_ids(targets)
    for name,table in tables.items():
        primary = list(table.primary_key.columns)[0].name if name in targets else None
        for row in _rows(conn,name):
            if primary and row[primary] in targets[name]: continue
            if _references(row,identifiers):
                raise ValueError(f'Retained resource in {name} references retirement scope')


def trigger_state(conn):
    return [tuple(r) for r in conn.execute(text("SELECT c.relname,t.tgname,t.tgenabled,p.proname FROM pg_trigger t "
        "JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid "
        "WHERE NOT t.tgisinternal AND n.nspname='public' ORDER BY c.relname,t.tgname"))]


def inventory(conn, workspace):
    metadata = MetaData(); metadata.reflect(conn)
    tables = metadata.tables
    targets = {}
    def take(table, clause):
        if table not in tables: return set()
        t = tables[table]
        pk = list(t.primary_key.columns)
        if len(pk) != 1: raise ValueError("Cleanup requires an explicit single primary key")
        ids = set(conn.scalars(select(pk[0]).where(clause)))
        if ids: targets.setdefault(table, set()).update(ids)
        return ids
    f=tables['functions']
    rows=list(conn.execute(select(f).where(f.c.workspace_id==workspace)).mappings())
    known_skills={s['key'] for s in business_skill_templates()}
    known_keys={p['key'] for p in function_packages(include_extension=True)} | {k.replace('demo.skill.','demo.composite.') for k in known_skills}
    fixture_audits={r['resource_id'] for r in conn.execute(select(tables['audit']).where(and_(
        tables['audit'].c.workspace_id==workspace,tables['audit'].c.action=='function.validate'))).mappings()
        if r['detail'].get('fixture') is True and r['detail'].get('valid') is True}
    proofs={}
    for row in rows:
        connector=row['definition'].get('connector',{}); workflow=connector.get('workflow_id')
        if connector.get('kind')=='fixture' and workflow in known_keys:
            proofs[row['id']]='registered legacy synthetic workflow: '+workflow
        elif row['id'] in REVIEWED_FIXTURES and connector.get('kind')=='fixture' and row['id'] in fixture_audits:
            proofs[row['id']]=REVIEWED_FIXTURES[row['id']]
        elif row['id']==HTTP_FIXTURE_ID:
            if connector.get('kind')!='http' or not row['disabled']:
                raise ValueError('Reviewed HTTP fixture changed; re-audit before retirement')
            proofs[row['id']]='disabled local fixture: docs/project/records/INT-011-source-import.md'
    function_ids=set(proofs)
    if not function_ids: raise ValueError("No proven old synthetic functions")
    targets['functions']=function_ids
    a=tables['agents']; agent_rows=list(conn.execute(select(a).where(a.c.workspace_id==workspace)).mappings())
    release=tables['agent_releases']
    release_rows=list(conn.execute(select(release).where(release.c.workspace_id==workspace)).mappings())
    agent_ids=set()
    for agent in agent_rows:
        refs=set(agent['capability_ids'])
        for r in release_rows:
            if r['agent_id']==agent['id']: refs.update(c['capability_id'] for c in r['snapshot'].get('capabilities',[]))
        if refs & function_ids:
            if refs - function_ids: raise ValueError("An agent mixes retained and retired capabilities; resolve explicitly")
            agent_ids.add(agent['id'])
    targets['agents']=agent_ids
    releases=take('agent_releases',release.c.agent_id.in_(agent_ids))
    conversations=take('conversations',tables['conversations'].c.agent_id.in_(agent_ids))
    runs=take('runs',tables['runs'].c.conversation_id.in_(conversations))
    calls=take('calls',tables['calls'].c.run_id.in_(runs))
    # External UNKNOWN/in-flight business must not disappear through demo cleanup.
    for c in conn.execute(select(tables['calls']).where(tables['calls'].c.id.in_(calls))).mappings():
        if c['status'] in {'EXECUTING','RECONCILING','UNKNOWN'} and c['definition']['connector']['kind']!='fixture':
            raise ValueError("An external call remains in flight or UNKNOWN")
    plans=take('execution_plans',tables['execution_plans'].c.run_id.in_(runs))
    take('plan_steps',tables['plan_steps'].c.plan_id.in_(plans))
    for name in ('result_files','events','management_traces','processing_attempts'):
        take(name,tables[name].c.run_id.in_(runs))
    sessions=take('runtime_sessions',tables['runtime_sessions'].c.agent_id.in_(agent_ids))
    take('handshakes',tables['handshakes'].c.agent_id.in_(agent_ids))
    ticket=tables['embed_tickets']; ticket_rows=list(conn.execute(select(ticket).where(ticket.c.agent_id.in_(agent_ids))).mappings())
    take('embed_tickets',ticket.c.agent_id.in_(agent_ids))
    broker_ids={t['source_token_id'] for t in ticket_rows}
    tokens=tables['auth_tokens']
    token_ids=take('auth_tokens',and_(tokens.c.scope.in_(['runtime','integration']),or_(tokens.c.runtime_session_id.in_(sessions),
         tokens.c.agent_id.in_(agent_ids),tokens.c.id.in_(broker_ids))))
    while True:
        descendants=take('auth_tokens',and_(tokens.c.scope.in_(['runtime','integration']),tokens.c.parent_token_id.in_(token_ids)))
        if descendants <= token_ids: break
        token_ids |= descendants
    take('anonymous_principals',tables['anonymous_principals'].c.agent_id.in_(agent_ids))
    take('function_versions',tables['function_versions'].c.function_id.in_(function_ids))
    # Exclusive legacy rule/domain references; other workspaces are never touched.
    skill=tables['business_skills']
    skill_ids=take('business_skills',and_(skill.c.workspace_id==workspace,skill.c.key.in_(known_skills)))
    take('business_skill_versions',tables['business_skill_versions'].c.skill_id.in_(skill_ids))
    retained=[r for r in rows if r['id'] not in function_ids]
    retained_definitions=[r['definition'] for r in retained]+list(conn.scalars(select(tables['function_versions'].c.definition).where(and_(
        tables['function_versions'].c.workspace_id==workspace,~tables['function_versions'].c.function_id.in_(function_ids)))))
    domains=tables['domain_packages']
    for domain in conn.execute(select(domains).where(and_(domains.c.workspace_id==workspace,domains.c.key=='demo-banking-v1'))).mappings():
        if any(_references(d,{domain['id']}) for d in retained_definitions):
            raise ValueError("Legacy domain is referenced by retained functions")
        targets.setdefault('domain_packages',set()).add(domain['id'])
    take('domain_versions',tables['domain_versions'].c.package_id.in_(targets.get('domain_packages',set())))
    # Shared action definitions are retained unless tied only to the retired catalog.
    legacy_actions={f['definition'].get('action') for f in rows if f['id'] in function_ids}
    remaining_actions={d.get('action') for d in retained_definitions}
    action=tables['action_definitions']
    action_ids=take('action_definitions',and_(action.c.workspace_id==workspace,action.c.key.in_(legacy_actions-remaining_actions)))
    take('action_definition_versions',tables['action_definition_versions'].c.action_id.in_(action_ids))
    take('demo_states',tables['demo_states'].c.workspace_id==workspace)
    old_ids=_resource_ids(targets)
    for name,field in [('evaluations','result'),('audit','detail')]:
        table=tables[name]
        for row in conn.execute(select(table).where(table.c.workspace_id==workspace)).mappings():
            if _references(row[field],old_ids) or row.get('resource_id') in old_ids:
                targets.setdefault(name,set()).add(row['id'])
    if 'identity_audit' in tables:
        table=tables['identity_audit']
        take('identity_audit',and_(table.c.action=='key.visitor_logout',table.c.subject_id.in_(sessions)))
    check_reverse_dependencies(conn,tables,targets)
    return {'format':'intent-retirement-v2','workspace_id':workspace,
            'tables':{name:sorted(values) for name,values in sorted(targets.items())},
            'agents':[{'id':r['id'],'name':r['name'],'public_id':r['public_id']} for r in sorted(agent_rows,key=lambda r:r['id']) if r['id'] in agent_ids],
            'function_count':len(function_ids),'proofs':{k:proofs[k] for k in sorted(proofs)},'reverse_dependencies_checked':True}


def apply(conn, manifest, inject_failure=False):
    if not conn.in_transaction(): raise ValueError('Retirement requires one explicit transaction')
    metadata=MetaData();metadata.reflect(conn)
    table_ids=manifest['tables']
    if set(table_ids)&PROTECTED: raise ValueError('Protected resource in retirement manifest')
    quote=conn.dialect.identifier_preparer.quote
    for name in sorted(metadata.tables):
        conn.execute(text(f'LOCK TABLE {quote(name)} IN ACCESS EXCLUSIVE MODE'))
    current=inventory(conn,manifest['workspace_id'])
    if current!=manifest: raise ValueError('Retirement inventory changed; regenerate and review exact scope')
    preserved=retained_rows(conn,metadata.tables,table_ids)
    triggers_before=trigger_state(conn)
    disabled=[]
    for table,name,enabled,function in triggers_before:
        if table in table_ids and table in IMMUTABLE_TABLES:
            if name!='immutable_version' or function!='intent_immutable' or enabled!='O':
                raise ValueError('Unexpected immutable-version trigger configuration')
            disabled.append((table,name))
    if {t for t,_ in disabled} != set(table_ids)&IMMUTABLE_TABLES:
        raise ValueError('Required immutable protection is missing')
    for table,name in disabled: conn.execute(text(f'ALTER TABLE {quote(table)} DISABLE TRIGGER {quote(name)}'))
    counts={}
    for table in reversed(metadata.sorted_tables):
        if table.name not in table_ids: continue
        primary=list(table.primary_key.columns)
        if len(primary)!=1: raise ValueError('Unsupported retirement primary key')
        result=conn.execute(delete(table).where(primary[0].in_(table_ids[table.name])))
        if result.rowcount!=len(table_ids[table.name]): raise ValueError('Retirement row count changed')
        counts[table.name]=result.rowcount
        if inject_failure and len(counts)==3: raise RuntimeError('Injected retirement rollback test')
    for table,name in disabled: conn.execute(text(f'ALTER TABLE {quote(table)} ENABLE TRIGGER {quote(name)}'))
    if trigger_state(conn)!=triggers_before: raise ValueError('Immutable trigger state changed')
    for name,ids in table_ids.items():
        table=metadata.tables[name];primary=list(table.primary_key.columns)[0]
        if conn.scalar(select(primary).where(primary.in_(ids)).limit(1)) is not None: raise ValueError('Retired rows remain')
    if retained_rows(conn,metadata.tables,{})!=preserved: raise ValueError('Retained rows or configuration values changed')
    check_reverse_dependencies(conn,metadata.tables,table_ids)
    return counts


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--workspace',default='ws-dev');parser.add_argument('--output',required=True)
    parser.add_argument('--apply');parser.add_argument('--restore-report');parser.add_argument('--maintenance',action='store_true')
    parser.add_argument('--database',required=True,help='Exact database confirmation matching connection')
    args=parser.parse_args()
    out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
    if out.exists(): raise SystemExit('Refusing to overwrite an existing evidence file')
    url=os.environ.get('INTENT_LOCAL_ADMIN_DATABASE_URL','')
    parsed=make_url(url)
    if parsed.drivername!='postgresql+psycopg' or parsed.host not in {'127.0.0.1','localhost','postgres'}: raise SystemExit('Local PostgreSQL required')
    if parsed.database!=args.database or not re.fullmatch(r'intent(?:_[a-z0-9_]+)?',args.database):
        raise SystemExit('Exact local database confirmation required')
    engine=create_engine(url)
    if args.apply:
        if not args.maintenance or not args.restore_report: raise SystemExit('Maintenance window and successful restore evidence required')
        evidence=json.loads(Path(args.restore_report).read_text(encoding='utf-8-sig'))
        if evidence.get('restored') is not True or args.database not in {evidence.get('source'),evidence.get('target')}:
            raise SystemExit('Restore has not been verified for this database')
        manifest=json.loads(Path(args.apply).read_text(encoding='utf-8-sig'))
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout='5s'"))
            counts=apply(conn,manifest)
        result={'database':args.database,'deleted':counts,'zero_residual_targets':True,'retained_full_values_unchanged':True,
                'immutable_triggers_restored':True,'completed_at':datetime.now(timezone.utc).isoformat()}
    else:
        with engine.connect() as conn: result=inventory(conn,args.workspace)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(out),'tables':len(result.get('tables',result.get('deleted',{})))},ensure_ascii=False))


if __name__=='__main__': main()
