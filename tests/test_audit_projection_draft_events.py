"""Real co24 history through the shared current-authority event and list readers."""
from pathlib import Path
import pytest
import bookflow
from bookflow.core.config import os_login
from bookflow.core.context import Context, client_version
from bookflow.core.host import Host
from bookflow.core.publication import OSBinding
from bookflow.core import identity_admin_binding as binding
from bookflow.core.publication_audit import open_selected
from bookflow.storage.engine import open_database
from bookflow.hub.permission_runtime import catalog_bundle
from bookflow.hub import audit_projection as projection
from bookflow.hub import audit_projection_deposit_drafts as codec
from tests.test_permission_snapshots import install_fixture_policy
from tests.test_audit_projection_activity import storage
from tests.test_audit_projection_draft_owners import draft_history


def test_stored_entry_requirements_exist_in_current_catalog(draft_history):
    from bookflow.hub import audit_projection_legacy as legacy
    catalog = catalog_bundle().descriptor
    known = {(cap.name, threshold) for cap in catalog.capabilities for threshold in cap.company_thresholds}
    with open_database(draft_history['path'], writable=False) as db:
        for (kind,) in db.raw.execute('SELECT DISTINCT record_type FROM audit_entries'):
            assert set(legacy.entry_requirement(kind)) <= known, kind


@pytest.mark.parametrize('kind', tuple(codec.MODELS))
def test_every_draft_kind_projects_its_complete_real_event(draft_history, kind):
    c = draft_history
    row = next(row for row in reversed(c['rows']) if row['record_type'] == kind)
    original = storage(c['path'])
    host = Host(c['root'], version=client_version())
    host.start()
    try:
        ctx = Context.new('http', 'Draft history witness')
        selection = projection.HistorySelection(mode='show',company=c['cid'],event=row['event_id'])
        with binding.hosted_reader(host, OSBinding.capture(host,os_login()), request_id=ctx.request_id) as reader:
            open_selected(reader,selection,ctx)
            audience = projection.make_audience(reader)
            event = projection.project_event(audience,row['event_id'],company=c['cid'])
            assert event is not None
            expected = set(reader.session.company.raw.execute(
                'SELECT id,record_type,record_id,action FROM audit_entries WHERE event_id=?', (row['event_id'],)))
            assert {(e.id,e.identity.kind,e.identity.id,e.action) for e in event.entries} == expected
            assert any(e.identity.kind == kind for e in event.entries)
            if kind == 'deposit_draft':
                page = projection.project_history(audience,projection.HistorySelection(
                    mode='list',company=c['cid'],record_type=kind,limit=100))
                assert next(e for e in page.events if e.id == event.id) == event
        assert host._readers_attached == 0
    finally:
        host.stop()
    assert storage(c['path']) == original
