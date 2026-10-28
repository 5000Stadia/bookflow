"""R107 increment 1: list imports (design/bulk-import.md §2-§5, §8a, §11).

Done when a 200-row customer file with 5 bad rows previews with the 5 listed, runs 195, reruns with 0 created, and
the 5 fixed rows then import alone; a killed slice resumes with no duplicates; an import is the same as typing
(criterion 5, with permission parity); `import reverse` removes the 195 created and releases their keys
(criterion 11, lists); and every company-only write command is a kind, planned, or not importable with a reason.
"""
import csv
import io
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import bookflow
from bookflow import BookflowError
from bookflow.company import batches, import_kinds
from bookflow.core import registry

BAD = {17: ('Terms', 'Net 99', 'unknown_reference'), 44: ('Credit Limit', '12x', 'bad_value'),
       90: ('Credit Limit', '1.005', 'E_AMOUNT_PRECISION'), 133: ('Customer', '', 'no_key'),
       171: ('Customer', 'Customer 005', 'duplicate_in_file')}
HEADER = ['Customer', 'E-mail', 'Phone', 'Terms', 'Credit Limit', 'City', 'Zip']


@pytest.fixture
def books(tmp_path, monkeypatch):
    data_root = tmp_path / 'imports'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Imports organization')
    company = client.company.new(legal_name='Imports', home_currency='USD', timezone='UTC',
                                 organization='Imports organization', chart='general')['company_id']

    def run(name, raw=None, **context):
        return client.run(name, raw or {}, company=company, **context)
    return SimpleNamespace(client=client, company=company, run=run, root=data_root)


def rows_file(n=200, bad=None, start=1):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(HEADER)
    for i in range(start, start + n):
        row = [f'Customer {i:03d}', f'c{i}@example.test', f'0{i:04d}', ['Net 30', 'Net 15'][i % 2], f'{i * 10},00'
               if False else f'{i * 10}.50', 'Springfield', f'0{i:04d}']
        for position, (column, value, _) in (bad or {}).items():
            if position == i:
                row[HEADER.index(column)] = value
        writer.writerow(row)
    return out.getvalue()


def company_db(books):
    return sqlite3.connect(Path(books.run('company show')['path']) / 'company.db')


def active_customers(books):
    with company_db(books) as db:
        return {row[0] for row in db.execute('SELECT full_name FROM customers WHERE active = 1')}


def run_all(books, run_id, **context):
    while True:
        out = books.run('import run', {'run': run_id}, **context)
        if out['run']['state'] != 'running':
            return out


