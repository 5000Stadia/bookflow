"""Complete receipt/application lifecycle through all four real interfaces."""
from copy import deepcopy
import re
import sqlite3
import anyio
import pytest
from bookflow.core import registry
from tests.mcp_matrix_support import Matrix
from tests.test_mcp_registry_payment_preparation import normalize_payment, payment_operation_rows, GHOST
from tests.test_service_sales_lifecycle import sale
from tests.test_payment_receipts import posted, method

COMMANDS = frozenset('''payment receive
payment apply
payment unapply
payment update
payment void
payment show
payment query
payment history
payment operation show
payment operation items
payment settlement
payment settlement changes
payment preview items
invoice settlement
application show
application history'''.splitlines())


@pytest.mark.timeout(420)
def test_payment_financial_lifecycle_full_documents_and_exact_ledger(root, client, sale, tmp_path):
    invoices = [posted(client, sale['customer'], sale['item'], amount, number) for amount, number in
                [('1.00', 'MCP-PAY-ONE'), ('2.00', 'MCP-PAY-TWO')]]
    payment_method = method(client)
    baseline_ids = set()
    for path in root.rglob('*.db'):
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
            for line in db.iterdump():
                baseline_ids.update(re.findall(r'\b[0-9A-HJKMNP-TV-Z]{26}\b', line))
    async def witness():
        matrix, payments = Matrix(), {}
        try:
            await matrix.open(root, tmp_path)
            operation_baselines = {surface: payment_operation_rows(path, matrix.company)
                                   for surface, path in matrix.roots.items()}
            for surface in matrix.documents:
                calls = {}
                async def call(name, data, **ctx):
                    if not ctx.get('rejected'):
                        calls[name] = deepcopy(data)
                    return await matrix.call(surface, name, data, **ctx)
                async def pages(name, data):
                    result = await call(name, data)
                    seen, total = list(result['items']), result['total_count']
                    while result['next_cursor']:
                        result = await call(name, dict(data, cursor=result['next_cursor']))
                        seen.extend(result['items'])
                    assert len(seen) == total
                    return seen
                receive = dict(customer=sale['customer'], date='2026-06-01', amount='2.00',
                               payment_method=payment_method, operation_key='matrix-payment-receive')
                preview = await call('payment receive', receive, dry_run=True)
                assert preview['dry_run']
                paid = await call('payment receive', dict(receive, expected_facts_fingerprint=preview['facts_fingerprint']))
                assert (await call('payment receive', receive))['idempotent_replay']
                assert (await call('payment receive', dict(receive, amount='3.00'), rejected=True))['code'] == 'E_PAYMENT_OPERATION_KEY_REUSED'
                payments[surface] = paid['id']
                shown = await call('payment show', {'payment': paid['id']})
                # Consume the authentic guard, preserving exact owner/company and
                # baseline content when comparing independently generated tokens.
                await pages('payment settlement changes', {'guard': shown['settlement_guard'], 'limit': 200})
                await pages('payment query', {'customer': sale['customer'], 'include_descendants': False, 'limit': 200})
                apply = dict(payment=paid['id'], expected_version=paid['version'], date='2026-06-01',
                             operation_key='matrix-payment-apply', applications=dict(mode='inline', items=[
                                 dict(invoice=row['id'], expected_version=row['version'], amount='0.50') for row in invoices]))
                preview = await call('payment apply', apply, dry_run=True)
                await pages('payment preview items', dict(request=dict(command='payment apply', input=apply,
                    context={'reason': 'Registry parity'}), facts_fingerprint=preview['facts_fingerprint'], kind='applications', limit=1))
                applied = await call('payment apply', dict(apply, expected_facts_fingerprint=preview['facts_fingerprint']))
                assert applied['current']['applied_minor_units'] == 100
                await pages('payment settlement changes', {'guard': shown['settlement_guard'], 'limit': 200})
                application = applied['effect']['applications'][0]['application_id']
                await call('application show', {'application': application})
                await pages('application history', {'application': application, 'limit': 200})
                for invoice in invoices:
                    settlement = await call('invoice settlement', {'invoice': invoice['id'], 'limit': 200})
                    assert settlement['applied_minor_units'] == 50
                await call('payment operation show', {'operation_key': 'matrix-payment-apply'})
                await pages('payment operation items', {'operation_key': 'matrix-payment-apply', 'kind': 'effect_applications', 'limit': 1})
                await pages('payment settlement', {'payment': paid['id'], 'kind': 'applications', 'limit': 1})
                unapply = dict(payment=paid['id'], expected_version=applied['version'], operation_key='matrix-payment-unapply',
                    applications=[dict(application_id=row['application_id'], invoice_expected_version=2) for row in applied['effect']['applications']])
                assert (await call('payment unapply', unapply, dry_run=True))['dry_run']
                unapplied = await call('payment unapply', unapply)
                assert unapplied['current']['available_minor_units'] == 200
                update = dict(payment=paid['id'], expected_version=unapplied['version'], operation_key='matrix-payment-update', memo='Correct receipt reference')
                assert (await call('payment update', update, dry_run=True))['dry_run']
                updated = await call('payment update', update)
                assert (await call('payment update', dict(update, operation_key='matrix-stale-update'), rejected=True))['code'] == 'E_VERSION_CONFLICT'
                void = dict(payment=paid['id'], expected_version=updated['version'], operation_key='matrix-payment-void')
                assert (await call('payment void', void, dry_run=True))['dry_run']
                await call('payment void', void)
                await pages('payment history', {'payment': paid['id'], 'limit': 200})
                assert set(calls) == COMMANDS
                for name, data in calls.items():
                    assert (await matrix.call(surface, name, data, company=GHOST, rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
                    if not registry.get(name).is_write:
                        assert (await matrix.call(surface, name, data, dry_run=True, rejected=True))['code'] == 'E_USAGE'
            expected = normalize_payment(matrix.documents['python'], matrix.roots['python'], baseline_ids)
            for surface in ('cli', 'http', 'mcp'):
                actual = normalize_payment(matrix.documents[surface], matrix.roots[surface], baseline_ids)
                assert len(actual) == len(expected)
                for index, (a, b) in enumerate(zip(expected, actual)):
                    assert a == b, (surface, index, a, b)
            for surface, path in matrix.roots.items():
                with sqlite3.connect((path/'hub.db').as_uri()+'?mode=ro', uri=True) as db:
                    relative = db.execute('SELECT path FROM companies WHERE id=?', (matrix.company,)).fetchone()[0]
                with sqlite3.connect((path/relative/'company.db').as_uri()+'?mode=ro', uri=True) as db:
                    assert db.execute("SELECT interface FROM audit_events WHERE reason='Registry parity'").fetchall() == [(surface,)] * 5
                    batches = db.execute('SELECT sum(debit_minor_units),sum(credit_minor_units) FROM posting_lines WHERE transaction_id=? GROUP BY batch_id', (payments[surface],)).fetchall()
                    assert batches and all(debit == credit for debit, credit in batches)
                    assert all(net == 0 for (net,) in db.execute('SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id=? GROUP BY account_id', (payments[surface],)))
                before = operation_baselines[surface]
                after = payment_operation_rows(path, matrix.company)
                assert {identifier: after[identifier] for identifier in before} == before
                added = [after[identifier] for identifier in after.keys() - before.keys()]
                assert len(added) == 5
                assert {row['operation_key'] for row in added} == {
                    'matrix-payment-' + action for action in ('receive', 'apply', 'unapply', 'update', 'void')}
        finally:
            await matrix.close()
    anyio.run(witness)
