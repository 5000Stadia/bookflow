"""G6 early ceiling: measured cost of the two public deposit detail reads.

Everything here is measured, never extrapolated. One company is produced through
the genuine deposit lifecycle, the fixed deposit is re-read at every population
the spec names, and each remaining cost dimension - composition size, deposit
revision depth, draft revision depth, inspection history growth and connected
root growth - is grown on its own and re-measured at the point it reaches.

Two clocks are kept apart and never mixed:

* **in-process** - the ordinary offline dispatch path with no host, no
  profiling hook and no instrumentation: ``bookflow.connect(...).run(...)``.
* **full MCP** - a real stdio MCP client subprocess talking to a real uvicorn
  server in front of the running host, which is the path the ceiling is stated
  against.

The ceiling this file asserts is the early one: first and continuation requests
under ten seconds through actual MCP, none reaching the transport deadline. The
blueprint's ten-thousand-deposit sub-hundred-millisecond target is a separate
gate and nothing here satisfies it.
"""
import json
import os
import statistics
import sys
import time
import types
from contextlib import AsyncExitStack
from pathlib import Path

import anyio
import pytest

import bookflow
from tests import deposit_public_support as support
from tests.test_service_sales_lifecycle import COMPANY, sale

# The one small fixture the owning plan recorded on the private path.
BASELINE_SHOW_MS = 695.9
BASELINE_ITEMS_MS = 539.6

CEILING_MS = 10_000.0
RECEIPTS = Path('/tmp/bfnav/g6')

UNRELATED = (1, 25, 100)
MIN_POPULATION = 100
EXTRA_CUSTOMERS = (250, 500)
CONNECTED_SOURCES = 20
DEPOSIT_REVISIONS = 10
DRAFT_REVISIONS = 11


# --------------------------------------------------------------- measurement

def sample(fn, reps):
    """Wall-clock milliseconds for one request, repeated, uninstrumented."""
    values = []
    for _ in range(reps):
        start = time.perf_counter()
        fn()
        values.append((time.perf_counter() - start) * 1000)
    return dict(reps=reps, median_ms=round(statistics.median(values), 1),
                mean_ms=round(statistics.fmean(values), 1), min_ms=round(min(values), 1),
                max_ms=round(max(values), 1), samples_ms=[round(v, 1) for v in values])


class Record:
    """Every measurement this run produced, in the order it was taken."""

    def __init__(self):
        self.rows = []

    def add(self, dimension, point, request, surface, stat, **extra):
        row = dict(dimension=dimension, point=point, request=request, surface=surface, **stat, **extra)
        self.rows.append(row)
        print('G6 ' + json.dumps({k: v for k, v in row.items() if k != 'samples_ms'}), flush=True)
        return row

    def find(self, dimension, point, request, surface='in_process'):
        for row in self.rows:
            if (row['dimension'], row['point'], row['request'], row['surface']) == (dimension, point, request, surface):
                return row
        raise AssertionError('no measurement for ' + repr((dimension, point, request, surface)))


# ------------------------------------------------------------------ producers

def company(seeded_template, tmp_path_factory):
    """A produced company with the masters every fixture deposit draws on."""
    root = support.make_root(seeded_template, tmp_path_factory, 'deposit-ceiling')
    client = bookflow.connect(data_root=str(root))
    world = dict(root=root, client=client, host=None, handle=None)
    world.update(support.masters(client))
    sales = sale.__wrapped__(client)
    world.update(customer=sales['customer'], income=sales['income'], item=sales['item'])
    world['cid'] = client.company.show(company=COMPANY)['company_id']
    world['party'] = dict(kind='customer', id=world['customer'])
    return world


def document(world, date, memo, rows=1, amount='5.00'):
    return dict(mode='inline', deposit_to=world['bank'], date=date, memo=memo,
                additional=[dict(received_from=world['party'], from_account=world['income'],
                                 amount=amount, memo='Row %d' % (index + 1)) for index in range(rows)])


def deposits_in(world):
    """How many deposit roots this company actually holds, counted not assumed."""
    import sqlite3
    path = world['root'] / 'organizations' / 'Demo Holdings LLC' / COMPANY / 'company.db'
    assert path.is_file(), path
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        return db.execute("SELECT COUNT(*) FROM transactions WHERE type='deposit'").fetchone()[0]


