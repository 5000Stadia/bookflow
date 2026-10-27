"""R180 stage 2: the verified history watermark, and `company verify`.

A reconciliation command proves only what was written after the account's watermark, plus every
older closure that touches (`company/reconciliation_watermark.py`); `company verify`, backups and
restores prove everything. What is shown here:

- on books reconciled through the commands and then changed in every way that reaches old facts
  (a reconciled check corrected, a reconciled deposit voided, a new month), the scoped proof
  gives the same snapshot as the full one, and the commands use it;
- a seeded random run of postings, deposits, voids, corrections and reconciliations, where after
  every step the scoped proof and the full one agree (both accept, the same snapshot facts);
- damage to the stored rows, one at a time (the damaged-books corpus of
  `test_reconciliation_indexed_equivalence`), is refused by the per-command proof whenever it is
  in what was written since the watermark or what that touches; damage to older rows is refused
  by `company verify` (and by the per-command proof, once a deleted row breaks the census);
- old, already verified history damaged in place: `company verify` and a backup refuse it, a
  code change and a digest mismatch each force the full proof, which refuses it;
- co0101 adds its table and changes no row.
"""
import json
import random
import re
import sqlite3
from contextlib import contextmanager

import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import reconciliation_adapters as adapters
from bookflow.company import reconciliation_capture as capture
from bookflow.company import reconciliation_loading as loading
from bookflow.company import reconciliation_preparation as preparation
from bookflow.company import reconciliation_watermark as watermark
from bookflow.company import schema as sch
from bookflow.company.reconciliation_storage_validation import Suffix, validate
from bookflow.core import registry
from bookflow.core.ids import new_id
from tests.test_deposit_command import COMPANY, books  # noqa: F401
from tests.test_reconciliation_indexed_equivalence import (
    cascade, damaged_graphs, damaged_references, damaged_rows, outcome, read_session)


def run(client, name, data, **ctx):
    if registry.get(name).is_write:
        ctx.setdefault('reason', 'R180 watermark witness')
    return client.run(name, data, company=COMPANY, **ctx)


def company_db(client):
    from pathlib import Path
    return Path(client.company.show(company=COMPANY)['path']) / 'company.db'


@contextmanager
def session(client):
    with read_session(client, COMPANY) as s:
        yield s


# ------------------------------------------------------------------ the books


def receipt(books, day, amount):
    """A counter sale into Undeposited Funds: a source for a deposit."""
    return run(books['client'], 'sales-receipt post', dict(
        customer=books['customer'], deposit_to=books['uf'], payment_method=books['method'], date=day,
        lines=[dict(item=books['item'], quantity='1', unit_price=amount)]))


def deposit(books, day):
    client = books['client']
    sources = run(client, 'deposit sources', dict(date=day))['items']
    if not sources:
        return None
    return run(client, 'deposit post', dict(operation_key=new_id(), document=dict(
        mode='inline', deposit_to=books['bank'], date=day,
        sources=[dict(source_type=row['source_type'], source=row['source'],
                      expected_version=row['expected_version']) for row in sources])))['deposit']


def void_deposit(books, posted):
    client = books['client']
    void = dict(deposit=posted['id'], expected_version=posted['version'], operation_key=new_id())
    preview = run(client, 'deposit void', void, dry_run=True)
    return run(client, 'deposit void', dict(void, dependency_guard=preview['dependency_guard']))


def check(books, day, amount):
    return run(books['client'], 'check post', dict(account=books['bank'], date=day, amount=amount,
                                                   expenses=[dict(account=books['supplies'], amount=amount)]))


def correct_check(books, posted, amount):
    client = books['client']
    shown = run(client, 'check show', dict(check=posted['id']))
    line = next(row for row in shown['revision']['lines'] if row['side'] == 'debit')
    return run(client, 'check update', dict(check=posted['id'], expected_version=shown['version'], amount=amount,
                                            expenses=[dict(line_id=line['line_id'], account=line['account_id'],
                                                           amount=amount)]))


def owner_money(books, day, amount):
    return run(books['client'], 'journal post', dict(date=day, lines=[
        dict(account=books['bank'], side='debit', amount=amount),
        dict(account=books['equity'], side='credit', amount=amount)]))


