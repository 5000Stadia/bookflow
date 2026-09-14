"""Private phase1 witnesses: real legacy facts, exact transitions, no public cutover."""
from dataclasses import replace
from contextlib import contextmanager
import sqlite3
import pytest
from bookflow.hub import identity_admin as b, permission_activation_catalog as build
from bookflow.hub import permission_runtime as runtime, permission_snapshot as s, permission_policy as a
from bookflow.hub import permission_catalog as c
from bookflow.storage.engine import open_database
from tests.permission_admin_support import make_root, binding, CONTEXT, apply, tokens, expected_tokens, G_REVOKED
from tests.payment_raw_evidence import database as snapshot


@contextmanager
def writer(path):
    with open_database(path,writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE')
        try:
            yield db
            db.raw.execute('COMMIT')
        except BaseException:
            db.raw.execute('ROLLBACK')
            raise


@pytest.fixture
def legacy(tmp_path):
    path=make_root(tmp_path/'hub.db')
    with sqlite3.connect(path) as db:
        db.execute("UPDATE permission_state SET mode='legacy',catalog_version=NULL,catalog_sha256=NULL,catalog_json=NULL")
        db.execute('DELETE FROM role_capabilities')
        db.executemany('INSERT INTO role_capabilities VALUES (?,?,?)',[(x.role,x.requirement.capability,x.requirement.threshold) for x in c.FROZEN_DEFAULTS])
    return path


def intent(generation=1, administrators=None):
    return b.ActivatePolicy(generation,build.MANIFEST.descriptor_sha256,
        (b.CompanyAdministrator('E','U',b.Absent()),) if administrators is None else administrators)


def activate(db, value=None, person='H'):
    return apply(db,value or intent(),person=person,catalog=build.catalog_bundle(),visibility=runtime.VISIBILITY)


def admitted(pair, phase, user, capability, floor='standard',company='C'):
    return next(x.admitted for x in a.admissions(pair.comparison,phase=phase,subject=user,
        scope=c.ScopeKey('company',company)) if x.requirement==c.Requirement(capability,floor))


def test_activation_literal_legacy_and_current_authority_then_second_observation(legacy):
    with sqlite3.connect(legacy) as db:
        db.execute("UPDATE memberships SET denies='[\"ledger.post\"]' WHERE user_id='P'")
    before=snapshot(legacy)
    with writer(legacy) as db:
        old_defaults = set(db.raw.execute('SELECT * FROM role_capabilities'))
        assert old_defaults == {(x.role,x.requirement.capability,x.requirement.threshold) for x in c.FROZEN_DEFAULTS}
        prior_tokens=tokens(db.raw)
        preview=b.preview_edit(db,binding=binding(legacy),intent=intent(),catalog=build.catalog_bundle(),visibility=runtime.VISIBILITY,request_id='REQUEST')
        pair=preview.pair
        assert admitted(pair,'old','H','ledger.post') is True  # old installation shortcut
        assert admitted(pair,'new','H','ledger.post') is False
        assert admitted(pair,'old','P','ledger.post') is True  # old override ignored
        assert admitted(pair,'new','P','ledger.post') is False
        assert admitted(pair,'new','Q','ledger.post') is True
        for family in ('check','card_charge','invoice','journal_entry','payment','sales_receipt'):
            assert not admitted(pair,'old','W','transaction.'+family+'.delete')
            assert not admitted(pair,'new','W','transaction.'+family+'.delete')
        result=activate(db)
        assert set(db.raw.execute('SELECT * FROM role_capabilities')) - old_defaults == {
            ('admin','membership','authenticated'), ('hub_admin','membership','authenticated'),
            ('owner','membership','authenticated'), ('readonly','membership','authenticated'),
            ('standard','membership','authenticated'), ('hub_admin','user','hub_admin')}
        assert old_defaults <= set(db.raw.execute('SELECT * FROM role_capabilities'))
        assert tokens(db.raw)==expected_tokens(prior_tokens,G_REVOKED)
        assert [(x.user_id,x.scope_id,x.role) for x in result.private.final.root.memberships if x.scope_type=='company']==[('U','E','admin')]
    after=snapshot(legacy)
    assert before != after
    with writer(legacy) as db:
        observed=runtime.observe_current(db)
        assert observed.snapshot.comparison.old.semantics=='scoped_v1'
        assert not admitted(observed.snapshot,'old','H','ledger.post')
        assert not admitted(observed.snapshot,'old','P','ledger.post')
        # Ordinary B2 edit after activation must NOT regain the installation bypass.
        with pytest.raises(b.AdministrationError):
            apply(db,b.PutMembership('Q',c.ScopeKey('company','C'),b.Absent(),'standard',('transaction.check.delete',),()),
                person='H',catalog=build.catalog_bundle(),visibility=runtime.VISIBILITY)
        apply(db,b.PutMembership('Q',c.ScopeKey('company','C'),b.Absent(),'standard',('transaction.check.delete',),()),
            person='A',catalog=build.catalog_bundle(),visibility=runtime.VISIBILITY)
        current=runtime.observe_current(db)
        assert admitted(current.snapshot,'old','Q','transaction.check.delete')
        assert not admitted(current.snapshot,'old','Q','transaction.card_charge.delete')
        action=a.company_action(current.snapshot.comparison,phase='old',subject='Q',scope=c.ScopeKey('company','C'),action='contract:delete:check')
        assert action.static_admitted and not action.available


def test_activation_no_reduction_retains_unaffected_agents_and_memberships(legacy):
    with writer(legacy) as db:
        original=db.raw.execute('SELECT * FROM memberships ORDER BY id').fetchall()
        prior=tokens(db.raw)
        result=activate(db)
        assert tokens(db.raw)==prior
        assert result.private.reconciliation.revoke_agents==()
        for row in original:
            assert db.raw.execute('SELECT * FROM memberships WHERE id=?',(row[0],)).fetchone()==row
        repeated=activate(db,intent(generation=2,administrators=()))
        assert not repeated.visible.changed and repeated.private.mutations==()


def test_activation_missing_admin_stale_catalog_and_late_failure_are_atomic(legacy,monkeypatch):
    before=snapshot(legacy)
    with writer(legacy) as db:
        for value in [intent(administrators=()),intent(generation=2),replace(intent(),expected_catalog_sha256='0'*64)]:
            with pytest.raises(b.AdministrationError):activate(db,value)
    assert snapshot(legacy)==before
    real=b._verify_final
    def fail_after_all(*args):
        real(*args)
        raise RuntimeError('owned failure after activation writes and audit')
    with monkeypatch.context() as patch:
        patch.setattr(b,'_verify_final',fail_after_all)
        with writer(legacy) as db:
            with pytest.raises(RuntimeError,match='owned failure'):activate(db)
    assert snapshot(legacy)==before


def test_legacy_read_and_public_delete_gate_remain_unchanged(legacy):
    from bookflow.hub import access
    before=snapshot(legacy)
    with open_database(legacy,writable=False) as db:
        current=runtime.observe_current(db)
        assert current.snapshot.comparison.old.semantics=='prepared_v1'
        assert current.snapshot.old.catalog.version==c.FROZEN_CATALOG.version
        with pytest.raises(Exception) as error:access.require_explicit_grant(None,'transaction.invoice.delete')
        assert error.value.code=='E_PERMISSION'
    assert snapshot(legacy)==before


def test_shipped_hub0013_defaults_visible_readonly_admin_and_latent_delete(legacy):
    # Literal pre-cutover migration additions, independent of the new descriptor.
    from tests.permission_admin_support import insert, OLD
    with sqlite3.connect(legacy) as db:
        db.executemany('INSERT INTO role_capabilities VALUES (?,?,?)', [
            ('admin','membership','authenticated'), ('hub_admin','membership','authenticated'),
            ('owner','membership','authenticated'), ('readonly','membership','authenticated'),
            ('standard','membership','authenticated'), ('hub_admin','user','hub_admin')])
        insert(db,'memberships',dict(id='M-H',user_id='H',scope_type='company',scope_id='C',
            role='readonly',grants=None,denies=None,granted_by='H',granted_at=OLD,revoked_at=None,version=1))
        db.execute("UPDATE memberships SET grants='[\"transaction.check.delete\"]' WHERE user_id='RO'")
    with writer(legacy) as db:
        defaults = tuple(db.raw.execute('SELECT * FROM role_capabilities ORDER BY 1,2,3'))
        result = activate(db)
        assert admitted(result.private.pair,'old','H','ledger.post')
        assert admitted(result.private.pair,'new','H','ledger.read','member')
        assert not admitted(result.private.pair,'new','H','ledger.post')
        assert not admitted(result.private.pair,'new','RO','transaction.check.delete')
        assert tuple(db.raw.execute('SELECT * FROM role_capabilities ORDER BY 1,2,3')) == defaults
    with writer(legacy) as db:
        current = runtime.observe_current(db)
        assert admitted(current.snapshot,'old','H','ledger.read','member')
        assert not admitted(current.snapshot,'old','H','ledger.post')
        before = tuple(db.raw.execute('SELECT * FROM memberships ORDER BY id'))
        with pytest.raises(b.AdministrationError) as caught:
            apply(db,b.PutMembership('Q',c.ScopeKey('company','C'),b.Absent(),'standard',(),()),
                person='H',catalog=runtime.catalog_for_root(db),visibility=runtime.VISIBILITY)
        assert caught.value.args == ('not_administrator','scope')
        assert tuple(db.raw.execute('SELECT * FROM memberships ORDER BY id')) == before
        apply(db,b.PutMembership('RO',c.ScopeKey('organization','O'),b.Version(1),'standard',
                  ('transaction.check.delete',),('ledger.post',)),
            person='A',catalog=runtime.catalog_for_root(db),visibility=runtime.VISIBILITY)
        after = runtime.observe_current(db)
        assert admitted(after.snapshot,'old','RO','transaction.check.delete')
        assert not admitted(after.snapshot,'old','RO','ledger.post')
        assert not admitted(after.snapshot,'old','RO','transaction.card_charge.delete')
        apply(db,b.PutMembership('RO',c.ScopeKey('company','C'),b.Absent(),'standard',(),
                  ('transaction.check.delete',)),person='A',catalog=runtime.catalog_for_root(db),visibility=runtime.VISIBILITY)
        denied = runtime.observe_current(db)
        assert not admitted(denied.snapshot,'old','RO','transaction.check.delete')
        assert admitted(denied.snapshot,'old','RO','transaction.check.delete',company='D')
