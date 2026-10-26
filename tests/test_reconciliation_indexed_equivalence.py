"""R180 increment 2: the indexed reconciliation checks prove exactly what the scans proved.

`tests/reconciliation_oracle.py` keeps the scans as they stood before the change. Two kinds of
evidence are taken against it, and both are kept for one increment:

- every mutation case of `test_reconciliation_storage_validation` runs again here with its
  `validate` replaced by one that runs the old and the new side by side and requires the same
  outcome: the same rule raised first, or both accepting;
- on real companies with reconciliations actually done -- the demo company reconciled monthly
  here, and a generated one when `BOOKFLOW_R180_ORACLE_ROOT` names its data root -- the closure,
  the authority set, `enumerate_graph`, `prove` at every captured cutoff and `validate` (with and
  without the write-time capture proofs) agree with the old ones exactly.
"""
import copy
import os
from contextlib import contextmanager
from datetime import date, timedelta

import pytest
import sqlalchemy as sa

import tests.test_reconciliation_storage_validation as storage
from bookflow.company import schema as sch
from bookflow.company import reconciliation_adapters as adapters
from bookflow.company import reconciliation_loading as loading
from bookflow.company import reconciliation_preparation as preparation
from bookflow.company.reconciliation_proof import prove
from bookflow.company.reconciliation_storage_validation import validate as indexed_validate
from bookflow.core.ids import new_id
from tests import reconciliation_oracle as oracle
from tests.test_deposit_lifecycle import driver  # noqa: F401
from tests.test_service_sales_lifecycle import sale  # noqa: F401
from tests.test_reconciliation_storage_validation import (  # noqa: F401
    account, journal, pair, run, COMPANY,
    # Collected again in this module, each `validate` call compared with the oracle's.
    test_real_source_full_history_exact_legs_subtypes_and_deferred_missing_subtype,
    test_persisted_g2_active_inactive_business_and_transition_anchors,
    test_nonempty_certificate_chain_claim_receipts_and_independent_mutations,
    test_actual_403_leaf_storage_chunks_barrier_and_deferred_subtype_mutations,
    test_receipt_whole_movement_and_no_claims_for_saved_draft_marks,
    test_proposal_exact_saved_fields_consumption_and_report_preset_versions,
    test_event_backed_release_retains_certificate_and_never_inverts_source,
)


def outcome(fn, *args, **kwargs):
    """What a check concluded: None when it accepted, else the exception's type and rule."""
    try:
        fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 -- the outcome itself is what is compared
        return type(exc).__name__, str(exc)
    return None


COMPARED = []
RULES = {}  # every outcome seen first, old and new agreeing, and one damage that produced it


@pytest.fixture(scope='module', autouse=True)
def coverage():
    """After the module: which storage rules the damaged books reached, printed for the record."""
    yield
    if RULES:
        rules = {v[1] for v in RULES if v and v[0] == 'InvalidStorage'}
        print(f'\nstorage rules raised first by some damage: {len(rules)} of {len(labels())}')
        print('never raised first:', sorted(labels() - rules))
        print('other outcomes:', sorted({v for v in RULES if v and v[0] != 'InvalidStorage'}, key=str)[:20])


@pytest.fixture(autouse=True)
def side_by_side(request, monkeypatch):
    """Each re-collected storage test runs `validate` old and new and requires one outcome."""
    if request.function.__module__ != storage.__name__:
        yield
        return
    before = len(COMPARED)
    # What the storage file's own autouse `owned_storage` arranges, for its tests only: the
    # whole-company tests below need the production materializer left on.
    from bookflow.company import reconciliation_materialization as materialization
    monkeypatch.setattr(materialization, 'drain_in_command', lambda *a, **k: 0)

    def both(rows, **kwargs):
        old = outcome(oracle.validate, rows, **kwargs)
        try:
            result = indexed_validate(rows, **kwargs)
        except Exception as exc:
            assert old == (type(exc).__name__, str(exc)), (old, exc)
            COMPARED.append(old)
            raise
        assert old is None, old
        COMPARED.append(None)
        if not damaged_here:
            # The first valid aggregate of each test is also damaged every way the corpus knows.
            damaged_here.append(True)
            for what, bad in damaged_rows(rows):
                same(what, bad, kwargs)
        return result
    damaged_here = []

    monkeypatch.setattr(storage, 'validate', both)
    yield
    assert len(COMPARED) > before, 'no validate call was compared'


