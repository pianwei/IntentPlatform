"""Read-only checks for the separate production Compose entry point."""
import json

from sqlalchemy import create_engine, text

from .config import settings
from .trust import Delegation, Integration, ROLES


def validate_authority_catalogs(cfg):
    try:
        integrations = {} if cfg.identity_mode == "local" else json.loads(cfg.integrations_json)
        memberships = {} if cfg.identity_mode == "local" else json.loads(cfg.membership_grants_json)
        if not isinstance(integrations, dict) or not isinstance(memberships, dict) or (cfg.identity_mode != "local" and (not integrations or not memberships)):
            raise ValueError("Empty trusted authority catalog")
        for ref, raw in integrations.items():
            if not isinstance(ref, str) or not ref.strip() or ref == "development":
                raise ValueError("Invalid trusted integration reference")
            profile = Integration.model_validate(raw)
            Delegation(integration_id=ref, workspace_ids=profile.workspace_ids, agent_ids=profile.agent_ids,
                       user_ids=profile.user_ids, origins=profile.origins)
        for workspace, roster in memberships.items():
            if not isinstance(workspace, str) or not workspace.strip() or not isinstance(roster, dict):
                raise ValueError("Invalid trusted membership catalog")
            for user, grant in roster.items():
                if (not isinstance(user, str) or not user.strip() or not isinstance(grant, dict)
                        or set(grant) != {"roles", "active"} or type(grant["active"]) is not bool
                        or not isinstance(grant["roles"], list) or any(role not in ROLES for role in grant["roles"])):
                    raise ValueError("Invalid trusted member grant")
        for name in ("credentials_json", "connector_targets_json", "resolvers_json",
                     "workspace_timezones_json", "workspace_limits_json"):
            if not isinstance(json.loads(getattr(cfg, name)), dict):
                raise ValueError("Invalid server-owned catalog")
    except (TypeError, ValueError, KeyError):
        # Do not print raw catalog values: some entries can contain credentials.
        raise RuntimeError("Production authority catalogs are invalid") from None


def _database_role(url):
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            row = connection.execute(text("SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).one()
            if row.rolsuper or row.rolbypassrls:
                raise RuntimeError("Production database roles must not be superuser or bypass RLS")
            return row.rolname
    finally:
        engine.dispose()


def verify_database_roles(cfg):
    try:
        app_role = _database_role(cfg.database_url)
        migration_role = _database_role(cfg.migration_database_url)
        if app_role == migration_role:
            raise RuntimeError("App and migration connections must use distinct database roles")
    except Exception:
        # Connection exceptions can include internal service addresses. Keep this preflight output small.
        raise RuntimeError("Production PostgreSQL role preflight failed") from None


def main():
    cfg = settings()
    if cfg.env != "production":
        raise RuntimeError("Production preflight requires INTENT_ENV=production")
    validate_authority_catalogs(cfg)
    verify_database_roles(cfg)
    print("Production configuration, trusted catalogs and PostgreSQL roles are ready")


if __name__ == "__main__":
    main()
