"""R107 increments 0-1, review fixes: every kind reverses, batches unwind newest first, a reversal restores the exact
prior state, uses without a foreign key block, reads keep each kind's permission, and the reader and error file
round-trip (notes/reviews/r107-inc01-review.md; design/bulk-import.md §8a and "As built: increments 0-1").
"""
import csv
import io
import sqlite3
from pathlib import Path

import pytest

from bookflow import BookflowError
from bookflow.company import batches, import_kinds, tabular
from tests.test_imports import active_customers, books, company_db, rows_file, run_all  # noqa: F401

NOISE = ('version', 'updated_at', 'updated_by', 'updated_via')
# A second field each kind's update changes (the five name-only kinds have none to change).
UPDATES = {'vendor': ('phone', '555 0101'), 'employee': ('phone', '555 0102'), 'other-name': ('phone', '555 0103'),
           'item': ('description', 'Changed'), 'account': ('description', 'Changed'), 'term': ('due_days', '30'),
           'ship-method': ('display_order', '7'), 'sales-rep': ('initials', 'PX'),
           'price-level': ('percent', '-20'), 'customer-message': ('text', 'Thanks again')}


def refusal(call):
    with pytest.raises(BookflowError) as caught:
        call()
    return caught.value


def imported(books, kind, content, **extra):
    preview = books.run('import preview', {'kind': kind, 'content': content, **extra})
    assert not preview['refused'] and not preview['decisions'], (kind, preview['refused'], preview['decisions'])
    out = run_all(books, preview['run']['id'])
    assert out['counts'].get('refused') is None, (kind, out['refused'])
    return preview['run']['id'], out


def shown(books, kind, record_id):
    k = import_kinds.kind(kind)
    out = books.run(f'{k.noun} show', {k.selector: record_id})
    return {key: value for key, value in out.items() if key not in NOISE}


def made_id(books, run_id):
    rows = books.run('import rows', {'run': run_id})['items']
    return next(r['record_id'] for r in rows if r['record_id'])


def test_every_kind_imports_reverses_imports_again_and_an_update_reverses_exactly(books):
    """High 1 (the selector field), Medium 5 (exact prior state) and Low 10 (templates preview clean)."""
    books.run('employee create', {'name': 'Pat Lee'})  # the sales-rep template's employee
    for kind in import_kinds.LIST_KINDS:  # documents: tests/test_import_documents.py
        template = books.run('import template', {'kind': kind})
        content = template['content']
        first, _ = imported(books, kind, content, source=f'{kind} file')
        record_id = made_id(books, first)
        assert books.run('import reverse', {'run': first})['state'] == 'reversed', kind
        again, _ = imported(books, kind, content, source=f'{kind} file')  # the released keys import as new
        assert made_id(books, again) != record_id
        assert books.run('import reverse', {'run': again})['state'] == 'reversed', kind
        if kind not in UPDATES:
            continue
        base, _ = imported(books, kind, content, source=f'{kind} base')
        record_id = made_id(books, base)
        before = shown(books, kind, record_id)
        field, value = UPDATES[kind]
        key = 'number' if kind == 'account' else 'name'
        key_value = template['example'][template['header'].index(key)]
        update, out = imported(books, kind, f'{key},{field}\n{key_value},{value}\n', mode='update',
                               source=f'{kind} update')
        assert out['counts'].get('updated') == 1, (kind, out['counts'])
        assert shown(books, kind, record_id) != before
        assert books.run('import reverse', {'run': update})['state'] == 'reversed', kind
        assert shown(books, kind, record_id) == before, kind


def test_an_update_that_made_a_primary_contact_is_undone_with_it(books):
    """Medium 5: the phone made a primary contact; the reversal removes it, so `customer show` is as before."""
    books.run('customer create', {'name': 'Bare Co'})
    books.run('customer create', {'name': 'Kept Co', 'phone': '111', 'email': 'k@x.test',
                                  'billing_address': {'line1': '1 Main', 'city': 'X'}, 'notes': 'hello'})
    ids = {n: books.run('customer show', {'customer': n})['id'] for n in ('Bare Co', 'Kept Co')}
    before = {n: shown(books, 'customer', i) for n, i in ids.items()}
    run_id, _ = imported(books, 'customer', 'Customer,Phone,Email,City,Notes\nBare Co,555 0100,b@x.test,Y,new\n'
                                            'Kept Co,222,,Z,changed\n', mode='update')
    assert shown(books, 'customer', ids['Bare Co'])['primary_contact'] == 'Bare Co'
    books.run('import reverse', {'run': run_id})
    assert {n: shown(books, 'customer', i) for n, i in ids.items()} == before


