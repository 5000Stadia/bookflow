"""`reconcile mark` with `all`: tick everything on the statement in one step (R158).

The blind trial ticked twelve items one command at a time. QuickBooks has "Mark All" on its
reconcile window; here it is an option on the same command, so it is previewable with --dry-run,
versioned like every other draft change, and leaves out what does not belong on this statement:
movements after the statement date and movements an earlier statement already cleared.
"""
import pytest

from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from tests.test_reconciliation_adapters import account, journal, pair, run
from tests.test_reconciliation_commands import prepared

EVIDENCE = dict(format=1, statement_reference=None, entered_text='No earlier statement')


@pytest.fixture
def bank(client):
    bank = account(client, 'Mark all bank')
    equity = account(client, 'Mark all equity', 'equity')
    journal(client, pair(bank, equity, '400.00'), date='2026-09-02')
    journal(client, pair(equity, bank, '174.82'), date='2026-09-10')
    journal(client, pair(bank, equity, '50.10'), date='2026-09-24')
    journal(client, pair(bank, equity, '999.00'), date='2026-10-05')    # after the statement date
    return bank, equity     # 275.28 on 2026-09-25


def _statement(client, bank, date, balance, opening=None):
    return run(client, 'reconcile start', dict(
        operation_key=new_id(), account=bank, statement_date=date, ending_balance=balance,
        **({'opening_draft_id': opening} if opening else {})))['draft']


def _mark(client, draft, version, **extra):
    return dict(operation_key=new_id(), draft=draft, expected_version=version, **extra)


def test_mark_all_then_finish_gives_difference_zero_and_a_certificate(client, bank):
    bank, equity = bank
    opening = run(client, 'reconcile opening start', dict(
        operation_key=new_id(), account=bank, opening_date='2026-09-01', entered_balance='0.00',
        evidence=EVIDENCE, references=[]))['draft']
    statement = _statement(client, bank, '2026-09-25', '275.28', opening['id'])

    # The preview changes nothing: the draft is still at version 1 with nothing ticked.
    preview = run(client, 'reconcile mark', _mark(client, statement['id'], 1, all=True), dry_run=True)['draft']
    assert len(preview['selections']) == 3
    _, shown = prepared(client, statement['id'], 1)
    assert shown['version'] == 1 and shown['totals']['difference'] != 0

    # Marked in one step: the three movements dated on or before the statement date, not the
    # October one.
    ticked = run(client, 'reconcile mark', _mark(client, statement['id'], 1, all=True))['draft']
    assert len(ticked['selections']) == 3 and ticked['version'] == 2
    guards, page = prepared(client, statement['id'], ticked['version'])
    assert page['balanced'] and page['totals']['difference'] == 0

    done = run(client, 'reconcile finish', dict(
        operation_key=new_id(), draft=statement['id'], expected_version=ticked['version'], **guards))
    assert done['certificate_id'] and done['totals']['difference'] == 0
    assert done['draft']['state'] == 'consumed'


def test_mark_all_can_be_cleared_and_is_versioned(client, bank):
    bank, equity = bank
    opening = run(client, 'reconcile opening start', dict(
        operation_key=new_id(), account=bank, opening_date='2026-09-01', entered_balance='0.00',
        evidence=EVIDENCE, references=[]))['draft']
    statement = _statement(client, bank, '2026-09-25', '275.28', opening['id'])
    ticked = run(client, 'reconcile mark', _mark(client, statement['id'], 1, all=True))['draft']

    with pytest.raises(BookflowError) as stale:      # a stale version is refused like any draft change
        run(client, 'reconcile mark', _mark(client, statement['id'], 1, all=True))
    assert stale.value.code == 'E_VERSION_CONFLICT'

    cleared = run(client, 'reconcile mark', _mark(
        client, statement['id'], ticked['version'], all=True, all_action='unmark'))['draft']
    assert cleared['selections'] == [] and cleared['version'] == ticked['version'] + 1


def test_a_later_statement_marks_all_without_retaking_what_was_cleared(client, bank):
    bank, equity = bank
    opening = run(client, 'reconcile opening start', dict(
        operation_key=new_id(), account=bank, opening_date='2026-09-01', entered_balance='0.00',
        evidence=EVIDENCE, references=[]))['draft']
    first = _statement(client, bank, '2026-09-25', '275.28', opening['id'])
    ticked = run(client, 'reconcile mark', _mark(client, first['id'], 1, all=True))['draft']
    guards, _ = prepared(client, first['id'], ticked['version'])
    run(client, 'reconcile finish', dict(
        operation_key=new_id(), draft=first['id'], expected_version=ticked['version'], **guards))

    second = _statement(client, bank, '2026-10-31', '1274.28')
    again = run(client, 'reconcile mark', _mark(client, second['id'], 1, all=True))['draft']
    assert len(again['selections']) == 1       # only the October journal; September is already cleared
    guards, page = prepared(client, second['id'], again['version'])
    assert page['balanced']


def test_all_and_entries_are_exclusive(client, bank):
    bank, equity = bank
    opening = run(client, 'reconcile opening start', dict(
        operation_key=new_id(), account=bank, opening_date='2026-09-01', entered_balance='0.00',
        evidence=EVIDENCE, references=[]))['draft']
    statement = _statement(client, bank, '2026-09-25', '275.28', opening['id'])
    with pytest.raises(BookflowError):
        run(client, 'reconcile mark', _mark(client, statement['id'], 1))     # neither
    with pytest.raises(BookflowError):
        run(client, 'reconcile mark', _mark(client, statement['id'], 1, all_action='unmark'))
