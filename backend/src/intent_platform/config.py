from functools import lru_cache
import ipaddress
import json
from urllib.parse import urlsplit
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from .origins import exact_origin


PRODUCTION_REQUIRED = (
    "database_url", "migration_database_url", "model_url", "model_name", "model_version",
    "model_api_key", "identity_introspection_url", "identity_service_key",
    "object_authorization_url", "object_authorization_key", "allowed_hosts", "cors_origins",
    "integrations_json", "membership_grants_json",
)


def _unsafe_production_host(host):
    if not host or host.lower() in {"localhost", "host.docker.internal", "api", "web", "postgres"} or host.lower().endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
        return (address.is_loopback or address.is_link_local or address.is_multicast
                or address.is_unspecified or not address.is_private)
    except ValueError:
        return False


def _database_role(url):
    try:
        parsed = make_url(url)
        if parsed.drivername != "postgresql+psycopg" or not parsed.username or not parsed.password or not parsed.database or _unsafe_production_host(parsed.host):
            raise ValueError("Invalid production PostgreSQL role or endpoint")
        if parsed.password in {"local-app-only", "local-owner-only", "local-admin-only"} or parsed.database.endswith("_test"):
            raise ValueError("Development database settings are forbidden in production")
        return parsed
    except Exception:
        # SQLAlchemy parsing errors can contain the supplied URL; never echo credentials.
        raise ValueError("Production requires a distinct non-development PostgreSQL role and database") from None


def _production_endpoint(url, allowed_hosts):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
                or parsed.fragment or _unsafe_production_host(parsed.hostname) or parsed.hostname.lower() not in allowed_hosts):
            raise ValueError("Unsafe endpoint")
    except ValueError:
        raise ValueError("Production service endpoints must use explicit allowlisted non-local hosts") from None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INTENT_", env_file=".env", extra="ignore",
                                      hide_input_in_errors=True)
    env: str = "development"
    database_url: str = "postgresql+psycopg://intent_app:local-app-only@127.0.0.1:54329/intent"
    migration_database_url: str = "postgresql+psycopg://intent_owner:local-owner-only@127.0.0.1:54329/intent"
    allow_dev_auth: bool = True
    model_mode: str = "fixture"
    model_url: str = ""
    model_name: str = "development-deterministic-v1"
    model_version: str | None = None
    model_api_key: str = ""
    model_secret_key: str = ""
    model_timeout: float = 30
    allowed_hosts: str = "localhost,127.0.0.1"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    identity_introspection_url: str = ""
    identity_service_key: str = ""
    object_authorization_url: str = ""
    object_authorization_key: str = ""
    credentials_json: str = "{}"
    connector_targets_json: str = "{}"
    worker_poll_seconds: float = 0.5
    worker_lease_seconds: int = 60
    confirmation_seconds: int = 120
    token_seconds: int = 3600
    ticket_seconds: int = 60
    max_upload_bytes: int = 10_000_000
    ingest_max_concurrency_per_workspace: int = 2
    ingest_max_queued_per_workspace: int = 20
    ingest_lease_seconds: int = 120
    resolvers_json: str = "{}"
    workspace_timezones_json: str = "{}"
    integrations_json: str = "{}"
    membership_grants_json: str = "{}"
    workspace_limits_json: str = "{}"
    allow_shadow_evaluation: bool = False

    @model_validator(mode="after")
    def safety(self):
        if not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("Only PostgreSQL with psycopg is supported")
        if self.env not in {"development", "test", "production"}:
            raise ValueError("Unknown environment")
        if self.model_mode not in {"fixture", "openai"}:
            raise ValueError("model_mode must be fixture or openai")
        if not 1 <= self.ingest_max_concurrency_per_workspace <= 16 or not 1 <= self.ingest_max_queued_per_workspace <= 500 or not 30 <= self.ingest_lease_seconds <= 3600:
            raise ValueError("Ingest concurrency, queue and lease limits are outside the finite bounds")
        if self.env == "production":
            if self.allow_dev_auth or self.model_mode == "fixture":
                raise ValueError("Production forbids development identity and fixture model")
            missing = [name for name in PRODUCTION_REQUIRED
                       if name not in self.model_fields_set or not str(getattr(self, name) or "").strip()]
            if missing:
                raise ValueError("Production configuration is incomplete: " + ", ".join(missing))
            runtime = _database_role(self.database_url)
            migration = _database_role(self.migration_database_url)
            if (runtime.username == migration.username or runtime.database != migration.database):
                raise ValueError("Production requires separate app and migration roles for one database")
            if self.model_name == "development-deterministic-v1" or self.model_version == "development-deterministic-v1":
                raise ValueError("Production requires an explicit non-development model identity")
            allowed = [host.strip().lower() for host in self.allowed_hosts.split(",")]
            if (not allowed or len(allowed) != len(set(allowed)) or
                    any(not host or "*" in host or "/" in host or _unsafe_production_host(host) for host in allowed)):
                raise ValueError("Production requires exact non-local outbound hosts")
            for endpoint in (self.model_url, self.identity_introspection_url, self.object_authorization_url):
                _production_endpoint(endpoint, set(allowed))
            origins = [origin.strip() for origin in self.cors_origins.split(",")]
            if not origins or len(origins) != len(set(origins)):
                raise ValueError("Production requires explicit unique browser origins")
            try:
                if any(exact_origin(origin) != origin or not origin.startswith("https://") or
                       _unsafe_production_host(urlsplit(origin).hostname) for origin in origins):
                    raise ValueError("Unsafe production origin")
            except ValueError:
                raise ValueError("Production requires exact HTTPS browser origins") from None
            for name in ("integrations_json", "membership_grants_json"):
                try:
                    catalog = json.loads(getattr(self, name))
                    if not isinstance(catalog, dict) or not catalog:
                        raise ValueError("Empty catalog")
                except (TypeError, ValueError):
                    raise ValueError("Production requires non-empty trusted identity and membership catalogs") from None
        return self

    @property
    def fixture_mode(self):
        return self.model_mode == "fixture"


@lru_cache
def settings():
    return Settings()
