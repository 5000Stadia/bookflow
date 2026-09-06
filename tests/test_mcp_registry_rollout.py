"""Owned organization/company rollout and detach/reattach through each adapter."""
import re
import sqlite3
from pathlib import Path
import anyio
import pytest
from tests.mcp_matrix_support import Matrix, normalize
from tests.test_mcp_registry_work import GHOST

COMMANDS = frozenset({'organization new', 'organization rename', 'company new', 'chart apply', 'profile apply', 'company detach', 'company attach'})


def state(root):
    result = {}
    for path in root.rglob('*.db'):
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
            result[str(path.relative_to(root))] = tuple(db.iterdump())
    return result


@pytest.mark.timeout(300)
def test_rollout_chart_profile_detach_reattach_full_documents(root, tmp_path):
    ids = set()
    for lines in state(root).values():
        for line in lines:
            ids.update(re.findall(r'\b[0-9A-HJKMNP-TV-Z]{26}\b', line))
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                here = matrix.roots[surface]
                calls = set()
                async def call(name, raw, **ctx):
                    calls.add(name)
                    before = state(here) if ctx.get('dry_run') or ctx.get('rejected') else None
                    out = await matrix.call(surface, name, raw, **ctx)
                    if before is not None:
                        assert state(here) == before, (surface,name,'mutation on preview/rejection')
                    return out
                orgraw = {'name':'Owned rollout organization'}
                await call('organization new', orgraw, dry_run=True)
                org = await call('organization new', orgraw, idempotency_key='rollout-org')
                assert (await call('organization new', orgraw, idempotency_key='rollout-org'))['idempotent_replay']
                assert (await call('organization new', orgraw, rejected=True))['code'] == 'E_NAME_TAKEN'
                rename = dict(organization=org['organization_id'], name='Renamed rollout organization')
                await call('organization rename', rename, dry_run=True)
                await call('organization rename', rename)
                await call('organization rename', dict(rename, organization=GHOST), rejected=True)
                raw = dict(organization=org['organization_id'], legal_name='Owned rollout company', home_currency='USD', chart='none')
                await call('company new', raw, dry_run=True)
                company = await call('company new', raw, idempotency_key='rollout-company')
                assert (await call('company new', raw, idempotency_key='rollout-company'))['idempotent_replay']
                assert (await call('company new', dict(raw,organization=GHOST), rejected=True))['code'] == 'E_ORGANIZATION_NOT_FOUND'
                cid, folder = company['company_id'], Path(company['path'])
                assert folder.is_relative_to(here) and (folder/'company.db').is_file()
                for name, field, value in [('chart apply','template_id','general'), ('profile apply','profile_id','standard')]:
                    payload = {field:value}
                    await call(name, payload, company=cid, dry_run=True)
                    await call(name, payload, company=cid, idempotency_key=name.replace(' ','-'))
                    await call(name, payload, company=cid, idempotency_key=name.replace(' ','-'))
                    await call(name, {field:'missing-owned-template'}, company=cid, rejected=True)
                    assert (await call(name, payload, company=GHOST, rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
                await call('company detach', {'company':cid}, dry_run=True)
                detached = await call('company detach', {'company':cid})
                assert detached['company_id'] == cid and folder.is_dir()
                with sqlite3.connect((here/'hub.db').as_uri()+'?mode=ro', uri=True) as db:
                    assert db.execute('SELECT id FROM companies WHERE id=?',(cid,)).fetchone() is None
                assert (await call('company detach', {'company':cid}, rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
                await call('company attach', {'path':str(folder)}, dry_run=True)
                attached = await call('company attach', {'path':str(folder)})
                assert attached['company_id'] == cid
                assert (await call('company attach', {'path':str(folder)}, rejected=True))['code'] == 'E_ALREADY_ATTACHED'
                # Hub commands reject execution-company context rather than applying a fallback.
                for name, payload in [('organization new',orgraw),('organization rename',rename),('company new',raw),
                                      ('company detach',{'company':cid}),('company attach',{'path':str(folder)})]:
                    assert (await call(name,payload,company=cid,rejected=True))['code'] == 'E_USAGE'
                assert calls == COMMANDS
            expected = normalize(matrix.documents['python'], matrix.roots['python'], ids)
            for surface in ('cli','http','mcp'):
                actual = normalize(matrix.documents[surface], matrix.roots[surface], ids)
                assert len(actual) == len(expected)
                for index,(a,b) in enumerate(zip(expected,actual)):
                    assert a == b,(surface,index,a,b)
        finally:
            await matrix.close()
    anyio.run(witness)
