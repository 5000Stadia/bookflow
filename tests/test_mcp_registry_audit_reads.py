"""Complete historical audit/activity documents preserve their original writers."""
import re
import sqlite3
import anyio
import pytest
from tests.mcp_matrix_support import Matrix, normalize
from tests.test_mcp_registry_work import company_snapshot, GHOST

COMMANDS = frozenset(('audit list','audit show','audit tail','activity'))


@pytest.mark.timeout(180)
def test_audit_activity_full_documents_historical_attribution_and_rejections(root,client,tmp_path):
    company = client.company.list()['items'][0]['company_id']
    target = client.customer.create(name='Audit matrix customer',company=company)['id']
    client.note.add(record_type='customer',record_id=target,body='Keep original <writer> é',company=company,reason='Audit matrix seed')
    baseline_ids = set()
    for path in root.rglob('*.db'):
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            for line in db.iterdump():
                baseline_ids.update(re.findall(r'\b[0-9A-HJKMNP-TV-Z]{26}\b',line))
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root,tmp_path)
            for surface in matrix.documents:
                before = company_snapshot(matrix.roots[surface])
                page = await matrix.call(surface,'audit list',dict(command='note add',limit=1))
                assert page['count'] == 1 and page['items'][0]['reason'] == 'Audit matrix seed'
                original = page['items'][0]
                assert original['interface'] == 'python'
                detail = await matrix.call(surface,'audit show',dict(event=original['id']))
                assert detail['interface'] == 'python' and detail['entries']
                tail_input = dict(after=original['seq']-1,command='note add',limit=1)
                tail = await matrix.call(surface,'audit tail',tail_input)
                assert [r['id'] for r in tail['items']] == [original['id']]
                assert tail['items'][0]['interface'] == 'python'
                activity_input = dict(record_type='customer',record_id=target,limit=200)
                activity = await matrix.call(surface,'activity',activity_input)
                notes = [row for row in activity['items'] if row['kind'] == 'note']
                assert len(notes) == 1 and notes[0]['body'] == 'Keep original <writer> é'
                assert notes[0]['interface'] == 'python'
                assert (await matrix.call(surface,'audit show',dict(event=GHOST),rejected=True))['code'] == 'E_EVENT_NOT_FOUND'
                for name,data in [('audit list',{}),('audit show',dict(event=original['id'])),('audit tail',tail_input),('activity',activity_input)]:
                    assert (await matrix.call(surface,name,data,company=GHOST,rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
                    assert (await matrix.call(surface,name,data,dry_run=True,rejected=True))['code'] == 'E_USAGE'
                assert company_snapshot(matrix.roots[surface]) == before
            expected = normalize(matrix.documents['python'],matrix.roots['python'],baseline_ids)
            for surface in ('cli','http','mcp'):
                actual = normalize(matrix.documents[surface],matrix.roots[surface],baseline_ids)
                assert actual == expected
        finally:
            await matrix.close()
    anyio.run(witness)