def test_batches_unwind_newest_first_and_a_record_touched_twice_reverses_once(books):
    """High 2: expected versions come from the record's newest effect in any batch, reversals included."""
    a, _ = imported(books, 'customer', 'Customer,Phone\nX1,1\nX2,2\n', source='a')
    b, _ = imported(books, 'customer', 'Customer,Phone\nX1,111\n', source='b', mode='update')
    error = refusal(lambda: books.run('import reverse', {'run': a}, dry_run=True))
    b_batch = books.run('import show', {'run': b})['run']['batch']['id']
    assert error.code == 'E_BATCH_BLOCKED'
    assert [(x['code'], x['batch']) for x in error.details['blockers']] == [('later_batch', b_batch)]
    assert books.run('import reverse', {'run': b})['state'] == 'reversed'
    assert books.run('customer show', {'customer': 'X1'})['phone'] == '1'
    assert books.run('import reverse', {'run': a})['state'] == 'reversed'
    assert active_customers(books) == set()
    # One batch touching one record twice: keyed on email, both rows update "Dup".
    books.run('customer create', {'name': 'Dup', 'phone': '0'})
    dup = books.run('customer show', {'customer': 'Dup'})['id']
    before = shown(books, 'customer', dup)
    twice, out = imported(books, 'customer', 'Customer,Email,Phone\nDup,a@x.test,1\nDup,b@x.test,2\n',
                          mode='update', key=['email'], source='twice')
    assert out['counts']['updated'] == 2
    plan = books.run('import reverse', {'run': twice}, dry_run=True)
    assert [s['steps'] for s in plan['steps']] == [['customer update']]
    books.run('import reverse', {'run': twice})
    assert shown(books, 'customer', dup) == before
    # Upsert making a record and then updating it: one record, retired once.
    made, out = imported(books, 'customer', 'Customer,Email,Phone\nNew1,a@x.test,1\nNew1,b@x.test,2\n',
                         mode='upsert', key=['email'], source='upsert')
    assert out['counts'] == {'created': 1, 'updated': 1, 'not_run': 0}
    assert books.run('import reverse', {'run': made})['state'] == 'reversed'
    assert 'New1' not in active_customers(books)


def test_an_atomic_undo_that_is_refused_fails_the_run_and_changes_nothing(books, monkeypatch):
    """High 1: an atomic run on a hyphenated kind rolls back; when its undo is refused the run ends `failed`."""
    books.run('customer-type create', {'name': 'P'})
    text = 'Name,Parent\nOk One,\nChild Of P,P\n'
    preview = books.run('import preview', {'kind': 'customer-type', 'content': text})
    books.run('customer-type deactivate', {'customer_type': 'P'})
    out = books.run('import run', {'run': preview['run']['id']})
    assert out['run']['state'] == 'rolled_back' and out['run']['batch']['state'] == 'reversed'
    books.run('customer-type create', {'name': 'Q'})
    preview = books.run('import preview', {'kind': 'customer-type', 'content': 'Name,Parent\nTwo,\nKid,Q\n',
                                           'source': 'second'})
    books.run('customer-type deactivate', {'customer_type': 'Q'})
    monkeypatch.setattr(batches, '_in_use', lambda s, rt, ids, made, by_batch=None: {i: {'tables': ['notes'], 'rows': [],
                                                                          'documents': []} for i in ids})
    out = books.run('import run', {'run': preview['run']['id']})
    assert out['run']['state'] == 'failed' and out['run']['batch']['state'] == 'live'
    assert 'was refused too' in out['notes'][-1] and 'is in use' in out['notes'][-1]
    with company_db(books) as db:
        assert db.execute("SELECT name, active FROM customer_types WHERE name = 'Two'").fetchall() == [('Two', 1)]
        assert db.execute("SELECT count(*) FROM batches WHERE kind = 'reversal'").fetchone()[0] == 1
    assert refusal(lambda: books.run('import run', {'run': preview['run']['id']})).code == 'E_VALIDATION'
    monkeypatch.undo()
    assert books.run('import reverse', {'run': preview['run']['id']})['state'] == 'reversed'


