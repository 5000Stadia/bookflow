"""A statement that genuinely will not tie, finished with a labelled adjustment (QuickBooks' "Enter
Adjustment" on Reconcile Now).

The bank, worked by hand. Adopted at zero on 2026-09-01; the September statement says 278.45:

    2026-09-02  deposit from equity        +400.00
    2026-09-10  paid out to equity         -174.82
    2026-09-24  deposit from equity         +50.10
                                           --------
                cleared with all ticked     275.28
                statement ending balance    278.45
                difference                    3.17   (the bank shows 3.17 more than the books)

A person finishes with an adjustment: one journal dated 2026-09-25 debits the bank 3.17 and credits
Reconciliation Discrepancies 3.17, the statement certifies with that movement cleared, and the
books still balance. An agent asking the same is refused. A dry run writes nothing. A statement
that already ties posts nothing even when the adjustment is asked for.
"""
import sqlite3

import pytest

from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from tests.conftest import make_actor, make_agent
from tests.test_reconciliation_adapters import COMPANY, account, journal, pair, run
from tests.test_reconciliation_commands import prepared
from tests.test_row8_journal import database_path

EVIDENCE = dict(format=1, statement_reference=None, entered_text='No earlier statement')
REASON = 'Bank figure differs by 3.17; nothing found after two passes'


@pytest.fixture
def statement(client):
    """A September statement with everything ticked, 3.17 short of tying, ready to finish."""
    bank = account(client, 'Discrepancy bank')
    equity = account(client, 'Discrepancy equity', 'equity')
    journal(client, pair(bank, equity, '400.00'), date='2026-09-02')
    journal(client, pair(equity, bank, '174.82'), date='2026-09-10')
    journal(client, pair(bank, equity, '50.10'), date='2026-09-24')
    return _ticked(client, bank, '278.45')


def _ticked(client, bank, balance, date='2026-09-25'):
    opening = run(client, 'reconcile opening start', dict(
        operation_key=new_id(), account=bank, opening_date='2026-09-01', entered_balance='0.00',
        evidence=EVIDENCE, references=[]))['draft']
    draft = run(client, 'reconcile start', dict(
        operation_key=new_id(), account=bank, statement_date=date, ending_balance=balance,
        opening_draft_id=opening['id']))['draft']
    ticked = run(client, 'reconcile mark', dict(operation_key=new_id(), draft=draft['id'],
                                                expected_version=1, all=True))['draft']
    guards, page = prepared(client, draft['id'], ticked['version'])
    return dict(bank=bank, draft=draft['id'], version=ticked['version'], guards=guards, page=page)


def _finish(statement, **extra):
    return dict(operation_key=new_id(), draft=statement['draft'], expected_version=statement['version'],
                **statement['guards'], **extra)


