"""Public saved reads coexist with current deposit writes on fresh disposable books."""
import pytest

from tests.test_deposit_command import books, receipts_in_undeposited_funds, COMPANY  # noqa: F401
from tests.conftest import Cli
from tests.test_row3_host import Hosted
from bookflow.commands.host_cmds import start_serving
from bookflow.core.context import client_version


@pytest.mark.timeout(180)
def test_post_then_read_same_identity_offline_http_and_forwarded_cli(books, tmp_path):
    client = books['client']
    _, payment, receipt = receipts_in_undeposited_funds(books)
    available = client.run('deposit sources', dict(date='2026-06-03'), company=COMPANY)
    posted = client.run('deposit post', dict(operation_key='coexist-post', document=dict(
        mode='inline', deposit_to=books['bank'], date='2026-06-03',
        sources=[{key: row[key] for key in ('source_type','source','expected_version')}
                 for row in available['items']])), company=COMPANY)
    identity = posted['deposit']['id']
    shown = client.run('deposit show', dict(deposit=identity), company=COMPANY)
    assert shown['deposit_id'] == identity and shown['totals']['bank_total']['minor_units'] == 16000
    first = client.run('deposit items', dict(deposit=identity, kind='sources', page=dict(limit=1)), company=COMPANY)
    second = client.run('deposit items', dict(deposit=identity, kind='sources', page=dict(limit=1, cursor=first['next_cursor'])), company=COMPANY)
    assert first['total_count'] == 2 and first['selected'] == second['selected']
    assert not second['next_cursor']
    assert {row['source']['transaction_id'] for row in (*first['items'], *second['items'])} == {payment['id'], receipt['id']}
    assert client.run('audit list', {}, company=COMPANY)['items']  # never installs policy_v1
    root = tmp_path / 'root'
    cli = Cli(root)
    assert cli.json('deposit', 'show', identity, '--company', COMPANY)['deposit_id'] == identity
    issued = client.token.issue(label='coexist reads')
    cid = shown['company_id']
    handle = start_serving(root, client_version(), bind='127.0.0.1:8765', secure_cookies=False)
    try:
        hosted = Hosted(handle, root, '', cid, issued, '', {})
        for verb, expected in [('show', shown), ('items', first)]:
            args = dict(deposit=identity)
            if verb == 'items': args.update(kind='sources', page=dict(limit=1))
            value = hosted.ok('deposit.'+verb, args, company=cid)
            # Ignore observation time; compare the entire public contract.
            stable = lambda x: {k:v for k,v in x.items() if k != 'current_observed_at'}
            assert stable(value) == stable(expected)
        assert cli.json('deposit', 'show', identity, '--company', cid)['deposit_id'] == identity
        assert hosted.ok('deposit.sources', dict(date='2026-06-03'), company=cid)['items'] == []
        assert hosted.ok('audit.list', {}, company=cid)['items']
    finally:
        handle.stop()


