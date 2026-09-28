"""R135: the same card credit through Python, the CLI, HTTP and the MCP adapter.

On each surface: a 45.50 credit on a fresh card is previewed, posted (and replayed under its
idempotency key), refused when its lines come to 44.00, read, listed, corrected to 50.00,
voided and walked through its two revisions. The card is debited and the expense credited.
"""
from copy import deepcopy

import anyio

import pytest

COMMANDS = frozenset(f'card-credit {verb}' for verb in ('post', 'show', 'query', 'update', 'void', 'history'))
SURFACES = ('python', 'cli', 'http', 'mcp')


@pytest.mark.timeout(300)
def test_the_same_card_credit_through_python_cli_http_and_mcp(root, tmp_path):
    pytest.importorskip('mcp')
    from tests.mcp_matrix_support import Matrix

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                call = lambda name, raw, **ctx: matrix.call(surface, name, deepcopy(raw), **ctx)
                card = (await call('account create', dict(name='Parity card', type='credit_card')))['id']
                expense = (await call('account create', dict(name='Parity returns', type='expense')))['id']
                credit = dict(account=card, date='2026-03-05', amount='45.50',
                              expenses=[{'account': expense, 'amount': '45.50'}])
                assert (await call('card-credit post', credit, dry_run=True))['dry_run'], surface
                posted = await call('card-credit post', credit, idempotency_key='card-credit-1')
                replay = await call('card-credit post', credit, idempotency_key='card-credit-1')
                assert replay['id'] == posted['id'] and replay['idempotent_replay'], surface
                assert posted['document']['kind'] == 'card_credit', surface
                assert sorted((line['account_id'], line['side'], line['amount_minor_units'])
                              for line in posted['revision']['lines']) == sorted(
                    [(card, 'debit', 4550), (expense, 'credit', 4550)]), surface
                short = {**credit, 'expenses': [{'account': expense, 'amount': '44.00'}]}
                refused = await call('card-credit post', short, rejected=True)
                assert refused['code'] == 'E_UNBALANCED_ENTRY', (surface, refused)
                read = await call('card-credit show', {'card_credit': posted['id']})
                assert read['document']['amount']['amount'] == '45.50', surface
                listed = await call('card-credit query', {'account': card, 'limit': 5})
                assert listed['count'] == 1, surface
                line = next(row for row in read['revision']['lines'] if row['account_id'] == expense)
                fixed = await call('card-credit update', {
                    'card_credit': posted['id'], 'expected_version': posted['version'], 'amount': '50.00',
                    'expenses': [{'line_id': line['line_id'], 'account': expense, 'amount': '50.00'}]})
                assert fixed['version'] == 2 and fixed['document']['amount']['amount'] == '50.00', surface
                voided = await call('card-credit void', {'card_credit': posted['id'],
                                                         'expected_version': fixed['version']})
                assert voided['status'] == 'voided', surface
                walk = await call('card-credit history', {'card_credit': posted['id'], 'limit': 5})
                assert walk['count'] == 2, surface
        finally:
            await matrix.close()

    anyio.run(witness)
