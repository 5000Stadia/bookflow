"""Correcting a customer refund over actual in-process Python, CLI, HTTP and MCP.

`customer-refund update` is the verb a bookkeeper reaches for when the refund was typed for the
wrong amount, and until now nothing drove it over a transport. Every figure is written out here:

    PARITY-RFU-INV   invoice        4 x 25.00   = 100.00
    PARITY-RFU-CM    credit memo                   40.00
    PARITY-RFU-1     refund of the credit          12.00, corrected to 20.00

so Accounts Receivable is 100.00 - 40.00 + 12.00 = 72.00 before the correction and 80.00 after,
the bank falls from 12.00 to 20.00, and income stays at -(100.00 - 40.00) throughout, because a
refund never reverses a sale a second time. Each surface runs the whole thing on its own private
copy of the same seed, and the four documents are compared field by field at the end.

What each real transport has to produce:

**The preview writes nothing.** The database is byte-identical after the dry run.

**The correction reverses its own effect and posts the corrected one.** One replacement batch at
the corrected date, the superseded revision reversed at its own, the number kept, and the ledger
at the corrected figures -- read back through `report general-ledger`, not off the writer.

**A credit is still spent once.** Correcting past what the credit is worth refuses with
`E_CREDIT_UNAVAILABLE`, names what is available, and writes nothing.

**Retries and stale versions behave.** One idempotency key corrects once; a superseded
`expected_version` is refused `E_VERSION_CONFLICT`; a ghost company is refused everywhere.
"""
from copy import deepcopy
from pathlib import Path

import anyio
import pytest

from tests.mcp_matrix_support import Matrix, normalize
from tests.payment_raw_evidence import database


# The commands this test is the designated four-surface witness for; the coverage ledger imports
# this set rather than restating the names beside a path it cannot check.
COMMANDS = frozenset(('customer-refund update',))

INVOICE = 10000     # 100.00, four units at 25.00
CREDIT = 4000       # 40.00 credited back
FIRST = 1200        # 12.00 paid out, the amount typed wrong
CORRECTED = 2000    # 20.00, what should have been paid