def test_two_hundred_rows_five_bad_rerun_fix_and_reverse(books):
    text = rows_file(bad=BAD)
    # All or nothing is the default (Kabe 2026-10-27): the file is refused whole, every problem named at once.
    with pytest.raises(BookflowError) as caught:
        books.run('import preview', {'kind': 'customer', 'content': text})
    assert caught.value.code == 'E_VALIDATION' and caught.value.details['check']['rows_with_problems'] == 4
    preview = books.run('import preview', {'kind': 'customer', 'content': text, 'file_name': 'old.csv',
                                           'partial': True}, reason='Customer list from the old system')
    assert preview['counts'] == {'create': 195, 'refused': 4, 'waiting': 1} and preview['run']['state'] == 'waiting'
    refused = {r['row']: (r['line'], r['column'], r['code']) for r in preview['refused']}
    assert refused == {row: (row + 1, column, code) for row, (column, _, code) in BAD.items()
                       if code not in ('no_key', 'duplicate_in_file', 'unknown_reference')} | {
        133: (134, None, 'no_key'), 171: (172, None, 'duplicate_in_file')}
    assert preview['mapping']['columns']['E-mail'] == 'email' and preview['mapping']['columns']['Zip'] == \
        'billing_address.postal_code'
    assert preview['unknown_references'][0]['suggestions'] and preview['unknown_references'][0]['rows'] == [17]
    sample = preview['sample'][0]
    assert sample == {'row': 1, 'command': 'customer create', 'input': {
        'name': 'Customer 001', 'email': 'c1@example.test', 'phone': '00001', 'terms_id': 'Net 15',
        'credit_limit': '10.50', 'billing_address': {'city': 'Springfield', 'postal_code': '00001'}}}
    assert active_customers(books) == set()  # a preview writes nothing to the books
    run_id = preview['run']['id']
    # The unknown term asks; the run waits until it is decided.
    assert books.run('import show', {'run': run_id})['decisions'][0]['value'] == 'Net 99'
    with pytest.raises(BookflowError) as caught:
        books.run('import run', {'run': run_id})
    assert caught.value.code == 'E_VALIDATION' and 'Net 99' in caught.value.message
    books.run('import decide', {'run': run_id, 'skip': ['Net 99']})
    done = run_all(books, run_id, reason='Customer list')
    assert done['counts'] == {'created': 195, 'refused': 4, 'skipped': 1, 'not_run': 0}
    assert done['run']['state'] == 'done'
    assert len(active_customers(books)) == 195
    acme = books.run('customer show', {'customer': 'Customer 001'})
    assert acme['phone'] == '00001' and acme['billing_address']['postal_code'] == '00001'  # leading zeros kept
    with company_db(books) as db:
        source_refs = {row[0] for row in db.execute("SELECT source_ref FROM audit_events WHERE command = "
                                                    "'customer create'")}
    assert len(source_refs) == 195 and all(ref.startswith(f'import:{run_id}:') for ref in source_refs)
    # A rerun of the same source creates nothing.
    again = books.run('import preview', {'kind': 'customer', 'content': text, 'partial': True})
    assert again['counts'] == {'already_in': 195, 'refused': 4, 'waiting': 1}
    books.run('import decide', {'run': again['run']['id'], 'skip': ['Terms:Net 99']})
    assert run_all(books, again['run']['id'])['counts'] == {'already_in': 195, 'refused': 4, 'skipped': 1,
                                                            'not_run': 0}
    # The error file, corrected, imports those 5 and only them.
    errors = books.run('import errors', {'run': run_id})
    assert errors['rows'] == 5 and errors['file_name'] == 'old - refused rows.csv'
    fixed = []
    for record in csv.reader(io.StringIO(errors['content'])):
        if record[0] == 'Customer':
            fixed.append(record)
            continue
        line = int(record[-1])
        record[0] = record[0] or 'Customer 133'
        record[0] = 'Customer 171' if line == 172 else record[0]
        record[3] = 'Net 30' if record[3] == 'Net 99' else record[3]
        record[4] = '12' if record[4] in ('12x', '1.005') else record[4]
        fixed.append(record)
    out = io.StringIO()
    csv.writer(out, lineterminator='\n').writerows(fixed)
    corrected = books.run('import preview', {'kind': 'customer', 'content': out.getvalue()})
    assert corrected['counts'] == {'create': 5} and set(corrected['mapping']['unmapped']) == {'code', 'message', 'line'}
    assert run_all(books, corrected['run']['id'])['counts']['created'] == 5
    assert len(active_customers(books)) == 200
    # Reversing the first run removes its 195 from the lists and releases their keys.
    plan = books.run('import reverse', {'run': run_id}, dry_run=True)
    assert plan['remaining'] == 195 and plan['blockers'] == [] and plan['batch']['state'] == 'live'
    reversed_ = books.run('import reverse', {'run': run_id, 'fingerprint': plan['fingerprint']},
                          reason='Terms were mapped wrongly')
    assert reversed_['state'] == 'reversed' and reversed_['reversed_now'] == 195
    assert active_customers(books) == {'Customer 017', 'Customer 044', 'Customer 090', 'Customer 133', 'Customer 171'}
    batch = books.run('batch show', {'batch': reversed_['batch']['id']})['batch']
    assert batch['state'] == 'reversed' and batch['reversal_reason'] == 'Terms were mapped wrongly'
    assert batch['counts'] == {'created': 195} and batch['reversible'] is False
    with pytest.raises(BookflowError) as caught:
        books.run('import reverse', {'run': run_id})
    assert caught.value.code == 'E_VALIDATION'  # a reversed batch is never reopened
    # The corrected file then imports as new: the keys and names were released.
    anew = books.run('import preview', {'kind': 'customer', 'content': rows_file(bad={})})
    assert anew['counts'] == {'create': 195, 'already_in': 5}
    run_all(books, anew['run']['id'])
    assert len(active_customers(books)) == 200


