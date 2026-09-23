import os
import uuid
import pytest

# Never point the destructive/authoring integration fixture at the demonstration database.
os.environ.setdefault("INTENT_DATABASE_URL", "postgresql+psycopg://intent_app:local-app-only@127.0.0.1:54329/intent_test")
os.environ.setdefault("INTENT_MIGRATION_DATABASE_URL", "postgresql+psycopg://intent_owner:local-owner-only@127.0.0.1:54329/intent_test")
os.environ.setdefault("INTENT_ENV", "test")
os.environ.setdefault("INTENT_MODEL_MODE", "fixture")


@pytest.fixture(scope="session")
def client():
    from sqlalchemy.engine import make_url
    from fastapi.testclient import TestClient
    from intent_platform.config import settings
    runtime_url = make_url(settings().database_url)
    migration_url = make_url(settings().migration_database_url)
    if not runtime_url.database.endswith("_test") or not migration_url.database.endswith("_test") or runtime_url.database != migration_url.database or (runtime_url.host, runtime_url.port) != (migration_url.host, migration_url.port):
        pytest.fail("Integration tests require a separate database ending in _test")
    from intent_platform.migrate import main
    main()
    from intent_platform.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin(client):
    result = client.post("/api/v2/dev/login", json={"user_id": "dev-admin"})
    assert result.status_code == 200, result.text
    return {"Authorization": "Bearer " + result.json()["access_token"]}


@pytest.fixture
def seeded(client, admin):
    result = client.post("/api/v2/dev/seed", headers=admin)
    assert result.status_code == 200, result.text
    return result.json()


@pytest.fixture
def create_published(client, admin):
    def create(definition):
        from intent_platform.schemas import Definition
        body = Definition.model_validate(definition).model_dump()
        response = client.post("/api/v2/management/workspaces/ws-dev/functions/import", headers=admin, json={"definition": body})
        assert response.status_code == 201, response.text
        fid = response.json()["id"]
        return fid, body
    return create


def conversation(client, headers, aid):
    result = client.post(f"/api/v2/runtime/agents/{aid}/conversations", headers=headers)
    assert result.status_code == 201, result.text
    return result.json()["id"]


def message(client, headers, cid, text):
    r = client.post(f"/api/v2/runtime/conversations/{cid}/messages", headers=headers,
                    json={"text": text, "client_message_id": str(uuid.uuid4())})
    assert r.status_code == 202, r.text
    return r.json()["run_id"]


def drain():
    from intent_platform.worker import tick
    for _ in range(50):
        if not tick():
            break
