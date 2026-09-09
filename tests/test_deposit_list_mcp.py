"""Actual MCP paging-to-show over saved deposits, with its latency measured and reported.

The number is recorded, not gated: this is one warm sample of a small population
on this machine, and a threshold built from it would be a claim the sample cannot
support.
"""
import json
import os
from pathlib import Path
import shlex
import sys
from time import perf_counter

import pytest

import bookflow
from tests.test_row3_host import hosted, live  # noqa: F401

POPULATION = 7


def _binary(tmp_path):
    """The MCP server process, running the code under test rather than an installed copy."""
    override = os.environ.get('BOOKFLOW_MCP_TEST_BINARY')
    if override:
        return override
    source = str(Path(bookflow.__file__).resolve().parent.parent)
    program = f'import sys; sys.path.insert(0, {source!r}); from bookflow.bootstrap import main; main()'
    shim = tmp_path / 'bookflow-under-test'
    shim.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -c {shlex.quote(program)} "$@"\n')
    shim.chmod(0o700)
    return str(shim)


@pytest.mark.timeout(300)
def test_actual_mcp_query_next_page_and_show(hosted, live, tmp_path):
    pytest.importorskip('mcp')
    import anyio
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    company = hosted.company_id
    bank = hosted.ok('account.create', dict(name='MCP list bank', type='bank'), company=company)['id']
    income = hosted.ok('account.create', dict(name='MCP list income', type='income'), company=company)['id']
    payer = hosted.ok('customer.create', dict(name='MCP list payer'), company=company)['id']
    for index in range(POPULATION):
        hosted.ok('deposit.post', dict(operation_key=f'mcp-list-{index}', document=dict(mode='inline',
            deposit_to=bank, date=f'2026-06-{index + 1:02d}', number=f'MCP-{index:03d}',
            memo=f'Counter deposit {index}',
            additional=[dict(received_from=dict(kind='customer', id=payer), from_account=income,
                             amount=f'{index + 1}0.00')])), company=company)
    measured = {}
    for _ in range(2):                       # the same read over HTTP, so the MCP figure has a scale
        started = perf_counter()
        hosted.ok('deposit.query', dict(page=dict(limit=3)), company=company)
        measured.setdefault('http_query_s', []).append(round(perf_counter() - started, 4))

    async def witness():
        params = StdioServerParameters(command=_binary(tmp_path), args=['mcp', '--url', live,
            '--client-name', 'deposit-list-mcp-witness'],
            env={'BOOKFLOW_TOKEN': hosted.secret, 'BOOKFLOW_COMPANY': company,
                 'BOOKFLOW_DATA_ROOT': str(tmp_path / 'absent')}, cwd=str(tmp_path))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                start = perf_counter()
                await session.discover()
                measured['discover_s'] = perf_counter() - start

                async def run(command, data):
                    began = perf_counter()
                    reply = await session.call_tool('bookflow_run', dict(command=command, input=data))
                    assert not reply.is_error, reply
                    return reply.structured_content, perf_counter() - began

                # A small company-scoped read through the same session, so the deposit
                # figures below separate transport cost from the deposit read itself.
                _, measured['baseline_account_list_s'] = await run('account list', {})
                first, measured['query_first_page_s'] = await run('deposit query', dict(page=dict(limit=3)))
                assert first['total_count'] == POPULATION and len(first['items']) == 3
                paging = perf_counter()
                second, measured['query_next_page_s'] = await run(
                    'deposit query', dict(page=dict(limit=3, cursor=first['next_cursor'])))
                assert len(second['items']) == 3
                # The one assertion the measurement must not be allowed to obscure.
                assert json.dumps([second['totals'], second['effective_bank_total'], second['total_count']],
                                  sort_keys=True) == json.dumps(
                    [first['totals'], first['effective_bank_total'], first['total_count']], sort_keys=True)
                row = second['items'][0]
                shown, measured['show_s'] = await run('deposit show', dict(deposit=row['current']['deposit_id']))
                measured['paging_to_show_s'] = perf_counter() - paging
                assert shown['deposit_id'] == row['current']['deposit_id']
                assert shown['totals'] == row['totals']
                assert shown['current']['effective_bank_total'] == row['current']['effective_bank_total']
                assert shown['selected']['number'] == row['selected']['number']

    anyio.run(witness)
    measured['population'] = POPULATION
    (tmp_path / 'mcp-times.json').write_text(json.dumps(measured, indent=2) + '\n')
    print('MCP deposit list timings:', json.dumps(measured), flush=True)
