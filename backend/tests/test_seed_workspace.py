"""Development seed targets its trusted identity's space, never list order."""
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from intent_platform import main
from intent_platform.config import settings
from intent_platform.db import engine
from intent_platform.models import Membership


def extra_space(client, admin, user):
    response = client.post("/api/v2/management/workspaces", headers=admin, json={
        "name": "Seed isolation " + uuid.uuid4().hex,
        "administrator_id": user,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.parametrize("user,expected", [("dev-admin", "ws-dev"), ("dev-other", "ws-other")])
def test_seed_uses_development_identity_space_when_another_space_is_listed_first(client, admin, monkeypatch, user, expected):
    login = client.post("/api/v2/dev/login", json={"user_id": user})
    assert login.status_code == 200, login.text
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    extra = extra_space(client, admin, user)
    rows = client.get("/api/v2/management/workspaces", headers=headers)
    assert rows.status_code == 200, rows.text
    listed = rows.json()
    first = next(row for row in listed if row["id"] == extra)
    default = next(row for row in listed if row["id"] == expected)
    monkeypatch.setattr(main, "workspaces", lambda *_: [first, default])

    response = client.post("/api/v2/dev/seed", headers=headers)
    assert response.status_code == 200, response.text
    seeded = response.json()
    assert seeded["workspace_id"] == expected
    assert seeded["agent"]["workspace_id"] == expected
    assert all(function["workspace_id"] == expected for function in seeded["functions"])
    # The seed must not install any fixtures into the other eligible space.
    functions = client.get(f"/api/v2/management/workspaces/{extra}/functions", headers=headers)
    assert functions.status_code == 200, functions.text
    assert functions.json() == []


def test_seed_does_not_fall_back_to_another_space_when_default_membership_is_revoked(client, admin, monkeypatch):
    extra = extra_space(client, admin, "dev-admin")
    monkeypatch.setattr(main, "workspaces", lambda *_: [{"id": extra, "name": "Other space", "roles": ["admin"]}])
    with Session(engine) as db, db.begin():
        member = db.get(Membership, ("dev-admin", "ws-dev"))
        previous = member.active
        member.active = False
    try:
        response = client.post("/api/v2/dev/seed", headers=admin)
        assert response.status_code == 403, response.text
        functions = client.get(f"/api/v2/management/workspaces/{extra}/functions", headers=admin)
        assert functions.status_code == 200, functions.text
        assert functions.json() == []
    finally:
        with Session(engine) as db, db.begin():
            db.get(Membership, ("dev-admin", "ws-dev")).active = previous


def test_seed_keeps_reader_role_restricted_despite_catalog_claims(client, monkeypatch):
    login = client.post("/api/v2/dev/login", json={"user_id": "dev-reader"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    monkeypatch.setattr(main, "workspaces", lambda *_: [{"id": "ws-dev", "name": "Default", "roles": ["admin"]}])
    response = client.post("/api/v2/dev/seed", headers=headers)
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("token", [
    SimpleNamespace(user_id="unregistered-subject", source="development"),
    SimpleNamespace(user_id="dev-admin", source="introspection"),
])
def test_seed_requires_a_registered_development_subject(token):
    with pytest.raises(HTTPException) as error:
        main.seed(token=token, db=object())
    assert error.value.status_code == 403


@pytest.mark.parametrize("field,value", [("env", "production"), ("allow_dev_auth", False), ("identity_mode", "local")])
def test_seed_remains_disabled_outside_explicit_development_fixture(field, value, monkeypatch):
    monkeypatch.setattr(settings(), field, value)
    with pytest.raises(HTTPException) as error:
        main.seed(token=SimpleNamespace(user_id="dev-admin", source="development"), db=object())
    assert error.value.status_code == 404
