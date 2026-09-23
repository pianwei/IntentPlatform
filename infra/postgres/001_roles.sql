-- Local development only. Production roles and secrets are supplied by the bank.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE ROLE intent_owner LOGIN PASSWORD 'local-owner-only' NOSUPERUSER NOBYPASSRLS;
CREATE ROLE intent_app LOGIN PASSWORD 'local-app-only' NOSUPERUSER NOBYPASSRLS;
ALTER SCHEMA public OWNER TO intent_owner;
GRANT CONNECT ON DATABASE intent TO intent_owner, intent_app;
GRANT USAGE ON SCHEMA public TO intent_app;
ALTER DEFAULT PRIVILEGES FOR ROLE intent_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO intent_app;
ALTER DEFAULT PRIVILEGES FOR ROLE intent_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO intent_app;
