"""R107 increment 4: document imports (design/bulk-import.md §2, §8a, §11).

Done when 40 invoices of 3 lines import keyed by number, a wrong-customer document is refused whole, a rerun creates
none and payments land on the right invoices. Tests: each kind against its typed command (criterion 5), a closed
period, duplicate numbers, a look-alike customer refused for every actor, and reversing a document import (deleted
with history preserved, refused once a payment is applied, naming the payment).
"""
import csv
import io
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import bookflow
from bookflow import BookflowError

DELETES = ['transaction.invoice.delete', 'transaction.payment.delete', 'transaction.bill.delete',
           'transaction.journal_entry.delete', 'transaction.check.delete', 'transaction.card_charge.delete',
           'transaction.credit_memo.delete', 'transaction.sales_receipt.delete']


@pytest.fixture
def books(tmp_path, monkeypatch):
    data_root = tmp_path / 'documents'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Documents organization')
    company = client.company.new(legal_name='Documents', home_currency='USD', timezone='UTC',
                                 organization='Documents organization', chart='general')['company_id']
    member = next(r for r in client.run('membership list', {'company': company})['items']
                  if r['scope_type'] == 'company' and r['scope_id'] == company)
    client.run('membership grant', dict(user=member['user_id'], company=company, expected_version=member['version'],
                                        grants=DELETES))

    def run(name, raw=None, **context):
        return client.run(name, raw or {}, company=company, **context)
    for name in ('Acme Plumbing', 'Bolt Electric', 'Cedar Homes'):
        run('customer create', {'name': name})
    run('vendor create', {'name': 'Northside Supply'})
    income = run('account show', {'account': 'Service Income'})['id']
    run('item create', {'name': 'Labor', 'type': 'service', 'price': '85', 'income_account_id': income})
    run('item create', {'name': 'Parts', 'type': 'service', 'price': '12.335', 'income_account_id': income})
    run('item create', {'name': 'Trip charge', 'type': 'service', 'price': '35', 'income_account_id': income})
    return SimpleNamespace(client=client, company=company, run=run, root=data_root)


def text(header, rows):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()


def run_all(books, run_id, **extra):
    while True:
        out = books.run('import run', {'run': run_id, **extra})
        if out['run']['state'] != 'running':
            return out


def db(books):
    return sqlite3.connect(Path(books.run('company show')['path']) / 'company.db')


def balances(books):
    """Debits and credits of every posting, summed as exact integers in Python (never SQL SUM)."""
    with db(books) as conn:
        rows = conn.execute('SELECT debit_minor_units, credit_minor_units FROM posting_lines').fetchall()
    return sum(d or 0 for d, _ in rows), sum(c or 0 for _, c in rows)


def invoice_lines(books, number):
    """(item name, description, net) of each line of an invoice, in order."""
    shown = books.run('invoice show', {'invoice': number})
    names = {}
    for line in shown['revision']['lines']:
        if line['item_id'] not in names:
            names[line['item_id']] = books.run('item show', {'item': line['item_id']})['name']
    return [(names[line['item_id']], line['description'], line['net']['amount'] if isinstance(line['net'], dict)
             else line['net']) for line in sorted(shown['revision']['lines'], key=lambda line: line['position'])]


INVOICE_HEADER = ['Invoice No', 'Customer', 'Date', 'Item', 'Description', 'Qty', 'Rate']
CUSTOMERS = ['Acme Plumbing', 'Bolt Electric', 'Cedar Homes']


def invoices(n=40, start=1, wrong=None):
    rows = []
    for i in range(start, start + n):
        customer = CUSTOMERS[i % 3]
        for j, (item, qty, rate) in enumerate((('Labor', '2', '85.00'), ('Parts', '3', '12.335'),
                                               ('Trip charge', '1', ''))):
            who = 'Bolt Electric' if wrong == i and j == 1 and customer != 'Bolt Electric' else (
                'Acme Plumbing' if wrong == i and j == 1 else customer)
            rows.append([f'INV-{i:04d}', who, f'2026-02-{1 + i % 27:02d}', item, f'{item} line {j + 1}', qty, rate])
    return text(INVOICE_HEADER, rows)


