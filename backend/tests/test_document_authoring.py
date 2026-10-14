"""Actual Worker evidence and bounded document authoring; no paid model calls."""
from copy import deepcopy
import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from intent_platform.db import engine, set_identity
from intent_platform.models import Function, Agent, Release, Run, Call, Conversation, SyntheticState, ExecutionPlan
from intent_platform.worker import tick
from test_local_identity import local
from test_zhiyu_runtime import zhiyu


def path(local, suffix=""):
    return f"/api/v2/management/workspaces/{local['ws']}" + suffix


def current_definition(local, app, key):
    with Session(engine) as db:
        set_identity(db, local['user'])
        release = db.get(Release, app['current_release_id'])
        cap = next(c for c in release.snapshot['capabilities'] if c['definition']['connector']['workflow_id'] == key)
        return deepcopy(cap['definition'])


def new_current(client, local, app, key):
    definition = current_definition(local, app, key)
    definition['authoring_version'] = 'function-v2'
    result = client.post(path(local, '/functions/import'), headers=local['headers'], json={'definition': definition})
    assert result.status_code == 201, result.text
    return result.json()


def test_static_checks_cannot_publish_and_actual_test_is_private_idempotent(client, local, zhiyu):
    f = new_current(client, local, zhiyu, 'zhiyu.customer.profile')
    base = path(local, '/functions/' + f['id'])
    static = client.post(base + '/validate', headers=local['headers'], json={'revision': f['revision']})
    assert static.status_code == 200 and static.json()['valid'], static.text
    assert client.post(base + '/publish', headers=local['headers']).status_code == 409
    body = {'revision': f['revision'], 'probe': True, 'arguments': {'customer_id': 'ZY-C001'}}
    tested = client.post(base + '/validate', headers=local['headers'], json=body)
    assert tested.status_code == 200 and tested.json()['valid'], tested.text
    evidence = tested.json()['execution_test']
    assert evidence['passed'] and evidence['status'] == 'SUCCEEDED'
    again = client.post(base + '/validate', headers=local['headers'], json=body)
    assert again.json()['execution_test']['run_id'] == evidence['run_id']
    with Session(engine) as db:
        set_identity(db, local['user'])
        run = db.get(Run, evidence['run_id']); conv = db.get(Conversation, run.conversation_id); private = db.get(Agent, conv.agent_id)
        assert db.scalar(select(func.count()).select_from(Call).where(Call.run_id == run.id)) == 1
        assert db.get(Release, conv.release_id).snapshot['authoring_test']
        pid, aid = private.public_id, private.id
    assert client.get('/api/v2/runtime/apps/' + pid).status_code == 404
    assert client.post('/api/v2/runtime/apps/' + pid + '/anonymous-sessions', json={}).status_code == 404
    assert client.get(path(local, '/agents/' + aid), headers=local['headers']).status_code == 404
    listing = client.get(path(local, '/agents'), headers=local['headers'])
    assert aid not in [a['id'] for a in listing.json()]
    definition = next(item for item in client.get(path(local, '/functions'), headers=local['headers']).json() if item['id'] == f['id'])
    assert 'data' not in definition['validation']['execution_test']
    example = client.post(base + '/output-examples', headers=local['headers'], json={'revision': f['revision']})
    assert example.status_code == 200 and example.json()['revision'] == f['revision']
    assert example.json()['definition']['output_examples'] == [evidence['data']]
    assert client.post(base + '/publish', headers=local['headers']).status_code == 200
    versions = client.get(base + '/versions', headers=local['headers']).json()
    assert all(v['version'] > 0 and not v['definition']['output_examples'] for v in versions)


