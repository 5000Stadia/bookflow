"""Stored statement effects are the ledger, on every command and after an upgrade.

Reconciliation reads a second representation of the same postings, and `reconciliation_
preparation` refuses an account whose stored current heads do not sum to the general ledger.
So the bar for every test here is equality with an independently derived expectation, never
"some rows appeared": what is compared is the complete set of derived effects against the
complete set of stored ones, for the whole company, after real commands.
"""
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

import bookflow
from bookflow.company import schema as c
from bookflow.company import reconciliation_adapters as adapters
from bookflow.company import reconciliation_models as models
from bookflow.company import reconciliation_materialization as materialization
from bookflow.company import reconciliation_preparation as preparation
from bookflow.company.reconciliation_storage_validation import validate
from bookflow.core.errors import BookflowError
from bookflow.storage.engine import open_database

from tests.test_deposit_command import books, receipts_in_undeposited_funds, COMPANY
from tests.test_reconciliation_storage_migration import old, BASE  # noqa: F401  (module fixture)


def path_of(client, company=COMPANY):
    return Path(client.run('company show', {}, company=company)['path']) / 'company.db'


def identities(db):
    return sorted(row[0] for row in db.conn.execute(sa.select(c.transactions.c.id)))


def derived(db):
    """Every statement effect the ledger says exists, read straight from the adapters.

    This is deliberately not `reconciliation_materialization.rows_for`: an expectation built
    by the code under test proves only that it is consistent with itself.
    """
    source = SimpleNamespace(company=db)
    identifiers = identities(db)
    history, current = {}, {}
    for offset in range(0, len(identifiers), 100):
        graph = adapters.graph(source, identifiers[offset:offset + 100])
        old_values, now = adapters.enumerate_graph(graph)
        for value in old_values:
            history[value.ref.producer, value.ref.transaction_id, value.ref.role,
                    value.ref.component_id, value.version_id] = (
                value.account_id, value.signed_debit, int(value.active), value.effective_date,
                value.transition_batch_id, value.business_batch_id)
        for value in now:
            current[value.ref.producer, value.ref.transaction_id, value.ref.role,
                    value.ref.component_id] = value.version_id
    return history, current


# A funding key names the document itself, so the coalesce falls through to it; the CHECK
# constraints make the other two shapes exclusive, so it can never fall through for them.
STORED_VERSIONS = sa.text("""
    SELECT k.producer, k.transaction_id, k.role, COALESCE(k.deposit_key_id, k.commercial_line_id, k.transaction_id),
           v.source_version, v.account_id, v.signed_debit, v.active, v.effective_date,
           v.transition_batch_id, v.business_batch_id
    FROM reconciliation_effect_versions v JOIN reconciliation_keys k ON k.id = v.key_id""")

STORED_HEADS = sa.text("""
    SELECT k.producer, k.transaction_id, k.role, COALESCE(k.deposit_key_id, k.commercial_line_id, k.transaction_id),
           v.source_version
    FROM reconciliation_effect_heads h
    JOIN reconciliation_keys k ON k.id = h.key_id
    JOIN reconciliation_effect_versions v ON v.id = h.version_id""")


def stored(db):
    history = {tuple(row[:5]): tuple(row[5:]) for row in db.conn.execute(STORED_VERSIONS)}
    current = {tuple(row[:4]): row[4] for row in db.conn.execute(STORED_HEADS)}
    return history, current


def general_ledger(db):
    """Each statement account's signed position, computed from posting lines alone."""
    return {row[0]: row[1] for row in db.conn.execute(sa.text("""
        SELECT l.account_id, SUM(l.debit_minor_units - l.credit_minor_units)
        FROM posting_lines l JOIN accounts a ON a.id = l.account_id
        WHERE a.type IN ('bank', 'credit_card') GROUP BY l.account_id"""))}


