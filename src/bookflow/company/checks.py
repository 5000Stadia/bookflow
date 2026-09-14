"""Write a check or a card charge, find it again, read it, correct it and void it.

``_register()`` builds register allocations from expenses and captured purchase items. Posting is
the shared register allocation resolver followed by the journal writer, which is the one path money takes. A
correction is the same translation with the retained line identities and
``operation='update'``, so it appends an exact reversal of the old accounting at its old date
and a full replacement at the new one; a void is the journal writer's exact reversal at the
document's own date. Neither erases anything.

Items and expenses must reconcile to the amount on the face of the document. Captured
item facts and stock movements persist atomically with the journal through check_items.
The shared inventory owner validates attribution and dated cost corrections.

**The check number is not the journal's number.** What the register writes carries no number
at all now: the journal takes its own reference from the shared document series, the way every
other entry does, and the number on the face of the cheque is an account-scoped fact this
module hands to ``company/check_numbers.py`` to allocate, refuse or carry forward. A card
charge has no such number -- the card statement carries the reference -- so its request is
simply absent rather than a validator saying so in prose.
"""
from __future__ import annotations

import json
import sqlalchemy as sa

from bookflow.company import (
    accounts, check_numbers, journals, money_out, parties, registers, schema as c, check_items, inventory_effects)
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


def document(s, noun, header, lines, check_number=None):
    """The document's own footer, derived from the revision that is stored.

    Positive money has a funding line first. A free purchase retains funding intent in its
    immutable revision profile and has only commercial item envelopes. Nothing is recomputed from a second
    copy of the figures, because there is no second copy -- the entry is the document.

    The two facts that make it this document rather than another are checked rather than
    assumed: money has to be leaving the funding account, and that account has to be the kind
    this document is drawn on. An entry rearranged in the journal editor until neither is
    true is refused by name; see ``money_out.unreadable`` for why that beats describing it.
    """
    if not lines:
        raise money_out.unreadable(noun, header, 'it no longer has a funding line and an expense line')
    captured = check_items.stored(s, lines)
    item_ids = {row["line_id"] for row in captured}
    funding = check_items.funding(s, lines)
    expenses = [line for line in lines if line['kind'] == 'journal' and line['line_id'] != funding['line_id'] and line['line_id'] not in item_ids]
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
        expense_total=Money(total, currency).to_dict(), expense_lines=len(expenses),
        check_number=check_number, funding_details=funding if not funding['amount_minor_units'] else None,
        item_total=Money(checked_sum((row['amount_minor_units'] for row in captured), 'items.total'), currency).to_dict(),
        items=[check_items.output(row, row['line_id'], currency) for row in captured])


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
                    expenses=list(inp.expenses), selected_line_id=None,
                    items=check_items.resolve(s, inp.items, [], inp.class_id, registers._home(s)))
    revision = journals.revision(s, header)
    lines = money_out.lines(s, revision)
    document(s, noun, header, lines)
    previous_items = check_items.stored(s, lines)
    item_ids = {row['line_id'] for row in previous_items}
    funding = check_items.funding(s, lines)
    supplied = inp.model_fields_set
    given = lambda field: field in supplied
    return dict(
        account=inp.account if given('account') else funding['account_id'],
        pay_to=(inp.pay_to if given('pay_to') else
                CheckParty(name_type=funding['name_type'], name_id=funding['name_id'])
                if funding['name_id'] else None),
        date=inp.date if given('date') else revision['date'],
        amount=(inp.amount if given('amount') else
                Money(funding['amount_minor_units'], funding['currency']).to_dict()['amount']),
        memo=inp.memo if given('memo') else revision['memo'],
        # A correction that does not name a number keeps the one on the paper; None here
        # means "keep", and company/check_numbers.py is what knows what is already there.
        number=getattr(inp, 'number', None),
        class_id=inp.class_id if given('class_id') else None,
        expenses=list(inp.expenses) if inp.expenses is not None else _saved_expenses([line for line in lines if line['kind'] == 'journal' and line['line_id'] != funding['line_id'] and line['line_id'] not in item_ids]),
        items=check_items.resolve(s, inp.items, previous_items, inp.class_id, registers._home(s)),
        selected_line_id=funding['line_id'])