SOURCES = ('core/deposit_offline.py', 'core/deposit_request.py', 'core/publication_deposit.py',
           'core/publication.py', 'company/deposit_public_reads.py',
           'company/deposit_public_authority.py', 'company/deposit_queries.py',
           'company/deposit_read_facts.py', 'company/deposit_read_authority.py',
           'adapters/http/execution.py', 'adapters/mcp/delivery.py', 'adapters/mcp/transport.py')


def source_state():
    """Exactly which source these numbers describe; the tree has other writers."""
    import hashlib
    import bookflow.core as anchor
    package = Path(anchor.__file__).parent.parent
    return {name: hashlib.sha256((package / name).read_bytes()).hexdigest()[:16]
            if (package / name).is_file() else None for name in SOURCES}


def receipt(world, index):
    """One ordinary sales receipt into undeposited funds: a contributing root."""
    from tests.test_deposit_sources import uf
    posted = world['client'].run('sales-receipt post', dict(
        customer=world['customer'], deposit_to=uf(world['client']), payment_method=world['method'],
        date='2026-06-02', memo='Ceiling receipt %d' % index,
        lines=[dict(item=world['item'], quantity='1', unit_price='60')]), company=COMPANY)
    return dict(source=posted['id'], source_type='sales_receipt', expected_version=posted['version'])


def private(client, action):
    """One private company action through the owned financial witness bridge."""
    from tests.test_deposit_draft_financial import run_private
    with pytest.MonkeyPatch.context() as patch:
        return run_private.__wrapped__(client, patch)(action)


def draft_deposit(world, key, revisions):
    """A deposit posted from a draft that really carries `revisions` revisions.

    No deposit write command is registered for this: the draft is built through
    the same private lifecycle owner the fixture producer already uses.
    """
    from bookflow.company import deposit_drafts as drafts
    from bookflow.company import deposit_draft_models as dm
    client = world['client']
    created = private(client, lambda s, ctx: drafts.run(s, ctx, dm.DraftCreate(
        header=dm.HeaderPatch(date='2026-06-09', deposit_to=world['bank'],
                              memo='Draft %s' % key)), 'create'))
    state = private(client, lambda s, ctx: drafts.run(s, ctx, dm.DraftUpdate(
        draft=created.id, expected_version=created.version,
        set_additional=[dm.AdditionalPatch(received_from=world['party'], from_account=world['income'],
                                           amount='9.00', memo='Draft row')]), 'update'))
    for number in range(revisions - 2):
        state = private(client, lambda s, ctx, n=number: drafts.run(s, ctx, dm.DraftUpdate(
            draft=created.id, expected_version=state.version,
            header=dm.HeaderPatch(memo='Draft %s revision %d' % (key, n + 3))), 'update'))
    depth = private(client, lambda s, ctx: s.company.raw.execute(
        'SELECT COUNT(*) FROM deposit_draft_revisions WHERE draft_id=?', (created.id,)).fetchone()[0])
    posted = support.post(client, dict(operation_key=key, document=dict(
        mode='draft', draft=created.id, expected_version=state.version)))
    return posted.current.id, depth


# ------------------------------------------------------------------- surfaces

def offline_calls(world):
    """The ordinary offline dispatch path: no host, no hook, no instrumentation."""
    client, cid = world['client'], world['cid']

    def show(deposit, **rest):
        return client.run('deposit show', dict(deposit=deposit, **rest), company=cid)

    def items(deposit, kind, **page):
        return client.run('deposit items', dict(deposit=deposit, kind=kind, page=page), company=cid)
    return show, items


def detail_set(record, dimension, point, deposit, kind, limit, show, items, reps, **extra):
    """show, the first items page, and a continuation page of the same request."""
    record.add(dimension, point, 'deposit show', 'in_process',
               sample(lambda: show(deposit), reps), **extra)
    first = items(deposit, kind, limit=limit)
    record.add(dimension, point, 'deposit items first', 'in_process',
               sample(lambda: items(deposit, kind, limit=limit), reps),
               total_count=first['total_count'], **extra)
    if first['next_cursor'] is None:
        return first
    cursor = first['next_cursor']
    record.add(dimension, point, 'deposit items continuation', 'in_process',
               sample(lambda: items(deposit, kind, limit=limit, cursor=cursor), reps),
               total_count=first['total_count'], **extra)
    return first