def reconcile(books, statement_date, *, opening_date=None):
    """Start a statement at a zero ending balance, tick everything, and finish it: with a labelled
    adjustment for whatever does not tie (a person's act), so every month can be finished."""
    client = books['client']
    body = dict(operation_key=new_id(), account=books['bank'], statement_date=statement_date, ending_balance='0.00')
    if opening_date:
        opening = run(client, 'reconcile opening start', dict(
            operation_key=new_id(), account=books['bank'], opening_date=opening_date, entered_balance='0.00',
            evidence=dict(format=1, statement_reference=None, entered_text='Adopted at zero'), references=[]))
        body['opening_draft_id'] = opening['draft']['id']
    draft = run(client, 'reconcile start', body)['draft']
    marked = run(client, 'reconcile mark', dict(operation_key=new_id(), draft=draft['id'],
                                                expected_version=draft['version'], all=True, all_action='mark'))
    version = (marked.get('draft') or draft)['version']
    page = run(client, 'reconcile preview', dict(draft=draft['id'], expected_version=version))
    finish = dict(operation_key=new_id(), draft=draft['id'], expected_version=version,
                  expected_facts_fingerprint=page['expected_facts_fingerprint'], dependency_guard=page['dependency_guard'])
    if not page['balanced']:
        finish['adjustment'] = dict(reason='Statement figure entered as zero in the witness')
    return run(client, 'reconcile finish', finish)


@pytest.fixture
def ledger(books):
    client = books['client']
    books['equity'] = client.account.create(name='Owner equity', type='equity', company=COMPANY)['id']
    books['supplies'] = client.account.create(name='Plumbing supplies', type='expense', company=COMPANY)['id']
    return books


# ------------------------------------------------------------------ comparing the two proofs


CUTOFFS = ('2026-05-31', '2026-06-15', '2026-06-30', '2026-07-31', '2026-08-31', '2026-12-31', '9999-12-31')


def facts(snapshot, account):
    """What the commands read of a snapshot: its rows, the population and ledger total at each
    cutoff, and the capture a finish would store."""
    out = {'rows': snapshot.rows}
    for cutoff in CUTOFFS:
        values, gl = preparation.account_population(snapshot, account, cutoff)
        out[cutoff] = (sorted(v['id'] for v in values), gl)
    out['capture'] = capture.population(snapshot, account, '2026-12-31', opening=False)
    return out


def both(s, account, *, prove_captures=()):
    """The full proof and the scoped one of the same state: (state reason, scope, full, scoped)."""
    db = s.company
    accounts, identifiers, rows = loading.gather(db, account)
    authorized = adapters.authority(s, identifiers)
    reason, scope = watermark.state(db, account, accounts, identifiers)
    full = loading._full(db, rows, identifiers, prove_captures, authorized)
    scoped = loading._scoped(db, rows, identifiers, scope, prove_captures, authorized) if scope else None
    return reason, scope, full, scoped


def agree(client, account):
    """The scoped proof accepts exactly when the full one does, and reads the same facts."""
    with session(client) as s:
        latest = [v['id'] for v in s.company.conn.execute(sa.select(sch.reconciliation_certificates)
                  .where(sch.reconciliation_certificates.c.account_id == account)
                  .order_by(sch.reconciliation_certificates.c.statement_date.desc()).limit(1)).mappings()]
        reason, scope, full, scoped = both(s, account)
        if scope is None:
            return reason, None
        assert facts(scoped, account) == facts(full, account)
        # The write-time proof of the newest capture, against today's ledger: the two agree on it too
        # (it fails once the ledger has moved under it, as it should, and then fails both ways).
        db = s.company
        accounts, identifiers, rows = loading.gather(db, account)
        authorized = adapters.authority(s, identifiers)
        for identity in latest:
            whole = outcome(loading._full, db, rows, identifiers, (identity,), authorized)
            part = outcome(loading._scoped, db, rows, identifiers, scope, (identity,), authorized)
            assert (whole is None) == (part is None), (whole, part)
        return reason, scope


# ------------------------------------------------------------------ the commands use it


