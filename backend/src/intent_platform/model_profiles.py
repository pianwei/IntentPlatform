"""Workspace-scoped, immutable model versions with server-only credentials."""

import json
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import Field, SecretStr
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .config import settings
from .db import engine, set_identity
from .models import ModelProfile, ModelProfileVersion
from .network import DEEPSEEK_CHAT_COMPLETIONS_URL, checked_url, request_model_json
from .schemas import StrictModel


class ModelConfig(StrictModel):
    protocol: str = Field(default="openai-compatible", pattern=r"^openai-compatible$")
    endpoint: str = Field(min_length=1, max_length=2000)
    model: str = Field(min_length=1, max_length=160)
    model_version: str = Field(min_length=1, max_length=160)
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    thinking_mode: Literal["default", "disabled"] = "default"


class ModelCreate(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    config: ModelConfig
    api_key: SecretStr


class ModelNewVersion(StrictModel):
    revision: int = Field(ge=1)
    config: ModelConfig
    api_key: SecretStr | None = None


class ModelStatusChange(StrictModel):
    revision: int = Field(ge=1)


def require_master_key():
    key = settings().model_secret_key
    if not key or len(key) < 16:
        raise ValueError("Server model-secret encryption key is unavailable")
    return key


def validate_config(config):
    config = ModelConfig.model_validate(config)
    parsed = urlsplit(config.endpoint)
    if parsed.query or parsed.fragment:
        raise ValueError("Model endpoint cannot contain a query or fragment")
    if config.thinking_mode == "disabled" and config.endpoint != DEEPSEEK_CHAT_COMPLETIONS_URL:
        raise ValueError("Disabled thinking is supported only by the approved DeepSeek endpoint")
    checked_url(config.endpoint, model_endpoint=True)
    return config.model_dump()


def encrypt_secret(db: Session, secret: str):
    if not secret or not secret.strip():
        raise ValueError("Model API credential is required")
    master = require_master_key()
    return db.scalar(text("SELECT pgp_sym_encrypt(:secret, :master)"), {"secret": secret, "master": master})


def decrypt_secret(db: Session, ciphertext: bytes | None):
    if not ciphertext:
        raise ValueError("Pinned model credential is unavailable")
    try:
        value = db.scalar(text("SELECT pgp_sym_decrypt(:cipher, :master)"),
                          {"cipher": ciphertext, "master": require_master_key()})
    except Exception:
        raise ValueError("Pinned model credential cannot be decrypted") from None
    if not isinstance(value, str) or not value:
        raise ValueError("Pinned model credential is unavailable")
    return value


def profile_version(db: Session, workspace_id: str, profile_id: str, version_id: str | None = None,
                    number: int | None = None, allow_disabled: bool = False):
    profile = db.scalar(select(ModelProfile).where(ModelProfile.id == profile_id,
                                                   ModelProfile.workspace_id == workspace_id))
    if not profile or (profile.disabled and not allow_disabled):
        raise ValueError("Pinned model profile is unavailable")
    query = select(ModelProfileVersion).where(ModelProfileVersion.workspace_id == workspace_id,
                                              ModelProfileVersion.profile_id == profile_id)
    if version_id:
        query = query.where(ModelProfileVersion.id == version_id)
    elif number is not None:
        query = query.where(ModelProfileVersion.version == number)
    else:
        query = query.where(ModelProfileVersion.version == profile.latest_version)
    version = db.scalar(query)
    if not version:
        raise ValueError("Pinned model version is unavailable")
    return profile, version


def version_view(version: ModelProfileVersion):
    return {"id": version.id, "profile_id": version.profile_id, "version": version.version,
            "config": ModelConfig.model_validate(version.config).model_dump(),
            "has_api_key": bool(version.secret_ciphertext), "created_at": version.created_at.isoformat()}


def profile_view(profile: ModelProfile, latest: ModelProfileVersion | None):
    return {"id": profile.id, "workspace_id": profile.workspace_id, "name": profile.name,
            "revision": profile.revision, "latest_version": profile.latest_version,
            "disabled": profile.disabled, "latest": version_view(latest) if latest else None}


def pinned_snapshot(profile: ModelProfile, version: ModelProfileVersion):
    if profile.disabled:
        raise ValueError("Pinned model profile is unavailable")
    config = ModelConfig.model_validate(version.config)
    if not version.secret_ciphertext:
        raise ValueError("Pinned model credential is unavailable")
    return {"mode": "openai", "profile_id": profile.id, "version_id": version.id,
            "name": config.model, "version": config.model_version, "url": config.endpoint,
            "temperature": 0, "thinking_mode": config.thinking_mode}


def _connection(snapshot: dict, user_id: str, workspace_id: str):
    selected = snapshot.get("model_profile", {})
    if selected.get("mode") != "openai":
        raise ValueError("Configured model is unavailable for structured planning")
    profile_id, version_id = selected.get("profile_id"), selected.get("version_id")
    if profile_id or version_id:
        if not profile_id or not version_id:
            raise ValueError("Pinned model profile and version must both be present")
        with Session(engine) as db:
            set_identity(db, user_id)
            _, version = profile_version(db, workspace_id, profile_id, version_id=version_id)
            config = ModelConfig.model_validate(version.config).model_dump()
            if any(selected.get(k) != config[v] for k, v in (("name", "model"), ("version", "model_version"),
                                                               ("url", "endpoint"))):
                raise ValueError("Pinned model snapshot differs from its immutable version")
            if selected.get("thinking_mode", "default") != config["thinking_mode"]:
                raise ValueError("Pinned model snapshot differs from its immutable version")
            secret = decrypt_secret(db, version.secret_ciphertext)
        validate_config(config)
        return config, secret
    cfg = settings()
    if cfg.model_mode != "openai" or not cfg.model_api_key or not cfg.model_url or not cfg.model_version:
        raise ValueError("Pinned legacy model dependency is unavailable")
    if any(selected.get(k) != v for k, v in (("name", cfg.model_name), ("version", cfg.model_version),
                                              ("url", cfg.model_url))):
        raise ValueError("Pinned legacy model dependency has changed")
    config = validate_config({"protocol": "openai-compatible", "endpoint": cfg.model_url,
                              "model": cfg.model_name, "model_version": cfg.model_version,
                              "timeout_seconds": cfg.model_timeout})
    return config, cfg.model_api_key


def invoke_structured(config: dict, secret: str, system: str, user: str):
    """Return one finite JSON object; never include remote text in error messages."""
    config = validate_config(config)
    if not secret:
        raise ValueError("Model credential is unavailable")
    body = {"model": config["model"], "temperature": 0, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if config["thinking_mode"] == "disabled":
        body["thinking"] = {"type": "disabled"}
        body["max_tokens"] = 4096
    try:
        response = request_model_json(config["endpoint"], payload=body,
                                      headers={"Authorization": "Bearer " + secret},
                                      timeout=config["timeout_seconds"])
        choice = response["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("Truncated model response")
        content = choice["message"]["content"]
        if not isinstance(content, str) or not content.strip() or len(content) > 256_000:
            raise ValueError("Oversize or non-text model response")
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError("Structured model result must be an object")
        return result
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        category = ({401: "MODEL_AUTH_REJECTED", 402: "MODEL_QUOTA_EXHAUSTED",
                     403: "MODEL_ACCESS_FORBIDDEN", 429: "MODEL_RATE_LIMITED"}.get(status)
                    or ("MODEL_REMOTE_FAILURE" if 500 <= status <= 599 else "MODEL_REQUEST_REJECTED"))
        # Remote bodies can contain unreviewed data. Surface only the code and
        # a fixed category, including to the administrator's connection test.
        raise ValueError(f"{category} (HTTP {status})") from None
    except httpx.TimeoutException:
        raise ValueError("MODEL_TIMEOUT") from None
    except httpx.RequestError:
        raise ValueError("MODEL_NETWORK_ERROR") from None
    except Exception:
        raise ValueError("MODEL_INVALID_RESPONSE") from None


def structured_model_call(snapshot: dict, user_id: str, workspace_id: str, system: str, user: str) -> dict:
    config, secret = _connection(snapshot, user_id, workspace_id)
    if (config["endpoint"] == DEEPSEEK_CHAT_COMPLETIONS_URL and
            settings().env == "development" and settings().allow_public_deepseek_model_test):
        # A development public call may include only this known synthetic
        # 20-function catalog, never real or ad-hoc fixture descriptions.
        from .demo.catalog import function_packages

        expected = {package["key"] for package in function_packages()}
        capabilities = snapshot.get("capabilities")

        def fixture_workflow(item):
            definition = item.get("definition") if isinstance(item, dict) else None
            connector = definition.get("connector") if isinstance(definition, dict) else None
            if isinstance(connector, dict) and connector.get("kind") == "fixture":
                return connector.get("workflow_id")
            return None

        workflows = [fixture_workflow(item) for item in capabilities] if isinstance(capabilities, list) else []
        if len(workflows) != len(expected) or set(workflows) != expected:
            raise ValueError("Public model testing requires the 20-function demo release")
    return invoke_structured(config, secret, system, user)