async def through_mcp(world, record, plan, reps):
    """A real stdio MCP client subprocess against a real uvicorn-served host.

    Nothing here is simulated. If the client does not start this raises; a
    silent skip would be a false green for the only surface the ceiling names.
    """
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from bookflow.commands.host_cmds import start_serving
    from bookflow.core.context import client_version
    from tests.test_row3_host import live
    client, cid = world['client'], world['cid']
    issued = client.token.issue(label='G6 ceiling bearer')
    stack = AsyncExitStack()
    try:
        started = time.perf_counter()
        handle = start_serving(world['root'], client_version(), bind='127.0.0.1:8765', secure_cookies=False)
        stack.callback(handle.stop)
        server = live.__wrapped__(types.SimpleNamespace(handle=handle))
        url = next(server)
        stack.callback(server.close)
        binary = os.environ.get('BOOKFLOW_MCP_TEST_BINARY', str(Path(sys.executable).with_name('bookflow')))
        assert Path(binary).is_file(), 'the MCP client binary is required: ' + binary
        read, write = await stack.enter_async_context(stdio_client(StdioServerParameters(
            command=binary, args=['mcp', '--url', url], cwd=str(world['root'].parent),
            env={'BOOKFLOW_TOKEN': issued['secret'], 'BOOKFLOW_COMPANY': cid,
                 'BOOKFLOW_DATA_ROOT': str(world['root'].parent / 'absent'),
                 'PATH': os.environ.get('PATH', '')})))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.discover()
        startup_ms = round((time.perf_counter() - started) * 1000, 1)

        async def call(command, raw):
            start = time.perf_counter()
            reply = await session.call_tool('bookflow_run', dict(command=command, input=raw, company=cid))
            elapsed = (time.perf_counter() - start) * 1000
            return elapsed, reply.structured_content, bool(reply.is_error)

        async def measure(dimension, point, request, command, raw, **extra):
            """Errors are recorded, not raised: a refused request is a measurement."""
            values, document, failures = [], None, []
            for _ in range(reps):
                elapsed, document, failed = await call(command, raw)
                values.append(elapsed)
                if failed:
                    failures.append(document)
            stat = dict(reps=reps, median_ms=round(statistics.median(values), 1),
                        mean_ms=round(statistics.fmean(values), 1), min_ms=round(min(values), 1),
                        max_ms=round(max(values), 1), samples_ms=[round(v, 1) for v in values])
            record.add(dimension, point, request, 'full_mcp', stat, error=failures[0] if failures else None, **extra)
            return None if failures else document

        # A control on the same client, so an MCP number can be read as the cost
        # of this command rather than the cost of the transport.
        await measure('MCP transport control', 'an ordinary company-scoped read',
                      'company show', 'company show', {})

        for dimension, point, deposit, kind, limit in plan:
            cold, document, failed = await call('deposit show', dict(deposit=deposit))
            record.add(dimension, point, 'deposit show (cold, first call of the session)', 'full_mcp',
                       dict(reps=1, median_ms=round(cold, 1), mean_ms=round(cold, 1),
                            min_ms=round(cold, 1), max_ms=round(cold, 1), samples_ms=[round(cold, 1)]),
                       error=document if failed else None)
            await measure(dimension, point, 'deposit show', 'deposit show', dict(deposit=deposit))
            page = await measure(dimension, point, 'deposit items first', 'deposit items',
                                 dict(deposit=deposit, kind=kind, page=dict(limit=limit)))
            if page is None:
                continue
            record.rows[-1]['total_count'] = page['total_count']
            assert page['next_cursor'], (point, kind, 'a continuation request needs a cursor')
            await measure(dimension, point, 'deposit items continuation', 'deposit items',
                          dict(deposit=deposit, kind=kind,
                               page=dict(limit=limit, cursor=page['next_cursor'])),
                          total_count=page['total_count'])

        # Every timing above is uninstrumented. Only now, after they are all
        # recorded, count how many complete projections one MCP request costs.
        # This call's own duration is deliberately not reported as a timing.
        diagnostic = await counted(call, plan[0][2])
        return startup_ms, diagnostic
    finally:
        await stack.aclose()


async def counted(call, deposit):
    """How many complete deposit projections one MCP `deposit show` really runs."""
    from bookflow.company import deposit_public_reads as reads
    from bookflow.core import publication_deposit
    tally = dict(execute_detail=0, revalidate_proof=0, check_hosted=0, projections=0)
    originals = {}

    def wrap(module, name, key):
        originals[(module, name)] = getattr(module, name)

        def counting(*args, **rest):
            tally[key] += 1
            return originals[(module, name)](*args, **rest)
        setattr(module, name, counting)

    wrap(publication_deposit, 'execute_detail', 'execute_detail')
    wrap(publication_deposit, 'revalidate_proof', 'revalidate_proof')
    wrap(publication_deposit, 'check_hosted', 'check_hosted')
    wrap(reads, 'show', 'projections')
    wrap(reads, 'items', 'projections')
    try:
        elapsed, _, failed = await call('deposit show', dict(deposit=deposit))
        return dict(tally, instrumented_call_ms=round(elapsed, 1), refused=failed,
                    note='Counted after every reported timing; these milliseconds are '
                         'instrumented and are not one of the reported measurements.')
    finally:
        for (module, name), original in originals.items():
            setattr(module, name, original)