def test_uses_without_a_foreign_key_block_the_reversal(books):
    """Medium 4: a journal line's name, a memorized invoice, a note and a to-do each keep a customer in use."""
    accounts = {a['type']: a['number'] for a in books.run('account query', {'limit': 200})['items']
                if a.get('number')}
    item = books.run('item create', {'name': 'Svc', 'type': 'service', 'price': '5.00',
                                     'income_account_id': books.run('account show', {'account': 'Service Income'})['id']})
    assert item['id']
    runs = {}
    for name in ('JE Cust', 'Mem Cust', 'Note Cust', 'Free Cust'):
        runs[name], _ = imported(books, 'customer', f'Customer\n{name}\n', source=name)
    je = books.run('customer show', {'customer': 'JE Cust'})['id']
    books.run('journal post', {'date': '2026-10-01', 'lines': [
        {'account': accounts['bank'], 'side': 'debit', 'amount': '10.00'},
        {'account': accounts['income'], 'side': 'credit', 'amount': '10.00', 'name_type': 'customer', 'name_id': je}]})
    books.run('memorized create', {'name': 'M1', 'command': 'invoice post', 'frequency': 'monthly',
                                   'start_date': '2026-11-01', 'payload': {
                                       'customer': 'Mem Cust', 'date': '2026-10-01',
                                       'lines': [{'item': 'Svc', 'quantity': '1', 'unit_price': '5.00'}]}})
    note = books.run('customer show', {'customer': 'Note Cust'})['id']
    books.run('note add', {'body': 'contract terms', 'record_type': 'customer', 'record_id': note})
    books.run('todo create', {'title': 'call', 'link_type': 'customer', 'link': note})
    found = {}
    for name in ('JE Cust', 'Mem Cust', 'Note Cust'):
        error = refusal(lambda: books.run('import reverse', {'run': runs[name]}, dry_run=True))
        found[name] = {row['table'] for b in error.details['blockers'] for row in b['used_by']}
    assert 'document_lines' in found['JE Cust'] or 'posting_lines' in found['JE Cust']
    assert 'memorized_references' in found['Mem Cust']
    assert {'notes', 'todos'} <= found['Note Cust']
    assert books.run('import reverse', {'run': runs['Free Cust']})['state'] == 'reversed'


def test_every_unkeyed_id_column_is_a_declared_reference_or_excluded_with_a_reason(books):
    """Medium 4: a new `*_id` column without a foreign key fails here until it is declared either way."""
    path = Path(books.run('company show')['path']) / 'company.db'
    db = sqlite3.connect(path)
    unkeyed = set()
    for (table,) in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        keyed = {row[3] for row in db.execute(f"PRAGMA foreign_key_list('{table}')")}
        unkeyed |= {(table, col[1]) for col in db.execute(f"PRAGMA table_info('{table}')")
                    if col[1].endswith('_id') and col[1] not in keyed}
    declared = set(batches.SOFT_REFERENCES)
    assert not declared & set(batches.NOT_REFERENCES)
    assert all(reason.strip() for reason in batches.NOT_REFERENCES.values())
    assert unkeyed - declared - set(batches.NOT_REFERENCES) == set()
    assert declared - unkeyed == set()  # every declaration names a real column


