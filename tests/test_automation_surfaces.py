"""R187 increment 2: the same automation through Python, the CLI, HTTP and MCP.

Each surface gets an identical demo copy. It drafts a send-mode delivery schedule, lists it as a draft automation,
makes it live through `delivery-schedule update --mode send` with its check's fingerprint, shows it, pauses it
(a second pause is a no-op that says so) and lists it paused; an unknown kind is refused. Every surface must end with
the same states, reasons and warnings.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('automation list', 'automation show', 'automation pause'))


@pytest.mark.timeout(900)
def test_the_same_automation_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                async def call(name, body, **kw):
                    return await matrix.call(surface, name, body, **kw)
                rule = {'kind': 'delivery', 'rule': 'Month end'}
                await call('delivery-schedule create', {
                    'name': 'Month end', 'kind': 'statement', 'period': 'previous-month', 'frequency': 'monthly',
                    'start_date': '2099-01-31', 'mode': 'send'})
                drafted = await call('automation list', {})
                check = (await call('delivery-schedule update', {'schedule': 'Month end', 'mode': 'send'},
                                    dry_run=True))['check']
                live = await call('delivery-schedule update', {'schedule': 'Month end', 'mode': 'send',
                                                               'confirm': check['fingerprint']})
                shown = await call('automation show', rule)
                paused = await call('automation pause', rule)
                again = await call('automation pause', rule)
                listed = await call('automation list', {'state': 'paused'})
                bad = await call('automation show', {'kind': 'nothing', 'rule': 'Month end'}, rejected=True)
                results[surface] = dict(drafted=drafted, live=live, shown=shown, paused=paused, again=again,
                                        listed=listed, bad=bad)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, r in results.items():
        assert [(x['name'], x['state']) for x in r['drafted']['rows']] == [('Month end', 'draft')], surface
        assert r['live']['state'] == 'live' and r['shown']['state'] == 'live', surface
        assert r['shown']['capability'] == 'automate.delivery' and r['shown']['would_pause'] is None, surface
        assert r['paused']['state'] == 'paused' and r['paused']['paused_reason'].startswith('paused by'), surface
        assert r['again']['warnings'] == ['It is already paused.'], surface
        assert [(x['name'], x['state']) for x in r['listed']['rows']] == [('Month end', 'paused')], surface
        assert 'E_VALIDATION' in str(r['bad']), surface
        shapes.add((r['shown']['what'], r['paused']['paused_reason'], r['again']['state']))
    assert len(shapes) == 1
