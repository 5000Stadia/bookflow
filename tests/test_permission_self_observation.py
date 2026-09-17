"""Self-observation saves duplicate work without weakening the general comparison."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from bookflow.hub import permission_snapshot as s, permission_policy as a, permission_runtime as r
from bookflow.storage.engine import open_database
from tests.test_permission_snapshots import path, BUNDLE, SuppliedVisibility, install_fixture_policy


class Visibility(SuppliedVisibility):
    def __init__(self, change=None):
        super().__init__()
        self.calls = 0
        self.change = change

    def facts(self, root, scopes, subjects):
        self.calls += 1
        result = super().facts(root, scopes, subjects)
        if self.change == 'first-malformed' and self.calls == 1:
            return None
        if self.calls == 2:
            if self.change == 'malformed':
                return None
            if self.change == 'incomplete':
                return replace(result, rows=result.rows[:-1])
            if self.change == 'revision':
                return replace(result, policy_revision='different-policy')
            if self.change == 'different':
                return replace(result, rows=tuple(replace(row, visible=False) for row in result.rows))
        return result


def observed(root, bundle, provider, *, slow=False, proposed=None, activated=True):
    # A distinct but equal bundle forces the original full two-state path, even in legacy mode.
    return s.observe_pair(root, root if proposed is None else proposed,
                          old_catalog=bundle, new_catalog=replace(bundle) if slow else bundle,
                          visibility=provider, activated=activated)


@pytest.mark.parametrize('mode', ['legacy', 'activated', 'scoped'])
def test_complete_result_and_duplicate_work_counts(path, monkeypatch, mode):
    bundle = r.current_catalog().catalog_bundle() if mode == 'scoped' else BUNDLE
    with open_database(path, writable=True) as db:
        if mode == 'legacy':
            db.raw.execute("UPDATE permission_state SET mode='legacy',catalog_version=NULL,catalog_sha256=NULL,catalog_json=NULL")
        elif mode == 'scoped':
            install_fixture_policy(db.raw, bundle)
    with open_database(path, writable=False) as db:
        root = s.load_root(db, catalog=bundle)
    counts = dict(validation=0, observation=0, phase=0, comparison=0)
    def count(key, fn):
        def wrapped(*args, **kwargs):
            counts[key] += 1
            return fn(*args, **kwargs)
        return wrapped
    monkeypatch.setattr(s, '_validated_root', count('validation', s._validated_root))
    monkeypatch.setattr(s, '_observe', count('observation', s._observe))
    # Count construction without replacing policy's dataclass used for type validation.
    proxy = SimpleNamespace(**vars(a))
    proxy.Phase = count('phase', a.Phase)
    proxy.validate_comparison = count('comparison', a.validate_comparison)
    monkeypatch.setattr(s, 'a', proxy)
    full_visibility = Visibility()
    full = observed(root, bundle, full_visibility, slow=True, activated=mode != 'legacy')
    assert counts == dict(validation=2, observation=2, phase=2, comparison=1)
    counts.update({key: 0 for key in counts})
    visibility = Visibility()
    fast = observed(root, bundle, visibility, activated=mode != 'legacy')
    assert fast == full
    assert visibility.calls == full_visibility.calls == 2
    assert counts == dict(validation=1, observation=1, phase=1, comparison=1)
    if mode != 'legacy':
        counts.update({key: 0 for key in counts})
        assert observed(root, bundle, Visibility(), proposed=replace(root)) == full
        assert counts == dict(validation=2, observation=2, phase=2, comparison=1)
    # Complete decision outputs, including agent/human conjunction, remain identical.
    for phase in ('old', 'new'):
        for who in full.snapshot.comparison.subjects:
            for scope in full.snapshot.comparison.scopes:
                if scope.kind not in ('company', 'future_company'):
                    continue
                for atom in a.admissions(full.snapshot.comparison, phase=phase, subject=who, scope=scope):
                    principals = (None, 'H') if who in full.snapshot.comparison.agents else (None,)
                    for principal in principals:
                        args = dict(phase=phase, actor=who, bound_human=principal, scope=scope, requirement=atom.requirement)
                        assert a.execution(fast.snapshot.comparison, **args) == a.execution(full.snapshot.comparison, **args)


@pytest.mark.parametrize('change,expected', [
    ('first-malformed', ('visibility_unresolved', 'visibility')),
    ('malformed', ('visibility_unresolved', 'visibility')),
    ('incomplete', ('visibility_unresolved', 'visibility')),
    ('revision', ('visibility_unresolved', 'visibility')),
    ('different', None),
])
def test_stateful_visibility_result_and_refusal_details_match_full_path(path, change, expected):
    with open_database(path, writable=False) as db:
        root = s.load_root(db, catalog=BUNDLE)
    outcomes = []
    for slow in (False, True):
        visibility = Visibility(change)
        try:
            result = observed(root, BUNDLE, visibility, slow=slow)
        except s.SnapshotError as error:
            result = error.args
        outcomes.append(result)
        assert visibility.calls == (1 if change == 'first-malformed' else 2)
    assert outcomes[0] == outcomes[1]
    if expected:
        assert outcomes[0] == expected
    else:
        assert outcomes[0].snapshot.comparison.old.visibility != outcomes[0].snapshot.comparison.new.visibility


@pytest.mark.parametrize('change', ['stamp', 'keys', 'rows', 'bundle'])
def test_identical_external_invalid_root_still_fully_validated_before_visibility(path, change):
    with open_database(path, writable=False) as db:
        root = s.load_root(db, catalog=BUNDLE)
    bundle = BUNDLE
    if change == 'stamp':
        root = replace(root, stamp=replace(root.stamp, authority_rows_digest='0'*64))
    elif change == 'keys':
        root = replace(root, keys=replace(root.keys, users=root.keys.users[:-1]))
    elif change == 'rows':
        root = replace(root, users=root.users[:-1])
    else:
        bundle = replace(bundle, source_inventory_digest='0'*64)
    errors = []
    for slow in (False, True):
        visibility = Visibility('first-malformed')
        with pytest.raises(s.SnapshotError) as error:
            observed(root, bundle, visibility, slow=slow)
        errors.append(error.value.args)
        assert visibility.calls == 0
    assert errors[0] == errors[1]
