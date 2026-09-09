"""Public deposit details on four real surfaces, reached the way a user reaches them.

Two obligations meet here. The first is parity: native, CLI, HTTP and a real
stdio MCP client run the same registered reads over identical copies of one
produced company and must return the same document. Only the observation
instants may move; every disclosed business fact, reference, total, count,
fingerprint and cursor must be identical, so the comparison replaces exactly
those instants and compares everything else literally.

The second is navigation. A fresh MCP client is told a company and nothing
else. It finds the bank account in an ordinary list, reads the ordinary
register, recognizes a deposit row, and from that row's transaction id reads the
deposit summary, all three composition kinds and their continuation pages. Every
identifier it sends is one an earlier registered result gave it: the guard below
fails the test if any request carries an identity the client was handed rather
than discovered, which is what makes the receipt identities on the sources page
evidence rather than decoration.
"""
import json
import re
import shutil
from datetime import datetime

import anyio
import pytest

from tests import deposit_public_support as support
from tests.mcp_matrix_support import Matrix

COMMANDS = frozenset(('deposit show', 'deposit items'))

SURFACES = ('python', 'cli', 'http', 'mcp')
# The two freshly observed instants. Everything else in these documents is a
# captured or current business fact and must agree across surfaces exactly.
OBSERVATION = ('current_observed_at', 'knowledge_observed_at')
ULID = re.compile(r'\b[0-9A-HJKMNP-TV-Z]{26}\b')
STAMP = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d')
BANK = 'Public detail bank'
MIXED = 'Public detail memo'


def instant(stamp):
    """One observation instant, parsed rather than compared as text."""
    return datetime.fromisoformat(stamp)


def at_one_instant(document):
    """The document with only its observation instants replaced, and those instants."""
    stamps = []

    def visit(value):
        if isinstance(value, dict):
            kept = {}
            for key, item in value.items():
                if key in OBSERVATION and isinstance(item, str):
                    stamps.append(item)
                    kept[key] = '<observed>'
                else:
                    kept[key] = visit(item)
            return kept
        if isinstance(value, (list, tuple)):
            return [visit(item) for item in value]
        return value

    return visit(document), stamps


