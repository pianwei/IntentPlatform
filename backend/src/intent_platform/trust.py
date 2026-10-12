"""Finite server authority; browser selections may only narrow these grants."""
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, Field, field_validator
from fastapi import HTTPException
from .config import settings
from .origins import exact_origin

ROLES = {"admin", "maintainer", "publisher", "user"}


class TrustModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Delegation(TrustModel):
    integration_id: StrictStr = Field(min_length=1, max_length=160)
    workspace_ids: list[StrictStr]
    agent_ids: list[StrictStr]
    user_ids: list[StrictStr]
    origins: list[StrictStr]

    @field_validator("workspace_ids", "agent_ids", "user_ids", "origins")
    @classmethod
    def exact_values(cls, values):
        if len(values) > 1000 or len(values) != len(set(values)) or any(not v.strip() or v != v.strip() or "*" in v for v in values):
            raise ValueError("Delegation requires finite exact values")
        return values

    @field_validator("origins")
    @classmethod
    def exact_origins(cls, values):
        return [exact_origin(v) for v in values]


class Integration(TrustModel):
    label: StrictStr = Field(min_length=1, max_length=160)
    version: StrictStr = Field(min_length=1, max_length=160)
    workspace_ids: list[StrictStr]
    channels: list[Literal["chat", "embed"]]
    origins: list[StrictStr]
    agent_ids: list[StrictStr]
    user_ids: list[StrictStr]


def integration_catalog(ws):
    cfg = settings(); result = {}
    if cfg.identity_mode == "local":
        return {"local": {"label": "本地账号与公开访问", "version": "local-v1", "workspace_ids": [ws], "channels": ["chat", "embed"], "origins": [], "agent_ids": [], "user_ids": [], "fixture_mode": False}}
    data = json.loads(cfg.integrations_json)
    if not isinstance(data, dict): raise ValueError("Invalid server integration catalog")
    for ref, raw in data.items():
        if not isinstance(ref, str) or not ref.strip() or ref == "development": raise ValueError("Invalid or reserved integration reference")
        profile = Integration.model_validate(raw).model_dump()
        Delegation(integration_id=ref, **{k: profile[k] for k in ("workspace_ids", "agent_ids", "user_ids", "origins")})
        if ws in profile["workspace_ids"]: result[ref] = profile
    if cfg.env != "production" and cfg.allow_dev_auth:
        result["development"] = {"label": "Development identity fixture", "version": "development-v1", "workspace_ids": [ws], "channels": ["chat", "embed"], "origins": [], "agent_ids": [], "user_ids": [], "fixture_mode": True}
    return result


def token_integration(token):
    if settings().identity_mode == "local":
        if token.source != "local": raise ValueError("Historical identity is read-only")
        return "local"
    if token.source == "development":
        if settings().env == "production" or not settings().allow_dev_auth: raise ValueError("Development identity unavailable")
        return "development"
    if token.source != "introspection": raise ValueError("Unknown identity source")
    claims = token.delegation if token.scope == "management" else {k: token.delegation[k] for k in Delegation.model_fields if k in token.delegation}
    return Delegation.model_validate(claims).integration_id


def selected_profiles(agent):
    catalog = integration_catalog(agent.workspace_id)
    if settings().identity_mode == "local":
        if "local" in agent.revoked_integration_refs: raise ValueError("Local application access is revoked")
        return catalog
    selected = agent.integration_refs
    if not selected or any(ref not in catalog or ref in agent.revoked_integration_refs for ref in selected):
        raise ValueError("Select currently approved, non-revoked integrations before publication")
    profiles = {ref: catalog[ref] for ref in selected}
    if any(ref != "development" and (agent.id not in profile["agent_ids"] or not profile["user_ids"]) for ref, profile in profiles.items()):
        raise ValueError("Selected integration does not approve this application or any users")
    for channel, enabled in agent.channels.items():
        if enabled and not any(channel in p["channels"] for p in profiles.values()): raise ValueError("Enabled channel has no selected integration")
    return profiles


def integration_allowed(agent, release, token, channel, origin=None, bound_id=None, policy_snapshot=None):
    try:
        ref = token_integration(token)
        if settings().identity_mode == "local":
            policy = (release.snapshot if policy_snapshot is None else policy_snapshot).get("identity_policy", {})
            return ref == "local" and (bound_id is None or bound_id == "local") and "local" not in agent.revoked_integration_refs and policy.get("version") == "local-v1" and release.snapshot.get("identity_policy", {}).get("version") == "local-v1"
        if bound_id is not None and bound_id != ref: return False
        if ref in agent.revoked_integration_refs: return False
        profile = integration_catalog(agent.workspace_id).get(ref)
        if not profile or channel not in profile["channels"]: return False
        policy = (release.snapshot if policy_snapshot is None else policy_snapshot).get("identity_policy")
        if policy and (policy.get("version") != "integration-v1" or policy.get("profiles", {}).get(ref) != profile): return False
        if ref == "local": return token.source == "local"
        if ref == "development": return True
        claim = Delegation.model_validate({k: token.delegation[k] for k in Delegation.model_fields if k in token.delegation})
        for key, value in (("workspace_ids", agent.workspace_id), ("agent_ids", agent.id), ("user_ids", token.user_id)):
            if value not in profile[key] or value not in getattr(claim, key): return False
        return channel != "embed" or origin in profile["origins"] and origin in claim.origins
    except (ValueError, TypeError, KeyError):
        return False


def trusted_members(ws):
    if settings().identity_mode == "local":
        return None  # Local membership records replace the retired SSO roster.
    data = json.loads(settings().membership_grants_json)
    if not isinstance(data, dict): raise ValueError("Invalid membership grants")
    if ws not in data: return None
    roster = data[ws]
    if not isinstance(roster, dict): raise ValueError("Invalid workspace roster")
    for user, grant in roster.items():
        if not isinstance(user, str) or not user.strip() or not isinstance(grant, dict) or set(grant) != {"roles", "active"} or type(grant["active"]) is not bool or not isinstance(grant["roles"], list) or any(r not in ROLES for r in grant["roles"]):
            raise ValueError("Invalid trusted member ceiling")
    return roster


def member_authority(member):
    if not member: return [], False
    roster = trusted_members(member.workspace_id)
    grant = roster.get(member.user_id, {"roles": [], "active": False}) if roster is not None else {"roles": member.role_ceiling, "active": True}
    roles = [role for role in member.roles if role in grant["roles"]]
    return roles, bool(member.active and grant["active"] and roles)


def member_ceiling(ws, user_id, member=None):
    roster = trusted_members(ws)
    if roster is not None: return roster.get(user_id, {"roles": [], "active": False})
    if member:
        return {"roles": member.role_ceiling, "active": True}
    # A trusted directory establishes identity only; this does not create a
    # membership. The space administrator explicitly chooses the actual roles.
    from .workspace_admin import identity_directory
    return {"roles": sorted(ROLES), "active": True} if user_id in identity_directory() else {"roles": [], "active": False}


def identity_workspace_allowed(token, ws):
    try:
        ref = token_integration(token)
        if ref == "local": return token.source == "local"
        if ref == "development": return True
        claim = Delegation.model_validate({k: token.delegation[k] for k in Delegation.model_fields if k in token.delegation})
        profile = integration_catalog(ws).get(ref)
        return bool(profile and ws in claim.workspace_ids and token.user_id in claim.user_ids and token.user_id in profile["user_ids"])
    except (ValueError, TypeError, KeyError):
        return False
