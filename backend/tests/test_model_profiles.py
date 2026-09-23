"""Model secrets and immutable versions use the isolated PostgreSQL test DB."""

import json
import socket
import uuid
from contextlib import contextmanager

import pytest
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from intent_platform.config import Settings, settings
from intent_platform.db import engine, set_identity
from intent_platform.demo.catalog import function_packages
from intent_platform.model_profiles import (ModelConfig, invoke_structured, pinned_snapshot,
                                            structured_model_call, validate_config)
from intent_platform.model_profiles_api import router
from intent_platform.models import ModelProfile, ModelProfileVersion
from intent_platform.network import (DEEPSEEK_CHAT_COMPLETIONS_URL, checked_url,
                                     request_json, request_json_parts, request_model_json)


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
    def fake_request(url, *, payload, headers, timeout):
        seen.append((url, payload["model"], headers["Authorization"], timeout))
        return {"choices": [{"message": {"content": json.dumps({"ready": True})}}]}
    monkeypatch.setattr("intent_platform.model_profiles.request_model_json", fake_request)
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
    monkeypatch.setattr("intent_platform.model_profiles.request_model_json",
                        lambda *a, **k: {"choices": [{"message": {"content": "internal-sensitive-raw"}}]})
    failed = model_client.post(PREFIX + f"/{identifier}/versions/1/test", headers=admin)
    assert failed.status_code == 502
    assert "internal-sensitive-raw" not in failed.text


def test_legacy_model_config_keeps_default_request_and_old_snapshot(monkeypatch):
    config = payload()["config"]
    assert ModelConfig.model_validate(config).thinking_mode == "default"
    observed = []

    def fake_request(url, *, payload, headers, timeout):
        observed.append(payload)
        return {"choices": [{"finish_reason": "stop", "message": {"content": '{"ready":true}'}}]}

    monkeypatch.setattr("intent_platform.model_profiles.request_model_json", fake_request)
    assert invoke_structured(config, "synthetic-secret", 'Return JSON {"ready":true}.', "test") == {"ready": True}
    assert "thinking" not in observed[0] and "max_tokens" not in observed[0]


