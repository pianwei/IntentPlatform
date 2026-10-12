"""Frozen Zhiyu cases through real model, API and Worker in an isolated clone.

Credentials stay in memory. Only the explicitly registered synthetic catalog is
sent to the public model. This command refuses the main database.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid
from collections import Counter

ROOT = Path(__file__).resolve().parents[2]
# A candidate image can supply frozen code while /work supplies credentials and
# evidence. Only the controlled image path or this repository is permitted.
code_parser = argparse.ArgumentParser(add_help=False)
code_parser.add_argument('--code-root', choices=['/app'])
code_args, _ = code_parser.parse_known_args()
sys.path.insert(0, str(Path(code_args.code_root) / 'src' if code_args.code_root else ROOT / 'backend/src'))


def configure(database, database_host='127.0.0.1'):
    if not database.startswith(('intent_int026_', 'intent_int029_')) or database == 'intent':
        raise ValueError('An INT-026/INT-029 isolated database is required')
    for line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            if key.startswith('INTENT_'): os.environ[key] = value.strip().strip('"').strip("'")
    address='postgres:5432' if database_host=='postgres' else '127.0.0.1:54329'
    os.environ.update(INTENT_ENV='development', INTENT_IDENTITY_MODE='local', INTENT_ALLOW_DEV_AUTH='false',
        INTENT_MODEL_MODE='fixture', INTENT_ALLOW_PUBLIC_DEEPSEEK_MODEL_TEST='true',
        INTENT_DATABASE_URL=f'postgresql+psycopg://intent_app:local-app-only@{address}/{database}',
        INTENT_MIGRATION_DATABASE_URL=f'postgresql+psycopg://intent_owner:local-owner-only@{address}/{database}')


def path_value(value, path):
    for part in path.split('.'):
        if part == 'length': value = len(value)
        elif isinstance(value, list): value = value[int(part)]
        else: value = value[part]
    return value


def assess(case, observation):
    failures=[]; expected=case['expected_status']; status=observation['status']
    if (status == 'SUCCEEDED') if expected == 'NO_SUCCESS' else (status != expected):
        failures.append(f'status:{status}, expected:{expected}')
    if expected=='NO_SUCCESS' and observation['decision']=='ERROR': failures.append('technical error is not authorization evidence')
    calls=observation['calls']; keys=[c['key'] for c in calls]; assertions=case['assertions']
    if expected != 'NO_SUCCESS' and not set(case['expected_keys']).issubset(keys): failures.append('missing expected business capabilities')
    if case.get('allowed_keys') and set(keys)-set(case['allowed_keys']): failures.append('unrequested capabilities executed')
    if not case['allow_model_fallback'] and observation['answer_source']=='model': failures.append('model fallback concealed business request')
    if case['group']=='compound' and (observation['plan_source']!='skill' or observation['skill_key']!=case['expected_skill_key']):
        failures.append('wrong preset Skill')
    if case['group']=='autonomous' and (observation['plan_source']!='model' or observation['skill_key']):
        failures.append('autonomous plan did not originate from model')
    if case['group']=='fallback' and case['expected_source']=='model' and (calls or observation['plan_source'] or observation['answer_source']!='model'):
        failures.append('not an independent model answer')
    for key,value in assertions.items():
        if key.startswith('data.'):
            matching=[c['result'] or {} for c in calls if c['key'] in case['expected_keys']]
            def matches(result):
                try:
                    if key.endswith('.contains'): return value in path_value(result,key[:-9])
                    return path_value(result,key)==value
                except (KeyError,IndexError,TypeError,ValueError): return False
            if not any(matches(r) for r in matching): failures.append('result:'+key)
        elif key=='business_calls' and len(calls)!=value: failures.append('business call count')
        elif key=='business_calls_min' and len(calls)<value: failures.append('business call minimum')
        elif key=='confirmation_required' and value and not observation['confirmed']: failures.append('confirmation missing')
        elif key=='min_steps' and len(observation['steps'])<value: failures.append('insufficient decomposition')
        elif key=='requires_result_reference' and value and not any('$step' in json.dumps(s) or s.get('foreach') for s in observation['steps']): failures.append('no result reference')
        elif key=='files.min_count' and observation['file_count']<value: failures.append('missing draft download')
        elif key=='answer_min_length' and len(observation['answer'])<value: failures.append('empty answer')
        elif key=='answer_source' and observation['answer_source']!=value: failures.append('answer source')
        elif key=='business_answer_excludes':
            parts=observation['answer'].split('\n\n通用解释（模型回答）：\n',1)
            if len(parts)!=2 or not parts[0].startswith('业务结果：\n'):
                failures.append('mixed answer sections missing')
            elif any(term in parts[0] for term in value):
                failures.append('general explanation mixed into business facts')
        elif key=='question_kind' and observation.get('question_kind')!=value: failures.append('interaction kind')
        elif key=='option_count' and observation.get('option_count')!=value: failures.append('authorized option count')
        elif key=='product_ids' and set(value)!={c['arguments'].get('product_id') for c in calls}: failures.append('comparison objects')
        elif key=='no_model_success' and observation['answer_source']=='model': failures.append('fault masked by model')
        elif key=='write_count' and observation['write_count']!=value: failures.append('write count')
    return failures


def supplemental_cases():
    """Additional fixed boundary checks; never replace the original 106 cases."""
    rows = [
        ('mixed', '查星澜科技的存款余额，并用通俗语言解释什么是现金流', 'manager_a',
         ['customer.profile'], 'SUCCEEDED', {'answer_source': 'mixed', 'data.deposit': 3600000,
                                           'business_answer_excludes':['现金流']}),
        ('ambiguous', '查看远川制造客户资料', 'manager_a', ['customer.profile'],
         'WAITING_INPUT', {'business_calls': 0, 'question_kind':'entity_choice', 'option_count':2}),
        ('missing', '创建营销任务', 'manager_a', ['marketing.create'],
         'WAITING_INPUT', {'business_calls': 0}),
        ('manager-b', '查看海岚供应链客户资料', 'manager_b', ['customer.profile'],
         'SUCCEEDED', {'data.customer_id': 'ZY-C002'}),
    ]
    return [{'id': 'boundary-'+key, 'group': 'boundary', 'text': text, 'persona_id': persona,
             'expected_keys': ['zhiyu.'+k for k in keys] if status=='SUCCEEDED' else [],
             'allowed_keys': ['zhiyu.'+k for k in keys], 'expected_source': 'business',
             'expected_status': status, 'assertions': assertions, 'allow_model_fallback': False}
            for key,text,persona,keys,status,assertions in rows]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--database',default='intent_int026_candidate')
    parser.add_argument('--database-host',choices=['127.0.0.1','postgres'],default='127.0.0.1')
    parser.add_argument('--output',required=True);parser.add_argument('--ids',nargs='*');parser.add_argument('--limit',type=int)
    parser.add_argument('--supplemental',action='store_true')
    parser.add_argument('--document-skills',action='store_true')
    parser.add_argument('--code-root',choices=['/app'])
    args=parser.parse_args();out=Path(args.output)
    if out.exists(): raise SystemExit('Preserve earlier evidence; use a fresh output directory')
    out.mkdir(parents=True);configure(args.database,args.database_host)
    from intent_platform.migrate import main as migrate
    migrate()
    from fastapi.testclient import TestClient
    from sqlalchemy import select,func
    from sqlalchemy.orm import Session
    from intent_platform.main import app
    from intent_platform.db import engine,set_identity
    from intent_platform.models import ModelProfile,ModelProfileVersion,Run,Call,ExecutionPlan,BusinessSkillVersion,Event,ResultFile,SyntheticState,ManagementTrace,Agent,Release
    from intent_platform import worker,zhiyu,three_layer
    from intent_platform.zhiyu.evaluation import evaluation_cases
    credentials=json.loads((ROOT/'.local/INT021-admin.json').read_text(encoding='utf-8-sig'))
    cases=supplemental_cases() if args.supplemental else evaluation_cases()
    (out/'frozen-cases.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.ids: cases=[c for c in cases if c['id'] in args.ids]
    if args.limit: cases=cases[:args.limit]
    results=[]
    with TestClient(app) as client:
        def checked(response):
            if response.status_code>=400:
                try:
                    detail=response.json().get('detail',{})
                    evidence=detail.get('execution_test',{}) if isinstance(detail,dict) else {}
                    rid=evidence.get('run_id')
                    if rid:
                        with Session(engine) as db:
                            set_identity(db,user)
                            run=db.get(Run,rid)
                            if run and run.user_id==user:
                                plan=db.scalar(select(ExecutionPlan).where(ExecutionPlan.run_id==rid))
                                safe={'run_id':rid,'status':run.status,'decision':run.decision,
                                    'plan':{'source':plan.source,'steps':len(plan.snapshot.get('steps',[])),
                                        'final_output_mapping':plan.snapshot.get('final_output_mapping')} if plan else None,
                                    'errors':[{'code':e.payload.get('code'),'message':e.payload.get('message')} for e in db.scalars(select(Event).where(Event.run_id==rid,Event.event_type=='ERROR'))],
                                    'trace':[{'layer':t.layer,'kind':t.kind,'detail':t.detail} for t in db.scalars(select(ManagementTrace).where(ManagementTrace.run_id==rid,ManagementTrace.kind=='test_rejected'))],
                                    'calls':[{'name':c.definition['name'],'status':c.status,'error_code':(c.result or {}).get('error_code'),
                                        'dispatched':c.dispatched_at is not None} for c in db.scalars(select(Call).where(Call.run_id==rid))]}
                                (out/'authoring-failure.json').write_text(json.dumps(safe,ensure_ascii=False,indent=2),encoding='utf-8')
                except Exception as exc:
                    (out/'authoring-diagnostic-error.txt').write_text(type(exc).__name__,encoding='utf-8')
                raise ValueError(f'API rejected request ({response.status_code}): '+response.text[:400])
            return response.json()
        login=checked(client.post('/api/v2/auth/login',json={k:credentials[k] for k in ('username','password')}))
        headers={'Authorization':'Bearer '+login['access_token']};user=login['user_id']
        with Session(engine) as db:
            set_identity(db,user)
            models=list(db.scalars(select(ModelProfile).where(ModelProfile.workspace_id=='ws-dev',ModelProfile.disabled.is_(False))))
            matching=[]
            for candidate in models:
                candidate_version=db.scalar(select(ModelProfileVersion).where(ModelProfileVersion.profile_id==candidate.id,
                    ModelProfileVersion.version==candidate.latest_version))
                config=candidate_version.config if candidate_version else {}
                if config.get('model_version')=='DeepSeek-V4.1-Flash' and config.get('thinking_mode')=='disabled':
                    matching.append((candidate,candidate_version))
            if len(matching)!=1:
                raise ValueError('Exactly one active DeepSeek V4.1 Flash non-thinking profile is required')
            model,model_version=matching[0]
            model_id=model.id
            existing_agents=list(db.scalars(select(Agent).where(Agent.workspace_id=='ws-dev')))
            existing_agents=[a for a in existing_agents if (a.synthetic_catalog or {}).get('id')=='zhiyu-v1'
                             and not (a.runtime_ui or {}).get('authoring_test')]
            if len(existing_agents)>1:
                raise ValueError('Multiple published Zhiyu demo agents make evaluation selection ambiguous')
            if existing_agents:
                existing_agent=existing_agents[0]
                existing_release=db.get(Release,existing_agent.current_release_id) if existing_agent.current_release_id else None
                pinned=(existing_release.snapshot or {}).get('model_profile',{}) if existing_release else {}
                if existing_agent.model_profile_id!=model_id or not existing_release:
                    raise ValueError('Existing Zhiyu demo agent is not pinned to the requested model profile')
                if (pinned.get('profile_id')!=model_id or pinned.get('version')!=model_version.config.get('model_version')
                    or pinned.get('thinking_mode')!='disabled'):
                    raise ValueError('Existing Zhiyu release does not pin DeepSeek V4.1 Flash non-thinking')
                installed={'agent':{'id':existing_agent.id,'public_id':existing_agent.public_id},
                           'reused_existing_release':existing_release.version}
            else:
                install_path='/api/v2/management/workspaces/ws-dev/synthetic/zhiyu/install'
                install_body={'model_profile_id':model_id, 'document_skills':args.document_skills}
                approvals=[]
                for attempt in range(8):
                    response=client.post(install_path,headers=headers,json=install_body)
                    if response.status_code != 409:
                        installed=checked(response);break
                    detail=response.json().get('detail',{})
                    tested=detail.get('execution_test',{}) if isinstance(detail,dict) else {}
                    preview=tested.get('confirmation')
                    if not args.document_skills or not isinstance(preview,dict) or tested.get('status')!='WAITING_CONFIRMATION':
                        checked(response)
                    # This command explicitly runs source-controlled synthetic write
                    # cases. Preserve the resolved preview and confirm that exact Run.
                    if not detail.get('function_id') or preview.get('run_id')!=tested.get('run_id'):
                        raise ValueError('Incomplete authorized write-test preview')
                    approvals.append({'function':detail['function'],'preview':preview})
                    (out/'authoring-confirmations.json').write_text(json.dumps(approvals,ensure_ascii=False,indent=2),encoding='utf-8')
                    test_path=f"/api/v2/management/workspaces/ws-dev/functions/{detail['function_id']}/execution-test"
                    checked(client.post(test_path+'/confirmations',headers=headers,json={'run_id':preview['run_id'],'revision':preview['revision'],'confirmed':True}))
                    for _ in range(50):
                        worker.tick()
                        current=checked(client.get(test_path,headers=headers))
                        if current['status'] in {'SUCCEEDED','FAILED','UNKNOWN','CANCELLED'}: break
                    if not current.get('passed'):
                        raise ValueError('Confirmed write-test did not pass; its ledger is retained')
                else:
                    raise ValueError('Synthetic document upgrade did not finish within its bounded attempts')
        agent=installed['agent'];(out/'application.json').write_text(json.dumps(installed,ensure_ascii=False,indent=2),encoding='utf-8')
        real_execute=zhiyu.execute
        real_invoke=three_layer._invoke
        for case in cases:
            started=time.monotonic();fault=case['assertions'].get('fault')
            shapes=[];model_errors=[]
            def measured_invoke(work,stage,data,instruction):
                if isinstance(data.get('previous_error'),dict):
                    model_errors.append({'stage':stage,'repair_reason':data['previous_error']})
                try:
                    response=real_invoke(work,stage,data,instruction)
                except ValueError as exc:
                    # Only fixed adapter categories, never model text or a prompt.
                    code=str(exc).split(' ',1)[0]
                    allowed={'MODEL_INVALID_RESPONSE','MODEL_NETWORK_ERROR','MODEL_TIMEOUT','MODEL_AUTH_REJECTED',
                             'MODEL_ACCESS_FORBIDDEN','MODEL_QUOTA_EXHAUSTED','MODEL_RATE_LIMITED',
                             'MODEL_REMOTE_FAILURE','MODEL_REQUEST_REJECTED'}
                    model_errors.append({'stage':stage,'code':code if code in allowed else 'VALIDATION_ERROR'})
                    raise
                if stage=='plan' and isinstance(response,dict):
                    shapes.append([{'step_id':s.get('step_id'),'capability_id':s.get('capability_id'),
                        'foreach':s.get('foreach'),'depends_on':s.get('depends_on'),
                        'references':{k:v for k,v in s.get('arguments',{}).items() if isinstance(v,dict)}}
                        for s in (response.get('plan') or {}).get('steps',[]) if isinstance(s,dict)])
                return response
            three_layer._invoke=measured_invoke
            def fault_execute(key,arguments,state,operation_id,**kw):
                return real_execute(key,arguments,state,operation_id,fault=fault if key in case['expected_keys'] else None,**kw)
            zhiyu.execute=fault_execute
            try:
                # Each case begins with the same deterministic simulated state.
                with Session(engine) as db,db.begin():
                    set_identity(db,user);row=db.get(SyntheticState,('ws-dev','zhiyu-v1'));row.state=zhiyu.initial_state()
                guest=checked(client.post(f"/api/v2/runtime/apps/{agent['public_id']}/anonymous-sessions",json={'demo_persona':case['persona_id']}))
                h={'Authorization':'Bearer '+guest['access_token']}
                conv=checked(client.post(f"/api/v2/runtime/agents/{agent['id']}/conversations",headers=h))
                rid=checked(client.post(f"/api/v2/runtime/conversations/{conv['id']}/messages",headers=h,json={'text':case['text'],'client_message_id':str(uuid.uuid4())}))['run_id']
                confirmed=False
                for _ in range(80):
                    work=worker.claim_run(guest['user_id'])
                    if work: worker.prepare_run(work)
                    work=worker.claim_call(guest['user_id'])
                    if work: worker.process_call(work)
                    response=client.get(f'/api/v2/runtime/runs/{rid}',headers=h)
                    if response.status_code == 403 and case['expected_source'] == 'denied':
                        # The runtime deliberately refuses even retained data
                        # after current object authorization fails. Inspect the
                        # persisted status and call count under the test owner,
                        # rather than calling this transport refusal success.
                        break
                    view=checked(response)
                    if view['status']=='WAITING_CONFIRMATION':
                        if not (case['assertions'].get('confirmation_required') or case['assertions'].get('confirm')):
                            break
                        with Session(engine) as db:
                            set_identity(db,guest['user_id'])
                            assert db.scalar(select(func.count()).select_from(Call).where(Call.run_id==rid,Call.definition['effect'].as_string()=='WRITE'))==0
                        checked(client.post(f'/api/v2/runtime/runs/{rid}/confirmations',headers=h,json={'revision':view['revision'],'confirmed':True}));confirmed=True
                        continue
                    if view['status'] in {'SUCCEEDED','FAILED','CANCELLED','UNKNOWN','WAITING_INPUT','WAITING_SELECTION'}: break
                with Session(engine) as db:
                    set_identity(db,guest['user_id']);run=db.get(Run,rid)
                    plan=db.scalar(select(ExecutionPlan).where(ExecutionPlan.run_id==rid))
                    events=list(db.scalars(select(Event).where(Event.run_id==rid).order_by(Event.sequence)))
                    final=next((e.payload for e in reversed(events) if e.event_type=='RESULT' and e.payload.get('title')=='答复' and not e.payload.get('call_id')), {})
                    calls=list(db.scalars(select(Call).where(Call.run_id==rid)))
                    skill=db.get(BusinessSkillVersion,plan.skill_version_id) if plan and plan.skill_version_id else None
                    state=db.get(SyntheticState,('ws-dev','zhiyu-v1')).state
                    observation={'status':run.status,'decision':run.decision,'confirmed':confirmed,
                        'question_kind':(run.question or {}).get('kind'),'option_count':len((run.question or {}).get('options',[])),
                        'answer_source':final.get('answer_source'),'answer':final.get('data',''),
                        'plan_source':plan.source if plan else None,'skill_key':skill.definition['key'] if skill else None,
                        'skill_mode':skill.definition.get('mode','template') if skill else None,
                        'final_output_mapping':plan.snapshot.get('final_output_mapping') if plan else None,
                        'steps':plan.snapshot['steps'] if plan else [],'file_count':db.scalar(select(func.count()).select_from(ResultFile).where(ResultFile.run_id==rid)),
                        'calls':[{'key':c.definition['connector']['workflow_id'],'arguments':c.arguments,'status':c.status,'result':c.result} for c in calls],
                        'write_count':len(state.get('_operations',{})), 'events':[{'type':e.event_type,'payload':e.payload} for e in events],
                        'run_id':rid,'conversation_id':conv['id']}
                failures=assess(case,observation)
                result={'id':case['id'],'group':case['group'],'passed':not failures,'failures':failures,'elapsed_seconds':round(time.monotonic()-started,3),'plan_shapes':shapes,'model_errors':model_errors,**observation}
            except Exception as exc:
                result={'id':case['id'],'group':case['group'],'passed':False,'error_type':type(exc).__name__,'error':str(exc)[:600],'elapsed_seconds':round(time.monotonic()-started,3)}
            finally: zhiyu.execute=real_execute;three_layer._invoke=real_invoke
            results.append(result);(out/(case['id']+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
            print(json.dumps({k:result.get(k) for k in ('id','passed','status','failures','error_type','elapsed_seconds')},ensure_ascii=False),flush=True)
    report={'cases':len(results),'passed':sum(r['passed'] for r in results),'by_group':dict(Counter(r['group'] for r in results)),
            'database':args.database,'model_profile_id':model_id,
            'model':model_version.config.get('model'),'model_version':model_version.config.get('model_version'),
            'thinking_mode':model_version.config.get('thinking_mode'),'results':results,'bank_acceptance':False}
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    return 0 if all(r['passed'] for r in results) else 1


if __name__=='__main__': sys.exit(main())
