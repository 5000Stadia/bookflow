"""Actual company-admin setup after activation, including absent-row previews."""
import sqlite3

import pytest

from tests.test_identity_commands import office, WB
from tests.test_permission_setup_http import activate


def policy_snapshot(root):
    # HTTP authentication can update session activity; policy and audit must not change.
    with sqlite3.connect(root / 'hub.db') as db:
        return {name: db.execute(f'SELECT * FROM {name} ORDER BY rowid').fetchall()
                for name in ('memberships', 'permission_state', 'role_capabilities',
                             'agent_authority', 'agent_principals', 'audit_events', 'audit_entries')}


@pytest.mark.timeout(180)
def test_company_admin_previews_creates_and_updates_absent_activated_member(office):
    admin = office.admin('user.add', dict(username='scope-admin', password='scope admin fixture',
                                        company=office.first, role='admin'))
    person = office.admin('user.add', dict(username='new-member', password='new member fixture'))
    activate(office)
    actor = office.login_as('scope-admin', 'scope admin fixture')
    with sqlite3.connect(office.root / 'hub.db') as db:
        assert db.execute('SELECT hub_admin FROM users WHERE id=?', (admin['user_id'],)).fetchone() == (0,)
        assert db.execute('SELECT COUNT(*) FROM memberships WHERE user_id=?', (person['user_id'],)).fetchone() == (0,)
    raw = dict(user=person['user_id'], company=office.first, role='standard', expected_version=0,
               grants=['transaction.payment.delete'], denies=['ledger.post'])
    before = policy_snapshot(office.root)
    preview = actor.post('/commands/membership.grant?dry_run=true', json=raw, headers=WB)
    assert preview.status_code == 200, preview.text
    preview = preview.json()
    assert preview['dry_run'] and preview['changed']
    assert preview['membership_id'] == '' and preview['version'] == 1
    for field in ('user_id', 'scope_type', 'scope_id', 'role', 'grants', 'denies'):
        assert preview[field] == dict(user_id=person['user_id'], scope_type='company',
                                     scope_id=office.first, role='standard',
                                     grants=raw['grants'], denies=raw['denies'])[field]
    assert policy_snapshot(office.root) == before

    saved = office.ok(actor, 'membership.grant', raw)
    identifier = saved['membership_id']
    assert len(identifier) == 26 and saved['version'] == 1 and saved['changed']
    rows = office.ok(actor, 'membership.list', dict(company=office.first))['items']
    member = next(row for row in rows if row['user_id'] == person['user_id'])
    assert member['membership_id'] == identifier and member['version'] == 1
    assert member['grants'] == raw['grants'] and member['denies'] == raw['denies']
    effective = office.ok(actor, 'membership.effective', dict(company=office.first, user=person['user_id']))
    bits = {p['requirement']['capability']: p['admitted'] for p in effective['permissions']
            if p['requirement']['threshold'] == 'standard'}
    assert bits['transaction.payment.delete'] and not bits['ledger.post']
    after = policy_snapshot(office.root)
    refused = office.call(actor, 'membership.grant', raw)
    assert refused.status_code == 409 and refused.json()['code'] == 'E_VERSION_CONFLICT'
    assert policy_snapshot(office.root) == after

    # Omitted grant/deny grids preserve them during a versioned role edit.
    update = dict(user=person['user_id'], company=office.first, role='readonly', expected_version=1)
    preview = actor.post('/commands/membership.grant?dry_run=true', json=update, headers=WB)
    assert preview.status_code == 200, preview.text
    assert preview.json()['membership_id'] == identifier and preview.json()['version'] == 2
    assert policy_snapshot(office.root) == after
    changed = office.ok(actor, 'membership.grant', update)
    assert changed['membership_id'] == identifier and changed['version'] == 2 and changed['role'] == 'readonly'
    assert changed['grants'] == raw['grants'] and changed['denies'] == raw['denies']
    final = policy_snapshot(office.root)
    unchanged = office.ok(actor, 'membership.grant', dict(update, expected_version=2))
    assert not unchanged['changed'] and unchanged['version'] == 2 and unchanged['membership_id'] == identifier
    assert policy_snapshot(office.root) == final
    with sqlite3.connect(office.root / 'hub.db') as db:
        assert db.execute('SELECT id,scope_id,version FROM memberships WHERE user_id=?',
                          (person['user_id'],)).fetchall() == [(identifier, office.first, 2)]
        db.row_factory = sqlite3.Row
        events = db.execute("SELECT * FROM audit_events WHERE command='membership grant'").fetchall()
        owned = [row for row in events if row['actor_id'] == admin['user_id']]
        assert len(owned) == 2
