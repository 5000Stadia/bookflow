"""Write a check or a card charge, find it again, read it, correct it and void it.

Nothing here decides accounting. ``_register()`` builds the same ``RegisterPostInput`` a
person could have typed into the account register, and the posting itself is
``registers.translate`` followed by the journal writer, which is the one path money takes. A
correction is the same translation with the retained line identities and
``operation='update'``, so it appends an exact reversal of the old accounting at its old date
and a full replacement at the new one; a void is the journal writer's exact reversal at the
document's own date. Neither erases anything.

The things this module owns are the document's own words, its one refusal -- the expense
lines have to add up to the amount on the face of the document, and when they do not the
error says by how much -- and the derivation that reads a stored revision back as the
document it was entered as.
"""
from __future__ import annotations

import sqlalchemy as sa

from bookflow.company import accounts, journals, money_out, parties, registers, schema as c
from bookflow.company.check_models import (
    CheckParty, DIRECTION, DOCUMENT_KIND, ExpenseLine, FUNDING_TYPE, MoneyOutHistoryOutput,
    MoneyOutOutput, MoneyOutPageOutput, MoneyOutRevisionSummaryOutput, MoneyOutSummary,
    MoneyOutSummaryOutput, MoneyOutWriteOutput,
)
from bookflow.company.journal_models import (
    JournalVoidInput, MoneyInput, checked_sum, parse_domestic_amount,
)
from bookflow.company.register_models import (
    RegisterAllocation, RegisterParty, RegisterPostInput, RegisterUpdateInput,
)
from bookflow.core.errors import BookflowError
from bookflow.core.money import Money
from bookflow.core.registry import Plan

# What each document calls the account that funds it and the figure on its face, so one
# refusal message can speak either document's language.
WORDS = {
    'check': ('check', 'a bank account', 'check amount'),
    'card-charge': ('credit card charge', 'a credit card account', 'charge amount'),
}

NOUNS = ('check', 'card-charge')

# The field each noun's own commands name the document with.
SELECTOR = {'check': 'check', 'card-charge': 'card_charge'}


def _funding(s, selector, noun):
    """The account this document is drawn on, required to be the kind that funds it."""
    account = journals.active(accounts.resolve_account(s.company, selector), 'account')
    wanted = FUNDING_TYPE[noun]
    if account['type'] != wanted:
        document, requires, _ = WORDS[noun]
        raise journals.invalid('account', f'a {document} is drawn on {requires}; '
                                          f'"{account["name"]}" is a {account["type"].replace("_", " ")} account')
    return account


def _party(party):
    return RegisterParty(name_type=party.name_type, name_id=party.name_id) if party else None


# ---------------------------------------------------------------- reading a stored document


def document(noun, header, lines):
    """The document's own footer, derived from the revision that is stored.

    The funding line is line one and the expense lines are the rest, which is how every one
    of these is written; the totals are their amounts. Nothing is recomputed from a second
    copy of the figures, because there is no second copy -- the entry is the document.

    The two facts that make it this document rather than another are checked rather than
    assumed: money has to be leaving the funding account, and that account has to be the kind
    this document is drawn on. An entry rearranged in the journal editor until neither is
    true is refused by name; see ``money_out.unreadable`` for why that beats describing it.
    """
    if len(lines) < 2:
        raise money_out.unreadable(noun, header, 'it no longer has a funding line and an expense line')
    funding, expenses = lines[0], lines[1:]
    snapshot = money_out.snapshot(funding)
    if funding['side'] != 'credit':
        raise money_out.unreadable(noun, header, 'money no longer leaves the account it is drawn on')
    if snapshot.get('type') != FUNDING_TYPE[noun]:
        _, requires, _ = WORDS[noun]
        raise money_out.unreadable(
            noun, header, f'it is no longer drawn on {requires}')
    currency = funding['currency']
    # Debits positive, credits negative: the entry balances, so this is the funding amount
    # however a later correction split the lines behind it.
    total = checked_sum((line['amount_minor_units'] if line['side'] == 'debit'
                         else -line['amount_minor_units'] for line in expenses), 'expenses.total')
    return MoneyOutSummary(
        kind=DOCUMENT_KIND[noun], account_id=funding['account_id'], funding=FUNDING_TYPE[noun],
        currency=currency, amount=Money(funding['amount_minor_units'], currency).to_dict(),
        expense_total=Money(total, currency).to_dict(), expense_lines=len(expenses))


def _saved_expenses(lines):
    """The stored expense rows as the grid a correction starts from.

    Each row keeps its own class explicitly rather than inheriting, so correcting a date or a
    payee cannot silently reclassify a line that was deliberately left unclassified.
    """
    return [ExpenseLine(
        line_id=row['line_id'], account=row['account_id'],
        amount=MoneyInput(minor_units=row['amount_minor_units'], currency=row['currency']),
        memo=row['description'],
        party=CheckParty(name_type=row['name_type'], name_id=row['name_id']) if row['name_id'] else None,
        **({'class_id': row['class_id'], 'class_mode': 'value'} if row['class_id'] else {'class_mode': 'none'}),
    ) for row in lines]


