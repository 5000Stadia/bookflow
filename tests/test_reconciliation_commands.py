"""The four commands, end to end, against a real company through the ordinary dispatch path.

What is checked is not that rows appeared. It is that the account the commands leave behind
loads, proves itself against its own general ledger, and carries a certificate whose captured
population is the account as it stood -- because a certificate that does not tie out is worse
than no certificate at all.
"""
import json

import pytest

from bookflow.company import reconciliation_loading as loading
from bookflow.company import reconciliation_preparation as preparation
from bookflow.company import reconciliation_queries as queries
from bookflow.commands.reconcile_cmds import dependency_guard
from bookflow.core.ids import new_id
from tests.test_deposit_lifecycle import driver  # noqa: F401
from tests.test_reconciliation_storage_validation import (  # noqa: F401
    account, journal, pair, run, COMPANY,
)

RECONCILE_COMMANDS = frozenset(('reconcile opening start', 'reconcile start', 'reconcile mark',
                                'reconcile finish'))


def marks(driver, identity, action='mark'):
    """Every movement the draft can see, in the shape `reconcile mark` takes them."""
    with driver.session() as s:
        snapshot = loading.load(s, _account(s, identity))
        from bookflow.company import reconciliation_drafts as drafts
        draft = drafts.load(snapshot, identity, authority_transactions=snapshot.authority_transactions)
        page = queries.candidates(snapshot, draft, _filters(),
                                  authority_transactions=snapshot.authority_transactions, limit=200)
    return [dict(movement=row.movement.model_dump(mode='json'),
                 group_fingerprint=row.group_fingerprint, action=action) for row in page.items]


def _filters():
    from bookflow.company import reconciliation_commands_models as m
    return m.CandidateFilter()


def _account(s, identity):
    from bookflow.company import schema as c
    return s.company.conn.execute(c.reconciliation_drafts.select()
                                  .where(c.reconciliation_drafts.c.id == identity)
                                  ).mappings().first()['account_id']


def prepared(driver, identity):
    """The two things a prepared change has to state: the facts it saw and the chain it depends on."""
    with driver.session() as s:
        snapshot = loading.load(s, _account(s, identity))
        from bookflow.company import reconciliation_drafts as drafts
        draft = drafts.load(snapshot, identity, authority_transactions=snapshot.authority_transactions)
        return dict(expected_facts_fingerprint=preparation.fingerprint(snapshot, draft),
                    dependency_guard=dependency_guard(draft))


@pytest.fixture
def reconciled(client, driver):
    """An adopted account with one certified statement over two ordinary journals."""
    bank = account(client, 'Command bank')
    equity = account(client, 'Command equity', 'equity')
    journal(client, pair(bank, equity, '10'), date='2026-01-10')
    journal(client, pair(bank, equity, '2.50'), date='2026-01-20')
    opening = run(client, 'reconcile opening start', dict(
        operation_key=new_id(), account=bank, opening_date='2026-01-01', entered_balance=0,
        evidence=dict(format=1, statement_reference=None, entered_text='Adopted at zero'),
        references=[]))
    statement = run(client, 'reconcile start', dict(
        operation_key=new_id(), account=bank, statement_date='2026-01-31', ending_balance=1250,
        opening_draft_id=opening['draft']['id']))
    marked = run(client, 'reconcile mark', dict(
        operation_key=new_id(), draft=statement['draft']['id'], expected_version=1,
        entries=marks(driver, statement['draft']['id'])))
    done = run(client, 'reconcile finish', dict(
        operation_key=new_id(), draft=statement['draft']['id'],
        expected_version=marked['draft']['version'],
        **prepared(driver, statement['draft']['id'])))
    return dict(bank=bank, opening=opening, statement=statement, marked=marked, done=done)


def test_the_four_commands_certify_a_statement_that_ties_to_the_ledger(reconciled, driver):
    done = reconciled['done']
    assert done['totals']['difference'] == 0
    assert done['totals']['cleared_balance'] == done['totals']['ending_balance'] == 1250
    assert done['totals']['positive_count'] == 2 and done['totals']['negative_count'] == 0
    with driver.session() as s:
        snapshot = loading.load(s, reconciled['bank'])
        values, gl = preparation.account_population(snapshot, reconciled['bank'], '2026-01-31')
    assert gl == 1250 and sum(v['signed_debit'] for v in values) == 1250
    certificate = snapshot.by('certificates')[done['certificate_id']]
    captured = json.loads(certificate['captured_source_snapshot']) \
        if isinstance(certificate['captured_source_snapshot'], str) else certificate['captured_source_snapshot']
    assert captured['signed_gl_total'] == 1250
    assert set(captured['version_ids']) == {v['id'] for v in values}