def test_write_test_requires_resolved_confirmation_and_isolates_state(client, local, zhiyu):
    f = new_current(client, local, zhiyu, 'zhiyu.marketing.create')
    base = path(local, '/functions/' + f['id'])
    args = {'customer_id': 'ZY-C001', 'title': '独立测试任务', 'due_on': '2026-10-20'}
    with Session(engine) as db:
        set_identity(db, local['user']); before = deepcopy(db.get(SyntheticState, (local['ws'], 'zhiyu-v1')).state)
    body = {'revision': f['revision'], 'probe': True, 'arguments': args, 'allow_effects': True}
    initial = client.post(base + '/validate', headers=local['headers'], json=body)
    assert initial.status_code == 200, initial.text
    evidence = initial.json()['execution_test']
    assert not evidence['passed'] and evidence['status'] == 'WAITING_CONFIRMATION', initial.text
    preview = evidence['confirmation']; assert preview['arguments'] == args
    with Session(engine) as db:
        set_identity(db, local['user'])
        assert db.scalar(select(func.count()).select_from(Call).where(Call.run_id == evidence['run_id'])) == 0
    assert client.post(base + '/validate', headers=local['headers'], json=body).json()['execution_test']['run_id'] == evidence['run_id']
    assert client.post(base + '/publish', headers=local['headers']).status_code == 409
    confirmed = client.post(base + '/execution-test/confirmations', headers=local['headers'], json={
        'run_id': preview['run_id'], 'revision': preview['revision'], 'confirmed': True})
    assert confirmed.status_code == 200, confirmed.text
    for _ in range(20):
        tick()
        result = client.get(base + '/execution-test', headers=local['headers']).json()
        if result['status'] == 'SUCCEEDED': break
    assert result['passed'], result
    with Session(engine) as db:
        set_identity(db, local['user'])
        assert db.get(SyntheticState, (local['ws'], 'zhiyu-v1')).state == before
        run = db.get(Run, evidence['run_id']); conv = db.get(Conversation, run.conversation_id)
        assert conv.business_context['authoring_synthetic_state'] != before
    assert client.post(base + '/validate', headers=local['headers'], json=body).json()['execution_test']['run_id'] == evidence['run_id']
    assert client.post(base + '/publish', headers=local['headers']).status_code == 200


def test_typed_inputs_and_outputs_enforce_formats_and_shacl():
    from intent_platform.finite_inputs import validate_pair
    from intent_platform.output_contract import _shape, project_output, OutputContractError
    schema = {'type': 'object', 'properties': {'request': {'type': 'object', 'properties': {
        'date': {'type': 'string', 'format': 'date', 'maxLength': 10}, 'enabled': {'type': 'boolean'},
        'amounts': {'type': 'array', 'items': {'type': 'number'}, 'maxItems': 5}},
        'required': ['date', 'enabled', 'amounts'], 'additionalProperties': False}}, 'required': ['request'], 'additionalProperties': False}
    valid = {'request': {'date': '2026-10-20', 'enabled': True, 'amounts': [1, 2.5]}}
    assert validate_pair(schema, valid)[:2] == (True, True)
    for wrong in [{'request': {'date': '2026-02-30', 'enabled': True, 'amounts': [1]}},
                  {'request': {'date': '2026-10-20', 'enabled': 'true', 'amounts': [1]}},
                  {'request': {**valid['request'], 'extra': 'secret'}},
                  {'request': {**valid['request'], 'amounts': [1] * 6}}]:
        assert validate_pair(schema, wrong)[:2] == (False, False)
    output = {'type': 'object', 'properties': {'date': {'type': 'string', 'format': 'date', 'maxLength': 10, 'examples': ['2026-10-20']}}, 'required': ['date'], 'additionalProperties': False}
    _shape(output)
    definition = {'output_schema': output, 'output_mapping': {'date': 'date'}, 'presentation': {'field_map': {'日期': 'date'}}}
    assert project_output(definition, {'date': '2026-10-20'})[0] == {'date': '2026-10-20'}
    with pytest.raises(OutputContractError): project_output(definition, {'date': '2026-02-30'})


def test_document_authority_has_no_steps_and_exact_reviewed_scope():
    from intent_platform.planning_api import SkillBody
    from intent_platform.zhiyu.document_skills import business_skill_documents
    from intent_platform.agent_projection import CompositeDefinition
    doc = business_skill_documents()[0]
    assert SkillBody.model_validate(doc).steps == []
    with pytest.raises(ValueError): SkillBody.model_validate({**doc, 'steps': [{'step_id': 'bad'}]})
    with pytest.raises(ValueError): SkillBody.model_validate({**doc, 'document': ''})
    comp = CompositeDefinition.model_validate({'mode': 'document', 'skill_version_id': 'skillv_test', 'dependencies': [{'function_id': 'f1', 'version': 1}]})
    assert comp.final_output_mapping == {}


