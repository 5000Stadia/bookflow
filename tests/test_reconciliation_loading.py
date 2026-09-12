"""The Snapshot every reconciliation read runs against, built from a database.

A capture is proven against the live graph where it is written and against its own stored rows
where it is read. Both halves of that are here: that a loader can still build a Snapshot after
the ledger has moved under a certificate -- the ordinary case, and the only one in which
`reconciliation_reports.project` has anything to report -- and that the write still cannot store
a capture the live graph does not agree with.
"""
import copy
import json

import pytest
import sqlalchemy as sa

from bookflow.company import schema as sch
from bookflow.company import reconciliation_adapters as adapters
from bookflow.company import reconciliation_loading as loading
from bookflow.company import reconciliation_preparation as preparation
from bookflow.company import reconciliation_reports as reports
from bookflow.company.reconciliation_storage_validation import (
    InvalidStorage, canonical, population_fingerprint, validate)
from tests.test_deposit_lifecycle import driver  # noqa: F401
from tests.test_reconciliation_storage_validation import (  # noqa: F401
    account, journal, pair, run, references, aggregate, blank, COMPANY,
)

# The aggregate tables only: the effect rows underneath were written by the production
# materializer when the document posted, and are already in the database.
ORDER = ('operations', 'events', 'event_effects', 'draft_revisions', 'drafts', 'openings',
         'certificates', 'certificate_members', 'event_accounts', 'accounts',
         'active_certificates', 'claims', 'current_members', 'operation_items',
         'operation_accounts', 'operation_transactions', 'operation_drafts', 'operation_openings',
         'operation_certificates')


def owned(session, transaction, *, account=None):
    """The stored reconciliation rows the production materializer wrote, for one document or account."""
    out = blank()
    for name in out:
        out[name] = [dict(v) for v in session.company.conn.execute(
            sa.select(sch.metadata.tables['reconciliation_' + name])).mappings()]
    if account is not None:
        owners = {v['key_id'] for v in out['effect_versions'] if v['account_id'] == account}
        keys = {v['id'] for v in out['keys'] if v['id'] in owners}
    else:
        keys = {v['id'] for v in out['keys'] if v['transaction_id'] == transaction}
    out['keys'] = [v for v in out['keys'] if v['id'] in keys]
    out['effect_versions'] = [v for v in out['effect_versions'] if v['key_id'] in keys]
    versions = {v['id'] for v in out['effect_versions']}
    out['effect_heads'] = [v for v in out['effect_heads'] if v['key_id'] in keys]
    for name in ('commercial_versions', 'deposit_versions'):
        out[name] = [v for v in out[name] if v['id'] in versions]
    for name in ('effect_legs', 'effect_sources'):
        out[name] = [v for v in out[name] if v['version_id'] in versions]
    return out


def certified(client, driver, label):
    """A bank account carrying one posted document and a certificate captured over it."""
    bank = account(client, label + ' bank')
    equity = account(client, label + ' equity', 'equity')
    doc = journal(client, pair(bank, equity, '10'))
    with driver.session() as s:
        rows = owned(s, doc['id'])
        graph = adapters.graph(s, [doc['id']])
        refs = references(s)
    stored = aggregate(rows, graph, bank)
    captures = {v['id']: graph for name in ('openings', 'certificates') for v in stored[name]}
    validate(stored, source=graph, captured_graphs=captures, referenced_rows=refs)
    return bank, doc, stored, graph, refs, captures


def doctored(stored, **changes):
    bad = copy.deepcopy(stored)
    pop = json.loads(bad['certificates'][0]['captured_source_snapshot'])
    pop.update(changes)
    bad['certificates'][0]['captured_source_snapshot'] = canonical(pop)
    return bad


def test_an_account_with_no_reconciliation_yet_loads_and_proves_its_own_balance(client, driver):
    bank = account(client, 'Loader bank')
    equity = account(client, 'Loader equity', 'equity')
    journal(client, pair(bank, equity, '10'))
    journal(client, pair(bank, equity, '2.50'))
    with driver.session() as s:
        snapshot = loading.load(s, bank)
        values, gl = preparation.account_population(snapshot, bank, '9999-12-31')
    assert gl == 1250 and sum(v['signed_debit'] for v in values) == 1250
    assert len(values) == 2 and {v['account_id'] for v in values} == {bank}


def test_the_loader_reads_only_the_account_it_was_asked_for(client, driver):
    first = account(client, 'Scoped bank one')
    second = account(client, 'Scoped bank two')
    equity = account(client, 'Scoped equity', 'equity')
    journal(client, pair(first, equity, '10'))
    journal(client, pair(second, equity, '7'))
    with driver.session() as s:
        snapshot = loading.load(s, first)
    assert {v['account_id'] for v in snapshot.rows['effect_versions']} == {first}, \
        "a second account's documents have no business in this population"
    assert {v['id'] for v in snapshot.source.rows['transactions']} == \
        {v['transaction_id'] for v in snapshot.rows['effect_versions']}