# ------------------------------------------------------------------ whole-company comparison

def statement_accounts(s):
    return sorted(s.company.conn.execute(
        sa.select(sch.accounts.c.id).where(sch.accounts.c.type.in_(('bank', 'credit_card')))).scalars())


def compare_account(s, account_id):
    """Everything `loading.load` computes for one account, old and new; returns what it saw."""
    db = s.company
    identifiers = loading.statement_transactions(db, account_id)
    accounts = {account_id}
    rows = loading._rows_for(db, accounts, identifiers)
    assert rows == oracle._rows_for(db, accounts, identifiers)
    assert outcome(adapters.authority, s, identifiers) == outcome(oracle.authority, s, identifiers)
    if outcome(adapters.authority, s, identifiers) is None:
        assert adapters.authority(s, identifiers) == oracle.authority(s, identifiers)
    g = adapters.graph(type('S', (), {'company': db})(), identifiers)
    enumerated = outcome(adapters.enumerate_graph, g)
    assert enumerated == outcome(oracle.enumerate_graph, g)
    seen = dict(rows=sum(map(len, rows.values())), versions=len(rows['effect_versions']),
                certificates=len(rows['certificates']), proofs=0)
    if enumerated is None:
        history, current = adapters.enumerate_graph(g)
        assert (history, current) == oracle.enumerate_graph(g)
        cutoffs = {'9999-12-31'} | {v['statement_date'] for v in rows['certificates']} \
            | {v['opening_date'] for v in rows['openings']}
        for owner in sorted({v.account_id for v in history} | {account_id}):
            for cutoff in sorted(cutoffs):
                new = outcome(prove, g, history, current, owner, cutoff)
                assert new == outcome(oracle.prove, g, history, current, owner, cutoff)
                if new is None:
                    assert prove(g, history, current, owner, cutoff) == oracle.prove(g, history, current, owner, cutoff)
                seen['proofs'] += 1
    scoped = loading.referenced_rows(db, rows)
    whole = loading.referenced_rows(db)
    captures = {v['id']: g for name in ('openings', 'certificates') for v in rows[name]}
    for captured in ({}, captures):
        old = outcome(oracle.validate, rows, source=g, captured_graphs=captured, referenced_rows=whole)
        assert outcome(indexed_validate, rows, source=g, captured_graphs=captured, referenced_rows=scoped) == old
        assert outcome(indexed_validate, rows, source=g, captured_graphs=captured, referenced_rows=whole) == old
    seen['validate'] = outcome(indexed_validate, rows, source=g, captured_graphs={}, referenced_rows=scoped)
    if seen['validate'] is None:
        snapshot = loading.load(s, account_id)
        for owner in sorted({v['account_id'] for v in snapshot.rows['effect_versions']} | {account_id}):
            cutoffs = {'9999-12-31'} | {v['statement_date'] for v in rows['certificates']}
            for cutoff in sorted(cutoffs):
                fresh = outcome(preparation.account_population, snapshot, owner, cutoff)
                again = outcome(preparation.account_population, snapshot, owner, cutoff)  # memoized
                old = outcome(legacy_population, snapshot, owner, cutoff)
                assert fresh == again == old
                if fresh is None:
                    assert preparation.account_population(snapshot, owner, cutoff) == legacy_population(snapshot, owner, cutoff)
    return seen


