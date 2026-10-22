"""R138: find any transaction -- one search across every document family, the anchor's Edit > Find.

Over the demo books: a name (with its jobs, and with names merged into it), a number (a check's
own number too), an exact amount or a range on the total, a date range, memo text and an
account, in any mix; voided documents found and marked, deleted ones not; a family the caller
may not read left out and named; pages that join up; and the same answer on all four surfaces.
"""
import json
import re

import anyio
import pytest

import bookflow
from bookflow import BookflowError
from bookflow.company import transaction_find
from tests.test_service_sales_lifecycle import COMPANY

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('transaction find',))


def _find(client, **data):
    return client.run('transaction find', data, company=COMPANY)


def _refused(client, **data):
    with pytest.raises(BookflowError) as raised:
        _find(client, **data)
    return raised.value.to_dict()


def test_every_stored_document_type_is_searched():
    # A new transaction type must be given a family (a label and the read that opens it) to ship.
    assert transaction_find.uncovered_types() == set()


def test_find_by_name_number_amount_date_memo_and_account(client):
    every = _find(client, limit=200)
    rows = every['items']
    assert every['matching_total'] == len(rows) > 90 and every['next_cursor'] is None and every['withheld_types'] == []
    assert [(r['date'], r['number']) for r in rows] == sorted((r['date'], r['number']) for r in rows)
    found_types = {r['type'] for r in rows}
    assert {'invoice', 'sales_receipt', 'credit_memo', 'payment', 'deposit', 'customer_refund', 'estimate', 'proposal',
            'work_order', 'purchase_order', 'item_receipt', 'bill', 'bill_payment', 'vendor_credit', 'check', 'card_charge',
            'transfer', 'journal_entry', 'sales_tax_adjustment', 'inventory_adjustment'} <= found_types
    for row in rows:
        assert row['show_command'].endswith(' show')
        shown = client.run(row['show_command'], {transaction_find_identifier(row): row['id']}, company=COMPANY)
        assert row['id'] in json.dumps(shown, default=str), row

    # "the $184.00 check to Regional": part of a name, the type and the exact amount.
    check = _find(client, types=['check'], name='Regional', amount='$184.00')
    assert check['matching_total'] == 1 and check['names'][0]['name'] == 'Regional Parts'
    (row,) = check['items']
    assert (row['type'], row['name'], row['amount']['amount'], row['status']) == ('check', 'Regional Parts', '184.00', 'posted')
    assert row['account'] == 'Payment Example Bank' and row['account_id']
    # Every document naming the vendor, whatever its type.
    regional = _find(client, name='Regional Parts')
    assert {r['type'] for r in regional['items']} == {'check', 'card_charge'} and regional['matching_total'] == 4

    # A number is the document's or the check's own number; letter case is ignored.
    assert [r['number'] for r in _find(client, number='demo-kit-check')['items']] == ['DEMO-KIT-CHECK']
    assert row['number'] in {r['number'] for r in _find(client, number=row['number'])['items']}

    ranged = _find(client, amount_from='100', amount_to='200', date_from='2026-05-01', date_to='2026-06-30')
    assert ranged['items'] and all(10000 <= r['amount']['minor_units'] <= 20000 and '2026-05-01' <= r['date'] <= '2026-06-30'
                                   for r in ranged['items'])
    kit = _find(client, memo='KIT')
    assert kit['items'] and all('kit' in (r['memo'] or '').lower() or r['type'] in ('item_receipt', 'bill', 'invoice')
                                for r in kit['items'])

    # An account: only documents posting to it, so the non-posting ones drop out.
    on_ar = _find(client, account='Accounts Receivable', limit=200)['items']
    assert on_ar and not {r['type'] for r in on_ar} & {'estimate', 'proposal', 'work_order', 'purchase_order', 'check'}

    # Voided documents are found and marked; include_voided false leaves them out.
    voided = [r for r in rows if r['status'] == 'void']
    assert voided and {r['state'] for r in voided} == {'voided'}
    kept = _find(client, include_voided=False, limit=200)
    assert kept['matching_total'] == len(rows) - len(voided)
    # Status reads what is still owed on an invoice or bill.
    statuses = {(r['type'], r['number']): r['status'] for r in rows}
    assert statuses[('invoice', 'DEMO-PAY-INV-B')] == 'paid' and statuses[('invoice', 'DEMO-PAY-INV-A')] == 'open'
    assert statuses[('bill', 'DEMO-BUY-BILL-1')] == 'paid' and statuses[('purchase_order', 'DEMO-KIT-PO')] == 'open'


def transaction_find_identifier(row):
    from bookflow.core import registry
    return registry.get(row['show_command']).positional[0]


def test_a_customer_brings_its_jobs_and_a_merged_name_reads_as_its_survivor(client):
    family = _find(client, name='Payment Example Customer', types=['invoice'])
    assert {r['name'] for r in family['items']} >= {'Payment Example Customer', 'Payment Example Customer:Job A',
                                                     'Payment Example Customer:Job B'}
    before = {r['id'] for r in _find(client, name='Summit Pipe Contracting')['items']}
    assert before
    client.run('vendor merge', {'merged': 'Summit Pipe Contracting', 'into': 'Lakeview Pipe Supply'},
               company=COMPANY, reason='same vendor twice')
    survivor = _find(client, name='Lakeview Pipe Supply', limit=200)
    assert before <= {r['id'] for r in survivor['items']}
    assert {r['name'] for r in survivor['items']} == {'Lakeview Pipe Supply'}
    merged = _find(client, name='Summit Pipe Contracting', limit=200)
    assert {r['id'] for r in merged['items']} == {r['id'] for r in survivor['items']}


