"""Complete weekly agent activity through SDK, CLI, Python, HTTP and Chrome."""

from contextlib import AsyncExitStack
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import anyio
import pytest

from bookflow.core import clock
from bookflow.core.config import Config
from bookflow.hub import schema as h
from tests.conftest import make_actor
from tests.test_row3_host import hosted, live, PASSWORD
from tests.test_row7_credentials import writer
from tests.test_row5_browser_acceptance import CHROME, _Cdp


@pytest.mark.timeout(180)
@pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
def test_weekly_agent_principal_filters_pagination_and_dst_on_all_surfaces(hosted, live, tmp_path, monkeypatch):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    principal = Config.load(hosted.root / 'config.toml').user_table(hosted.login)['user_id']
    other = make_actor(hosted.root, 'weekly-other', company_role=(hosted.company_id, 'owner'))
    agent = make_actor(hosted.root, 'weekly-shared-agent', kind='agent', owner_user_id=principal,
                       company_role=(hosted.company_id, 'owner'))
    second = make_actor(hosted.root, 'weekly-second-agent', kind='agent', owner_user_id=other,
                        company_role=(hosted.company_id, 'owner'))
    with writer(hosted.root) as db:
        db.conn.execute(h.users.update().where(h.users.c.id == principal).values(timezone='Europe/Berlin'))
        for actor in (agent, second):
            db.conn.execute(h.agent_authority.insert().values(agent_user_id=actor, epoch=1))
        for actor, person in ((agent, principal), (agent, other), (second, other)):
            db.conn.execute(h.agent_principals.insert().values(agent_user_id=actor, principal_user_id=person,
                assigned_by=principal, assigned_at=clock.now_iso()))
    tokens = {key: hosted.ok('token.issue', {'user': actor, 'principal': person, 'label': 'Weekly fixture'})['secret']
              for key, actor, person in [('a1', agent, principal), ('a2', agent, other), ('b2', second, other)]}
    current = [datetime.fromisoformat('2027-03-21T22:59:59+00:00')]
    monkeypatch.setattr(clock, 'now', lambda: current[0])
    cases = [
        ('2027-03-21T22:59:59+00:00', 'a1', 'before'),
        ('2027-03-21T23:00:00+00:00', 'a1', 'start'),
        ('2027-03-21T23:01:00+00:00', 'a2', 'other-principal'),
        ('2027-03-21T23:02:00+00:00', 'human', 'human'),
        ('2027-03-21T23:03:00+00:00', 'a1-http', 'same-agent-http'),
        ('2027-03-28T00:59:59+00:00', 'a1', 'before-dst'),
        ('2027-03-28T01:00:00+00:00', 'a2', 'after-dst'),
        ('2027-03-28T01:00:01+00:00', 'b2', 'other-agent'),
        ('2027-03-28T21:59:59+00:00', 'a1', 'last'),
        ('2027-03-28T22:00:00+00:00', 'a1', 'after'),
    ]
    binary = os.environ.get('BOOKFLOW_MCP_TEST_BINARY', str(Path(sys.executable).with_name('bookflow')))
    def params(secret, label):
        return StdioServerParameters(command=binary, args=['mcp', '--url', live, '--client-name', label],
            env={'BOOKFLOW_TOKEN': secret, 'BOOKFLOW_COMPANY': hosted.company_id,
                 'BOOKFLOW_DATA_ROOT': str(tmp_path / 'absent')}, cwd=str(tmp_path))

    async def seed():
        async with AsyncExitStack() as stack:
            sessions = {}
            for key in ('a1', 'a2'):
                read, write = await stack.enter_async_context(stdio_client(params(tokens[key], 'weekly-mcp-agent')))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.discover()
                sessions[key] = session
            for at, who, label in cases:
                current[0] = datetime.fromisoformat(at)
                raw = {'fax': 'weekly-' + label}
                reason = 'Weekly witness ' + label
                if who in sessions:
                    reply = await sessions[who].call_tool('bookflow_run', {'command': 'company update', 'input': raw, 'reason': reason})
                    assert not reply.is_error, 'Authenticated weekly agent write failed'
                else:
                    secret = hosted.secret if who == 'human' else tokens['a1' if who == 'a1-http' else who]
                    response = hosted.call('company.update', raw, company=hosted.company_id,
                        headers={'Authorization': 'Bearer ' + secret, 'X-Bookflow-Reason': reason})
                    assert response.status_code == 200
    anyio.run(seed)

    with sqlite3.connect((hosted.root / 'hub.db').as_uri() + '?mode=ro', uri=True) as db:
        relative = db.execute('SELECT path FROM companies WHERE id=?', (hosted.company_id,)).fetchone()[0]
    with sqlite3.connect((hosted.root / relative / 'company.db').as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute("SELECT * FROM audit_events WHERE reason LIKE 'Weekly witness %' ORDER BY seq DESC")]
    assert len(rows) == 10
    since, until = '2027-03-22T00:00:00+01:00', '2027-03-29T00:00:00+02:00'
    start, end = datetime.fromisoformat(since), datetime.fromisoformat(until)
    assert (end.timestamp() - start.timestamp()) / 3600 == 167
    variants = [{}, {'via': 'mcp'}, {'principal': principal}, {'principal': other}, {'actor': agent},
                {'principal': principal, 'via': 'mcp', 'command': 'company update'}]
    filters = [{'since': since, 'until': until, 'kind': 'agent', 'limit': 2, **variant} for variant in variants]
    expected = []
    for variant in variants:
        expected.append([row['id'] for row in rows if start <= datetime.fromisoformat(row['at'].replace('Z', '+00:00')) < end
            and row['actor_kind'] == 'agent' and all(row[{'via': 'interface', 'principal': 'on_behalf_of', 'actor': 'actor_id'}.get(key, key)] == value
                                                   for key, value in variant.items())])
    assert [len(ids) for ids in expected] == [7, 5, 4, 3, 6, 3]

    async def mcp_queries():
        async with stdio_client(params(hosted.secret, 'weekly-owner')) as (read, write):
            async with ClientSession(read, write) as session:
                await session.discover()
                for options, ids in zip(filters, expected):
                    found, before = [], None
                    while True:
                        reply = await session.call_tool('bookflow_run', {'command': 'audit list',
                            'input': {**options, **({'before': before} if before else {})}})
                        assert not reply.is_error
                        page = reply.structured_content
                        found.extend(row['id'] for row in page['items'])
                        before = page['next_before']
                        if before is None:
                            break
                    assert found == ids
    anyio.run(mcp_queries)

    for options, ids in zip(filters, expected):
        found, before = [], None
        while True:
            page = hosted.ok('audit.list', {**options, **({'before': before} if before else {})}, company=hosted.company_id)
            found.extend(row['id'] for row in page['items'])
            for row in page['items']:
                original = next(item for item in rows if item['id'] == row['id'])
                instant = datetime.fromisoformat(original['at'].replace('Z', '+00:00'))
                assert datetime.fromisoformat(row['at']).utcoffset() == instant.astimezone(ZoneInfo('Europe/Berlin')).utcoffset()
                assert row['actor_name'] and row['on_behalf_of_name']
            before = page['next_before']
            if before is None:
                break
        assert found == ids
    # The actual installed CLI and Python client forward through the shared local
    # host using the owner's OS identity; their filters are the same contract.
    options, ids = filters[0], expected[0]
    for surface in ('cli', 'python'):
        found, before = [], None
        while True:
            query = {**options, **({'before': before} if before else {})}
            if surface == 'cli':
                args = [binary, 'audit', 'list', '--company', hosted.company_id, '--json']
                for key, value in query.items():
                    args += ['--' + key.replace('_', '-'), str(value)]
            else:
                args = [str(Path(binary).with_name('python')), '-c',
                    'import bookflow,json,os; c=bookflow.connect(data_root=os.environ["BOOKFLOW_DATA_ROOT"]); print(json.dumps(c.run("audit list", json.loads(os.environ["WEEK_QUERY"]), company=os.environ["BOOKFLOW_COMPANY"])))']
            result = subprocess.run(args, capture_output=True, text=True, cwd=tmp_path,
                env={**os.environ, 'BOOKFLOW_DATA_ROOT': str(hosted.root), 'BOOKFLOW_COMPANY': hosted.company_id,
                     'WEEK_QUERY': json.dumps(query)}, timeout=20)
            assert result.returncode == 0, result.stderr
            page = json.loads(result.stdout)
            found.extend(row['id'] for row in page['items'])
            before = page['next_before']
            if before is None:
                break
        assert found == ids

    browser = _Cdp(tmp_path / 'weekly-chrome')
    try:
        browser.navigate(live + '/login')
        browser.evaluate(f'''(() => {{document.querySelector('[name=username]').value={json.dumps(hosted.login)};
            document.querySelector('[name=password]').value={json.dumps(PASSWORD)};
            document.querySelector('form[hx-post="/login"]').requestSubmit();}})()''')
        browser.wait_for('!!document.querySelector(".group-grid")')
        browser.navigate(live + f'/c/{hosted.company_id}/audit')
        browser.evaluate(f'''(() => {{const values={json.dumps(filters[0])};
            for(const [name,value] of Object.entries(values)) document.querySelector(`[name="${{name}}"]`).value=value;
            document.querySelector('main form').requestSubmit();}})()''')
        browser.wait_for('new URL(location.href).searchParams.get("limit") === "2"')
        found = []
        while True:
            found.extend(browser.evaluate('Array.from(document.querySelectorAll(".table-wrap table tr td:first-child a")).map(a=>a.href.split("/").pop())'))
            older = browser.evaluate('Array.from(document.querySelectorAll("a")).find(a=>a.textContent.trim()==="older")?.href || null')
            if older is None:
                break
            parsed = parse_qs(urlsplit(older).query)
            assert parsed['since'] == [since] and parsed['until'] == [until]
            browser.navigate(older)
        assert found == expected[0]
    finally:
        browser.close()