def head_totals(db):
    return {row[0]: row[1] for row in db.conn.execute(sa.text("""
        SELECT v.account_id, SUM(v.signed_debit) FROM reconciliation_effect_heads h
        JOIN reconciliation_effect_versions v ON v.id = h.version_id
        WHERE v.active = 1 GROUP BY v.account_id"""))}


def referenced(db):
    """Old-table rows every new-table foreign key points at, plus the company's currency."""
    names = {fk.column.table.name for name, table in c.metadata.tables.items()
             if name.startswith('reconciliation_') for fk in table.foreign_keys
             if not fk.column.table.name.startswith('reconciliation_')}
    names.add('company_info')
    return {name: [dict(row) for row in db.conn.execute(sa.select(c.metadata.tables[name])).mappings()]
            for name in names}


def snapshot_rows(db):
    return {name.removeprefix('reconciliation_'):
            [dict(row) for row in db.conn.execute(sa.select(table)).mappings()]
            for name, table in c.metadata.tables.items() if name.startswith('reconciliation_')}


def audit(path):
    """The whole bar, applied to a real company file.

    Nothing is owed; every derived effect is stored and nothing else is; every statement
    account's stored heads sum to its general ledger position; and the stored aggregate
    satisfies the private validator and the opening gate that every reconciliation command
    runs before it reads anything.
    """
    with open_database(path, writable=True) as db:
        assert materialization.pending_documents(db) == []
        expected_history, expected_current = derived(db)
        actual_history, actual_current = stored(db)
        assert expected_history, 'the fixture posted nothing a statement can show'
        assert actual_history == expected_history
        assert actual_current == expected_current
        ledger = general_ledger(db)
        assert head_totals(db) == {account: total for account, total in ledger.items() if total}
        rows, moved = materialization.rows_for(db, identities(db))
        assert not any(rows.values()) and not moved, 'materialization is not idempotent'

        identifiers = identities(db)
        graph = adapters.graph(SimpleNamespace(company=db), identifiers)
        references = referenced(db)
        aggregate = snapshot_rows(db)
        validate(aggregate, source=graph, referenced_rows=references)
        snapshot = preparation.snapshot(aggregate, source=graph, captured_graphs={},
                                        referenced_rows=references, authority_transactions=identifiers)
        for account, total in ledger.items():
            values, gl = preparation.account_population(snapshot, account, '9999-12-31')
            assert sum(value['signed_debit'] for value in values) == total
            assert gl == (-total if graph.accounts[account]['type'] == 'credit_card' else total)
        return ledger


def test_storage_admits_exactly_the_producers_the_adapters_can_derive():
    """A producer that gains an adapter and not a key shape is derivable and unstorable.

    That is not hypothetical: `_funding` shipped with the statement adapters while
    `reconciliation_keys` still admitted the five producers co0022 froze, so a bill payment
    could be derived and then refused at the table -- which would have taken every bank
    account that has ever paid a bill out of reconciliation. The producer set and the shapes
    are read from the adapter registry here so the next one cannot land the same way.
    """
    off_bank = {name for name, rule in adapters.REGISTRY.items() if rule is adapters._offbank}
    funding = {name for name, rule in adapters.REGISTRY.items() if rule is adapters._funding}
    deposit = {name for name, rule in adapters.REGISTRY.items() if rule is adapters._deposit}
    assert set(models.PRODUCER_ROLES) == set(adapters.REGISTRY) - off_bank
    assert set(models.FUNDING_PRODUCERS) == funding
    assert set(models.DEPOSIT_PRODUCERS) == deposit
    assert set(models.COMMERCIAL_PRODUCERS) == set(models.PRODUCER_ROLES) - funding - deposit
    assert all(models.PRODUCER_ROLES[name] == ('funding',) for name in funding)
    declared = {role for roles in models.PRODUCER_ROLES.values() for role in roles}
    assert declared == set(models.Role.__args__), 'a role no producer can name, or one with no role'
    assert not adapters.UNCOVERED_PRODUCERS
    # The same set, once more, where a person meets it: the candidate list's producer filter.
    # It shipped naming five, so the bill payments already sitting in a bookkeeper's candidate
    # list could not be filtered for by the only name they have.
    from bookflow.company import reconciliation_commands_models as commands
    import typing
    offered = typing.get_args(typing.get_args(commands.CandidateFilter.model_fields['producer'].annotation)[0])
    assert set(offered) == set(models.PRODUCER_ROLES)


