"""Fresh authority observations can reuse only the unchanged catalog descriptor."""
from dataclasses import replace
import pytest
from bookflow.hub import permission_catalog as c, permission_snapshot as s, permission_runtime as r
from bookflow.storage.engine import open_database
from tests.test_permission_snapshots import path, install_fixture_policy, SuppliedVisibility


def test_unchanged_current_catalog_avoids_recursive_keys_but_rereads_authority(path, monkeypatch):
    bundle = r.current_catalog().catalog_bundle()
    with open_database(path, writable=True) as db:
        db.raw.execute('DELETE FROM role_capabilities')
        db.raw.executemany('INSERT INTO role_capabilities(role,capability,required_role) VALUES(?,?,?)',
            [(x.role, x.requirement.capability, x.requirement.threshold) for x in bundle.descriptor.defaults])
        install_fixture_policy(db.raw, bundle)
    def observe():
        with open_database(path, writable=False) as db:
            root = s.load_root(db, catalog=bundle)
            s.observe_pair(root, root, old_catalog=bundle, new_catalog=bundle,
                           visibility=SuppliedVisibility())
            return root
    before = observe()  # Warm only immutable descriptor/decode memo residency.
    with monkeypatch.context() as patch:
        def unnecessary_key(value):
            raise AssertionError('unchanged descriptor recursively rebuilt')
        patch.setattr(c, '_descriptor_key', unnecessary_key)
        assert observe() == before
        with open_database(path, writable=True) as db:
            db.raw.execute("UPDATE users SET active=0 WHERE id='Q'")
        changed = observe()
        assert not next(x for x in changed.users if x.id == 'Q').active
        assert changed.stamp.authority_rows_digest != before.stamp.authority_rows_digest
    with open_database(path, writable=True) as db:
        db.raw.execute("DELETE FROM role_capabilities WHERE role='readonly' AND capability='ledger.read'")
    # A fresh defaults reduction cannot be hidden by immutable descriptor reuse.
    with pytest.raises(s.SnapshotError, match='catalog_mismatch'):
        observe()
    with open_database(path, writable=True) as db:
        install_fixture_policy(db.raw, bundle)
    reduced = observe()
    assert len(reduced.role_defaults) < len(before.role_defaults)
    with open_database(path, writable=True) as db:
        db.raw.executemany('INSERT OR IGNORE INTO role_capabilities(role,capability,required_role) VALUES(?,?,?)',
            [(x.role, x.requirement.capability, x.requirement.threshold) for x in before.role_defaults])
        install_fixture_policy(db.raw, bundle)
    assert observe().role_defaults == before.role_defaults


def test_defaults_reuse_preserves_full_validation():
    base = c._normal_catalog(r.current_catalog().catalog_bundle().descriptor)
    assert c._with_defaults(base, tuple(reversed(base.defaults))) is base
    assert c._with_defaults(base, ()) == c._normal_catalog(replace(base, defaults=()))
    class EqualString(str):
        pass
    wrong = (
        list(base.defaults),
        (*base.defaults, base.defaults[0]),
        (replace(base.defaults[0], role=EqualString(base.defaults[0].role)), *base.defaults[1:]),
        (replace(base.defaults[0], role=True), *base.defaults[1:]),
        (c.DefaultEntry('standard', c.Requirement('transaction.payment.delete', 'standard')),),
        (c.DefaultEntry('standard', c.Requirement('unknown.capability', 'standard')),),
    )
    for defaults in wrong:
        with pytest.raises(c.PolicyInputError):
            c._with_defaults(base, defaults)
        with pytest.raises(c.PolicyInputError):
            c._normal_catalog(replace(base, defaults=defaults))
