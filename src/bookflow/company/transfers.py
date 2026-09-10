"""Translate a funds transfer into the register entry that already posts it.

Not ``core/transfers.py``, which moves file bytes over the wire; this one moves money
between two of the company's own accounts.

Nothing here decides accounting. ``_register()`` builds the same ``RegisterPostInput`` a
person could have typed into the account register -- the account it comes out of as the
selected account, the account it goes into as the category -- and the posting itself is
``registers.translate`` followed by the journal writer, which is the one path money takes.

What this module owns is the document's own words and its two refusals: an account that
cannot be one end of a transfer, and the same account named at both ends.
"""
from __future__ import annotations

from bookflow.company import accounts, journals, registers
from bookflow.company.journal_models import parse_domestic_amount
from bookflow.company.register_models import RegisterPostInput
from bookflow.company.transfer_models import (
    CREDITED_BY, DEBITED_BY, TransferLeg, TransferSummary, TransferWriteOutput,
)
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Plan

# Both ends of a transfer are accounts the company's own money sits in, is owed on, or is
# held as: every balance-sheet posting type except the two party ledgers.
#
# Why these and not the rest. A transfer moves what the company already has from one place
# to another, so it must leave profit exactly where it found it -- and the only structural
# way to guarantee that is to keep every profit-and-loss account off both ends, rather than
# to post the entry and hope nobody chose one. Accounts receivable and accounts payable are
# balance-sheet accounts and are still refused: every posting line on them names the
# customer or the vendor it belongs to, and a transfer names nobody, so a transfer leg there
# is not a stricter version of a customer payment -- it is an unattributable hole in a party
# ledger. Non-posting accounts take no entry at all. Everything else on the balance sheet is
# eligible at both ends, by the same rule on each, because a transfer is symmetric: what is
# a bank paying a card down read one way is a card advancing cash read the other.
ELIGIBLE = frozenset((
    'bank', 'credit_card', 'other_current_asset', 'fixed_asset', 'other_asset',
    'other_current_liability', 'long_term_liability', 'equity',
))

_PROFIT = ('A transfer moves money you already have from one place to another and never '
           'changes profit, so no account that appears on a profit and loss can stand at '
           'either end of one.')
_CUSTOMERS = ('Every posting to it names the customer it belongs to, and a transfer names '
              'nobody.')
_VENDORS = ('Every posting to it names the vendor it belongs to, and a transfer names '
            'nobody.')
_NOTHING = 'Nothing posts to it at all.'

_SALE = 'Money arriving as income is a sale: record it with `sales-receipt post` or `invoice post`.'
_SPEND = 'Money spent is a purchase: record it with `check post` or `card-charge post`.'
_RECEIVE = 'Money coming in from a customer is `payment receive`.'
_SETTLE = ('Paying a vendor is `check post` or `card-charge post` with the payable account '
           'and the vendor named on the line.')

# Every account type that cannot be an end of a transfer, what to call it, why it cannot,
# and the document that does own that movement. Together with ELIGIBLE this covers every
# account type there is; ``tests/test_transfer_funds.py`` holds that to be true.
REFUSED: dict[str, tuple[str, str, str]] = {
    'income': ('an income account', _PROFIT, _SALE),
    'other_income': ('an other income account', _PROFIT, _SALE),
    'expense': ('an expense account', _PROFIT, _SPEND),
    'other_expense': ('an other expense account', _PROFIT, _SPEND),
    'cost_of_goods_sold': ('a cost of goods sold account', _PROFIT, _SPEND),
    'accounts_receivable': ('an accounts receivable account', _CUSTOMERS, _RECEIVE),
    'accounts_payable': ('an accounts payable account', _VENDORS, _SETTLE),
    'non_posting': ('a non-posting account', _NOTHING, ''),
}

# What each end is called, in the words the form uses, so a refusal reads as the sentence a
# person would say about the control they were filling in.
ENDS = {'from_account': 'The account a transfer comes out of',
        'to_account': 'The account a transfer goes into'}