@pytest.mark.timeout(600)
def test_correcting_a_refund_crosses_all_four_actual_transports(root, tmp_path):
    pytest.importorskip('mcp')

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            driven = {}
            for surface in matrix.documents:
                calls = {}
                dbpath = next(matrix.roots[surface].rglob('company.db'))

                async def call(name, raw, **ctx):
                    """Drive a command this file is the ledger's witness for, and record it."""
                    if not ctx.get('rejected'):
                        calls[name] = deepcopy(raw)
                    return await matrix.call(surface, name, raw, **ctx)

                async def ledger():
                    """Signed minor units per account, read through the real report command."""
                    page = await matrix.call(surface, 'report general-ledger', {
                        'date_from': '2025-01-01', 'date_to': '2025-12-31', 'limit': 200})
                    net = {}
                    for line in page['rows']:
                        if line['kind'] != 'posting':
                            continue
                        net[line['account_id']] = net.get(line['account_id'], 0) + (
                            line['debit']['minor_units'] - line['credit']['minor_units'])
                    return {account: units for account, units in net.items() if units}

                income = (await matrix.call(surface, 'account create',
                                            dict(name='Parity correction income',
                                                 type='income')))['id']
                bank = (await matrix.call(surface, 'account create',
                                          dict(name='Parity correction bank', type='bank')))['id']
                receivable = next(row['id'] for row in (await matrix.call(
                    surface, 'account query', {'limit': 200}))['items']
                    if row['type'] == 'accounts_receivable')
                exempt = next(row['id'] for row in (await matrix.call(
                    surface, 'sales-tax-code list', {}))['items'] if not row['taxable'])
                item = (await matrix.call(surface, 'item create', dict(
                    name='Parity corrected service', type='service', sales_enabled=True,
                    description='Parity service', income_account_id=income, price='25.00',
                    sales_tax_code_id=exempt)))['id']
                method = next(row['id'] for row in (await matrix.call(
                    surface, 'payment-method list', {}))['items'] if row['kind'] == 'check')
                customer = (await matrix.call(surface, 'customer create',
                                              dict(name='Parity Correction Co')))['id']
                await matrix.call(surface, 'invoice post', dict(
                    customer=customer, date='2025-03-02', due_date='2025-04-01',
                    number='PARITY-RFU-INV',
                    lines=[{'item': item, 'quantity': '4', 'unit_price': '25.00'}]))
                credit = await matrix.call(surface, 'credit-memo post', dict(
                    customer=customer, date='2025-03-10', number='PARITY-RFU-CM',
                    lines=[{'item': item, 'quantity': '1', 'unit_price': '40.00'}]))
                paid = await matrix.call(surface, 'customer-refund post', dict(
                    date='2025-03-20', number='PARITY-RFU-1', funding_account=bank, method=method,
                    check_number='4101', memo='Cheque 4101',
                    sources=[{'credit_memo': credit['id'], 'amount': '12.00'}]))

                assert await ledger() == {receivable: INVOICE - CREDIT + FIRST,
                                          income: -(INVOICE - CREDIT), bank: -FIRST}, surface

                patch = dict(refund=paid['id'], expected_version=paid['version'],
                             date='2025-03-22', memo='Twenty, not twelve',
                             sources=[{'credit_memo': credit['id'], 'amount': '20.00'}])
                before = database(dbpath)
                preview = await call('customer-refund update', patch, dry_run=True)
                assert preview['dry_run'], surface
                assert database(dbpath) == before, surface

                # More than the credit is worth is refused, by name, writing nothing.
                greedy = await call('customer-refund update', {
                    **patch, 'sources': [{'credit_memo': credit['id'], 'amount': '50.00'}]},
                    rejected=True)
                assert greedy['code'] == 'E_CREDIT_UNAVAILABLE', surface
                assert greedy['details']['available']['minor_units'] == CREDIT, surface
                assert database(dbpath) == before, surface

                corrected = await call('customer-refund update', patch,
                                       idempotency_key='refund-correction')
                assert corrected['total_minor_units'] == CORRECTED, surface
                assert corrected['version'] == paid['version'] + 1, surface
                assert corrected['revision']['revision_number'] == 2, surface
                assert corrected['revision']['supersedes_revision_id'] == paid['revision']['id'], surface
                assert corrected['number'] == paid['number'] == 'PARITY-RFU-1', surface
                assert sorted(corrected['changed_fields']) == [
                    'date', 'memo', 'total_minor_units'], surface
                assert [(batch['kind'], batch['effective_date'])
                        for batch in corrected['revision']['batches']] == [
                    ('replacement', '2025-03-22')], surface

                # The bank and the receivable move to the corrected figure; income does not move.
                assert await ledger() == {receivable: INVOICE - CREDIT + CORRECTED,
                                          income: -(INVOICE - CREDIT), bank: -CORRECTED}, surface
                # The superseded revision is still readable and was reversed at its own date.
                superseded = await matrix.call(surface, 'customer-refund show',
                                               {'refund': paid['id'], 'revision_number': 1})
                assert superseded['revision']['total']['minor_units'] == FIRST, surface
                assert [(batch['kind'], batch['effective_date'])
                        for batch in superseded['revision']['batches']] == [
                    ('original', '2025-03-20'), ('reversal', '2025-03-20')], surface
                # The credit paid out 20.00 of its 40.00 and never both amounts at once.
                worth = (await matrix.call(surface, 'credit-memo show',
                                           {'credit_memo': credit['id']}))['source_current']
                assert worth['refunded_minor_units'] == CORRECTED, surface
                assert worth['available_minor_units'] == CREDIT - CORRECTED, surface

                after = database(dbpath)
                replay = await call('customer-refund update', patch,
                                    idempotency_key='refund-correction')
                assert replay['revision']['id'] == corrected['revision']['id'], surface
                assert database(dbpath) == after, surface

                stale = await call('customer-refund update', {**patch, 'memo': 'Stale'},
                                   rejected=True)
                assert stale['code'] == 'E_VERSION_CONFLICT', surface
                assert database(dbpath) == after, surface

                assert set(calls) == COMMANDS, surface
                driven[surface] = set(calls)
                from tests.test_mcp_registry_work import GHOST
                for name, data in list(calls.items()):
                    assert (await call(name, data, company=GHOST,
                                       rejected=True))['code'] == 'E_COMPANY_NOT_FOUND', surface
            # This file claims exactly these commands on all four surfaces, and drove them.
            assert set(driven) == set(matrix.documents)
            assert all(names == COMMANDS for names in driven.values())
            expected = normalize(matrix.documents['python'], matrix.roots['python'], set())
            for surface in ('cli', 'http', 'mcp'):
                actual = normalize(matrix.documents[surface], matrix.roots[surface], set())
                assert len(actual) == len(expected)
                for index, (left, right) in enumerate(zip(expected, actual)):
                    assert left == right, (surface, index, left, right)
        finally:
            await matrix.close()

    anyio.run(witness)
