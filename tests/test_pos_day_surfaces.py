"""A counted shift and agent drawer answer through the four command adapters."""
import anyio
import pytest
from tests.mcp_matrix_support import Matrix

COMMANDS = frozenset(('pos shift open', 'pos shift close', 'report pos-day', 'pos deposit prepare'))
SURFACES = ('python', 'cli', 'http', 'mcp')


@pytest.mark.timeout(1200)
def test_day_close_four_surfaces(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            answers = {}
            for surface in matrix.documents:
                async def call(command, body):
                    return await matrix.call(surface, command, body)
                info = await call('company show', {})
                await call('company update', {'pos_enabled': True, 'expected_version': info['info_version']})
                reg = await call('pos-register show', {'pos_register': 'Main'})
                await call('pos-register update', {'pos_register': reg['id'], 'expected_version': reg['version'],
                                                   'count_shifts': True})
                shift = await call('pos shift open', {'float': '50', 'operation_key': 'surface-open'})
                cart = {'lines': [{'item': 'Pipe Sealant', 'quantity': '2'}]}
                total = (await call('pos sale preview', cart))['total']['amount']
                sale = await call('pos sale complete', dict(cart, ticket='surface-day-sale', expected_total=total,
                    tender={'payment_method': 'Cash'}))
                # This report is the agent's answer to "what should be in the drawer?".
                report = await call('report pos-day', {'shift': shift['id']})
                assert report['expected_drawer']['minor_units'] == 5000 + sale['total']['minor_units']
                close_body = {'shift': shift['id'], 'counted': report['expected_drawer']['amount'],
                              'operation_key': 'surface-close'}
                closed = await call('pos shift close', close_body)
                replay = await call('pos shift close', close_body)
                handoff = await call('pos deposit prepare', {'shift': shift['id']})
                assert handoff['document']['sources'][0]['source'] == sale['sales_receipt']['id']
                assert len(handoff['document']['sources']) == 1
                answers[surface] = (report['expected_drawer'], closed['expected'], closed['over_short'], replay['replayed'])
            assert len(answers) == 4
            assert all(answer == answers['python'] for answer in answers.values())
            assert answers['mcp'][2]['minor_units'] == 0 and answers['mcp'][3]
        finally:
            await matrix.close()
    anyio.run(witness)


from tests.test_row3_host import hosted, live  # noqa: E402,F401


def test_mcp_agent_drawer_answer_requires_explicit_sell_grant(hosted, live, tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from bookflow.core.config import Config
    from tests import provenance
    from tests.conftest import hosted_call, make_agent
    from tests.test_item_pictures_review import _membership_version

    cid = hosted.company_id
    info = hosted.ok('company.show', {})
    hosted.ok('company.update', {'pos_enabled': True, 'expected_version': info['info_version']})
    reg = hosted.ok('pos-register.show', {'pos_register': 'Main'})
    hosted.ok('pos-register.update', {'pos_register': reg['id'], 'expected_version': reg['version'], 'count_shifts': True})
    shift = hosted.ok('pos.shift.open', {'float': '37.25', 'operation_key': 'agent-drawer-open'})
    principal = Config.load(hosted.root / 'config.toml').user_table(hosted.login)['user_id']
    agent = make_agent(hosted_call(hosted), 'drawer-report-agent', principals=principal, company=cid, role='admin')

    async def witness():
        for granted in (False, True):
            if granted:
                hosted.ok('membership.grant', {'user': agent, 'company': cid, 'role': 'admin',
                    'grants': ['pos.sell'], 'expected_version': _membership_version(hosted, agent)})
            issued = hosted.ok('token.issue', {'user': agent, 'principal': principal, 'label': f'Drawer report {granted}'})
            params = StdioServerParameters(command=provenance.launcher(), args=['mcp', '--url', live],
                env=provenance.child_env(BOOKFLOW_TOKEN=issued['secret'], BOOKFLOW_COMPANY=cid,
                                        BOOKFLOW_DATA_ROOT=str(tmp_path / 'absent')), cwd=str(tmp_path))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.discover()
                    reply = await session.call_tool('bookflow_run', {'command': 'report pos-day',
                        'company': cid, 'input': {'shift': shift['id']}})
                    if granted:
                        assert not reply.is_error, reply.structured_content
                        assert reply.structured_content['expected_drawer']['minor_units'] == 3725
                    else:
                        assert reply.is_error
                        assert reply.structured_content['code'] == 'E_PERMISSION'
                        assert reply.structured_content['details']['capability'] == 'pos.sell'
    anyio.run(witness)
