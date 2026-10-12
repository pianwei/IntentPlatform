"""Backward-compatible display preferences and history filtering; no business behavior changes."""
import uuid
from sqlalchemy.orm import Session
from intent_platform.db import engine
from intent_platform.identity_models import LocalAccount
from conftest import conversation, message
from test_local_identity import local, app, guest, bearer


def test_preferences_read_defaults_and_partial_self_updates(client, local):
    with Session(engine) as db, db.begin():
        db.get(LocalAccount, local["user"]).preferences = {"density": "compact"}
    headers = local["headers"]
    assert client.get("/api/v2/me", headers=headers).json()["preferences"] == {"density": "compact", "text_size": "standard"}
    changed = client.patch("/api/v2/me", headers=headers, json={"preferences": {"text_size": "extra_large"}})
    assert changed.status_code == 200
    assert changed.json()["preferences"] == {"density": "compact", "text_size": "extra_large"}
    for payload in ({"preferences": {"density": "comfortable"}}, {"preferences": {}}, {"display_name": "Renamed"}):
        response = client.patch("/api/v2/me", headers=headers, json=payload)
        assert response.status_code == 200
        assert response.json()["preferences"] == {"density": "comfortable", "text_size": "extra_large"}
    assert client.patch("/api/v2/me", headers=headers, json={"preferences": {"text_size": "huge"}}).status_code == 422


def test_admin_preferences_merge_explicit_fields_and_keep_revision_guard(client, local):
    headers = local["headers"]
    created = client.post("/api/v2/management/accounts", headers=headers, json={"username": "reader_" + uuid.uuid4().hex, "display_name": "Reader"})
    assert created.status_code == 201
    account = created.json()["account"]
    assert account["preferences"]["text_size"] == "standard"
    path = "/api/v2/management/accounts/" + account["user_id"]
    for prefs, expected in (({"density": "compact", "text_size": "large"}, {"density": "compact", "text_size": "large"}),
                            ({"density": "comfortable"}, {"density": "comfortable", "text_size": "large"}),
                            ({"text_size": "extra_large"}, {"density": "comfortable", "text_size": "extra_large"}),
                            ({}, {"density": "comfortable", "text_size": "extra_large"})):
        response = client.patch(path, headers=headers, json={"revision": account["revision"], "preferences": prefs})
        assert response.status_code == 200
        account = response.json()
        assert account["preferences"] == expected
    assert client.patch(path, headers=headers, json={"revision": 1, "preferences": {"text_size": "standard"}}).status_code == 409


def test_nonempty_history_requires_real_run_and_preserves_visitor_boundary(client, local):
    a, _ = app(client, local)
    first, second = guest(client, a), guest(client, a)
    headers = bearer(first["access_token"])
    blank = conversation(client, headers, a["id"])
    active = conversation(client, headers, a["id"])
    run = message(client, headers, active, "Identity sample")
    path = "/api/v2/runtime/conversations"
    assert {item["id"] for item in client.get(path, headers=headers).json()} == {blank, active}
    assert {item["id"] for item in client.get(path + "?include_empty=true", headers=headers).json()} == {blank, active}
    assert [item["id"] for item in client.get(path + "?include_empty=false", headers=headers).json()] == [active]
    # A cancelled request remains an actual conversation; we do not infer from title or status.
    current = client.get(f"/api/v2/runtime/runs/{run}", headers=headers).json()
    assert client.post(f"/api/v2/runtime/runs/{run}/cancel", headers=headers, json={"revision": current["revision"]}).status_code == 200
    assert [item["id"] for item in client.get(path + "?include_empty=false", headers=headers).json()] == [active]
    assert client.get(path + "?include_empty=false", headers=bearer(second["access_token"])).json() == []
    assert client.get(path + "?include_empty=false", headers=local["headers"]).status_code == 403
    base = f"/api/v2/management/workspaces/{local['ws']}/agents/{a['id']}"
    assert client.put(base + "/channels", headers=local["headers"], json={"chat": False, "embed": True}).status_code == 200
    assert client.get(path + "?include_empty=false", headers=headers).status_code == 401