def test_forty_invoices_of_three_lines_import_keyed_by_number_and_a_rerun_creates_none(books):
    content = invoices()
    check = books.run('import check', {'kind': 'invoice', 'content': content})
    assert check['ok'] and check['rows'] == 120 and check['documents'] == 40, check['summary']
    assert check['mapping']['key'] == ['number']
    preview = books.run('import preview', {'kind': 'invoice', 'content': content, 'source': 'old system'},
                        reason='Open invoices from the old system')
    assert preview['counts'] == {'create': 40} and preview['run']['rows'] == 40
    done = run_all(books, preview['run']['id'])
    assert done['counts']['created'] == 40 and done['run']['state'] == 'done'
    shown = books.run('invoice show', {'invoice': 'INV-0007'})
    assert [x[:2] for x in invoice_lines(books, 'INV-0007')] == [
        ('Labor', 'Labor line 1'), ('Parts', 'Parts line 2'), ('Trip charge', 'Trip charge line 3')]
    assert shown['customer_name'] == CUSTOMERS[7 % 3]
    debits, credits = balances(books)
    assert debits == credits
    # A rerun of the same source finds every invoice in the key ledger and creates none.
    again = books.run('import preview', {'kind': 'invoice', 'content': content, 'source': 'old system'})
    assert again['counts'] == {'already_in': 40}
    assert run_all(books, again['run']['id'])['counts'].get('created', 0) == 0
    with db(books) as conn:
        assert conn.execute("SELECT count(*) FROM transactions WHERE type = 'invoice'").fetchone()[0] == 40


def test_a_wrong_customer_document_is_refused_whole(books):
    content = invoices(n=5, wrong=3)
    check = books.run('import check', {'kind': 'invoice', 'content': content})
    conflict = [g for g in check['problems'] if g['code'] == 'header_conflict']
    assert len(conflict) == 1 and conflict[0]['column'] == 'Customer' and conflict[0]['rows'] == [8]
    assert check['documents_with_problems'] == 1
    with pytest.raises(BookflowError) as caught:  # all or nothing: the whole file
        books.run('import preview', {'kind': 'invoice', 'content': content})
    assert caught.value.code == 'E_VALIDATION'
    preview = books.run('import preview', {'kind': 'invoice', 'content': content, 'partial': True})
    assert preview['counts'] == {'create': 4, 'refused': 1}
    assert preview['refused'][0]['code'] == 'header_conflict' and preview['refused'][0]['line'] == 8
    run_all(books, preview['run']['id'])
    with db(books) as conn:  # no line of the refused invoice landed
        assert conn.execute("SELECT count(*) FROM transactions WHERE number = 'INV-0003'").fetchone()[0] == 0
    errors = books.run('import errors', {'run': preview['run']['id']})
    lines = list(csv.reader(io.StringIO(errors['content'].lstrip('﻿'))))
    assert [r[0] for r in lines[1:]] == ['INV-0003'] * 3 and [r[-1] for r in lines[1:]] == ['8', '9', '10']


def test_a_document_continues_on_rows_whose_key_is_blank_and_its_lines_keep_file_order(books):
    content = text(['Num', 'Customer', 'Date', 'Item', 'Amount'],
                   [['A-1', 'Acme Plumbing', '2026-03-01', 'Labor', '100.00'], ['', '', '', 'Parts', '5.00'],
                    ['B-1', 'Bolt Electric', '2026-03-02', 'Labor', '50.00'], ['A-1', '', '', 'Trip charge', '35.00']])
    preview = books.run('import preview', {'kind': 'invoice', 'content': content})
    assert preview['counts'] == {'create': 2}
    run_all(books, preview['run']['id'])
    assert [x[0] for x in invoice_lines(books, 'A-1')] == ['Labor', 'Parts', 'Trip charge']
    assert books.run('invoice show', {'invoice': 'A-1'})['total']['amount'] == '140.00'


