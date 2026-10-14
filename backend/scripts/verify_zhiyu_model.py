"""Frozen Zhiyu cases through real model, API and Worker in an isolated clone.

Credentials stay in memory. Only the explicitly registered synthetic catalog is
sent to the public model. This command refuses the main database.
"""
import argparse
import copy
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
    if not database.startswith(('intent_int026_', 'intent_int029_', 'intent_int035_')) or database == 'intent':
        raise ValueError('An INT-026/INT-029/INT-035 isolated database is required')
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
    if case.get('group') == 'multi_turn':
        turns = observation.get('turn_observations', [])
        expected_turns = case.get('turns', [])
        if len(turns) != len(expected_turns) or len(turns) < 2:
            failures.append('multi-turn count')
        if len({item.get('conversation_id') for item in turns}) != 1:
            failures.append('multi-turn conversation continuity')
        for index, expected_turn in enumerate(expected_turns):
            if index >= len(turns):
                break
            turn = turns[index]
            if turn.get('status') != expected_turn.get('expected_status', 'SUCCEEDED'):
                failures.append(f'multi-turn status:{index + 1}')
            keys_for_turn = {call['key'] for call in turn.get('calls', [])}
            if not set(expected_turn.get('expected_keys', [])) <= keys_for_turn:
                failures.append(f'multi-turn route:{index + 1}')
    return failures


