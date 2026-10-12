"""Synthetic protocol tests; these do not claim real model quality."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from intent_platform import three_layer as layers


@pytest.mark.parametrize('path', ['data.documents.0.document_id', 'data.documents.2.owner_id'])
def test_bound_entity_cannot_silently_index_a_search_result(path):
    selected = {'detail': {'definition': {'argument_bindings': {'document_id': {'role': 'target'}}}}}
    raw = {'steps': [{'step_id': 'detail', 'arguments': {'document_id': {'$step': 'lookup', 'path': path}}}]}
    with pytest.raises(layers.RoutingValidationError, match='INDEXED_ENTITY_REQUIRES_BINDING_OR_BATCH'):
        layers._validate_bound_references(raw, selected, None)
    for value in ['A named document', {'$item': 'document_id'}, {'$step': 'detail', 'path': 'data.document_id'}]:
        raw['steps'][0]['arguments']['document_id'] = value
        layers._validate_bound_references(raw, selected, None)


@pytest.mark.parametrize('revision',['scope-v6','scope-v7','scope-v8','scope-v9'])
def test_scope_v6_rejects_unregistered_goal_before_plan(work, monkeypatch, revision):
    work['snapshot']['three_layer_prompt_revision'] = revision
    monkeypatch.setattr(layers, 'claim_repair', lambda _: False)
    monkeypatch.setattr(layers, '_invoke', lambda *args: {**goal(), 'target_capability_ids': ['unavailable']})
    with pytest.raises(layers.RoutingValidationError, match='UNREGISTERED_GOAL_CAPABILITY'):
        layers.propose(work, [])


def test_named_entity_must_not_be_reinterpreted_as_a_population():
    definition = {'participant_roles': [{'role': 'target', 'type': 'Document'}],
                  'argument_bindings': {'document_id': {'role': 'target'}}}
    raw = {'steps': [
        {'step_id': 'lookup', 'arguments': {'query': '同名文档'}},
        {'step_id': 'detail', 'arguments': {'document_id': {'$item': 'document_id'}},
         'foreach': {'step': 'lookup', 'path': 'data.documents', 'item_key': 'document_id'}}]}
    selected = {'lookup': {'definition': {}}, 'detail': {'definition': definition}}
    goal_value = layers.ScopedGoal(goal='查同名文档详情', entities=[{'type': 'Document', 'mention': '同名文档', 'selection': 'named'}])
    with pytest.raises(layers.RoutingValidationError, match='NAMED_ENTITY_REQUIRES_TRUSTED_BINDING'):
        layers._validate_bound_references(raw, selected, goal_value)
    goal_value.entities[0].selection = 'population'
    layers._validate_bound_references(raw, selected, goal_value)


@pytest.mark.parametrize('revision',['scope-v6','scope-v7','scope-v8','scope-v9'])
def test_scope_v6_targets_keep_narrow_plan_and_exclude_broad_rule(work, monkeypatch, revision):
    work['snapshot']['three_layer_prompt_revision'] = revision
    work['text'] = '请列出 U17 的文档，不要概览'
    rule = {'version_id': 'rule-v1', 'function_id': 'overview', 'definition': {
        'key': 'doc-overview', 'name': '概览', 'description': '清单及详情', 'steps':
        work['snapshot']['agent_projection']['composites']['overview']['plan']['steps']}}
    def invoke(_work, stage, data, instruction):
        if stage == 'entity':
            assert 'target_capability_ids' in data['output_schema']['properties']
            assert ('trusted_name_inputs' in data['available_goals'][0]) == (revision in {'scope-v7','scope-v8','scope-v9'})
            return {**goal(), 'target_capability_ids': ['list-docs']}
        assert stage == 'plan'  # The broader rule must not reach rule selection.
        assert [x['capability_id'] for x in data['functions']] == ['list-docs']
        return proposal()
    monkeypatch.setattr(layers, '_invoke', invoke)
    raw, source, *_ = layers.propose(work, [rule])
    assert source == 'model' and len(raw['steps']) == 1


@pytest.mark.parametrize('revision',['scope-v6','scope-v7','scope-v8','scope-v9'])
def test_scope_v6_cannot_omit_a_recognized_outcome(work, monkeypatch, revision):
    work['snapshot']['three_layer_prompt_revision'] = revision
    work['text'] = '列出 U17 文档并读取详情'
    def invoke(_work, stage, *args):
        if stage == 'entity':
            return {**goal(), 'target_capability_ids': ['list-docs', 'doc-detail']}
        return proposal()
    monkeypatch.setattr(layers, '_invoke', invoke)
    monkeypatch.setattr(layers, 'claim_repair', lambda _: False)
    with pytest.raises(layers.RoutingValidationError, match='PLAN_OMITS_REQUESTED_OUTCOME'):
        layers.propose(work, [])


def candidate(fid, name, aliases, action, entity, result, inputs, output, kind="direct"):
    return {"capability_id": fid, "definition": {
        "name": name, "description": name, "aliases": aliases, "action": action,
        "object_type": entity, "result_type": result, "effect": "READ_ONLY",
        "execution_kind": kind, "requires_confirmation": False,
        "connector": {"workflow_id": fid}, "compiler_version": "finite-json-v1",
        "input_schema": {"type": "object", "properties": {key: {"type": "string", "minLength": 1} for key in inputs},
                         "required": inputs, "additionalProperties": False},
        "output_schema": output}, "compiled": {}}


@pytest.fixture
def work():
    strings = {"type": "object", "properties": {"document_id": {"type": "string"}}, "required": ["document_id"], "additionalProperties": False}
    rows = {"type": "object", "properties": {"documents": {"type": "array", "items": strings, "maxItems": 5}},
            "required": ["documents"], "additionalProperties": False}
    direct = candidate("list-docs", "查询文档清单", ["文档"], "LIST", "Owner", "Document", ["owner_id"], rows)
    detail = candidate("doc-detail", "文档详情", [], "READ", "Document", "Document", ["document_id"], strings)
    overview = candidate("overview", "文档概览", ["文档全貌"], "READ", "Owner", "Overview", ["owner_id"], strings, "composite")
    caps = [direct, detail, overview]
    plan = {"inputs": {}, "steps": [
        {"step_id": "documents", "capability_id": "list-docs", "arguments": {"owner_id": {"$input": "owner_id"}}, "depends_on": []},
        {"step_id": "details", "capability_id": "doc-detail", "arguments": {"document_id": {"$item": "document_id"}},
         "depends_on": ["documents"], "foreach": {"step": "documents", "path": "data.documents", "item_key": "document_id"}}]}
    mapping = {"details": {"$step": "details", "path": "data"}}
    overview["definition"]["composite"] = {"final_output_mapping": mapping}
    projection = {"entities": [{"id": entity, "label": entity, "function_ids": [c["capability_id"] for c in caps
        if entity in {c["definition"]["object_type"], c["definition"]["result_type"]}]} for entity in ("Owner", "Document", "Overview")],
        "actions": [{"key": action, "name": action, "effect": "READ_ONLY", "function_ids": [c["capability_id"] for c in caps
                     if c["definition"]["action"] == action]} for action in ("LIST", "READ")],
        "domain": {"relations": {"owns": {"source_type": "Owner", "target_type": "Document"}}, "closure": {}},
        "explicit_function_ids": [c["capability_id"] for c in caps],
        "composites": {"overview": {"dependency_ids": ["list-docs", "doc-detail"], "plan": plan, "final_output_mapping": mapping}}}
    return {"text": "文档；owner_id=U17。", "candidates": caps, "snapshot": {
        "three_layer_prompt_revision": "scope-v4", "agent_projection": projection, "planning_policy": {}}}


def goal():
    return {"goal": "文档", "entities": [{"type": "Owner", "mention": "U17"}], "actions": ["LIST", "READ"], "negated": False}


def proposal(arguments=None):
    return {"decision": "PLAN", "plan": {"inputs": {}, "steps": [{"step_id": "documents", "capability_id": "list-docs",
        "arguments": {"owner_id": "U17"} if arguments is None else arguments, "depends_on": []}]}}


def test_exact_published_request_keeps_direct_output_and_excludes_broader_rule(work, monkeypatch):
    stages = []
    rule = {"version_id": "rule-v1", "function_id": "overview", "definition": {
        "key": "doc-overview", "name": "文档概览", "description": "清单后读取详情", "steps":
        work["snapshot"]["agent_projection"]["composites"]["overview"]["plan"]["steps"]}}
    def invoke(selected, stage, data, instruction):
        stages.append(stage)
        if stage == "entity":
            assert data["exact_published_request"]["capability_id"] == "list-docs"
            assert data["available_goals"][0]["aliases"] == ["文档"]
            return goal()
        assert stage == "plan"
        assert [f["capability_id"] for f in data["functions"]] == ["list-docs"]
        return proposal()
    monkeypatch.setattr(layers, "_invoke", invoke)
    raw, source, _, _, relevant = layers.propose(work, [rule])
    assert source == "model" and relevant == ["list-docs"]
    assert raw["steps"][0]["capability_id"] == "list-docs"
    assert stages == ["entity", "plan"]


@pytest.mark.parametrize('revision',['scope-v6','scope-v7','scope-v8','scope-v9'])
def test_exact_scoped_target_remains_a_capability_not_a_step_id(work, monkeypatch, revision):
    work['snapshot']['three_layer_prompt_revision']=revision
    monkeypatch.setattr(layers,'claim_repair',lambda _:False)
    monkeypatch.setattr(layers,'_invoke',lambda _work,stage,*args:
        {**goal(),'target_capability_ids':['list-docs']} if stage=='entity' else proposal())
    raw, source, *_ = layers.propose(work,[])
    assert source=='model' and raw['steps'][0]['step_id']=='documents'
    assert raw['steps'][0]['capability_id']=='list-docs'


@pytest.mark.parametrize("text", ["文档概览；owner_id=U17", "请把 U17 的文档列出来并逐份给出详情"])
def test_explicit_broader_outcome_keeps_registered_rule(work, monkeypatch, text):
    work["text"] = text
    rule = {"version_id": "rule-v1", "function_id": "overview", "definition": {
        "key": "doc-overview", "name": "文档概览", "description": "清单后读取详情", "steps":
        work["snapshot"]["agent_projection"]["composites"]["overview"]["plan"]["steps"]}}
    def invoke(selected, stage, data, instruction):
        if stage == "entity": return goal()
        assert stage == "skill"
        assert any(c["aliases"] == ["文档"] for c in data["direct_alternatives"])
        return {"skill_key": "doc-overview", "inputs": {"owner_id": "U17"}}
    monkeypatch.setattr(layers, "_invoke", invoke)
    raw, source, version, *_ = layers.propose(work, [rule])
    assert source == "skill" and version == "rule-v1"
    assert [step["capability_id"] for step in raw["steps"]] == ["list-docs", "doc-detail"]


def test_exact_request_rejects_extra_model_operations_before_dispatch(work, monkeypatch):
    bad = proposal()
    bad["plan"]["steps"].append({"step_id": "extra", "capability_id": "doc-detail", "arguments": {"document_id": "D17"}, "depends_on": []})
    monkeypatch.setattr(layers, "_invoke", lambda selected, stage, data, instruction: goal() if stage == "entity" else bad)
    monkeypatch.setattr(layers, "claim_repair", lambda selected: False)
    with pytest.raises(ValueError, match="unavailable capability"):
        layers.propose(work, [])


@pytest.mark.parametrize("text, exact", [
    ("文档；owner_id=U17。", "list-docs"),
    ('查询文档清单；owner_id="U17"', "list-docs"),
    ("文档全貌；owner_id=U17", "overview"),
    ("文档概览；owner_id=U17", "overview"),
    ("不要文档；owner_id=U17", None),
    ("文档；owner_id=U17；还需要详情", None),
    ("文档的详情；owner_id=U17", None),
    ("查文档和其他人的详情；owner_id=U17", None),
])
def test_scope_evidence_uses_whole_published_name_not_substrings(work, text, exact):
    selected, _ = layers._request_evidence(text, work["candidates"])
    assert (selected or {}).get("capability_id") == exact


def test_ambiguous_alias_and_parameter_do_not_choose_values(work):
    duplicate = deepcopy(work["candidates"][0]); duplicate["capability_id"] = "other-list"
    assert layers._request_evidence(work["text"], [*work["candidates"], duplicate])[0] is None
    _, values = layers._request_evidence("文档；owner_id=U17；owner_id=U18", work["candidates"])
    assert values == {}
    _, values = layers._request_evidence("不要使用 owner_id=U17，请补充正确对象", work["candidates"])
    assert values == {}


@pytest.mark.parametrize("entities, actions", [(["Owner"], ["LIST"]), ({"type": "Owner", "mention": "U17"}, "LIST")])
def test_unambiguous_registered_entity_shapes_are_normalized(work, monkeypatch, entities, actions):
    def invoke(selected, stage, data, instruction):
        return {"goal": "文档", "entities": entities, "actions": actions} if stage == "entity" else proposal()
    monkeypatch.setattr(layers, "_invoke", invoke)
    monkeypatch.setattr(layers, "claim_repair", lambda selected: pytest.fail("shape normalization is not semantic repair"))
    raw, _, _, recognized, _ = layers.propose(work, [])
    assert raw["steps"][0]["arguments"]["owner_id"] == "U17"
    assert recognized["entities"][0]["type"] == "Owner"


@pytest.mark.parametrize("entities", [["Unknown"], [{"type": "Unknown"}],
    [{"type": "Owner", "mention": {"owner_id": "U17"}}], [{"type": "Owner", "extra": "ignored?"}],
    [{"type": "Owner", "mention": "invented"}]])
def test_unknown_or_invented_entities_still_reject_before_planning(work, monkeypatch, entities):
    stages = []
    def invoke(selected, stage, data, instruction):
        stages.append(stage)
        return {"goal": "文档", "entities": entities, "actions": ["LIST"]}
    monkeypatch.setattr(layers, "_invoke", invoke)
    monkeypatch.setattr(layers, "claim_repair", lambda selected: False)
    with pytest.raises((ValidationError, layers.RoutingValidationError)):
        layers.propose(work, [])
    assert stages == ["entity"]


@pytest.mark.parametrize("missing", [proposal({"owner_id": {"$input": "subject"}}),
                                     {"decision": "NEEDS_INPUT", "question": "请补充所有者编号"}])
def test_supplied_parameter_omission_consumes_one_existing_repair(work, monkeypatch, missing):
    attempts, repairs = [], []
    def invoke(selected, stage, data, instruction):
        if stage == "entity": return goal()
        attempts.append(data)
        return missing if len(attempts) == 1 else proposal()
    monkeypatch.setattr(layers, "_invoke", invoke)
    monkeypatch.setattr(layers, "claim_repair", lambda selected: repairs.append(True) or len(repairs) == 1)
    raw, *_ = layers.propose(work, [])
    assert len(repairs) == 1 and len(attempts) == 2
    assert attempts[1]["previous_error"]["reason_code"] == "SUPPLIED_ARGUMENT_OMITTED"
    assert raw["steps"][0]["arguments"]["owner_id"] == "U17"


def test_missing_or_negated_parameter_still_asks_without_autofill(work, monkeypatch):
    work["text"] = "查询文档清单，owner_id=U17 不是我要使用的编号"
    def invoke(selected, stage, data, instruction):
        if stage == "entity": return {**goal(), "entities": [{"type": "Owner"}]}
        assert data["supplied_parameter_evidence"] == {}
        return proposal({"owner_id": {"$input": "owner_id"}})
    monkeypatch.setattr(layers, "_invoke", invoke)
    raw, *_ = layers.propose(work, [])
    assert raw["inputs"] == {} and raw["steps"][0]["arguments"] == {"owner_id": {"$input": "owner_id"}}


def test_invalid_supplied_value_can_request_a_valid_replacement(work, monkeypatch):
    work["text"] = '文档；owner_id=""。'
    def invoke(selected, stage, data, instruction):
        if stage == "entity": return {**goal(), "entities": [{"type": "Owner"}]}
        return {"decision": "NEEDS_INPUT", "question": "请提供非空编号"}
    monkeypatch.setattr(layers, "_invoke", invoke)
    monkeypatch.setattr(layers, "claim_repair", lambda selected: pytest.fail("invalid input is not accepted evidence"))
    raw, *_ = layers.propose(work, [])
    assert raw == {"_clarification": "请提供非空编号"}


def test_exhausted_shared_repair_budget_does_not_retry_parameter_loss(work, monkeypatch):
    stages = []
    def invoke(selected, stage, data, instruction):
        stages.append(stage)
        return goal() if stage == "entity" else proposal({"owner_id": {"$input": "owner_id"}})
    monkeypatch.setattr(layers, "_invoke", invoke)
    monkeypatch.setattr(layers, "claim_repair", lambda selected: False)
    with pytest.raises(layers.RoutingValidationError, match="SUPPLIED_ARGUMENT_OMITTED"):
        layers.propose(work, [])
    assert stages == ["entity", "plan"]


@pytest.mark.parametrize("revision", ["scope-v1", "scope-v2", "scope-v3"])
def test_historical_revisions_preserve_parameter_and_prompt_behavior(work, monkeypatch, revision):
    work["snapshot"]["three_layer_prompt_revision"] = revision
    def invoke(selected, stage, data, instruction):
        assert "supplied_parameter_evidence" not in data
        assert "exact_published_request" not in data
        if stage == "entity":
            assert all("aliases" not in item for item in data.get("available_goals", []))
            return goal()
        return proposal({"owner_id": {"$input": "owner_id"}})
    monkeypatch.setattr(layers, "_invoke", invoke)
    raw, *_ = layers.propose(work, [])
    assert raw["inputs"] == {} and raw["steps"][0]["arguments"] == {"owner_id": {"$input": "owner_id"}}


def test_old_entity_contract_does_not_silently_use_new_normalization(work, monkeypatch):
    work["snapshot"]["three_layer_prompt_revision"] = "scope-v3"
    monkeypatch.setattr(layers, "_invoke", lambda *args: {"goal": "文档", "entities": ["Owner"], "actions": ["LIST"]})
    monkeypatch.setattr(layers, "claim_repair", lambda selected: False)
    with pytest.raises(ValidationError): layers.propose(work, [])


@pytest.mark.parametrize('message,code', [('MODEL_TIMEOUT','MODEL_TIMEOUT'),
    ('MODEL_INVALID_RESPONSE','MODEL_INVALID_RESPONSE'), ('MODEL_NETWORK_ERROR','MODEL_NETWORK_ERROR'),
    ('MODEL_QUOTA_EXHAUSTED (HTTP 402)','MODEL_QUOTA_EXHAUSTED'),
    ('unreviewed secret response','CONTRACT_VALIDATION_FAILED')])
def test_trace_retains_only_fixed_model_failure_categories(message, code):
    detail = layers.failure_detail(ValueError(message), 'entity')
    assert detail == {'stage':'entity','error_type':'ValueError','reason_code':code}


@pytest.mark.parametrize('mention,selection', [('文档','named'), ('owner_id=U17','named'), ('U17','population'), (None,'unspecified')])
def test_exact_request_grounds_explicit_subject_before_authorization(work, monkeypatch, mention, selection):
    work['snapshot']['three_layer_prompt_revision'] = 'scope-v7'
    definition = work['candidates'][0]['definition']
    definition['participant_roles'] = [{'role':'owner','type':'Owner'}]
    definition['argument_bindings'] = {'owner_id':{'role':'owner','resolver_ref':'owner'}}
    attempts, lookups = [], []
    def invoke(_work, stage, data, instruction):
        if stage != 'entity': return proposal()
        attempts.append(data)
        return {**goal(), 'target_capability_ids':['list-docs'], 'entities':[
            {'type':'Owner','mention':mention if len(attempts)==1 else 'U17',
             'selection':selection if len(attempts)==1 else 'named'}]}
    monkeypatch.setattr(layers,'_invoke',invoke)
    monkeypatch.setattr(layers,'claim_repair',lambda _: pytest.fail('Literal binding does not require a model repair'))
    monkeypatch.setattr(layers,'named_subjects_available',lambda _,g,c: lookups.append(g.entities[0].mention) or True)
    raw, *_ = layers.propose(work, [])
    assert len(attempts)==1 and lookups==['U17']
    assert raw['steps'][0]['arguments']=={'owner_id':'U17'}


def test_exact_subject_grounding_never_grants_object_permission(work, monkeypatch):
    work['snapshot']['three_layer_prompt_revision']='scope-v7'
    d=work['candidates'][0]['definition']
    d['participant_roles']=[{'role':'owner','type':'Owner'}]
    d['argument_bindings']={'owner_id':{'role':'owner','resolver_ref':'owner'}}
    monkeypatch.setattr(layers,'_invoke',lambda *args: {**goal(),'target_capability_ids':['list-docs'],
        'entities':[{'type':'Owner','mention':'U17','selection':'named'}]})
    monkeypatch.setattr(layers,'named_subjects_available',lambda *args:False)
    raw, *_=layers.propose(work,[])
    assert raw=={'_authorization_denied':True}


@pytest.mark.parametrize('text', ['请查文档，owner_id=U17；owner_id=U18。', '查询文档清单，owner_id=U17 不是我要使用的编号'])
def test_ambiguous_or_negated_assignments_do_not_replace_model_subject(work, monkeypatch, text):
    work['text']=text
    work['snapshot']['three_layer_prompt_revision']='scope-v7'
    d=work['candidates'][0]['definition']
    d['participant_roles']=[{'role':'owner','type':'Owner'}]
    d['argument_bindings']={'owner_id':{'role':'owner','resolver_ref':'owner'}}
    seen=[]
    monkeypatch.setattr(layers,'_invoke',lambda *args: {**goal(),'target_capability_ids':['list-docs'],
        'entities':[{'type':'Owner','mention':'文档','selection':'named'}]})
    monkeypatch.setattr(layers,'named_subjects_available',lambda _,g,c:seen.append(g.entities[0].mention) or False)
    raw,*_=layers.propose(work,[])
    assert raw=={'_authorization_denied':True} and seen==['文档']