@pytest.mark.timeout(900)
def test_deposit_details_four_surface_parity_and_register_discovered_navigation(
        public_deposit_world, tmp_path):
    world = public_deposit_world
    support.stop(world)  # the matrix opens its own hosts; the data root must be free
    baseline = tmp_path / 'baseline'
    shutil.copytree(world['root'], baseline)
    surfaces = tmp_path / 'surfaces'
    surfaces.mkdir()

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(baseline, surfaces)

            # A silently skipped MCP client would make every parity claim below
            # a claim about three surfaces. Prove the stdio session is live.
            tools = {tool.name for tool in (await matrix.mcp.list_tools()).tools}
            assert 'bookflow_run' in tools, tools

            # ---------------------------------------------------------------- discovery
            known: set[str] = set()

            async def discovered(command, raw, **context):
                unknown = set(ULID.findall(json.dumps(raw))) - known
                assert not unknown, ('briefed identity', command, sorted(unknown))
                document = await matrix.call('mcp', command, raw, **context)
                known.update(ULID.findall(json.dumps(document)))
                return document

            accounts = await discovered('account list', {})
            bank = [row for row in accounts['items'] if row['name'] == BANK]
            assert len(bank) == 1, [row['name'] for row in accounts['items']]

            register = await discovered('register query', dict(
                account=bank[0]['id'], date_from='2026-01-01', date_to='2026-12-31', limit=200))
            rows = [row for row in register['rows'] if row['category_label'] == 'Deposit']
            assert rows, register['rows']
            assert {row['transaction_type'] for row in rows} == {'deposit'}
            mixed = {row['transaction_id'] for row in rows if row['memo'] == MIXED}
            assert len(mixed) == 1, rows
            deposit = mixed.pop()

            detail = await discovered('deposit show', dict(deposit=deposit))
            assert detail['deposit_id'] == deposit
            assert detail['company_id'] == matrix.company
            assert detail['selected_is_current'] and detail['selected']['memo'] == MIXED
            assert detail['inspection'] == dict(purpose='inspection_only', history='complete',
                                                source_count=1)
            counts = detail['counts']
            assert counts == dict(sources=1, additional=2, cash_allocations=6, components=3)
            assert [link['revision_number'] for link in detail['revisions']] == [1]
            assert [link['selected'] and link['current'] for link in detail['revisions']] == [True]

            # The receipt identities are not in the briefing and are not in the
            # register: the client learns them only by reading the composition.
            before_sources = set(known)
            whole = {}
            for kind in ('sources', 'additional', 'cash_allocations'):
                whole[kind] = await discovered('deposit items', dict(deposit=deposit, kind=kind))
                assert whole[kind]['selected'] == detail['selected']['pin']
                assert whole[kind]['total_count'] == counts[kind]
                assert whole[kind]['next_cursor'] is None
                assert len(whole[kind]['items']) == counts[kind]
                # One page at a time, continuing on the returned cursor alone.
                collected, cursor, requests = [], None, 0
                while True:
                    page = dict(limit=1)
                    if cursor is not None:
                        page['cursor'] = cursor
                    got = await discovered('deposit items',
                                           dict(deposit=deposit, kind=kind, page=page))
                    requests += 1
                    assert got['fingerprint'] == whole[kind]['fingerprint']
                    assert got['total_count'] == counts[kind]
                    assert got['selected'] == whole[kind]['selected']
                    assert len(got['items']) == 1
                    collected += got['items']
                    cursor = got['next_cursor']
                    if cursor is None:
                        break
                assert requests == counts[kind]
                assert collected == list(whole[kind]['items'])

            source = whole['sources']['items'][0]
            assert source['row'] == 'source'
            assert source['source']['transaction_id'] not in before_sources, (
                'the receipt identity must come from the composition, not from a briefing')
            assert {row['row'] for row in whole['additional']['items']} == {'additional'}
            assert {row['row'] for row in whole['cash_allocations']['items']} == {'allocation'}
            assert {row['bucket'] for row in whole['cash_allocations']['items']} <= {
                'main_bank', 'cash_back', 'additional'}

            # ------------------------------------------------------------------ parity
            first = await matrix.call('python', 'deposit items',
                                      dict(deposit=deposit, kind='cash_allocations',
                                           page=dict(limit=2)))
            cursor = first['next_cursor']
            assert cursor, first

            requests = [
                ('deposit show', dict(deposit=deposit), False),
                ('deposit show', dict(deposit=deposit, revision_number=1), False),
                ('deposit items', dict(deposit=deposit, kind='sources'), False),
                ('deposit items', dict(deposit=deposit, kind='additional'), False),
                ('deposit items', dict(deposit=deposit, kind='cash_allocations'), False),
                ('deposit items', dict(deposit=deposit, kind='cash_allocations',
                                       page=dict(limit=2, cursor=cursor)), False),
                ('deposit show', dict(deposit=support.ABSENT), True),
                ('deposit items', dict(deposit=deposit, kind='sources',
                                       page=dict(cursor='not-a-cursor')), True),
            ]
            for command, raw, rejected in requests:
                documents = {}
                for surface in SURFACES:
                    documents[surface] = await matrix.call(surface, command, raw, rejected=rejected)
                compared = {surface: at_one_instant(document)
                            for surface, document in documents.items()}
                reference, stamps = compared['python']
                for surface in SURFACES:
                    assert compared[surface][0] == reference, (command, raw, surface)
                if rejected:
                    assert not stamps
                else:
                    every = [stamp for surface in SURFACES for stamp in compared[surface][1]]
                    assert every and all(STAMP.match(stamp) for stamp in every), every
                    # The observation instant is the one thing that legitimately
                    # moves between four separately executed reads.
                    assert len({tuple(compared[surface][1]) for surface in SURFACES}) == len(SURFACES)

            # Both rejections are named, not generic, and disclose nothing.
            malformed = documents  # the last request compared above
            assert malformed['python']['code'] == 'E_VALIDATION'
            assert malformed['python']['details'] == {'field': 'cursor'}
            absent = await matrix.call('mcp', 'deposit show', dict(deposit=support.ABSENT),
                                       rejected=True)
            assert absent['code'] == 'E_RECORD_NOT_FOUND' and absent['details'] == {}

            # Two reads of the same revision differ only by their instant.
            earlier, first_stamps = at_one_instant(
                await matrix.call('python', 'deposit show', dict(deposit=deposit)))
            later, second_stamps = at_one_instant(
                await matrix.call('python', 'deposit show', dict(deposit=deposit)))
            assert earlier == later
            assert len(first_stamps) == len(second_stamps) == 1
            assert instant(first_stamps[0]) <= instant(second_stamps[0])
        finally:
            await matrix.close()

    anyio.run(witness)
