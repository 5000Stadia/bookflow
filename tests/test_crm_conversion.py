"""R115 increment 2: a lead becomes a customer through native validation, one history reads across both, and
source-keyed lead imports and conversions reverse as one unit while nothing later depends on them."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import threading

import anyio
import pytest

from bookflow import BookflowError

SURFACES = ('python', 'cli', 'http', 'mcp')


@pytest.fixture
def books(tmp_path, monkeypatch):
    from tests.crm_support import company
    return company(tmp_path, monkeypatch, 'UTC')


def write(books, command, values, **context):
    return books.run(command, values, reason='Work the enquiry', **context)


def database(books):
    return Path(books.run('company show', {})['path']) / 'company.db'


def counts(books):
    with sqlite3.connect(database(books)) as db:
        return {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0]
                for t in ('customers', 'customer_contacts', 'crm_conversions', 'crm_conversion_contacts', 'batches',
                          'crm_lead_import_keys', 'crm_leads')}


def enquiry(books, name='Ada Lane', **extra):
    lead = write(books, 'lead create', {'display_name': name, 'summary': 'Wants a quote', **extra, 'contacts': [
        {'role': 'primary', 'display_name': 'Ada', 'primary_email': 'ada@example.test', 'mobile_phone': '555-0100'}]})
    write(books, 'note add', {'record_type': 'lead', 'record_id': lead['id'], 'body': 'Called about the roof'})
    return lead


def convert(books, lead, **values):
    context = {k: values.pop(k) for k in ('dry_run', 'idempotency_key') if k in values}
    return write(books, 'lead convert', {'lead': lead['id'], 'expected_version': lead['version'], 'target': 'new',
                                         **values}, **context)


def blockers(books, batch):
    with pytest.raises(BookflowError) as refused:
        write(books, 'batch reverse', {'batch': batch})
    assert refused.value.code == 'E_BATCH_BLOCKED', refused.value.to_dict()
    return {b['code'] for b in refused.value.details['blockers']}


def test_two_conversions_of_one_lead_racing_leave_one_customer(books):
    lead = enquiry(books)
    before = counts(books)
    start = threading.Barrier(2)

    def attempt(name):
        import bookflow
        client = bookflow.connect(data_root=str(books.root))
        start.wait()
        try:
            return client.run('lead convert', {'lead': lead['id'], 'expected_version': lead['version'], 'target': 'new',
                                               'name': name}, company=books.company, reason='Won the work')
        except BookflowError as error:
            return error.code
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(attempt, ('Ada Lane Roofing', 'Ada Lane Building')))
    won = [x for x in outcomes if isinstance(x, dict)]
    lost = [x for x in outcomes if not isinstance(x, dict)]
    assert len(won) == 1 and lost and lost[0] in {'E_CRM_ALREADY_CONVERTED', 'E_VERSION_CONFLICT', 'E_BUSY'}, outcomes
    after = counts(books)
    assert after['customers'] == before['customers'] + 1 and after['crm_conversions'] == 1
    with pytest.raises(BookflowError) as again:
        convert(books, books.run('lead show', {'lead': lead['id']}), name='Third name')
    assert again.value.code == 'E_CRM_ALREADY_CONVERTED'
    assert again.value.details['customer_id'] == won[0]['conversion']['customer_id']
    with sqlite3.connect(database(books)) as db, pytest.raises(sqlite3.IntegrityError):
        row = db.execute('SELECT * FROM crm_conversions').fetchone()
        db.execute('INSERT INTO crm_conversions SELECT ?' + ',?' * (len(row) - 1), ('X' * 26, *row[1:4], 'Y' * 26, *row[5:]))


def test_lost_response_retried_returns_the_same_conversion(books):
    lead = enquiry(books)
    preview = convert(books, lead, dry_run=True)
    assert preview['conversion']['id'] is None and preview['conversion']['customer_id'] is None
    assert books.run('lead show', {'lead': lead['id']})['status'] == 'open' and counts(books)['crm_conversions'] == 0
    assert preview['copied_fields'] == ['notes', 'contacts']
    first = convert(books, lead, idempotency_key='won-ada')
    keyed = convert(books, lead, idempotency_key='won-ada')
    assert keyed['conversion']['id'] == first['conversion']['id']
    unkeyed = convert(books, lead)
    assert unkeyed['replayed'] and unkeyed['conversion']['id'] == first['conversion']['id']
    assert counts(books)['crm_conversions'] == 1
    customer = books.run('customer show', {'customer': first['conversion']['customer_id']})
    assert customer['name'] == 'Ada Lane' and customer['contacts'][0]['primary_email'] == 'ada@example.test'
    assert books.run('lead show', {'lead': lead['id']})['status'] == 'converted'
    history = books.run('crm timeline', {'customer': customer['id']})
    assert [x['kind'] for x in history['items']] == ['conversion', 'note']
    assert history['items'][1]['from_lead'] == 'From lead Ada Lane'


def test_mapping_onto_an_existing_contact_copies_only_named_fields_and_reverses(books):
    customer = write(books, 'customer create', {'name': 'Riverside Clinic', 'contacts': [
        {'role': 'primary', 'display_name': 'Front desk', 'primary_email': 'desk@example.test'}]})
    shown = books.run('customer show', {'customer': customer['id']})
    lead = enquiry(books, 'Riverside second site')
    lead = books.run('lead show', {'lead': lead['id']})
    lead_contact, customer_contact = lead['contacts'][0]['id'], shown['contacts'][0]['id']
    values = {'target': 'existing', 'customer': customer['id'], 'customer_expected_version': shown['version'],
              'contact_transfers': [{'lead_contact': lead_contact, 'customer_contact': customer_contact,
                                     'fields': ['mobile_phone']}]}
    out = convert(books, lead, **values)
    assert out['conversion']['contact_map'] == [{'lead_contact_id': lead_contact, 'customer_contact_id': customer_contact,
                                                 'action': 'updated', 'fields': ['mobile_phone']}]
    contact = books.run('customer show', {'customer': customer['id']})['contacts'][0]
    assert contact['mobile_phone'] == '555-0100' and contact['primary_email'] == 'desk@example.test'
    assert contact['display_name'] == 'Front desk'
    reversed_ = write(books, 'batch reverse', {'batch': out['conversion']['batch_id']})
    assert reversed_['state'] == 'reversed'
    contact = books.run('customer show', {'customer': customer['id']})['contacts'][0]
    assert contact['mobile_phone'] is None and contact['primary_email'] == 'desk@example.test'
    assert books.run('lead show', {'lead': lead['id']})['status'] == 'open'
    history = books.run('crm timeline', {'lead': lead['id']})
    assert {'conversion', 'conversion_reversed', 'note'} <= {x['kind'] for x in history['items']}


def test_source_key_collision_refuses_changed_rows_and_repeats_none(books):
    rows = [{'source_key': 'r1', 'display_name': 'Harbor Cafe'}, {'source_key': 'r2', 'display_name': 'Elm Dental'}]
    first = write(books, 'lead import', {'source': 'fair', 'rows': rows})
    assert first['created'] == 2
    again = write(books, 'lead import', {'source': 'fair', 'rows': rows})
    assert again['created'] == 0 and again['unchanged'] == 2 and counts(books)['crm_leads'] == 2
    with pytest.raises(BookflowError) as changed:
        write(books, 'lead import', {'source': 'fair', 'rows': [{'source_key': 'r1', 'display_name': 'Harbour Cafe'}]})
    assert changed.value.details['problem'] == 'source_key_changed'
    with pytest.raises(BookflowError) as doubled:
        write(books, 'lead import', {'source': 'fair', 'rows': [rows[0], {**rows[1], 'source_key': 'r1'}]})
    assert doubled.value.details['problem'] == 'duplicate_key'
    other = write(books, 'lead import', {'source': 'website', 'rows': [rows[0]]})
    assert other['created'] == 1 and other['batch_id'] != first['batch_id']
    assert counts(books)['crm_leads'] == 3


def test_converter_denied_customer_write_writes_nothing(books):
    from tests.conftest import as_user, make_actor
    lead = enquiry(books)
    customer = write(books, 'customer create', {'name': 'Linked account'})
    user = make_actor(books.root, 'crm-converter', company_role=(books.company, 'standard'))
    books.client.run('membership grant', {'user': user, 'company': books.company, 'role': 'standard',
                                          'denies': ['customer'], 'expected_version': 1}, reason='No customer access')
    denied = as_user(books.root, 'crm-converter')
    before = counts(books)
    for values in ({'target': 'new'},
                   {'target': 'existing', 'customer': customer['id'], 'customer_expected_version': 1}):
        with pytest.raises(BookflowError) as refused:
            denied.run('lead convert', {'lead': lead['id'], 'expected_version': lead['version'], **values},
                       company=books.company, reason='Attempt a conversion')
        assert refused.value.code == 'E_PERMISSION', refused.value.to_dict()
    assert counts(books) == before
    assert books.run('lead show', {'lead': lead['id']})['status'] == 'open'


@pytest.mark.parametrize('later', ('note', 'todo', 'document'))
def test_later_note_task_and_document_block_reversal_by_name(books, later):
    lead = enquiry(books)
    out = convert(books, lead)
    customer, batch = out['conversion']['customer_id'], out['conversion']['batch_id']
    assert books.run('batch show', {'batch': batch})['batch']['reversible']
    if later == 'note':
        write(books, 'note add', {'record_type': 'customer', 'record_id': customer, 'body': 'Booked a visit'})
        expected = 'later_note'
    elif later == 'todo':
        write(books, 'todo create', {'title': 'Send the quote', 'link_type': 'customer', 'link': customer})
        expected = 'later_task'
    else:
        income = write(books, 'account create', {'name': 'Roof income', 'type': 'income'})['id']
        code = next(r['id'] for r in books.run('sales-tax-code list', {})['items'] if not r['taxable'])
        item = write(books, 'item create', {'name': 'Roof survey', 'type': 'service', 'sales_enabled': True,
            'income_account_id': income, 'price': '100.00', 'sales_tax_code_id': code})['id']
        write(books, 'estimate create', {'date': '2026-01-12', 'title': 'Roof', 'customer': customer,
                                         'lines': [{'item': item}]})
        expected = 'later_document'
    assert expected in blockers(books, batch)
    assert books.run('lead show', {'lead': lead['id']})['status'] == 'converted'
    assert books.run('customer show', {'customer': customer})['active']


def test_failed_import_resumes_without_duplicating(books, monkeypatch):
    from bookflow.company import crm_imports
    real = crm_imports.batches.run_nested
    landed = []

    def flaky(s, ctx, name, values, **kw):
        if not kw.get('dry_run') and len(landed) == 1:
            raise RuntimeError('lost the disk')
        out = real(s, ctx, name, values, **kw)
        if not kw.get('dry_run'):
            landed.append(out['id'])
        return out
    rows = [{'source_key': str(n), 'display_name': f'Visitor {n}'} for n in range(3)]
    monkeypatch.setattr(crm_imports.batches, 'run_nested', flaky)
    with pytest.raises(RuntimeError):
        write(books, 'lead import', {'source': 'fair', 'rows': rows})
    assert counts(books)['crm_leads'] == 1 and counts(books)['crm_lead_import_keys'] == 1
    monkeypatch.setattr(crm_imports.batches, 'run_nested', real)
    resumed = write(books, 'lead import', {'source': 'fair', 'rows': rows})
    assert resumed['created'] == 2 and resumed['unchanged'] == 1
    assert resumed['rows'][0]['lead_id'] == landed[0]
    assert counts(books)['crm_leads'] == 3
    shown = books.run('batch show', {'batch': resumed['batch_id']})
    assert shown['batch']['reversible']


def test_interrupted_reversal_resumes_and_keeps_history(books, monkeypatch):
    from dataclasses import replace
    from bookflow.company import batches
    rows = [{'source_key': str(n), 'display_name': f'Visitor {n}'} for n in range(3)]
    batch = write(books, 'lead import', {'source': 'fair', 'rows': rows})['batch_id']
    adapter = batches.ADAPTERS['crm-lead-import']
    calls = 0

    def interrupt(s, ctx, batch_, reversal_id, item):
        nonlocal calls
        if calls == 1:
            raise RuntimeError('interrupted')
        adapter.runner(s, ctx, batch_, reversal_id, item)
        calls += 1
    monkeypatch.setitem(batches.ADAPTERS, 'crm-lead-import', replace(adapter, runner=interrupt))
    with pytest.raises(RuntimeError, match='interrupted'):
        write(books, 'batch reverse', {'batch': batch})
    assert len(books.run('lead list', {})['items']) == 2
    with pytest.raises(BookflowError):  # a new import of that source waits for the reversal to finish
        write(books, 'lead import', {'source': 'fair', 'rows': rows[:1]})
    monkeypatch.setitem(batches.ADAPTERS, 'crm-lead-import', adapter)
    done = write(books, 'batch reverse', {'batch': batch})
    assert done['state'] == 'reversed' and done['reversed_now'] == 2
    closed = books.run('lead list', {'status': 'all', 'include_archived': True})['items']
    assert len(closed) == 3 and {x['status'] for x in closed} == {'closed'} and all(x['archived'] for x in closed)
    with sqlite3.connect(database(books)) as db:
        assert db.execute('SELECT count(*) FROM crm_lead_import_keys WHERE released_by IS NULL').fetchone() == (0,)
    again = write(books, 'lead import', {'source': 'fair', 'rows': rows})
    assert again['created'] == 3
    # A conversion reversal interrupted before its audit event rolls back whole and reruns cleanly.
    lead = enquiry(books)
    out = convert(books, lead)
    from bookflow.core import audit
    real = audit.write_event_to
    monkeypatch.setattr(audit, 'write_event_to', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('cut')))
    with pytest.raises(RuntimeError, match='cut'):
        write(books, 'batch reverse', {'batch': out['conversion']['batch_id']})
    monkeypatch.setattr(audit, 'write_event_to', real)
    assert books.run('lead show', {'lead': lead['id']})['status'] == 'converted'
    assert books.run('customer show', {'customer': out['conversion']['customer_id']})['active']
    assert write(books, 'batch reverse', {'batch': out['conversion']['batch_id']})['state'] == 'reversed'
    customer = books.run('customer show', {'customer': out['conversion']['customer_id']})
    assert not customer['active'] and customer['name'] != 'Ada Lane'
    history = books.run('crm timeline', {'lead': lead['id']})
    assert [x['kind'] for x in history['items']][:2] == ['conversion_reversed', 'conversion']


def test_customer_merge_and_unmerge_after_conversion(books):
    lead = enquiry(books)
    out = convert(books, lead)
    converted, batch = out['conversion']['customer_id'], out['conversion']['batch_id']
    survivor = write(books, 'customer create', {'name': 'Ada Lane Holdings'})['id']
    write(books, 'customer merge', {'merged': converted, 'into': survivor})
    assert 'customer_merged' in blockers(books, batch)
    history = books.run('crm timeline', {'customer': survivor})
    assert {'conversion', 'note'} <= {x['kind'] for x in history['items']}
    assert any(x['from_lead'] == 'From lead Ada Lane' for x in history['items'])
    write(books, 'customer unmerge', {'merged': converted})
    # Undoing the merge reactivates the customer: a later change to it that the reversal names rather than undoes.
    assert blockers(books, batch) == {'customer_edited'}
    assert books.run('crm timeline', {'customer': converted})['items'][-1]['from_lead'] == 'From lead Ada Lane'


def test_old_company_gains_conversion_tables(books, monkeypatch):
    from bookflow.storage.engine import open_database
    from bookflow.storage.migrate import HEADS, migrate_to_head
    head = HEADS['company']
    lead = enquiry(books)
    path = database(books)
    with sqlite3.connect(path) as db:
        before = db.execute('SELECT * FROM crm_leads').fetchall()
        for table in ('crm_lead_import_keys', 'crm_conversion_contacts', 'crm_conversions'):
            db.execute('DROP TABLE ' + table)
        db.execute("UPDATE alembic_version SET version_num='co0135'")
    # Only this migration is rewound and run; the file is stamped back to its real head afterwards.
    with monkeypatch.context() as patch:
        patch.setitem(HEADS, 'company', 'co0136')
        with open_database(path, writable=True) as db:
            assert migrate_to_head(db, 'company', None) == ('co0135', 'co0136')
            assert db.raw.execute('SELECT * FROM crm_leads').fetchall() == before
            assert db.raw.execute('SELECT count(*) FROM crm_conversions').fetchone() == (0,)
            assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
    with sqlite3.connect(path) as db:
        db.execute('UPDATE alembic_version SET version_num=?', (head,))
    assert convert(books, lead)['lead']['status'] == 'converted'


@pytest.mark.parametrize('surface', SURFACES)
def test_conversion_history_and_import_through_python_cli_http_and_mcp(books, tmp_path, surface):
    from tests.mcp_matrix_support import Matrix

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(books.root, tmp_path / 'surfaces')
            call = lambda name, values: matrix.call(surface, name, values)
            lead = await call('lead create', {'display_name': 'Surface enquiry'})
            await call('note add', {'record_type': 'lead', 'record_id': lead['id'], 'body': 'Asked for a visit'})
            out = await call('lead convert', {'lead': lead['id'], 'expected_version': lead['version'], 'target': 'new'})
            history = await call('crm timeline', {'customer': out['conversion']['customer_id']})
            imported = await call('lead import', {'source': 'surface list', 'rows': [
                {'source_key': 'a', 'display_name': 'Listed enquiry'}]})
            shown = await call('batch show', {'batch': imported['batch_id']})
            assert out['lead']['status'] == 'converted' and out['conversion']['state'] == 'active'
            assert [x['kind'] for x in history['items']] == ['conversion', 'note']
            assert history['items'][1]['from_lead'] == 'From lead Surface enquiry'
            assert imported['created'] == 1 and shown['batch']['reversible']
        finally:
            await matrix.close()
    anyio.run(witness)