def test_payments_land_on_the_right_invoices_by_number(books):
    run_all(books, books.run('import preview', {'kind': 'invoice', 'content': invoices(n=6)})['run']['id'])
    totals = {f'INV-{i:04d}': books.run('invoice show', {'invoice': f'INV-{i:04d}'})['total']['amount']
              for i in range(1, 7)}
    header = ['Customer', 'Date', 'Amount', 'Reference', 'Method', 'Deposit To', 'Invoice No', 'Applied']
    first, fourth = totals['INV-0001'], totals['INV-0004']
    both = f"{float(first) + 50:.2f}"
    content = text(header, [
        [CUSTOMERS[1], '2026-03-05', both, '1001', 'Check', 'Undeposited Funds', 'INV-0001', first],
        [CUSTOMERS[1], '2026-03-05', both, '1001', 'Check', 'Undeposited Funds', 'INV-0004', '50.00'],
        [CUSTOMERS[2], '2026-03-06', '20.00', '1002', 'Cash', 'Undeposited Funds', 'INV-0002', '20.00']])
    preview = books.run('import preview', {'kind': 'payment', 'content': content})
    assert preview['counts'] == {'create': 2}
    run_all(books, preview['run']['id'])
    due = {n: books.run('invoice show', {'invoice': n})['settlement_current']['due_minor_units'] for n in totals}
    assert due['INV-0001'] == 0
    assert due['INV-0004'] == round(float(fourth) * 100) - 5000
    assert due['INV-0002'] == round(float(totals['INV-0002']) * 100) - 2000
    assert all(due[n] == round(float(totals[n]) * 100) for n in ('INV-0003', 'INV-0005', 'INV-0006'))
    # Another customer's invoice, or one that does not exist, is refused in the check.
    bad = text(header, [[CUSTOMERS[0], '2026-03-07', '5.00', '1003', 'Cash', 'Undeposited Funds', 'INV-0005', ''],
                        [CUSTOMERS[0], '2026-03-07', '6.00', '1004', 'Cash', 'Undeposited Funds', 'INV-9999', '']])
    check = books.run('import check', {'kind': 'payment', 'content': bad})
    assert {(g['code'], tuple(g['rows'])) for g in check['problems']} == {
        ('E_VALIDATION', (1,)), ('unknown_reference', (2,))}, check['summary']


# ------------------------------------------------------------------ criterion 5: each kind against its typed command

VOLATILE = ('id', 'number', 'version', 'reference', 'supplier_reference', 'check_number', 'operation_key', 'memo',
            'status', 'settlement_current', 'settlement', 'title', 'position', 'settlement_guard')


def same(value):
    """A shown document with what differs between two equal documents left out: ids, times, who, numbers."""
    if isinstance(value, dict):
        return {k: same(v) for k, v in value.items()
                if k not in VOLATILE and not k.endswith(('_id', '_at', '_by', '_via', '_ids', 'fingerprint'))
                and 'number' not in k and 'revision' not in k}
    if isinstance(value, list):
        return [same(v) for v in value]
    return value


def _inventory(books):
    ids = {n: books.run('account show', {'account': n})['id'] for n in ('Inventory Asset', 'Cost of Goods Sold',
                                                                        'Sales Income')}
    books.run('item create', {'name': 'Widget', 'type': 'inventory_part', 'price': '20', 'cost': '8',
                              'description': 'Widget', 'purchase_description': 'Widget',
                              'asset_account_id': ids['Inventory Asset'], 'cogs_account_id': ids['Cost of Goods Sold'],
                              'income_account_id': ids['Sales Income']})


def _card(books):
    books.run('account create', {'name': 'Company Card', 'type': 'credit_card'})


def _bills(books):
    for ref in ('V-1', 'V-2'):
        books.run('bill post', {'vendor': 'Northside Supply', 'date': '2026-04-01', 'number': ref,
                                'expenses': [{'account': 'Utilities', 'amount': '84.20'}]})


def _invoices(books):
    for n in ('P-1', 'P-2'):
        books.run('invoice post', {'customer': 'Acme Plumbing', 'date': '2026-04-01', 'number': n,
                                   'lines': [{'item': 'Labor', 'quantity': '1'}]})


SALE = ['Num', 'Customer', 'Date', 'Item', 'Qty', 'Rate', 'Description']
SALE_ROWS = [['{n}', 'Acme Plumbing', '2026-04-02', 'Labor', '2', '85.00', 'Callout'],
             ['{n}', 'Acme Plumbing', '2026-04-02', 'Parts', '3', '12.335', 'Washers']]
SALE_LINES = [{'item': 'Labor', 'quantity': '2', 'unit_price': '85.00', 'description': 'Callout'},
              {'item': 'Parts', 'quantity': '3', 'unit_price': '12.335', 'description': 'Washers'}]
