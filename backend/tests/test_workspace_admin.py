"""Platform allocation is separate from tenant data and member authority."""
import json
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from intent_platform.config import Settings, settings
from intent_platform.db import engine, set_identity


BASE = "/api/v2/management"


def login(client, user):
    result = client.post("/api/v2/dev/login", json={"user_id": user})
    assert result.status_code == 200
    return {"Authorization": "Bearer " + result.json()["access_token"]}


def allocated(client, admin):
    name = "INT019-space-" + uuid.uuid4().hex[:10]
    result = client.post(BASE + "/workspaces", headers=admin,
                         json={"name": name, "administrator_id": "dev-other"})
    assert result.status_code == 201, result.text
    assert result.json()["roles"] == []
    return result.json()["id"]


def test_platform_allocation_does_not_grant_data_access(client, admin):
    other = login(client, "dev-other")
    ws = allocated(client, admin)
    assert client.get(BASE + "/platform-context", headers=admin).json()["can_manage_workspaces"]
    assert not client.get(BASE + "/platform-context", headers=other).json()["can_manage_workspaces"]
    assert client.get(f"{BASE}/workspaces/{ws}/functions", headers=admin).status_code == 403
    assert client.get(f"{BASE}/workspaces/{ws}/functions", headers=other).json() == []
    audit = client.get(f"{BASE}/workspaces/{ws}/audit", headers=other)
    assert audit.status_code == 200
    assert any(a["action"] == "workspace.create" and a["user_id"] == "dev-admin" for a in audit.json())
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        assert db.scalar(text("SELECT count(*) FROM audit WHERE workspace_id=:ws"), {"ws": ws}) == 0
        assert db.scalar(text("SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user")) is False


def test_space_admin_can_add_and_revoke_directory_member(client, admin):
    other = login(client, "dev-other")
    reader = login(client, "dev-reader")
    ws = allocated(client, admin)
    path = f"{BASE}/workspaces/{ws}/members/dev-reader"
    added = client.put(path, headers=other, json={"revision": 0, "roles": ["user"], "active": True})
    assert added.status_code == 200, added.text
    assert any(w["id"] == ws for w in client.get(BASE + "/workspaces", headers=reader).json())
    assert client.get(f"{BASE}/workspaces/{ws}/members", headers=reader).status_code == 403
    body = {"revision": added.json()["revision"], "roles": ["user"], "active": False}
    assert client.put(path, headers=other, json=body).status_code == 200
    assert not any(w["id"] == ws for w in client.get(BASE + "/workspaces", headers=reader).json())
    assert client.put(path, headers=other, json=body).status_code == 409


def test_allocation_requires_platform_authority_and_trusted_identity(client, admin):
    other = login(client, "dev-other")
    body = {"name": "Allocation boundary", "administrator_id": "dev-other"}
    assert client.post(BASE + "/workspaces", headers=other, json=body).status_code == 403
    assert client.post(BASE + "/workspaces", headers=admin,
                       json={**body, "administrator_id": "untrusted"}).status_code == 403
    assert client.post(BASE + "/workspaces", headers=admin,
                       json={**body, "name": "  "}).status_code == 422
    own = client.post(BASE + "/workspaces", headers=admin,
                      json={**body, "administrator_id": "dev-admin"})
    assert own.status_code == 201
    assert "admin" in own.json()["roles"]


def test_directory_admission_cannot_override_workspace_ceiling(client, admin, monkeypatch):
    other = login(client, "dev-other")
    ws = allocated(client, admin)
    monkeypatch.setattr(settings(), "membership_grants_json", json.dumps({ws: {
        "dev-other": {"roles": ["admin"], "active": True},
        "dev-reader": {"roles": ["user"], "active": True}}}))
    path = f"{BASE}/workspaces/{ws}/members"
    assert client.put(path + "/dev-reader", headers=other,
                       json={"revision": 0, "roles": ["admin"], "active": True}).status_code == 403
    assert client.put(path + "/dev-admin", headers=other,
                       json={"revision": 0, "roles": ["user"], "active": True}).status_code == 403
    assert client.put(path + "/dev-reader", headers=other,
                       json={"revision": 0, "roles": ["user"], "active": True}).status_code == 200


def test_member_management_cannot_create_identity_or_platform_role(client, admin):
    other = login(client, "dev-other")
    ws = allocated(client, admin)
    path = f"{BASE}/workspaces/{ws}/members"
    assert client.put(path + "/unknown", headers=other,
                       json={"revision": 0, "roles": ["user"], "active": True}).status_code == 403
    assert client.put(path + "/dev-reader", headers=other,
                       json={"revision": 0, "roles": ["platform_admin"], "active": True}).status_code == 422
    assert client.put(path + "/dev-other", headers=other,
                       json={"revision": 1, "roles": ["user"], "active": True}).status_code == 403


def test_explicit_empty_configuration_disables_development_allocator(client, admin, monkeypatch):
    monkeypatch.setattr(settings(), "platform_admins_json", "[]")
    assert not client.get(BASE + "/platform-context", headers=admin).json()["can_manage_workspaces"]
    assert client.post(BASE + "/workspaces", headers=admin,
                       json={"name": "Disabled", "administrator_id": "dev-admin"}).status_code == 403


@pytest.mark.parametrize("config", [
    {"platform_admins_json": '["unknown"]'},
    {"platform_admins_json": '"dev-admin"'},
    {"identity_directory_json": '{"user":{"label":"not a string"}}'},
    {"identity_directory_json": '{" ":"Blank identity"}'},
])
def test_invalid_server_directory_fails_configuration(config):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, env="test", **config)


def test_no_development_identities_without_development_auth(monkeypatch):
    from intent_platform import workspace_admin
    cfg = Settings(_env_file=None, env="test", allow_dev_auth=False)
    monkeypatch.setattr(workspace_admin, "settings", lambda: cfg)
    assert workspace_admin.identity_directory() == {}
    assert workspace_admin.platform_administrators() == set()


def test_unset_compose_directory_configuration_has_no_production_fallback(monkeypatch):
    from intent_platform import workspace_admin
    cfg = Settings(_env_file=None, env="test", allow_dev_auth=False,
                   identity_directory_json="", platform_admins_json="")
    monkeypatch.setattr(workspace_admin, "settings", lambda: cfg)
    assert workspace_admin.identity_directory() == {}
    assert workspace_admin.platform_administrators() == set()
