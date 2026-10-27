"""R180 increment 2, lane C: the bounded deposit reader proves exactly what the full scan proved.

The reader used to read every owner kind whole (every row, every audit entry) for each read
set. It now reads selected records by id in sets, resolves a current relation with an indexed
SQL read plus every audit-only identity, and a historical one from the latest image at the
cutoff of every identity. tests/deposit_history_oracle.py keeps the full scan. Over a history
with renames, a closing-date change, a void, a draft consumed and an edit draft consumed, and a
planted orphan audit entry, both readers must give the same read set (records, relations,
digest, unknowns), current and historical at several endpoints, and the same first refusal
on damaged books.
"""
import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

import bookflow
from bookflow.core import registry
from bookflow.core.ids import new_id
from tests.deposit_history_oracle import full_scan
from tests.test_deposit_command import COMPANY, books  # noqa: F401 - fixture


def _company_db(root):
    return next(Path(root).glob('organizations/*/*/company.db'))


def _private(client, function):
    """Run `function(s, ctx)` in an authentic write session (and keep what it wrote)."""
    command = registry.get('company show'); result = []
    def apply(plan, ctx, s):
        result.append(function(s, ctx.model_copy(update={'reason': 'Bounded reader witness'})))
        return registry.Applied(plan.preview, [], 'Bounded reader witness', audited=True)
    saved = command.writes, command.kind, command.truth, command.apply
    command.writes, command.kind, command.truth, command.apply = frozenset({'company'}), 'write', 'company', apply
    try:
        client.run('company show', {}, company=COMPANY)
    finally:
        command.writes, command.kind, command.truth, command.apply = saved
    return result[0]


def _observe(client, function):
    """Run `function(s)` in a read session of the company."""
    command = registry.get('company show'); found = []; original = command.plan
    def plan(inp, ctx, s):
        found.append(function(s))
        return original(inp, ctx, s)
    command.plan = plan
    try:
        client.run('company show', {}, company=COMPANY)
    finally:
        command.plan = original
    return found[0]


def _sources(client, day='2026-06-30'):
    page = client.run('deposit sources', dict(date=day, limit=200), company=COMPANY)
    return {row['source']: dict(source_type=row['source_type'], source=row['source'], expected_version=row['expected_version'])
            for row in page['items']}


