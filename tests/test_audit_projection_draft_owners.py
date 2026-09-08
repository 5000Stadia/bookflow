"""Real co24 retained-row ownership proofs, before event-reader registration."""
from pathlib import Path
import pytest
import bookflow
from bookflow.storage.engine import open_database
from bookflow.hub.permission_runtime import catalog_bundle
from bookflow.hub import audit_projection_deposit_drafts as codec
from tests.test_permission_snapshots import install_fixture_policy
from tests.test_audit_projection_activity import storage
from tests import test_audit_projection_deposit_draft_records as captures


@pytest.mark.parametrize('kind', tuple(codec.MODELS))
def test_draft_images_prove_their_retained_owner(draft_history, kind):
    from bookflow.core.audit import decode_snapshot
    from bookflow.hub import audit_projection_draft_owners as owners
    c = draft_history
    original = storage(c['path'])
    with open_database(c['path'], writable=False) as db:
        for row in c['rows']:
            if row['record_type'] != kind:
                continue
            event = dict(id=row['event_id'], command=row['command'])
            for side in ('before', 'after'):
                if row[side] is not None:
                    owners.validate(db, event=event, entry=row,
                                    snapshot=decode_snapshot(row[side]), side=side)
    assert storage(c['path']) == original


def test_draft_images_reject_wrong_event_and_retained_values(draft_history):
    from bookflow import BookflowError
    from bookflow.core.audit import decode_snapshot
    from bookflow.hub import audit_projection_draft_owners as owners
    c = draft_history
    original = storage(c['path'])
    with open_database(c['path'], writable=False) as db:
        row = next(r for r in c['rows'] if r['record_type'] == 'deposit_draft_source')
        raw = decode_snapshot(row['after'])
        event = dict(id=row['event_id'], command=row['command'])
        other_event = next(r['event_id'] for r in c['rows'] if r['event_id'] != event['id'])
        for event_value, image in ((dict(event, id=other_event), raw),
                                   (event, dict(raw, created_at='2026-01-01T00:00:00Z'))):
            codec.decode_snapshot(producer=event['command'], record_type=row['record_type'],
                                  action=row['action'], record_id=row['record_id'], snapshot=image)
            with pytest.raises(BookflowError) as error:
                owners.validate(db, event=event_value, entry=row, snapshot=image, side='after')
            assert error.value.code == 'E_VALIDATION'
            assert error.value.details == {'reason': 'audit_format'}
    assert storage(c['path']) == original


def test_mutable_draft_image_cannot_borrow_another_drafts_revision(draft_history):
    from bookflow import BookflowError
    from bookflow.core.audit import decode_snapshot
    from bookflow.hub import audit_projection_draft_owners as owners
    c = draft_history
    original = storage(c['path'])
    row = next(r for r in c['rows'] if r['record_type'] == 'deposit_draft' and r['action'] == 'create')
    raw = decode_snapshot(row['after'])
    with open_database(c['path'], writable=False) as db:
        foreign_revision = db.raw.execute('SELECT id FROM deposit_draft_revisions WHERE draft_id != ? LIMIT 1',
                                          (raw['id'],)).fetchone()[0]
        broken = dict(raw, current_revision_id=foreign_revision)
        codec.decode_snapshot(producer=row['command'], record_type=row['record_type'],
                              action=row['action'], record_id=row['record_id'], snapshot=broken)
        with pytest.raises(BookflowError) as error:
            owners.validate(db, event=dict(id=row['event_id'], command=row['command']),
                            entry=row, snapshot=broken, side='after')
        assert error.value.details == {'reason': 'audit_format'}
    assert storage(c['path']) == original


def test_old_header_cannot_use_later_revision_or_current_event(draft_history):
    from bookflow import BookflowError
    from bookflow.core.audit import decode_snapshot
    from bookflow.hub import audit_projection_draft_owners as owners
    c = draft_history
    original = storage(c['path'])
    row = next(r for r in c['rows'] if r['record_type'] == 'deposit_draft' and r['before'] is not None)
    raw = decode_snapshot(row['before'])
    with open_database(c['path'], writable=False) as db:
        later = db.raw.execute('SELECT id FROM deposit_draft_revisions WHERE draft_id=? AND version>? LIMIT 1',
                               (raw['id'], raw['version'])).fetchone()[0]
        for broken in (dict(raw, current_revision_id=later), dict(raw, audit_event_id=row['event_id'])):
            codec.decode_snapshot(producer=row['command'], record_type=row['record_type'],
                                  action=row['action'], record_id=row['record_id'], snapshot=broken)
            with pytest.raises(BookflowError) as error:
                owners.validate(db, event=dict(id=row['event_id'], command=row['command']),
                                entry=row, snapshot=broken, side='before')
            assert error.value.details == {'reason': 'audit_format'}
    assert storage(c['path']) == original


@pytest.fixture(scope='module')
def draft_history(_seeded_template, tmp_path_factory):
    # Reuse the actual producer fixture; record its allocated root without changing
    # any producer, session, binding or authority behavior.
    class Paths:
        def mktemp(self, name):
            self.directory = tmp_path_factory.mktemp(name)
            return self.directory
    paths = Paths()
    rows = captures.records.__wrapped__(_seeded_template, paths)
    root = paths.directory / 'root'
    with open_database(root/'hub.db', writable=True) as db:
        install_fixture_policy(db.raw, catalog_bundle())
    company = bookflow.connect(data_root=str(root)).company.show(company='Demo Plumbing Co')
    return dict(root=root, path=Path(company['path'])/'company.db', cid=company['company_id'], rows=rows)


