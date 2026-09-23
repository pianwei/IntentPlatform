"""Model secrets and immutable versions use the isolated PostgreSQL test DB."""

import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from intent_platform.config import settings
from intent_platform.db import engine, set_identity
from intent_platform.model_profiles import pinned_snapshot, structured_model_call
from intent_platform.model_profiles_api import router
from intent_platform.models import ModelProfile, ModelProfileVersion


PREFIX = "/api/v2/management/workspaces/ws-dev/model-profiles"


@pytest.fixture
def model_client(client):
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as local:
        yield local


@pytest.fixture
def master(monkeypatch):
    monkeypatch.setattr(settings(), "model_secret_key", "local-test-master-key-keep-out-of-repo")


def payload(name=None, secret="local-test-api-secret"):
    return {"name": name or "模拟模型配置-" + uuid.uuid4().hex[:8],
            "config": {"protocol": "openai-compatible", "endpoint": "http://127.0.0.1:19999/v1/chat/completions",
                       "model": "demo-llm", "model_version": "2026-09-r1", "timeout_seconds": 7},
            "api_key": secret}


def test_admin_versions_encrypt_mask_pin_and_disable(model_client, admin, master, monkeypatch):
    body = payload()
    created = model_client.post(PREFIX, headers=admin, json=body)
    assert created.status_code == 201, created.text
    profile = created.json()
    assert profile["latest_version"] == 1 and profile["latest"]["has_api_key"]
    assert body["api_key"] not in created.text and "secret_ciphertext" not in created.text
    identifier = profile["id"]
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        row = db.scalar(select(ModelProfileVersion).where(ModelProfileVersion.profile_id == identifier))
        assert row and body["api_key"].encode() not in row.secret_ciphertext
        old_snapshot = pinned_snapshot(db.get(ModelProfile, identifier), row)
    assert old_snapshot["profile_id"] == identifier and old_snapshot["version_id"] == row.id
    seen = []
    def fake_request(method, url, *, payload, headers, timeout):
        seen.append((url, payload["model"], headers["Authorization"], timeout))
        return {"choices": [{"message": {"content": json.dumps({"ready": True})}}]}
    monkeypatch.setattr("intent_platform.model_profiles.request_json", fake_request)
    assert structured_model_call({"model_profile": old_snapshot}, "dev-admin", "ws-dev", "JSON", "test") == {"ready": True}
    assert seen[-1][-2] == "Bearer " + body["api_key"]
    version_two = dict(body["config"], model_version="2026-09-r2")
    updated = model_client.post(PREFIX + f"/{identifier}/versions", headers=admin,
                                json={"revision": profile["revision"], "config": version_two})
    assert updated.status_code == 201, updated.text
    assert updated.json()["latest_version"] == 2
    versions = model_client.get(PREFIX + f"/{identifier}/versions", headers=admin)
    assert versions.status_code == 200 and [v["version"] for v in versions.json()] == [2, 1]
    assert versions.json()[1]["config"]["model_version"] == "2026-09-r1"
    assert body["api_key"] not in versions.text
    assert structured_model_call({"model_profile": old_snapshot}, "dev-admin", "ws-dev", "JSON", "test") == {"ready": True}
    tested = model_client.post(PREFIX + f"/{identifier}/versions/2/test", headers=admin)
    assert tested.status_code == 200, tested.text
    assert tested.json()["ok"] is True and body["api_key"] not in tested.text
    disabled = model_client.post(PREFIX + f"/{identifier}/disable", headers=admin,
                                 json={"revision": updated.json()["revision"]})
    assert disabled.status_code == 200 and disabled.json()["disabled"] is True
    with pytest.raises(ValueError, match="unavailable"):
        structured_model_call({"model_profile": old_snapshot}, "dev-admin", "ws-dev", "JSON", "test")


def test_model_admin_scope_secret_key_and_endpoint_boundaries(model_client, client, admin, master, monkeypatch):
    other = client.post("/api/v2/dev/login", json={"user_id": "dev-other"})
    assert other.status_code == 200
    other_header = {"Authorization": "Bearer " + other.json()["access_token"]}
    assert model_client.get(PREFIX, headers=other_header).status_code == 403
    assert model_client.post(PREFIX, headers=other_header, json=payload()).status_code == 403
    monkeypatch.setattr(settings(), "model_secret_key", "")
    missing = model_client.post(PREFIX, headers=admin, json=payload())
    assert missing.status_code == 422 and "encryption key" in missing.text
    monkeypatch.setattr(settings(), "model_secret_key", "local-test-master-key-keep-out-of-repo")
    bad = payload()
    bad["config"]["endpoint"] = "https://public.example.com/v1/chat/completions"
    rejected = model_client.post(PREFIX, headers=admin, json=bad)
    assert rejected.status_code == 422 and bad["api_key"] not in rejected.text
    query = payload()
    query["config"]["endpoint"] += "?token=unsafe"
    assert model_client.post(PREFIX, headers=admin, json=query).status_code == 422


def test_structured_response_rejects_invalid_content_without_echo(model_client, admin, master, monkeypatch):
    created = model_client.post(PREFIX, headers=admin, json=payload()).json()
    identifier = created["id"]
    monkeypatch.setattr("intent_platform.model_profiles.request_json",
                        lambda *a, **k: {"choices": [{"message": {"content": "internal-sensitive-raw"}}]})
    failed = model_client.post(PREFIX + f"/{identifier}/versions/1/test", headers=admin)
    assert failed.status_code == 502
    assert "internal-sensitive-raw" not in failed.text