def _scenario(books):
    """Renames, a closing-date change, a void, a consumed draft and a consumed edit draft."""
    from bookflow.company import deposit_drafts as drafts, deposit_draft_models as m
    from bookflow.company import deposit_lifecycle as lifecycle, deposit_persistence as persistence
    client = books['client']
    def invoice(amount, day):
        return client.run('invoice post', dict(customer=books['customer'], date=day,
            lines=[dict(item=books['item'], quantity='1', net_amount=amount)]), company=COMPANY)
    def pay(inv, amount, day, key):
        return client.run('payment receive', dict(customer=books['customer'], date=day, amount=amount,
            payment_method=books['method'], operation_key=key, applications=dict(mode='inline',
            items=[dict(invoice=inv['id'], expected_version=inv['version'], amount=amount)])), company=COMPANY)
    i1, i2, i3 = invoice('100', '2026-06-01'), invoice('70', '2026-06-01'), invoice('45', '2026-06-02')
    p1, p2 = pay(i1, '100', '2026-06-02', 'p1'), pay(i2, '30', '2026-06-02', 'p2')
    p3 = pay(i3, '45', '2026-06-03', 'p3')
    s1 = client.run('sales-receipt post', dict(customer=books['customer'], deposit_to=books['uf'],
        payment_method=books['method'], date='2026-06-02', lines=[dict(item=books['item'], quantity='1', unit_price='60')]),
        company=COMPANY)
    available = _sources(client)
    first = client.run('deposit post', dict(operation_key='d1', document=dict(mode='inline', deposit_to=books['bank'],
        date='2026-06-04', sources=[available[p1['id']], available[s1['id']]])), company=COMPANY)['deposit']
    # Renames of the bank and of the customer, and a closing-date change.
    bank = next(row for row in client.run('account list', {}, company=COMPANY)['items'] if row['id'] == books['bank'])
    client.run('account update', dict(account=books['bank'], expected_version=bank['version'], name='Operating checking'), company=COMPANY)
    client.customer.update(customer=books['customer'], expected_version=1, name='Ada Waterworks Ltd', company=COMPANY)
    client.run('company update', dict(closing_date='2026-05-31'), company=COMPANY)
    # Void the first deposit (through its own preview's guard).
    void = dict(deposit=first['id'], expected_version=first['version'], operation_key='d1-void')
    preview = client.run('deposit void', void, company=COMPANY, reason='never banked', dry_run=True)
    client.run('deposit void', dict(void, dependency_guard=preview['dependency_guard']), company=COMPANY, reason='never banked')
    # A draft of the freed payment, consumed by a post; then an edit draft of that deposit, consumed.
    available = _sources(client)
    def post_draft(s, ctx, verb, body):
        inp = lifecycle.INPUTS[verb].model_validate(body)
        plan = lifecycle.prepare(s, ctx, inp, verb)
        if verb != 'post' and isinstance(plan, lifecycle.Prepared):
            inp = inp.model_copy(update={'dependency_guard': plan.dependency_guard})
            plan = lifecycle.prepare(s, ctx, inp, verb)
        return persistence.execute(s, ctx, plan)
    draft = _private(client, lambda s, ctx: drafts.run(s, ctx, m.DraftCreate(header=m.HeaderPatch(date='2026-06-05', deposit_to=books['bank'])), 'create'))
    draft = _private(client, lambda s, ctx: drafts.run(s, ctx, m.DraftUpdate(draft=draft.id, expected_version=draft.version,
        set_sources=[m.SourcePatch(**available[p1['id']]), m.SourcePatch(**available[p2['id']])]), 'update'))
    second = _private(client, lambda s, ctx: post_draft(s, ctx, 'post', dict(operation_key='d2',
        document=dict(mode='draft', draft=draft.id, expected_version=draft.version))))
    edit = _private(client, lambda s, ctx: drafts.run(s, ctx, m.DraftCreate(from_deposit=second.current.id, expected_version=1), 'create'))
    edit = _private(client, lambda s, ctx: drafts.run(s, ctx, m.DraftUpdate(draft=edit.id, expected_version=edit.version,
        header=m.HeaderPatch(memo='Edited through a saved draft')), 'update'))
    _private(client, lambda s, ctx: post_draft(s, ctx, 'update', dict(deposit=second.current.id, expected_version=1,
        operation_key='d2-edit', document=dict(mode='draft', draft=edit.id, expected_version=edit.version))))
    third = client.run('deposit post', dict(operation_key='d3', document=dict(mode='inline', deposit_to=books['bank'],
        date='2026-06-06', sources=[available[s1['id']]])), company=COMPANY)['deposit']
    return dict(deposits=[first['id'], second.current.id, third['id']], payments=[p1['id'], p2['id'], p3['id']],
                receipts=[s1['id']], invoices=[i1['id'], i2['id'], i3['id']], draft=draft.id, edit=edit.id)


def _plant_orphan(root, source):
    """An audit-only deposit membership naming `source`: an identity with entries and no row."""
    with sqlite3.connect(_company_db(root)) as db:
        event, = db.execute('SELECT id FROM audit_events ORDER BY seq DESC LIMIT 1').fetchone()
        blob, = db.execute("SELECT after FROM audit_entries WHERE record_type='deposit_membership' ORDER BY rowid LIMIT 1").fetchone()
        from bookflow.core import audit
        image = dict(audit.decode_snapshot(blob)); identity = new_id()
        image.update(id=identity, source_transaction_id=source)
        db.execute('INSERT INTO audit_entries (id, event_id, record_type, record_id, action, version_before, version_after, after, before) '
                   'VALUES (?,?,?,?,?,?,?,?,?)', (new_id(), event, 'deposit_membership', identity, 'create', None, 1, audit.encode_snapshot(image), None))
    return identity


def _outcome(call):
    try:
        readset, history = call()
    except Exception as error:  # noqa: BLE001 - the refusal itself is compared
        return ('refused', type(error).__name__, str(error), getattr(error, 'code', None), getattr(error, 'details', None))
    return ('read', readset.model_dump(mode='json'), sorted(history.unknown))


def _both(s, original, binding, **kw):
    from bookflow.company import deposit_dependency_history as history
    bounded = _outcome(lambda: history._readset(s, original, binding=binding, **kw))
    with full_scan():
        scanned = _outcome(lambda: history._readset(s, original, binding=binding, **kw))
    return bounded, scanned


