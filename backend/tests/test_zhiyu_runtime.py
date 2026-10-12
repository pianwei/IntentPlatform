"""Synthetic catalog integration, persona isolation and model-only persistence."""
from copy import deepcopy
import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from intent_platform.db import engine, set_identity
from intent_platform.models import Agent, Release, Run, Call, RuntimeSession, SyntheticState, Conversation, ExecutionPlan
from intent_platform.synthetic_runtime import CATALOG_ID
from test_local_identity import local, bearer, guest
from conftest import conversation, message, drain


@pytest.fixture
def zhiyu(client, local):
    path = f"/api/v2/management/workspaces/{local['ws']}/synthetic/zhiyu/install"
    result = client.post(path, headers=local['headers'], json={})
    assert result.status_code == 200, result.text
    app = result.json()['agent']
    return app


def test_install_idempotent_release_and_restricted_public_catalog(client, local, zhiyu):
    path = f"/api/v2/management/workspaces/{local['ws']}/synthetic/zhiyu/install"
    again = client.post(path, headers=local['headers'], json={})
    assert again.status_code == 200, again.text
    assert again.json()['agent']['current_release_id'] == zhiyu['current_release_id']
    public = client.get(f"/api/v2/runtime/apps/{zhiyu['public_id']}").json()
    assert public['synthetic_catalog']['id'] == CATALOG_ID
    assert len(public['synthetic_catalog']['personas']) == 3
    g = guest(client, zhiyu)
    assert client.post(path, headers=bearer(g['access_token']), json={}).status_code == 403
    with Session(engine) as db:
        set_identity(db, local['user'])
        snapshot = db.get(Release, zhiyu['current_release_id']).snapshot
        from intent_platform.public_demo import assert_synthetic_snapshot
        assert_synthetic_snapshot(snapshot)
        tampered = deepcopy(snapshot); tampered['capabilities'][0]['definition']['description'] += ' arbitrary'
        with pytest.raises(ValueError): assert_synthetic_snapshot(tampered)


def test_persona_cannot_change_during_recovery_or_inherit_history(client, local, zhiyu):
    a = guest(client, zhiyu, demo_persona='manager_a')
    b = guest(client, zhiyu, demo_persona='manager_b')
    cid = conversation(client, bearer(a['access_token']), zhiyu['id'])
    assert client.get(f'/api/v2/runtime/conversations/{cid}/history', headers=bearer(b['access_token'])).status_code in {403,404}
    path = f"/api/v2/runtime/apps/{zhiyu['public_id']}/anonymous-sessions"
    assert client.post(path,json={'demo_persona':'admin'}).status_code == 422
    assert client.post(path,json={'runtime_session_id':a['runtime_session_id'],'recovery_secret':a['recovery_secret'],'demo_persona':'supervisor'}).status_code == 409
    restored = guest(client, zhiyu, runtime_session_id=a['runtime_session_id'], recovery_secret=a['recovery_secret'])
    with Session(engine) as db:
        set_identity(db,a['user_id'])
        assert db.get(RuntimeSession,restored['runtime_session_id']).demo_persona == 'manager_a'


def protocol_run(client, zhiyu, persona='manager_a', text='解释复利'):
    g=guest(client,zhiyu,demo_persona=persona); headers=bearer(g['access_token'])
    cid=conversation(client,headers,zhiyu['id']); rid=message(client,headers,cid,text)
    from intent_platform.worker import _claim_run
    with Session(engine) as db:
        set_identity(db,g['user_id']); ws=db.get(Run,rid).workspace_id
    work=_claim_run(g['user_id'],ws)
    return work,g,headers,cid,rid


def test_model_answer_has_zero_calls_and_survives_history(client, local, zhiyu, monkeypatch):
    from intent_platform import model_answer
    work,g,h,cid,rid=protocol_run(client,zhiyu)
    work['snapshot']=deepcopy(work['snapshot']);work['snapshot']['model_profile']['mode']='openai-compatible'
    monkeypatch.setattr(model_answer,'classify',lambda *a:model_answer.AnswerRoute(mode='GENERAL',needs_business_data=False,needs_business_action=False))
    monkeypatch.setattr(model_answer,'structured_model_call',lambda *a:{'answer':'复利是利息加入本金后继续计息。'})
    assert model_answer.maybe_answer(work)
    assert client.get(f'/api/v2/runtime/runs/{rid}',headers=h).json()['status']=='SUCCEEDED'
    with Session(engine) as db:
        set_identity(db,g['user_id'])
        assert db.scalar(select(func.count()).select_from(Call).where(Call.run_id==rid))==0
        assert db.scalar(select(func.count()).select_from(ExecutionPlan).where(ExecutionPlan.run_id==rid))==0
    history=client.get(f'/api/v2/runtime/conversations/{cid}/history',headers=h)
    assert history.status_code==200 and '复利' in history.text


