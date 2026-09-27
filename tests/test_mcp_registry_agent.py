"""The agent commands are the same command on all four interfaces.

One scenario on identical copies of a fresh (activated) install: create an agent, preview and
assign its principal, grant its membership, authorize it, narrow it and reauthorize it with the
fresh-context acknowledgment, with the refusals an operator is likely to meet on the way.
"""
import re
import sqlite3

import anyio
import pytest

import bookflow
from tests.mcp_matrix_support import Matrix, normalize

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('agent create', 'agent show', 'agent list', 'agent assign', 'agent unassign', 'agent authorize'))


@pytest.mark.timeout(900)
def test_agent_administration_full_documents_on_four_actual_surfaces(root, tmp_path):
    seed = bookflow.connect(data_root=str(root))
    company = seed.company.list()['items'][0]['company_id']
    for name in ('matrix-p', 'matrix-q'):
        seed.user.add(username=name, company=company, role='owner', password='pw-' + name + '-12345')
    baseline_ids = set()
    for path in root.rglob('*.db'):
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            for line in db.iterdump():
                baseline_ids.update(re.findall(r'\b[0-9A-HJKMNP-TV-Z]{26}\b', line))

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                async def call(name, raw, **ctx):
                    return await matrix.call(surface, name, raw, **ctx)

                preview = await call('agent create', dict(username='matrix-agent', owner='matrix-p'), dry_run=True)
                assert preview['dry_run'] and preview['agent']['authority']['suspended']
                created = await call('agent create', dict(username='matrix-agent', owner='matrix-p'))
                assert created['agent']['authority']['suspension_reason'] == 'not_yet_authorized'
                assert (await call('agent create', dict(username='MATRIX-AGENT'), rejected=True))['code'] == 'E_VALIDATION'
                assert (await call('agent assign', dict(agent='matrix-agent', principal='matrix-p'),
                                   rejected=True))['code'] == 'E_VALIDATION'
                assert (await call('agent show', dict(agent='nobody-agent'), rejected=True))['code'] == 'E_USER_NOT_FOUND'
                previewed = await call('agent assign', dict(agent='matrix-agent', principal='matrix-p',
                                                            confirm_permitted_use=True), dry_run=True)
                assert previewed['dry_run'] and previewed['changed']
                assigned = await call('agent assign', dict(agent='matrix-agent', principal='matrix-p',
                                                           confirm_permitted_use=True, expected_version=1))
                assert [p['username'] for p in assigned['agent']['principals']] == ['matrix-p']
                await call('membership grant', dict(user='matrix-agent', company=company, role='standard'))
                authorized = await call('agent authorize', dict(agent='matrix-agent', confirm_permitted_use=True))
                assert not authorized['agent']['authority']['suspended']
                await call('agent assign', dict(agent='matrix-agent', principal='matrix-q', confirm_permitted_use=True))
                narrowed = await call('agent unassign', dict(agent='matrix-agent', principal='matrix-q'))
                assert narrowed['agent']['authority']['fresh_context_required']
                assert (await call('agent unassign', dict(agent='matrix-agent', principal='matrix-q'),
                                   rejected=True))['code'] == 'E_RECORD_NOT_FOUND'
                assert (await call('agent authorize', dict(agent='matrix-agent', confirm_permitted_use=True),
                                   rejected=True))['code'] == 'E_VALIDATION'
                again = await call('agent authorize', dict(agent='matrix-agent', confirm_permitted_use=True,
                                                           acknowledge_fresh_context=True))
                assert not again['agent']['authority']['suspended']
                shown = await call('agent show', dict(agent='matrix-agent'))
                assert shown['authority']['epoch'] == 2
                listed = await call('agent list', dict(principal='matrix-p'))
                assert 'matrix-agent' in [a['username'] for a in listed['items']]

            expected = normalize(matrix.documents['python'], matrix.roots['python'], baseline_ids)
            for surface in ('cli', 'http', 'mcp'):
                actual = normalize(matrix.documents[surface], matrix.roots[surface], baseline_ids)
                assert len(actual) == len(expected)
                for index, (a, b) in enumerate(zip(expected, actual)):
                    assert a == b, (surface, index, a, b)
        finally:
            await matrix.close()

    anyio.run(witness)