def _originals(ids):
    from bookflow.company import deposit_dependency_history as history
    from bookflow.company.deposit_dependency_models import InspectionRoot
    roots = [InspectionRoot(kind='deposit', id=d) for d in ids['deposits']]
    roots += [InspectionRoot(kind='payment', id=p) for p in ids['payments']]
    roots += [InspectionRoot(kind='sales_receipt', id=r) for r in ids['receipts']]
    void = history.request(dict(command='deposit void', input=dict(deposit=ids['deposits'][2], expected_version=1,
        operation_key='probe-void'), context={'reason': 'probe'}))
    return roots + [void]


def test_bounded_readsets_equal_the_full_scan_current_and_historical(books):
    from bookflow.company.deposit_dependency_models import InspectionRoot
    from bookflow.core.publication import OSBinding
    ids = _scenario(books)
    orphan = _plant_orphan(os.environ['BOOKFLOW_DATA_ROOT'], ids['payments'][2])
    client = books['client']
    def check(s):
        binding = OSBinding.from_session(s)
        events = [row[0] for row in s.company.raw.execute('SELECT id FROM audit_events ORDER BY seq')]
        first = s.company.raw.execute("SELECT min(seq) FROM audit_events WHERE command='deposit post'").fetchone()[0]
        later = [e for e, in s.company.raw.execute('SELECT id FROM audit_events WHERE seq>=? ORDER BY seq', (first,))]
        endpoints = sorted(set(later[::max(1, len(later) // 5)] + [later[-1], events[len(events) // 2]]), key=events.index)
        compared = refused = 0
        for original in _originals(ids):
            for kw in ({}, {'allow_missing_selectors': True}):
                bounded, scanned = _both(s, original, binding, **kw)
                assert bounded == scanned, (original, kw, scanned if scanned[0] == "refused" else bounded)
                compared += 1
            for endpoint in endpoints:
                bounded, scanned = _both(s, original, binding, endpoint=endpoint, historical=True)
                assert bounded == scanned, (original, endpoint)
                compared += 1; refused += bounded[0] == 'refused'
        # The orphan is found as a claim of its source in both readers, and, having no row
        # or creation it can prove, is unknown history (a later command is stale, not wrong).
        bounded, scanned = _both(s, InspectionRoot(kind='payment', id=ids['payments'][2]), binding)
        assert bounded == scanned and 'deposit_membership:' + orphan in bounded[1]['unknown']
        return compared, refused
    compared, refused = _observe(client, check)
    assert compared >= 60 and refused < compared


def _damage(root, ids, kind):
    """One corruption of the books, written behind the application's back."""
    from bookflow.core import audit
    with sqlite3.connect(_company_db(root)) as db:
        db.execute('PRAGMA foreign_keys=OFF')
        deposit = ids['deposits'][2]
        if kind == 'missing_owner':
            # A source's posting line row is gone; its audit history remains.
            line, = db.execute('SELECT id FROM posting_lines WHERE transaction_id=? LIMIT 1', (ids['receipts'][0],)).fetchone()
            for name, in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('posting_lines','posting_line_sources')").fetchall():
                db.execute(f'DROP TRIGGER "{name}"')
            db.execute('DELETE FROM posting_line_sources WHERE posting_line_id=?', (line,))
            db.execute('DELETE FROM posting_lines WHERE id=?', (line,))
        elif kind == 'dangling_relation':
            # A membership row of the deposit names a source transaction that does not exist.
            membership, = db.execute('SELECT id FROM deposit_memberships WHERE transaction_id=? LIMIT 1', (deposit,)).fetchone()
            for name, in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='deposit_memberships'").fetchall():
                db.execute(f'DROP TRIGGER "{name}"')
            db.execute('UPDATE deposit_memberships SET source_revision_id=? WHERE id=?', (new_id(), membership))
        elif kind == 'invalid_image':
            # The latest audit image of an unrelated document line no longer decodes.
            event, = db.execute('SELECT id FROM audit_events ORDER BY seq DESC LIMIT 1').fetchone()
            line, = db.execute('SELECT record_id FROM audit_entries WHERE record_type=? ORDER BY rowid DESC LIMIT 1', ('posting_line',)).fetchone()
            db.execute('INSERT INTO audit_entries (id, event_id, record_type, record_id, action, version_before, version_after, after, before) '
                       'VALUES (?,?,?,?,?,?,?,?,?)', (new_id(), event, 'posting_line', line, 'update', 1, 1, audit.ZIP + b'not zlib', None))
        elif kind == 'unrelated_invalid_image':
            # The latest audit image of a posting line of an invoice outside the deposit's graph.
            event, = db.execute('SELECT id FROM audit_events ORDER BY seq DESC LIMIT 1').fetchone()
            line, = db.execute('SELECT id FROM posting_lines WHERE transaction_id=? LIMIT 1', (ids['invoices'][2],)).fetchone()
            db.execute('INSERT INTO audit_entries (id, event_id, record_type, record_id, action, version_before, version_after, after, before) '
                       'VALUES (?,?,?,?,?,?,?,?,?)', (new_id(), event, 'posting_line', line, 'update', 1, 1, audit.ZIP + b'not zlib', None))
        elif kind == 'invalid_orphan_image':
            event, = db.execute('SELECT id FROM audit_events ORDER BY seq DESC LIMIT 1').fetchone()
            db.execute('INSERT INTO audit_entries (id, event_id, record_type, record_id, action, version_before, version_after, after, before) '
                       'VALUES (?,?,?,?,?,?,?,?,?)', (new_id(), event, 'deposit_membership', new_id(), 'create', None, 1, audit.RAW + b'{', None))
        elif kind == 'tampered_draft':
            for name, in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='deposit_drafts'").fetchall():
                db.execute(f'DROP TRIGGER "{name}"')
            db.execute("UPDATE deposit_drafts SET version=version+1 WHERE id=?", (ids['edit'],))
        elif kind == 'renamed_behind':
            # The bank's row renamed without an audit entry: current row differs from history.
            for name, in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='accounts'").fetchall():
                db.execute(f'DROP TRIGGER "{name}"')
            db.execute("UPDATE accounts SET name='Renamed behind the books' WHERE system_role='undeposited_funds'")


def _attempt(client, ids, books):
    """A void's preview and its commit with that preview's guard (plan and apply each compare
    the current and the historical read set), a source page, and a post's preview."""
    deposit = ids['deposits'][2]
    void = dict(deposit=deposit, expected_version=1, operation_key='corpus-void')
    out = []; guard = []
    def preview():
        result = client.run('deposit void', void, company=COMPANY, reason='corpus', dry_run=True)
        guard.append(result['dependency_guard'])
        return result
    for call in (preview,
                 lambda: client.run('deposit void', dict(void, dependency_guard=guard[0] if guard else None), company=COMPANY, reason='corpus'),
                 lambda: client.run('deposit sources', dict(date='2026-06-30', limit=200), company=COMPANY),
                 lambda: client.run('deposit post', dict(operation_key='corpus-post', document=dict(mode='inline',
                     deposit_to=books['bank'], date='2026-06-07', sources=list(_sources(client).values())[:1])), company=COMPANY,
                     dry_run=True)):
        try:
            result = call()
            out.append(('ok', sorted(result) if isinstance(result, dict) else None))
        except Exception as error:  # noqa: BLE001 - the refusal is what is compared
            out.append(('refused', getattr(error, 'code', type(error).__name__), str(error)[:200], json.dumps(getattr(error, 'details', None), sort_keys=True, default=str)[:400]))
    return out


@pytest.mark.parametrize('damage', ['missing_owner', 'dangling_relation', 'invalid_image', 'unrelated_invalid_image',
                                    'invalid_orphan_image', 'tampered_draft', 'renamed_behind'])
def test_damaged_books_give_the_same_first_refusal(books, damage, tmp_path):
    ids = _scenario(books)
    root = os.environ['BOOKFLOW_DATA_ROOT']
    healthy_root = tmp_path / 'healthy-root'; scan_root = tmp_path / 'scan-root'
    shutil.copytree(root, healthy_root)
    _damage(root, ids, damage)
    shutil.copytree(root, scan_root)   # the same damaged books, for the full-scan reader
    try:
        os.environ['BOOKFLOW_DATA_ROOT'] = str(healthy_root)
        healthy = _attempt(bookflow.connect(data_root=str(healthy_root)), ids, books)
        os.environ['BOOKFLOW_DATA_ROOT'] = root
        bounded = _attempt(books['client'], ids, books)
        os.environ['BOOKFLOW_DATA_ROOT'] = str(scan_root)
        with full_scan():
            scanned = _attempt(bookflow.connect(data_root=str(scan_root)), ids, books)
    finally:
        os.environ['BOOKFLOW_DATA_ROOT'] = root
    assert bounded == scanned
    assert healthy[0][0] == healthy[1][0] == 'ok'
    if damage != 'tampered_draft':   # the drafts are already consumed: no inline deposit reads them
        assert bounded != healthy, 'the damage must be met'
    record = os.environ.get('BF_R180_CORPUS')
    if record:
        with open(record, 'a') as handle:
            handle.write(json.dumps({'damage': damage, 'outcome': bounded}) + '\n')
