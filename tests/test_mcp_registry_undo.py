"""Shared compensating list undo preserves immutable evidence across adapters."""
import re
import sqlite3
import anyio
import pytest
from copy import deepcopy
from bookflow.core.context import client_version
from tests.mcp_matrix_support import Matrix, normalize
from tests.test_mcp_registry_work import company_snapshot, GHOST

COMMANDS = frozenset({'undo'})


@pytest.mark.timeout(180)
def test_undo_preview_compensation_replay_and_rejected_state(root, client, tmp_path):
    company = 'Demo Plumbing Co'
    account = client.account.create(name='Owned undo witness', type='expense', company=company)
    event = client.audit.list(command='account create', record_type='account', record_id=account['id'], company=company)['items'][0]
    ids = set()
    for path in root.rglob('*.db'):
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
            for line in db.iterdump():
                ids.update(re.findall(r'\b[0-9A-HJKMNP-TV-Z]{26}\b', line))
    def compared(documents, here, surface):
        documents = deepcopy(documents)
        for name, row in documents:
            if name == 'audit show' and row['command'] == 'undo':
                assert row['client_name'] == {'python':'python', 'cli':'bookflow-cli', 'http':'Parity bearer', 'mcp':'bookflow-agent'}[surface]
                assert row['client_version'] == ('0.0.1' if surface == 'mcp' else client_version())
                row['client_name'] = '<verified adapter name>'
                row['client_version'] = '<verified adapter version>'
        return normalize(documents, here, ids)
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                here = matrix.roots[surface]
                before = company_snapshot(here)
                raw = {'event_id':event['id']}
                preview = await matrix.call(surface, 'undo', raw, dry_run=True)
                assert preview['dry_run'] and company_snapshot(here) == before
                result = await matrix.call(surface, 'undo', raw, idempotency_key='compensate-once')
                assert result['original_event_id'] == event['id']
                current = await matrix.call(surface, 'account show', {'account':account['id']})
                assert not current['active'] and current['version'] == account['version'] + 1
                original = await matrix.call(surface, 'audit show', {'event':event['id']})
                inverse = await matrix.call(surface, 'audit show', {'event':result['undo_event_id']})
                assert original['interface'] == 'python' and inverse['interface'] == surface
                assert inverse['undo_of_event_id'] == original['id']
                after = company_snapshot(here)
                replay = await matrix.call(surface, 'undo', raw, idempotency_key='compensate-once')
                assert replay['undo_event_id'] == result['undo_event_id'] and company_snapshot(here) == after
                for payload,ctx,code in [(raw,{},'E_ALREADY_UNDONE'), ({'event_id':GHOST},{},'E_EVENT_NOT_FOUND'),
                                         (raw,{'company':GHOST},'E_COMPANY_NOT_FOUND')]:
                    assert (await matrix.call(surface,'undo',payload,rejected=True,**ctx))['code'] == code
                    assert company_snapshot(here) == after
            expected = compared(matrix.documents['python'], matrix.roots['python'], 'python')
            for surface in ('cli','http','mcp'):
                actual = compared(matrix.documents[surface], matrix.roots[surface], surface)
                assert actual == expected,(surface,actual,expected)
        finally:
            await matrix.close()
    anyio.run(witness)