def test_pages_join_up_and_refusals_say_what_to_fix(client):
    whole = _find(client, limit=200)['items']
    pages, cursor = [], None
    while True:
        page = _find(client, limit=17, **({'cursor': cursor} if cursor else {}))
        pages += page['items']
        cursor = page['next_cursor']
        if not cursor:
            break
    assert pages == whole
    first = _find(client, limit=5)
    client.run('vendor merge', {'merged': 'Summit Pipe Contracting', 'into': 'Lakeview Pipe Supply'},
               company=COMPANY, reason='same vendor twice')
    assert _refused(client, limit=5, cursor=first['next_cursor'])['code'] == 'E_QUERY_STALE'
    assert _refused(client, limit=6, cursor=first['next_cursor'])['code'] == 'E_VALIDATION'
    assert _refused(client, name='Nobody At All')['code'] == 'E_RECORD_NOT_FOUND'
    assert _refused(client, amount='twelve')['code'] == 'E_VALIDATION'
    assert _refused(client, amount='1.00', amount_from='0.50')['code'] == 'E_VALIDATION'
    assert _refused(client, date_from='2026-06-01', date_to='2026-05-01')['code'] == 'E_VALIDATION'
    assert _refused(client, types=['timesheet'])['code'] == 'E_VALIDATION'


def test_a_family_the_caller_may_not_read_is_left_out_and_named(client, monkeypatch):
    from bookflow.hub import access
    real = access.require_resource

    def deny_work(s, capability, role):
        if capability == 'customer-work':
            raise BookflowError('E_PERMISSION', details={'capability': capability})
        return real(s, capability, role)

    monkeypatch.setattr(access, 'require_resource', deny_work)
    found = _find(client, limit=200)
    assert set(found['withheld_types']) == {'estimate', 'proposal', 'work_order'}
    assert 'estimates' in found['note'] and not {r['type'] for r in found['items']} & {'estimate', 'proposal', 'work_order'}
    asked = _find(client, types=['estimate', 'invoice'])
    assert asked['withheld_types'] == ['estimate'] and {r['type'] for r in asked['items']} == {'invoice'}


@pytest.mark.timeout(900)
def test_the_same_find_through_python_cli_http_and_mcp(root, tmp_path):
    from tests.mcp_matrix_support import Matrix
    query = dict(types=['check'], name='Regional', amount='184.00')

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                results[surface] = (await matrix.call(surface, 'transaction find', query),
                                    await matrix.call(surface, 'transaction find', dict(name='Nobody At All'), rejected=True))
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    for surface, (found, refused) in results.items():
        assert found['matching_total'] == 1 and found['items'][0]['amount']['amount'] == '184.00', surface
        assert refused['code'] == 'E_RECORD_NOT_FOUND', surface
    assert len({repr(found['items']) for found, _ in results.values()}) == 1


def test_the_find_page_is_in_the_header_and_opens_each_document(root):
    """The workbench page: reached from the header and the menu, the filters as query parameters,
    each row linking to its document's own page, a refusal shown in words."""
    from fastapi.testclient import TestClient
    from bookflow.commands.host_cmds import start_serving
    from bookflow.core.config import os_login
    from bookflow.core.context import client_version
    login, password = os_login(), 'correct-horse-battery'
    library = bookflow.connect(data_root=str(root))
    library.run('user set-password', {'username': login, 'password': password})
    company_id = library.company.list()['items'][0]['company_id']
    del library
    handle = start_serving(root, client_version(), bind='127.0.0.1:0', secure_cookies=False, publish_descriptor=False)
    try:
        browser = TestClient(handle.app)
        assert browser.post('/login', json={'username': login, 'password': password}).status_code == 200
        home = browser.get(f'/c/{company_id}/').text
        assert home.count(f'href="/c/{company_id}/find"') == 2  # the header link and the menu entry
        empty = browser.get(f'/c/{company_id}/find')
        assert empty.status_code == 200 and 'id="find-filters"' in empty.text and 'id="find-lines"' not in empty.text
        page = browser.get(f'/c/{company_id}/find', params=[('name', 'Regional'), ('types', 'check'),
                                                            ('include_voided', '0'), ('include_voided', '1')]).text
        assert '<strong>2</strong> transactions found for Regional Parts' in page
        links = re.findall(rf'href="(/c/{company_id}/check/[0-9A-Z]{{26}})"', page)
        assert len(links) == 2 and 'value="check" checked' in page
        assert browser.get(links[0]).status_code == 200
        paged = browser.get(f'/c/{company_id}/find', params={'limit': '1', 'include_voided': '0'}).text
        assert 'data-status="void"' not in paged
        refused = browser.get(f'/c/{company_id}/find', params={'name': 'Nobody At All'}).text
        assert 'id="find-error"' in refused and 'Nobody At All' in refused
        follow = browser.get(f'/c/{company_id}/find', params={'memo': 'kit'}).text
        assert 'id="find-next"' not in follow and 'data-type="purchase_order"' in follow
    finally:
        handle.stop()