KINDS = {
    # kind: (setup, header, rows ({n} the import's number or reference), typed input (its own number), show)
    'invoice': (None, SALE, SALE_ROWS, {'customer': 'Acme Plumbing', 'date': '2026-04-02', 'lines': SALE_LINES}),
    'credit-memo': (None, SALE, SALE_ROWS, {'customer': 'Acme Plumbing', 'date': '2026-04-02', 'lines': SALE_LINES}),
    'sales-receipt': (None, SALE + ['Deposit To', 'Payment Method'],
                      [r + ['Undeposited Funds', 'Cash'] for r in SALE_ROWS],
                      {'customer': 'Acme Plumbing', 'date': '2026-04-02', 'lines': SALE_LINES,
                       'deposit_to': 'Undeposited Funds', 'payment_method': 'Cash'}),
    'estimate': (None, SALE + ['Title'], [r + ['Kitchen'] for r in SALE_ROWS],
                 {'customer': 'Acme Plumbing', 'date': '2026-04-02', 'lines': SALE_LINES, 'title': 'Kitchen'}),
    'sales-order': (None, SALE, SALE_ROWS, {'customer': 'Acme Plumbing', 'date': '2026-04-02', 'lines': SALE_LINES}),
    'bill': (None, ['Vendor', 'Ref No', 'Date', 'Account', 'Amount', 'Line Memo'],
             [['Northside Supply', '{n}', '2026-04-03', 'Utilities', '84.20', 'March'],
              ['Northside Supply', '{n}', '2026-04-03', 'Office Supplies', '12.05', 'Paper']],
             {'vendor': 'Northside Supply', 'date': '2026-04-03',
              'expenses': [{'account': 'Utilities', 'amount': '84.20', 'memo': 'March'},
                           {'account': 'Office Supplies', 'amount': '12.05', 'memo': 'Paper'}]}),
    'vendor-credit': (None, ['Vendor', 'Ref No', 'Date', 'Account', 'Amount'],
                      [['Northside Supply', '{n}', '2026-04-03', 'Utilities', '10.00']],
                      {'vendor': 'Northside Supply', 'date': '2026-04-03',
                       'expenses': [{'account': 'Utilities', 'amount': '10.00'}]}),
    'purchase-order': (_inventory, ['PO No', 'Vendor', 'Date', 'Item', 'Qty', 'Cost'],
                       [['{n}', 'Northside Supply', '2026-04-03', 'Widget', '10', '8.00']],
                       {'vendor': 'Northside Supply', 'date': '2026-04-03',
                        'lines': [{'item': 'Widget', 'quantity': '10', 'rate': '8.00'}]}),
    'journal': (None, ['Num', 'Date', 'Account', 'Side', 'Amount', 'Description'],
                [['{n}', '2026-04-04', 'Rent', 'debit', '1200.00', 'April'],
                 ['{n}', '2026-04-04', 'Checking', 'credit', '1200.00', 'April']],
                {'date': '2026-04-04', 'lines': [
                    {'account': 'Rent', 'side': 'debit', 'amount': '1200.00', 'description': 'April'},
                    {'account': 'Checking', 'side': 'credit', 'amount': '1200.00', 'description': 'April'}]}),
    'check': (None, ['Bank Account', 'Check No', 'Date', 'Amount', 'Expense Account', 'Line Amount'],
              [['Checking', '{n}', '2026-04-05', '96.25', 'Utilities', '84.20'],
               ['Checking', '{n}', '2026-04-05', '', 'Office Supplies', '12.05']],
              {'account': 'Checking', 'date': '2026-04-05', 'amount': '96.25',
               'expenses': [{'account': 'Utilities', 'amount': '84.20'},
                            {'account': 'Office Supplies', 'amount': '12.05'}]}),
    'card-charge': (_card, ['Card', 'Date', 'Amount', 'Expense Account', 'Line Amount', 'Memo'],
                    [['Company Card', '2026-04-05', '45.10', 'Office Supplies', '45.10', '{n}']],
                    {'account': 'Company Card', 'date': '2026-04-05', 'amount': '45.10',
                     'expenses': [{'account': 'Office Supplies', 'amount': '45.10'}]}),
    'payment': (_invoices, ['Customer', 'Date', 'Amount', 'Reference', 'Method', 'Deposit To', 'Invoice No'],
                [['Acme Plumbing', '2026-04-06', '85.00', '{n}', 'Check', 'Undeposited Funds', 'P-2']],
                {'customer': 'Acme Plumbing', 'date': '2026-04-06', 'amount': '85.00', 'payment_method': 'Check',
                 'deposit_to': 'Undeposited Funds', 'operation_key': 'typed-1',
                 'applications': {'mode': 'inline', 'items': [{'invoice': 'P-1', 'expected_version': 1,
                                                               'amount': '85.00'}]}}),
    'bill-payment': (_bills, ['Date', 'Account', 'Method', 'Check No', 'Bill', 'Amount'],
                     [['2026-04-07', 'Checking', 'check', '{n}', 'V-2', '84.20']],
                     {'date': '2026-04-07', 'funding_account': 'Checking', 'method': 'check',
                      'bills': [{'bill': 'V-1', 'amount': '84.20'}]}),
    'inventory-adjustment': (_inventory, ['Ref No', 'Date', 'Adjustment Account', 'Item', 'Qty Change',
                                          'Value Change'],
                             [['{n}', '2026-04-08', 'Cost of Goods Sold', 'Widget', '5', '40.00']],
                             {'date': '2026-04-08', 'adjustment_account': 'Cost of Goods Sold',
                              'lines': [{'item': 'Widget', 'quantity_change': '5', 'value_change': '40.00'}]}),
}
SHOW = {'credit-memo': ('credit-memo show', 'credit_memo'), 'sales-receipt': ('sales-receipt show', 'sales_receipt'),
        'sales-order': ('sales-order show', 'sales_order'), 'vendor-credit': ('vendor-credit show', 'credit'),
        'purchase-order': ('purchase-order show', 'purchase_order'), 'card-charge': ('card-charge show', 'card_charge'),
        'bill-payment': ('bill payment show', 'payment'), 'inventory-adjustment': ('inventory show', 'adjustment')}