def legacy_population(s, account, cutoff):
    """`account_population` as it stood: a fresh enumeration and proof by the old scans."""
    with preparation.adapter_errors():
        a = s.source.accounts[account]
        home = s.referenced_rows['company_info'][0]['home_currency']
        preparation.require(a['type'] in ('bank', 'credit_card') and a['currency'] == home, 'E_RECONCILIATION_UNSUPPORTED')
        history, current = oracle.enumerate_graph(s.source)
        total, gl = oracle.prove(s.source, history, current, account, cutoff)
        values = tuple(v for v in s.current.values() if v['account_id'] == account and v['active'])
        stored = sum(v['signed_debit'] for v in values if v['effective_date'] <= cutoff)
        preparation.require(stored == total, 'E_RECONCILIATION_SOURCE_INVALID')
        return values, (-gl if a['type'] == 'credit_card' else gl)


@contextmanager
def read_session(client, company):
    """A session over one company, as `driver` builds it, rolled back on the way out."""
    from bookflow.core import registry
    from bookflow.core.locks import RootLock
    from bookflow.storage.engine import open_database
    captured = []
    command = registry.get('company show'); original = command.plan

    def capture(inp, ctx, s):
        captured.append(copy.copy(s))
        return original(inp, ctx, s)
    command.plan = capture
    try:
        client.run('company show', {}, company=company)
    finally:
        command.plan = original
    seed = captured[0]
    from pathlib import Path
    path = Path(client.company.show(company=company)['path']) / 'company.db'
    with RootLock(seed.data_root, 'r180-oracle'), open_database(seed.data_root / 'hub.db', writable=False) as hub, \
            open_database(path, writable=True) as db:
        s = copy.copy(seed); s.company = db; s.hub = hub; s.dry_run = False
        db.raw.execute('BEGIN IMMEDIATE')
        s.company_info_row = dict(db.conn.execute(sa.select(sch.company_info)).mappings().one())
        try:
            yield s
        finally:
            if db.raw.in_transaction:
                db.raw.execute('ROLLBACK')


def month_ends(start, count):
    out, y, m = [], start.year, start.month
    for _ in range(count):
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        out.append((nxt - timedelta(days=1)).isoformat())
        y, m = nxt.year, nxt.month
    return out


def reconcile_month(call, account_id, statement_date, ending, opening_draft_id=None):
    """Start, mark every candidate, preview and finish one statement through the commands."""
    body = dict(operation_key=new_id(), account=account_id, statement_date=statement_date, ending_balance=ending)
    if opening_draft_id:
        body['opening_draft_id'] = opening_draft_id
    draft = call('reconcile start', body)['draft']
    marked = call('reconcile mark', dict(operation_key=new_id(), draft=draft['id'], expected_version=draft['version'],
                                         all=True, all_action='mark'))
    version = (marked.get('draft') or draft)['version']
    page = call('reconcile preview', dict(draft=draft['id'], expected_version=version))
    return call('reconcile finish', dict(operation_key=new_id(), draft=draft['id'], expected_version=version,
                                         expected_facts_fingerprint=page['expected_facts_fingerprint'],
                                         dependency_guard=page['dependency_guard']))


def test_demo_company_reconciled_monthly_matches_the_oracle(client, driver):
    """Three certified months over journals, a void after the last, then every account compared."""
    bank = account(client, 'Oracle bank')
    equity = account(client, 'Oracle equity', 'equity')
    posted = []
    for month, day in ((1, 5), (1, 20), (2, 3), (2, 17), (3, 9), (3, 25), (4, 2)):
        posted.append(journal(client, pair(bank, equity, f'{month}.{day:02d}'), date=f'2026-{month:02d}-{day:02d}'))
    opening = run(client, 'reconcile opening start', dict(
        operation_key=new_id(), account=bank, opening_date='2026-01-01', entered_balance='0.00',
        evidence=dict(format=1, statement_reference=None, entered_text='Adopted at zero'), references=[]))
    totals = {'2026-01-31': 105 + 120, '2026-02-28': 105 + 120 + 203 + 217, '2026-03-31': 105 + 120 + 203 + 217 + 309 + 325}
    for n, end in enumerate(month_ends(date(2026, 1, 1), 3)):
        cents = totals[end]
        reconcile_month(lambda name, body: run(client, name, body), bank, end, f'{cents // 100}.{cents % 100:02d}',
                        opening['draft']['id'] if n == 0 else None)
    run(client, 'journal void', dict(journal=posted[-1]['id'], expected_version=posted[-1]['version']))
    with driver.session() as s:
        seen = {a: compare_account(s, a) for a in statement_accounts(s)}
    assert seen[bank]['certificates'] == 3 and seen[bank]['validate'] is None and seen[bank]['proofs']


