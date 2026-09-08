"""Real snapshot passes normalize once without skipping membership validation."""
from dataclasses import replace

import pytest

from bookflow.hub import permission_snapshot as s, permission_catalog as c
from bookflow.storage.engine import open_database
from tests.test_permission_snapshots import path, BUNDLE


def root_from(path):
    with open_database(path, writable=False) as db:
        return s.load_root(db, catalog=BUNDLE)


def rows_from(root):
    return (root.users, root.organizations, root.companies, root.memberships,
            root.assignments, root.authorities, root.role_defaults)


def exercise(root, method):
    rows = rows_from(root)
    if method == 'validate':
        return s._validate(rows, s._keys(rows), root.catalog)
    scopes = tuple(c.ScopeKey('organization', x.id) for x in root.organizations)
    scopes += tuple(c.ScopeKey('company', x.id) for x in root.companies)
    return s._observe(root, scopes, root.keys.users)


@pytest.mark.parametrize('method', ['validate', 'observe'])
@pytest.mark.parametrize('count', [0, 1, 4])
def test_each_real_pass_normalizes_once_or_zero_when_empty(path, monkeypatch, method, count):
    root = root_from(path)
    root = replace(root, memberships=root.memberships[:count])
    calls = []
    original = c._normal_catalog
    def counted(value):
        calls.append(value)
        return original(value)
    monkeypatch.setattr(c, '_normal_catalog', counted)
    exercise(root, method)
    assert len(calls) == (1 if count else 0)
    # The next observation must validate afresh, never reuse the prior pass.
    exercise(root, method)
    assert len(calls) == (2 if count else 0)


@pytest.mark.parametrize('method', ['validate', 'observe'])
@pytest.mark.parametrize('count', [0, 1])
def test_bad_catalog_preserves_lazy_error_category(path, method, count):
    root = root_from(path)
    root = replace(root, memberships=root.memberships[:count], catalog=object())
    if not count:
        exercise(root, method)
    else:
        with pytest.raises(s.SnapshotError) as caught:
            exercise(root, method)
        assert (caught.value.code, caught.value.field) == ('legacy_policy_invalid', 'memberships')


@pytest.mark.parametrize('revoked', [False, True])
def test_later_invalid_membership_is_checked_after_catalog_warming(path, revoked):
    root = root_from(path)
    bad = replace(root.memberships[-1], grants='["unknown-private-capability"]',
                  revoked_at='2026-01-01' if revoked else None)
    root = replace(root, memberships=(*root.memberships[:-1], bad))
    with pytest.raises(s.SnapshotError) as caught:
        exercise(root, 'validate')
    assert (caught.value.code, caught.value.field) == ('legacy_policy_invalid', 'memberships')
    assert 'unknown-private-capability' not in str(caught.value)
