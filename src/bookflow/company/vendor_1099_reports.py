"""The 1099 vendor summary: what was paid to each 1099 vendor in a year. A report, not a filing.

The anchor's 1099 summary lists the vendors marked eligible for a 1099 and what was paid to
each of them in the calendar year, by default only those paid at least the year's filing
threshold. It is a cash report by nature: what counts is money paid, on the day it was
paid, not bills entered.

**Paid means money out of a bank account to the vendor.** A payment is every posting line
on a bank account that names the vendor, credit minus debit: the bank side of a bill
payment and of a check, less a vendor refund deposited back. A payment made by credit card
is not counted, because the card company reports it on its own information return; it is
shown beside the payments so the reader can see what was left out and why. Voided and
corrected payments net to what the ledger says was finally paid on each date.

**Every such payment is nonemployee compensation, box 1 of the 1099-NEC.** Bookflow does
not yet map expense accounts to 1099 boxes, so there is no account to exclude a payment by
and no second box to put one in; this is the anchor's result for a company whose 1099
accounts are all mapped to that one box.

**The threshold is the year's filing threshold for the 1099-NEC**: 600.00 for payments
made before 2026 and 2,000.00 for payments made in 2026 or later, read for the year of
``date_to``. The inflation adjustment the law applies after 2026 is not applied.
"""
from __future__ import annotations

from bookflow.company.party_merges import survivor_sql
from typing import Literal

from pydantic import Field, field_validator, model_validator

from bookflow.company import ledger_reports as ledger
from bookflow.company.ledger_reports import MoneyOutput, StrictModel, iso_date, money
from bookflow.company.payable_reports import NO_VENDOR

# Filing threshold in minor units, by the first year it applies.
THRESHOLDS = ((2026, 200000), (1, 60000))


def threshold_for(year: int) -> int:
    return next(amount for first, amount in THRESHOLDS if year >= first)


class Vendor1099SummaryInput(StrictModel):
    date_from: str = Field(min_length=10, max_length=10, description="Inclusive first payment date, YYYY-MM-DD; a 1099 year is January 1 to December 31.")
    date_to: str = Field(min_length=10, max_length=10, description="Inclusive last payment date, YYYY-MM-DD; its year decides the filing threshold.")
    above_threshold_only: bool = Field(default=True, description="List only vendors paid at least the year's filing threshold, as the anchor does by default. False lists every 1099 vendor paid anything.")
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _dates = field_validator("date_from", "date_to")(iso_date)

    @model_validator(mode="after")
    def ordered(self):
        if self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class Vendor1099Totals(StrictModel):
    threshold: MoneyOutput
    reportable: MoneyOutput
    vendors_meeting_threshold: int = Field(ge=0)
    payments: MoneyOutput
    card_payments_excluded: MoneyOutput


class Vendor1099Row(StrictModel):
    vendor_id: str
    current_vendor_name: str
    display_vendor_label: str
    active: bool
    box: Literal["nonemployee_compensation"]
    payments: MoneyOutput
    card_payments_excluded: MoneyOutput
    meets_threshold: bool


class Vendor1099SummaryOutput(ledger.Page):
    totals: Vendor1099Totals
    rows: list[Vendor1099Row]


# Money out to each 1099 vendor, by the kind of account it left. Only stored INTEGER columns
# are subtracted; the sums are lossless text.
_PAID = """
WITH paid AS (
 SELECT """ + survivor_sql("vendor", "l.name_id") + """ AS vendor, a.type AS account_type, l.credit_minor_units-l.debit_minor_units AS amount
 FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id
 JOIN accounts a ON a.id=l.account_id
 WHERE l.name_type='vendor' AND a.type IN ('bank', 'credit_card')
   AND b.effective_date>=:date_from AND b.effective_date<=:date_to
), by_vendor AS (
 SELECT vendor,
   coalesce(bookflow_sum_int(CASE WHEN account_type='bank' THEN amount END),'0') AS payments,
   coalesce(bookflow_sum_int(CASE WHEN account_type='credit_card' THEN amount END),'0') AS card
 FROM paid GROUP BY vendor
)
SELECT v.id, v.name, v.name_key, v.active, p.payments, p.card
FROM by_vendor p JOIN vendors v ON v.id=p.vendor
WHERE v.eligible_1099=1 AND (p.payments!='0' OR p.card!='0')
ORDER BY v.name_key, v.id
"""


def vendor_1099_summary(inp: Vendor1099SummaryInput, s, *, principal_id=None) -> Vendor1099SummaryOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        state, offset = ledger._state(s, inp, "vendor-1099-summary", principal_id, None, account_scoped=False)
        # Money paid, on the day it was paid: the report is cash by nature, whatever the
        # company reports its statements on.
        state = state.model_copy(update={"metadata": state.metadata.model_copy(update={"basis": "cash"})})
        raw, currency = s.company.raw, state.metadata.currency
        threshold = threshold_for(int(inp.date_to[:4]))
        vendors = []
        for vendor_id, name, _key, active, payments, card in raw.execute(
                _PAID, {"date_from": inp.date_from, "date_to": inp.date_to}):
            payments = money(int(payments), currency).minor_units
            card = money(int(card), currency).minor_units
            vendors.append(dict(vendor_id=vendor_id, name=name, active=bool(active), payments=payments,
                                card=card, meets=payments >= threshold))
        reportable = sum(vendor["payments"] for vendor in vendors if vendor["meets"])
        meeting = sum(1 for vendor in vendors if vendor["meets"])
        listed = [vendor for vendor in vendors if vendor["meets"] or not inp.above_threshold_only]
        page = listed[offset:offset + inp.limit + 1]
        rows = [Vendor1099Row(vendor_id=vendor["vendor_id"], current_vendor_name=vendor["name"],
            display_vendor_label=str(vendor["name"]) if vendor["name"] is not None else NO_VENDOR,
            active=vendor["active"], box="nonemployee_compensation",
            payments=money(vendor["payments"], currency), card_payments_excluded=money(vendor["card"], currency),
            meets_threshold=vendor["meets"]) for vendor in page[:inp.limit]]
        return Vendor1099SummaryOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=Vendor1099Totals(threshold=money(threshold, currency), reportable=money(reportable, currency),
                vendors_meeting_threshold=meeting,
                payments=money(sum(vendor["payments"] for vendor in listed), currency),
                card_payments_excluded=money(sum(vendor["card"] for vendor in listed), currency)),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))