def test_a_killed_slice_resumes_with_no_duplicates(books, monkeypatch):
    preview = books.run('import preview', {'kind': 'customer', 'content': rows_file(30)})
    real, calls = batches.run_nested, {'n': 0}

    def dying(s, ctx, name, payload, **kw):
        out = real(s, ctx, name, payload, **kw)
        if name == 'customer create' and not kw.get('dry_run'):
            calls['n'] += 1
            if calls['n'] == 7:
                raise KeyboardInterrupt('killed after the seventh write committed')
        return out
    monkeypatch.setattr(batches, 'run_nested', dying)
    with pytest.raises(KeyboardInterrupt):
        books.run('import run', {'run': preview['run']['id'], 'limit': 10})
    monkeypatch.setattr(batches, 'run_nested', real)
    assert len(active_customers(books)) == 7
    shown = books.run('import show', {'run': preview['run']['id']})
    assert shown['counts']['created'] == 7 and shown['counts']['not_run'] == 23
    first = books.run('import run', {'run': preview['run']['id'], 'limit': 10})
    assert first['run']['state'] == 'running' and first['done_now'] == 10 and first['next'].startswith('import run')
    done = run_all(books, preview['run']['id'])
    assert done['counts'] == {'created': 30, 'not_run': 0} and len(active_customers(books)) == 30
    with company_db(books) as db:
        assert db.execute('SELECT count(*) FROM import_keys').fetchone()[0] == 30
        assert db.execute('SELECT count(*) FROM batch_effects').fetchone()[0] == 30


def test_import_is_the_same_as_typing_and_permission_parity(books):
    import random
    rng = random.Random(107)
    typed, imported = [], []
    for i in range(12):
        row = {'name': f'Party {i}', 'email': f'p{i}@x.test', 'phone': f'{rng.randint(0, 99999):05d}',
               'credit_limit': f'{rng.randint(1, 9999)}.{rng.randint(0, 99):02d}',
               'notes': rng.choice(['', 'Pays late', 'Cash only']), 'city': rng.choice(['Ames', 'Boone', ''])}
        typed.append(row)
    for row in typed:
        raw = {k: v for k, v in row.items() if v and k != 'city'}
        if row['city']:
            raw['billing_address'] = {'city': row['city']}
        books.run('customer create', dict(raw, name='T ' + raw['name']))
    header = ['Name', 'Email', 'Phone', 'Credit Limit', 'Notes', 'City']
    text = '\n'.join([','.join(header)] + [','.join([r['name'], r['email'], r['phone'], r['credit_limit'], r['notes'],
                                                     r['city']]) for r in typed]) + '\n'
    run_all(books, books.run('import preview', {'kind': 'customer', 'content': text})['run']['id'])
    volatile = {'id', 'created_at', 'updated_at', 'created_by', 'updated_by', 'created_via', 'updated_via', 'name',
                'full_name', 'version', 'path'}
    for row in typed:
        a = books.run('customer show', {'customer': 'T ' + row['name']})
        b = books.run('customer show', {'customer': row['name']})
        assert _clean(a, volatile) == _clean(b, volatile), row
    # The same refusal as typing: a phone longer than the field allows.
    with pytest.raises(BookflowError) as typed_error:
        books.run('customer create', {'name': 'Long', 'phone': '9' * 80})
    checked = books.run('import check', {'kind': 'customer', 'content': 'Name,Phone\nLong,' + '9' * 80 + '\n'})
    assert [(g['code'], g['column'], g['rows']) for g in checked['problems']] == [(typed_error.value.code, 'Phone', [1])]
    preview = books.run('import preview', {'kind': 'customer', 'content': 'Name,Phone\nLong,' + '9' * 80 + '\n',
                                           'partial': True})
    assert preview['refused'][0]['code'] == typed_error.value.code and preview['refused'][0]['column'] == 'Phone'
    # A caller who may import but not read or make customers is refused the preview itself (its rows would show
    # which customers exist and how they differ), as typing is refused the create.
    from tests.conftest import as_user, make_actor
    make_actor(Path(books.root), 'clerk')
    books.client.run('membership grant', {'user': 'clerk', 'company': books.company, 'role': 'standard',
                                          'expected_version': 0, 'denies': ['customer']})
    clerk = as_user(Path(books.root), 'clerk')
    with pytest.raises(BookflowError) as direct:
        clerk.run('customer create', {'name': 'Clerk Co'}, company=books.company)
    with pytest.raises(BookflowError) as shown:
        clerk.run('import preview', {'kind': 'customer', 'content': 'Name\nClerk Co\n'}, company=books.company)
    assert direct.value.code == shown.value.code == 'E_PERMISSION'
    # Someone who cannot import at all is refused the import itself.
    make_actor(Path(books.root), 'viewer')
    books.client.run('membership grant', {'user': 'viewer', 'company': books.company, 'role': 'readonly',
                                          'expected_version': 0})
    viewer = as_user(Path(books.root), 'viewer')
    with pytest.raises(BookflowError) as caught:
        viewer.run('import preview', {'kind': 'customer', 'content': 'Name\nX\n'}, company=books.company)
    assert caught.value.code == 'E_PERMISSION'
    assert viewer.run('import query', {}, company=books.company)['items']  # reads are member


