"""The short account of what a payment settled, beside its full detail.

A payment write returns every application, allocation and settlement change it made, which is
complete but long. ``summary`` says the same thing in the words a bookkeeper would use: which
documents it paid, what each still owes, what is left as the payer's credit, and any
early-payment discount taken. Every figure is copied from the write it summarises; nothing here
decides accounting.
"""
from __future__ import annotations

from pydantic import Field

from bookflow.company.journal_outputs import JournalMoneyOutput
from bookflow.core.models import StrictModel
from bookflow.core.money import Money

#: The documents listed one by one, in the structured list and in the sentence. A larger
#: settlement is still counted in full; its complete rows are in the write's own detail.
LISTED = 50
NAMED = 10

_KIND_WORDS = {'invoice': 'invoice', 'statement_charge': 'statement charge', 'bill': 'bill'}


class SettledDocument(StrictModel):
    document_id: str
    document_type: str
    number: str
    applied: JournalMoneyOutput = Field(description='Money from this payment applied to the document.')
    discount: JournalMoneyOutput | None = Field(default=None, exclude_if=lambda v: v is None,
                                                description='Early-payment discount taken on it, beside the money.')
    still_due: JournalMoneyOutput = Field(description='What the document still owes after this payment.')
    paid_in_full: bool


class PaymentSummary(StrictModel):
    text: str = Field(description='One plain paragraph: documents paid, what each still owes, credit left, discounts.')
    documents: list[SettledDocument] = Field(description=f'The documents this write settled, at most {LISTED}.')
    document_count: int
    paid_in_full_count: int
    still_due: JournalMoneyOutput = Field(description='Total still owed on the documents this write settled.')
    credit: JournalMoneyOutput | None = Field(default=None, exclude_if=lambda v: v is None, description=(
        "Money on this payment not applied to any document: the customer's credit, to apply later or refund. "
        'Absent for a bill payment.'))
    discount: JournalMoneyOutput | None = Field(default=None, exclude_if=lambda v: v is None,
                                                description='Early-payment discounts taken in total.')


def _names(rows, limit=NAMED):
    shown = [row['number'] for row in rows[:limit]]
    return ', '.join(shown) + (f' and {len(rows) - limit} more' if len(rows) > limit else '')


def summarize(rows, currency, *, opening, credit=None, party_label=None, discount_label='Early-payment discount taken') -> PaymentSummary:
    """``rows``: dicts of document_id, document_type, number, applied, discount, still_due (minor units).

    ``opening`` is the sentence that starts the paragraph, e.g. "Received 150.00 USD from Jones."
    ``credit`` is what stays on the payment as credit (customer payments only).
    ``discount_label`` names what the discount was: a write-off is a discount to an expense account.
    """
    money = lambda units: Money(units, currency)
    paid = [row for row in rows if row['still_due'] == 0]
    owing = [row for row in rows if row['still_due'] != 0]
    discount = sum(row['discount'] for row in rows)
    words = [opening]
    kind = _KIND_WORDS.get(rows[0]['document_type'], 'document') if rows else 'document'
    plural = lambda n: kind + ('s' if n != 1 else '')
    if paid:
        words.append(f'Paid in full: {len(paid)} {plural(len(paid))} ({_names(paid)}).')
    for row in owing[:NAMED]:
        words.append(f'{_KIND_WORDS.get(row["document_type"], "Document").capitalize()} {row["number"]}: '
                     f'{money(row["applied"] + row["discount"])} paid, {money(row["still_due"])} still due.')
    if len(owing) > NAMED:
        words.append(f'{len(owing) - NAMED} more {plural(len(owing) - NAMED)} still partly due.')
    if not rows:
        words.append('Nothing was applied to any document.')
    if discount:
        words.append(f'{discount_label}: {money(discount)}.')
    if credit is not None:
        words.append(f'{money(credit)} left as credit' + (f' for {party_label}' if party_label else '') + '.'
                     if credit else 'Nothing left as credit.')
    return PaymentSummary(
        text=' '.join(words),
        documents=[SettledDocument(document_id=row['document_id'], document_type=row['document_type'],
                                   number=row['number'], applied=money(row['applied']).to_dict(),
                                   discount=money(row['discount']).to_dict() if row['discount'] else None,
                                   still_due=money(row['still_due']).to_dict(), paid_in_full=row['still_due'] == 0)
                   for row in rows[:LISTED]],
        document_count=len(rows), paid_in_full_count=len(paid),
        still_due=money(sum(row['still_due'] for row in rows)).to_dict(),
        credit=money(credit).to_dict() if credit is not None else None,
        discount=money(discount).to_dict() if discount else None)
