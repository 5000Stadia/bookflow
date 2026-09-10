"""Translate a check or a card charge into the register split that already posts it.

Nothing here decides accounting. ``_register()`` builds the same ``RegisterPostInput`` a
person could have typed into the account register, and the posting itself is
``registers.translate`` followed by the journal writer, which is the one path money takes.

The two things this module owns are the document's own words and its one refusal: the
expense lines have to add up to the amount on the face of the document, and when they do not
the error says by how much.
"""
from __future__ import annotations

from bookflow.company import accounts, journals, registers
from bookflow.company.check_models import (
    DIRECTION, DOCUMENT_KIND, FUNDING_TYPE, MoneyOutSummary, MoneyOutWriteOutput,
)
from bookflow.company.journal_models import checked_sum, parse_domestic_amount
from bookflow.company.register_models import RegisterAllocation, RegisterParty, RegisterPostInput
from bookflow.core.errors import BookflowError
from bookflow.core.money import Money
from bookflow.core.registry import Plan

# What each document calls the account that funds it and the figure on its face, so one
# refusal message can speak either document's language.
WORDS = {
    'check': ('check', 'a bank account', 'check amount'),
    'card-charge': ('credit card charge', 'a credit card account', 'charge amount'),
}


def _funding(s, inp, noun):
    """The account this document is drawn on, required to be the kind that funds it."""
    account = journals.active(accounts.resolve_account(s.company, inp.account), 'account')
    wanted = FUNDING_TYPE[noun]
    if account['type'] != wanted:
        document, requires, _ = WORDS[noun]
        raise journals.invalid('account', f'a {document} is drawn on {requires}; '
                                          f'"{account["name"]}" is a {account["type"].replace("_", " ")} account')
    return account


def _party(party):
    return RegisterParty(name_type=party.name_type, name_id=party.name_id) if party else None


def _register(inp, s, noun):
    """The register split this document is, plus the figures its own footer shows."""
    currency = registers._home(s)
    account = _funding(s, inp, noun)
    amount = parse_domestic_amount(inp.amount, currency, 'amount')
    totals = [parse_domestic_amount(line.amount, currency, f'expenses.{index}.amount').minor_units
              for index, line in enumerate(inp.expenses)]
    expense_total = checked_sum(totals, 'expenses.total')
    if expense_total != amount.minor_units:
        raise _mismatch(noun, currency, amount.minor_units, expense_total)
    allocations = [RegisterAllocation(
        account=line.account, amount=line.amount, memo=line.memo,
        party=_party(line.party), class_mode=line.class_mode,
        **({'class_id': line.class_id} if line.class_id is not None else {}),
    ) for line in inp.expenses]
    register = RegisterPostInput(
        account=inp.account, date=inp.date, memo=inp.memo, payee=_party(inp.pay_to),
        direction=DIRECTION[FUNDING_TYPE[noun]], amount=inp.amount, allocations=allocations,
        class_id=inp.class_id, custom_fields=inp.custom_fields,
        custom_field_kinds=inp.custom_field_kinds,
        **({'number': inp.number} if getattr(inp, 'number', None) is not None else {}),
    )
    summary = MoneyOutSummary(
        kind=DOCUMENT_KIND[noun], account_id=account['id'], funding=FUNDING_TYPE[noun],
        currency=currency, amount=amount.to_dict(),
        expense_total=Money(expense_total, currency).to_dict(), expense_lines=len(inp.expenses),
    )
    return register, summary


def _mismatch(noun, currency, amount, expense_total):
    """Refuse, and say by how much — a person needs the number, not the fact of a difference."""
    document, _, face = WORDS[noun]
    difference = expense_total - amount
    shown = {name: Money(abs(value) if name == 'difference' else value, currency).to_dict()
             for name, value in (('amount', amount), ('expense_total', expense_total),
                                 ('difference', difference))}
    direction = 'more than' if difference > 0 else 'less than'
    problem = (f'the expense lines add up to {shown["expense_total"]["amount"]} {currency}, which is '
               f'{shown["difference"]["amount"]} {currency} {direction} the {face} of '
               f'{shown["amount"]["amount"]} {currency}')
    return BookflowError('E_UNBALANCED_ENTRY', message=(
        f'The expense lines on this {document} do not add up to what it is written for: {problem}. '
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


def prepare(s, ctx, inp, noun):
    register, summary = _register(inp, s, noun)
    journal, _ = registers.translate(register, s, 'post')
    fresh = journals.prepare(s, ctx, journal, 'post')
    return Plan(_output(fresh.preview, summary), {'input': inp, 'noun': noun})


def apply(plan, ctx, s):
    # Rebuilt inside the writer transaction, the same way the register does it: references,
    # dates and numbering are only decisive here.
    inp, noun = plan.data['input'], plan.data['noun']
    register, summary = _register(inp, s, noun)
    journal, _ = registers.translate(register, s, 'post')
    fresh = journals.prepare(s, ctx, journal, 'post')
    applied = journals.persist_prepared(fresh, ctx, s, command_name=noun + ' post')
    applied.output = _output(applied.output, summary)
    return applied
