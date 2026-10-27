"""R183 trial fixes in reconciliation: a draft whose ticked entry was edited, discarding a draft,
an import's statement date, and a statement that follows a covered opening."""
import pytest

from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from tests.test_reconciliation_adapters import account, journal, pair, run
from tests.test_reconciliation_commands import prepared

EVIDENCE = dict(format=1, statement_reference=None, entered_text='No earlier statement')


def _opening(client, bank, date='2026-09-01', balance='0.00'):
    return run(client, 'reconcile opening start', dict(
        operation_key=new_id(), account=bank, opening_date=date, entered_balance=balance,
        evidence=EVIDENCE, references=[]))['draft']


def _statement(client, bank, date, balance, opening=None):
    return run(client, 'reconcile start', dict(
        operation_key=new_id(), account=bank, statement_date=date, ending_balance=balance,
        **({'opening_draft_id': opening} if opening else {})))['draft']


def _mark(draft, version, **extra):
    return dict(operation_key=new_id(), draft=draft, expected_version=version, **extra)


@pytest.fixture
def ticked(client):
    """A statement draft with every movement ticked, then one ticked journal edited."""
    bank = account(client, 'Trial bank')
    equity = account(client, 'Trial equity', 'equity')
    first = journal(client, pair(bank, equity, '400.00'), date='2026-09-02')
    journal(client, pair(equity, bank, '100.00'), date='2026-09-10')
    opening = _opening(client, bank)
    statement = _statement(client, bank, '2026-09-25', '300.00', opening['id'])
    draft = run(client, 'reconcile mark', _mark(statement['id'], 1, all=True))['draft']
    lines = [dict(account=line['account_id'] if 'account_id' in line else line['account'], side=line['side'],
                  amount='450.00', line_id=line['line_id']) for line in first['revision']['lines']]
    run(client, 'journal update', dict(journal=first['id'], expected_version=first['version'], lines=lines))
    return bank, equity, statement['id'], draft['version']


def test_an_edited_ticked_entry_leaves_the_draft_usable(client, ticked):
    bank, equity, draft, version = ticked
    # The preview names what changed and how to recover, rather than only refusing.
    with pytest.raises(BookflowError) as stale:
        prepared(client, draft, version)
    assert stale.value.code == 'E_RECONCILIATION_SELECTION_STALE'
    [changed] = stale.value.details['stale']
    assert changed['ticked_amount_minor_units'] == 40000 and changed['now']['amount_minor_units'] == 45000
    assert any('--all-action unmark' in step for step in stale.value.details['next_steps'])
    # Unmark all takes back every tick, the edited one included; ticking again ties to the new amount.
    cleared = run(client, 'reconcile mark', _mark(draft, version, all=True, all_action='unmark'))['draft']
    assert cleared['selections'] == []
    again = run(client, 'reconcile mark', _mark(draft, cleared['version'], all=True))['draft']
    _, page = prepared(client, draft, again['version'])
    assert page['totals']['cleared_balance'] == 35000


def test_discard_drops_an_open_draft_from_review_and_keeps_its_history(client, ticked):
    bank, equity, draft, version = ticked
    with pytest.raises(BookflowError):
        run(client, 'reconcile discard', dict(operation_key=new_id(), draft=draft, expected_version=version + 5))
    done = run(client, 'reconcile discard', dict(operation_key=new_id(), draft=draft, expected_version=version))['draft']
    assert done['state'] == 'canceled'
    events = run(client, 'audit list', dict(command='reconcile discard', limit=5))['items']
    assert events and events[0]['reason']
    with pytest.raises(BookflowError) as again:
        run(client, 'reconcile mark', _mark(draft, done['version'], all=True))
    assert again.value.code == 'E_RECONCILIATION_DRAFT_STATE'
    review = run(client, 'report entries-to-review', {})
    assert draft not in str(review)
