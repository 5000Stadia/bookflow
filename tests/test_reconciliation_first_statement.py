"""An account's first reconciliation, with no earlier statement, through the public commands (R80).

The blind trial adopted an opening at the statement's own date, ticked everything as covered and
then met `reconcile finish` refusing an opening draft with no reason given. An opening is certified
together with its first statement, so the fix is the path, stated where the person is: the opening
help, the preview of an opening, and every refusal on the way name the step that fits. The flow a
first reconciliation takes -- start from zero, tick everything, finish -- is then run end to end.
"""
import pytest

from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from tests.test_reconciliation_adapters import account, journal, pair, run
from tests.test_reconciliation_commands import marks, prepared

EVIDENCE = dict(format=1, statement_reference=None, entered_text='No earlier statement')


@pytest.fixture
def bank(client):
    bank = account(client, 'First statement bank')
    equity = account(client, 'First statement equity', 'equity')
    journal(client, pair(bank, equity, '400.00'), date='2026-09-02')
    journal(client, pair(equity, bank, '174.82'), date='2026-09-10')
    journal(client, pair(bank, equity, '50.10'), date='2026-09-24')
    return bank     # 400.00 - 174.82 + 50.10 = 275.28 on 2026-09-25


def _opening(client, bank, date, balance):
    return run(client, 'reconcile opening start', dict(
        operation_key=new_id(), account=bank, opening_date=date, entered_balance=balance,
        evidence=EVIDENCE, references=[]))['draft']


def _refusal(call):
    with pytest.raises(BookflowError) as error:
        call()
    return error.value


def test_an_opening_is_finished_through_its_first_statement_and_says_so(client, bank):
    # What the trial did: the statement itself as the opening, everything covered.
    opening = _opening(client, bank, '2026-09-25', '275.28')
    covered = run(client, 'reconcile mark', dict(operation_key=new_id(), draft=opening['id'],
        expected_version=1, entries=marks(client, opening['id'], action='covered')))['draft']
    guards, preview = prepared(client, opening['id'], covered['version'])
    assert preview['balanced'] and 'reconcile start' in preview['next_step']

    refused = _refusal(lambda: run(client, 'reconcile finish', dict(
        operation_key=new_id(), draft=opening['id'], expected_version=covered['version'], **guards)))
    assert refused.code == 'E_RECONCILIATION_DRAFT_STATE'
    assert refused.details['draft_kind'] == 'opening'
    assert refused.details['accepts'] == ['statement', 'amendment']
    assert refused.details['next_command'] == 'reconcile start'
    assert refused.details['next_input']['opening_draft_id'] == opening['id']
    assert 'entered_balance 0.00' in refused.details['next']

    # A statement on the opening's own date is refused with both dates and the way through.
    same_day = run(client, 'reconcile start', dict(
        operation_key=new_id(), account=bank, statement_date='2026-09-25', ending_balance='275.28',
        opening_draft_id=opening['id']))['draft']
    dated = _refusal(lambda: run(client, 'reconcile preview',
                                 dict(draft=same_day['id'], expected_version=1)))
    assert dated.code == 'E_RECONCILIATION_DATE'
    assert dated.details['statement_date'] == dated.details['previous_date'] == '2026-09-25'
    assert 'opening date' in dated.details['next'] and 'reconcile finish' in dated.details['next']


def test_a_first_reconciliation_starts_from_zero_and_ticks_everything(client, bank):
    from bookflow.core.registry import REGISTRY
    assert 'entered_balance 0.00' in REGISTRY['reconcile opening start'].description
    assert 'never an opening draft' in REGISTRY['reconcile finish'].description

    opening = _opening(client, bank, '2026-09-01', '0.00')
    statement = run(client, 'reconcile start', dict(
        operation_key=new_id(), account=bank, statement_date='2026-09-25', ending_balance='275.28',
        opening_draft_id=opening['id']))['draft']
    ticked = run(client, 'reconcile mark', dict(operation_key=new_id(), draft=statement['id'],
        expected_version=1, entries=marks(client, statement['id'])))['draft']
    assert len(ticked['selections']) == 3
    guards, preview = prepared(client, statement['id'], ticked['version'])
    assert preview['balanced'] and preview['next_step'] is None
    done = run(client, 'reconcile finish', dict(
        operation_key=new_id(), draft=statement['id'], expected_version=ticked['version'], **guards))
    assert done['account_id'] == bank and done['certificate_id'] and done['opening_id']
    assert done['totals']['difference'] == 0