def test_the_account_chain_and_its_claims_are_what_the_certificate_says(reconciled, driver):
    done = reconciled['done']
    with driver.session() as s:
        snapshot = loading.load(s, reconciled['bank'])
    state = snapshot.rows['accounts'][0]
    assert (state['version'], state['opening_id'], state['head_certificate_id']) == \
        (1, done['opening_id'], done['certificate_id'])
    assert [v['certificate_id'] for v in snapshot.rows['active_certificates']] == [done['certificate_id']]
    # Every movement the statement cleared is claimed, so a second statement cannot clear it again.
    selected = {v['key_id'] for v in snapshot.rows['certificate_members']
                if v['classification'] == 'selected'}
    assert selected and {v['key_id'] for v in snapshot.rows['current_members']} == selected
    assert {d['state'] for d in snapshot.rows['drafts']} == {'consumed'}


def test_a_statement_whose_difference_is_not_zero_is_refused(client, driver):
    bank = account(client, 'Unbalanced bank')
    equity = account(client, 'Unbalanced equity', 'equity')
    journal(client, pair(bank, equity, '10'), date='2026-01-10')
    opening = run(client, 'reconcile opening start', dict(
        operation_key=new_id(), account=bank, opening_date='2026-01-01', entered_balance=0,
        evidence=dict(format=1, statement_reference=None, entered_text='Adopted at zero'),
        references=[]))
    statement = run(client, 'reconcile start', dict(
        operation_key=new_id(), account=bank, statement_date='2026-01-31', ending_balance=9999,
        opening_draft_id=opening['draft']['id']))
    run(client, 'reconcile mark', dict(operation_key=new_id(), draft=statement['draft']['id'],
                                       expected_version=1,
                                       entries=marks(driver, statement['draft']['id'])))
    with pytest.raises(Exception) as raised:
        run(client, 'reconcile finish', dict(
            operation_key=new_id(), draft=statement['draft']['id'], expected_version=2,
            **prepared(driver, statement['draft']['id'])))
    assert 'E_RECONCILIATION_DIFFERENCE' in str(raised.value)


def test_a_source_that_moves_after_preparation_is_refused_at_the_write(client, driver):
    """What was prepared is what is certified, or nothing is."""
    bank = account(client, 'Moving bank')
    equity = account(client, 'Moving equity', 'equity')
    journal(client, pair(bank, equity, '10'), date='2026-01-10')
    opening = run(client, 'reconcile opening start', dict(
        operation_key=new_id(), account=bank, opening_date='2026-01-01', entered_balance=0,
        evidence=dict(format=1, statement_reference=None, entered_text='Adopted at zero'),
        references=[]))
    statement = run(client, 'reconcile start', dict(
        operation_key=new_id(), account=bank, statement_date='2026-01-31', ending_balance=1000,
        opening_draft_id=opening['draft']['id']))
    run(client, 'reconcile mark', dict(operation_key=new_id(), draft=statement['draft']['id'],
                                       expected_version=1,
                                       entries=marks(driver, statement['draft']['id'])))
    guards = prepared(driver, statement['draft']['id'])
    # The books move between preparing the certificate and writing it.
    journal(client, pair(bank, equity, '3'), date='2026-01-15')
    with pytest.raises(Exception) as raised:
        run(client, 'reconcile finish', dict(
            operation_key=new_id(), draft=statement['draft']['id'], expected_version=2, **guards))
    assert 'E_RECONCILIATION_SELECTION_STALE' in str(raised.value)


def test_the_write_cannot_get_past_a_prover_that_refuses(client, driver, monkeypatch):
    """The prover is on the write path, not beside it.

    What makes a stored capture proven is that it went through the proof, the same way what
    makes a posting write materialized is that it went through the trigger. Take the proof away
    and the certificate must not be written at all.
    """
    from bookflow.company import reconciliation_capture as capture
    bank = account(client, 'Bypass bank')
    equity = account(client, 'Bypass equity', 'equity')
    journal(client, pair(bank, equity, '10'), date='2026-01-10')
    opening = run(client, 'reconcile opening start', dict(
        operation_key=new_id(), account=bank, opening_date='2026-01-01', entered_balance=0,
        evidence=dict(format=1, statement_reference=None, entered_text='Adopted at zero'),
        references=[]))
    statement = run(client, 'reconcile start', dict(
        operation_key=new_id(), account=bank, statement_date='2026-01-31', ending_balance=1000,
        opening_draft_id=opening['draft']['id']))
    run(client, 'reconcile mark', dict(operation_key=new_id(), draft=statement['draft']['id'],
                                       expected_version=1,
                                       entries=marks(driver, statement['draft']['id'])))
    guards = prepared(driver, statement['draft']['id'])

    def refuses(*a, **k):
        raise AssertionError('the prover refused')

    monkeypatch.setattr(capture, 'population', refuses)
    with pytest.raises(Exception):
        run(client, 'reconcile finish', dict(
            operation_key=new_id(), draft=statement['draft']['id'], expected_version=2, **guards))
    with driver.session() as s:
        snapshot = loading.load(s, bank)
    assert not snapshot.rows['certificates'] and not snapshot.rows['openings']
    assert {d['state'] for d in snapshot.rows['drafts']} == {'open'}
