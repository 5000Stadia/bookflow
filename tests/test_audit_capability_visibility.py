"""The audit trail and activity keep a member's explicit capability denies (R141).

A member whose company membership denies `customer-work` (estimates, work orders, recorded time)
and `ledger.read` (purchase orders and every other transaction) reads the demo company's audit
trail over the real HTTP host and a real MCP child bound to it. Neither the estimate nor the
purchase order events come back from `audit list`, `audit tail` or `audit show`, their record
types filter to nothing without a next cursor, and `activity` refuses the estimate before looking
it up, so an existing estimate reads the same as an absent one. An unrestricted member of the same
company, over the same two transports, sees all of it.
"""
from contextlib import AsyncExitStack

import anyio
import pytest

from tests import provenance
from tests.test_row3_host import hosted, live  # noqa: F401  (fixtures)

pytestmark = pytest.mark.timeout(600)

GHOST = '01ARZ3NDEKTSV4RRFFQ69G5FAV'
PASSWORD = 'correct horse battery staple 9'


def _member(admin, company, username, denies):
    admin('user add', {'username': username, 'display_name': username.title(), 'password': PASSWORD})
    admin('membership grant', {'user': username, 'company': company, 'role': 'standard',
                               'expected_version': 0, 'denies': denies})
    return admin('token issue', {'user': username, 'label': username + ' reader'})['secret']


def _http(hosted, secret, company):
    def call(name, body):
        response = hosted.api.post(f'/companies/{company}/commands/{name.replace(" ", ".")}', json=body,
                                   headers={'Authorization': f'Bearer {secret}'})
        return response.status_code < 400, response.json()
    return call


async def _mcp(stack, url, secret, company, directory):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    parameters = StdioServerParameters(
        command=provenance.launcher(), args=['mcp', '--url', url], cwd=str(directory),
        env=provenance.child_env(BOOKFLOW_TOKEN=secret, BOOKFLOW_COMPANY=company,
                                 BOOKFLOW_DATA_ROOT=str(directory / 'absent')))
    read, write = await stack.enter_async_context(stdio_client(parameters))
    session = await stack.enter_async_context(ClientSession(read, write))
    with anyio.fail_after(provenance.HANDSHAKE_SECONDS):
        await session.initialize()

    async def call(name, body):
        reply = await session.call_tool('bookflow_run', {'command': name, 'input': body})
        return not reply.is_error, reply.structured_content
    return call


def test_denied_estimates_and_purchase_orders_stay_out_of_audit_and_activity(hosted, live, tmp_path):  # noqa: F811
    pytest.importorskip('mcp')
    company = hosted.company_id
    admin = lambda name, body, **kw: hosted.ok(name.replace(' ', '.'), body, **kw)  # the installer, over HTTP
    first = lambda record_type: admin('audit list', {'record_type': record_type, 'limit': 1}, company=company)['items'][0]
    estimate_event, order_event = first('work_document'), first('purchase_order')
    estimate = admin('audit show', {'event': estimate_event['id']}, company=company)['entries'][0]
    assert estimate['record_type'] == 'work_document'
    hidden = {estimate_event['id'], order_event['id']}
    secrets = {'restricted': _member(admin, company, 'restricted', ['customer-work', 'ledger.read']),
               'unrestricted': _member(admin, company, 'unrestricted', [])}

    async def witness():
        async with AsyncExitStack() as stack:
            readers = {}
            for who, secret in secrets.items():
                readers[(who, 'http')] = _http(hosted, secret, company)
                readers[(who, 'mcp')] = await _mcp(stack, live, secret, company, tmp_path)
            seen = {}
            for (who, surface), call in readers.items():
                where = (who, surface)

                async def run(name, body, ok=True):
                    result = call(name, body)
                    passed, document = await result if hasattr(result, '__await__') else result
                    assert passed is ok, (where, name, document)
                    return document

                page = await run('audit list', {'limit': 1000})
                ids = {item['id'] for item in page['items']}
                assert page['count'] == len(page['items']) and page['next_before'] is None, where
                tail = await run('audit tail', {'after': 0, 'limit': 1000})
                assert {item['id'] for item in tail['items']} == ids, where
                scan = await run('audit tail', {'after': 0, 'limit': 1000, 'scan_limit': 1000})
                assert scan['scanned_count'] == len(ids) and not scan['scan_more'], where
                seen[where] = ids
                if who == 'restricted':
                    assert not ids & hidden, where
                    for record_type in ('work_document', 'purchase_order', 'transaction'):
                        filtered = await run('audit list', {'record_type': record_type, 'limit': 5})
                        assert filtered == {'items': [], 'count': 0, 'next_before': None}, (where, record_type)
                    absent = await run('audit show', {'event': GHOST}, ok=False)
                    for event in sorted(hidden):
                        refused = await run('audit show', {'event': event}, ok=False)
                        # The same answer as for an event that does not exist.
                        assert refused['code'] == absent['code'] == 'E_EVENT_NOT_FOUND', where
                        assert refused['message'] == absent['message'], where
                    for record_id in (estimate['record_id'], GHOST):
                        refused = await run('activity', {'record_type': 'work_document', 'record_id': record_id}, ok=False)
                        assert refused['code'] == 'E_PERMISSION', where
                    # The newest cursor is the newest event this reader may see.
                    newest = await run('audit tail', {'limit': 1})
                    assert newest['high_water'] == max(item['seq'] for item in page['items']), where
                else:
                    assert hidden <= ids, where
                    for event in hidden:
                        assert (await run('audit show', {'event': event}))['entries'], where
                    history = await run('activity', {'record_type': 'work_document', 'record_id': estimate['record_id']})
                    assert estimate_event['id'] in {item['event_id'] for item in history['items']}, where
            # The same trail over both transports; the restricted one is strictly smaller.
            assert seen[('restricted', 'http')] == seen[('restricted', 'mcp')]
            assert seen[('unrestricted', 'http')] == seen[('unrestricted', 'mcp')]
            assert seen[('restricted', 'http')] < seen[('unrestricted', 'http')]

    anyio.run(witness)


