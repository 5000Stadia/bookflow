"""The same batch of invoices through Python, the CLI, the HTTP host and MCP.

Four clones of one company run the identical sequence and the four documents are compared after
generated identifiers and timestamps are normalized. What that proves is the contract every
adapter is meant to satisfy: the same inputs, the same outputs, the same errors -- including the
per-customer failure rows, which are ordinary output here rather than an exception on any surface.
"""
from copy import deepcopy

import pytest

COMMANDS = frozenset((
    'billing-group create', 'billing-group add', 'billing-group remove', 'billing-group rename',
    'billing-group delete', 'billing-group show', 'billing-group list',
    'batch-invoice post', 'batch-invoice retry', 'batch-invoice show', 'batch-invoice query',
))

LINES = [{'item': 'Mainline Clearing', 'quantity': '1'}]
DATE = '2026-09-01'


@pytest.mark.timeout(900)
def test_the_same_batch_of_invoices_through_python_cli_http_and_mcp(root, tmp_path):
    pytest.importorskip('mcp')
    import anyio

    from tests.mcp_matrix_support import Matrix, normalize
    from tests.test_mcp_registry_work import GHOST

    def _stable(documents, root):
        """Normalize, and flatten the elapsed readings the shared normalizer does not know.

        The company-update receipt this scenario's setup produces says how many seconds ago the
        previous writer touched the company, and four runs one after another are four different
        numbers. That reading is a clock, not a contract, so it is levelled here the same way
        ``normalize`` already levels the one it does know about.
        """
        def levelled(value):
            if isinstance(value, dict):
                return {key: ('<elapsed>' if key.startswith('seconds_since_')
                              and isinstance(item, (int, float)) and not isinstance(item, bool)
                              else levelled(item)) for key, item in value.items()}
            if isinstance(value, list):
                return [levelled(item) for item in value]
            if isinstance(value, tuple):
                return tuple(levelled(item) for item in value)
            return value

        return normalize(levelled(documents), root, set())

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                calls = {}

                async def call(name, raw, **ctx):
                    if not ctx.get('rejected'):
                        calls[name] = deepcopy(raw)
                    return await matrix.call(surface, name, raw, **ctx)

                # The demo company has no default sales tax item and its customers are
                # taxable, so a hand-entered invoice needs one too. A batch carries no tax
                # item of its own on purpose; setting the company default is the remedy.
                items = await matrix.call(surface, 'item query',
                                          dict(limit=200, projection='summary'))
                tax_item = next(row['id'] for row in items['items']
                                if row['type'] == 'sales_tax_item' and row['active'])
                await matrix.call(surface, 'company update', dict(
                    default_sales_tax_item_id=tax_item))

                # A customer that is paused, so the run has a real failure to report and a real
                # failure to retry rather than a contrived one.
                paused = await matrix.call(surface, 'customer create',
                                           dict(name='Parity Paused Customer'))
                await matrix.call(surface, 'customer deactivate',
                                  dict(customer=paused['id'], expected_version=paused['version']))

                group = (await call('billing-group create',
                                    dict(name='Parity retainers')))['billing_group']['id']
                await call('billing-group add', dict(
                    billing_group=group, customers=['Riverside Apartments', paused['id']]))
                shown = await call('billing-group show', dict(billing_group=group))
                assert [member['position'] for member in shown['members']] == [1, 2]
                await call('billing-group list', dict(limit=25))

                entry = dict(date=DATE, billing_group=group, lines=LINES, memo='Parity retainer')
                preview = await call('batch-invoice post', entry, dry_run=True)
                assert preview['dry_run'] and preview['batch_id'] is None
                assert (preview['created_count'], preview['failed_count']) == (1, 1)

                posted = await call('batch-invoice post', entry, idempotency_key='batch-1')
                replay = await call('batch-invoice post', entry, idempotency_key='batch-1')
                assert replay['batch_id'] == posted['batch_id']
                assert (posted['created_count'], posted['failed_count']) == (1, 1)

                await call('batch-invoice show', dict(batch=posted['batch_id']))
                await call('batch-invoice query', dict(limit=25))

                # A retry while the customer is still paused records its own batch and reports
                # the refusal again; it is not an exception on any surface.
                again = await call('batch-invoice retry', dict(batch=posted['batch_id']))
                assert (again['created_count'], again['failed_count']) == (0, 1)
                current = await matrix.call(surface, 'customer show', dict(customer=paused['id']))
                await matrix.call(surface, 'customer activate', dict(
                    customer=paused['id'], expected_version=current['version']))
                finished = await call('batch-invoice retry', dict(batch=again['batch_id']))
                assert (finished['created_count'], finished['failed_count']) == (1, 0)

                await call('billing-group remove', dict(billing_group=group, customers=[paused['id']]))
                renamed = await call('billing-group rename', dict(
                    billing_group=group, name='Parity retainers 2026'))
                await call('billing-group delete', dict(
                    billing_group=group, expected_version=renamed['billing_group']['version']))

                refused = await call('batch-invoice post', dict(
                    date=DATE, customers=['Riverside Apartments', 'Riverside Apartments'],
                    lines=LINES), rejected=True)
                assert refused['code'] == 'E_VALIDATION'

                assert set(calls) == COMMANDS
                for name, data in list(calls.items()):
                    assert (await call(name, data, company=GHOST,
                                       rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
            expected = _stable(matrix.documents['python'], matrix.roots['python'])
            for surface in ('cli', 'http', 'mcp'):
                actual = _stable(matrix.documents[surface], matrix.roots[surface])
                assert len(actual) == len(expected)
                for index, (left, right) in enumerate(zip(expected, actual)):
                    assert left == right, (surface, index, left, right)
        finally:
            await matrix.close()

    anyio.run(witness)