def test_reads_keep_each_kinds_own_permission(books):
    """Medium 6: holding `import` reveals nothing the caller could not read directly."""
    from tests.conftest import as_user, make_actor
    books.run('customer create', {'name': 'Secret', 'notes': 'confidential note', 'credit_limit': '999.00'})
    preview = books.run('import preview', {'kind': 'customer', 'mode': 'update',
                                           'content': 'Customer,Notes,Credit Limit\nSecret,changed,1.00\nBad,x,12x\n',
                                           'partial': True})
    run_id = preview['run']['id']
    books.run('import run', {'run': run_id})
    batch = books.run('import show', {'run': run_id})['run']['batch']['id']
    company = books.run('company show')
    added = books.run('attachment add', dict(record_type='company_info', record_id=company['company_id'],
                                             original_filename='private.csv', media_type='text/plain'),
                      input_stream=io.BytesIO(b'Customer,Credit Limit\nSalary Memo,12x\n'))
    make_actor(Path(books.root), 'clerk')
    books.client.run('membership grant', {'user': 'clerk', 'company': books.company, 'role': 'standard',
                                          'expected_version': 0, 'denies': ['customer', 'attachment']})
    clerk = as_user(Path(books.root), 'clerk')

    def as_clerk(name, raw):
        return refusal(lambda: clerk.run(name, raw, company=books.company)).code
    assert as_clerk('customer show', {'customer': 'Secret'}) == 'E_PERMISSION'
    for name, raw in (('batch show', {'batch': batch}), ('import show', {'run': run_id}),
                      ('import errors', {'run': run_id}), ('import rows', {'run': run_id}),
                      ('import reverse', {'run': run_id}),
                      ('import preview', {'kind': 'vendor', 'attachment': added['attachment']['id']})):
        assert as_clerk(name, raw) == 'E_PERMISSION', name
    listed = clerk.run('batch query', {}, company=books.company)['items']
    assert [(b['reversible'], b['first_blocker']['code']) for b in listed] == [(False, 'E_PERMISSION')]
    assert 'Secret' not in str(listed)


def test_the_refused_rows_file_round_trips_to_the_same_values(books):
    """Medium 7: numbers, phones and dates are not prefixed; formula-like text is, and reads back as itself."""
    text = 'Customer,Phone,Credit Limit,Acct No\n=Formula Co,+1 415 555 0100,12x,2026-01-31\n' \
           'Neg Co,-5 555,12y,2026-02-01\n'
    preview = books.run('import preview', {'kind': 'customer', 'content': text, 'source': 'f1', 'partial': True})
    assert preview['counts'] == {'refused': 2}
    books.run('import run', {'run': preview['run']['id']})
    content = books.run('import errors', {'run': preview['run']['id']})['content']
    lines = content.splitlines()
    assert lines[1].startswith("'=Formula Co,+1 415 555 0100,12x,2026-01-31,")
    assert lines[2].startswith('Neg Co,-5 555,12y,2026-02-01,')
    table = tabular.read(content)
    assert [cells[:4] for _, cells in table.rows] == [['=Formula Co', '+1 415 555 0100', '12x', '2026-01-31'],
                                                      ['Neg Co', '-5 555', '12y', '2026-02-01']]
    fixed = content.replace(',12x,', ',12.00,').replace(',12y,', ',5.00,')
    again = books.run('import preview', {'kind': 'customer', 'content': fixed, 'source': 'f1'})
    assert again['counts'] == {'create': 2}
    books.run('import run', {'run': again['run']['id']})
    assert books.run('customer show', {'customer': '=Formula Co'})['phone'] == '+1 415 555 0100'
    assert tabular.write_csv(['A'], [['-5'], ['+1 (415) 555-0100'], ['@SUM(A1)'], ['-1+cmd']]).splitlines() == [
        'A', '-5', '+1 (415) 555-0100', "'@SUM(A1)", "'-1+cmd"]


def test_a_money_cell_that_will_not_parse_refuses_its_row(books):
    """Medium 8: a code or symbol alone is refused with a message; the preview goes on."""
    text = 'Customer,Credit Limit\nA,TBD\nB,$\nC,abc\nD,10.00\n'
    preview = books.run('import preview', {'kind': 'customer', 'content': text, 'partial': True})
    assert preview['counts'] == {'create': 1, 'refused': 3}
    assert {r['code'] for r in preview['refused']} == {'bad_value'}
    assert all('not an amount' in r['message'] for r in preview['refused'])