def test_unknown_record_types_need_every_read_capability():
    from bookflow.company import audit_visibility as v
    assert v.record_capabilities('work_document') == {'customer-work'}
    assert v.record_capabilities('purchase_order') == {'ledger.read'}
    assert v.record_capabilities('note') == {'note'}
    assert v.record_capabilities('directive') == {'directive'}
    assert v.record_capabilities('sales_rep') == {'sales-rep'}
    assert v.record_capabilities('work_billing_allocation') == {'customer-work', 'ledger.read'}
    assert v.record_capabilities('some_future_record') == v.CAPABILITIES
    # No two families claim one record type, so the SQL and Python readings agree.
    prefixes = [p for p, _ in v.PREFIXES]
    assert not [(a, b) for a in prefixes for b in prefixes if a != b and a.startswith(b)]
    # The catalog records exactly the requirements this filter asks for.
    from bookflow.hub import permission_audit_visibility_catalog as catalog
    assert set(catalog.READ_CAPABILITIES) == v.CAPABILITIES


def test_directive_text_needs_the_directive_capability(root):
    import bookflow
    from tests.conftest import as_user
    from bookflow.core.config import Config
    admin = bookflow.connect(data_root=str(root))
    company = admin.company.list()['items'][0]['company_id']
    directive = admin.run('directive add', {'text': 'Post every receipt the day it arrives'}, company=company)['directive']
    customer = admin.run('customer create', {'name': 'Directive witness'}, company=company,
                         reason='Directed write', directive=directive['code'])['id']
    readers = {}
    for username, denies in (('nodirective', ['directive']), ('reader', [])):
        user = admin.run('user add', {'username': username, 'display_name': username.title(), 'password': PASSWORD})['user_id']
        admin.run('membership grant', {'user': username, 'company': company, 'role': 'standard',
                                       'expected_version': 0, 'denies': denies})
        config = Config.load(root / 'config.toml')
        config.set_user(username, user)
        config.save()
        readers[username] = as_user(root, username)
    for username, reader in readers.items():
        event = reader.audit.list(company=company, record_type='customer', record_id=customer, limit=1)['items'][0]
        shown = reader.audit.show(company=company, event=event['id'])
        for document in (event, shown):
            if username == 'reader':
                assert document['directive_text'] == 'Post every receipt the day it arrives'
                assert document['directive_code'] == directive['code']
            else:
                # The customer write stays visible; the instruction behind it does not.
                assert (document['directive_id'], document['directive_code'], document['directive_text']) == (None, None, None)
        directives = reader.audit.list(company=company, record_type='directive', limit=5)['items']
        assert bool(directives) is (username == 'reader')
