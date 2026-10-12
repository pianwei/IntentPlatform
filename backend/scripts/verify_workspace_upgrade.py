"""Verify 0013 -> 0014 on a fresh isolated database using value comparisons."""
import json
import re
import sys
from datetime import timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, MetaData, Table, insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from intent_platform.config import settings
from intent_platform.db import now, set_identity


def main():
    cfg = settings()
    urls = [make_url(u) for u in (cfg.database_url, cfg.migration_database_url)]
    if cfg.env != "test" or not re.fullmatch(r"intent_[a-z0-9_]+_test", urls[0].database or "") or len({(u.host, u.port, u.database) for u in urls}) != 1:
        raise RuntimeError("Only an isolated workspace upgrade test database is permitted")
    owner, runtime = create_engine(urls[1]), create_engine(urls[0])
    with owner.connect() as db:
        if db.scalar(text("SELECT to_regclass('public.alembic_version')")):
            raise RuntimeError("Use an empty disposable database")
    migrations = Config()
    import intent_platform
    migrations.set_main_option("script_location", str(Path(intent_platform.__file__).parent / "migrations"))
    command.upgrade(migrations, "0013")
    from intent_platform.models import (Membership, Function, FunctionVersion, Agent, Release,
                                       AuthToken, Conversation, Run, Call, Event)
    with Session(owner) as db, db.begin():
        set_identity(db, "dev-admin")
        db.execute(text("INSERT INTO workspaces(id,name) VALUES ('ws-upgrade','Historical upgrade space')"))
        db.add(Membership(user_id="dev-admin", workspace_id="ws-upgrade", roles=["admin", "user"]))
        function = Function(id="fn-upgrade", workspace_id="ws-upgrade", definition={"name": "Historical capability"}, status="published", latest_version=1)
        agent = Agent(id="agent-upgrade", workspace_id="ws-upgrade", name="Historical agent", capability_ids=[function.id])
        db.add_all([function, agent]); db.flush()
        release = Release(id="release-upgrade", workspace_id="ws-upgrade", agent_id=agent.id, version=1,
                          snapshot={"fixture_mode": True, "capabilities": [], "channels": {"chat": True, "embed": True}, "allowed_origins": ["http://localhost:5173"]})
        db.add_all([release, FunctionVersion(workspace_id="ws-upgrade", function_id=function.id, version=1, definition=function.definition, compiled={"version": "legacy-fixture"})]); db.flush()
        agent.current_release_id = release.id
        legacy_session = Table("runtime_sessions", MetaData(), autoload_with=db.connection())
        db.execute(insert(legacy_session).values(id="session-upgrade", workspace_id="ws-upgrade", agent_id=agent.id, user_id="dev-admin",
                   channel="chat", revoked=False, legacy_claimable=False, created_at=now(), integration_id="development"))
        token = AuthToken(id="synthetic-upgrade-token", user_id="dev-admin", scope="runtime", agent_id=agent.id,
                          runtime_session_id="session-upgrade", expires_at=now()+timedelta(hours=1))
        db.add(token); db.flush()
        conversation = Conversation(id="conv-upgrade", workspace_id="ws-upgrade", agent_id=agent.id, release_id=release.id,
                                    user_id="dev-admin", token_id=token.id, runtime_session_id="session-upgrade")
        db.add(conversation); db.flush()
        run = Run(id="run-upgrade", workspace_id="ws-upgrade", conversation_id=conversation.id, user_id="dev-admin",
                  client_message_id="historical-request", text="Historical query", status="SUCCEEDED")
        db.add(run); db.flush()
        db.add(Call(workspace_id="ws-upgrade", run_id=run.id, action_id="action_0", revision=1, capability_id=function.id, version=1,
                    definition=function.definition, arguments={}, status="SUCCEEDED", result={"data": {"amount": "12.00"}}))
        db.add(Event(workspace_id="ws-upgrade", conversation_id=conversation.id, run_id=run.id, revision=1, sequence=1,
                     event_type="RESULT", payload={"data": {"amount": "12.00"}}))
    tables = ["function_versions", "agent_releases", "conversations", "runs", "calls", "events", "auth_tokens"]
    canonical = lambda rows: sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)
    with owner.connect() as db:
        before = {table: canonical(db.execute(text(f'SELECT to_jsonb(t) FROM "{table}" t')).scalars()) for table in tables}
    command.upgrade(migrations, "head")
    with owner.connect() as db:
        version = db.scalar(text("SELECT version_num FROM alembic_version"))
        assert version == "0014", f"Migration remained at {version}"
        for table, rows in before.items():
            assert rows == canonical(db.execute(text(f'SELECT to_jsonb(t) FROM "{table}" t')).scalars()), table
        assert db.execute(text("SELECT status,revision,access_epoch,archived_at FROM workspaces WHERE id='ws-upgrade'")).one() == ("active", 1, 0, None)
        assert db.scalar(text("SELECT workspace_epoch FROM runtime_sessions WHERE id='session-upgrade'")) == 0
    from intent_platform.auth import token_valid
    with Session(runtime) as db:
        set_identity(db, "dev-admin")
        assert token_valid(db, db.get(AuthToken, "synthetic-upgrade-token"))
    with runtime.begin() as db:
        assert db.scalar(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")) is False
        db.execute(text("SELECT set_config('intent.user_id','unrelated-upgrade-user',true)"))
        for table in ("conversations", "agent_releases", "calls"):
            assert db.scalar(text(f'SELECT count(*) FROM "{table}"')) == 0, table
    print(json.dumps({"status": "passed", "migration": "0013->0014", "preserved_tables": tables,
                      "historical_values_unchanged": True, "legacy_runtime_epoch_zero_works": True,
                      "restricted_role_isolation": True, "acceptance_evidence": False}))


if __name__ == "__main__":
    main()