def _rows(client, sql, *args):
    with sqlite3.connect(f'file:{database_path(client)}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql, args)]


def test_a_person_finishes_with_an_adjustment_for_the_exact_difference(client, statement):
    assert statement['page']['totals']['difference'] == 317 and not statement['page']['balanced']
    with pytest.raises(BookflowError) as plain:          # without the option it still refuses
        run(client, 'reconcile finish', _finish(statement))
    assert plain.value.code == 'E_RECONCILIATION_DIFFERENCE'

    done = run(client, 'reconcile finish', _finish(statement, adjustment=dict(reason=REASON)))
    adjustment = done['adjustment']
    assert (adjustment['amount'], adjustment['amount_decimal'], adjustment['date']) == (317, '3.17', '2026-09-25')
    assert adjustment['account_name'] == 'Reconciliation Discrepancies' and adjustment['account_created']
    assert done['original_difference'] == 317
    totals = done['totals']
    assert (totals['difference'], totals['cleared_balance'], totals['positive_count']) == (0, 27845, 3)
    assert done['draft']['state'] == 'consumed'

    # The journal: dated the statement date, labelled, bank against Reconciliation Discrepancies.
    entry = run(client, 'journal show', dict(journal=adjustment['journal_id']))
    revision = entry['revision']
    assert revision['date'] == '2026-09-25' and revision['memo'].startswith('Reconciliation adjustment')
    assert REASON in revision['memo']
    sides = {(line['account_id'], line['side'], line['amount']['minor_units']) for line in revision['lines']}
    assert sides == {(statement['bank'], 'debit', 317), (adjustment['account_id'], 'credit', 317)}
    discrepancies = run(client, 'account show', dict(account=adjustment['account_id']))
    assert discrepancies['type'] == 'expense' and discrepancies['is_system']

    # The certificate records it: the difference it adjusted away, and the movement as cleared.
    certificate = _rows(client, 'SELECT * FROM reconciliation_certificates WHERE id=?', done['certificate_id'])[0]
    assert (certificate['original_difference'], certificate['final_difference'],
            certificate['ending_balance'], certificate['selected_sum']) == (317, 0, 27845, 27845)
    cleared = _rows(client, """SELECT v.transaction_id FROM reconciliation_certificate_members m
                               JOIN reconciliation_effect_versions v ON v.id=m.version_id
                               WHERE m.certificate_id=? AND m.classification='selected'""", done['certificate_id'])
    assert adjustment['journal_id'] in {row['transaction_id'] for row in cleared}

    # The books still balance, and the bank's ledger balance is now the statement's.
    rows, cursor = [], None
    while True:
        report = run(client, 'report trial-balance', dict(date_to='2026-09-30', limit=200,
                                                          **({'cursor': cursor} if cursor else {})))
        rows.extend(report['rows'])
        cursor = report['next_cursor']
        if not cursor:
            break
    debit = sum(row['debit']['minor_units'] for row in rows)
    credit = sum(row['credit']['minor_units'] for row in rows)
    assert debit == credit
    bank = next(row for row in rows if row['account_id'] == statement['bank'])
    assert bank['debit']['minor_units'] - bank['credit']['minor_units'] == 27845

    # A second adjustment reuses the account rather than making another.
    again = _ticked(client, account(client, 'Second discrepancy bank'), '-1.05')
    second = run(client, 'reconcile finish', _finish(again, adjustment=dict(reason='Bank fee never itemised')))
    assert second['adjustment']['account_id'] == adjustment['account_id']
    assert not second['adjustment']['account_created'] and second['adjustment']['amount'] == -105


def test_an_agent_is_refused_and_the_draft_stays_open(client, root, statement):
    """An authorized agent acting for an owner, made the way an administrator makes one."""
    from bookflow.core.config import Config
    from bookflow.core.context import Context, Interface
    from bookflow.core.dispatch import run as dispatch_run
    from bookflow.core import registry
    company = client.company.list()['items'][0]['company_id']
    owner = make_actor(root, 'discrepancy-owner', company_role=(company, 'owner'))
    agent = make_agent(lambda name, body: client.run(name, body), 'discrepancy-agent',
                       principals=owner, company=company, role='owner')
    config = Config.load(root / 'config.toml')
    config.set_user('discrepancy-agent', agent)
    config.save()
    for dry_run in (True, False):
        ctx = Context.new(Interface.python, 'discrepancy agent witness', on_behalf_of=owner,
                          reason='agent tries to force it')
        with pytest.raises(BookflowError) as refused:
            dispatch_run(registry.get('reconcile finish'), _finish(statement, adjustment=dict(reason=REASON)),
                         ctx, data_root=str(root), company_selector=company, company_source='option',
                         dry_run=dry_run, _login='discrepancy-agent')
        assert refused.value.code == 'E_PERMISSION', refused.value
        assert 'Leave this draft open and tell' in refused.value.message
    _, page = prepared(client, statement['draft'], statement['version'])
    assert page['version'] == statement['version'] and page['totals']['difference'] == 317
    assert not _rows(client, "SELECT id FROM accounts WHERE system_role='reconciliation_discrepancies'")


def test_a_dry_run_names_the_adjustment_and_changes_nothing(client, statement):
    journals = _rows(client, 'SELECT count(*) AS n FROM transactions')[0]['n']
    preview = run(client, 'reconcile finish', _finish(statement, adjustment=dict(reason=REASON)), dry_run=True)
    adjustment = preview['adjustment']
    assert (adjustment['amount'], adjustment['date'], adjustment['journal_id']) == (317, '2026-09-25', None)
    assert adjustment['account_name'] == 'Reconciliation Discrepancies' and adjustment['account_created']
    assert adjustment['account_id'] is None
    assert preview['totals']['difference'] == 0 and preview['totals']['cleared_balance'] == 27845
    assert _rows(client, 'SELECT count(*) AS n FROM transactions')[0]['n'] == journals
    assert not _rows(client, "SELECT id FROM accounts WHERE system_role='reconciliation_discrepancies'")
    assert not _rows(client, 'SELECT id FROM reconciliation_certificates')
    # And the real finish that follows gives exactly what the dry run said.
    done = run(client, 'reconcile finish', _finish(statement, adjustment=dict(reason=REASON)))
    assert done['totals'] == preview['totals'] and done['adjustment']['amount'] == 317


def test_a_zero_difference_needs_no_adjustment(client):
    bank = account(client, 'Tied bank')
    equity = account(client, 'Tied equity', 'equity')
    journal(client, pair(bank, equity, '400.00'), date='2026-09-02')
    tied = _ticked(client, bank, '400.00')
    assert tied['page']['balanced']
    before = _rows(client, 'SELECT count(*) AS n FROM transactions')[0]['n']
    done = run(client, 'reconcile finish', _finish(tied, adjustment=dict(reason='Asked for out of caution')))
    assert done['adjustment'] is None and done['original_difference'] == 0
    assert done['certificate_id'] and done['totals']['difference'] == 0
    assert _rows(client, 'SELECT count(*) AS n FROM transactions')[0]['n'] == before
    with pytest.raises(BookflowError):                   # a reason is required when asked for
        run(client, 'reconcile finish', _finish(tied, adjustment=dict(reason='')))