def test_business_data_cannot_be_model_only():
    from intent_platform.model_answer import AnswerRoute
    with pytest.raises(ValueError): AnswerRoute(mode='GENERAL',needs_business_data=True,needs_business_action=False)


def test_new_connector_enforces_persona_before_dispatch(client, local, zhiyu, monkeypatch):
    from intent_platform import three_layer
    from intent_platform.worker import prepare_run
    work,g,h,cid,rid=protocol_run(client,zhiyu,text='查询 ZY-C001 的客户画像')
    cap=next(c for c in work['candidates'] if c['definition']['connector'].get('workflow_id')=='zhiyu.customer.profile')
    def proposal(work,skills):
        return ({'inputs':{},'steps':[{'step_id':'profile','capability_id':cap['capability_id'],'arguments':{'customer_id':'ZY-C001'},'depends_on':[]}]},
                'model',None,{'goal':work['text'],'entities':[{'type':'zhiyu:Customer','mention':'ZY-C001'}],'actions':[cap['definition']['action']],'negated':False},[cap['capability_id']])
    monkeypatch.setattr(three_layer,'propose',proposal)
    prepare_run(work);drain()
    result=client.get(f'/api/v2/runtime/runs/{rid}',headers=h)
    assert result.status_code==200,result.text
    assert result.json()['status']=='SUCCEEDED',result.text


def test_key_persona_switch_preserves_trusted_host_lineage(client, local, zhiyu):
    from test_local_identity import key, embed, ticket
    k=key(client,local); original=embed(client,zhiyu,k['secret'])
    old_cid=conversation(client,bearer(original['access_token']),zhiyu['id'])
    result=client.post(f"/api/v2/runtime/apps/{zhiyu['public_id']}/demo-persona",
        headers=bearer(original['access_token']),json={'demo_persona':'manager_b'})
    assert result.status_code==200,result.text
    switched=result.json(); assert switched['demo_persona']=='manager_b'
    assert switched['runtime_session_id']!=original['runtime_session_id']
    assert client.get(f'/api/v2/runtime/conversations/{old_cid}/history',headers=bearer(switched['access_token'])).status_code in {403,404}
    resumed=embed(client,zhiyu,k['secret'],runtime=original['runtime_session_id'])
    assert resumed['runtime_session_id']==switched['runtime_session_id']
    assert resumed['demo_persona']=='manager_b'
    wrong,_=ticket(client,zhiyu,k['secret'],visitor='another-visitor-aaaa',runtime=original['runtime_session_id'])
    assert wrong.status_code==403
    revoked=client.post(f"/api/v2/integrations/runtime-sessions/{original['runtime_session_id']}/revoke",
        headers=bearer(k['secret']),json={'visitor_id':'host-visitor-aaaaaaaa'})
    assert revoked.status_code==200,revoked.text
    assert client.post(f"/api/v2/runtime/agents/{zhiyu['id']}/conversations",headers=bearer(resumed['access_token'])).status_code in {401,403}


def test_failed_model_answer_never_creates_calls(client, local, zhiyu, monkeypatch):
    from intent_platform import model_answer
    work,g,h,cid,rid=protocol_run(client,zhiyu)
    work['snapshot']=deepcopy(work['snapshot']);work['snapshot']['model_profile']['mode']='openai'
    def fail(*args): raise ValueError('unavailable')
    monkeypatch.setattr(model_answer,'classify',fail)
    assert model_answer.maybe_answer(work)
    failed=client.get(f'/api/v2/runtime/runs/{rid}',headers=h).json()
    assert failed['status']=='FAILED' and failed['decision']=='ERROR'
    with Session(engine) as db:
        set_identity(db,g['user_id'])
        assert db.scalar(select(func.count()).select_from(Call).where(Call.run_id==rid))==0


def test_late_failed_model_attempt_does_not_fail_current_attempt(client, local, zhiyu, monkeypatch):
    from intent_platform import model_answer, worker
    work,g,h,cid,rid=protocol_run(client,zhiyu)
    work['snapshot']=deepcopy(work['snapshot']);work['snapshot']['model_profile']['mode']='openai'
    def fail(*args): raise ValueError('old request timed out')
    monkeypatch.setattr(model_answer,'classify',fail)
    monkeypatch.setattr(worker,'attempt_live',lambda *_:False)
    assert model_answer.maybe_answer(work)
    with Session(engine) as db:
        set_identity(db,g['user_id'])
        assert db.get(Run,rid).status=='ROUTING'
        assert db.scalar(select(func.count()).select_from(Call).where(Call.run_id==rid))==0


