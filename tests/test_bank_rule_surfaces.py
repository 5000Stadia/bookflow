"""R108 increment 4: bank rules through Python, the CLI, HTTP and MCP.

Each surface gets an identical demo copy. It writes a rule for Shell, imports a statement whose Shell line the rule
proposes for, lists and shows the rule, applies the rules again (dry run, then for real), adds two coffee and two tea
lines so two rules are learned, accepts one suggestion and declines the other, makes an ignore rule live with its
check's fingerprint, and deactivates the Shell rule; a duplicate name is refused. Every surface must end with the same
states, proposals and refusals.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix
from tests.test_bank_feeds import csv_file

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('bank-rule create', 'bank-rule update', 'bank-rule deactivate', 'bank-rule accept',
                      'bank-rule decline', 'bank-rule apply', 'bank-rule show', 'bank-rule query'))
SHELL = {'name': 'Shell fuel', 'account': 'Checking', 'direction': 'out',
         'conditions': [{'field': 'payee', 'op': 'contains', 'value': 'SHELL'}], 'payee': 'Central Supply',
         'category': 'Vehicle Expense'}
FILE = csv_file([('2026-10-06', 'SHELL OIL 4471', '-45.67'), ('2026-10-07', 'COFFEE BEAN 12', '-4.50'),
                 ('2026-10-08', 'COFFEE BEAN 34', '-5.25'), ('2026-10-09', 'TEA HOUSE 1', '-3.00'),
                 ('2026-10-10', 'TEA HOUSE 2', '-3.50'), ('2026-10-11', 'MONTHLY SERVICE FEE', '-12.00')])


@pytest.mark.timeout(900)
def test_the_same_bank_rules_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                async def call(name, body, **kw):
                    return await matrix.call(surface, name, body, **kw)
                made = await call('bank-rule create', SHELL)
                taken = await call('bank-rule create', SHELL, rejected=True)
                done = await call('bank-feed import', {'account': 'Checking', 'content': FILE})
                lines = {v['payee']: v for v in done['lines']}
                shown = await call('bank-rule show', {'rule': 'Shell fuel'})
                listed = await call('bank-rule query', {'account': 'Checking'})
                plan = await call('bank-rule apply', {}, dry_run=True)
                applied = await call('bank-rule apply', {})
                for payee in ('COFFEE BEAN 12', 'COFFEE BEAN 34', 'TEA HOUSE 1', 'TEA HOUSE 2'):
                    await call('bank-line add', {'line': lines[payee]['id'], 'payee': 'Central Supply',
                                                 'account': 'Office Supplies'})
                suggested = await call('bank-rule query', {'status': 'suggested'})
                accepted = await call('bank-rule accept', {'rule': 'Coffee Bean (learned)'})
                declined = await call('bank-rule decline', {'rule': 'Tea House (learned)'})
                await call('bank-rule create', {'name': 'Fee', 'account': 'Checking', 'action': 'ignore',
                                                'conditions': [{'field': 'payee', 'op': 'contains',
                                                                'value': 'SERVICE FEE'}]})
                check = (await call('bank-rule update', {'rule': 'Fee', 'live': True}, dry_run=True))['check']
                live = await call('bank-rule update', {'rule': 'Fee', 'live': True, 'confirm': check['fingerprint']})
                off = await call('bank-rule deactivate', {'rule': 'Shell fuel'})
                results[surface] = dict(made=made, taken=taken, shell=lines['SHELL OIL 4471'], shown=shown,
                                        listed=listed, plan=plan, applied=applied, suggested=suggested,
                                        accepted=accepted, declined=declined, check=check, live=live, off=off)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, r in results.items():
        assert r['made']['rule']['state'] == 'draft' and not r['made']['rule']['automation'], surface
        assert 'E_NAME_TAKEN' in str(r['taken']), surface
        assert r['shell']['rule_id'] == r['made']['rule']['id'], surface
        assert r['shell']['rule_reason'] == "rule 'Shell fuel': payee contains SHELL", surface
        assert r['shown']['rule']['hits'] == 1 and [x['name'] for x in r['listed']['rules']] == ['Shell fuel']
        assert r['plan']['lines'] == [] and r['applied']['lines'] == [], surface
        assert sorted(x['learned_key'] for x in r['suggested']['rules']) == ['COFFEE BEAN', 'TEA HOUSE'], surface
        assert r['accepted']['rule']['status'] == 'active' and r['declined']['rule']['status'] == 'retired'
        assert r['check']['waiting'] == 1 and r['live']['rule']['state'] == 'live', surface
        assert r['off']['rule']['status'] == 'disabled', surface
        shapes.add((r['shown']['rule']['does'], r['check']['does'], r['declined']['summary']))
    assert len(shapes) == 1
