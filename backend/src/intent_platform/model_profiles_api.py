"""Administrator-only model configuration; no credential appears in responses."""

from time import monotonic

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import audit, authenticate, manager
from .db import session, set_identity
from .models import ModelProfile, ModelProfileVersion
from .model_profiles import (
    ModelConfig, ModelCreate, ModelNewVersion, ModelStatusChange,
    decrypt_secret, encrypt_secret, invoke_structured, profile_version,
    profile_view, validate_config, version_view,
)
from .schemas import StrictModel


router = APIRouter()
PREFIX = "/api/v2/management/workspaces/{ws}/model-profiles"


class ModelVersionOut(StrictModel):
    id: str
    profile_id: str
    version: int
    config: ModelConfig
    has_api_key: bool
    created_at: str


class ModelProfileOut(StrictModel):
    id: str
    workspace_id: str
    name: str
    revision: int
    latest_version: int
    disabled: bool
    latest: ModelVersionOut | None


class ModelTestOut(StrictModel):
    ok: bool
    profile_id: str
    version: int
    elapsed_ms: float = Field(ge=0)


def _model_error(exc, status=409):
    # Every error in the profile service is deliberately secret-free.
    raise HTTPException(status, str(exc)) from None


def _read_profile(db, ws, profile_id, lock=False):
    query = select(ModelProfile).where(ModelProfile.workspace_id == ws, ModelProfile.id == profile_id)
    if lock:
        query = query.with_for_update()
    profile = db.scalar(query)
    if not profile:
        raise HTTPException(404, "Model profile unavailable")
    return profile


@router.get(PREFIX, response_model=list[ModelProfileOut])
def list_model_profiles(ws: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "publisher")
    profiles = db.scalars(select(ModelProfile).where(ModelProfile.workspace_id == ws).order_by(ModelProfile.name, ModelProfile.id)).all()
    rows = []
    for profile in profiles:
        latest = db.scalar(select(ModelProfileVersion).where(ModelProfileVersion.workspace_id == ws,
                                                             ModelProfileVersion.profile_id == profile.id,
                                                             ModelProfileVersion.version == profile.latest_version))
        rows.append(profile_view(profile, latest))
    return rows


@router.post(PREFIX, response_model=ModelProfileOut, status_code=201)
def create_model_profile(ws: str, body: ModelCreate, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "admin")
    try:
        config = validate_config(body.config)
        cipher = encrypt_secret(db, body.api_key.get_secret_value())
    except ValueError as exc:
        _model_error(exc, 422)
    profile = ModelProfile(workspace_id=ws, name=body.name)
    db.add(profile); db.flush()
    profile.latest_version = 1
    version = ModelProfileVersion(workspace_id=ws, profile_id=profile.id, version=1,
                                  config=config, secret_ciphertext=cipher)
    db.add(version); db.flush()
    audit(db, token.user_id, ws, "model_profile.create", profile.id,
          {"version": 1, "model": config["model"], "model_version": config["model_version"]})
    result = profile_view(profile, version)
    db.commit()
    return result


@router.get(PREFIX + "/{profile_id}/versions", response_model=list[ModelVersionOut])
def list_model_versions(ws: str, profile_id: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "publisher")
    _read_profile(db, ws, profile_id)
    versions = db.scalars(select(ModelProfileVersion).where(ModelProfileVersion.workspace_id == ws,
                                                             ModelProfileVersion.profile_id == profile_id)
                          .order_by(ModelProfileVersion.version.desc())).all()
    return [version_view(version) for version in versions]


@router.post(PREFIX + "/{profile_id}/versions", response_model=ModelProfileOut, status_code=201)
def create_model_version(ws: str, profile_id: str, body: ModelNewVersion,
                         token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "admin")
    profile = _read_profile(db, ws, profile_id, lock=True)
    if profile.disabled:
        raise HTTPException(409, "Disabled model profile cannot receive a version")
    if body.revision != profile.revision:
        raise HTTPException(409, "Model profile revision changed")
    try:
        config = validate_config(body.config)
        if body.api_key is None:
            old = db.scalar(select(ModelProfileVersion).where(ModelProfileVersion.workspace_id == ws,
                                                               ModelProfileVersion.profile_id == profile_id,
                                                               ModelProfileVersion.version == profile.latest_version))
            if not old:
                raise ValueError("Previous model credential is unavailable")
            decrypt_secret(db, old.secret_ciphertext)
            cipher = old.secret_ciphertext
        else:
            cipher = encrypt_secret(db, body.api_key.get_secret_value())
    except ValueError as exc:
        _model_error(exc, 422)
    profile.latest_version += 1
    profile.revision += 1
    version = ModelProfileVersion(workspace_id=ws, profile_id=profile_id,
                                  version=profile.latest_version, config=config,
                                  secret_ciphertext=cipher)
    db.add(version); db.flush()
    audit(db, token.user_id, ws, "model_profile.version", profile_id,
          {"version": version.version, "model": config["model"], "model_version": config["model_version"]})
    result = profile_view(profile, version)
    db.commit()
    return result


@router.post(PREFIX + "/{profile_id}/disable", response_model=ModelProfileOut)
def disable_model_profile(ws: str, profile_id: str, body: ModelStatusChange,
                          token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "admin")
    profile = _read_profile(db, ws, profile_id, lock=True)
    if body.revision != profile.revision:
        raise HTTPException(409, "Model profile revision changed")
    if not profile.disabled:
        profile.disabled = True
        profile.revision += 1
        audit(db, token.user_id, ws, "model_profile.disable", profile_id, {})
    latest = db.scalar(select(ModelProfileVersion).where(ModelProfileVersion.workspace_id == ws,
                                                         ModelProfileVersion.profile_id == profile_id,
                                                         ModelProfileVersion.version == profile.latest_version))
    result = profile_view(profile, latest)
    db.commit()
    return result


@router.post(PREFIX + "/{profile_id}/versions/{version_number}/test", response_model=ModelTestOut)
def test_model_profile(ws: str, profile_id: str, version_number: int,
                       token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "admin")
    try:
        _, version = profile_version(db, ws, profile_id, number=version_number)
        config = ModelConfig.model_validate(version.config).model_dump()
        secret = decrypt_secret(db, version.secret_ciphertext)
        validate_config(config)
    except ValueError as exc:
        _model_error(exc)
    db.commit()  # Do not hold the membership RLS transaction across remote I/O.
    started = monotonic()
    try:
        reply = invoke_structured(config, secret,
                                  'Return only JSON object {"ready": true}.',
                                  'Check structured JSON response support.')
        if reply.get("ready") is not True:
            raise ValueError("Model structured response did not match the test contract")
    except ValueError:
        raise HTTPException(502, "Model connection or structured output test failed") from None
    elapsed = round((monotonic() - started) * 1000, 2)
    set_identity(db, token.user_id)
    audit(db, token.user_id, ws, "model_profile.test", profile_id,
          {"version": version_number, "ok": True, "elapsed_ms": elapsed})
    db.commit()
    return {"ok": True, "profile_id": profile_id, "version": version_number, "elapsed_ms": elapsed}