def test_the_commands_prove_only_what_followed_the_watermark_and_read_what_the_full_proof_reads(ledger, monkeypatch):
    client, bank = ledger['client'], ledger['bank']
    calls = []
    for name in ('_full', '_scoped'):
        original = getattr(loading, name)
        monkeypatch.setattr(loading, name, lambda *a, _o=original, _n=name, **k: (calls.append(_n), _o(*a, **k))[1])

    owner_money(ledger, '2026-06-01', '1000.00')
    receipt(ledger, '2026-06-02', '100'); receipt(ledger, '2026-06-02', '60')
    june_deposit = deposit(ledger, '2026-06-03')
    june_check = check(ledger, '2026-06-10', '250.00')
    with session(client) as s:
        assert watermark.state(s.company, bank, {bank}, loading.statement_transactions(s.company, bank))[0] == 'missing'
    calls.clear()
    reconcile(ledger, '2026-06-30', opening_date='2026-05-31')
    # The first load had no watermark; it wrote one, and every load after it was scoped.
    assert calls[0] == '_full' and set(calls[1:]) == {'_scoped'}, calls

    reason, scope = agree(client, bank)
    assert reason == 'holds' and scope.documents == frozenset() and not any(scope.written.values())

    # A new month, a correction to a reconciled check (its head moves), and a reconciled deposit voided:
    # each document is derived again, and nothing else.
    july = check(ledger, '2026-07-08', '75.00')
    correct_check(ledger, june_check, '275.00')
    void_deposit(ledger, june_deposit)
    receipt(ledger, '2026-07-12', '40'); deposit(ledger, '2026-07-13')
    reason, scope = agree(client, bank)
    assert reason == 'holds' and {july['id'], june_check['id'], june_deposit['id']} <= scope.documents

    calls.clear()
    done = reconcile(ledger, '2026-07-31')
    assert done['certificate_id'] and set(calls) == {'_scoped'}, calls
    assert loading.last_proof['proof'] == 'scoped'
    reason, scope = agree(client, bank)
    assert reason == 'holds' and scope.documents == frozenset()

    out = run(client, 'company verify', {})
    assert out['ok'] and {v['account_id']: v['outcome'] for v in out['accounts']}[bank] == 'proved'
    assert next(v for v in out['accounts'] if v['account_id'] == bank)['watermark'] == 'holds'


# ------------------------------------------------------------------ a random run


def test_a_random_run_of_postings_deposits_voids_corrections_and_reconciliations_agrees_with_the_full_proof(ledger):
    client, bank = ledger['client'], ledger['bank']
    rng = random.Random(180)
    owner_money(ledger, '2026-06-01', '5000.00')
    reconcile(ledger, '2026-06-30', opening_date='2026-05-31')
    checks, deposits, journals = [], [], []
    month_ends = ['2026-07-31', '2026-08-31', '2026-09-30']
    seen = {'holds': 0}
    for month, end in enumerate(month_ends, start=7):
        for _ in range(7):
            day = f'2026-{month:02d}-{rng.randint(1, 28):02d}'
            step = rng.choice(('check', 'check', 'receipts', 'journal', 'correct', 'void_deposit', 'void_check'))
            if step == 'check':
                checks.append(check(ledger, day, f'{rng.randint(5, 300)}.{rng.randint(0, 99):02d}'))
            elif step == 'receipts':
                for _ in range(rng.randint(1, 3)):
                    receipt(ledger, day, str(rng.randint(10, 200)))
                posted = deposit(ledger, day)
                if posted:
                    deposits.append(posted)
            elif step == 'journal':
                journals.append(owner_money(ledger, day, f'{rng.randint(1, 900)}.00'))
            elif step == 'correct' and checks:
                target = rng.choice(checks)
                correct_check(ledger, target, f'{rng.randint(5, 300)}.00')
            elif step == 'void_deposit' and deposits:
                target = deposits.pop(rng.randrange(len(deposits)))
                void_deposit(ledger, target)
            elif step == 'void_check' and checks:
                target = checks.pop(rng.randrange(len(checks)))
                run(client, 'check void', dict(check=target['id']))
            reason, _ = agree(client, bank)
            seen[reason] = seen.get(reason, 0) + 1
        reconcile(ledger, end)
        reason, _ = agree(client, bank)
        seen[reason] = seen.get(reason, 0) + 1
    # Every state after the first reconciliation was compared through a holding watermark.
    assert set(seen) == {'holds'} and seen['holds'] == 3 * 8, seen
    assert run(client, 'company verify', {})['ok']