def _ineligible(field, account):
    """Refuse an account that cannot be an end of a transfer, by name and with the reason."""
    kind = account['type']
    # The fallback is unreachable while ELIGIBLE and REFUSED cover every account type, which
    # a test holds; it exists so a type added without a disposition still refuses readably
    # rather than claiming a reason that may not be the true one.
    phrase, why, instead = REFUSED.get(
        kind, (f'a {kind.replace("_", " ")} account',
               'That kind of account is not one a transfer can move money between.', ''))
    name = account.get('full_name') or account['name']
    problem = f'"{name}" is {phrase}'
    return BookflowError('E_VALIDATION', message=' '.join(filter(None, (
        f'{ENDS[field]} has to be one of the company\'s own balance-sheet accounts. '
        f'{problem}. {why}', instead))), details={
        'fields': [{'field': field, 'problem': problem}],
        'account_id': account['id'], 'account_name': name, 'account_type': kind,
        'eligible_types': sorted(ELIGIBLE),
    })


def _same(account):
    """Refuse a transfer to the account it came from, by name."""
    name = account.get('full_name') or account['name']
    problem = f'"{name}" is named at both ends'
    return BookflowError('E_VALIDATION', message=(
        f'A transfer moves money from one account into another. {problem} of this one, so '
        f'nothing would move. Choose a different account at one end.'), details={
        'fields': [{'field': 'to_account', 'problem': problem}],
        'account_id': account['id'], 'account_name': name,
    })


def _end(s, selector, field, currency):
    """One end of the transfer: active, eligible, and kept in the home currency."""
    account = journals.active(accounts.resolve_account(s.company, selector), 'account')
    if account['type'] not in ELIGIBLE:
        raise _ineligible(field, account)
    held = account.get('currency')
    if held not in (None, currency):
        raise journals.invalid(field, f'this account is kept in {held}; a transfer moves '
                                      f'money between accounts kept in {currency}')
    return account


def _leg(account, side):
    normal = accounts.NORMAL_BALANCE[account['type']]
    return TransferLeg(
        account_id=account['id'], name=account.get('full_name') or account['name'],
        type=account['type'], normal_balance=normal, side=side,
        effect=(CREDITED_BY if side == 'credit' else DEBITED_BY)[normal])


def _register(inp, s):
    """The register entry this transfer is, plus what its own footer shows.

    One rule sets both signs: credit the account it comes out of, debit the account it goes
    into. The register's ``direction`` is that credit expressed in the selected account's own
    words, and the offset it derives is the matching debit.
    """
    currency = registers._home(s)
    source = _end(s, inp.from_account, 'from_account', currency)
    target = _end(s, inp.to_account, 'to_account', currency)
    if source['id'] == target['id']:
        raise _same(source)
    amount = parse_domestic_amount(inp.amount, currency, 'amount')
    register = RegisterPostInput(
        account=source['id'], date=inp.date, memo=inp.memo, amount=inp.amount,
        direction=CREDITED_BY[accounts.NORMAL_BALANCE[source['type']]],
        category=target['id'])
    summary = TransferSummary(
        kind='transfer', currency=currency, amount=amount.to_dict(),
        from_account=_leg(source, 'credit'), to_account=_leg(target, 'debit'))
    return register, summary


def _output(journal_output, summary):
    return TransferWriteOutput(**journal_output.model_dump(), document=summary)


def prepare(s, ctx, inp):
    register, summary = _register(inp, s)
    journal, _ = registers.translate(register, s, 'post')
    fresh = journals.prepare(s, ctx, journal, 'post')
    return Plan(_output(fresh.preview, summary), {'input': inp})


def apply(plan, ctx, s):
    # Rebuilt inside the writer transaction, the same way the register does it: references,
    # dates and numbering are only decisive here.
    inp = plan.data['input']
    register, summary = _register(inp, s)
    journal, _ = registers.translate(register, s, 'post')
    fresh = journals.prepare(s, ctx, journal, 'post')
    applied = journals.persist_prepared(fresh, ctx, s, command_name='transfer post')
    applied.output = _output(applied.output, summary)
    return applied
