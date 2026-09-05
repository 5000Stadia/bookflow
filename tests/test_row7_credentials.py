"""Foundation credential guards; authority fixtures do not expose authorization APIs."""

import json
from contextlib import contextmanager

import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.adapters.http import auth
from bookflow.core import audit, clock
from bookflow.core.config import Config, os_login
from bookflow.hub import credentials
from bookflow.hub import schema as h
from bookflow.storage.engine import open_database
from tests.conftest import as_user, make_actor


@contextmanager
def writer(root):
    with open_database(root / "hub.db", writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        try:
            yield db
            db.raw.execute("COMMIT")
        except BaseException:
            db.raw.execute("ROLLBACK")
            raise


@pytest.fixture
def authority(root):
    human = Config.load(root / "config.toml").user_table(os_login())["user_id"]
    agent = make_actor(root, "credential-bot", kind="agent", owner_user_id=human)
    with writer(root) as db:
        db.conn.execute(h.agent_authority.insert().values(
            agent_user_id=agent, epoch=3, suspended_at=None, suspension_reason=None))
        db.conn.execute(h.agent_principals.insert().values(
            agent_user_id=agent, principal_user_id=human, assigned_by=human,
            assigned_at=clock.now_iso(), revoked_at=None))
    return human, agent


def invalidate(db, state, human, agent, token_id):
    tokens = h.api_tokens.update().where(h.api_tokens.c.id == token_id)
    authority = h.agent_authority.update().where(h.agent_authority.c.agent_user_id == agent)
    assignment = h.agent_principals.update().where(h.agent_principals.c.agent_user_id == agent)
    if state == "null epoch":
        db.conn.execute(tokens.values(authority_epoch=None))
    elif state == "null principal":
        db.conn.execute(tokens.values(on_behalf_of=None))
    elif state == "missing principal":
        db.conn.execute(tokens.values(on_behalf_of="01ARZ3NDEKTSV4RRFFQ69G5FAV"))
    elif state == "stale epoch":
        db.conn.execute(authority.values(epoch=4))
    elif state == "suspended":
        db.conn.execute(authority.values(suspended_at=clock.now_iso(), suspension_reason="test"))
    elif state == "missing authority":
        db.conn.execute(h.agent_authority.delete().where(h.agent_authority.c.agent_user_id == agent))
    elif state == "unassigned":
        db.conn.execute(h.agent_principals.delete().where(h.agent_principals.c.agent_user_id == agent))
    elif state == "revoked assignment":
        db.conn.execute(assignment.values(revoked_at=clock.now_iso()))
    elif state == "inactive principal":
        db.conn.execute(h.users.update().where(h.users.c.id == human).values(active=False))
    elif state == "nonhuman principal":
        db.conn.execute(h.users.update().where(h.users.c.id == human).values(kind="agent", owner_user_id=human))
    elif state == "inactive actor":
        db.conn.execute(h.users.update().where(h.users.c.id == agent).values(active=False))
    elif state == "expired":
        db.conn.execute(tokens.values(expires_at="2000-01-01T00:00:00.000Z"))
    elif state == "revoked":
        db.conn.execute(tokens.values(revoked_at=clock.now_iso()))
    else:
        raise AssertionError(state)


@pytest.mark.parametrize("state", [
    "null epoch", "null principal", "missing principal", "stale epoch", "suspended", "missing authority",
    "unassigned", "revoked assignment", "inactive principal", "nonhuman principal",
    "inactive actor", "expired", "revoked",
])
def test_invalid_agent_credentials_fail_without_identity_details(root, client, authority, state):
    human, agent = authority
    issued = client.token.issue(user=agent, principal=human, label="guard witness")
    with writer(root) as db:
        invalidate(db, state, human, agent, issued["token_id"])
    with open_database(root / "hub.db", writable=False) as db:
        for resolver in (credentials.resolve_token, auth.resolve_token):
            with pytest.raises(BookflowError) as caught:
                resolver(db, issued["secret"])
            assert caught.value.code == "E_UNAUTHENTICATED"
            assert set(caught.value.details) == {"reason"}
            details = json.dumps(caught.value.details)
            assert all(hidden not in details for hidden in (human, agent, issued["token_id"], issued["secret"]))


def test_agent_binding_is_complete_at_insert_and_visible_in_output(root, client, authority):
    human, agent = authority
    with writer(root) as db:
        # A post-insert principal/epoch update cannot satisfy this witness.
        db.raw.execute("""CREATE TRIGGER require_complete_agent_binding BEFORE INSERT ON api_tokens
            WHEN (SELECT kind FROM users WHERE id = NEW.user_id) = 'agent'
            AND (NEW.on_behalf_of IS NULL OR NEW.authority_epoch IS NOT 3)
            BEGIN SELECT RAISE(ABORT, 'incomplete agent binding'); END""")
    issued = client.token.issue(user=agent, principal=human, label="bound")
    assert issued["on_behalf_of"] == human and issued["authority_epoch"] == 3
    with open_database(root / "hub.db", writable=False) as db:
        row = auth.resolve_token(db, issued["secret"])
        assert row["user_id"] == agent and row["on_behalf_of"] == human and row["authority_epoch"] == 3
        assert row["token_hash"] == credentials.token_hash(issued["secret"])
        assert issued["secret"] not in json.dumps(row)
        entry = db.raw.execute('SELECT "after" FROM audit_entries WHERE record_id = ?', (issued["token_id"],)).fetchone()
        assert entry is not None
        snapshot = audit.decode_snapshot(entry[0])
        assert snapshot["authority_epoch"] == 3 and snapshot["on_behalf_of"] == human
        assert "token_hash" not in snapshot and issued["secret"] not in json.dumps(snapshot)
    listed = client.token.list(user=agent)["items"]
    assert len(listed) == 1
    assert listed[0]["authority_epoch"] == 3 and listed[0]["on_behalf_of"] == human
    assert "secret" not in listed[0] and "token_hash" not in listed[0]


@pytest.mark.parametrize("state", [
    "suspended", "missing authority", "unassigned", "revoked assignment", "inactive principal", "inactive actor",
])
def test_ineligible_agent_cannot_get_fresh_token(root, client, authority, state):
    human, agent = authority
    # Keep the issuing administrator distinct from the principal being deactivated.
    issuer = make_actor(root, "credential-admin", hub_admin=True)
    admin_client = as_user(root, "credential-admin")
    with writer(root) as db:
        invalidate(db, state, human, agent, "no-token")
        before = db.conn.execute(sa.select(sa.func.count()).select_from(h.api_tokens)).scalar_one()
    with pytest.raises(BookflowError) as caught:
        admin_client.token.issue(user=agent, principal=human, label="fresh bypass")
    # Existing command lookup hides inactive users before credential eligibility.
    expected = "E_USER_NOT_FOUND" if state in ("inactive principal", "inactive actor") else "E_PERMISSION"
    assert caught.value.code == expected
    with writer(root) as db:
        # The insertion service independently enforces the guard, even without planning.
        with pytest.raises(BookflowError) as direct:
            credentials.issue_token(db, user_id=agent, on_behalf_of=human, kind="bearer",
                                    label="direct bypass", days=None, via="python", actor_id=issuer)
        assert direct.value.code == "E_PERMISSION"
        assert db.conn.execute(sa.select(sa.func.count()).select_from(h.api_tokens)).scalar_one() == before


@pytest.mark.parametrize("kind", ["bearer", "session"])
def test_human_credentials_remain_unbound(root, authority, kind):
    human, _ = authority
    with writer(root) as db:
        row, secret = auth.issue_token(db, user_id=human, kind=kind, label="human", days=None,
                                       via="http", actor_id=human)
    with open_database(root / "hub.db", writable=False) as db:
        assert auth.resolve_token(db, secret) == row
    assert row["on_behalf_of"] is None and row["authority_epoch"] is None
    assert (row["expires_at"] is not None) == (kind == "session")


@pytest.mark.parametrize("corruption", ["principal", "epoch", "inactive"])
def test_human_credentials_reject_agent_binding_or_inactive_actor(root, client, authority, corruption):
    human, _ = authority
    issued = client.token.issue(label="human")
    with writer(root) as db:
        if corruption == "inactive":
            db.conn.execute(h.users.update().where(h.users.c.id == human).values(active=False))
        else:
            values = {"on_behalf_of": human} if corruption == "principal" else {"authority_epoch": 3}
            db.conn.execute(h.api_tokens.update().where(h.api_tokens.c.id == issued["token_id"]).values(**values))
    with open_database(root / "hub.db", writable=False) as db:
        with pytest.raises(BookflowError) as caught:
            auth.resolve_token(db, issued["secret"])
        assert caught.value.code == "E_UNAUTHENTICATED"


def test_human_self_service_and_admin_target_rules_remain(root, client, authority):
    human, agent = authority
    other = make_actor(root, "credential-human")
    ordinary = as_user(root, "credential-human")
    own = ordinary.token.issue(label="self")
    assert own["user_id"] == other and own["authority_epoch"] is None and own["on_behalf_of"] is None
    for target in (agent, human, "missing-user"):
        with pytest.raises(BookflowError) as caught:
            ordinary.token.issue(user=target, principal=human, label="not admin")
        assert caught.value.code == "E_PERMISSION"
        assert caught.value.details == {"capability": "token", "required_role": "hub_admin"}
    issued = client.token.issue(user=other, label="admin issued")
    assert issued["user_id"] == other and issued["on_behalf_of"] is None and issued["authority_epoch"] is None


def test_http_refuses_suspended_agent_and_fresh_issuance(root, client, authority):
    from fastapi.testclient import TestClient

    from bookflow.commands.host_cmds import start_serving
    from bookflow.core.context import client_version

    human, agent = authority
    issued = client.token.issue(user=agent, principal=human, label="http agent")
    admin = client.token.issue(label="http admin")
    handle = start_serving(root, client_version(), bind="127.0.0.1:8765", secure_cookies=False)
    try:
        api = TestClient(handle.app)
        headers = {"Authorization": f"Bearer {issued['secret']}"}
        assert api.post("/commands/company.list", json={}, headers=headers).status_code == 200

        def suspend():
            db = handle.host._hub
            db.raw.execute("BEGIN IMMEDIATE")
            try:
                invalidate(db, "suspended", human, agent, issued["token_id"])
                db.raw.execute("COMMIT")
            except BaseException:
                db.raw.execute("ROLLBACK")
                raise
        handle.host.submit(suspend)
        denied = api.post("/commands/company.list", json={}, headers=headers)
        assert denied.status_code == 401 and denied.json()["code"] == "E_UNAUTHENTICATED"
        assert denied.json()["details"] == {"reason": "credential authority"}
        fresh = api.post("/commands/token.issue", json={"user": agent, "principal": human, "label": "bypass"},
                         headers={"Authorization": f"Bearer {admin['secret']}"})
        assert fresh.status_code == 403 and fresh.json()["code"] == "E_PERMISSION"
    finally:
        handle.stop()


def test_issuance_rechecks_current_authority_and_active_issuer(root, authority):
    human, agent = authority
    with writer(root) as db:
        assert credentials.issuance_epoch(db, user_id=agent, on_behalf_of=human) == 3
        invalidate(db, "suspended", human, agent, "no-token")
        with pytest.raises(BookflowError) as caught:
            credentials.issue_token(db, user_id=agent, on_behalf_of=human, kind="bearer",
                                    label="stale plan", days=None, via="python", actor_id=human)
        assert caught.value.code == "E_PERMISSION"
        db.conn.execute(h.agent_authority.update().where(h.agent_authority.c.agent_user_id == agent)
                        .values(epoch=4, suspended_at=None, suspension_reason=None))
        row, _ = credentials.issue_token(db, user_id=agent, on_behalf_of=human, kind="bearer",
                                         label="current binding", days=None, via="python", actor_id=human)
        assert row["authority_epoch"] == 4
        db.conn.execute(h.users.update().where(h.users.c.id == human).values(active=False))
        with pytest.raises(BookflowError) as caught:
            credentials.issue_token(db, user_id=human, kind="bearer", label="inactive issuer",
                                    days=None, via="python", actor_id=human)
        assert caught.value.code == "E_PERMISSION"


def test_expiry_boundary_is_unauthenticated(root, client, authority, monkeypatch):
    issued = client.token.issue(label="boundary", days=1)
    with open_database(root / "hub.db", writable=False) as db:
        row = credentials.resolve_token(db, issued["secret"])
        boundary = clock.parse_iso(row["expires_at"])
        monkeypatch.setattr(clock, "now", lambda: boundary)
        with pytest.raises(BookflowError) as caught:
            credentials.resolve_token(db, issued["secret"])
        assert caught.value.code == "E_UNAUTHENTICATED" and caught.value.details == {"reason": "expired"}


@pytest.mark.parametrize("malformed", ["authority", "token", "both"])
def test_real_epochs_fail_closed_even_when_equal(root, client, authority, malformed):
    human, agent = authority
    issued = client.token.issue(user=agent, principal=human, label="malformed epoch")
    with writer(root) as db:
        if malformed in ("authority", "both"):
            db.raw.execute("UPDATE agent_authority SET epoch = 1.5 WHERE agent_user_id = ?", (agent,))
            assert db.raw.execute("SELECT typeof(epoch) FROM agent_authority WHERE agent_user_id = ?",
                                  (agent,)).fetchone()[0] == "real"
        if malformed in ("token", "both"):
            db.raw.execute("UPDATE api_tokens SET authority_epoch = 1.5 WHERE id = ?", (issued["token_id"],))
            assert db.raw.execute("SELECT typeof(authority_epoch) FROM api_tokens WHERE id = ?",
                                  (issued["token_id"],)).fetchone()[0] == "real"
        with pytest.raises(BookflowError) as caught:
            auth.resolve_token(db, issued["secret"])
        assert caught.value.code == "E_UNAUTHENTICATED"
        assert caught.value.details == {"reason": "credential authority"}
        if malformed in ("authority", "both"):
            before = db.conn.execute(sa.select(sa.func.count()).select_from(h.api_tokens)).scalar_one()
            with pytest.raises(BookflowError) as caught:
                credentials.issue_token(db, user_id=agent, on_behalf_of=human, kind="bearer",
                                        label="malformed authority", days=None, via="python", actor_id=human)
            assert caught.value.code == "E_PERMISSION"
            assert db.conn.execute(sa.select(sa.func.count()).select_from(h.api_tokens)).scalar_one() == before


@pytest.mark.parametrize("kind", ["system", "arbitrary", ""])
def test_invalid_credential_kinds_cannot_authenticate_or_be_issued(root, client, authority, kind):
    human, _ = authority
    issued = client.token.issue(label="invalid kind")
    with writer(root) as db:
        db.raw.execute("UPDATE api_tokens SET kind = ? WHERE id = ?", (kind, issued["token_id"]))
        with pytest.raises(BookflowError) as caught:
            auth.resolve_token(db, issued["secret"])
        assert caught.value.code == "E_UNAUTHENTICATED"
        assert caught.value.details == {"reason": "credential kind"}
        before = db.conn.execute(sa.select(sa.func.count()).select_from(h.api_tokens)).scalar_one()
        with pytest.raises(BookflowError) as caught:
            credentials.issue_token(db, user_id=human, kind=kind, label="invalid kind issuance",
                                    days=None, via="python", actor_id=human)
        assert caught.value.code == "E_VALIDATION"
        assert caught.value.details == {"fields": [{"field": "kind", "problem": "must be bearer or session"}]}
        assert db.conn.execute(sa.select(sa.func.count()).select_from(h.api_tokens)).scalar_one() == before