# ------------------------------------------------------------------ the damaged-books corpus


def _primary(name):
    return [c.name for c in loading._tables()[name].primary_key.columns]


def suffix_for(original, damaged, written):
    """The rows a damaged file would hold after its watermark, the way its row numbers would say:
    an unchanged row as it was, a row changed in place where it stood, a row added at the end.
    Returns (Suffix, whether a row of the verified prefix was deleted, whether one was changed)."""
    rows, deleted, changed = {}, False, False
    for name, values in original.items():
        fresh = [tuple(v.get(c) for c in _primary(name)) in written.get(name, ()) for v in values]
        objects = {id(v): f for v, f in zip(values, fresh)}
        out = damaged.get(name, [])
        kept = {id(v) for v in out}
        # Rows gone (the table shorter, not a row replaced where it stood) from the verified prefix.
        if len(out) < len(values) and any(id(v) not in kept and not f for v, f in zip(values, fresh)):
            deleted = True
        keys = set()
        for position, row in enumerate(out):
            if id(row) in objects:
                is_fresh = objects[id(row)]
            elif position < len(values):
                is_fresh = fresh[position]
                changed = changed or not is_fresh
            else:
                is_fresh = True
            if is_fresh:
                keys.add(tuple(row.get(c) for c in _primary(name)))
        rows[name] = keys
    return Suffix(rows), deleted, changed


def test_damaged_books_are_caught_by_the_per_command_proof_or_by_verify(ledger):
    """Each damage of `test_reconciliation_indexed_equivalence`'s corpus, one at a time, over an account
    reconciled for June with July written after its watermark (checks, a corrected June check, a voided
    June deposit, a July statement marked). Wherever the full proof refuses: the scoped proof refuses too
    when the damage is in what was written since the watermark; otherwise the damage is to the verified
    prefix, and `company verify` (the full proof) is what refuses it -- or, for a deleted row, the
    per-command proof once the census no longer holds."""
    client, bank = ledger['client'], ledger['bank']
    owner_money(ledger, '2026-06-01', '1000.00')
    receipt(ledger, '2026-06-02', '100'); receipt(ledger, '2026-06-02', '60')
    june_deposit = deposit(ledger, '2026-06-03')
    june_check = check(ledger, '2026-06-10', '250.00')
    reconcile(ledger, '2026-06-30', opening_date='2026-05-31')
    with session(client) as s:
        saved = dict(s.company.conn.execute(sa.select(sch.statement_watermarks)).mappings().one())
    check(ledger, '2026-07-08', '75.00')
    correct_check(ledger, june_check, '275.00')
    void_deposit(ledger, june_deposit)
    draft = run(client, 'reconcile start', dict(operation_key=new_id(), account=bank, statement_date='2026-07-31',
                                               ending_balance='0.00'))['draft']
    run(client, 'reconcile mark', dict(operation_key=new_id(), draft=draft['id'], expected_version=draft['version'],
                                       all=True, all_action='mark'))
    with sqlite3.connect(company_db(client)) as raw:
        # Back to the June watermark: everything of July is the suffix.
        raw.execute('UPDATE statement_watermarks SET ' + ', '.join(f'{k}=?' for k in saved if k != 'account_id')
                    + ' WHERE account_id=?', [saved[k] for k in saved if k != 'account_id'] + [bank])
    tally, tables = dict(command=0, census=0, verify=0, accepted=0), {}
    with session(client) as s:
        db = s.company
        accounts, identifiers, rows = loading.gather(db, bank)
        reason, scope = watermark.state(db, bank, accounts, identifiers)
        assert reason == 'holds' and scope.documents and any(scope.written.values())
        full_graph = adapters.graph(type('S', (), {'company': db})(), identifiers)
        scoped_graph = adapters.graph(type('S', (), {'company': db})(), identifiers, only=scope.documents,
                                      verified=watermark.verified(db, rows, identifiers, scope.documents))
        whole = loading.referenced_rows(db)
        assert outcome(validate, rows, source=full_graph, referenced_rows=whole) is None
        assert outcome(validate, rows, source=scoped_graph, referenced_rows=whole, suffix=Suffix(scope.written)) is None
        for what, bad in damaged_rows(rows):
            full = outcome(validate, bad, source=full_graph, referenced_rows=whole)
            if full is None:
                tally['accepted'] += 1
                continue
            written, deleted, changed = suffix_for(rows, bad, scope.written)
            scoped = outcome(validate, bad, source=scoped_graph, referenced_rows=whole, suffix=written)
            table = re.split(r'[.\[ ]', what)[0]
            if scoped is not None:
                found = 'command'
            elif deleted:
                found = 'census'
            else:
                assert changed, (what, full)
                found = 'verify'
            tally[found] += 1
            tables.setdefault(table, dict(command=0, census=0, verify=0))[found] += 1
        # The ledger under the stored rows, damaged within the scope: refused by the per-command proof.
        for what, bad in damaged_graphs(scoped_graph):
            bad = adapters.Graph(dict(bad.rows), bad.accounts, scoped_graph.scope, scoped_graph.verified)
            if outcome(validate, rows, source=bad, referenced_rows=whole, suffix=Suffix(scope.written)) is not None:
                tally['command'] += 1
            else:
                tally['accepted'] += 1
        for what, bad in damaged_references(whole):
            got = outcome(validate, rows, source=scoped_graph, referenced_rows=bad, suffix=Suffix(scope.written))
            if outcome(validate, rows, source=full_graph, referenced_rows=bad) is not None:
                assert got is not None, what
                tally['command'] += 1
    print('\ndamaged variants refused --', tally)
    for table, found in sorted(tables.items()):
        print(f'  {table}: {found}')
    assert tally['command'] > 1000 and tally['verify'] > 100 and tally['census'] > 10, tally