def _clean(value, volatile):
    if isinstance(value, dict):
        return {k: _clean(v, volatile) for k, v in value.items() if k not in volatile and not k.endswith('_id')}
    if isinstance(value, list):
        return [_clean(v, volatile) for v in value]
    return value.replace('T Party ', 'Party ') if isinstance(value, str) else value


def test_other_list_kinds_update_mode_and_its_reversal(books):
    terms = books.run('import preview', {'kind': 'term', 'content': 'Terms,Net due in days\nNet 45,45\nNet 7,7\n',
                                         'constants': {'kind': 'standard'}})
    assert terms['counts'] == {'create': 2}
    run_all(books, terms['run']['id'])
    assert books.run('term show', {'term': 'Net 45'})['due_days'] == 45
    accounts = books.run('import preview', {'kind': 'account', 'content': (
        'Account Number,Account Name,Account Type\n6810,Postage,Expense\n6820,Freight Out,expense\n')})
    assert accounts['mapping']['key'] == ['number'] and accounts['counts'] == {'create': 2}
    run_all(books, accounts['run']['id'])
    items = books.run('import preview', {'kind': 'item', 'content': (
        'Item,Type,Description,Sales Price,Income Account\nService call,Service,Call out,"$1,250.00",Sales Income\n'
        'Part A,non-inventory part,A part,12.125,Service Income\nPart B,service,,1,Postage\n'), 'partial': True})
    assert items['counts'] == {'create': 2, 'refused': 1}, items['refused']
    assert items['refused'][0]['message'] == ('Invalid input (income_account_id: must reference an account of type '
                                              'income, other_income).')
    assert items['refused'][0]['column'] == 'Income Account'
    run_all(books, items['run']['id'])
    assert books.run('item show', {'item': 'Part A'})['price']['amount'] == '12.125'
    # Update mode changes only matches, merging as a typed update does; its reversal puts the prior values back.
    before = books.run('customer create', {'name': 'Acme', 'phone': '555-0100', 'email': 'old@acme.test'})
    update = books.run('import preview', {'kind': 'customer', 'mode': 'update', 'allow': ['skipped'], 'content': (
        'Name,Phone\nAcme,555-0199\nNobody,1\n')})
    assert update['counts'] == {'update': 1, 'skipped': 1}
    run_all(books, update['run']['id'])
    after = books.run('customer show', {'customer': 'Acme'})
    assert after['phone'] == '555-0199' and after['email'] == 'old@acme.test'
    books.run('import reverse', {'run': update['run']['id']})
    restored = books.run('customer show', {'customer': 'Acme'})
    assert (restored['phone'], restored['email'], restored['active']) == ('555-0100', 'old@acme.test', True)
    assert restored['version'] == before['version'] + 2


def test_every_company_write_is_a_kind_planned_or_not_importable():
    registry.load_all()
    writes = {c.name for c in registry.all_commands()
              if c.scope == 'company' and c.is_write and set(c.writes) == {'company'}}
    kinds = {k.create for k in import_kinds.KINDS.values()} | {k.update for k in import_kinds.KINDS.values()}
    listed = {name for names in import_kinds.NOT_IMPORTABLE.values() for name in names}
    assert set(import_kinds.NOT_IMPORTABLE) <= set(import_kinds.REASONS)
    assert not (kinds & listed) and not (import_kinds.PLANNED & listed) and not (kinds & import_kinds.PLANNED)
    assert writes - kinds - import_kinds.PLANNED - listed == set(), 'a new company write needs a kind or a reason'
    assert (kinds | import_kinds.PLANNED | listed) - writes == set(), 'a listed command no longer exists'
    # No kind reaches a delete, void, merge or restore.
    for k in import_kinds.LIST_KINDS.values():
        assert k.create.split()[-1] == 'create' and k.update.split()[-1] == 'update'
    # A document kind posts or creates its document, and is never updated by an import.
    for k in import_kinds.DOC_KINDS.values():
        assert k.create.split()[-1] in ('post', 'create', 'receive', 'pay', 'adjust') and k.update == k.create