def _register(facts, s, noun, inp, header):
    """The register entry this document is, plus the figures its own footer shows."""
    currency = registers._home(s)
    account = _funding(s, facts['account'], noun)
    from bookflow.company.sales_models import money
    amount = money(facts['amount'], currency, 'amount')
    totals = [parse_domestic_amount(line.amount, currency, f'expenses.{index}.amount').minor_units
              for index, line in enumerate(facts['expenses'])]
    expense_total = checked_sum(totals, 'expenses.total')
    item_total = checked_sum((line['amount_minor_units'] for line in facts['items']), 'items.total')
    if not facts['expenses'] and not facts['items']:
        raise journals.invalid('items', 'at least one expense or item is required')
    if len(facts['expenses']) + len(facts['items']) > 199:
        raise journals.invalid('items', 'at most 199 allocations are allowed')
    if checked_sum((expense_total, item_total), 'allocations.total') != amount.minor_units:
        raise _mismatch(noun, currency, amount.minor_units, expense_total, item_total)
    allocations = [RegisterAllocation(
        account=line.account, amount=line.amount, memo=line.memo,
        party=_party(line.party), class_mode=line.class_mode,
        **({'class_id': line.class_id} if line.class_id is not None else {}),
        **({'line_id': line.line_id} if line.line_id is not None else {}),
    ) for line in facts['expenses']]
    allocations += [RegisterAllocation(account=line['profile'].account.id,
        amount=Money(line['amount_minor_units'], currency).to_dict()['amount'], memo=line['memo'],
        party=RegisterParty(name_type='customer', name_id=line['profile'].customer.id) if line['profile'].customer else None,
        class_id=line['profile'].class_id.id if line['profile'].class_id else None,
        class_mode='value' if line['profile'].class_id else 'none',
        **({'line_id': line['line_id']} if line['line_id'] else {}))
        for line in facts['items']]
    values = dict(
        account=facts['account'], date=facts['date'], memo=facts['memo'], payee=_party(facts['pay_to']),
        direction=DIRECTION[FUNDING_TYPE[noun]], amount=amount.to_dict()['amount'], allocations=allocations,
        class_id=facts['class_id'], custom_fields=inp.custom_fields,
        custom_field_kinds=inp.custom_field_kinds)
    if header is None:
        register = RegisterPostInput(**values)
    else:
        # The register input requires a version and the document does not; the version the
        # person actually supplied is carried to ``registers.translate`` instead.
        register = check_items.PurchaseRegisterUpdate(
            journal=header['id'], selected_line_id=facts['selected_line_id'],
            expected_version=inp.expected_version if inp.expected_version is not None
            else header['version'], **values)
    summary = MoneyOutSummary(
        kind=DOCUMENT_KIND[noun], account_id=account['id'], funding=FUNDING_TYPE[noun],
        check_number=check_numbers.current(s, header['id'])['check_number'] if noun == 'check' and header else None,
        currency=currency, amount=amount.to_dict(),
        expense_total=Money(expense_total, currency).to_dict(),
        expense_lines=len(facts['expenses']), item_total=Money(item_total, currency).to_dict(),
        items=[check_items.output(row, row['line_id'] or 'pending', currency) for row in facts['items']])
    # Only a cheque asks for one. A card charge never consumes a check number, so it sends
    # no request at all and the journal it posts keeps its own document reference alone.
    instrument = (check_numbers.request(account['id'], facts['number'])
                  if noun == 'check' else None)
    return register, summary, instrument, facts["items"]