# ------------------------------------------------------------------ old history damaged in place


def tamper(path, table, sql, params=()):
    """Change a row behind Bookflow: lift the table's guards, write, put them back word for word."""
    with sqlite3.connect(path) as raw:
        guards = raw.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchall()
        for name, _ in guards:
            raw.execute(f'DROP TRIGGER "{name}"')
        raw.execute(sql, params)
        assert raw.execute('SELECT changes()').fetchone()[0] == 1
        for _, text in guards:
            raw.execute(text)


def test_old_history_damaged_in_place_is_found_by_verify_and_by_a_full_proof_a_code_or_digest_change_forces(ledger, monkeypatch):
    client, bank = ledger['client'], ledger['bank']
    owner_money(ledger, '2026-06-01', '1000.00')
    june_check = check(ledger, '2026-06-10', '250.00')
    reconcile(ledger, '2026-06-30', opening_date='2026-05-31')
    check(ledger, '2026-07-08', '75.00')
    path = company_db(client)
    with sqlite3.connect(path) as raw:
        before = raw.execute('SELECT digest FROM statement_watermarks').fetchone()[0]
        old = raw.execute('SELECT id, display_snapshot FROM reconciliation_effect_versions WHERE transaction_id=?',
                          (june_check['id'],)).fetchone()
    shown = json.loads(old[1]); shown['memo'] = 'Changed behind the books'
    tamper(path, 'reconciliation_effect_versions', 'UPDATE reconciliation_effect_versions SET display_snapshot=? WHERE id=?',
           (json.dumps(shown, separators=(',', ':'), sort_keys=True), old[0]))

    # The per-command proof does not look at verified rows again: the scoped load accepts, and says so.
    with session(client) as s:
        loading.load(s, bank)
        assert loading.last_proof['proof'] == 'scoped'
    # (a) `company verify` proves everything, and refuses it; so does a backup.
    out = run(client, 'company verify', dict(account=bank))
    assert not out['ok'] and out['accounts'][0]['outcome'] == 'failed' and out['accounts'][0]['check'] == 'display_facts'
    with pytest.raises(BookflowError) as refused:
        run(client, 'company backup', {})
    assert refused.value.code == 'E_BACKUP_INVALID' and refused.value.details['check'] == 'statement_proof'
    assert refused.value.details['accounts'] == [dict(account_id=bank, check='display_facts')]

    # (b) Other derivation or validation code: the watermark is stale, the full proof runs and refuses.
    monkeypatch.setattr(watermark, '_code', 'f' * 64)
    with session(client) as s:
        with pytest.raises(preparation.ReconciliationError) as found:
            loading.load(s, bank)
        assert found.value.details['check'] == 'display_facts' and loading.last_proof['reason'] == 'stale'
    monkeypatch.undo()

    # (c) The digest no longer matches the file: broken, the full proof runs and refuses.
    with sqlite3.connect(path) as raw:
        raw.execute('UPDATE statement_watermarks SET digest=?', ('0' * 64,))
    with session(client) as s:
        with pytest.raises(preparation.ReconciliationError) as found:
            loading.load(s, bank)
        assert found.value.details['check'] == 'display_facts' and loading.last_proof['reason'] == 'broken'
    with sqlite3.connect(path) as raw:
        raw.execute('UPDATE statement_watermarks SET digest=?', (before,))
    # ... and so does a row deleted from the verified prefix (the census counts it).
    with sqlite3.connect(path) as raw:
        top = raw.execute("SELECT max(rowid) FROM audit_entries").fetchone()[0]
        first = raw.execute("SELECT min(rowid) FROM posting_line_sources").fetchone()[0]
    tamper(path, 'posting_line_sources', 'DELETE FROM posting_line_sources WHERE rowid=?', (first,))
    with session(client) as s:
        assert watermark.state(s.company, bank, *loading.gather(s.company, bank)[:2])[0] == 'broken'
    assert top


