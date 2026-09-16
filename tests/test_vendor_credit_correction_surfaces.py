"""Correcting a vendor credit, and reading its history, over actual Python, CLI, HTTP and MCP.

Every figure is written out here so a reader can add it up without running anything:

    PB-1    bill            1000.00, dated 2025-05-01
    PVC-1   vendor credit    462.54, dated 2025-05-10, applied to PB-1 on 2025-05-12
            corrected to     650.00 -- 600.00 of roofing plus 50.00 of parts

    1000.00 - 462.54 = 537.46, which is what PB-1 still owes before and after the correction.
    650.00 - 462.54 = 187.46, which is what the corrected credit still has free.

The danger a vendor-credit correction carries is not the ledger, which is a reversal and a
replacement like any other document's. It is the capacity: the bills a credit answers name the
settlement components of one revision, so a correction that minted new components and left the
old edges standing would hold PB-1 settled against capacity the current revision no longer has,
while what the credit says it has free -- read off the current revision -- claimed the whole
credit was untouched. The same credit, spendable twice. This file drives that over every real
transport, and reads every figure back through the real commands:

**The preview writes nothing**, and a correction worth less than what the credit already answers
is refused `E_APPLICATION_CAPACITY`, names what is available and what was asked for, points at
Unapply, and writes nothing either.

**The bill owes exactly what it owed.** A correction is not a settlement. Every standing edge
names a component of the corrected revision, and the record holds both halves -- what was
released and what was taken again.

**`vendor-credit history` is the audit trail a person reads.** Both revisions in order with
their own posting batches and their own application edges, only the current revision's edge
active, and the current header and version on the page. Cursor paging is witnessed in-process
in `tests/test_vendor_credit_update.py` instead, for the reason written beside that read below.
"""
from copy import deepcopy

import anyio
import pytest

from tests.mcp_matrix_support import Matrix, normalize
from tests.payment_raw_evidence import database


# The commands this test is the designated four-surface witness for; the coverage ledger imports
# this set rather than restating the names beside a path it cannot check.
COMMANDS = frozenset(('vendor-credit update', 'vendor-credit history'))

BILL = 100000       # 1000.00 owed on PB-1
CREDIT = 46254      # 462.54 credited by PVC-1 and applied to PB-1
ROOFING = 60000     # 600.00, the corrected roofing row
PARTS = 5000        # 50.00, the row the correction adds
CORRECTED = ROOFING + PARTS     # 650.00