@pytest.mark.parametrize('kind', sorted(KINDS))
def test_each_kind_imports_the_same_document_as_typing_it(books, kind):
    setup, header, rows, typed = KINDS[kind]
    if setup is not None:
        setup(books)
    content = text(header, [[cell.replace('{n}', 'IMP-1') for cell in row] for row in rows])
    check = books.run('import check', {'kind': kind, 'content': content, 'key_by_content': True})
    assert check['ok'], (check['summary'], check['to_decide'])
    preview = books.run('import preview', {'kind': kind, 'content': content, 'key_by_content': True})
    assert preview['counts'] == {'create': 1}, preview['refused']
    done = run_all(books, preview['run']['id'], key_by_content=True)
    assert done['counts']['created'] == 1, done['refused']
    imported_id = books.run('import rows', {'run': preview['run']['id'], 'phase': 'run'})['items'][0]['record_id']
    made = books.run(kinds_command(kind), typed)
    command, selector = SHOW.get(kind, (f'{kind} show', kind))
    imported = books.run(command, {selector: imported_id})
    typed_shown = books.run(command, {selector: made.get('id') or made['payments'][0]['id']})
    assert same(imported) == same(typed_shown)
    debits, credits = balances(books)
    assert debits == credits


def kinds_command(kind):
    from bookflow.company import import_kinds
    return import_kinds.kind(kind).create


# ------------------------------------------------------------------ refusals, rounding, look-alikes, the key guard

def test_a_closed_period_and_a_used_number_refuse_as_typing_does(books):
    books.client.company.update(company=books.company, closing_date='2026-01-31', expected_version=None,
                                reason='Close January')
    early = text(INVOICE_HEADER, [['C-1', 'Acme Plumbing', '2026-01-15', 'Labor', '', '1', '']])
    with pytest.raises(BookflowError) as typed:
        books.run('invoice post', {'customer': 'Acme Plumbing', 'date': '2026-01-15',
                                   'lines': [{'item': 'Labor', 'quantity': '1'}]})
    preview = books.run('import preview', {'kind': 'invoice', 'content': early, 'partial': True})
    assert preview['counts'] == {'refused': 1} and preview['refused'][0]['code'] == typed.value.code
    books.run('invoice post', {'customer': 'Acme Plumbing', 'date': '2026-02-15', 'number': 'D-1',
                               'lines': [{'item': 'Labor', 'quantity': '1'}]})
    with pytest.raises(BookflowError) as again:
        books.run('invoice post', {'customer': 'Acme Plumbing', 'date': '2026-02-15', 'number': 'D-1',
                                   'lines': [{'item': 'Labor', 'quantity': '1'}]})
    used = text(INVOICE_HEADER, [['D-1', 'Acme Plumbing', '2026-02-15', 'Labor', '', '1', '']])
    check = books.run('import check', {'kind': 'invoice', 'content': used})
    assert [(g['code'], g['column']) for g in check['problems']] == [(again.value.code, 'Invoice No')]


