"""Real authority facts remain fresh across mutation and transaction lifetimes."""
import pytest
from bookflow.hub import permission_runtime as runtime, permission_catalog as catalog
from bookflow.hub.identity_admin import AdministrationError
from bookflow.storage.engine import open_database
from tests.test_permission_snapshots import path


def require(db):
    return runtime.require_company(db, actor='Q', principal=None, company='C',
                                   requirement=catalog.Requirement('ledger.read', 'member'))


def test_repeated_requirement_reuses_facts_but_explicit_observation_is_fresh(path, monkeypatch):
    original = runtime.observe_current
    calls = []
    def counted(tx):
        calls.append(tx)
        return original(tx)
    monkeypatch.setattr(runtime, 'observe_current', counted)
    with open_database(path, writable=False) as db:
        first = require(db)
        assert require(db) == first
        assert len(calls) == 1
        assert runtime.observe_current(db) is not None
        assert len(calls) == 2
        db.raw.execute('ROLLBACK')
        db.raw.execute('BEGIN')
        assert require(db) == first
        assert len(calls) == 3


def test_local_mutation_rollback_and_corruption_cannot_reuse_allow(path):
    with open_database(path, writable=True) as db:
        db.raw.execute('BEGIN')
        before = require(db)
        db.raw.execute('SAVEPOINT before_disable')
        db.raw.execute("UPDATE users SET active=0 WHERE id='Q'")
        with pytest.raises(AdministrationError):
            require(db)
        # The denial populated an observation of the modified state. Rollback
        # must invalidate that too; total_changes alone would retain the denial.
        db.raw.execute('ROLLBACK TO before_disable')
        assert require(db) == before
        db.raw.execute("UPDATE permission_state SET catalog_sha256=?", ("0" * 64,))
        with pytest.raises(AdministrationError):
            require(db)
        db.raw.execute('ROLLBACK TO before_disable')
        assert require(db) == before


def test_actor_company_and_principal_decisions_not_cached(path):
    with open_database(path, writable=False) as db:
        require(db)
        for actor, principal, company in [('I', None, 'C'), ('Q', None, 'E'), ('G', 'H', 'C')]:
            with pytest.raises(AdministrationError):
                runtime.require_company(db, actor=actor, principal=principal, company=company,
                    requirement=catalog.Requirement('ledger.read', 'member'))


def test_bound_operation_checks_expiry_even_with_unchanged_cached_facts(path, monkeypatch):
    from datetime import datetime, timezone
    from bookflow.core import clock, identity_admin_binding as producer
    from bookflow.core.errors import BookflowError
    from bookflow.hub import credentials, identity_admin as admin
    secret = 'owned-reuse-expiry-fixture'
    with open_database(path, writable=True) as db:
        db.raw.execute('UPDATE api_tokens SET token_hash=?,expires_at=? WHERE id=?',
                       (credentials.token_hash(secret), '2026-09-17T12:00:00Z', 'V'))
    moment = datetime(2026, 9, 17, 11, tzinfo=timezone.utc)
    monkeypatch.setattr(clock, 'now', lambda: moment)
    with open_database(path, writable=False) as db:
        binding = admin.TokenBinding(secret, 'V', 'H', 'bearer', None, db.path, 'expiry-reuse')
        with admin.OSOperation(db, request_id='expiry-reuse', purpose='preview') as guard:
            operation = producer.BoundOperation(db, binding, guard, '')
            requirement = catalog.Requirement('ledger.read', 'member')
            assert operation.require_company('C', requirement).intersection_admitted
            cached = db._permission_observation
            assert operation.require_company('C', requirement).intersection_admitted
            assert db._permission_observation is cached
            moment = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
            with pytest.raises(BookflowError) as error:
                operation.require_company('C', requirement)
            assert error.value.code == 'E_UNAUTHENTICATED'
            assert error.value.details['reason'] == 'expired'
            assert db._permission_observation is cached
