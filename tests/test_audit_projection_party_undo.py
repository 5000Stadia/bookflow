"""Actual compensating party events under current governed history authority."""
from pathlib import Path
import pytest
from bookflow.storage.engine import open_database
from tests.test_audit_projection_activity import world
from tests.test_audit_projection_coordinate_events import hosted, denied
from tests.test_audit_projection_draft_disclosure import project, listing, complete

COMPANY = 'Demo Plumbing Co'


@pytest.fixture(scope='module')
def events_world(world):
    client = world['client']
    cases = {}
    for mode in ('link','unlink'):
        customer = client.customer.create(name='Undo projected customer '+mode, company=COMPANY)
        vendor = client.vendor.create(name='Undo projected vendor '+mode, company=COMPANY)
        linked = client.run('customer link-vendor', dict(customer=customer['id'],vendor=vendor['id'],
            expected_customer_version=customer['version'],expected_vendor_version=vendor['version']), company=COMPANY)
        command = 'customer link-vendor'
        if mode == 'unlink':
            client.run('customer unlink-vendor', dict(customer=customer['id'],
                expected_customer_version=linked['customer_version'],expected_vendor_version=linked['vendor_version'],
                expected_link_version=linked['link_version']),company=COMPANY)
            command = 'customer unlink-vendor'
        original = client.audit.list(company=COMPANY, command=command, record_type='customer_vendor_link',record_id=linked['link_id'])['items'][0]['id']
        result = client.run('undo',dict(event_id=original),company=COMPANY)
        cases[mode] = dict(event=result['undo_event_id'],original=original,link=linked['link_id'],customer=customer['id'],vendor=vendor['id'])
    account = client.account.create(name='Ordinary projected undo',type='expense',company=COMPANY)
    original = client.audit.list(company=COMPANY,command='account create',record_type='account',record_id=account['id'])['items'][0]['id']
    result = client.run('undo',dict(event_id=original),company=COMPANY)
    cases['ordinary'] = dict(event=result['undo_event_id'],original=original,account=account['id'])
    info = client.company.show(company=COMPANY)
    return dict(root=world['root'],path=Path(info['path'])/'company.db',cid=info['company_id'],cases=cases)


@pytest.mark.parametrize('mode,action', [('link','deactivate'),('unlink','update'),('ordinary','deactivate')])
def test_real_undo_full_event_and_list_preserve_actions(events_world, mode, action):
    c = events_world; case=c['cases'][mode]
    kind = 'account' if mode == 'ordinary' else 'customer_vendor_link'
    identity = case['account'] if mode == 'ordinary' else case['link']
    with open_database(c['path'],writable=False) as db:
        raw = db.raw.execute('SELECT command,undo_of_event_id FROM audit_events WHERE id=?',(case['event'],)).fetchone()
        assert raw == ('undo',case['original'])
        assert db.raw.execute('SELECT action FROM audit_entries WHERE event_id=? AND record_type=? AND record_id=?',
            (case['event'],kind,identity)).fetchone() == (action,)
    with hosted(c) as host:
        event = complete(c,project(c,host,case['event']))
        entry = next(e for e in event.entries if e.identity.kind==kind and e.identity.id==identity)
        assert entry.action==action
        assert entry.before.active is (mode=='link' or mode=='ordinary')
        assert entry.after.active is (mode=='unlink')
        page = listing(c,host,kind,identity)
        assert next(e for e in page if e.id==event.id) == event
        if mode!='ordinary':
            assert {e.identity.kind for e in event.entries} == {'customer','vendor','customer_vendor_link'}


# Actual governed denial + list read + B2 restoration exceeded60s.
# Keep the global deadline and ordinary producer cases unchanged.
@pytest.mark.timeout(120)
@pytest.mark.parametrize('capability', ('customer','vendor'))
def test_link_undo_requires_both_current_party_rights(events_world, capability):
    c = events_world; case=c['cases']['link']
    with hosted(c) as host:
        event=complete(c,project(c,host,case['event']))
        assert any(e.identity.kind=='customer_vendor_link' for e in event.entries)
        with denied(c,host,(capability,)):
            hidden=project(c,host,case['event'])
            # This is per-entry conjunction, not financial whole-event admission.
            # Any still-visible endpoint must independently survive its owner.
            if hidden is not None:
                assert all(e.identity.kind not in (capability,'customer_vendor_link') for e in hidden.entries)
                assert case['link'] not in repr(hidden) and case[capability] not in repr(hidden)
            assert listing(c,host,'customer_vendor_link',case['link']) == ()
