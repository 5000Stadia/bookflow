"""Combined reviewed payment base through installed MCP; not blind acceptance."""
import os
import sqlite3
from pathlib import Path
import sys

import anyio
import pytest
from tests.test_row3_host import hosted, live


@pytest.mark.timeout(180)
def test_installed_payment_preview_receive_retry_and_settlement(hosted, live, tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    with sqlite3.connect((hosted.root / 'hub.db').as_uri() + '?mode=ro', uri=True) as db:
        relative = db.execute('SELECT path FROM companies WHERE id=?', (hosted.company_id,)).fetchone()[0]
    company_path = hosted.root / relative / 'company.db'
    def audit_rows():
        with sqlite3.connect(company_path.as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            return {row['id']: dict(row) for row in db.execute('SELECT * FROM audit_events')}
    audit_before = audit_rows()
    def seed(command, raw):
        return hosted.ok(command, raw, company=hosted.company_id)
    customer = seed('customer.create', {'name': 'Installed payment customer'})['id']
    income = seed('account.create', {'name': 'Installed payment income', 'type': 'income'})['id']
    code = next(row['id'] for row in seed('sales-tax-code.list', {})['items'] if not row['taxable'])
    item = seed('item.create', dict(name='Installed payment service', type='service', sales_enabled=True,
        description='Payment fixture service', income_account_id=income, price='100.00', sales_tax_code_id=code))['id']
    method = seed('payment-method.create', {'name': 'Installed cash', 'kind': 'cash'})['id']
    result_ids = {}
    async def witness():
        binary = os.environ.get('BOOKFLOW_MCP_TEST_BINARY', str(Path(sys.executable).with_name('bookflow')))
        params = StdioServerParameters(command=binary, args=['mcp', '--url', live, '--client-name', 'installed-payment'],
            env={'BOOKFLOW_TOKEN': hosted.secret, 'BOOKFLOW_COMPANY': hosted.company_id,
                 'BOOKFLOW_DATA_ROOT': str(tmp_path / 'absent')}, cwd=str(tmp_path))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.discover()
                async def call(tool, args, error=None):
                    reply = await session.call_tool(tool, args)
                    assert reply.is_error == bool(error), reply
                    if error:
                        assert reply.structured_content['code'] == error
                    return reply.structured_content
                async def run(command, raw, **ctx):
                    return await call('bookflow_run', dict(command=command, input=raw, **ctx))
                catalog = await call('bookflow_list_commands', {'prefix': 'payment', 'limit': 200})
                # This journey requires these commands; unrelated additions are valid.
                names = {row['name'] for row in catalog['commands']}
                assert {
                    'payment receive', 'payment show', 'payment history',
                    'payment operation show',
                } <= names
                assert any(row['name'] == 'payment-method create' for row in catalog['commands'])
                help_ = await call('bookflow_help', {'command': 'payment receive'})
                assert 'operation_key' in help_['input_schema']['properties']
                assert 'bookflow_run' in help_['documentation']
                invoice = await run('invoice post', dict(customer=customer, date='2026-06-01',
                    lines=[dict(item=item, quantity='1')]), reason='Installed payment fixture')
                args = dict(customer=customer, date='2026-06-01', amount='100.00', payment_method=method,
                    operation_key='installed-receipt-once', applications=dict(mode='inline', items=[
                        dict(invoice=invoice['id'], expected_version=invoice['version'], amount='100.00')]))
                before = await run('invoice settlement', {'invoice': invoice['id']})
                assert before['due_minor_units'] == 10000
                preview = await run('payment receive', args, dry_run=True, reason='Customer cash receipt')
                assert preview['dry_run']
                assert (await run('invoice settlement', {'invoice': invoice['id']}))['due_minor_units'] == 10000
                paid = await run('payment receive', dict(args, expected_facts_fingerprint=preview['facts_fingerprint']), reason='Customer cash receipt')
                retry = await run('payment receive', args, reason='Customer cash receipt')
                assert retry['idempotent_replay'] and retry['effect'] == paid['effect']
                assert (await run('invoice settlement', {'invoice': invoice['id']}))['due_minor_units'] == 0
                assert (await run('payment show', {'payment': paid['id']}))['current']['available_minor_units'] == 0
                operation = await run('payment operation show', {'operation_key': args['operation_key']})
                assert operation['execution']['interface'] == 'mcp'
                history = await run('payment history', {'payment': paid['id']})
                assert history['items']
                await call('bookflow_run', dict(command='payment receive', input=dict(args, amount='101.00'),
                    reason='Customer cash receipt'), error='E_PAYMENT_OPERATION_KEY_REUSED')
                result_ids.update(payment=paid['id'], invoice=invoice['id'])
    anyio.run(witness)
    with sqlite3.connect((hosted.root / 'hub.db').as_uri() + '?mode=ro', uri=True) as db:
        relative = db.execute('SELECT path FROM companies WHERE id=?', (hosted.company_id,)).fetchone()[0]
    with sqlite3.connect((hosted.root / relative / 'company.db').as_uri() + '?mode=ro', uri=True) as db:
        audit_after = audit_rows()
        assert {identifier: audit_after[identifier] for identifier in audit_before} == audit_before
        new_receipts = [audit_after[identifier] for identifier in audit_after.keys() - audit_before.keys()
                        if audit_after[identifier]['command'] == 'payment receive']
        assert [(row['interface'], row['reason']) for row in new_receipts] == [('mcp', 'Customer cash receipt')]
        batches = db.execute('SELECT sum(debit_minor_units),sum(credit_minor_units) FROM posting_lines WHERE transaction_id=? GROUP BY batch_id', (result_ids['payment'],)).fetchall()
        assert batches and all(debit == credit == 10000 for debit, credit in batches)
        assert db.execute('SELECT count(*) FROM payment_operations WHERE operation_key=?', ('installed-receipt-once',)).fetchone() == (1,)