def summarize_metrics(cases, results):
    """Return transparent denominators for the approved 384-case light set."""
    by_id = {item['id']: item for item in results}
    groups = {}
    for group in ('single', 'boundary', 'multi_turn', 'compound', 'autonomous', 'fallback'):
        expected = [case for case in cases if case['group'] == group]
        executed = [case for case in expected if case['id'] in by_id]
        passed = sum(bool(by_id[case['id']].get('passed')) for case in executed)
        groups[group] = {'target': len(expected), 'executed': len(executed), 'passed': passed,
                         'failed': len(executed) - passed, 'unexecuted': len(expected) - len(executed),
                         'pass_rate': round(passed / len(executed), 4) if executed else None}

    business_cases = [case for case in cases if case['group'] in {'single', 'multi_turn', 'compound', 'autonomous'}]
    business_results = [(case, by_id[case['id']]) for case in business_cases if case['id'] in by_id]
    route_correct = []
    coverage = []
    decided = []
    semantic_total = semantic_passed = 0
    for case, result in business_results:
        calls = result.get('calls', [])
        called = {call.get('key') for call in calls}
        expected = set(case.get('expected_keys', []))
        allowed = set(case.get('allowed_keys', []))
        correct = expected <= called and (not allowed or called <= allowed)
        if case['group'] == 'compound':
            correct = correct and result.get('plan_source') == 'skill' and result.get('skill_key') == case.get('expected_skill_key')
        if case['group'] == 'autonomous':
            correct = correct and result.get('plan_source') == 'model' and not result.get('skill_key')
        if case['group'] == 'multi_turn':
            turns = result.get('turn_observations', [])
            correct = correct and len(turns) == len(case.get('turns', [])) and all(
                set(turn_spec.get('expected_keys', [])) <= {call.get('key') for call in turns[index].get('calls', [])}
                for index, turn_spec in enumerate(case.get('turns', [])) if index < len(turns))
        route_correct.append(bool(correct))
        coverage.append(bool(expected <= called))
        if calls or result.get('plan_source') or result.get('status') not in {'ROUTING', 'QUEUED'}:
            decided.append(bool(correct))
        for key, value in case.get('assertions', {}).items():
            if not key.startswith('data.'):
                continue
            semantic_total += 1
            matching = [call.get('result') or {} for call in calls if call.get('key') in expected]
            matched = False
            for payload in matching:
                try:
                    if key.endswith('.contains'):
                        matched = value in path_value(payload, key[:-9])
                    else:
                        matched = path_value(payload, key) == value
                except (KeyError, IndexError, TypeError, ValueError):
                    matched = False
                if matched:
                    break
            semantic_passed += int(matched)

    single_function = {}
    for case in cases:
        if case['group'] != 'single' or case['id'] not in by_id:
            continue
        key = case['expected_keys'][0]
        single_function.setdefault(key, {'target': 0, 'executed': 0, 'passed': 0})
        row = single_function[key]
        row['target'] += 1; row['executed'] += 1
        row['passed'] += int(bool(by_id[case['id']].get('passed')))
    for key, row in single_function.items():
        row['unexecuted'] = 10 - row['executed']
        row['pass_rate'] = round(row['passed'] / row['executed'], 4) if row['executed'] else None

    boundary_rows = [(case, by_id[case['id']]) for case in cases
                     if case['group'] == 'boundary' and case['id'] in by_id]
    boundary_metrics = {}
    for kind in ('ambiguity', 'missing_required_input', 'out_of_scope', 'missing_business_fact', 'function_owned_object_authorization'):
        rows = [(case, result) for case, result in boundary_rows if case.get('boundary_kind') == kind]
        if not rows:
            continue
        if kind == 'out_of_scope':
            good = sum(not result.get('calls') and result.get('answer_source') == 'model' for _, result in rows)
            boundary_metrics[kind] = {'target': len(rows), 'executed': len(rows), 'correct': good,
                'false_accepts': len(rows) - good, 'false_accept_rate': round((len(rows) - good) / len(rows), 4)}
        elif kind in {'ambiguity', 'missing_required_input'}:
            good = sum(result.get('status') == 'WAITING_INPUT' and not result.get('calls') for _, result in rows)
            boundary_metrics[kind] = {'target': len(rows), 'executed': len(rows), 'correct': good,
                                      'recognition_rate': round(good / len(rows), 4)}
        else:
            good = sum(bool(result.get('passed')) for _, result in rows)
            boundary_metrics[kind] = {'target': len(rows), 'executed': len(rows), 'correct': good,
                                      'pass_rate': round(good / len(rows), 4)}

    skill_metrics = {}
    for case in cases:
        if case['group'] != 'compound':
            continue
        skill_key = case.get('expected_skill_key', 'unknown')
        row = skill_metrics.setdefault(skill_key, {'target': 0, 'executed': 0, 'passed': 0})
        row['target'] += 1
        if case['id'] in by_id:
            row['executed'] += 1; row['passed'] += int(bool(by_id[case['id']].get('passed')))
    for row in skill_metrics.values():
        row['unexecuted'] = row['target'] - row['executed']
        row['pass_rate'] = round(row['passed'] / row['executed'], 4) if row['executed'] else None

    autonomous_metrics = {}
    for kind in ('single', 'multi'):
        rows = [case for case in cases if case['group'] == 'autonomous' and case.get('kind') == kind]
        run = [by_id[case['id']] for case in rows if case['id'] in by_id]
        passed = sum(bool(result.get('passed')) for result in run)
        autonomous_metrics[kind] = {'target': len(rows), 'executed': len(run), 'passed': passed,
            'unexecuted': len(rows) - len(run), 'pass_rate': round(passed / len(run), 4) if run else None}

    multi_cases = [case for case in cases if case['group'] == 'multi_turn']
    multi_run = [by_id[case['id']] for case in multi_cases if case['id'] in by_id]
    multi_passed = sum(bool(result.get('passed')) for result in multi_run)
    return {
        'reference_targets': {'route_accuracy': 0.95, 'decided_accuracy': 0.98, 'coverage': 0.97,
            'per_function_accuracy': 0.90, 'semantic_parameter_accuracy': 0.95,
            'ambiguity_recognition': 0.95, 'out_of_scope_false_accept_rate_max': 0.02,
            'multi_turn_completion': 0.90},
        'groups': groups,
        'route_accuracy': {'numerator': sum(route_correct), 'denominator': len(route_correct),
                           'value': round(sum(route_correct) / len(route_correct), 4) if route_correct else None},
        'decided_accuracy': {'numerator': sum(decided), 'denominator': len(decided),
                             'value': round(sum(decided) / len(decided), 4) if decided else None},
        'coverage': {'numerator': sum(coverage), 'denominator': len(coverage),
                     'value': round(sum(coverage) / len(coverage), 4) if coverage else None},
        'per_function_accuracy': single_function,
        'semantic_parameter_accuracy': {'numerator': semantic_passed, 'denominator': semantic_total,
            'value': round(semantic_passed / semantic_total, 4) if semantic_total else None},
        'boundary': boundary_metrics,
        'skills': skill_metrics,
        'autonomous_planning': autonomous_metrics,
        'multi_turn_completion': {'numerator': multi_passed, 'denominator': len(multi_cases),
            'executed': len(multi_run), 'unexecuted': len(multi_cases) - len(multi_run),
            'value': round(multi_passed / len(multi_run), 4) if multi_run else None},
        'fallback': groups['fallback'],
        'strict_zero_tolerance_model_checks': 'not measured by this aggregate; see software permission and confirmation tests',
    }


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
    parser.add_argument('--data-snapshot', help='INT-035 sanitized source snapshot merged with project simulation for gaps; requires an explicit --ids or --limit')
    parser.add_argument('--fail-fast',action='store_true',help='Stop on the first failed case to cap evaluation cost')
    parser.add_argument('--source-contract-preflight',action='store_true',
                        help='Check source replay against the pinned published contract without model calls')
    parser.add_argument('--contract-smoke-only',action='store_true',
                        help='Run the first authorized source case and retain sanitized output-contract diagnostics')
    parser.add_argument('--code-root',choices=['/app'])
    args=parser.parse_args();out=Path(args.output)
    if out.exists(): raise SystemExit('Preserve earlier evidence; use a fresh output directory')
    snapshot = None
    if args.data_snapshot:
        if args.supplemental or (not args.ids and not args.limit):
            raise SystemExit('Source-data replay requires explicit case IDs or a strict limit')
        snapshot_path = Path(args.data_snapshot).resolve()
        local_data_root = (ROOT / 'backend/evaluation_data/local').resolve()
        try:
            snapshot_path.relative_to(local_data_root)
        except ValueError:
            raise SystemExit('Source snapshot must remain inside backend/evaluation_data/local') from None
        snapshot = json.loads(snapshot_path.read_text(encoding='utf-8'))
        meta = snapshot.get('meta') or {}
        if meta.get('read_only_transaction_verified') is not True:
            raise SystemExit('Source snapshot does not carry the required read-only export evidence')
    out.mkdir(parents=True);configure(args.database,args.database_host)
    from intent_platform.migrate import main as migrate
    migrate()
    from fastapi.testclient import TestClient
    from sqlalchemy import select,func
    from sqlalchemy.orm import Session
    from intent_platform.main import app
    from intent_platform.db import engine,set_identity
    from intent_platform.models import ModelProfile,ModelProfileVersion,Run,Call,ExecutionPlan,BusinessSkillVersion,Event,ResultFile,SyntheticState,ManagementTrace,Agent,Release
    from intent_platform import worker,zhiyu,three_layer,model_profiles,output_contract
    from intent_platform.zhiyu.evaluation import evaluation_cases
    if snapshot is not None:
        sys.path.insert(0, str(ROOT / 'backend/scripts'))
        from int035_realdata import hybrid_cases, processor_preflight, simulator_fill_cases, source_cases, source_state
        source_case_pool=source_cases(snapshot)
        simulator_case_pool=simulator_fill_cases(snapshot)
        cases=hybrid_cases(snapshot)
        available_source_case_count=len(source_case_pool)
        available_simulator_fill_case_count=len(simulator_case_pool)
        available_hybrid_case_count=len(cases)
        replay_state=source_state(snapshot,zhiyu.initial_state())
        preflight=processor_preflight(snapshot)
        (out/'source-processor-preflight.json').write_text(
            json.dumps(preflight,ensure_ascii=False,indent=2),encoding='utf-8')
    else:
        source_state=None;replay_state=None
        cases=supplemental_cases() if args.supplemental else evaluation_cases()
        available_source_case_count=None
        available_simulator_fill_case_count=None
        available_hybrid_case_count=None
        for case in cases:
            case.setdefault('data_origin','project_simulation')
    credentials=json.loads((ROOT/'.local/INT021-admin.json').read_text(encoding='utf-8-sig'))
    if args.ids:
        known={c['id'] for c in cases}
        missing=set(args.ids)-known
        if missing: raise SystemExit('Unknown frozen case IDs: '+','.join(sorted(missing)))
        cases=[c for c in cases if c['id'] in set(args.ids)]
    if args.limit: cases=cases[:args.limit]
    if args.contract_smoke_only:
        if snapshot is None or not cases or cases[0]['id']!='crm-profile-01':
            raise SystemExit('INT-035 contract smoke requires the first frozen source profile case')
        cases=cases[:1]
    if not cases: raise SystemExit('No evaluation cases selected')
    (out/'frozen-cases.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf-8')
    if available_source_case_count is not None:
        (out/'available-source-cases.json').write_text(json.dumps(source_case_pool,ensure_ascii=False,indent=2),encoding='utf-8')
        (out/'available-simulator-fill-cases.json').write_text(
            json.dumps(simulator_case_pool,ensure_ascii=False,indent=2),encoding='utf-8')
    selected_case_count=len(cases)
    model_requests=0
    setup_model_requests=0
    model_budget_stop=False
    failed_case_stop=False
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

        def install_catalog(document_skills):
            nonlocal setup_model_requests
            original_request = model_profiles.request_model_json
            confirmed_function_ids=set()
            def count_setup_request(*request_args, **request_kwargs):
                nonlocal setup_model_requests
                setup_model_requests += 1
                return original_request(*request_args, **request_kwargs)
            model_profiles.request_model_json = count_setup_request
            try:
                install_path='/api/v2/management/workspaces/ws-dev/synthetic/zhiyu/install'
                install_body={'model_profile_id':model_id, 'document_skills':document_skills}
                approvals=[]
                for _ in range(8):
                    response=client.post(install_path,headers=headers,json=install_body)
                    if response.status_code != 409:
                        return checked(response)
                    detail=response.json().get('detail',{})
                    if isinstance(detail,dict):
                        failed_test=detail.get('execution_test') or {}
                        failed_run=failed_test.get('run_id')
                        if failed_run and not failed_test.get('passed'):
                            trace_response=client.get(
                                f"/api/v2/management/workspaces/ws-dev/runs/{failed_run}/trace",
                                headers=headers)
                            if trace_response.status_code == 200:
                                (out/'catalog-setup-failure-trace.json').write_text(
                                    json.dumps(trace_response.json(),ensure_ascii=False,indent=2),encoding='utf-8')
                            else:
                                (out/'catalog-setup-failure-trace-status.json').write_text(
                                    json.dumps({'status_code':trace_response.status_code},ensure_ascii=False),encoding='utf-8')
                    tested=detail.get('execution_test',{}) if isinstance(detail,dict) else {}
                    preview=tested.get('confirmation')
                    if not document_skills or not isinstance(preview,dict) or tested.get('status')!='WAITING_CONFIRMATION':
                        checked(response)
                    if not detail.get('function_id') or preview.get('run_id')!=tested.get('run_id'):
                        raise ValueError('Incomplete authorized write-test preview')
                    if detail['function_id'] in confirmed_function_ids:
                        (out/'catalog-setup-repeated-confirmation.json').write_text(
                            json.dumps({'function':detail['function'],
                                'resume_diagnostics':detail.get('resume_diagnostics'),
                                'previously_confirmed_function_count':len(confirmed_function_ids)},
                                ensure_ascii=False,indent=2),encoding='utf-8')
                        raise ValueError('Catalog upgrade requested a second write-test confirmation for the same function')
                    confirmed_function_ids.add(detail['function_id'])
                    approvals.append({'function':detail['function'],'function_id':detail['function_id'],
                                      'preview':preview,
                                      'resume_diagnostics':detail.get('resume_diagnostics')})
                    (out/'authoring-confirmations.json').write_text(json.dumps(approvals,ensure_ascii=False,indent=2),encoding='utf-8')
                    test_path=f"/api/v2/management/workspaces/ws-dev/functions/{detail['function_id']}/execution-test"
                    checked(client.post(test_path+'/confirmations',headers=headers,json={'run_id':preview['run_id'],'revision':preview['revision'],'confirmed':True}))
                    for _ in range(50):
                        worker.tick()
                        current=checked(client.get(test_path,headers=headers))
                        if current['status'] in {'SUCCEEDED','FAILED','UNKNOWN','CANCELLED'}: break
                    if not current.get('passed'):
                        raise ValueError('Confirmed write-test did not pass; its ledger is retained')
                raise ValueError('Synthetic catalog upgrade did not finish within its bounded attempts')
            finally:
                model_profiles.request_model_json = original_request
                (out/'catalog-setup-model-requests.json').write_text(
                    json.dumps({'request_attempts':setup_model_requests},ensure_ascii=False,indent=2),encoding='utf-8')

        def execute_turn(case, turn_spec, conv, guest, h):
            text = turn_spec.get('text', case['text'])
            rid = checked(client.post(f"/api/v2/runtime/conversations/{conv['id']}/messages", headers=h,
                json={'text': text, 'client_message_id': str(uuid.uuid4())}))['run_id']
            confirmed = False
            for _ in range(80):
                work = worker.claim_run(guest['user_id'])
                if work: worker.prepare_run(work)
                work = worker.claim_call(guest['user_id'])
                if work: worker.process_call(work)
                response = client.get(f'/api/v2/runtime/runs/{rid}', headers=h)
                if response.status_code == 403 and case.get('expected_source') == 'denied':
                    break
                view = checked(response)
                if view['status'] == 'WAITING_CONFIRMATION':
                    assertions = turn_spec.get('assertions', case.get('assertions', {}))
                    if not (assertions.get('confirmation_required') or assertions.get('confirm')):
                        break
                    with Session(engine) as db:
                        set_identity(db, guest['user_id'])
                        assert db.scalar(select(func.count()).select_from(Call).where(
                            Call.run_id == rid, Call.definition['effect'].as_string() == 'WRITE')) == 0
                    checked(client.post(f'/api/v2/runtime/runs/{rid}/confirmations', headers=h,
                        json={'revision': view['revision'], 'confirmed': True}))
                    confirmed = True
                    continue
                if view['status'] in {'SUCCEEDED', 'FAILED', 'CANCELLED', 'UNKNOWN',
                                      'WAITING_INPUT', 'WAITING_SELECTION'}:
                    break
            with Session(engine) as db:
                set_identity(db, guest['user_id'])
                run = db.get(Run, rid)
                plan = db.scalar(select(ExecutionPlan).where(ExecutionPlan.run_id == rid))
                events = list(db.scalars(select(Event).where(Event.run_id == rid).order_by(Event.sequence)))
                final = next((event.payload for event in reversed(events)
                              if event.event_type == 'RESULT' and event.payload.get('title') == '答复'
                              and not event.payload.get('call_id')), {})
                calls = list(db.scalars(select(Call).where(Call.run_id == rid)))
                traces = list(db.scalars(select(ManagementTrace).where(ManagementTrace.run_id == rid)))
                safe_traces = []
                safe_detail_keys = {'code', 'reason_code', 'stage', 'decision', 'source', 'count',
                                    'capability_key', 'catalog_diff_fields', 'action_key', 'entity_type', 'status'}
                for trace in traces:
                    detail = trace.detail if isinstance(trace.detail, dict) else {}
                    safe_detail = {}
                    for key in safe_detail_keys:
                        value = detail.get(key)
                        if isinstance(value, (int, float, bool)) or (isinstance(value, str) and len(value) <= 200
                                and value.replace('_', '').replace('.', '').replace(':', '').replace('-', '').replace(',', '').isalnum()):
                            safe_detail[key] = value
                    safe_traces.append({'layer': trace.layer, 'kind': trace.kind, 'detail': safe_detail})
                skill = db.get(BusinessSkillVersion, plan.skill_version_id) if plan and plan.skill_version_id else None
                state = db.get(SyntheticState, ('ws-dev', 'zhiyu-v1')).state
                result = {
                    'status': run.status, 'decision': run.decision, 'confirmed': confirmed,
                    'question_kind': (run.question or {}).get('kind'),
                    'option_count': len((run.question or {}).get('options', [])),
                    'answer_source': final.get('answer_source'), 'answer': final.get('data', ''),
                    'plan_source': plan.source if plan else None,
                    'skill_key': skill.definition['key'] if skill else None,
                    'skill_mode': skill.definition.get('mode', 'template') if skill else None,
                    'final_output_mapping': plan.snapshot.get('final_output_mapping') if plan else None,
                    'steps': plan.snapshot['steps'] if plan else [],
                    'file_count': db.scalar(select(func.count()).select_from(ResultFile).where(ResultFile.run_id == rid)),
                    'management_trace': safe_traces,
                    'calls': [{'key': call.definition['connector']['workflow_id'], 'arguments': call.arguments,
                               'status': call.status, 'result': call.result, 'run_id': rid} for call in calls],
                    'write_count': len(state.get('_operations', {})),
                    'events': [{'type': event.event_type, 'payload': event.payload} for event in events],
                    'run_id': rid, 'conversation_id': conv['id'],
                }
            return result

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
                installed=install_catalog(args.document_skills)
            else:
                if snapshot is not None:
                    raise ValueError('INT-035 replay requires the existing published agent to avoid uncounted setup calls')
                installed=install_catalog(args.document_skills)
        agent=installed['agent'];(out/'application.json').write_text(json.dumps(installed,ensure_ascii=False,indent=2),encoding='utf-8')
        if args.source_contract_preflight:
            from intent_platform.output_contract import project_output, _project
            from intent_platform.zhiyu import engine as source_engine
            from intent_platform.ports import execute_connector
            with Session(engine) as db, db.begin():
                set_identity(db,user)
                agent_row=db.get(Agent,agent['id'])
                release=db.get(Release,agent_row.current_release_id) if agent_row and agent_row.current_release_id else None
                if not release:
                    raise ValueError('INT-035 pinned release is unavailable for source contract preflight')
                state_row=db.get(SyntheticState,('ws-dev','zhiyu-v1'))
                if not state_row:
                    raise ValueError('INT-035 source contract preflight has no isolated synthetic state')
                state_row.state=replay_state
                pinned_by_key={
                    item.get('definition',{}).get('connector',{}).get('workflow_id'):item
                    for item in release.snapshot.get('capabilities',[])
                }
            cases_by_id={case['id']:case for case in cases}
            case=cases_by_id.get('crm-profile-01')
            if not case:
                raise ValueError('INT-035 source contract preflight has no profile case')
            key='zhiyu.customer.profile'
            pinned=pinned_by_key.get(key)
            if not pinned:
                raise ValueError('INT-035 pinned release lacks the source profile capability')
            definition=pinned.get('definition',{})
            prior_manager=copy.deepcopy(source_engine._PERSONAS['manager_a'])
            source_engine._PERSONAS['manager_a']['manager_id']=case['eval_persona_manager_id']
            try:
                connector_result=execute_connector(definition,
                    {'customer_id':case['assertions']['data.customer_id']},
                    'probe-int035-contract-preflight',user,'ws-dev')
            finally:
                source_engine._PERSONAS['manager_a']=prior_manager
            data=connector_result.get('data') or {}
            schema=definition.get('output_schema') or {}
            properties=schema.get('properties') or {}
            field_failures=[]
            for field,spec in properties.items():
                if field in data:
                    try: _project(spec,data[field])
                    except (ValueError,TypeError,KeyError): field_failures.append(field)
            try:
                project_output(definition,data)
                projection_error=None
            except (ValueError,TypeError,KeyError) as exc:
                projection_error=type(exc).__name__
            current=next(item['definition'] for item in zhiyu.function_packages() if item['key']==key)
            report={
                'status':'passed' if connector_result.get('status')=='SUCCEEDED' and projection_error is None else 'failed',
                'capability_key':key,
                'pinned_version':pinned.get('version'),
                'connector_status':connector_result.get('status'),
                'pinned_output_fields':sorted(properties),
                'current_output_fields':sorted((current.get('output_schema') or {}).get('properties',{})),
                'projection_error_type':projection_error,
                'field_failures':sorted(field_failures),
                'external_model_calls':0,
                'source_production_origin_verified':False,
            }
            (out/'pinned-release-contract-preflight.json').write_text(
                json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False))
            raise SystemExit(0 if report['status']=='passed' else 2)
        real_execute=zhiyu.execute
        real_invoke=three_layer._invoke
        real_model_request=model_profiles.request_model_json
        real_output_project=output_contract.project_output
        output_contract_diagnostics=[]

        def measured_output_project(definition, raw):
            try:
                return real_output_project(definition, raw)
            except (ValueError, TypeError, KeyError) as exc:
                from intent_platform.output_contract import _project
                properties=(definition.get('output_schema') or {}).get('properties') or {}
                bad_fields=[]
                for field,spec in properties.items():
                    if isinstance(raw,dict) and field in raw:
                        try: _project(spec,raw[field])
                        except (ValueError,TypeError,KeyError): bad_fields.append(field)
                output_contract_diagnostics.append({
                    'capability_key':(definition.get('connector') or {}).get('workflow_id'),
                    'error_type':type(exc).__name__,
                    'returned_fields':sorted(raw.keys()) if isinstance(raw,dict) else [],
                    'contract_fields':sorted(properties),
                    'incompatible_fields':sorted(bad_fields),
                })
                raise

        output_contract.project_output=measured_output_project
        for case in cases:
            started=time.monotonic();fault=case['assertions'].get('fault')
            contract_diagnostics_start=len(output_contract_diagnostics)
            # The hook is restored after every case; reapply it so output
            # contract failures are observed consistently across the run.
            output_contract.project_output=measured_output_project
            shapes=[];model_errors=[];goal_shapes=[];entity_preflight=[]
            def measured_model_request(*request_args,**request_kwargs):
                nonlocal model_requests
                model_requests+=1
                return real_model_request(*request_args,**request_kwargs)
            model_profiles.request_model_json=measured_model_request
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
                if stage=='entity' and isinstance(response,dict):
                    entities=response.get('entities',[])
                    question=str(data.get('question',''))
                    goal_shapes.append({
                        'entity_types':[item.get('type') for item in entities if isinstance(item,dict)],
                        'mention_present':[bool(item.get('mention')) for item in entities if isinstance(item,dict)],
                        'mention_grounded':[bool(item.get('mention') and str(item.get('mention')) in question) for item in entities if isinstance(item,dict)],
                        'selection':[item.get('selection') for item in entities if isinstance(item,dict)],
                        'action_count':len(response.get('actions',[])) if isinstance(response.get('actions'),list) else int(bool(response.get('actions'))),
                    })
                if stage=='plan' and isinstance(response,dict):
                    shapes.append([{'step_id':s.get('step_id'),'capability_id':s.get('capability_id'),
                        'foreach':s.get('foreach'),'depends_on':s.get('depends_on'),
                        'references':{k:v for k,v in s.get('arguments',{}).items() if isinstance(v,dict)}}
                        for s in (response.get('plan') or {}).get('steps',[]) if isinstance(s,dict)])
                return response
            three_layer._invoke=measured_invoke
            real_named_subjects=three_layer.named_subjects_available
            def measured_named_subjects(named_work,goal,capabilities):
                found=real_named_subjects(named_work,goal,capabilities)
                entity_preflight.append({
                    'result':bool(found),
                    'entity_types':[getattr(entity,'type',None) for entity in goal.entities],
                    'named_entity_count':sum(bool(getattr(entity,'mention',None)) for entity in goal.entities),
                    'mention_grounded':[bool(getattr(entity,'mention',None) and str(getattr(entity,'mention')) in named_work['text']) for entity in goal.entities],
                    'selection':[getattr(entity,'selection',None) for entity in goal.entities],
                    'candidate_count':len(capabilities),
                })
                return found
            three_layer.named_subjects_available=measured_named_subjects
            def fault_execute(key,arguments,state,operation_id,**kw):
                return real_execute(key,arguments,state,operation_id,fault=fault if key in case['expected_keys'] else None,**kw)
            zhiyu.execute=fault_execute
            from intent_platform.zhiyu import engine as zhiyu_engine
            prior_persona=copy.deepcopy(zhiyu_engine._PERSONAS[case['persona_id']])
            try:
                # Source data is replayed only in this isolated engine state.
                # The trusted manager context is derived from the exported
                # ownership link and never comes from the test prompt.
                case_state=copy.deepcopy(replay_state) if replay_state is not None else zhiyu.initial_state()
                if case.get('eval_persona_manager_id'):
                    zhiyu_engine._PERSONAS[case['persona_id']]['manager_id']=case['eval_persona_manager_id']
                with Session(engine) as db,db.begin():
                    set_identity(db,user);row=db.get(SyntheticState,('ws-dev','zhiyu-v1'));row.state=case_state
                guest=checked(client.post(f"/api/v2/runtime/apps/{agent['public_id']}/anonymous-sessions",json={'demo_persona':case['persona_id']}))
                h={'Authorization':'Bearer '+guest['access_token']}
                conv=checked(client.post(f"/api/v2/runtime/agents/{agent['id']}/conversations",headers=h))
                turn_specs=case.get('turns') or [{'text':case['text']}]
                turn_observations=[execute_turn(case,turn_spec,conv,guest,h) for turn_spec in turn_specs]
                last=turn_observations[-1]
                observation={**last,
                    'turn_observations':turn_observations,
                    'calls':[call for turn in turn_observations for call in turn.get('calls',[])],
                    'steps':[step for turn in turn_observations for step in turn.get('steps',[])],
                    'management_trace':[trace for turn in turn_observations for trace in turn.get('management_trace',[])],
                    'events':[event for turn in turn_observations for event in turn.get('events',[])],
                    'file_count':sum(turn.get('file_count',0) for turn in turn_observations),
                    'confirmed':any(turn.get('confirmed') for turn in turn_observations)}
                failures=assess(case,observation)
                result={'id':case['id'],'group':case['group'],'data_origin':case.get('data_origin','project_simulation'),'passed':not failures,'failures':failures,'elapsed_seconds':round(time.monotonic()-started,3),'goal_shapes':goal_shapes,'entity_preflight':entity_preflight,'plan_shapes':shapes,'model_errors':model_errors,'output_contract_diagnostics':output_contract_diagnostics[contract_diagnostics_start:],**observation}
            except Exception as exc:
                result={'id':case['id'],'group':case['group'],'data_origin':case.get('data_origin','project_simulation'),'passed':False,'error_type':type(exc).__name__,
                    'error':str(exc)[:600],'goal_shapes':goal_shapes,'entity_preflight':entity_preflight,
                    'plan_shapes':shapes,'model_errors':model_errors,
                    'output_contract_diagnostics':output_contract_diagnostics[contract_diagnostics_start:],
                    'elapsed_seconds':round(time.monotonic()-started,3)}
            finally:
                zhiyu.execute=real_execute;three_layer._invoke=real_invoke
                three_layer.named_subjects_available=real_named_subjects
                model_profiles.request_model_json=real_model_request
                output_contract.project_output=real_output_project
                zhiyu_engine._PERSONAS[case['persona_id']]=prior_persona
            results.append(result);(out/(case['id']+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
            print(json.dumps({k:result.get(k) for k in ('id','passed','status','failures','error_type','elapsed_seconds')},ensure_ascii=False),flush=True)
            if any(error.get('code') in {'MODEL_QUOTA_EXHAUSTED','MODEL_RATE_LIMITED','MODEL_TIMEOUT','MODEL_NETWORK_ERROR',
                                         'MODEL_AUTH_REJECTED','MODEL_ACCESS_FORBIDDEN','MODEL_REMOTE_FAILURE','MODEL_REQUEST_REJECTED'}
                   for error in result.get('model_errors',[])):
                model_budget_stop=True
                break
            if args.fail_fast and not result['passed']:
                failed_case_stop=True
                break
    selected_by_origin=Counter(case.get('data_origin','project_simulation') for case in cases)
    origin_groups={}
    for origin in ('super_workbench_source','project_simulation'):
        completed=sum(r.get('data_origin')==origin for r in results)
        passed=sum(r.get('data_origin')==origin and r.get('passed') for r in results)
        selected=selected_by_origin.get(origin,0)
        origin_groups[origin]={
            'selected':selected,'completed':completed,'passed':passed,
            'failed':completed-passed,'unexecuted':max(0,selected-completed),
            'pass_rate':round(passed/completed,4) if completed else None,
        }
    report={'cases':len(results),'passed':sum(r['passed'] for r in results),'by_group':dict(Counter(r['group'] for r in results)),
            'database':args.database,'model_profile_id':model_id,
            'model':model_version.config.get('model'),'model_version':model_version.config.get('model_version'),
            'thinking_mode':model_version.config.get('thinking_mode'),'results':results,'bank_acceptance':False,
            'data_mode':'hybrid-source-plus-project-simulation' if snapshot is not None else 'synthetic',
            'source_production_origin_verified':False if snapshot is not None else None,
            'available_source_cases':available_source_case_count,
            'available_simulator_fill_cases':available_simulator_fill_case_count,
            'available_hybrid_cases':available_hybrid_case_count,
            'selected_candidates_by_origin':dict(selected_by_origin),
            'selected_results_by_origin':origin_groups,
            'selected_cases':selected_case_count,'unexecuted_cases':selected_case_count-len(results),
            'deepseek_request_attempts':setup_model_requests + model_requests,
            'catalog_setup_model_request_attempts':setup_model_requests,
            'case_model_request_attempts':model_requests,'stopped_for_model_budget_or_transport':model_budget_stop,
            'stopped_on_first_failed_case':failed_case_stop,
            'metrics':summarize_metrics(cases,results),
            'case_set':{
                'target':384,'selected':len(cases),'groups':dict(Counter(case['group'] for case in cases)),
                'origins':dict(Counter(case.get('data_origin','project_simulation') for case in cases)),
                'single_by_function':dict(Counter(case['expected_keys'][0] for case in cases if case['group']=='single')),
                'single_per_function_target':10,'multi_turn_turns_per_case':2,
                'skills_per_rule_target':5,'autonomous_single_target':6,'autonomous_multi_target':6,
            }}
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    return 0 if all(r['passed'] for r in results) and len(results)==selected_case_count else 1


if __name__=='__main__': sys.exit(main())
