"""The public setup contract across Python, CLI, HTTP and source-bound MCP."""
from pathlib import Path
import anyio
import pytest
from tests.payment_raw_evidence import database


@pytest.mark.timeout(300)
def test_permission_setup_crosses_all_four_actual_transports(root,client,tmp_path):
    pytest.importorskip('mcp')
    from tests.mcp_matrix_support import Matrix
    company=client.company.list()['items'][0]['company_id']
    person=client.user.add(username='transport-clerk',company=company)

    async def witness():
        matrix=Matrix()
        try:
            await matrix.open(root,tmp_path/'surfaces',mcp_env={
                'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')})
            # Help also exercises the actual registered examples and schemas.
            for name in ('permission show','permission activate','membership effective'):
                reply=await matrix.mcp.call_tool('bookflow_help',{'command':name})
                assert not reply.is_error,reply
            for surface in matrix.documents:
                async def call(name,raw,**ctx):
                    return await matrix.call(surface,name,raw,**ctx)
                path=matrix.roots[surface]/'hub.db'
                state=await call('permission show',{})
                assert state['mode']=='legacy'
                raw=dict(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256'])
                before=database(path)
                preview=await call('permission activate',raw,dry_run=True)
                assert preview['dry_run'] and preview['changed'],surface
                assert database(path)==before,surface
                activated=await call('permission activate',raw)
                assert activated['mode']=='policy_v1' and activated['changed'],surface
                current=await call('permission show',{})
                assert current['generation']==activated['generation'] and current['catalog_sha256']==state['catalog_sha256']
                grant=dict(user=person['user_id'],company=company,expected_version=1,role='standard',
                    grants=['transaction.check.delete'],denies=['ledger.post'])
                before=database(path)
                assert (await call('membership grant',grant,dry_run=True))['dry_run']
                assert database(path)==before,surface
                saved=await call('membership grant',grant)
                assert saved['version']==2 and saved['grants']==grant['grants'] and saved['denies']==grant['denies'],surface
                rows=await call('membership effective',dict(company=company,user=person['user_id']))
                bits={x['requirement']['capability']:x['admitted'] for x in rows['permissions']}
                assert bits['ledger.read'] and bits['transaction.check.delete'] and not bits['ledger.post']
                assert not bits['transaction.card_charge.delete']
                before=database(path)
                stale=await call('membership grant',grant,rejected=True)
                assert stale['code']=='E_VERSION_CONFLICT' and database(path)==before,surface
                revoked=await call('membership revoke',dict(user=person['user_id'],company=company,expected_version=2))
                assert revoked['version']==3 and revoked['revoked_at'] is not None,surface
        finally:
            await matrix.close()
    anyio.run(witness)