def _facts(s, inp, noun, header):
    """The complete document after this call: what was captured, with what was supplied over it."""
    if header is None:
        return dict(account=inp.account, pay_to=inp.pay_to, date=inp.date, amount=inp.amount,
                    memo=inp.memo, number=getattr(inp, 'number', None), class_id=inp.class_id,
                    expenses=list(inp.expenses), selected_line_id=None)
    revision = journals.revision(s, header)
    lines = money_out.lines(s, revision)
    document(noun, header, lines)
    funding = lines[0]
    supplied = inp.model_fields_set
    given = lambda field: field in supplied
    return dict(
        account=inp.account if given('account') else funding['account_id'],
        pay_to=(inp.pay_to if given('pay_to') else
                CheckParty(name_type=funding['name_type'], name_id=funding['name_id'])
                if funding['name_id'] else None),
        date=inp.date if given('date') else revision['date'],
        amount=(inp.amount if given('amount') else
                MoneyInput(minor_units=funding['amount_minor_units'], currency=funding['currency'])),
        memo=inp.memo if given('memo') else revision['memo'],
        number=inp.number if given('number') and getattr(inp, 'number', None) is not None else header['number'],
        class_id=inp.class_id if given('class_id') else None,
        expenses=list(inp.expenses) if inp.expenses is not None else _saved_expenses(lines[1:]),
        selected_line_id=funding['line_id'])


def _register(facts, s, noun, inp, header):
    """The register entry this document is, plus the figures its own footer shows."""
    currency = registers._home(s)
    account = _funding(s, facts['account'], noun)
    amount = parse_domestic_amount(facts['amount'], currency, 'amount')
    totals = [parse_domestic_amount(line.amount, currency, f'expenses.{index}.amount').minor_units
              for index, line in enumerate(facts['expenses'])]
    expense_total = checked_sum(totals, 'expenses.total')
    if expense_total != amount.minor_units:
        raise _mismatch(noun, currency, amount.minor_units, expense_total)
    allocations = [RegisterAllocation(
        account=line.account, amount=line.amount, memo=line.memo,
        party=_party(line.party), class_mode=line.class_mode,
        **({'class_id': line.class_id} if line.class_id is not None else {}),
        **({'line_id': line.line_id} if line.line_id is not None else {}),
    ) for line in facts['expenses']]
    values = dict(
        account=facts['account'], date=facts['date'], memo=facts['memo'], payee=_party(facts['pay_to']),
        direction=DIRECTION[FUNDING_TYPE[noun]], amount=facts['amount'], allocations=allocations,
        class_id=facts['class_id'], custom_fields=inp.custom_fields,
        custom_field_kinds=inp.custom_field_kinds,
        **({'number': facts['number']} if facts['number'] is not None else {}))
    if header is None:
        register = RegisterPostInput(**values)
    else:
        # The register input requires a version and the document does not; the version the
        # person actually supplied is carried to ``registers.translate`` instead.
        register = RegisterUpdateInput(
            journal=header['id'], selected_line_id=facts['selected_line_id'],
            expected_version=inp.expected_version if inp.expected_version is not None
            else header['version'], **values)
    summary = MoneyOutSummary(
        kind=DOCUMENT_KIND[noun], account_id=account['id'], funding=FUNDING_TYPE[noun],
        currency=currency, amount=amount.to_dict(),
        expense_total=Money(expense_total, currency).to_dict(),
        expense_lines=len(facts['expenses']))
    return register, summary


def _mismatch(noun, currency, amount, expense_total):
    """Refuse, and say by how much — a person needs the number, not the fact of a difference."""
    document_word, _, face = WORDS[noun]
    difference = expense_total - amount
    shown = {name: Money(abs(value) if name == 'difference' else value, currency).to_dict()
             for name, value in (('amount', amount), ('expense_total', expense_total),
                                 ('difference', difference))}
    direction = 'more than' if difference > 0 else 'less than'
    problem = (f'the expense lines add up to {shown["expense_total"]["amount"]} {currency}, which is '
               f'{shown["difference"]["amount"]} {currency} {direction} the {face} of '
               f'{shown["amount"]["amount"]} {currency}')
    return BookflowError('E_UNBALANCED_ENTRY', message=(
        f'The expense lines on this {document_word} do not add up to what it is written for: {problem}. '
        f'Change a line, add one, or change the {face}, so that the two agree.'), details={
        'fields': [{'field': 'expenses', 'problem': problem}],
        'document': DOCUMENT_KIND[noun], 'currency': currency,
        'amount': shown['amount'], 'expense_total': shown['expense_total'],
        'difference': shown['difference'],
        'difference_minor_units': difference,
        'amount_minor_units': amount, 'expense_total_minor_units': expense_total,
    })


def _output(journal_output, summary):
    return MoneyOutWriteOutput(**journal_output.model_dump(), document=summary)


# ---------------------------------------------------------------- reads