def test_an_imported_invoice_rounds_as_the_typed_one(books):
    lines = [('Parts', '3', '12.335'), ('Parts', '0.5', '0.005'), ('Labor', '1.25', '85.555')]
    content = text(INVOICE_HEADER, [['R-1', 'Acme Plumbing', '2026-02-20', i, '', q, r] for i, q, r in lines])
    run_all(books, books.run('import preview', {'kind': 'invoice', 'content': content, 'decimal': '.'})['run']['id'])
    books.run('invoice post', {'customer': 'Acme Plumbing', 'date': '2026-02-20', 'number': 'R-2', 'lines': [
        {'item': i, 'quantity': q, 'unit_price': r} for i, q, r in lines]})
    imported, typed = (books.run('invoice show', {'invoice': n}) for n in ('R-1', 'R-2'))
    assert imported['total'] == typed['total'] and invoice_lines(books, 'R-1') == invoice_lines(books, 'R-2')


def test_a_look_alike_customer_is_never_created_and_a_new_one_only_on_request(books):
    content = text(INVOICE_HEADER, [['L-1', 'Acme Plumbing Inc', '2026-02-21', 'Labor', '', '1', ''],
                                    ['L-2', 'Dune Roofing', '2026-02-21', 'Labor', '', '1', '']])
    check = books.run('import check', {'kind': 'invoice', 'content': content})
    unknown = {u['value']: u for u in check['unknown_references']}
    assert unknown['Acme Plumbing Inc']['look_alikes'] == ['Acme Plumbing'] and not check['ok']
    # Named, not made: the run waits on both.
    plain = books.run('import preview', {'kind': 'invoice', 'content': content})
    assert plain['run']['state'] == 'waiting' and plain['counts'] == {'waiting': 2}
    # create_missing makes the new one, never the look-alike, for any actor.
    preview = books.run('import preview', {'kind': 'invoice', 'content': content, 'create_missing': True})
    assert preview['run']['state'] == 'waiting'
    assert any("'Acme Plumbing Inc' was not created" in n for n in preview['notes'])
    with pytest.raises(BookflowError) as caught:
        books.run('import decide', {'run': preview['run']['id'], 'create': ['Acme Plumbing Inc']})
    assert caught.value.details['look_alikes'] == ['Acme Plumbing']
    books.run('import decide', {'run': preview['run']['id'], 'map': ['Acme Plumbing Inc=Acme Plumbing']})
    run_all(books, preview['run']['id'])
    assert books.run('invoice show', {'invoice': 'L-1'})['customer_name'] == 'Acme Plumbing'
    assert books.run('invoice show', {'invoice': 'L-2'})['customer_name'] == 'Dune Roofing'
    # An item is never created: it is mapped or its rows are skipped.
    items = text(INVOICE_HEADER, [['L-3', 'Acme Plumbing', '2026-02-21', 'Mulch', '', '1', '5']])
    waiting = books.run('import preview', {'kind': 'invoice', 'content': items, 'create_missing': True})
    with pytest.raises(BookflowError):
        books.run('import decide', {'run': waiting['run']['id'], 'create': ['Mulch']})


def test_an_unkeyed_document_file_runs_only_with_key_by_content(books):
    books.run('account create', {'name': 'Company Card', 'type': 'credit_card'})
    content = text(['Card', 'Date', 'Amount', 'Expense Account', 'Line Amount'],
                   [['Company Card', '2026-02-22', '9.99', 'Office Supplies', '9.99'],
                    ['Company Card', '2026-02-22', '9.99', 'Office Supplies', '9.99']])
    preview = books.run('import preview', {'kind': 'card-charge', 'content': content})
    assert preview['mapping']['keyed_by_content'] and preview['counts'] == {'create': 2}
    with pytest.raises(BookflowError) as caught:
        books.run('import run', {'run': preview['run']['id']})
    assert caught.value.details['field'] == 'key_by_content'
    assert run_all(books, preview['run']['id'], key_by_content=True)['counts']['created'] == 2
    # Two equal rows are two charges (occurrences), and the same file again makes none.
    again = books.run('import preview', {'kind': 'card-charge', 'content': content, 'key_by_content': True})
    assert again['counts'] == {'already_in': 2}


