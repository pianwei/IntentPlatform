"""Authoring lists existing approved bindings, never draft or unrelated ones."""
from copy import deepcopy
from sqlalchemy import select
from sqlalchemy.orm import Session
from intent_platform.config import settings
from intent_platform.db import engine, set_identity
from intent_platform.function_authoring import options
from intent_platform.models import Function
from test_local_identity import local
from test_zhiyu_runtime import zhiyu


def test_published_synthetic_bindings_are_available_for_business_mapping(client, local, zhiyu, monkeypatch):
    monkeypatch.setattr(settings(), 'resolvers_json', '{}')
    response = client.get(f"/api/v2/management/workspaces/{local['ws']}/function-options", headers=local['headers'])
    assert response.status_code == 200, response.text
    rows = response.json()['resolvers']
    references = {row['reference']: row for row in rows}
    assert references['zhiyu.entity.Customer@1'] == {'reference': 'zhiyu.entity.Customer@1', 'version': '1', 'entity_type': 'zhiyu:Customer'}
    assert references['zhiyu.entity.Product@1']['entity_type'] == 'zhiyu:Product'
    assert references['zhiyu.entity.Todo@1']['entity_type'] == 'zhiyu:Todo'
    assert all(set(row) == {'reference', 'version', 'entity_type'} for row in rows)


def test_unpublished_draft_references_do_not_enter_authoring_inventory(client, local, zhiyu, monkeypatch):
    monkeypatch.setattr(settings(), 'resolvers_json', '{}')
    with Session(engine) as db:
        set_identity(db, local['user'])
        before = options(db, local['ws'])['resolvers']
        function = next(item for item in db.scalars(select(Function).where(Function.workspace_id == local['ws']))
                        if item.definition.get('connector', {}).get('workflow_id') == 'zhiyu.customer.profile')
        definition = deepcopy(function.definition)
        definition['argument_bindings']['customer_id']['resolver_ref'] = 'zhiyu.entity.Contact@1'
        function.definition = definition
        db.flush()
        assert options(db, local['ws'])['resolvers'] == before


def test_synthetic_bindings_are_not_advertised_without_catalog_or_in_production(client, local, zhiyu, monkeypatch):
    monkeypatch.setattr(settings(), 'resolvers_json', '{}')
    with Session(engine) as db:
        set_identity(db, local['user'])
        assert options(db, 'unrelated-space')['resolvers'] == []
        monkeypatch.setattr(settings(), 'env', 'production')
        assert options(db, local['ws'])['resolvers'] == []