@pytest.mark.skipif(not os.environ.get('BOOKFLOW_R180_ORACLE_ROOT'), reason='names a generated, reconciled data root')
def test_generated_reconciled_company_matches_the_oracle():
    """`perf generate` company reconciled monthly (see design/performance.md, increment 2)."""
    import bookflow
    root = os.environ['BOOKFLOW_R180_ORACLE_ROOT']
    company = os.environ.get('BOOKFLOW_R180_ORACLE_COMPANY', 'Bench Trades Co')
    client = bookflow.connect(data_root=root)
    with read_session(client, company) as s:
        seen = {a: compare_account(s, a) for a in statement_accounts(s)}
    assert any(v['certificates'] >= 12 for v in seen.values()), seen


# ------------------------------------------------------------------ damaged books

def labels():
    """Every rule `validate` can raise, read from its source: `require(..., 'rule')` and
    `InvalidStorage('rule')`."""
    import ast
    import inspect
    from bookflow.company import reconciliation_storage_validation as module
    found = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Call) and getattr(node.func, 'id', None) in ('require', 'InvalidStorage'):
            last = node.args[-1] if node.args else None
            if isinstance(last, ast.Constant) and isinstance(last.value, str):
                found.add(last.value)
    return found


def same(what, rows, kwargs):
    old = outcome(oracle.validate, rows, **kwargs)
    new = outcome(indexed_validate, rows, **kwargs)
    assert new == old, (what, old, new)
    RULES.setdefault(old, what)


def damaged_rows(rows):
    """Stored rows damaged one way at a time: each yields (what, rows)."""
    import json
    for name in rows:
        yield f'{name} table missing', {n: list(v) for n, v in rows.items() if n != name}
    for name, values in rows.items():
        if not values:
            continue
        if len(values) > 1:
            out = {n: list(v) for n, v in rows.items()}; out[name] = out[name][::-1]
            yield f'{name} reversed', out
            out = {n: list(v) for n, v in rows.items()}; out[name] = out[name][:1]
            yield f'{name} only first', out
        for index in sorted({0, len(values) - 1}):
            row = values[index]

            def changed(new_row, what):
                out = {n: list(v) for n, v in rows.items()}
                out[name][index] = new_row
                return what, out
            out = {n: list(v) for n, v in rows.items()}; del out[name][index]
            yield f'{name}[{index}] missing', out
            out = {n: list(v) for n, v in rows.items()}; out[name].append(dict(row))
            yield f'{name}[{index}] duplicated', out
            yield changed(dict(row, r180_extra=1), f'{name} extra column')
            for column, value in row.items():
                yield changed(dict(row, **{column: None}), f'{name}.{column} null')
                yield changed({k: v for k, v in row.items() if k != column}, f'{name}.{column} column missing')
                for odd in (7, 2 ** 63, 'not an id', '[]', 'NaN', '{"a":NaN}'):
                    if odd != value:
                        yield changed(dict(row, **{column: odd}), f'{name}.{column} = {odd!r}')
                others = [v[column] for v in values if v[column] != value]
                if others:
                    yield changed(dict(row, **{column: others[0]}), f'{name}.{column} another row\'s')
                for other in others[1:4]:
                    yield changed(dict(row, **{column: other}), f'{name}.{column} yet another row\'s')
                if type(value) is int:
                    yield changed(dict(row, **{column: value + 1}), f'{name}.{column} +1')
                    yield changed(dict(row, **{column: -value - 1}), f'{name}.{column} negated')
                    yield changed(dict(row, **{column: 0}), f'{name}.{column} zero')
                elif type(value) is str:
                    yield changed(dict(row, **{column: new_id()}), f'{name}.{column} dangling')
                    try:
                        decoded = json.loads(value)
                    except ValueError:
                        decoded = None
                    if type(decoded) is dict and decoded:
                        first = sorted(decoded)[0]
                        yield changed(dict(row, **{column: value[:-1] + ',' + json.dumps(first) + ':1}'}), f'{name}.{column} duplicate json key')
                        yield changed(dict(row, **{column: json.dumps({k: v for k, v in decoded.items() if k != first})}), f'{name}.{column} json key dropped')
                        for key in sorted(decoded):
                            item = decoded[key]
                            bent = [] if type(item) is not list else (item[1:] if item else ['x'])
                            yield changed(dict(row, **{column: json.dumps(dict(decoded, **{key: bent}))}), f'{name}.{column}.{key} bent')
                            for kept in kept_variants(item):
                                yield changed(dict(row, **{column: json.dumps(dict(decoded, **{key: kept}))}), f'{name}.{column}.{key} shifted')
                    elif value[:4].isdigit():
                        yield changed(dict(row, **{column: '2099-12-31'}), f'{name}.{column} moved date')