@pytest.mark.timeout(180)
def test_mcp_reads_current_public_correction_and_void_with_company_isolation(books, tmp_path):
    import asyncio
    import sys
    from pathlib import Path
    pytest.importorskip('mcp')
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from tests.test_row3_host import live
    from tests.test_deposit_command import ORGANIZATION
    from tests.conftest import make_actor, as_user

    client = books['client']
    document = dict(mode='inline', deposit_to=books['bank'], date='2026-06-03',
        additional=[dict(received_from=dict(kind='customer',id=books['customer']), from_account=books['income'], amount='12.34', memo='Cash counted')])
    posted = client.run('deposit post', dict(operation_key='mcp-coexist-post', document=document), company=COMPANY)
    identity = posted['deposit']['id']
    cid = client.company.list()['items'][0]['company_id']
    client.run('company new', dict(legal_name='Separate books', home_currency='USD', organization=ORGANIZATION), reason='Isolation witness')
    other = next(x['company_id'] for x in client.company.list()['items'] if x['company_id'] != cid)
    root = tmp_path / 'root'
    make_actor(root, 'read-outsider', company_role=(other, 'standard'))
    outsider = as_user(root, 'read-outsider').token.issue(label='Other company only')
    issued = client.token.issue(label='MCP coexistence')
    handle = start_serving(root, client_version(), bind='127.0.0.1:8765', secure_cookies=False)
    hosted = Hosted(handle, root, '', cid, issued, '', {})
    server = live.__wrapped__(hosted)
    try:
        url = next(server)
        # A real identity in the wrong company is indistinguishable from missing.
        for verb, args in [('show', {}), ('items', {'kind':'additional'})]:
            wrong = hosted.call('deposit.'+verb, dict(deposit=identity, **args), company=other)
            assert wrong.status_code == 404 and wrong.json()['code'] == 'E_RECORD_NOT_FOUND'
            denied = hosted.call('deposit.'+verb, dict(deposit=identity, **args), company=cid,
                headers={'Authorization':'Bearer '+outsider['secret']})
            assert denied.status_code >= 400 and identity not in denied.text

        async def journey():
            checkout = Path(__file__).resolve().parents[1]
            params = StdioServerParameters(command=sys.executable, args=['-c','from bookflow.bootstrap import main; main()','mcp','--url',url],
                cwd=str(checkout), env={'BOOKFLOW_TOKEN':issued['secret'], 'BOOKFLOW_COMPANY':cid,
                    'BOOKFLOW_DATA_ROOT':str(tmp_path/'absent'), 'PYTHONPATH':str(checkout/'src')+':'+str(checkout)})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.discover()
                    async def run(verb, args):
                        reply = await session.call_tool('bookflow_run', {'command':'deposit '+verb, 'input':args, 'company':cid})
                        assert not reply.is_error, reply.structured_content
                        return reply.structured_content
                    shown = await run('show', dict(deposit=identity))
                    assert shown['current']['version'] == 1 and shown['totals']['bank_total']['minor_units'] == 1234
                    rows = await run('items', dict(deposit=identity,kind='additional'))
                    assert rows['total_count'] == 1
                    def write(verb, args):
                        headers = {**hosted.bearer, 'X-Bookflow-Reason':'Correct or cancel saved deposit'}
                        preview = hosted.api.post(f'/companies/{cid}/commands/deposit.{verb}',
                            params={'dry_run':'true'}, json=args, headers=headers)
                        assert preview.status_code == 200, preview.text
                        return hosted.ok('deposit.'+verb, dict(args, dependency_guard=preview.json()['dependency_guard']),
                            company=cid, headers=headers)
                    corrected = write('update', dict(operation_key='mcp-coexist-update', deposit=identity,
                        expected_version=1, document=dict(document,number=posted['deposit']['number'],memo='Corrected memo',cash_back=None,custom_fields={},expected_custom_field_kinds={},sources=[])))
                    assert corrected['deposit']['version'] == 2
                    shown = await run('show', dict(deposit=identity))
                    assert shown['current']['version'] == 2 and shown['totals']['bank_total']['minor_units'] == 1234
                    write('void', dict(operation_key='mcp-coexist-void',deposit=identity,expected_version=2))
                    shown = await run('show', dict(deposit=identity))
                    assert shown['current']['status'] == 'voided' and shown['current']['effective_bank_total']['minor_units'] == 0
                    assert (await run('items', dict(deposit=identity,kind='additional')))['total_count'] == 1
        asyncio.run(journey())
    finally:
        server.close()
        handle.stop()


def test_deposit_catalog_and_publication_inventory_keep_both_owners():
    from bookflow.core import registry, publication_inventory
    from bookflow.company import deposit_public_authority
    from bookflow.hub import permission_catalog as catalog
    registry.load_all()
    deposit_public_authority.conform()
    assert registry.get('deposit query') is None
    commands = {x.name:x for x in catalog.FROZEN_COMMANDS if x.name.startswith('deposit ')}
    actions = {x.key:x for x in catalog.FROZEN_COMPANY_ACTIONS if x.key.startswith('deposit ')}
    assert commands.keys() == actions.keys() == {'deposit '+x for x in ('show','items','post','sources','update','void')}
    for name in commands:
        cmd = registry.get(name)
        assert (commands[name].capability, commands[name].threshold) == (cmd.capability,cmd.required_role)
        assert actions[name].requirements == (catalog.Requirement(cmd.capability,cmd.required_role),)
        policy = publication_inventory.policy(cmd)
        assert policy == ('reader_bound_public_detail_proof' if name in ('deposit show','deposit items')
                          else 'selected_company_and_current_resources')
