"""Claude F1/O4: initiating versions and a real default insertion."""
from dataclasses import asdict, replace
import json

from bookflow.core.audit import decode_snapshot
from bookflow.hub import identity_admin as b, permission_catalog as c
from bookflow.storage.engine import open_database
from tests.permission_admin_support import (
    AT, BUNDLE, VISIBILITY, apply, binding, make_root, receipt, rows, s, snapshot,
)


def preview(db, intent):
    before = snapshot(db.raw)
    result = b.preview_edit(db, binding=binding(db.path), intent=intent,
                            catalog=BUNDLE, visibility=VISIBILITY, request_id='REQUEST')
    assert snapshot(db.raw) == before
    return result.visible


def test_agent_activity_returns_user_version_for_followup_preview_and_noop(tmp_path):
    path = make_root(tmp_path / 'hub.db')
    with open_database(path, writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE')
        result = apply(db, b.SetUserActive('G', 5, False))
        user_version = db.raw.execute("SELECT version FROM users WHERE id='G'").fetchone()[0]
        authority_version = db.raw.execute("SELECT version FROM agent_authority WHERE agent_user_id='G'").fetchone()[0]
        receipt(tmp_path / 'activity-first.json', visible=asdict(result.visible),
                expected_user_version=6, observed_user_version=user_version,
                observed_authority_version=authority_version)
        assert result.visible.version == user_version == 6
        assert authority_version == 5
        assert result.visible == b.VisibleEdit('user active', 'G', 6, True, False)

        unchanged = b.SetUserActive('G', result.visible.version, False)
        assert preview(db, unchanged) == b.VisibleEdit('user active', 'G', 6, False, True)
        before = snapshot(db.raw)
        noop = apply(db, unchanged)
        assert noop.visible == b.VisibleEdit('user active', 'G', 6, False, False)
        assert snapshot(db.raw) == before

        restore = b.SetUserActive('G', noop.visible.version, True)
        assert preview(db, restore) == b.VisibleEdit('user active', 'G', 7, True, True)
        restored = apply(db, restore)
        assert restored.visible.version == db.raw.execute("SELECT version FROM users WHERE id='G'").fetchone()[0] == 7
        assert db.raw.execute("SELECT version FROM agent_authority WHERE agent_user_id='G'").fetchone() == (5,)
        before = snapshot(db.raw)
        repeated = apply(db, b.SetUserActive('G', restored.visible.version, True))
        assert repeated.visible.version == 7 and not repeated.visible.changed
        assert snapshot(db.raw) == before
        again = apply(db, b.SetUserActive('G', repeated.visible.version, False))
        assert again.visible.version == db.raw.execute("SELECT version FROM users WHERE id='G'").fetchone()[0] == 8
        receipt(tmp_path / 'activity-final.json', visible=asdict(again.visible),
                users=rows(db.raw, 'users'), authorities=rows(db.raw, 'agent_authority'))


def test_authority_intents_keep_authority_versions_for_preview_apply_and_noop(tmp_path):
    path = make_root(tmp_path / 'hub.db')
    with open_database(path, writable=True) as db:
        # Deliberately distinct counters, so a user-version substitution cannot pass.
        db.raw.execute("UPDATE users SET version=19 WHERE id='G'")
        db.raw.execute('BEGIN IMMEDIATE')
        change = b.SetAssignments('G', 4, 7, ('Q', 'I'), False)
        assert preview(db, change) == b.VisibleEdit('assignments set', 'G', 5, True, True)
        changed = apply(db, change)
        assert changed.visible == b.VisibleEdit('assignments set', 'G', 5, True, False)
        unchanged = b.SetAssignments('G', changed.visible.version, 8, ('Q', 'I'), False)
        assert preview(db, unchanged) == b.VisibleEdit('assignments set', 'G', 5, False, True)
        before = snapshot(db.raw)
        noop = apply(db, unchanged)
        assert noop.visible.version == 5 and not noop.visible.changed
        assert snapshot(db.raw) == before

        authorize = b.AuthorizeAgent('G', noop.visible.version, 8, True, True)
        assert preview(db, authorize) == b.VisibleEdit('agent authorize', 'G', 6, True, True)
        authorized = apply(db, authorize)
        assert authorized.visible == b.VisibleEdit('agent authorize', 'G', 6, True, False)
        unchanged = b.AuthorizeAgent('G', authorized.visible.version, 8, True, False)
        assert preview(db, unchanged) == b.VisibleEdit('agent authorize', 'G', 6, False, True)
        before = snapshot(db.raw)
        assert apply(db, unchanged).visible == b.VisibleEdit('agent authorize', 'G', 6, False, False)
        assert snapshot(db.raw) == before
        assert db.raw.execute("SELECT version FROM users WHERE id='G'").fetchone() == (19,)
        assert db.raw.execute("SELECT version,epoch FROM agent_authority WHERE agent_user_id='G'").fetchone() == (6, 8)


def test_new_readonly_default_inserts_exact_rows_generation_and_audit(tmp_path):
    path = make_root(tmp_path / 'hub.db')
    with open_database(path, writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE')
        old = s.load_root(db, catalog=BUNDLE)
        old_state = rows(db.raw, 'permission_state')[0]
        old_defaults = rows(db.raw, 'role_capabilities')
        before = snapshot(db.raw)
        addition = c.DefaultEntry('readonly', c.Requirement('ledger.post', 'standard'))
        assert addition not in old.role_defaults
        desired = tuple(sorted((*old.role_defaults, addition), key=lambda x: (
            (*c.ROLES, 'hub_admin').index(x.role), x.requirement.capability,
            c.THRESHOLDS.index(x.requirement.threshold))))
        expected_catalog = replace(old.catalog, defaults=desired)
        expected_state = dict(old_state, generation=2,
            catalog_sha256=c.catalog_manifest(expected_catalog).descriptor_sha256,
            catalog_json=s.encode_catalog(expected_catalog), updated_at=AT,
            updated_by='H', updated_via='cli')
        result = apply(db, b.ReplaceCatalog(old.stamp.catalog_sha256, BUNDLE, desired))
        assert result.visible.changed
        assert rows(db.raw, 'role_capabilities') == (*old_defaults,
            dict(role='readonly', capability='ledger.post', required_role='standard'))
        assert rows(db.raw, 'permission_state') == (expected_state,)
        observed = s.load_root(db, catalog=BUNDLE)
        assert observed.catalog == expected_catalog and observed.role_defaults == desired
        after = snapshot(db.raw)
        assert after['ddl'] == before['ddl'] and after['columns'] == before['columns']
        changed_tables = {'permission_state', 'role_capabilities', 'audit_events', 'audit_entries'}
        for table in before['tables'].keys() - changed_tables:
            assert after['tables'][table] == before['tables'][table]
        assert after['tables']['role_capabilities'][:-1] == before['tables']['role_capabilities']

        events, entries = rows(db.raw, 'audit_events'), rows(db.raw, 'audit_entries')
        assert len(events) == len(entries) == 1
        event, entry = events[0], entries[0]
        assert (event['id'], event['seq'], event['at'], event['command'], event['actor_id']) == (
            result.event_id, 1, AT, 'permission catalog replace', 'H')
        assert (entry['event_id'], entry['record_type'], entry['record_id'], entry['action'],
                entry['version_before'], entry['version_after']) == (event['id'], 'permission_state', '1', 'update', 1, 2)
        def envelope(state, catalog, defaults):
            row = {key: value for key, value in state.items() if key != 'catalog_json'}
            return json.loads(json.dumps(dict(format=1, kind='permission_state', state_key=1,
                row=row, catalog=asdict(catalog), defaults=[asdict(x) for x in defaults])))
        expected_before = envelope(old_state, old.catalog, old.role_defaults)
        expected_after = envelope(expected_state, expected_catalog, desired)
        assert decode_snapshot(entry['before']) == expected_before
        assert decode_snapshot(entry['after']) == expected_after
        receipt(tmp_path / 'defaults-addition.json', expected_state=expected_state,
                observed_state=rows(db.raw, 'permission_state')[0], expected_before=expected_before,
                observed_before=decode_snapshot(entry['before']), expected_after=expected_after,
                observed_after=decode_snapshot(entry['after']), event=event,
                expected_default_rows=(*old_defaults, dict(role='readonly', capability='ledger.post', required_role='standard')),
                observed_default_rows=rows(db.raw, 'role_capabilities'))
