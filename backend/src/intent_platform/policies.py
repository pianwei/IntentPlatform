"""Explicit authorization and immutable execution profile compatibility."""
from .config import settings


def permitted(resource, user_id):
    return resource.access_mode == "workspace" or user_id in resource.allowed_users


def effective_channels(agent, release):
    published = release.snapshot.get("channels", {"chat": True, "embed": True})
    return {k: bool(published.get(k) and agent.live_channels.get(k)) for k in ("chat", "embed")}


def dependency_error(snapshot):
    # These profiles are implemented by the current router/compiler/adapter code.
    # A deployment changing those implementations must add a distinct profile or reject old releases.
    if snapshot.get("dependency_profile", "platform-v1") not in {"platform-v1", "platform-v2", "platform-v3"}:
        return "Pinned execution profile is unavailable"
    from .ports import ROUTING_PROMPTS, CONNECTOR_IMPLEMENTATIONS
    from .ontology import COMPILERS
    if snapshot.get("prompt_policy_version") not in ROUTING_PROMPTS or snapshot.get("ontology_compiler_version") not in COMPILERS or snapshot.get("ui_schema_version") != "1":
        return "Pinned prompt, compiler or UI version is unavailable"
    cfg = settings()
    current_model = {"mode": cfg.model_mode, "name": cfg.model_name, "url": cfg.model_url, "temperature": 0}
    if "version" in snapshot.get("model_profile", {}):
        current_model["version"] = "development-deterministic-v1" if cfg.model_mode == "fixture" else cfg.model_version
    if snapshot.get("model_profile") != current_model:
        return "Pinned model profile is unavailable; no silent replacement"
    for c in snapshot.get("capabilities", []):
        version = c["definition"]["connector"].get("version", "1")
        if version not in CONNECTOR_IMPLEMENTATIONS.get(c["definition"]["connector"]["kind"], {}) or snapshot.get("connector_versions", {}).get(c["capability_id"]) != version:
            return "Pinned connector implementation is unavailable"
        if c.get("compiled", {}).get("compiler_version") != snapshot.get("compiler_versions", {}).get(c["capability_id"], snapshot["ontology_compiler_version"]):
            return "Pinned compiled ontology is unavailable"
    if snapshot.get("dependency_profile") == "platform-v3":
        from .resolvers import check_pinned_profiles
        if snapshot.get("context_policy", {}).get("version") != "context-v1":
            return "Pinned context policy is unavailable"
        if not check_pinned_profiles(snapshot.get("resolver_profiles", {}), snapshot["workspace_id"]):
            return "Pinned trusted resolver is unavailable"
    return None
