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

import sqlalchemy as sa

from bookflow.company import accounts, journals, money_out, registers, schema as c
from bookflow.company.journal_models import JournalVoidInput, MoneyInput, parse_domestic_amount
from bookflow.company.register_models import RegisterPostInput, RegisterUpdateInput
from bookflow.company.transfer_models import (
    CREDITED_BY, DEBITED_BY, TransferHistoryOutput, TransferLeg, TransferOutput,
    TransferPageOutput, TransferRevisionSummaryOutput, TransferSummary, TransferSummaryOutput,
    TransferWriteOutput,
)
from bookflow.core.errors import BookflowError
from bookflow.core.money import Money
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


def _register(facts, s, header):
    """The register entry this transfer is, plus what its own footer shows.

    One rule sets both signs: credit the account it comes out of, debit the account it goes
    into. The register's ``direction`` is that credit expressed in the selected account's own
    words, and the offset it derives is the matching debit. A correction is the same entry
    with the two line identities retained, so changing ``to_account`` moves the money out of
    the account it went into and into the new one at the same date rather than on two.
    """
    currency = registers._home(s)
    source = _end(s, facts['from_account'], 'from_account', currency)
    target = _end(s, facts['to_account'], 'to_account', currency)
    if source['id'] == target['id']:
        raise _same(source)
    amount = parse_domestic_amount(facts['amount'], currency, 'amount')
    values = dict(
        account=source['id'], date=facts['date'], memo=facts['memo'], amount=facts['amount'],
        direction=CREDITED_BY[accounts.NORMAL_BALANCE[source['type']]], category=target['id'],
        **({'number': facts['number']} if facts['number'] is not None else {}))
    if header is None:
        register = RegisterPostInput(**values)
    else:
        # The register input requires a version and the document does not; the version the
        # person actually supplied is carried to ``registers.translate`` instead.
        register = RegisterUpdateInput(
            journal=header['id'], selected_line_id=facts['selected_line_id'],
            category_line_id=facts['category_line_id'],
            expected_version=facts['expected_version'] if facts['expected_version'] is not None
            else header['version'], **values)
    summary = TransferSummary(
        kind='transfer', currency=currency, amount=amount.to_dict(),
        from_account=_leg(source, 'credit'), to_account=_leg(target, 'debit'))
    return register, summary


def _stored_leg(line, side):
    """One end of a stored transfer, read back out of the revision that holds it.

    The account facts come from the snapshot the revision captured, not from the account as it
    stands now, so a transfer reads back saying what it said when it was written. The normal
    balance falls back to the account type's own because it is a property of the type rather
    than of the account, and an older snapshot may predate the column.
    """
    snapshot = money_out.snapshot(line)
    kind = snapshot.get('type', '')
    normal = snapshot.get('normal_balance') or accounts.NORMAL_BALANCE[kind]
    return TransferLeg(
        account_id=line['account_id'], name=snapshot.get('full_name') or snapshot.get('name') or '',
        type=kind, normal_balance=normal, side=side,
        effect=(CREDITED_BY if side == 'credit' else DEBITED_BY)[normal])


def document(header, lines):
    """The transfer's own footer, derived from the revision that is stored.

    A transfer is two lines and only two: the account it came out of, credited, and the
    account it went into, debited. An entry that has grown a third line, lost a side or
    acquired an end a transfer may not touch is no longer a transfer, and is refused by name
    rather than described with figures nobody entered.
    """
    if len(lines) != 2:
        raise money_out.unreadable('transfer', header,
                                   f'a transfer has exactly two lines and this has {len(lines)}')
    source, target = lines
    if (source['side'], target['side']) != ('credit', 'debit'):
        raise money_out.unreadable('transfer', header,
                                   'it no longer credits one account and debits the other')
    for line, field in ((source, 'from_account'), (target, 'to_account')):
        kind = money_out.snapshot(line).get('type')
        if kind not in ELIGIBLE:
            named = REFUSED.get(kind, (f'a {str(kind).replace("_", " ")} account',))[0]
            raise money_out.unreadable('transfer', header,
                                       f'{ENDS[field].lower()} is now {named}')
    currency = source['currency']
    return TransferSummary(
        kind='transfer', currency=currency,
        amount=Money(source['amount_minor_units'], currency).to_dict(),
        from_account=_stored_leg(source, 'credit'), to_account=_stored_leg(target, 'debit'))


def _facts(s, inp, header):
    """The complete transfer after this call: what was captured, with what was supplied over it."""
    if header is None:
        return dict(from_account=inp.from_account, to_account=inp.to_account, date=inp.date,
                    amount=inp.amount, memo=inp.memo, number=None, expected_version=None,
                    selected_line_id=None, category_line_id=None)
    revision = journals.revision(s, header)
    lines = money_out.lines(s, revision)
    document(header, lines)
    source, target = lines
    given = lambda field: field in inp.model_fields_set
    return dict(
        from_account=inp.from_account if given('from_account') else source['account_id'],
        to_account=inp.to_account if given('to_account') else target['account_id'],
        date=inp.date if given('date') else revision['date'],
        amount=(inp.amount if given('amount') else
                MoneyInput(minor_units=source['amount_minor_units'], currency=source['currency'])),
        memo=inp.memo if given('memo') else revision['memo'],
        number=header['number'],
        expected_version=inp.expected_version,
        selected_line_id=source['line_id'], category_line_id=target['line_id'])