def _development_deepseek(monkeypatch):
    cfg = settings()
    monkeypatch.setattr(cfg, "env", "development")
    monkeypatch.setattr(cfg, "allow_public_deepseek_model_test", True)
    monkeypatch.setattr("intent_platform.network.socket.getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443))])
    return {"protocol": "openai-compatible", "endpoint": DEEPSEEK_CHAT_COMPLETIONS_URL,
            "model": "deepseek-flash", "model_version": "V4.1-Flash", "timeout_seconds": 7,
            "thinking_mode": "disabled"}


def test_deepseek_no_think_request_and_truncation_are_explicit(monkeypatch):
    config = _development_deepseek(monkeypatch)
    seen = []

    def fake_request(url, *, payload, headers, timeout):
        seen.append((url, payload, headers, timeout))
        return {"choices": [{"finish_reason": "stop", "message": {"content": '{"ready":true}'}}]}

    monkeypatch.setattr("intent_platform.model_profiles.request_model_json", fake_request)
    assert invoke_structured(config, "synthetic-secret", 'Return JSON {"ready":true}.', "test") == {"ready": True}
    url, body, headers, timeout = seen[0]
    assert url == DEEPSEEK_CHAT_COMPLETIONS_URL and body["model"] == "deepseek-flash"
    assert body["thinking"] == {"type": "disabled"} and body["max_tokens"] == 4096
    assert body["response_format"] == {"type": "json_object"}
    assert headers["Authorization"] == "Bearer synthetic-secret" and timeout == 7

    monkeypatch.setattr("intent_platform.model_profiles.request_model_json",
                        lambda *a, **k: {"choices": [{"finish_reason": "length",
                                                       "message": {"content": '{"ready":true}'}}]})
    with pytest.raises(ValueError, match="MODEL_INVALID_RESPONSE"):
        invoke_structured(config, "synthetic-secret", "JSON", "test")
    monkeypatch.setattr("intent_platform.model_profiles.request_model_json",
                        lambda *a, **k: {"choices": [{"finish_reason": "stop",
                                                       "message": {"content": ""}}]})
    with pytest.raises(ValueError, match="MODEL_INVALID_RESPONSE"):
        invoke_structured(config, "synthetic-secret", "JSON", "test")


def test_deepseek_profile_version_pins_thinking_mode(model_client, admin, master, monkeypatch):
    config = _development_deepseek(monkeypatch)
    created = model_client.post(PREFIX, headers=admin,
                                json={"name": "DeepSeek fixture test", "config": config,
                                      "api_key": "synthetic-secret"})
    assert created.status_code == 201, created.text
    profile = created.json()
    assert profile["latest"]["config"]["thinking_mode"] == "disabled"
    with Session(engine) as db:
        set_identity(db, "dev-admin")
        version = db.scalar(select(ModelProfileVersion).where(ModelProfileVersion.profile_id == profile["id"],
                                                       ModelProfileVersion.version == 1))
        old_snapshot = pinned_snapshot(db.get(ModelProfile, profile["id"]), version)
    assert old_snapshot["thinking_mode"] == "disabled"
    newer = model_client.post(PREFIX + f"/{profile['id']}/versions", headers=admin,
                              json={"revision": profile["revision"],
                                    "config": {**config, "thinking_mode": "default"}})
    assert newer.status_code == 201, newer.text
    versions = model_client.get(PREFIX + f"/{profile['id']}/versions", headers=admin).json()
    assert [v["config"]["thinking_mode"] for v in versions] == ["default", "disabled"]
    seen = []
    monkeypatch.setattr("intent_platform.model_profiles.request_model_json",
                        lambda url, *, payload, headers, timeout: seen.append(payload) or
                        {"choices": [{"finish_reason": "stop", "message": {"content": '{"ready":true}'}}]})
    snapshot = {"model_profile": old_snapshot,
                "capabilities": [{"definition": {"connector": {"kind": "fixture",
                                                            "workflow_id": package["key"]}}}
                                 for package in function_packages()]}
    assert structured_model_call(snapshot, "dev-admin", "ws-dev", 'JSON {"ready":true}', "test") == {"ready": True}
    assert seen[0]["thinking"] == {"type": "disabled"}


def test_public_model_exception_is_exact_and_never_opens_connectors(monkeypatch):
    config = _development_deepseek(monkeypatch)
    assert validate_config(config)["thinking_mode"] == "disabled"
    assert checked_url(DEEPSEEK_CHAT_COMPLETIONS_URL, model_endpoint=True)[1] == "8.8.8.8"
    with pytest.raises(ValueError, match="allowlisted"):
        checked_url(DEEPSEEK_CHAT_COMPLETIONS_URL)
    for transport in (request_json, request_json_parts):
        with pytest.raises(ValueError, match="allowlisted"):
            transport("POST", DEEPSEEK_CHAT_COMPLETIONS_URL, payload={}) if transport is request_json else transport(
                "POST", DEEPSEEK_CHAT_COMPLETIONS_URL, json_body={})
    for endpoint in ("https://api.deepseek.com/v1/chat/completions",
                     DEEPSEEK_CHAT_COMPLETIONS_URL + "/", "https://api.deepseek.com.evil.test/chat/completions",
                     "http://api.deepseek.com/chat/completions"):
        with pytest.raises(ValueError):
            validate_config({**config, "endpoint": endpoint})
    with pytest.raises(ValueError, match="only by the approved DeepSeek endpoint"):
        validate_config({**config, "endpoint": "http://127.0.0.1:19999/v1/chat/completions"})
    monkeypatch.setattr("intent_platform.network.socket.getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="approved gateway addresses"):
        checked_url(DEEPSEEK_CHAT_COMPLETIONS_URL, model_endpoint=True)
    def unresolved(*args, **kwargs):
        raise socket.gaierror("untrusted-DNS-detail")
    monkeypatch.setattr("intent_platform.network.socket.getaddrinfo", unresolved)
    with pytest.raises(ValueError, match="Endpoint DNS resolution failed") as exc:
        checked_url(DEEPSEEK_CHAT_COMPLETIONS_URL, model_endpoint=True)
    assert "untrusted-DNS-detail" not in str(exc.value)


def test_deepseek_gateway_range_keeps_dns_pin_host_and_tls_sni(monkeypatch):
    _development_deepseek(monkeypatch)
    monkeypatch.setattr("intent_platform.network.socket.getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("198.18.0.205", 443))])
    assert checked_url(DEEPSEEK_CHAT_COMPLETIONS_URL, model_endpoint=True)[1] == "198.18.0.205"
    with pytest.raises(ValueError, match="allowlisted"):
        request_json("POST", DEEPSEEK_CHAT_COMPLETIONS_URL, payload={})
    seen = {}

    class Client:
        def __init__(self, **kwargs):
            seen["client"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        @contextmanager
        def stream(self, method, url, **kwargs):
            seen["method"] = method
            seen["url"] = str(url)
            seen["stream"] = kwargs
            yield httpx.Response(200, request=httpx.Request(method, url),
                                 stream=httpx.ByteStream(b'{"ok":true}'))

    monkeypatch.setattr("intent_platform.network.httpx.Client", Client)
    assert request_model_json(DEEPSEEK_CHAT_COMPLETIONS_URL, payload={"model": "deepseek-flash"}) == {"ok": True}
    assert seen["method"] == "POST"
    assert seen["url"] == "https://198.18.0.205/chat/completions"
    assert seen["stream"]["headers"]["Host"] == "api.deepseek.com"
    assert seen["stream"]["extensions"] == {"sni_hostname": "api.deepseek.com"}
    assert seen["client"]["follow_redirects"] is False and seen["client"]["trust_env"] is False


@pytest.mark.parametrize("address", ["10.0.0.1", "127.0.0.1", "169.254.1.2",
                                     "192.168.1.2", "100.64.1.2"])
def test_deepseek_gateway_exception_rejects_other_non_global_addresses(monkeypatch, address):
    _development_deepseek(monkeypatch)
    monkeypatch.setattr("intent_platform.network.socket.getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", (address, 443))])
    with pytest.raises(ValueError, match="approved gateway addresses"):
        checked_url(DEEPSEEK_CHAT_COMPLETIONS_URL, model_endpoint=True)


def test_public_deepseek_requires_development_opt_in(monkeypatch):
    config = _development_deepseek(monkeypatch)
    monkeypatch.setattr(settings(), "allow_public_deepseek_model_test", False)
    with pytest.raises(ValueError, match="allowlisted"):
        validate_config(config)
    with pytest.raises(ValueError, match="development-only"):
        Settings(_env_file=None, env="test", allow_public_deepseek_model_test=True)
    with pytest.raises(ValueError, match="development-only"):
        Settings(_env_file=None, env="production", allow_public_deepseek_model_test=True)


def test_public_deepseek_runtime_accepts_only_fixture_release(monkeypatch):
    config = _development_deepseek(monkeypatch)
    monkeypatch.setattr("intent_platform.model_profiles._connection", lambda *a: (config, "synthetic-secret"))
    seen = []
    monkeypatch.setattr("intent_platform.model_profiles.invoke_structured",
                        lambda *a: seen.append(a) or {"ready": True})
    fixtures = [{"definition": {"connector": {"kind": "fixture", "workflow_id": package["key"]}}}
                for package in function_packages()]
    real = {"definition": {"connector": {"kind": "http", "workflow_id": fixtures[0]["definition"]["connector"]["workflow_id"]}}}
    snapshot = {"capabilities": fixtures}
    assert structured_model_call(snapshot, "dev-admin", "ws-dev", "JSON", "synthetic") == {"ready": True}
    assert len(seen) == 1
    alien = {"definition": {"connector": {"kind": "fixture", "workflow_id": "other-demo"}}}
    for blocked in ({"capabilities": fixtures[:-1] + [real]},
                    {"capabilities": fixtures[:-1] + [alien]},
                    {"capabilities": fixtures[:-1]},
                    {"capabilities": fixtures[:-1] + [fixtures[0]]},
                    {"capabilities": [{"definition": None}]}, {}):
        with pytest.raises(ValueError, match="20-function demo release"):
            structured_model_call(blocked, "dev-admin", "ws-dev", "JSON", "synthetic")
    assert len(seen) == 1


@pytest.mark.parametrize("status,category", [(401, "MODEL_AUTH_REJECTED"),
                                             (402, "MODEL_QUOTA_EXHAUSTED"),
                                             (403, "MODEL_ACCESS_FORBIDDEN"),
                                             (429, "MODEL_RATE_LIMITED"),
                                             (503, "MODEL_REMOTE_FAILURE")])
def test_model_http_failures_report_only_safe_category(monkeypatch, status, category):
    config = payload()["config"]
    request = httpx.Request("POST", config["endpoint"])
    response = httpx.Response(status, request=request, json={"secret": "untrusted-sensitive-body"})

    def rejected(*args, **kwargs):
        raise httpx.HTTPStatusError("untrusted-sensitive-body", request=request, response=response)

    monkeypatch.setattr("intent_platform.model_profiles.request_model_json", rejected)
    with pytest.raises(ValueError, match=category) as exc:
        invoke_structured(config, "synthetic-secret", "JSON", "test")
    assert f"HTTP {status}" in str(exc.value)
    assert "untrusted-sensitive-body" not in str(exc.value)


def test_management_model_test_exposes_only_safe_http_category(model_client, admin, master, monkeypatch):
    created = model_client.post(PREFIX, headers=admin, json=payload()).json()
    request = httpx.Request("POST", "http://127.0.0.1:19999/v1/chat/completions")
    response = httpx.Response(402, request=request, json={"message": "untrusted-sensitive-body"})

    def rejected(*args, **kwargs):
        raise httpx.HTTPStatusError("untrusted-sensitive-body", request=request, response=response)

    monkeypatch.setattr("intent_platform.model_profiles.request_model_json", rejected)
    failed = model_client.post(PREFIX + f"/{created['id']}/versions/1/test", headers=admin)
    assert failed.status_code == 502 and failed.json()["detail"] == "MODEL_QUOTA_EXHAUSTED"
    assert "untrusted-sensitive-body" not in failed.text
