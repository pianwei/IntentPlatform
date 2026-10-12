"""Real PostgreSQL authority-lock and successor-ticket boundary checks."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from intent_platform.db import engine, set_identity
from intent_platform.models import RuntimeSession, AuthToken, Ticket
from intent_platform.identity_models import IntegrationKey
from intent_platform import local_identity
from test_local_identity import local, key, embed, ticket, bearer
from test_zhiyu_runtime import zhiyu


def switch(client, app, identity, persona="manager_b"):
    return client.post(f"/api/v2/runtime/apps/{app['public_id']}/demo-persona",
                       headers=bearer(identity["access_token"]), json={"demo_persona": persona})


def session_count(local, app):
    with Session(engine) as db:
        set_identity(db, local["user"])
        return db.scalar(select(func.count(RuntimeSession.id)).where(
            RuntimeSession.user_id == local["user"], RuntimeSession.agent_id == app["id"]))


@pytest.mark.parametrize("change", ["rotate", "revoke", "disable_account"])
def test_key_change_after_authentication_cannot_mint_successor(client, local, zhiyu, monkeypatch, change):
    issued = key(client, local)
    runtime = embed(client, zhiyu, issued["secret"])
    before = session_count(local, zhiyu)
    reached_lock = Event()
    original = local_identity.locked_key

    def observed(db, identifier):
        reached_lock.set()
        return original(db, identifier)

    # The old committed authority remains visible to authenticate while this
    # independent transaction holds its replacement uncommitted. The switch
    # must wait, then reread the newly committed authority before any issuance.
    with Session(engine) as authority:
        set_identity(authority, local["user"])
        account = local_identity.locked_account(authority, local["user"])
        credential = original(authority, issued["key"]["id"])
        if change == "rotate":
            credential.version += 1
            credential.secret_hash = local_identity.secret_hash("rotated-test-only-secret")
        elif change == "revoke":
            credential.revoked = True
        else:
            account.active = False
            local_identity.invalidate_account(authority, account, revoke_keys=True)
        authority.flush()
        monkeypatch.setattr(local_identity, "locked_key", observed)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(switch, client, zhiyu, runtime)
            try:
                assert reached_lock.wait(8), "Switch never reached the key authority lock"
            finally:
                authority.commit()
            assert pending.result(timeout=8).status_code == 401
    assert session_count(local, zhiyu) == before
    with Session(engine) as db:
        set_identity(db, local["user"])
        assert db.get(RuntimeSession, runtime["runtime_session_id"]).demo_successor_id is None
        assert db.scalar(select(func.count(AuthToken.id)).where(
            AuthToken.user_id == local["user"], AuthToken.auth_method == "api-key",
            AuthToken.scope == "runtime")) == 1


@pytest.mark.parametrize("change", ["password_reset", "parent_logout"])
def test_management_preview_rechecks_parent_after_account_lock(client, local, zhiyu, monkeypatch, change):
    response = client.post(f"/api/v2/runtime/apps/{zhiyu['public_id']}/sessions", headers=local["headers"], json={})
    assert response.status_code == 200
    runtime = response.json()
    before = session_count(local, zhiyu)
    reached_lock = Event()
    original = local_identity.locked_account

    def observed(db, user_id):
        reached_lock.set()
        return original(db, user_id)

    with Session(engine) as authority:
        set_identity(authority, local["user"])
        account = original(authority, local["user"])
        if change == "password_reset":
            local_identity.invalidate_account(authority, account, revoke_keys=True)
        else:
            authority.get(AuthToken, local["token"]).revoked = True
        authority.flush()
        monkeypatch.setattr(local_identity, "locked_account", observed)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(switch, client, zhiyu, runtime)
            try:
                assert reached_lock.wait(8), "Switch never reached the account authority lock"
            finally:
                authority.commit()
            assert pending.result(timeout=8).status_code == 401
    assert session_count(local, zhiyu) == before


def test_old_ticket_cannot_restore_predecessor_after_persona_switch(client, local, zhiyu):
    issued = key(client, local)
    original = embed(client, zhiyu, issued["secret"])
    pending_ticket, payload = ticket(client, zhiyu, issued["secret"], runtime=original["runtime_session_id"])
    assert pending_ticket.status_code == 200
    changed = switch(client, zhiyu, original)
    assert changed.status_code == 200, changed.text
    current = changed.json()
    response = client.post("/api/v2/runtime/embed/exchange", json={**payload, "ticket": pending_ticket.json()["ticket"]})
    assert response.status_code == 403
    with Session(engine) as db:
        set_identity(db, local["user"])
        assert db.get(Ticket, pending_ticket.json()["ticket"]).consumed is False
        assert db.get(RuntimeSession, original["runtime_session_id"]).current_token_id == original["access_token"]
        assert db.get(RuntimeSession, current["runtime_session_id"]).current_token_id == current["access_token"]
    restored = embed(client, zhiyu, issued["secret"], runtime=original["runtime_session_id"])
    assert (restored["runtime_session_id"], restored["demo_persona"]) == (current["runtime_session_id"], "manager_b")


def test_repeated_switches_flatten_only_trusted_lineage_and_prevent_forks(client, local, zhiyu):
    issued = key(client, local)
    original = embed(client, zhiyu, issued["secret"])
    other = embed(client, zhiyu, issued["secret"], visitor="host-visitor-independent")
    identities = [original]
    for index in range(35):
        response = switch(client, zhiyu, identities[-1], ("manager_b", "supervisor", "manager_a")[index % 3])
        assert response.status_code == 200, response.text
        identities.append(response.json())
    current = identities[-1]
    assert switch(client, zhiyu, original).status_code == 409
    with Session(engine) as db:
        set_identity(db, local["user"])
        for predecessor in identities[:-1]:
            assert db.get(RuntimeSession, predecessor["runtime_session_id"]).demo_successor_id == current["runtime_session_id"]
        assert db.get(RuntimeSession, current["runtime_session_id"]).demo_successor_id is None
        assert db.get(RuntimeSession, other["runtime_session_id"]).demo_successor_id is None
    assert session_count(local, zhiyu) == len(identities) + 1
    restored = embed(client, zhiyu, issued["secret"], runtime=original["runtime_session_id"])
    assert (restored["runtime_session_id"], restored["demo_persona"]) == (current["runtime_session_id"], current["demo_persona"])
    bad, _ = ticket(client, zhiyu, issued["secret"], visitor="host-visitor-independent", runtime=original["runtime_session_id"])
    assert bad.status_code == 403
    assert embed(client, zhiyu, issued["secret"], visitor="host-visitor-independent", runtime=other["runtime_session_id"])["runtime_session_id"] == other["runtime_session_id"]