def _output(journal_output, summary):
    return TransferWriteOutput(**journal_output.model_dump(), document=summary)


# ---------------------------------------------------------------- reads


def show(s, inp):
    header = money_out.resolve(s, inp.transfer, 'transfer')
    requested = journals.revision(s, header, inp.revision_number)
    return TransferOutput(**journals.summary(header, requested),
                          revision=journals.revision_output(s, requested),
                          document=document(header, money_out.lines(s, requested)))


def page(s, ctx, inp):
    state = money_out.state_for(s, ctx, inp, 'transfer', 'query')
    t, r, m = c.transactions, c.transaction_revisions, c.money_out_documents
    source = c.document_lines.alias('from_line')
    target = c.document_lines.alias('to_line')
    beyond = c.document_lines.alias('extra_line')
    # The two ends, and the same shape ``document`` insists on when it reads one back: exactly
    # two lines, the first credited and the second debited. An entry rearranged in the journal
    # editor until that stopped being true is refused by ``transfer show``, so listing it here
    # would be listing a row nothing can open.
    query = (sa.select(t.c.id).select_from(
        t.join(r, r.c.id == t.c.current_revision_id)
        .join(m, m.c.transaction_id == t.c.id)
        .join(source, sa.and_(source.c.revision_id == r.c.id, source.c.position == 1))
        .join(target, sa.and_(target.c.revision_id == r.c.id, target.c.position == 2)))
        .where(m.c.kind == 'transfer', source.c.side == 'credit', target.c.side == 'debit',
               *(sa.func.json_extract(end.c.account_snapshot, '$.type').in_(sorted(ELIGIBLE))
                 for end in (source, target)),
               ~sa.exists(sa.select(sa.literal(1)).select_from(beyond).where(
                   beyond.c.revision_id == r.c.id, beyond.c.position > 2))))
    if inp.date_from:
        query = query.where(r.c.date >= inp.date_from)
    if inp.date_to:
        query = query.where(r.c.date <= inp.date_to)
    if inp.status:
        query = query.where(t.c.status == inp.status)
    if inp.number:
        query = query.where(t.c.number.contains(inp.number, autoescape=True))
    for selector, column in ((inp.from_account, source.c.account_id),
                             (inp.to_account, target.c.account_id)):
        if selector:
            query = query.where(column == accounts.resolve_account(s.company, selector)['id'])
    if inp.account:
        either = accounts.resolve_account(s.company, inp.account)['id']
        query = query.where(sa.or_(source.c.account_id == either, target.c.account_id == either))
    if inp.query:
        query = query.where(sa.or_(t.c.number.contains(inp.query, autoescape=True),
                                   r.c.memo.contains(inp.query, autoescape=True)))
    found, shared = money_out.take(s, money_out.ordered(query, inp.direction), state, inp.limit)
    pairs = money_out.headers(s, [row['id'] for row in found])
    grouped = money_out.lines_by_revision(s, [revision['id'] for _, revision in pairs])
    return TransferPageOutput(items=[
        TransferSummaryOutput(**journals.summary(header, revision),
                              document=document(header, grouped[revision['id']]))
        for header, revision in pairs], **shared)


def history(s, ctx, inp):
    header = money_out.resolve(s, inp.transfer, 'transfer')
    found, shared, grouped = money_out.history_page(s, ctx, inp, 'transfer', header)
    items = [TransferRevisionSummaryOutput(
        **journals.revision_output(s, revision, summary_only=True).model_dump(),
        document=document(header, grouped[revision['id']])) for revision in found]
    return TransferHistoryOutput(
        **{key: header[key] for key in ('id', 'version', 'current_revision_id', 'number', 'status')},
        items=items, **shared)


# ---------------------------------------------------------------- writes


def _translate(s, inp, operation):
    header = money_out.resolve(s, inp.transfer, 'transfer') if operation != 'post' else None
    if operation == 'void':
        lines = money_out.lines(s, journals.revision(s, header))
        return (JournalVoidInput(journal=header['id'], expected_version=inp.expected_version),
                document(header, lines))
    register, summary = _register(_facts(s, inp, header), s, header)
    journal, _ = registers.translate(register, s, operation, moving=operation == 'update',
                                     expected_version=inp.expected_version if header else None)
    return journal, summary


def prepare(s, ctx, inp, operation='post'):
    journal, summary = _translate(s, inp, operation)
    fresh = journals.prepare(s, ctx, journal, operation)
    return Plan(_output(fresh.preview, summary), {'input': inp, 'operation': operation})


def apply(plan, ctx, s):
    # Rebuilt inside the writer transaction, the same way the register does it: references,
    # dates, versions and numbering are only decisive here.
    inp, operation = plan.data['input'], plan.data['operation']
    journal, summary = _translate(s, inp, operation)
    fresh = journals.prepare(s, ctx, journal, operation)
    extra = ()
    if operation == 'post' and fresh.data['changed']:
        header = fresh.data['header']
        extra = (('money_out_documents', 'money_out_document', 'transaction_id', [money_out.marker(
            'transfer', header['id'], at=header['created_at'], actor_id=s.actor.id,
            interface=ctx.interface.value, event=fresh.data['event'])]),)
    applied = journals.persist_prepared(fresh, ctx, s, command_name='transfer ' + operation,
                                        extra=extra, noun='transfer')
    applied.output = _output(applied.output, summary)
    return applied