def kept_variants(item):
    """Damage that keeps a snapshot field's shape: what passes the models and fails later."""
    if type(item) is int and type(item) is not bool:
        return [item + 1, -item - 1]
    if type(item) is str and len(item) == 10 and item[4] == '-':
        return ['2001-01-01', '2099-12-31']
    if type(item) is str:
        return [item + 'x']
    if type(item) is list and item:
        return [item[1:], item + item[:1], item[::-1]]
    return []


def damaged_graphs(g):
    """The ledger under the stored rows damaged one way at a time (contradictory provenance)."""
    for name, values in g.rows.items():
        if not values:
            continue
        row = values[0]
        rows = {n: list(v) for n, v in g.rows.items()}; del rows[name][0]
        yield f'graph {name}[0] missing', adapters.Graph(rows, g.accounts)
        rows = {n: list(v) for n, v in g.rows.items()}; rows[name].append(dict(row))
        yield f'graph {name}[0] duplicated', adapters.Graph(rows, g.accounts)
        for column, value in row.items():
            if type(value) is int and column != 'id':
                rows = {n: list(v) for n, v in g.rows.items()}; rows[name][0] = dict(row, **{column: value + 1})
                yield f'graph {name}.{column} +1', adapters.Graph(rows, g.accounts)


def damaged_references(refs):
    for name, values in refs.items():
        if values:
            yield f'{name} emptied', dict(refs, **{name: []})
            yield f'{name}[0] duplicated', dict(refs, **{name: list(values) + [dict(values[0])]})


def compare_damage(s, account_id):
    """Old and new report the same first rule on every damaged variant; returns the rules seen."""
    db = s.company
    identifiers = loading.statement_transactions(db, account_id)
    rows = loading._rows_for(db, {account_id}, identifiers)
    g = adapters.graph(type('S', (), {'company': db})(), identifiers)
    whole = loading.referenced_rows(db)
    seen, cases = {}, 0

    def both(what, rows, graph, old_refs, new_refs):
        nonlocal cases
        captures = {v['id']: graph for name in ('openings', 'certificates') for v in rows.get(name, ()) if v.get('id')}
        old = outcome(oracle.validate, rows, source=graph, captured_graphs=captures, referenced_rows=old_refs)
        new = outcome(indexed_validate, rows, source=graph, captured_graphs=captures, referenced_rows=new_refs)
        assert new == old, (what, old, new)
        seen.setdefault(old, what); RULES.setdefault(old, what); cases += 1
    for what, bad in damaged_rows(rows):
        # The scoped references are computed from the damaged rows, as `load` would compute them.
        # (A damage `load` itself could never read back -- a table missing, a column missing --
        # is checked against the whole references instead.)
        scoped = whole if outcome(loading.referenced_rows, db, bad) else loading.referenced_rows(db, bad)
        both(what, bad, g, whole, scoped)
    for what, bad in damaged_graphs(g):
        both(what, rows, bad, whole, loading.referenced_rows(db, rows))
    for what, bad in damaged_references(whole):
        both(what, rows, g, bad, bad)
    return seen, cases


