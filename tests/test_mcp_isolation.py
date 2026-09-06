"""Actual stdio isolation across siblings, organizations and intent owners."""

from contextlib import AsyncExitStack
import json
import os
from pathlib import Path
import sqlite3
import sys

import anyio
import pytest

from bookflow.core import clock
from bookflow.hub import schema as h
from tests.conftest import make_actor
from tests.test_row3_host import hosted, live
from tests.test_row7_credentials import writer

GHOST = '01ARZ3NDEKTSV4RRFFQ69G5FAV'


@pytest.mark.timeout(120)
def test_actual_mcp_siblings_foreign_org_readonly_and_intent_ownership(hosted, live, tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    org = hosted.info()['organization_id']
    sibling = hosted.ok('company.new', {'legal_name': 'Private sibling 9381', 'home_currency': 'USD', 'organization': org})['company_id']
    foreign_org = hosted.ok('organization.new', {'name': 'Private organization 4729'})['organization_id']
    foreign = hosted.ok('company.new', {'legal_name': 'Private foreign 2857', 'home_currency': 'USD', 'organization': foreign_org})['company_id']
    principal = make_actor(hosted.root, 'limited-principal', company_role=(hosted.company_id, 'owner'))
    agent = make_actor(hosted.root, 'limited-agent', kind='agent', owner_user_id=principal,
                       company_role=(hosted.company_id, 'owner'))
    readonly = make_actor(hosted.root, 'limited-reader', company_role=(hosted.company_id, 'readonly'))
    with writer(hosted.root) as db:
        db.conn.execute(h.agent_authority.insert().values(agent_user_id=agent, epoch=1))
        db.conn.execute(h.agent_principals.insert().values(agent_user_id=agent, principal_user_id=principal,
            assigned_by=principal, assigned_at=clock.now_iso()))
    tokens = {name: hosted.ok('token.issue', {'user': actor, 'label': 'Isolation fixture', **options})['secret']
              for name, actor, options in [('agent', agent, {'principal': principal}), ('reader', readonly, {})]}
    binary = os.environ.get('BOOKFLOW_MCP_TEST_BINARY', str(Path(sys.executable).with_name('bookflow')))

    def business_snapshot():
        with sqlite3.connect((hosted.root / 'hub.db').as_uri() + '?mode=ro', uri=True) as db:
            paths = db.execute('SELECT id,path FROM companies ORDER BY id').fetchall()
        result = {}
        for company, relative in paths:
            with sqlite3.connect((hosted.root / relative / 'company.db').as_uri() + '?mode=ro', uri=True) as db:
                result[company] = (db.execute('SELECT * FROM company_info').fetchall(),
                                   db.execute('SELECT * FROM audit_events ORDER BY seq').fetchall())
        return result

    async def witness():
        async with AsyncExitStack() as stack:
            sessions = {}
            for name, secret in {'owner': hosted.secret, **tokens}.items():
                params = StdioServerParameters(command=binary, args=['mcp', '--url', live],
                    env={'BOOKFLOW_TOKEN': secret, 'BOOKFLOW_DATA_ROOT': str(tmp_path / 'absent')}, cwd=str(tmp_path))
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.discover()
                sessions[name] = session

            async def call(name, command, raw=None, **context):
                return await sessions[name].call_tool('bookflow_run', {'command': command, 'input': raw or {}, **context})

            # Catalog metadata is static, while company/organization lists are protected data.
            catalogs = [await session.call_tool('bookflow_list_commands', {'limit': 200}) for session in sessions.values()]
            assert all(not item.is_error for item in catalogs)
            assert all(item.structured_content == catalogs[0].structured_content for item in catalogs)
            for name in tokens:
                listing = await call(name, 'company list')
                assert not listing.is_error
                assert [row['company_id'] for row in listing.structured_content['items']] == [hosted.company_id]
                organizations = await call(name, 'organization list')
                assert not organizations.is_error
                assert [row['organization_id'] for row in organizations.structured_content['items']] == [org]
                for command, raw in [('company show', {}), ('account list', {}),
                                     ('company update', {'fax': 'must never persist'})]:
                    before = business_snapshot()
                    errors = []
                    for company in (sibling, foreign, GHOST):
                        context = {'company': company, **({'reason': 'Rejected isolation witness'} if command.endswith('update') else {})}
                        denied = await call(name, command, raw, **context)
                        assert denied.is_error
                        http = hosted.call(command.replace(' ', '.'), raw, company=company,
                            headers={'Authorization': 'Bearer ' + tokens[name],
                                     **({'X-Bookflow-Reason': context['reason']} if 'reason' in context else {})})
                        assert http.status_code == 404
                        assert denied.structured_content == http.json()
                        errors.append(denied.structured_content)
                        encoded = json.dumps(denied.structured_content)
                        for protected in ('Private sibling', 'Private foreign', 'Private organization', str(hosted.root)):
                            assert protected not in encoded
                    # Only the supplied identifier may differ; existing hidden targets reveal no extra fields.
                    normalized = [json.dumps(error, sort_keys=True).replace(company, '<supplied>')
                                  for error, company in zip(errors, (sibling, foreign, GHOST))]
                    assert len(set(normalized)) == 1
                    assert business_snapshot() == before

            before = business_snapshot()
            denied = await call('reader', 'company update', {'fax': 'readonly must not persist'},
                                company=hosted.company_id, reason='Readonly witness')
            http = hosted.call('company.update', {'fax': 'readonly must not persist'}, company=hosted.company_id,
                               headers={'Authorization': 'Bearer ' + tokens['reader'], 'X-Bookflow-Reason': 'Readonly witness'})
            assert denied.is_error and http.status_code == 403
            assert denied.structured_content == http.json()
            assert business_snapshot() == before

            prepared = await call('agent', 'company update', {'fax': 'unsubmitted isolated intent'},
                company=hosted.company_id, reason='Intent ownership witness', transport={'prepare_only': True})
            assert not prepared.is_error
            ref = prepared.structured_content['operation_ref']
            for name in ('owner', 'reader'):
                for key in ('input_ref', 'operation_ref'):
                    rejected = await sessions[name].call_tool('bookflow_run', {key: ref, 'action': 'execute'})
                    assert rejected.is_error
                    assert 'unsubmitted isolated intent' not in json.dumps(rejected.structured_content)
            released = await sessions['agent'].call_tool('bookflow_run', {'operation_ref': ref, 'action': 'release'})
            assert not released.is_error
            assert business_snapshot() == before
    anyio.run(witness)