def test_a_change_to_the_ledger_setup_or_schema_breaks_the_watermark(ledger):
    client, bank = ledger['client'], ledger['bank']
    owner_money(ledger, '2026-06-01', '1000.00')
    reconcile(ledger, '2026-06-30', opening_date='2026-05-31')
    path = company_db(client)
    with session(client) as s:
        assert watermark.state(s.company, bank, *loading.gather(s.company, bank)[:2])[0] == 'holds'
    with sqlite3.connect(path) as raw:
        raw.execute('CREATE INDEX r180_witness ON reconciliation_claims (event_id)')
    with session(client) as s:
        assert watermark.state(s.company, bank, *loading.gather(s.company, bank)[:2])[0] == 'broken'
    with sqlite3.connect(path) as raw:
        raw.execute('DROP INDEX r180_witness')
    with session(client) as s:
        assert watermark.state(s.company, bank, *loading.gather(s.company, bank)[:2])[0] == 'holds'
    # A header changed in place, with nothing appended: that one document is derived again.
    with sqlite3.connect(path) as raw:
        doc = raw.execute("SELECT transaction_id FROM reconciliation_effect_versions WHERE account_id=? LIMIT 1", (bank,)).fetchone()[0]
        raw.execute("UPDATE transactions SET updated_at=updated_at||'x' WHERE id=?", (doc,))
    with session(client) as s:
        reason, scope = watermark.state(s.company, bank, *loading.gather(s.company, bank)[:2])
        assert reason == 'holds' and scope.documents == {doc}


# ------------------------------------------------------------------ co0101


def test_co0101_adds_the_watermark_table_and_changes_no_row(ledger, tmp_path):
    import importlib
    from tests.test_negative_stock_migration import _at
    m = importlib.import_module('bookflow.storage.company_migrations.versions.0101_statement_watermarks')
    from sqlalchemy.dialects.sqlite import dialect
    from sqlalchemy.schema import CreateTable
    assert m.DDL == (str(CreateTable(sch.statement_watermarks).compile(dialect=dialect())),)
    owner_money(ledger, '2026-06-01', '1000.00')
    reconcile(ledger, '2026-06-30', opening_date='2026-05-31')
    copy = tmp_path / 'rewound101.db'
    with sqlite3.connect(company_db(ledger['client'])) as source, sqlite3.connect(copy) as target:
        source.backup(target)
    with sqlite3.connect(copy) as conn:
        assert conn.execute('SELECT count(*) FROM statement_watermarks').fetchone()[0] == 1
        conn.execute('DROP TABLE statement_watermarks')
        conn.execute("UPDATE alembic_version SET version_num='co0100'")
        conn.commit()
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                            "AND name != 'alembic_version'")]
        before = {name: sorted(conn.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr) for name in names}
    _at(copy, 'co0101')
    with sqlite3.connect(copy) as conn:
        for name in names:
            assert sorted(conn.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr) == before[name], name
        assert conn.execute('SELECT count(*) FROM statement_watermarks').fetchone()[0] == 0
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