def test_document_plan_repairs_mapping_once_without_retaining_model_values(monkeypatch):
    from intent_platform.document_planning import plan_document
    from intent_platform.planning_schemas import ExecutionPlanSpec
    from intent_platform.three_layer import failure_detail
    candidate = {'capability_id': 'read-cap', 'version': 1, 'definition': {
        'name': '读取资料', 'description': '读取获准资料', 'effect': 'READ_ONLY',
        'input_schema': {'type': 'object', 'properties': {}, 'required': []},
        'output_schema': {'type': 'object', 'properties': {'title': {'type': 'string', 'maxLength': 100}}, 'required': ['title']}}}
    definition = {'input_schema': candidate['definition']['input_schema'], 'output_schema': {
        'type': 'object', 'properties': {'record': candidate['definition']['output_schema']}, 'required': ['record']}}
    work = {'text': '读取资料', 'snapshot': {'planning_policy': {}}}
    responses = []
    def invoke(_work, _stage, data, _instruction):
        responses.append(data)
        ref = {'step': 'read', 'path': 'data'} if len(responses) == 1 else {'$step': 'read', 'path': 'data'}
        return {'decision': 'PLAN', 'plan': {'steps': [{'step_id': 'read', 'capability_id': 'read-cap', 'arguments': {}}],
                                           'final_output_mapping': {'record': ref}}}
    monkeypatch.setattr('intent_platform.three_layer._invoke', invoke)
    monkeypatch.setattr('intent_platform.three_layer.claim_repair', lambda _: len(responses) == 1)
    result = plan_document(work, {'key': 'record', 'document': '读取资料后返回完整记录'}, [candidate], definition, {}, {}, work['text'])
    assert len(responses) == 2 and result['final_output_mapping'] == {'record': {'$step': 'read', 'path': 'data'}}
    assert responses[1]['previous_error']['constraints'] == ['FINAL_MAPPING_KEYS_INVALID']
    with pytest.raises(ValueError) as rejected:
        ExecutionPlanSpec.model_validate({'steps': [{'step_id': 'read', 'capability_id': 'read-cap'}],
                                         'final_output_mapping': {'record': {'$step': 'private-secret-value', 'path': 'data'}}})
    assert 'private-secret-value' not in str(failure_detail(rejected.value, 'plan'))


