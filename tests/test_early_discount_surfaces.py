"""One discounted receipt and one discounted bill payment, identical on Python, CLI, HTTP and MCP.

Each surface gets its own copy of the seeded demo and makes the same calls: preview the receipt
with the suggested discount, record it, read it back; pay a bill with its discount, read it back.
The documents every surface returned are compared after identities and timestamps are
normalized, so a field one adapter dropped or reshaped fails here by name.
"""
import pytest


def _differences(left, right, path=''):
    """Every path at which two normalized documents disagree, for a failure that names them."""
    if isinstance(left, dict) and isinstance(right, dict):
        return [found for key in sorted(set(left) | set(right))
                for found in (_differences(left.get(key), right.get(key), f'{path}.{key}'))]
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)) and len(left) == len(right):
        return [found for index, pair in enumerate(zip(left, right))
                for found in _differences(*pair, f'{path}[{index}]')]
    return [] if left == right else [(path, left, right)]


@pytest.mark.timeout(300)
def test_the_same_discounts_through_python_cli_http_and_mcp(root, tmp_path):
    pytest.importorskip('mcp')
    import anyio

    from tests.mcp_matrix_support import Matrix, normalize

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                async def call(name, raw, **ctx):
                    document = await matrix.call(surface, name, raw, **ctx)
                    # A settlement guard is a signed token carrying its own issue time, and an
                    # audit watermark counts the events each surface's own session wrote.
                    for key in ('settlement_guard', 'audit_watermark'):
                        matrix.documents[surface][-1][1].pop(key, None)
                    return document

                terms = next(row['id'] for row in (await call('term query', dict(limit=50)))['items']
                             if row['name'] == '2% 10 Net 30')
                income = (await call('account create', dict(name='Parity discount income', type='income')))['id']
                bank = (await call('account create', dict(name='Parity discount bank', type='bank')))['id']
                expense = (await call('account create', dict(name='Parity discount supplies', type='expense')))['id']
                exempt = next(row['id'] for row in (await call('sales-tax-code list', {}))['items'] if not row['taxable'])
                item = (await call('item create', dict(name='Parity discount labor', type='service', sales_enabled=True,
                        description='Parity discount labor', sales_tax_code_id=exempt, income_account_id=income,
                        price='1.00')))['id']
                method = next(row['id'] for row in (await call('payment-method query', dict(limit=50)))['items']
                              if row['name'] == 'Check')
                customer = (await call('customer create', dict(name='Parity Discount Customer', terms_id=terms)))['id']
                vendor = (await call('vendor create', dict(name='Parity Discount Vendor', terms_id=terms)))['id']

                sale = await call('invoice post', dict(customer=customer, date='2026-03-01', number='PARITY-DISC-INV',
                                  customer_tax_code=exempt, lines=[dict(item=item, quantity='1', net_amount='250.00')]))
                listed = await call('payment invoices', dict(mode='new_receipt', customer=customer, date='2026-03-09'))
                row = next(entry for entry in listed['items'] if entry['invoice_id'] == sale['id'])
                assert (row['discount_date'], row['suggested_discount_minor_units']) == ('2026-03-11', 500)
                receipt = dict(customer=customer, date='2026-03-09', amount='245.00', payment_method=method,
                               deposit_to=bank, number='PARITY-DISC-PAY', operation_key='parity-discount-receipt',
                               applications=dict(mode='inline', items=[
                                   dict(invoice=sale['id'], expected_version=1, amount='245.00')]),
                               discounts=[dict(invoice=sale['id'], amount='5.00')])
                preview = await call('payment receive', receipt, dry_run=True)
                paid = await call('payment receive', dict(receipt, expected_facts_fingerprint=preview['facts_fingerprint']))
                assert paid['effect']['applications'][0]['discount']['amount'] == '5.00'
                await call('payment show', dict(payment=paid['id']))
                settled = await call('invoice settlement', dict(invoice=sale['id']))
                assert settled['due_minor_units'] == 0 and settled['applications'][0]['discount_minor_units'] == 500

                bill = await call('bill post', dict(vendor=vendor, date='2026-03-01', number='PARITY-DISC-BILL',
                                  expenses=[dict(account=expense, amount='400.00')]))
                row = next(entry for entry in (await call('bill query', dict(vendor=vendor, limit=10)))['items']
                           if entry['id'] == bill['id'])
                assert (row['discount_date'], row['early_discount_minor_units']) == ('2026-03-11', 800)
                request = dict(date='2026-03-09', number='PARITY-DISC-BPAY', funding_account=bank, method=method,
                               bills=[dict(bill=bill['id'], amount='392.00', discount='8.00')])
                assert (await call('bill pay', request, dry_run=True))['dry_run']
                written = await call('bill pay', request)
                assert written['discount_minor_units'] == 800 and written['paid_minor_units'] == 39200
                await call('bill payment show', dict(payment=written['payments'][0]['id']))
                await call('bill show', dict(bill=bill['id']))
                refused = await call('bill pay', dict(request, number='PARITY-DISC-BPAY-2',
                                     bills=[dict(bill=bill['id'], amount='1.00', discount='1.00')]), rejected=True)
                assert refused['code'] == 'E_APPLICATION_CAPACITY'
            expected = normalize(matrix.documents['python'], matrix.roots['python'], set())
            for surface in ('cli', 'http', 'mcp'):
                actual = normalize(matrix.documents[surface], matrix.roots[surface], set())
                assert len(actual) == len(expected)
                for index, (left, right) in enumerate(zip(expected, actual)):
                    assert left == right, (surface, index, left[0], _differences(left, right))
        finally:
            await matrix.close()

    anyio.run(witness)