def test_every_posting_family_is_materialized_by_its_own_command(books):
    """Invoice, payment, counter sale, deposit, card charge, cheque, bill payment and a void.

    One document of every family the adapters name a rule for, posted through the registered
    commands a person actually uses, and then the whole company checked against the ledger.
    """
    client = books['client']
    invoice, payment, sale = receipts_in_undeposited_funds(books)
    available = client.run('deposit sources', dict(date='2026-06-03'), company=COMPANY)
    document = dict(mode='inline', deposit_to=books['bank'], date='2026-06-03', memo='Saturday receipts',
                    sources=[dict(source_type=row['source_type'], source=row['source'],
                                  expected_version=row['expected_version']) for row in available['items']])
    client.run('deposit post', dict(operation_key='june-deposit', document=document),
               company=COMPANY, reason='bank Saturday receipts')

    card = client.account.create(name='Company Card', type='credit_card', company=COMPANY)['id']
    expense = client.account.create(name='Shop supplies', type='expense', company=COMPANY)['id']
    vendor = client.vendor.create(name='Northside Supply', company=COMPANY)['id']

    client.run('card-charge post', dict(account=card, pay_to=dict(name_type='vendor', name_id=vendor),
                                        date='2026-06-04', amount='60.00',
                                        expenses=[dict(account=expense, amount='60.00')]),
               company=COMPANY, reason='fuel on the card')
    client.run('check post', dict(account=books['bank'], pay_to=dict(name_type='vendor', name_id=vendor),
                                  date='2026-06-05', number='1042', amount='25.00',
                                  expenses=[dict(account=expense, amount='25.00')]),
               company=COMPANY, reason='parts')
    bill = client.run('bill post', dict(vendor=vendor, date='2026-06-05', due_date='2026-07-05',
                                        expenses=[dict(account=expense, amount='40.00')]),
                      company=COMPANY, reason='supplier invoice')
    method = next(row['id'] for row in client.run('payment-method query', dict(limit=50),
                                                  company=COMPANY)['items'] if row['name'] == 'Check')
    client.run('bill pay', dict(date='2026-06-06', funding_account=books['bank'], method=method,
                                bills=[dict(bill=bill['id'])]),
               company=COMPANY, reason='pay the supplier')
    # A receipt banked straight to the bank account, so the `cash` and `control` roles the
    # commercial adapter names are exercised as well as the deposited ones.
    direct = client.run('invoice post', dict(customer=books['customer'], date='2026-06-06',
        lines=[dict(item=books['item'], quantity='1', net_amount='30')]),
        company=COMPANY, reason='another service call')
    client.run('payment receive', dict(customer=books['customer'], date='2026-06-06', amount='30',
        payment_method=books['method'], deposit_to=books['bank'], operation_key='direct-cash',
        applications=dict(mode='inline', items=[dict(invoice=direct['id'], expected_version=1, amount='30')])),
        company=COMPANY, reason='paid straight into the bank')
    client.run('sales-receipt post', dict(customer=books['customer'], deposit_to=books['bank'],
        payment_method=books['method'], date='2026-06-06',
        lines=[dict(item=books['item'], quantity='1', unit_price='20')]),
        company=COMPANY, reason='counter sale banked directly')
    journal = client.run('journal post', dict(date='2026-06-07', lines=[
        dict(account=books['bank'], side='debit', amount='10.00'),
        dict(account=books['income'], side='credit', amount='10.00')]),
        company=COMPANY, reason='bank correction')
    client.run('journal update', dict(journal=journal['id'], expected_version=1, memo='restated'),
               company=COMPANY, reason='restate it')
    client.run('journal void', dict(journal=journal['id'], expected_version=2),
               company=COMPANY, reason='take it back out')

    path = path_of(client)
    audit(path)

    with open_database(path, writable=False) as db:
        producers = {row[0] for row in db.conn.execute(sa.select(c.reconciliation_keys.c.producer))}
        roles = {row[0] for row in db.conn.execute(sa.select(c.reconciliation_keys.c.role))}
        # Each of the three key shapes storage has: a deposit naming a bank effect key, a
        # money-out document naming itself, and a commercial movement naming its entered line.
        # `bill_payment` is the one co0022 could not hold at all.
        assert {'deposit', 'bill_payment', 'journal_entry', 'payment', 'sales_receipt'} <= producers
        assert {'entered', 'cash', 'control', 'main_bank', 'funding'} <= roles
        # A bill and the receipts still sitting in Undeposited Funds touch no statement
        # account, so they correctly have no key at all.
        assert 'bill' not in producers
        shapes = db.conn.execute(sa.text('''
            SELECT count(*) FILTER (WHERE deposit_key_id IS NOT NULL),
                   count(*) FILTER (WHERE commercial_line_id IS NOT NULL),
                   count(*) FILTER (WHERE deposit_key_id IS NULL AND commercial_line_id IS NULL)
            FROM reconciliation_keys''')).one()
        assert all(count > 0 for count in shapes), shapes


