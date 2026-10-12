import json

import pytest
from pydantic import ValidationError

from intent_platform.config import Settings
from intent_platform.production_preflight import validate_authority_catalogs, verify_database_roles


def production_values():
    return {
        "env": "production",
        "identity_mode": "local",
        "allow_dev_auth": False,
        "model_mode": "openai",
        "database_url": "postgresql+psycopg://intent_app:private-app-test@db.bank.internal:5432/intent",
        "migration_database_url": "postgresql+psycopg://intent_owner:private-owner-test@db.bank.internal:5432/intent",
        "model_url": "https://model.bank.internal/v1/chat/completions",
        "model_name": "bank-approved-model",
        "model_version": "bank-model-2026-09",
        "model_api_key": "synthetic-model-key",
        "identity_introspection_url": "https://identity.bank.internal/introspect",
        "identity_service_key": "synthetic-identity-key",
        "object_authorization_url": "https://object.bank.internal/check",
        "object_authorization_key": "synthetic-object-key",
        "allowed_hosts": "model.bank.internal,identity.bank.internal,object.bank.internal",
        "cors_origins": "https://portal.bank.internal",
        "integrations_json": json.dumps({"bank-portal": {
            "label": "Bank portal", "version": "1", "workspace_ids": ["ws-bank"],
            "channels": ["chat", "embed"], "origins": ["https://portal.bank.internal"],
            "agent_ids": [], "user_ids": ["employee-1"]}}),
        "membership_grants_json": json.dumps({"ws-bank": {
            "employee-1": {"roles": ["admin", "user"], "active": True}}}),
    }


def production(**changes):
    values = production_values()
    values.update(changes)
    return Settings(_env_file=None, **values)


def test_production_config_accepts_only_explicit_safe_values():
    cfg = production()
    assert cfg.env == "production"
    validate_authority_catalogs(cfg)


@pytest.mark.parametrize("field", [
    "model_version", "model_api_key", "object_authorization_key", "cors_origins", "allowed_hosts",
])
def test_production_rejects_missing_required_values(field, monkeypatch):
    monkeypatch.delenv("INTENT_" + field.upper(), raising=False)
    values = production_values()
    values.pop(field)
    with pytest.raises(ValidationError, match="Production configuration is incomplete"):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize("change", [
    {"allow_dev_auth": True}, {"model_mode": "fixture"},
    {"database_url": "postgresql+psycopg://intent_app:local-app-only@postgres:5432/intent"},
    {"database_url": "postgresql+psycopg://intent_app:private@127.0.0.1:5432/intent"},
    {"migration_database_url": "postgresql+psycopg://intent_app:private@db.bank.internal:5432/intent"},
    {"model_name": "development-deterministic-v1"},
    {"model_url": "https://public.example/v1/chat/completions"},
    {"allowed_hosts": "model.bank.internal,identity.bank.internal,object.bank.internal,localhost"},
    {"allowed_hosts": "model.bank.internal,identity.bank.internal,object.bank.internal,8.8.8.8"},
    {"cors_origins": "http://localhost:5173"},
    {"cors_origins": "https://portal.bank.internal,*"},
    {"identity_mode": "legacy-test"},
])
def test_production_rejects_development_or_unapproved_configuration(change):
    with pytest.raises(ValidationError):
        production(**change)


def test_preflight_checks_catalog_shape_without_echoing_secret_values():
    cfg = production(credentials_json=json.dumps(["sensitive-synthetic-example"]))
    with pytest.raises(RuntimeError, match="catalogs are invalid") as exc:
        validate_authority_catalogs(cfg)
    assert "sensitive-synthetic-example" not in str(exc.value)


def test_production_local_identity_does_not_require_retired_sso_configuration():
    values = production_values()
    for key in ("identity_introspection_url", "identity_service_key", "integrations_json", "membership_grants_json"):
        values.pop(key)
    cfg = Settings(_env_file=None, **values)
    assert cfg.identity_mode == "local" and not cfg.allow_dev_auth
    validate_authority_catalogs(cfg)


def test_preflight_rejects_same_database_role(monkeypatch):
    monkeypatch.setattr("intent_platform.production_preflight._database_role", lambda _: "same-role")
    with pytest.raises(RuntimeError, match="role preflight failed"):
        verify_database_roles(production())


def test_rejected_configuration_does_not_echo_supplied_secret():
    with pytest.raises(ValidationError) as exc:
        production(model_api_key="synthetic-do-not-log-this", cors_origins="http://localhost:5173")
    assert "synthetic-do-not-log-this" not in str(exc.value)