@pytest.mark.parametrize('transport_fault', [False, True])
def test_current_import_requires_real_success_and_business_failure_not_transport_fault(client, local, monkeypatch, transport_fault):
    import json, threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlsplit, parse_qs
    from intent_platform.config import settings
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            failing = parse_qs(urlsplit(self.path).query).get('fail') == ['true']
            requests.append(failing)
            if failing and transport_fault:
                self.close_connection = True; return
            body = json.dumps({'status': 'failed' if failing else 'succeeded', 'data': {'synthetic': True}}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setattr(settings(), 'connector_targets_json', json.dumps({local['ws']: [{'origin': origin, 'path_prefix': '/records', 'kinds': ['http']}]}))
    try:
        defaults = client.post(path(local, '/function-options/bootstrap'), headers=local['headers']).json()
        definition = {'authoring_version': 'function-v2', 'name': '有失败契约的资料查询', 'description': '本地回环测试',
            'object_type': 'business:Document', 'result_type': 'business:Detail', 'domain_refs': defaults['domain_refs'],
            'input_schema': {'type': 'object', 'properties': {'fail': {'type': 'boolean'}}, 'required': ['fail'], 'additionalProperties': False},
            'output_schema': {'type': 'object', 'properties': {'synthetic': {'type': 'boolean'}}, 'required': ['synthetic'], 'additionalProperties': False},
            'presentation': {'field_map': {'模拟结果': 'synthetic'}},
            'connector': {'kind': 'http', 'version': '2', 'endpoint': origin+'/records', 'method': 'GET',
                'status_path': 'status', 'result_path': 'data', 'http_mapping': {'path': {}, 'query': {'fail': 'fail'}, 'headers': {}, 'body': {}}}}
        created = client.post(path(local, '/functions/import'), headers=local['headers'], json={'definition': definition})
        assert created.status_code == 201, created.text
        f = created.json(); base = path(local, '/functions/'+f['id'])
        with Session(engine) as db, db.begin():
            set_identity(db, local['user']); db.get(Function, f['id']).source_state = {'require_failure_probe': True}
        body = {'revision': f['revision'], 'probe': True, 'arguments': {'fail': False}}
        successful = client.post(base+'/validate', headers=local['headers'], json=body)
        assert successful.status_code == 200 and (successful.json().get('execution_test') or {}).get('passed'), successful.text
        assert not successful.json()['valid'] and client.post(base+'/publish', headers=local['headers']).status_code == 409
        checked = client.post(base+'/validate', headers=local['headers'], json={**body, 'failure_arguments': {'fail': True}})
        assert checked.status_code == 200, checked.text
        assert checked.json()['valid'] is not transport_fault
        assert checked.json()['failure_execution_test']['passed'] is not transport_fault
        assert requests == [False, True]  # The successful probe was not replayed.
        published = client.post(base+'/publish', headers=local['headers'])
        assert published.status_code == (409 if transport_fault else 200), published.text
    finally:
        server.shutdown(); server.server_close(); thread.join(3)


@pytest.fixture
def cross_author_tests(client, local, monkeypatch):
    import json, threading, uuid
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlsplit, parse_qs
    from intent_platform.config import settings
    from intent_platform.models import Membership
    from intent_platform.identity_models import LocalAccount
    from intent_platform.local_identity import secret_hash
    from test_local_identity import PASSWORD, bearer
    author, publisher = ('author_' + uuid.uuid4().hex, 'publisher_' + uuid.uuid4().hex)
    with Session(engine) as db, db.begin():
        for user, roles in ((author, ['maintainer', 'user']), (publisher, ['maintainer', 'publisher', 'user'])):
            db.add(LocalAccount(user_id=user, username=user, display_name=user, password_hash=secret_hash(PASSWORD), must_change_password=False))
            db.add(Membership(user_id=user, workspace_id=local['ws'], roles=roles))
    headers = {}
    for user in (author, publisher):
        login = client.post('/api/v2/auth/login', json={'username': user, 'password': PASSWORD})
        assert login.status_code == 200, login.text
        headers[user] = bearer(login.json()['access_token'])
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            fail = parse_qs(urlsplit(self.path).query).get('fail') == ['true']
            calls.append(fail)
            body = json.dumps({'status': 'failed' if fail else 'succeeded',
                               'data': {'notice': 'author-private-test-output'}}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setattr(settings(), 'connector_targets_json', json.dumps({local['ws']: [{'origin': origin, 'path_prefix': '/records', 'kinds': ['http']}]}))
    try:
        defaults = client.post(path(local, '/function-options/bootstrap'), headers=local['headers']).json()
        definition = {'authoring_version': 'function-v2', 'name': '独立测试作者资料查询', 'description': '受限正反执行测试',
            'object_type': 'business:Document', 'result_type': 'business:Detail', 'domain_refs': defaults['domain_refs'],
            'input_schema': {'type': 'object', 'properties': {'fail': {'type': 'boolean'}}, 'required': ['fail'], 'additionalProperties': False},
            'output_schema': {'type': 'object', 'properties': {'notice': {'type': 'string', 'maxLength': 100}}, 'required': ['notice'], 'additionalProperties': False},
            'presentation': {'field_map': {'结果说明': 'notice'}},
            'connector': {'kind': 'http', 'version': '2', 'endpoint': origin+'/records', 'method': 'GET',
                'status_path': 'status', 'result_path': 'data', 'http_mapping': {'path': {}, 'query': {'fail': 'fail'}, 'headers': {}, 'body': {}}}}
        created = client.post(path(local, '/functions/import'), headers=headers[author], json={'definition': definition})
        assert created.status_code == 201, created.text
        function = created.json(); base = path(local, '/functions/' + function['id'])
        with Session(engine) as db, db.begin():
            set_identity(db, local['user']); db.get(Function, function['id']).source_state = {'require_failure_probe': True}
        checked = client.post(base+'/validate', headers=headers[author], json={
            'revision': function['revision'], 'probe': True, 'arguments': {'fail': False}, 'failure_arguments': {'fail': True}})
        assert checked.status_code == 200 and checked.json()['valid'], checked.text
        assert checked.json()['execution_test']['passed'] and checked.json()['failure_execution_test']['passed']
        assert checked.json()['execution_test']['data']['notice'] == 'author-private-test-output'
        yield {'author': author, 'publisher': publisher, 'headers': headers, 'function': function, 'base': base, 'calls': calls}
    finally:
        server.shutdown(); server.server_close(); thread.join(3)


def test_another_publisher_can_use_ledger_facts_but_cannot_read_private_test_data(client, local, cross_author_tests):
    case = cross_author_tests; publisher = case['headers'][case['publisher']]
    for suffix in ('/execution-test', '/execution-test?failure=true'):
        private = client.get(case['base'] + suffix, headers=publisher)
        assert private.status_code == 404 and 'author-private-test-output' not in private.text
    published = client.post(case['base']+'/publish', headers=publisher, json={'revision': case['function']['revision']})
    assert published.status_code == 200 and published.json()['latest_version'] == 1, published.text
    assert 'author-private-test-output' not in published.text and case['calls'] == [False, True]
    with Session(engine) as db:
        set_identity(db, case['publisher'])
        for key in ('execution_test', 'failure_execution_test'):
            evidence = db.get(Function, case['function']['id']).validation[key]
            assert db.get(Run, evidence['run_id']) is None  # Publishing did not relax owner RLS.


@pytest.mark.parametrize('revocation', ['membership', 'account', 'authoring_role', 'function_access'])
def test_another_publisher_cannot_publish_after_original_test_authority_is_revoked(client, local, cross_author_tests, revocation):
    from intent_platform.models import Membership
    from intent_platform.identity_models import LocalAccount
    case = cross_author_tests
    with Session(engine) as db, db.begin():
        set_identity(db, local['user'])
        if revocation == 'membership': db.get(Membership, (case['author'], local['ws'])).active = False
        elif revocation == 'account': db.get(LocalAccount, case['author']).active = False
        elif revocation == 'authoring_role': db.get(Membership, (case['author'], local['ws'])).roles = ['user']
        else:
            function = db.get(Function, case['function']['id']); function.access_mode = 'allowlist'; function.allowed_users = [case['publisher']]
    published = client.post(case['base']+'/publish', headers=case['headers'][case['publisher']], json={'revision': case['function']['revision']})
    assert published.status_code == 409, published.text
    assert 'author-private-test-output' not in published.text and case['calls'] == [False, True]
    with Session(engine) as db:
        set_identity(db, local['user']); assert db.get(Function, case['function']['id']).latest_version == 0


def test_markdown_fallback_uses_approved_labels_not_json():
    from intent_platform.business_markdown import render_rows
    d = {'name': '客户指标', 'output_schema': {'type': 'object', 'properties': {'balance': {'type': 'number', 'title': '资产余额'}}}, 'presentation': {'field_map': {'资产余额': 'balance'}}}
    text = render_rows([{'name': d['name'], 'data': {'balance': 125}}], [d])
    assert '资产余额' in text and '125' in text and '"balance"' not in text


def test_document_upgrade_runs_model_plan_persists_mapping_and_preserves_old_release(client, local, zhiyu, monkeypatch):
    from intent_platform.zhiyu.skills import business_skill_templates
    from intent_platform.planning import _skill_spec
    with Session(engine) as db:
        set_identity(db, local['user'])
        original_release = deepcopy(db.get(Release, zhiyu['current_release_id']).snapshot)
    invoked = []
    def model(work, stage, data, instruction):
        assert stage == 'plan' and data['skill_document'] and data['final_output_schema']
        from intent_platform.public_demo import assert_synthetic_snapshot
        assert_synthetic_snapshot(work['snapshot'])
        invoked.append(data['skill_key'])
        template = next(s for s in business_skill_templates() if s['key'] == data['skill_key'])
        ids = {c['definition']['connector']['workflow_id']: c for c in work['snapshot']['capabilities']}
        plan = _skill_spec(template, data['supplied_inputs'], ids)
        fields = data['final_output_schema']['properties']
        plan['final_output_mapping'] = {field: {'$step': field, 'path': 'data'} for field in fields}
        return {'decision': 'PLAN', 'plan': plan}
    monkeypatch.setattr('intent_platform.three_layer._invoke', model)
    # Exercise the resumable public catalog-install path as well as the
    # focused document-skills endpoint. A retry through this route reinstalls
    # the direct catalog before resuming confirmed composite tests.
    upgrade = path(local, '/synthetic/zhiyu/install')
    upgrade_body = {'document_skills': True}
    result = client.post(upgrade, headers=local['headers'], json=upgrade_body)
    assert result.status_code == 409, result.text
    detail = result.json()['detail']; assert detail['execution_test']['status'] == 'WAITING_CONFIRMATION', result.text
    preview = detail['execution_test']['confirmation']
    test_path = path(local, '/functions/' + detail['function_id'] + '/execution-test')
    assert client.post(test_path + '/confirmations', headers=local['headers'], json={
        'run_id': preview['run_id'], 'revision': preview['revision'], 'confirmed': True}).status_code == 200
    for _ in range(20): tick()
    assert client.get(test_path, headers=local['headers']).json()['passed']
    result = client.post(upgrade, headers=local['headers'], json=upgrade_body)
    assert result.status_code == 200, result.text
    app = result.json()['agent']; assert app['current_release_id'] != zhiyu['current_release_id']
    # Saving an approved output example changes only authoring metadata; it
    # must not make the runtime snapshot appear outdated and re-run the
    # confirmed marketing write during an idempotent catalog-install retry.
    marketing = next(row for row in result.json()['composites'] if row['name'] == '营销任务创建')
    test_path = path(local, '/functions/' + marketing['function_id'] + '/execution-test')
    with Session(engine) as db:
        set_identity(db, local['user'])
        marketing_revision = db.get(Function, marketing['function_id']).revision
    saved_example = client.post(test_path.rsplit('/execution-test', 1)[0] + '/output-examples',
        headers=local['headers'], json={'revision': marketing_revision})
    assert saved_example.status_code == 200, saved_example.text
    marketing_presentation = deepcopy(saved_example.json()['definition']['presentation'])
    release_id = app['current_release_id']
    with Session(engine) as db:
        set_identity(db, local['user'])
        marketing_version = db.get(Function, marketing['function_id']).latest_version
    retried = client.post(upgrade, headers=local['headers'], json=upgrade_body)
    assert retried.status_code == 200, retried.text
    assert retried.json()['agent']['current_release_id'] == release_id
    with Session(engine) as db:
        set_identity(db, local['user'])
        retried_marketing = db.get(Function, marketing['function_id'])
        assert retried_marketing.latest_version == marketing_version
        assert retried_marketing.definition['presentation'] == marketing_presentation
    with Session(engine) as db:
        set_identity(db, local['user'])
        old = db.get(Release, zhiyu['current_release_id']); new = db.get(Release, app['current_release_id'])
        assert old.snapshot == original_release
        assert new.snapshot['three_layer_prompt_revision'] == 'scope-v11'
        from intent_platform.public_demo import assert_synthetic_snapshot
        assert_synthetic_snapshot(old.snapshot); assert_synthetic_snapshot(new.snapshot)
        for row in result.json()['composites']:
            f = db.get(Function, row['function_id'])
            rid = f.validation['execution_test']['run_id']
            plan = db.scalar(select(ExecutionPlan).where(ExecutionPlan.run_id == rid))
            assert plan.snapshot['final_output_mapping'] and plan.status == 'SUCCEEDED'
            assert f.definition['composite']['mode'] == 'document' and not f.definition['composite']['final_output_mapping']
        changed = deepcopy(new.snapshot); changed['business_skills'][0]['definition']['document'] += ' arbitrary outside material'
        with pytest.raises(ValueError): assert_synthetic_snapshot(changed)
    assert len(invoked) == 6
