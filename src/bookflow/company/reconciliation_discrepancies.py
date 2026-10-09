"""Finishing a statement that will not tie: a labelled adjustment to Reconciliation Discrepancies.

QuickBooks Desktop's Reconcile Now offers "Enter Adjustment" when the difference is not zero: it
posts a journal for the difference to a "Reconciliation Discrepancies" expense account, labelled
as a reconciliation adjustment, and the reconciliation completes. This is that, and only that:

- the amount is the exact remaining difference, never one a caller types;
- the date is the statement date;
- the other side is always the Reconciliation Discrepancies account, made on first use the way a
  system account is (a chart row carrying its own system role), so nobody picks another account;
- only a person may ask for it. An agent that cannot make a statement tie leaves the draft open
  and tells the owner: an adjustment hides a real error until someone looks for it, and that
  judgement is the person's to make.

`reconcile finish` calls in here; nothing in this module registers a command.
"""
import sqlalchemy as sa

from bookflow.company import accounts
from bookflow.company import reconciliation_commands_models as m
from bookflow.company import schema as c
from bookflow.company.reconciliation_preparation import bounded, require
from bookflow.core.errors import BookflowError

ACCOUNT_NAME = 'Reconciliation Discrepancies'
ROLE = 'reconciliation_discrepancies'
ACCOUNT_TYPE = 'expense'


def require_person(s, ctx, difference=None, currency=None):
    """An adjustment is a person's decision. Any agent-acted request is refused, plainly."""
    from bookflow.core.context import Interface
    actor = s.actor
    agent = (actor is None or getattr(actor, 'kind', None) != 'human' or bool(ctx.on_behalf_of)
             or ctx.interface == Interface.mcp)
    if not agent:
        return
    remaining = '' if difference is None else f' of {_decimal(difference, currency)} {currency}'
    message = (f'Only a person can finish a reconciliation with an adjustment. Leave this draft open and '
               f'tell the company\'s owner the statement does not tie (difference{remaining}); they can '
               f'look for the missing or wrong movement, or finish with an adjustment themselves.')
    raise BookflowError('E_PERMISSION', message=message, details={
        'capability': 'ledger.post', 'required_role': 'person',
        'reason': 'reconciliation adjustments are made only by a person',
        'next': 'leave the draft open and tell the owner'})


def _decimal(units, currency):
    from bookflow.core.money import Money
    return Money(units, currency).amount


def find_account(db):
    """The Reconciliation Discrepancies account, or None until the first adjustment makes it."""
    row = db.conn.execute(sa.select(c.accounts).where(c.accounts.c.system_role == ROLE)).mappings().first()
    return dict(row) if row is not None else None


def account(s, ctx, *, at=None):
    """The account an adjustment posts to, and the chart row to insert first when it is new.

    Returns ``(row, created)``: ``created`` is the account mutation to persist before the journal
    names the account, or None when it already exists.
    """
    db = s.company
    row = find_account(db)
    if row is not None:
        require(row['active'], 'E_RECONCILIATION_UNSUPPORTED')
        return row, None
    mutation = accounts.plan_account_create(db, {'name': ACCOUNT_NAME, 'type': ACCOUNT_TYPE},
                                            actor_id=s.actor.id, via=ctx.interface.value, at=at)
    after = dict(mutation.after, system_role=ROLE)
    return after, accounts.AccountMutation(mutation.action, mutation.before, after)


def journal(draft, account_type, offset_id, amount, currency, reason):
    """The adjusting journal: the reconciled account against Reconciliation Discrepancies.

    ``amount`` is the difference in the statement's own convention (ending minus cleared), so the
    movement it puts on the statement is exactly that amount. On a card the statement counts what
    is owed, which runs opposite to the debit side.
    """
    from bookflow.company.journal_models import JournalPostInput
    signed = -amount if account_type == 'credit_card' else amount
    money = dict(minor_units=abs(signed), currency=currency)
    memo = (f'Reconciliation adjustment for the statement dated {draft.header.statement_date}: '
            f'{reason}')[:2000]
    return JournalPostInput(date=draft.header.statement_date, memo=memo, lines=[
        dict(account=draft.account_id, side='debit' if signed > 0 else 'credit', amount=money,
             description='Reconciliation adjustment'),
        dict(account=offset_id, side='credit' if signed > 0 else 'debit', amount=money,
             description='Reconciliation adjustment')])


def projected(totals, amount):
    """The totals once the adjustment is cleared with the statement: the difference becomes zero."""
    positive = totals.positive_sum + (amount if amount > 0 else 0)
    negative = totals.negative_sum + (amount if amount < 0 else 0)
    selected = positive + negative
    cleared = totals.beginning_balance + selected
    money = dict(positive_sum=positive, negative_sum=negative, selected_sum=selected,
                 beginning_balance=totals.beginning_balance, ending_balance=totals.ending_balance,
                 cleared_balance=cleared, difference=totals.ending_balance - cleared)
    return m.Totals(positive_count=totals.positive_count + (amount > 0),
                    negative_count=totals.negative_count + (amount < 0),
                    **{k: bounded(v) for k, v in money.items()},
                    decimal_units={k: str(v) for k, v in money.items()})


def output(row, created, *, draft, amount, currency, reason, journal_id=None, number=None):
    return m.AdjustmentOutput(
        journal_id=journal_id, number=number, date=draft.header.statement_date, amount=amount,
        amount_decimal=_decimal(amount, currency), account_id=None if created is not None and journal_id is None else row['id'],
        account_name=row['full_name'], account_created=created is not None, reason=reason)


def post(s, ctx, draft, amount, reason):
    """Post the adjusting journal inside the finish's own transaction, then store its effects.

    Returns ``(journal_header, row, created, touches)``. The journal is written through the
    journal writer exactly as `journal post` writes one, under its own audit event, so it reads
    in every register, report and audit trail as the labelled adjustment it is.
    """
    from bookflow.company import journals
    from bookflow.company import early_discounts
    from bookflow.company import reconciliation_materialization as materialization
    source = s.company.conn.execute(sa.select(c.accounts).where(c.accounts.c.id == draft.account_id)).mappings().one()
    row, created = account(s, ctx)
    if created is not None:
        accounts.persist_account_mutation(s.company, created)
    entry = journal(draft, source['type'], row['id'], amount, source['currency'], reason)
    fresh = journals.prepare(s, ctx, entry, 'post')
    applied = journals.persist_prepared(fresh, ctx, s, command_name='reconcile finish',
                                        noun='reconciliation adjustment')
    header = fresh.data['header']
    # Bring the statement effects level with the ledger now, so the finish can tick the movement
    # this journal put on the account and certify the statement it belongs to.
    materialization.drain(s.company)
    return header, row, created, early_discounts.created_account_touches(created) + list(applied.touched)