def test_damaged_books_report_the_same_first_rule(client, driver):
    """A corpus of damaged books, one damage at a time, over a reconciled account.

    Every column of the first and last stored row of every table nulled, dangled, swapped with
    another row's, bumped, negated, re-dated; JSON snapshots with a repeated key, a dropped key
    and each field bent; rows deleted and duplicated; the ledger graph and the referenced tables
    damaged the same way. Old and new must agree on the outcome of each, rule for rule. Then a
    table rebuilt without its foreign key, holding a dangling key, is loaded both ways.
    """
    bank = account(client, 'Damage bank')
    equity = account(client, 'Damage equity', 'equity')
    for month, day in ((1, 5), (1, 20), (2, 3), (2, 17)):
        journal(client, pair(bank, equity, f'{month}.{day:02d}'), date=f'2026-{month:02d}-{day:02d}')
    voided = journal(client, pair(bank, equity, '9.99'), date='2026-02-20')
    run(client, 'journal void', dict(journal=voided['id'], expected_version=voided['version']))
    opening = run(client, 'reconcile opening start', dict(
        operation_key=new_id(), account=bank, opening_date='2026-01-01', entered_balance='0.00',
        evidence=dict(format=1, statement_reference=None, entered_text='Adopted at zero'), references=[]))
    call = lambda name, body: run(client, name, body)  # noqa: E731
    reconcile_month(call, bank, '2026-01-31', '2.25', opening['draft']['id'])
    reconcile_month(call, bank, '2026-02-28', '6.45')
    draft = call('reconcile start', dict(operation_key=new_id(), account=bank, statement_date='2026-03-31',
                                         ending_balance='6.45'))['draft']
    call('reconcile mark', dict(operation_key=new_id(), draft=draft['id'], expected_version=draft['version'],
                                all=True, all_action='mark'))
    with driver.session() as s:
        seen, cases = compare_damage(s, bank)
    rules = {v[1] for v in seen if v and v[0] == 'InvalidStorage'}
    print(f'\n{cases} damaged variants; {len(rules)} of {len(labels())} storage rules raised first; '
          f'other outcomes: {sorted({v for v in seen if v and v[0] != "InvalidStorage"})}')
    print('rules never raised first:', sorted(labels() - rules))
    assert len(rules) >= 40

    # A table rebuilt without its foreign key (the migration bug `validate` exists to catch).
    with read_session(client, COMPANY) as s:
        raw = s.company.raw
        table = 'reconciliation_effect_legs'
        columns = [r[1] for r in raw.execute(f'PRAGMA table_info({table})')]
        raw.execute(f'CREATE TABLE r180_copy AS SELECT * FROM {table}')
        for (trigger,) in raw.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchall():
            raw.execute(f'DROP TRIGGER {trigger}')
        raw.execute('PRAGMA defer_foreign_keys=ON')
        raw.execute(f'DROP TABLE {table}')
        raw.execute(f'CREATE TABLE {table} ({", ".join(columns)})')
        raw.execute(f'INSERT INTO {table} SELECT * FROM r180_copy')
        raw.execute('DROP TABLE r180_copy')
        raw.execute(f"UPDATE {table} SET posting_line_id=? WHERE rowid=(SELECT max(rowid) FROM {table} "
                    f"WHERE transaction_id IN (SELECT transaction_id FROM reconciliation_effect_versions WHERE account_id=?))",
                    (new_id(), bank))
        assert raw.execute('SELECT changes()').fetchone()[0] == 1
        rebuilt = compare_account(s, bank)
        assert rebuilt['validate'] == ('InvalidStorage', 'foreign_owner')