def show(s, inp, noun):
    header = money_out.resolve(s, getattr(inp, SELECTOR[noun]), noun)
    requested = journals.revision(s, header, inp.revision_number)
    return MoneyOutOutput(**journals.summary(header, requested),
                          revision=journals.revision_output(s, requested),
                          document=document(noun, header, money_out.lines(s, requested)))


def _shaped(noun, funding):
    """The two conditions that make a listed row this document rather than another entry."""
    return (funding.c.side == 'credit',
            sa.func.json_extract(funding.c.account_snapshot, '$.type') == FUNDING_TYPE[noun])


def page(s, ctx, inp, noun):
    state = money_out.state_for(s, ctx, inp, noun, 'query')
    t, r, m = c.transactions, c.transaction_revisions, c.money_out_documents
    funding = c.document_lines.alias('funding_line')
    query = (sa.select(t.c.id).select_from(
        t.join(r, r.c.id == t.c.current_revision_id)
        .join(m, m.c.transaction_id == t.c.id)
        .join(funding, sa.and_(funding.c.revision_id == r.c.id, funding.c.position == 1)))
        .where(m.c.kind == money_out.KIND[noun], *_shaped(noun, funding)))
    if inp.date_from:
        query = query.where(r.c.date >= inp.date_from)
    if inp.date_to:
        query = query.where(r.c.date <= inp.date_to)
    if inp.status:
        query = query.where(t.c.status == inp.status)
    if inp.number:
        query = query.where(t.c.number.contains(inp.number, autoescape=True))
    if inp.account:
        query = query.where(funding.c.account_id == accounts.resolve_account(s.company, inp.account)['id'])
    if inp.payee:
        payee = parties.resolve_party(s.company, inp.payee_type.replace('_', '-'), inp.payee)
        query = query.where(funding.c.name_type == inp.payee_type, funding.c.name_id == payee['id'])
    if inp.query:
        query = query.where(sa.or_(t.c.number.contains(inp.query, autoescape=True),
                                   r.c.memo.contains(inp.query, autoescape=True)))
    found, shared = money_out.take(s, money_out.ordered(query, inp.direction), state, inp.limit)
    pairs = money_out.headers(s, [row['id'] for row in found])
    grouped = money_out.lines_by_revision(s, [revision['id'] for _, revision in pairs])
    return MoneyOutPageOutput(items=[
        MoneyOutSummaryOutput(**journals.summary(header, revision),
                              document=document(noun, header, grouped[revision['id']]))
        for header, revision in pairs], **shared)


def history(s, ctx, inp, noun):
    header = money_out.resolve(s, getattr(inp, SELECTOR[noun]), noun)
    found, shared, grouped = money_out.history_page(s, ctx, inp, noun, header)
    items = []
    for revision in found:
        base = journals.revision_output(s, revision, summary_only=True)
        items.append(MoneyOutRevisionSummaryOutput(
            **base.model_dump(), document=document(noun, header, grouped[revision['id']])))
    return MoneyOutHistoryOutput(
        **{key: header[key] for key in ('id', 'version', 'current_revision_id', 'number', 'status')},
        items=items, **shared)


# ---------------------------------------------------------------- writes


def _translate(s, ctx, inp, noun, operation):
    """The journal this call posts, plus the document footer it will show."""
    header = money_out.resolve(s, getattr(inp, SELECTOR[noun]), noun) if operation != 'post' else None
    if operation == 'void':
        lines = money_out.lines(s, journals.revision(s, header))
        return (JournalVoidInput(journal=header['id'], expected_version=inp.expected_version),
                document(noun, header, lines), header)
    facts = _facts(s, inp, noun, header)
    register, summary = _register(facts, s, noun, inp, header)
    journal, _ = registers.translate(register, s, operation, moving=operation == 'update',
                                     expected_version=inp.expected_version if header else None)
    return journal, summary, header


def prepare(s, ctx, inp, noun, operation):
    journal, summary, _ = _translate(s, ctx, inp, noun, operation)
    fresh = journals.prepare(s, ctx, journal, operation)
    return Plan(_output(fresh.preview, summary), {'input': inp, 'noun': noun, 'operation': operation})


def apply(plan, ctx, s):
    # Rebuilt inside the writer transaction, the same way the register does it: references,
    # dates, versions and numbering are only decisive here.
    inp, noun, operation = plan.data['input'], plan.data['noun'], plan.data['operation']
    journal, summary, _ = _translate(s, ctx, inp, noun, operation)
    fresh = journals.prepare(s, ctx, journal, operation)
    extra = ()
    if operation == 'post' and fresh.data['changed']:
        header = fresh.data['header']
        extra = (('money_out_documents', 'money_out_document', 'transaction_id', [money_out.marker(
            noun, header['id'], at=header['created_at'], actor_id=s.actor.id,
            interface=ctx.interface.value, event=fresh.data['event'])]),)
    applied = journals.persist_prepared(fresh, ctx, s, command_name=f'{noun} {operation}',
                                        extra=extra, noun=WORDS[noun][0])
    applied.output = _output(applied.output, summary)
    return applied
