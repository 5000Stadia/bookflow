"""R114 increment 4: POS registers through Python, the CLI, HTTP and MCP.

Each surface gets an identical demo copy and acts as its owner. It is refused a register while POS is off, turns
POS on (register Main appears), adds a register, changes it under its version, shows it and lists both. Every
surface ends with the same facts and the same refusal.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

COMMANDS = frozenset(('pos-register create', 'pos-register update', 'pos-register show', 'pos-register list'))


@pytest.mark.timeout(900)
def test_the_same_registers_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in matrix.documents:
                call = matrix.call
                off = await call(surface, 'pos-register list', {}, rejected=True)
                info = await call(surface, 'company show', {})
                await call(surface, 'company update', {'pos_enabled': True, 'expected_version': info['info_version']})
                made = await call(surface, 'pos-register create', {'name': 'Front counter', 'receipt_width': 58})
                changed = await call(surface, 'pos-register update', {'pos_register': 'Front counter',
                                                                      'expected_version': 1,
                                                                      'receipt_message': 'Thank you!'})
                shown = await call(surface, 'pos-register show', {'pos_register': 'Front counter'})
                listed = await call(surface, 'pos-register list', {})
                results[surface] = (
                    off.get('code') or off.get('error', {}).get('code'),
                    (made['name'], made['receipt_width'], made['walk_in_customer']['name']),
                    (changed['version'], changed['receipt_message']),
                    (shown['name'], shown['version'], shown['deposit_account']['name']),
                    [r['name'] for r in listed['items']],
                )
            first = results['python']
            assert first[0] == 'E_FEATURE_DISABLED'
            assert first[1] == ('Front counter', 58, 'Walk-in (Front counter)')
            assert first[2] == (2, 'Thank you!') and first[3] == ('Front counter', 2, 'Undeposited Funds')
            assert first[4] == ['Front counter', 'Main']
            assert all(value == first for value in results.values()), results
        finally:
            await matrix.close()

    anyio.run(witness)