def test_a_correction_under_a_certificate_no_longer_locks_the_account_out(client, driver):
    """The blocker this row opened with, inverted into the behaviour that replaced it."""
    bank, doc, stored, _, _, _ = certified(client, driver, 'Moved')
    with driver.session() as s:
        for name in ORDER:
            for row in stored[name]:
                s.company.raw.execute(
                    'INSERT INTO reconciliation_' + name + ' (' + ','.join(row) + ') VALUES ('
                    + ','.join('?' for _ in row) + ')', tuple(row.values()))
    run(client, 'journal update', dict(journal=doc['id'], expected_version=1, memo='Moved on'))
    with driver.session() as s:
        snapshot = loading.load(s, bank)
        certified_version = stored['certificate_members'][0]['version_id']
        # The certificate still names the version it certified, and that version is still stored.
        assert certified_version in {v['id'] for v in snapshot.rows['effect_versions']}
        # It is no longer the head, which is the point: the ledger moved, the account is still
        # readable, and that is the only state in which the report has anything to report.
        assert certified_version not in {v['version_id'] for v in snapshot.rows['effect_heads']}
        report = reports.project(snapshot, stored['certificates'][0]['id'],
                                 authority_transactions=snapshot.authority_transactions)
    assert report.as_certified.difference == 0


def test_a_capture_the_live_graph_does_not_agree_with_cannot_be_written(client, driver):
    """The proof moved to the write; this is the write refusing what the read used to."""
    _, _, stored, graph, refs, captures = certified(client, driver, 'Refused')
    for changes, rule in ((dict(signed_gl_total=1001), 'capture_gl'),
                          (dict(version_ids=[]), 'capture_population_completeness')):
        with pytest.raises(InvalidStorage, match=rule):
            validate(doctored(stored, **changes), source=graph, captured_graphs=captures,
                     referenced_rows=refs)


def test_a_certificate_total_the_stored_members_do_not_add_up_to_is_refused_without_a_graph(client, driver):
    """`certificate_gl`: the tie the read would otherwise have lost along with the graph."""
    _, _, stored, graph, refs, _ = certified(client, driver, 'Tied')
    with pytest.raises(InvalidStorage, match='certificate_gl'):
        validate(doctored(stored, signed_gl_total=1001), source=graph, captured_graphs={},
                 referenced_rows=refs)


def test_a_population_the_stored_members_do_not_spell_is_refused_without_a_graph(client, driver):
    """`capture_fingerprint`, recomputed from the rows rather than asked of the present."""
    _, _, stored, graph, refs, _ = certified(client, driver, 'Fingerprinted')
    with pytest.raises(InvalidStorage, match='capture_fingerprint'):
        validate(doctored(stored, source_fingerprint=population_fingerprint([])),
                 source=graph, captured_graphs={}, referenced_rows=refs)


def test_the_prover_is_the_only_thing_that_can_produce_a_capture(client, driver):
    """A blob assembled any other way is not a proven capture, so nothing else assembles one."""
    from bookflow.company import reconciliation_capture as capture
    bank, doc, stored, _, _, _ = certified(client, driver, 'Proven')
    with driver.session() as s:
        snapshot = loading.load(s, bank)
        produced = capture.population(snapshot, bank, '2026-01-31', opening=False)
    hand_built = json.loads(stored['certificates'][0]['captured_source_snapshot'])
    assert produced == hand_built, 'the prover and the hand-built expectation describe one population'


def test_a_capture_that_leaves_a_movement_out_is_rejected_even_though_it_adds_up(client, driver):
    """Consistency is not completeness, and this is the difference between them.

    Both movements are stored; the certificate simply does not mention the second. Its members,
    population, total and fingerprint all agree about the smaller world, so every stored-row tie
    is satisfied. Only deriving the heads from the graph catches it -- which is why the write
    supplies the graph and the read does not have to, and why hash plus sum is never evidence
    that a capture is whole.
    """
    bank = account(client, 'Partial bank')
    equity = account(client, 'Partial equity', 'equity')
    journal(client, pair(bank, equity, '10'), date='2026-01-10')
    journal(client, pair(bank, equity, '4'), date='2026-01-12')
    with driver.session() as s:
        identifiers = sorted(loading.statement_transactions(s.company, bank))
        rows = owned(s, None, account=bank)
        graph = adapters.graph(s, identifiers)
        refs = references(s)
    stored = aggregate(rows, graph, bank)
    assert len(stored['effect_versions']) == 2 and len(stored['certificate_members']) == 1, \
        'both movements are stored; only the capture describes a smaller world'
    validate(stored, source=graph, captured_graphs={}, referenced_rows=refs)
    captures = {v['id']: graph for n in ('openings', 'certificates') for v in stored[n]}
    with pytest.raises(InvalidStorage, match='capture_population_completeness'):
        validate(stored, source=graph, captured_graphs=captures, referenced_rows=refs)


def test_a_future_dated_movement_stays_in_the_population_and_out_of_the_general_ledger(client, driver):
    """A certificate keeps what it cannot yet clear, and does not count it."""
    from bookflow.company import reconciliation_capture as capture
    bank = account(client, 'Future bank')
    equity = account(client, 'Future equity', 'equity')
    journal(client, pair(bank, equity, '1'), date='2026-01-10')
    journal(client, pair(bank, equity, '0.50'), date='2026-03-15')
    with driver.session() as s:
        snapshot = loading.load(s, bank)
        produced = capture.population(snapshot, bank, '2026-01-31', opening=False)
        members = capture.members(snapshot, bank, '2026-01-31', opening=False)
    assert len(members) == 2, 'the later movement belongs to the population'
    assert len(produced['version_ids']) == 2
    assert produced['signed_gl_total'] == 100, 'and no part of the general ledger at this cutoff'