@pytest.mark.parametrize('persona,key', [('manager_a','performance.ranking'),('supervisor','performance.summary')])
def test_role_only_restriction_denies_before_call_registration(client, local, zhiyu, monkeypatch, persona, key):
    from intent_platform import three_layer
    from intent_platform.worker import prepare_run
    work,g,h,cid,rid=protocol_run(client,zhiyu,persona=persona,text='查看2026-09业绩')
    cap=next(c for c in work['candidates'] if c['definition']['connector'].get('workflow_id')=='zhiyu.'+key)
    monkeypatch.setattr(three_layer,'propose',lambda work,skills:(
        {'inputs':{},'steps':[{'step_id':'performance','capability_id':cap['capability_id'],'arguments':{'period':'2026-09'},'depends_on':[]}]},
        'model',None,{'goal':work['text'],'entities':[{'type':'zhiyu:Performance','mention':None}],'actions':['ZY_SUMMARIZE'],'negated':False},[cap['capability_id']]))
    prepare_run(work);drain()
    with Session(engine) as db:
        set_identity(db,g['user_id'])
        assert db.get(Run,rid).status=='FAILED'
        assert db.scalar(select(func.count()).select_from(Call).where(Call.run_id==rid))==0


def test_persona_switch_starts_without_practice_context(client, local, zhiyu):
    a=guest(client,zhiyu); h=bearer(a['access_token'])
    cid=conversation(client,h,zhiyu['id'])
    with Session(engine) as db,db.begin():
        set_identity(db,a['user_id']);db.get(Conversation,cid).business_context={'practice':{'synthetic':True}}
    result=client.post(f"/api/v2/runtime/apps/{zhiyu['public_id']}/demo-persona",headers=h,json={'demo_persona':'supervisor'})
    assert result.status_code==200,result.text
    new=result.json(); assert new['recovery_secret'] and new['user_id']!=a['user_id']
    newcid=conversation(client,bearer(new['access_token']),zhiyu['id'])
    with Session(engine) as db:
        set_identity(db,new['user_id']); assert not db.get(Conversation,newcid).business_context


def test_practice_start_continue_end_persist_and_do_not_call_business_again(client, local, zhiyu, monkeypatch):
    from intent_platform import three_layer, model_answer
    from intent_platform.worker import prepare_run,_claim_run
    work,g,h,cid,rid=protocol_run(client,zhiyu,text='启动星澜科技的AI陪练')
    cap=next(c for c in work['candidates'] if c['definition']['connector'].get('workflow_id')=='zhiyu.roleplay.start')
    monkeypatch.setattr(three_layer,'propose',lambda work,skills:(
        {'inputs':{},'steps':[{'step_id':'practice','capability_id':cap['capability_id'],'arguments':{'customer_id':'星澜科技'},'depends_on':[]}]},
        'model',None,{'goal':work['text'],'entities':[{'type':'zhiyu:Customer','mention':'星澜科技'}],'actions':['ZY_PRACTICE'],'negated':False},[cap['capability_id']]))
    prepare_run(work);drain()
    view=client.get(f'/api/v2/runtime/runs/{rid}',headers=h)
    assert view.json()['status']=='SUCCEEDED',view.text
    history=client.get(f'/api/v2/runtime/conversations/{cid}/history',headers=h)
    assert 'approved_facts' not in history.text and 'session_action' not in history.text
    monkeypatch.setattr(model_answer,'structured_model_call',lambda *a:{'answer':'请介绍服务的办理步骤。'})
    for mode,phrase,active in [('PRACTICE','您好，想了解下贵司的需求',True),('END_PRACTICE','结束陪练并给我反馈',False)]:
        follow=message(client,h,cid,phrase);nextwork=_claim_run(g['user_id'],local['ws'])
        nextwork['snapshot']=deepcopy(nextwork['snapshot']);nextwork['snapshot']['model_profile']['mode']='openai'
        monkeypatch.setattr(model_answer,'classify',lambda *a:model_answer.AnswerRoute(mode=mode,needs_business_data=False,needs_business_action=False))
        assert model_answer.maybe_answer(nextwork)
        with Session(engine) as db:
            set_identity(db,g['user_id'])
            assert db.get(Run,follow).status=='SUCCEEDED'
            assert bool(db.get(Conversation,cid).business_context.get('practice')) is active
            assert db.scalar(select(func.count()).select_from(Call).where(Call.run_id==follow))==0