# ----------------------------------------------------------------- the ceiling

@pytest.mark.timeout(3600)
def test_public_deposit_detail_early_ceiling(_seeded_template, tmp_path_factory):
    from bookflow.adapters.mcp.limits import json_seconds
    deadline_ms = json_seconds() * 1000.0
    record = Record()
    world = company(_seeded_template, tmp_path_factory)
    client = world['client']
    show, items = offline_calls(world)

    # ---- the fixed deposit whose cost is followed across every population.
    fixed = support.post(client, dict(operation_key='ceiling-fixed',
                                      document=document(world, '2026-06-03', 'Fixed deposit', rows=4)))
    fixed = fixed.current.id
    show(fixed)  # one warm-up: imports and lazily loaded owners are not the measurement

    made = 0
    for target in UNRELATED:
        while made < target:
            made += 1
            support.post(client, dict(operation_key='ceiling-fill-%d' % made,
                                      document=document(world, '2026-06-04', 'Filler %d' % made)))
        detail_set(record, 'unrelated deposit population', '%d unrelated deposits' % target,
                   fixed, 'additional', 2, show, items, 5, deposits_in_company=deposits_in(world))

    # ---- composition size, measured against that same small deposit.
    support.wide(world)
    wide = world['wide']
    wide_page = detail_set(record, 'composition size', '403 cash allocation rows',
                           wide, 'cash_allocations', 200, show, items, 5)
    assert wide_page['total_count'] == support.WIDE_CELLS == 403, wide_page['total_count']
    small = detail_set(record, 'composition size', '4 cash allocation rows',
                       fixed, 'cash_allocations', 2, show, items, 5)
    assert small['total_count'] == 4, small['total_count']

    # ---- deposit revision depth: the same root, read at 1 then at 10 revisions.
    base = document(world, '2026-06-05', 'Revision base', rows=2, amount='11.00')
    posted = support.post(client, dict(operation_key='ceiling-revision-post', document=base))
    revised = posted.current.id
    detail_set(record, 'deposit revision depth', '1 revision', revised, 'additional', 1, show, items, 3)
    for number in range(DEPOSIT_REVISIONS - 1):
        changed = support.replacement(posted, base)
        changed['memo'] = 'Revision %d' % (number + 2)
        changed['additional'][0]['amount'] = '%d.00' % (12 + number)
        posted = support.update(client, dict(deposit=revised, expected_version=posted.current.version,
                                             operation_key='ceiling-revision-%d' % number,
                                             document=changed))
    assert posted.current.version == DEPOSIT_REVISIONS, posted.current.version
    detail_set(record, 'deposit revision depth', '%d revisions' % DEPOSIT_REVISIONS,
               revised, 'additional', 1, show, items, 3)

    # ---- draft revision depth, reached through the private lifecycle owner only.
    shallow, shallow_depth = draft_deposit(world, 'ceiling-draft-shallow', 2)
    detail_set(record, 'draft revision depth', '%d draft revisions' % shallow_depth,
               shallow, 'additional', 1, show, items, 3)
    deep, deep_depth = draft_deposit(world, 'ceiling-draft-deep', DRAFT_REVISIONS)
    assert deep_depth > shallow_depth, (shallow_depth, deep_depth)
    detail_set(record, 'draft revision depth', '%d draft revisions' % deep_depth,
               deep, 'additional', 1, show, items, 3)

    # ---- connected root growth: one contributing receipt against many.
    one = support.post(client, dict(operation_key='ceiling-connected-1', document=dict(
        mode='inline', deposit_to=world['bank'], date='2026-06-10', memo='One contributing receipt',
        sources=[receipt(world, 0)])))
    detail_set(record, 'connected root growth', '1 contributing receipt',
               one.current.id, 'sources', 1, show, items, 3)
    many = support.post(client, dict(operation_key='ceiling-connected-many', document=dict(
        mode='inline', deposit_to=world['bank'], date='2026-06-10',
        memo='%d contributing receipts' % CONNECTED_SOURCES,
        sources=[receipt(world, index + 1) for index in range(CONNECTED_SOURCES)])))
    detail_set(record, 'connected root growth', '%d contributing receipts' % CONNECTED_SOURCES,
               many.current.id, 'sources', 1, show, items, 3)

    # ---- inspection history growth. The dependency history loads each owner
    # kind whole - the table and every audit entry for it - so this grows the
    # company's reference history with no new deposit and re-reads the same one.
    grown = 0
    detail_set(record, 'inspection history growth', 'no extra reference history',
               fixed, 'additional', 2, show, items, 3, extra_customers=grown)
    for step in EXTRA_CUSTOMERS:
        for index in range(step):
            client.customer.create(name='Ceiling history customer %d' % (grown + index), company=COMPANY)
        grown += step
        detail_set(record, 'inspection history growth', '+%d customer records and audit entries' % grown,
                   fixed, 'additional', 2, show, items, 3, extra_customers=grown)

    # ---- the two surfaces, at the identical final population.
    population = deposits_in(world)
    assert population >= MIN_POPULATION, population
    ceiling_plan = [('early ceiling', 'small deposit, full population', fixed, 'additional', 2),
                    ('early ceiling', '403-row deposit, full population', wide, 'cash_allocations', 200)]
    for dimension, point, deposit, kind, limit in ceiling_plan:
        detail_set(record, dimension, point, deposit, kind, limit, show, items, 3,
                   deposits_in_company=population)

    startup_ms = diagnostic = None
    try:
        startup_ms, diagnostic = anyio.run(through_mcp, world, record, ceiling_plan, 2)
    finally:
        # The receipt is written whether or not the ceiling holds. A blocker that
        # loses its own measurements is not a reported blocker.
        mcp = [row for row in record.rows if row['surface'] == 'full_mcp']
        inprocess = [row for row in record.rows if row['surface'] == 'in_process']
        detail = [row for row in mcp if row['dimension'] != 'MCP transport control']
        control = [row for row in mcp if row['dimension'] == 'MCP transport control']
        over = [row for row in detail if row['max_ms'] >= CEILING_MS]
        deadline = [row for row in detail if row['max_ms'] >= deadline_ms]
        refused = [row for row in mcp if row.get('error')]
        summary = dict(
            source_state=source_state(),
            deposits_in_company=population,
            wide_composition_rows=wide_page['total_count'],
            transport_deadline_seconds=json_seconds(),
            mcp_client_startup_ms=startup_ms,
            projection_count_diagnostic=diagnostic,
            worst_mcp_ms=max([row['max_ms'] for row in detail], default=None),
            mcp_transport_control_median_ms=control[0]['median_ms'] if control else None,
            worst_in_process_ms=max(row['max_ms'] for row in inprocess),
            mcp_requests_over_ceiling=len(over),
            mcp_requests_at_transport_deadline=len(deadline),
            mcp_requests_refused=len(refused),
            baseline_show_ms=BASELINE_SHOW_MS,
            baseline_items_ms=BASELINE_ITEMS_MS,
            beats_baseline_show=[row['point'] for row in inprocess
                                 if row['request'] == 'deposit show' and row['median_ms'] < BASELINE_SHOW_MS],
            beats_baseline_items=[row['point'] for row in inprocess
                                  if row['request'].startswith('deposit items')
                                  and row['median_ms'] < BASELINE_ITEMS_MS],
            blueprint_gate='The 10k-deposit / sub-100ms bounded read target is a separate open final '
                           'gate; nothing measured here satisfies it.')
        RECEIPTS.mkdir(parents=True, exist_ok=True)
        (RECEIPTS / 'g6-early-ceiling.json').write_text(json.dumps(
            dict(summary=summary, measurements=record.rows), indent=2, sort_keys=True) + '\n')
        print('G6 SUMMARY ' + json.dumps(summary), flush=True)

    # ---- the assertion the gate is actually about. Nothing above narrows a
    # request or caps the population to reach it.
    assert mcp, 'no MCP measurement was taken'
    assert not refused, ('a public detail request was refused through MCP', refused)
    assert not over, ('G6 early ceiling exceeded: %d of %d MCP deposit requests took 10s or more, worst %.1f ms'
                      % (len(over), len(detail), summary['worst_mcp_ms']),
                      [(row['point'], row['request'], row['max_ms']) for row in over])
    assert not deadline, ('reached the transport deadline', deadline)