def test_a_caller_without_the_document_permission_is_refused_as_typing_is(books):
    from tests.conftest import as_user, make_actor
    make_actor(Path(books.root), 'clerk')
    books.client.run('membership grant', {'user': 'clerk', 'company': books.company, 'role': 'standard',
                                          'expected_version': 0, 'denies': ['ledger.post']})
    clerk = as_user(Path(books.root), 'clerk')
    with pytest.raises(BookflowError) as direct:
        clerk.run('invoice post', {'customer': 'Acme Plumbing', 'date': '2026-02-23',
                                   'lines': [{'item': 'Labor', 'quantity': '1'}]}, company=books.company)
    content = text(INVOICE_HEADER, [['Q-1', 'Acme Plumbing', '2026-02-23', 'Labor', '', '1', '']])
    preview = clerk.run('import preview', {'kind': 'invoice', 'content': content, 'partial': True},
                        company=books.company)
    assert preview['refused'][0]['code'] == direct.value.code == 'E_PERMISSION'


# ------------------------------------------------------------------ reversal

def test_reversing_a_document_import_deletes_with_history_and_is_refused_while_a_payment_is_applied(books):
    invoices_run = books.run('import preview', {'kind': 'invoice', 'content': invoices(n=3)})['run']['id']
    run_all(books, invoices_run)
    before = balances(books)
    total = books.run('invoice show', {'invoice': 'INV-0002'})['total']['amount']
    pay = text(['Customer', 'Date', 'Amount', 'Reference', 'Method', 'Deposit To', 'Invoice No'],
               [[CUSTOMERS[2], '2026-03-01', total, '77', 'Check', 'Undeposited Funds', 'INV-0002']])
    pay_run = books.run('import preview', {'kind': 'payment', 'content': pay})['run']['id']
    run_all(books, pay_run)
    with pytest.raises(BookflowError) as caught:
        books.run('import reverse', {'run': invoices_run}, dry_run=True, reason='Wrong file')
    blocker = caught.value.details['blockers'][0]
    assert caught.value.code == 'E_BATCH_BLOCKED' and blocker['code'] == 'in_use'
    assert 'INV-0002' in blocker['message'] and 'payment' in blocker['message']
    # The payment's run reverses first (unapplied, then deleted); then the invoices'.
    assert books.run('import reverse', {'run': pay_run}, reason='Wrong file')['state'] == 'reversed'
    assert books.run('import reverse', {'run': invoices_run}, reason='Wrong file')['state'] == 'reversed'
    for n in ('INV-0001', 'INV-0002', 'INV-0003'):
        assert books.run('invoice show', {'invoice': n, 'include_deleted': True})['status'] == 'deleted'
    debits, credits = balances(books)
    assert debits == credits and debits - before[0] == credits - before[1]
    with db(books) as conn:  # the postings net to nothing: each account's balance is as before the imports
        rows = conn.execute('SELECT account_id, debit_minor_units, credit_minor_units FROM posting_lines').fetchall()
    net = {}
    for account, d, c in rows:
        net[account] = net.get(account, 0) + (d or 0) - (c or 0)
    assert set(net.values()) == {0}
    # The keys are released: the same file previews as new, and says the numbers stay with the deleted invoices.
    check = books.run('import check', {'kind': 'invoice', 'content': invoices(n=3)})
    assert {g['code'] for g in check['problems']} == {'E_DUPLICATE_NUMBER'}
    assert 'deleted document keeps its number' in check['problems'][0]['message']


# ------------------------------------------------------------------ the seeded check, extended to documents

def _state(books):
    """Each account's net (debits less credits) and each invoice's due, all exact integers."""
    with db(books) as conn:
        rows = conn.execute('SELECT account_id, debit_minor_units, credit_minor_units FROM posting_lines').fetchall()
        gone = {r[0] for r in conn.execute('SELECT transaction_id FROM sales_deletions')}
        live = [n for n, i in conn.execute("SELECT number, id FROM transactions WHERE type = 'invoice' "
                                           "AND status != 'voided'") if i not in gone]
    net = {}
    for account, d, c in rows:
        net[account] = net.get(account, 0) + (d or 0) - (c or 0)
    dues = {n: books.run('invoice show', {'invoice': n})['settlement_current']['due_minor_units'] for n in live}
    return {k: v for k, v in net.items() if v}, dues


