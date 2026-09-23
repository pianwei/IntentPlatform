"""Explicit authorization and immutable execution profile compatibility."""
from .config import settings


def permitted(resource, user_id):
    return resource.access_mode == "workspace" or user_id in resource.allowed_users


def effective_channels(agent, release):
    published = release.snapshot.get("channels", {"chat": True, "embed": True})
    return {k: bool(published.get(k) and agent.live_channels.get(k)) for k in ("chat", "embed")}


def dependency_error(snapshot, db=None):
    # These profiles are implemented by the current router/compiler/adapter code.
    # A deployment changing those implementations must add a distinct profile or reject old releases.
    if snapshot.get("dependency_profile", "platform-v1") not in {"platform-v1", "platform-v2", "platform-v3", "platform-v4"}:
        return "Pinned execution profile is unavailable"
    from .ports import ROUTING_PROMPTS, CONNECTOR_IMPLEMENTATIONS
    from .ontology import COMPILERS
    if snapshot.get("prompt_policy_version") not in ROUTING_PROMPTS or snapshot.get("ontology_compiler_version") not in COMPILERS or snapshot.get("ui_schema_version") != "1":
        return "Pinned prompt, compiler or UI version is unavailable"
    profile = snapshot.get("model_profile", {})
    if snapshot.get("dependency_profile") == "platform-v4" and profile.get("profile_id"):
        if not profile.get("version_id") or not profile.get("version") or not profile.get("url"):
            return "Pinned model profile is incomplete"
    else:
        cfg = settings()
        current_model = {"mode": cfg.model_mode, "name": cfg.model_name, "url": cfg.model_url, "temperature": 0}
        if "version" in profile:
            current_model["version"] = "development-deterministic-v1" if cfg.model_mode == "fixture" else cfg.model_version
        if profile != current_model:
            return "Pinned model profile is unavailable; no silent replacement"
    for c in snapshot.get("capabilities", []):
        version = c["definition"]["connector"].get("version", "1")
        if version not in CONNECTOR_IMPLEMENTATIONS.get(c["definition"]["connector"]["kind"], {}) or snapshot.get("connector_versions", {}).get(c["capability_id"]) != version:
            return "Pinned connector implementation is unavailable"
        if c.get("compiled", {}).get("compiler_version") != snapshot.get("compiler_versions", {}).get(c["capability_id"], snapshot["ontology_compiler_version"]):
            return "Pinned compiled ontology is unavailable"
    if snapshot.get("dependency_profile") in {"platform-v3", "platform-v4"} and snapshot.get("domain_packages"):
        from .resolvers import check_pinned_profiles
        if snapshot.get("context_policy", {}).get("version") != "context-v1":
            return "Pinned context policy is unavailable"
        if not check_pinned_profiles(snapshot.get("resolver_profiles", {}), snapshot["workspace_id"]):
            return "Pinned trusted resolver is unavailable"
    if snapshot.get("dependency_profile") == "platform-v4":
        if snapshot.get("planning_policy", {}).get("version") != "finite-plan-v1":
            return "Pinned planner implementation is unavailable"
        if db is not None:
            from .models import BusinessSkill, BusinessSkillVersion, ModelProfile, ModelProfileVersion, ActionDefinitionVersion
            for item in snapshot.get("business_skills", []):
                skill = db.get(BusinessSkill, item["skill_id"])
                version = db.get(BusinessSkillVersion, item["version_id"])
                if not skill or skill.disabled or not version or version.skill_id != skill.id:
                    return "Pinned business Skill is unavailable"
            for item in snapshot.get("action_definitions", []):
                version = db.get(ActionDefinitionVersion, item["version_id"])
                if not version or version.action_id != item["action_id"]:
                    return "Pinned action definition is unavailable"
            if profile.get("profile_id"):
                row = db.get(ModelProfile, profile["profile_id"])
                version = db.get(ModelProfileVersion, profile["version_id"])
                if not row or row.disabled or not version or version.profile_id != row.id or version.workspace_id != snapshot.get("workspace_id"):
                    return "Pinned model profile is unavailable"
    return None