def _mismatch(noun, currency, amount, expense_total, item_total=0):
    """Refuse, and say by how much — a person needs the number, not the fact of a difference."""
    document_word, _, face = WORDS[noun]
    difference = expense_total + item_total - amount
    shown = {name: Money(abs(value) if name == 'difference' else value, currency).to_dict()
             for name, value in (('amount', amount), ('expense_total', expense_total),
                                 ('difference', difference), ('item_total', item_total), ('allocated_total', expense_total + item_total))}
    direction = 'more than' if difference > 0 else 'less than'
    problem = (f'the expense and item lines add up to {shown["allocated_total"]["amount"]} {currency}, which is '
               f'{shown["difference"]["amount"]} {currency} {direction} the {face} of '
               f'{shown["amount"]["amount"]} {currency}')
    return BookflowError('E_UNBALANCED_ENTRY', message=(
        f'The expense lines on this {document_word} do not add up to what it is written for: {problem}. '
        f'Change a line, add one, or change the {face}, so that the two agree.'), details={
        'fields': [{'field': 'expenses', 'problem': problem}],
        'document': DOCUMENT_KIND[noun], 'currency': currency,
        'amount': shown['amount'], 'expense_total': shown['expense_total'],
        'item_total': shown['item_total'], 'allocated_total': shown['allocated_total'],
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
    # The number this revision was written with, not the one the cheque carries today: a
    # correction that renumbered it must not rewrite what the earlier revision said.
    numbers = money_out.check_numbers_of(s, noun, [requested['id']])
    return MoneyOutOutput(**journals.summary(header, requested),
                          revision=journals.revision_output(s, requested),
                          document=document(s, noun, header, money_out.lines(s, requested),
                                            numbers.get(requested['id'])))


def _number_filter(noun, text):
    """What `number` means to each noun: the cheque's own number, or the document reference.

    A cheque is looked up by what is written on it, because that is the only number a person
    reading a chequebook has. A card charge has no such number, so its filter is the document
    reference it does carry.
    """
    t = c.transactions
    if noun != 'check':
        return t.c.number.contains(text, autoescape=True)
    instruments = c.check_instruments
    return t.c.id.in_(sa.select(instruments.c.transaction_id).where(
        instruments.c.check_number.contains(text, autoescape=True)))


def _shaped(noun, funding):
    """The two conditions that make a listed row this document rather than another entry."""
    return (funding.c.side == 'credit',
            sa.func.json_extract(funding.c.account_snapshot, '$.type') == FUNDING_TYPE[noun])


def page(s, ctx, inp, noun):
    state = money_out.state_for(s, ctx, inp, noun, 'query')
    t, r, m = c.transactions, c.transaction_revisions, c.money_out_documents
    funding = c.document_lines.alias('funding_line')
    profile = c.money_out_revision_profiles
    def captured(field):
        return sa.case((profile.c.revision_id.is_not(None), sa.func.json_extract(profile.c.funding_snapshot, '$.' + field)), else_=getattr(funding.c, field))
    query = (sa.select(t.c.id).select_from(
        t.join(r, r.c.id == t.c.current_revision_id)
        .join(m, m.c.transaction_id == t.c.id)
        .outerjoin(funding, sa.and_(funding.c.revision_id == r.c.id, funding.c.position == 1))
        .outerjoin(profile, profile.c.revision_id == r.c.id))
        .where(m.c.kind == money_out.KIND[noun], captured('side') == 'credit',
            sa.func.json_extract(captured('account_snapshot'), '$.type') == FUNDING_TYPE[noun]))
    if inp.date_from:
        query = query.where(r.c.date >= inp.date_from)
    if inp.date_to:
        query = query.where(r.c.date <= inp.date_to)
    if inp.status:
        query = query.where(t.c.status == inp.status)
    if inp.number:
        query = query.where(_number_filter(noun, inp.number))
    if inp.account:
        query = query.where(captured('account_id') == accounts.resolve_account(s.company, inp.account)['id'])
    if inp.payee:
        payee = parties.resolve_party(s.company, inp.payee_type.replace('_', '-'), inp.payee)
        query = query.where(captured('name_type') == inp.payee_type, captured('name_id') == payee['id'])
    if inp.query:
        query = query.where(sa.or_(_number_filter(noun, inp.query),
                                   r.c.memo.contains(inp.query, autoescape=True)))
    found, shared = money_out.take(s, money_out.ordered(query, inp.direction), state, inp.limit)
    pairs = money_out.headers(s, [row['id'] for row in found])
    grouped = money_out.lines_by_revision(s, [revision['id'] for _, revision in pairs])
    numbers = money_out.check_numbers_of(s, noun, [revision['id'] for _, revision in pairs])
    return MoneyOutPageOutput(items=[
        MoneyOutSummaryOutput(**journals.summary(header, revision),
                              document=document(s, noun, header, grouped[revision['id']],
                                                numbers.get(revision['id'])))
        for header, revision in pairs], **shared)


def history(s, ctx, inp, noun):
    header = money_out.resolve(s, getattr(inp, SELECTOR[noun]), noun)
    found, shared, grouped = money_out.history_page(s, ctx, inp, noun, header)
    numbers = money_out.check_numbers_of(s, noun, [revision['id'] for revision in found])
    items = []
    for revision in found:
        base = journals.revision_output(s, revision, summary_only=True)
        items.append(MoneyOutRevisionSummaryOutput(
            **base.model_dump(), document=document(s, noun, header, grouped[revision['id']],
                                                   numbers.get(revision['id']))))
    return MoneyOutHistoryOutput(
        **{key: header[key] for key in ('id', 'version', 'current_revision_id', 'number', 'status')},
        items=items, **shared)


# ---------------------------------------------------------------- writes


def _translate(s, ctx, inp, noun, operation):
    """The journal this call posts, the footer it will show, and what it asks of the chequebook."""
    header = money_out.resolve(s, getattr(inp, SELECTOR[noun]), noun) if operation != 'post' else None
    if operation == 'void':
        revision = journals.revision(s, header)
        lines = money_out.lines(s, revision)
        numbers = money_out.check_numbers_of(s, noun, [revision['id']])
        # A voided cheque keeps its number: the paper it was written on is still gone, so
        # nothing here asks the chequebook for anything.
        return (JournalVoidInput(journal=header['id'], expected_version=inp.expected_version),
                document(s, noun, header, lines, numbers.get(revision['id'])), header, None, [], check_items.funding(s, lines))
    facts = _facts(s, inp, noun, header)
    register, summary, instrument, items = _register(facts, s, noun, inp, header)
    journal, funding = check_items.translate(s, register, summary, header, inp.expected_version if header else None)
    if not summary.amount.minor_units:
        summary = summary.model_copy(update={'funding_details': funding})
    return journal, summary, header, instrument, items, funding


def _finish(summary, planned):
    """Put the number the writer actually settled on into the footer this call returns."""
    if planned is not None:
        summary = summary.model_copy(update={'check_number': planned['instrument']['check_number']})
    return summary


def prepare(s, ctx, inp, noun, operation):
    journal, summary, header, instrument, items, funding = _translate(s, ctx, inp, noun, operation)
    fresh = journals.prepare(s, ctx, journal, operation, check_instrument=instrument,
                             owner='inventory', purchase_items=items, force_revision=(operation == 'update' and (check_items.changed(s, header, items) or check_items.funding_changed(s, header, funding))))
    fresh.data['purchase_funding'] = funding
    item_rows, stock, item_outputs = check_items.attach(s, fresh, items)
    if operation != 'void' and fresh.data['changed']:
        summary = summary.model_copy(update={'items': item_outputs})
    return Plan(_output(fresh.preview, _finish(summary, fresh.data.get('check_instrument'))),
                {'input': inp, 'noun': noun, 'operation': operation})


def apply(plan, ctx, s):
    # Rebuilt inside the writer transaction, the same way the register does it: references,
    # dates, versions and numbering -- the cheque's as well as the journal's -- are only
    # decisive here. A retry under the same idempotency key never reaches this: dispatch
    # replays the stored output, so the cheque keeps the number the first attempt gave it.
    inp, noun, operation = plan.data['input'], plan.data['noun'], plan.data['operation']
    journal, summary, header, instrument, items, funding = _translate(s, ctx, inp, noun, operation)
    fresh = journals.prepare(s, ctx, journal, operation, check_instrument=instrument,
                             owner='inventory', purchase_items=items, force_revision=(operation == 'update' and (check_items.changed(s, header, items) or check_items.funding_changed(s, header, funding))))
    fresh.data['purchase_funding'] = funding
    item_rows, stock, item_outputs = check_items.attach(s, fresh, items)
    if operation != 'void' and fresh.data['changed']:
        summary = summary.model_copy(update={'items': item_outputs})
    extra = ()
    if operation == 'post' and fresh.data['changed']:
        header = fresh.data['header']
        extra = (('money_out_documents', 'money_out_document', 'transaction_id', [money_out.marker(
            noun, header['id'], at=header['created_at'], actor_id=s.actor.id,
            interface=ctx.interface.value, event=fresh.data['event'])]),)
    if operation != 'void' and fresh.data['changed']:
        monetary = [l for l in fresh.data['pending']['document_lines'] if l['kind'] == 'journal']
        funding['line_id'] = monetary[0]['line_id'] if monetary else None
        extra += (('money_out_revision_profiles', 'money_out_revision_profile', 'revision_id', [dict(transaction_id=fresh.data['header']['id'], revision_id=fresh.preview.current_revision_id, funding_snapshot=json.dumps(funding, sort_keys=True))]),)
    if item_rows:
        extra += (('money_out_item_lines', 'money_out_item_line', 'document_line_id', item_rows),)
    planned = fresh.data.get('check_instrument') if fresh.data['changed'] else None
    applied = journals.persist_prepared(
        fresh, ctx, s, command_name=f'{noun} {operation}', extra=extra, noun=WORDS[noun][0],
        number=planned['instrument']['check_number'] if planned else None)
    if stock is not None:
        applied = inventory_effects.settle(applied, stock, ctx, s,
            command_name=f'{noun} {operation}', summary=applied.summary)
    applied.output = _output(applied.output, _finish(summary, planned))
    return applied