def test_a_correction_and_a_void_append_versions_and_move_the_head(books):
    """A movement keeps its identity across a restatement and is retired, never rewritten."""
    client = books['client']
    journal = client.run('journal post', dict(date='2026-02-01', lines=[
        dict(account=books['bank'], side='debit', amount='100.00'),
        dict(account=books['income'], side='credit', amount='100.00')]),
        company=COMPANY, reason='opening deposit')
    path = path_of(client)
    with open_database(path, writable=False) as db:
        first = stored(db)[1]
        assert len(first) == 1
    client.run('journal update', dict(journal=journal['id'], expected_version=1, lines=[
        dict(account=books['bank'], side='debit', amount='120.00',
             line_id=journal['revision']['lines'][0]['line_id']),
        dict(account=books['income'], side='credit', amount='120.00',
             line_id=journal['revision']['lines'][1]['line_id'])]),
        company=COMPANY, reason='it was 120')
    with open_database(path, writable=False) as db:
        history, current = stored(db)
        assert len(current) == 1 and set(current) == set(first)
        assert sorted(value[1] for key, value in history.items() if key[:4] in current) == [10000, 12000]
        assert head_totals(db)[books['bank']] == 12000
    client.run('journal void', dict(journal=journal['id'], expected_version=2),
               company=COMPANY, reason='reverse it')
    with open_database(path, writable=False) as db:
        history, current = stored(db)
        assert len(current) == 1
        retired = history[(*next(iter(current)), current[next(iter(current))])]
        assert retired[1] == 0 and retired[2] == 0 and retired[4] is not None
        assert retired[4] != retired[5], 'a void retires at its reversal batch, not its business one'
        assert head_totals(db) == {}
    audit(path)