@pytest.mark.parametrize('seed', (5, 41, 107))
def test_seeded_document_imports_payments_and_reversals_keep_the_invariants(books, seed):
    import random
    rng = random.Random(seed)
    receivable = books.run('account show', {'account': 'Accounts Receivable'})['id']
    batches = []  # {'run', 'kind', 'numbers' or 'applied': {number: minor}, 'live', 'before'}
    files = []  # (source, content, numbers) of invoice files run
    dues = {}  # the model: each live invoice's due
    serial = changes = 0
    totals = {'invoices': 0, 'reruns': 0, 'payments': 0, 'reversed': 0, 'refused': 0, 'exact': 0}
    for step in range(20):
        state = _state(books)
        choice = rng.random()
        live = [b for b in batches if b['live']]
        if choice < 0.35 or not dues:
            rows, numbers = [], []
            for _ in range(rng.randint(1, 4)):
                serial += 1
                number = f'S{seed}-{serial:03d}'
                customer = rng.choice(CUSTOMERS)
                numbers.append(number)
                for _ in range(rng.randint(1, 3)):
                    rows.append([number, customer, '2026-05-01', rng.choice(['Labor', 'Parts', 'Trip charge']), '',
                                 str(rng.randint(1, 4)), rng.choice(['', '9.99', '12.335', '150.00'])])
            content, source = text(INVOICE_HEADER, rows), rng.choice(['shop', 'web'])
            run = books.run('import preview', {'kind': 'invoice', 'content': content, 'source': source,
                                               'decimal': '.'})['run']['id']
            run_all(books, run)
            for number in numbers:
                dues[number] = books.run('invoice show', {'invoice': number})['total_minor_units']
            files.append((source, content, numbers))
            totals['invoices'] += len(numbers)
            changes += 1
            batches.append({'run': run, 'kind': 'invoice', 'numbers': numbers, 'live': True, 'before': state,
                            'at': changes})
        elif choice < 0.5 and files:
            source, content, numbers = rng.choice(files)  # a rerun creates nothing
            preview = books.run('import preview', {'kind': 'invoice', 'content': content, 'source': source,
                                                   'partial': True, 'decimal': '.'})
            assert set(preview['counts']) <= {'already_in', 'refused'}
            assert all(r['code'] == 'E_DUPLICATE_NUMBER' for r in preview['refused'])  # reversed: number kept
            totals['reruns'] += 1
        elif choice < 0.75:
            open_ = [n for n, d in dues.items() if d > 0]
            if not open_:
                continue
            number = rng.choice(open_)
            customer = books.run('invoice show', {'invoice': number})['customer_name']
            minor = rng.randint(1, dues[number])
            amount = f'{minor // 100}.{minor % 100:02d}'
            content = text(['Customer', 'Date', 'Amount', 'Reference', 'Method', 'Deposit To', 'Invoice No'],
                           [[customer, '2026-05-10', amount, f'R{step}', 'Check', 'Undeposited Funds', number]])
            run = books.run('import preview', {'kind': 'payment', 'content': content})['run']['id']
            run_all(books, run)
            dues[number] -= minor
            totals['payments'] += 1
            changes += 1
            batches.append({'run': run, 'kind': 'payment', 'applied': {number: minor}, 'live': True, 'before': state,
                            'at': changes})
        elif live:
            batch = rng.choice(live)
            applied_live = {n for b in batches if b['live'] and b['kind'] == 'payment' for n in b['applied']}
            blocked = batch['kind'] == 'invoice' and set(batch['numbers']) & applied_live
            if blocked:
                with pytest.raises(BookflowError) as caught:
                    books.run('import reverse', {'run': batch['run']}, reason='Seeded')
                assert {b['code'] for b in caught.value.details['blockers']} == {'in_use'}
                assert _state(books) == state  # a refused reversal changes nothing
                totals['refused'] += 1
                continue
            newest = batch['at'] == changes
            changes += 1
            books.run('import reverse', {'run': batch['run']}, reason='Seeded')
            batch['live'] = False
            if batch['kind'] == 'invoice':
                for n in batch['numbers']:
                    dues.pop(n)
            else:
                for n, minor in batch['applied'].items():
                    dues[n] += minor
            totals['reversed'] += 1
            if newest:  # undoing the last change restores the exact state before it
                assert _state(books) == batch['before']
                totals['exact'] += 1
        net, shown = _state(books)
        # Invariants: balanced; payments landed on the right invoices; receivables tie to what is due.
        debits, credits = balances(books)
        assert debits == credits
        assert shown == dues
        assert net.get(receivable, 0) == sum(dues.values())
    print(f'seeded documents {seed}: {totals}')
    assert totals['invoices'] and totals['payments']