@pytest.mark.timeout(600)
def test_correcting_and_reading_a_vendor_credit_crosses_all_four_actual_transports(root, tmp_path):
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

                async def owed(bill_id):
                    shown = await matrix.call(surface, 'bill show', {'bill': bill_id})
                    return (shown['settlement_current']['open_minor_units'],
                            shown['settlement_current']['status'])

                async def free(credit_id):
                    shown = await matrix.call(surface, 'vendor-credit show', {'credit': credit_id})
                    return shown['settlement_current']['unapplied_minor_units']

                repairs = (await matrix.call(surface, 'account create',
                                             dict(name='Parity roof repairs', type='expense')))['id']
                parts = (await matrix.call(surface, 'account create',
                                           dict(name='Parity parts bought', type='expense')))['id']
                payable = next(row['id'] for row in (await matrix.call(
                    surface, 'account query', {'limit': 200}))['items']
                    if row['type'] == 'accounts_payable')
                vendor = (await matrix.call(surface, 'vendor create',
                                            dict(name='Parity Correction Roofing')))['id']
                bill = await matrix.call(surface, 'bill post', dict(
                    vendor=vendor, date='2025-05-01', due_date='2025-05-31', number='PB-1',
                    expenses=[{'account': repairs, 'amount': '1000.00', 'memo': 'Roof'}]))
                credit = await matrix.call(surface, 'vendor-credit post', dict(
                    vendor=vendor, date='2025-05-10', number='PVC-1', memo='Material returned',
                    supplier_reference='CN-9001',
                    expenses=[{'account': repairs, 'amount': '462.54', 'memo': 'Returned tiles'}]))
                await matrix.call(surface, 'vendor-credit apply', {
                    'credit': credit['id'], 'bills': [{'bill': bill['id']}], 'date': '2025-05-12',
                    'expected_version': credit['version']})
                assert await owed(bill['id']) == (BILL - CREDIT, 'partial'), surface
                assert await free(credit['id']) == 0, surface
                assert await ledger() == {payable: CREDIT - BILL, repairs: BILL - CREDIT}, surface

                line_id = credit['revision']['expenses'][0]['line_id']
                patch = {'credit': credit['id'],
                         'expenses': [{'line_id': line_id, 'account': repairs,
                                       'amount': '600.00', 'memo': 'Returned tiles'},
                                      {'account': parts, 'amount': '50.00',
                                       'memo': 'Two boxes back'}]}
                before = database(dbpath)
                preview = await call('vendor-credit update', patch, dry_run=True)
                assert preview['dry_run'], surface
                assert database(dbpath) == before, surface

                # Worth less than the bills it already answers: refused by name, nothing written.
                shrunk = await call('vendor-credit update', {
                    'credit': credit['id'],
                    'expenses': [{'line_id': line_id, 'account': repairs, 'amount': '100.00',
                                  'memo': 'Returned tiles'}]}, rejected=True)
                assert shrunk['code'] == 'E_APPLICATION_CAPACITY', surface
                assert shrunk['details']['available_minor_units'] == 10000, surface
                assert shrunk['details']['requested_minor_units'] == CREDIT, surface
                assert 'Unapply' in shrunk['details']['next'], surface
                assert database(dbpath) == before, surface

                corrected = await call('vendor-credit update', patch, idempotency_key='vc-fix-1')
                assert corrected['changed'] is True, surface
                assert corrected['total_minor_units'] == CORRECTED, surface
                assert corrected['revision']['revision_number'] == 2, surface
                assert corrected['number'] == credit['number'] == 'PVC-1', surface
                # The row a reader follows is the same row.
                assert corrected['revision']['expenses'][0]['line_id'] == line_id, surface
                assert [batch['kind'] for batch in corrected['revision']['batches']] == [
                    'replacement'], surface

                # The bill owes exactly what it owed: a correction is not a settlement.
                assert await owed(bill['id']) == (BILL - CREDIT, 'partial'), surface
                assert await free(credit['id']) == CORRECTED - CREDIT, surface
                assert await ledger() == {payable: CORRECTED - BILL,
                                          repairs: BILL - ROOFING, parts: -PARTS}, surface

                # Every standing edge names a component the corrected revision actually has.
                shown = await matrix.call(surface, 'vendor-credit show', {'credit': credit['id']})
                current = {row['id'] for row in shown['revision']['source']['components']}
                active = [row for row in shown['applications'] if row['active']]
                assert {row['source_component_id'] for row in active} <= current, surface
                assert sum(row['amount_minor_units'] for row in active) == CREDIT, surface
                assert [row['effective_date'] for row in active] == ['2025-05-12'] * len(active), surface
                # Both halves are in the record: what was released and what was taken again.
                assert sorted(row['kind'] for row in shown['applications']) == [
                    'apply', 'apply', 'unapply'], surface

                after = database(dbpath)
                replay = await call('vendor-credit update', patch, idempotency_key='vc-fix-1')
                assert replay['revision']['id'] == corrected['revision']['id'], surface
                assert database(dbpath) == after, surface

                # The audit trail a person reads: both revisions, in order, with their own effects.
                page = await call('vendor-credit history', {'credit': credit['id']})
                assert page['count'] == 2 and page['has_more'] is False, surface
                assert [row['revision_number'] for row in page['items']] == [1, 2], surface
                assert [row['line_count'] for row in page['items']] == [1, 2], surface
                assert page['number'] == 'PVC-1' and page['status'] == 'posted', surface
                assert page['version'] == corrected['version'], surface
                assert page['current_revision_id'] == corrected['revision']['id'], surface
                assert [[batch['kind'] for batch in row['batches']] for row in page['items']] == [
                    ['original', 'reversal'], ['replacement']], surface
                # Only the revision the books stand on answers PB-1; revision 1 was released.
                assert [sum(1 for edge in row['applications'] if edge['active'])
                        for row in page['items']] == [0, 1], surface
                assert [len(row['applications']) for row in page['items']] == [2, 1], surface

                assert page['next_cursor'] is None, surface

                # Paging is deliberately NOT driven here. A continuation cursor carries a
                # fingerprint hashed over the query, and this query names a credit whose id was
                # minted separately in each surface's own copy of the seed, so four correct
                # surfaces return four different cursors and the parity comparison cannot read
                # them as the same answer. Cursor paging for this command is witnessed
                # in-process in tests/test_vendor_credit_update.py, on one database, where the
                # cursor means something.
                #
                # The way out, untried: page over rows that come from the shared baseline rather
                # than rows minted during the test. A seeded record has the same id in all four
                # copies -- the company id already proves that much -- so a bounded page over
                # seed data should hash to the same fingerprint everywhere and hand back cursors
                # that can be compared. That would witness paging across the transports for every
                # query able to page over seeded rows.
                missing = await call('vendor-credit history', {'credit': 'PVC-NOPE'}, rejected=True)
                assert missing['code'] == 'E_RECORD_NOT_FOUND', surface
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