def test_a_posting_write_that_skips_the_hook_is_caught_and_then_repaired(books, monkeypatch):
    """What catches a writer that does not materialize is the table it had to write.

    The drain is disabled for one command, which is exactly what a posting writer added later
    and wired somewhere the hook does not run would look like. The SQLite trigger on
    `posting_batches` still fires, the work stays owed, and every reconciliation read refuses
    while it is owed. Nothing about this depends on which module did the writing.
    """
    client = books['client']
    path = path_of(client)
    with monkeypatch.context() as patch:
        patch.setattr(materialization, 'drain_in_command', lambda *a, **k: 0)
        journal = client.run('journal post', dict(date='2026-03-01', lines=[
            dict(account=books['bank'], side='debit', amount='50.00'),
            dict(account=books['income'], side='credit', amount='50.00')]),
            company=COMPANY, reason='a writer that forgot')
    with open_database(path, writable=False) as db:
        assert materialization.pending_documents(db) == [journal['id']]
        assert stored(db) == ({}, {})
        with pytest.raises(BookflowError) as refused:
            materialization.assert_materialized(db)
        assert refused.value.code == 'E_RECONCILIATION_SOURCE_INVALID'
    # Any later command settles it, because the work list outlived the command that made it.
    client.run('account create', dict(name='Petty cash', type='bank'), company=COMPANY,
               reason='unrelated write')
    audit(path)


def test_the_fence_is_the_posting_batch_table_and_not_any_writer(books):
    """A posting batch written by nothing but SQL is still owed materialization."""
    client = books['client']
    journal = client.run('journal post', dict(date='2026-04-01', lines=[
        dict(account=books['bank'], side='debit', amount='5.00'),
        dict(account=books['income'], side='credit', amount='5.00')]),
        company=COMPANY, reason='a movement to copy')
    path = path_of(client)
    with open_database(path, writable=True) as db:
        assert materialization.pending_documents(db) == []
        batch = db.conn.execute(sa.select(c.posting_batches)
                                .where(c.posting_batches.c.transaction_id == journal['id'])).mappings().one()
        db.raw.execute('BEGIN IMMEDIATE')
        columns = ','.join(batch.keys())
        placeholders = ','.join('?' for _ in batch)
        values = ['01JRECONCILEFENCE0000000AA' if key == 'id' else
                  'reversal' if key == 'kind' else
                  batch['id'] if key == 'reverses_batch_id' else batch[key] for key in batch]
        db.raw.execute(f'INSERT INTO posting_batches ({columns}) VALUES ({placeholders})', tuple(values))
        assert materialization.pending_documents(db) == [journal['id']]
        db.raw.execute('ROLLBACK')
        assert materialization.pending_documents(db) == []
    audit(path)


def test_an_existing_company_file_is_backfilled_by_its_upgrade(old, tmp_path):
    """A file whose history predates this storage becomes reconcilable, not merely upgradable.

    `old` is a real company built by the binary that shipped before the reconciliation tables
    existed, with demo history already posted and not one effect row anywhere. The upgrade is
    the ordinary public one; what it has to leave behind is a store that reconciles.
    """
    root = tmp_path / 'root'
    shutil.copytree(old[0], root)
    path = next(root.glob('organizations/*/Demo Plumbing Co/company.db'))
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone() == ('co0021',)
        posted = raw.execute('SELECT count(DISTINCT transaction_id) FROM posting_batches').fetchone()[0]
        assert posted, 'the old company must carry posted history for this to mean anything'
    client = bookflow.connect(data_root=str(root))
    output = client.run('upgrade', {})
    assert output['companies_migrated'] and not output['companies_failed']
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT count(*) FROM reconciliation_effect_versions').fetchone()[0] > 0
        assert raw.execute('SELECT count(*) FROM statement_effect_pending').fetchone()[0] == 0
    audit(path)
    # And the upgraded file goes on materializing: the queue and its triggers came with it.
    company = 'Demo Plumbing Co'
    client.run('journal post', dict(date='2026-08-01', lines=[
        dict(account=_bank(client, company), side='debit', amount='7.00'),
        dict(account=_equity(client, company), side='credit', amount='7.00')]),
        company=company, reason='after the upgrade')
    audit(path)


def _bank(client, company):
    return next(row['id'] for row in client.run('account list', {}, company=company)['items']
                if row['type'] == 'bank')


def _equity(client, company):
    return next(row['id'] for row in client.run('account list', {}, company=company)['items']
                if row['type'] == 'equity')