def test_a_reversal_is_frozen_across_slices_and_never_runs_twice(books, monkeypatch):
    """Low 9: an edit after a slice blocks the rest by name; a second reversal of one batch does not start."""
    run_id, _ = imported(books, 'customer', rows_file(6))
    out = books.run('import reverse', {'run': run_id, 'limit': 2})
    assert (out['state'], out['remaining']) == ('reversing', 4)
    left = sorted(active_customers(books))[0]
    books.run('customer update', {'customer': left, 'phone': '999'})
    error = refusal(lambda: books.run('import reverse', {'run': run_id}))
    assert error.code == 'E_BATCH_BLOCKED' and 'part way (2 of 6 records reversed)' in error.message
    assert error.details['blockers'][0]['code'] == 'edited_again' and left in error.details['blockers'][0]['message']
    assert books.run('import show', {'run': run_id})['run']['batch']['state'] == 'reversing'
    # A second reversal that read the batch while it was still live is refused when it opens.
    other, _ = imported(books, 'customer', rows_file(2, start=50), source='other')
    books.run('import reverse', {'run': other, 'limit': 1})  # another caller began after this one read the batch
    real = batches._batch
    calls = []

    def stale(s, batch_id):
        row = real(s, batch_id)
        calls.append(batch_id)
        return dict(row, state='live', reversed_by=None) if len(calls) == 1 else row
    monkeypatch.setattr(batches, '_batch', stale)
    error = refusal(lambda: books.run('import reverse', {'run': other}))
    monkeypatch.undo()
    assert error.code == 'E_VERSION_CONFLICT' and 'began meanwhile' in error.message
    with company_db(books) as db:
        batch = books.run('import show', {'run': other})['run']['batch']['id']
        assert db.execute('SELECT count(*) FROM batches WHERE reversal_of = ?', (batch,)).fetchone()[0] == 1


def test_a_dry_runs_fingerprint_spares_the_probe_and_later_slices_reprobe_nothing(books, monkeypatch):
    """High 3: the whole plan is probed once."""
    probed = []
    real = batches._probe
    monkeypatch.setattr(batches, '_probe', lambda *a: probed.append(a[3]['record_id']) or real(*a))
    run_id, _ = imported(books, 'customer', rows_file(6))
    plan = books.run('import reverse', {'run': run_id}, dry_run=True)
    assert len(probed) == 6
    out = books.run('import reverse', {'run': run_id, 'limit': 2, 'fingerprint': plan['fingerprint']})
    while out['state'] == 'reversing':
        out = books.run('import reverse', {'run': run_id, 'limit': 2, 'fingerprint': plan['fingerprint']})
    assert out['state'] == 'reversed' and len(probed) == 6
    other, _ = imported(books, 'customer', rows_file(4, start=20), source='other')
    out = books.run('import reverse', {'run': other, 'limit': 2})
    assert len(probed) == 10  # the first slice probes the whole plan
    books.run('import reverse', {'run': other, 'limit': 2})
    assert len(probed) == 10


def test_utf16_without_a_byte_order_mark_is_read_and_a_nul_is_kept_for_the_import():
    """Low 10; since Kabe's rules (2026-10-27) a NUL in a cell no longer refuses the file: the import drops it from text
    and reports it (tests/test_import_rules.py), and a file that is not text at all is refused."""
    for encoding in ('utf-16-le', 'utf-16-be'):
        table = tabular.read('Customer,City\nAcmé,Zürich\n'.encode(encoding))
        assert (table.headers, table.rows[0][1], table.encoding, table.encoding_guessed) == (
            ['Customer', 'City'], ['Acmé', 'Zürich'], encoding, True)
    assert tabular.read(b'Customer\nAc\x00me\n').rows == [(2, ['Ac\x00me'])]
    with pytest.raises(BookflowError) as caught:
        tabular.read(b'\x00\x01\x02\x03\x04' * 100)
    assert caught.value.details['problem'] == 'not_text'


def test_hierarchy_rows_read_the_subtree_by_its_path_range(books):
    """High 3: list_service reads a subtree by an indexed range, not a LIKE scan."""
    books.run('customer create', {'name': 'Root'})
    books.run('customer create', {'name': 'Kid', 'parent_id': books.run('customer show', {'customer': 'Root'})['id']})
    books.run('customer create', {'name': 'Rooted'})
    with company_db(books) as db:
        root = db.execute("SELECT path FROM customers WHERE name = 'Root'").fetchone()[0]
        plan = ' '.join(r[3] for r in db.execute('EXPLAIN QUERY PLAN SELECT * FROM customers WHERE path >= ? AND '
                                                 'path < ?', (root, root[:-1] + '0')))
        assert 'ix_customers_path' in plan
    from bookflow.company import list_service, schema as c
    import sqlalchemy as sa
    engine = sa.create_engine(f"sqlite:///{Path(books.run('company show')['path']) / 'company.db'}")
    with engine.connect() as connection:
        rows = list_service._hierarchy_rows(connection, c.metadata.tables['customers'], {'path': root})
    assert [r['name'] for r in rows] == ['Root', 'Kid']
