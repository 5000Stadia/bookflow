"""Where a period's money came from and where it went, over the same immutable effects.

Four breakdowns of one profit-and-loss period, each summing exactly the posting lines
one of that statement's own totals sums.

**Nothing here selects on a document type.** An income effect is an effect on an account
the profit and loss calls income, and an expense effect is an effect on an account it
calls a cost, whatever document wrote it. That is why ``report expenses-by-vendor``
covers bills, cheques, credit card charges, vendor credits and hand-written journal
entries without naming any of them: the set of expense-bearing documents is not a list
anybody has to maintain, it is whatever posts to those accounts, and the account side is
read from ``financial_statements`` rather than retyped here. A document family that lands
next year is in these reports the day it posts.

**The three sales reports are one total cut three ways**, and every cut is the ``income``
total on ``report profit-and-loss`` for the same dates. The customer cut
is complete by construction -- every income line lands on a customer or on the one row for
income no customer was named on. The item cut is not: a sales line always names an item, so
income with no item is income no sale line posted -- an income journal entry, a deposit
taken straight to an income account -- and this report says what that came to instead of
dropping it. ``item_income`` plus ``no_item_income`` is ``income``, and the report refuses
to print figures that do not.

**An item's income is the income leg's own attribution**, not a second copy of the line
total: ``posting_line_sources`` already says which entered line produced each cent of an
income posting, and the entered line's one-to-one profile says which item was sold.
Reading it that way is what makes a correction and a void come out right -- both append a
posting batch whose attributions point at the same entered lines, so the reversal carries
the item and the quantity back off the report with the sign its own posting has.

**A representative's sales are the rep the sale itself captured**, read from the exact
revision that posted the effect -- ``posting_batches.revision_id`` -- and never from the
customer's current sales representative. That distinction is the whole of this report: a
customer reassigned to a new rep today has not moved last year's commission, and a
correction that changes the rep reverses the old one's income at the old revision and
posts the new one's at the new. The rep is a fact of the sale, defaulted from the customer
when it was entered and fixed from then on, so the snapshot is the one authority and this
report adds no second one. Income from a document that captures no rep, and income no
document captured at all, is the one row called Unassigned.

**A customer's income is the dimension the posting line carries.** A sale writes its
customer onto every leg, so a job's income is the job's own and never its parent's; the
rows are ordered by hierarchy name, so a job still reads directly under the customer it
belongs to and each row names its parent outright. Income posted against a name from
another list -- a vendor named on a journal entry that credits income -- is not a
customer's, so it joins the unnamed row rather than pretending to be one.

**A vendor's expense is the line's own vendor where the line names one, and otherwise the
one vendor the whole effect names.** A bill writes its vendor onto every leg and needs no
fallback. A cheque does not: the payee is on the line that pays, and the expense lines
behind it carry only what someone typed in the Class and Customer columns, so without the
fallback every cheque ever written would be filed under no vendor at all. Where an effect
names two vendors, neither of them is the document's, and a line that named none stays
unattributed rather than being assigned to a guess.

Rows worth nothing are omitted, which cannot move a total: a sale fully credited back, a
voided bill, a customer whose invoice and credit memo cancel. They leave because they are
worth nothing, not because a status was filtered.

**A filter narrows the effects, and the report says what it left out.** A class filter is
on all four, because the class is the one dimension none of these reports has a row for
and it is what a company with departments, locations or job types splits its trading by.
A customer filter is on the item and representative cuts only: those two carry no customer
information at all, so "what did I sell this job, item by item" and "who sold to this job"
are questions their unfiltered form cannot answer. It is deliberately not on
``sales-by-customer``, whose rows already are the customer cut, and not on
``expenses-by-vendor``, because a bill posts its vendor onto every leg including the
expense ones -- the customer typed in a bill's Customer:Job column never reaches
``posting_lines.name_id``, so a job-filtered expense report would silently omit every bill.

Filtering is exact: a subclass is its own class and a job is its own customer, the same
rule the receivables filters already state, so a parent is never quietly read as its
children. Every filtered report carries ``scope``: the period total it would have shown
unfiltered -- which is the figure ``report profit-and-loss`` reports for the same dates --
and what the filter kept out, so the smaller number on a filtered report is stated rather
than merely smaller. ``totals`` plus ``scope.excluded`` is ``scope.period``, and each
report refuses to print figures that do not.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from bookflow.company import financial_statements as statements
from bookflow.company import ledger_reports as ledger
from bookflow.company import schema as c
from bookflow.company.ledger_reports import MoneyOutput, StrictModel, iso_date, money
# One home for what an unnamed row is called, so every party report says it the same way.
from bookflow.company.payable_reports import NO_VENDOR
from bookflow.company.receivable_reports import NO_CUSTOMER
from bookflow.core.errors import BookflowError
from bookflow.core.exact import (
    PERCENTAGE_SCALE, format_percentage_millionths, format_quantity_micro_units,
    round_ratio_half_even,
)

# What a row with no item and a row with no representative are called, in the same shape
# as the unnamed customer and vendor rows the other reports already use.
NO_ITEM = "No item"
NO_SALES_REP = "Unassigned"

# The profit-and-loss total the two sales reports are the breakdown of. It is a section
# name because that is what `financial_statements.SECTION_SQL` answers with and what
# `ProfitAndLossTotals` names its field, so the report and the total it reconciles to
# cannot drift onto different sets of accounts.
SALES_INCOME_SECTION = "income"


def _sections(*names):
    """The account-side predicate for these sections, over an `accounts a` alias."""
    return f"{statements.SECTION_SQL} IN ({', '.join(repr(name) for name in names)})"


INCOME_ACCOUNTS = _sections(SALES_INCOME_SECTION)
EXPENSE_ACCOUNTS = _sections(*statements.COST_SECTIONS)

# The income a sale posts is attributed to an entered line, and an entered line carries
# its item in a one-to-one profile table beside it: `sales_line_profiles` for an invoice
# or a sales receipt, `credit_line_profiles` for a credit memo. The set is read off the
# schema rather than typed here, because a third family would otherwise be silently
# absent from this report while its income still moved the total. A profile keyed on the
# entered line that carries an item, a base quantity and a line net is an item line,
# whatever it ends up being called.
ITEM_LINE_TABLES = tuple(sorted(
    name for name, table in c.metadata.tables.items()
    if list(table.primary_key.columns.keys()) == ["document_line_id"]
    and {"item_id", "base_quantity_microunits", "net_minor_units"} <= set(table.c.keys())))

_ITEM_LINES = "\n UNION ALL ".join(
    f"SELECT document_line_id, item_id, base_quantity_microunits FROM {name}"
    for name in ITEM_LINE_TABLES)

# The sales representative is captured in the revision's own profile snapshot, which is
# where every commercial header fact defaulted from the customer lives. The tables holding
# one are read off the schema for the same reason the item lines are: a revision-level
# profile of a sale to a customer is `sales_profiles` today and `credit_profiles` beside
# it, and a third would otherwise post income this report could not attribute. A snapshot
# that carries no representative at all yields SQL NULL from `json_extract`, so a table
# whose header has no such field simply lands in the unassigned row rather than failing.
COMMERCIAL_PROFILE_TABLES = tuple(sorted(
    name for name, table in c.metadata.tables.items()
    if list(table.primary_key.columns.keys()) == ["revision_id"]
    and {"transaction_id", "customer_id", "profile_snapshot"} <= set(table.c.keys())))

_CAPTURED_REPS = "\n UNION ALL ".join(
    f"SELECT revision_id, json_extract(profile_snapshot,'$.sales_rep.id') AS sales_rep_id FROM {name}"
    for name in COMMERCIAL_PROFILE_TABLES)


class PeriodInput(StrictModel):
    date_from: str = Field(min_length=10, max_length=10, description="Inclusive first accounting date, YYYY-MM-DD.")
    date_to: str = Field(min_length=10, max_length=10, description="Inclusive last accounting date, YYYY-MM-DD.")
    basis: Literal["accrual"] = "accrual"
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _dates = field_validator("date_from", "date_to")(iso_date)

    @model_validator(mode="after")
    def ordered(self):
        if self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class ClassFilterInput(PeriodInput):
    """A period narrowed to one class, which every one of these four reports offers."""

    class_id: str | None = Field(default=None, min_length=1, max_length=1000, description="Optional class ID or canonical full name, inactive classes included; a subclass is its own class and is not included with its parent. Omit for every class together with the lines entered under none.")


class ClassAndCustomerFilterInput(ClassFilterInput):
    """The same period also narrowed to one customer or job.

    Only the reports whose rows carry no customer at all take this: on a report whose
    rows already are the customer cut it would only hide rows a reader can see anyway.
    """

    customer: str | None = Field(default=None, min_length=1, max_length=1000, description="Optional customer or job ID or canonical full name; a job is its own customer and is not included with its parent. Omit for every customer together with the income no customer was named on.")


class SalesByCustomerInput(ClassFilterInput):
    pass


class SalesByItemInput(ClassAndCustomerFilterInput):
    pass


class SalesByRepInput(ClassAndCustomerFilterInput):
    pass


class ExpensesByVendorInput(ClassFilterInput):
    pass


class FilterSelection(StrictModel):
    """One record a filter named, as its own list names it now.

    ``filter`` is the input field that named it, so a reader and a browser form both know
    which control produced this selection without a second list of field names anywhere.
    An inactive record is reportable and says so, exactly as a retired account does on the
    statements: last year's trading happened under classes a company has since retired.
    """

    filter: Literal["class_id", "customer"]
    id: str
    label: str
    active: bool


class ReportScope(StrictModel):
    """What the filter admitted, and what it kept out of this report's own total.

    ``period`` is what this report shows with no filter at all: the income total, or the
    cost-of-goods-sold, expense and other-expense total, that ``report profit-and-loss``
    reports for the same two dates. ``excluded`` is what the filter kept out of it. The
    report's own total plus ``excluded`` is ``period``, which is how a filtered report
    still reconciles with the statement instead of quietly showing a smaller number.
    """

    filtered: bool
    selected: list[FilterSelection]
    period: MoneyOutput
    excluded: MoneyOutput


class SalesByCustomerTotals(StrictModel):
    income: MoneyOutput


class SalesByCustomerRow(StrictModel):
    customer_id: str | None
    current_customer_label: str | None
    current_customer_name: str | None
    display_customer_label: str
    parent_id: str | None
    parent_label: str | None
    job_status: str | None
    is_job: bool
    active: bool | None
    income: MoneyOutput
    percent_of_total: str | None
    percent_of_total_millionths: int | None


class SalesByItemTotals(StrictModel):
    income: MoneyOutput
    item_income: MoneyOutput
    no_item_income: MoneyOutput


class SalesByItemRow(StrictModel):
    item_id: str | None
    current_item_label: str | None
    current_item_name: str | None
    display_item_label: str
    item_type: str | None
    parent_id: str | None
    active: bool | None
    quantity: str
    quantity_microunits: int
    quantity_complete: bool
    income: MoneyOutput
    average_price: MoneyOutput | None
    percent_of_total: str | None
    percent_of_total_millionths: int | None


class SalesByRepTotals(StrictModel):
    income: MoneyOutput


class SalesByRepRow(StrictModel):
    sales_rep_id: str | None
    current_sales_rep_name: str | None
    current_sales_rep_initials: str | None
    display_sales_rep_label: str
    active: bool | None
    income: MoneyOutput
    percent_of_total: str | None
    percent_of_total_millionths: int | None


class ExpensesByVendorTotals(StrictModel):
    expense: MoneyOutput


class ExpensesByVendorRow(StrictModel):
    vendor_id: str | None
    current_vendor_name: str | None
    display_vendor_label: str
    active: bool | None
    expense: MoneyOutput
    percent_of_total: str | None
    percent_of_total_millionths: int | None


class SalesByCustomerOutput(ledger.Page):
    totals: SalesByCustomerTotals
    scope: ReportScope
    rows: list[SalesByCustomerRow]


class SalesByItemOutput(ledger.Page):
    totals: SalesByItemTotals
    scope: ReportScope
    rows: list[SalesByItemRow]


class SalesByRepOutput(ledger.Page):
    totals: SalesByRepTotals
    scope: ReportScope
    rows: list[SalesByRepRow]


class ExpensesByVendorOutput(ledger.Page):
    totals: ExpensesByVendorTotals
    scope: ReportScope
    rows: list[ExpensesByVendorRow]


# Never let SQLite do arithmetic on an aggregate: bookflow_sum_int returns lossless text
# and SQLite would coerce text arithmetic to REAL. Every subtraction and every product
# below is over stored INTEGER columns, and every second-level aggregation is
# bookflow_sum_int over its own lossless output.
#
# `direction` is the sign the posting itself carries: exactly one side of a posting line
# is positive (`ck_posting_line_one_side`), so a credit is +1 and a debit -1 on the
# income side. Attribution amounts are stored positive, and this is what gives a
# reversal's copied attribution the sign its reversed posting has.
# The two filter predicates, each written once and bound to NULL when its filter is not in
# force, so a filtered and an unfiltered report are the same query text and the period a
# filtered report reconciles against is that same text with the filters unbound. A report
# that offers a filter takes its predicate; one that does not never writes it, so no report
# carries a filter it does not mean.
#
# A class is the value on the line and nothing else: the class a document carries reaches
# its lines when they are posted, so there is no header arm here. A subclass is its own
# class, the same exactness the receivables customer filter states.
CLASS_SCOPE = "(:class_id IS NULL OR l.class_id=:class_id)"
# A posting line names exactly one party (`ck_ledger_party_pair`), so a customer filter
# is that party being a customer and being this one. A sale writes its customer onto every
# leg, which is what makes this cut of income complete.
CUSTOMER_SCOPE = "(:customer IS NULL OR (l.name_type='customer' AND l.name_id=:customer))"


def _income(*scopes):
    return f"""
WITH income_lines AS (
 SELECT l.id AS line_id, l.batch_id, l.name_type, l.name_id,
        l.credit_minor_units-l.debit_minor_units AS amount,
        CASE WHEN l.credit_minor_units>0 THEN 1 ELSE -1 END AS direction
 FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id
 JOIN accounts a ON a.id=l.account_id
 WHERE b.effective_date>=:date_from AND b.effective_date<=:date_to AND {INCOME_ACCOUNTS}
   AND {' AND '.join(scopes)}
)
"""


# The customer cut takes a class and nothing else: its rows already are the customer cut.
_INCOME = _income(CLASS_SCOPE)
# The item and representative cuts carry no customer on a row, so a customer filter there
# answers a question their unfiltered form cannot.
_INCOME_BY_PARTY = _income(CLASS_SCOPE, CUSTOMER_SCOPE)

# Income posted against a name from another list is nobody's sale, so only a customer
# keys a row; everything else falls into the one unnamed row and the total is unmoved.
_BY_CUSTOMER = _INCOME + """, keyed AS (
 SELECT CASE WHEN name_type='customer' THEN name_id END AS party, amount FROM income_lines
), grouped AS (
 SELECT party, bookflow_sum_int(amount) AS income FROM keyed GROUP BY party
), selected AS (
 SELECT g.party, g.income, c.id AS customer_id, c.full_name, c.name, c.full_name_key,
        c.parent_id, c.active, c.job_status, p.full_name AS parent_full_name
 FROM grouped g
 LEFT JOIN customers c ON c.id=g.party
 LEFT JOIN customers p ON p.id=c.parent_id
 WHERE g.income!='0'
) """

_ITEM_ATTRIBUTION = _INCOME_BY_PARTY + f""", item_lines AS (
 {_ITEM_LINES}
), attributed AS (
 SELECT e.line_id, p.item_id, e.direction*s.amount_minor_units AS amount,
        CASE WHEN p.base_quantity_microunits IS NULL THEN NULL
             ELSE e.direction*p.base_quantity_microunits END AS quantity
 FROM income_lines e
 JOIN posting_line_sources s ON s.posting_line_id=e.line_id
 JOIN item_lines p ON p.document_line_id=s.document_line_id
), unattributed AS (
 SELECT e.line_id, e.amount FROM income_lines e
 WHERE NOT EXISTS (
   SELECT 1 FROM posting_line_sources s JOIN item_lines p ON p.document_line_id=s.document_line_id
   WHERE s.posting_line_id=e.line_id)
) """

# One row per item, then the one row for income that reached no item at all. An entered
# line priced by allocation carries no quantity, so `unknown_quantity` counts those and
# the row says its quantity is incomplete rather than printing a short one as if it were
# the whole of it.
_BY_ITEM = _ITEM_ATTRIBUTION + """, by_item AS (
 SELECT item_id, bookflow_sum_int(amount) AS income,
        bookflow_sum_int(coalesce(quantity,0)) AS quantity,
        count(CASE WHEN quantity IS NULL THEN 1 END) AS unknown_quantity
 FROM attributed GROUP BY item_id
), combined AS (
 SELECT item_id, income, quantity, unknown_quantity FROM by_item
 UNION ALL
 SELECT NULL, coalesce(bookflow_sum_int(amount),'0'), '0', 0 FROM unattributed
), selected AS (
 SELECT b.item_id, b.income, b.quantity, b.unknown_quantity,
        i.full_name, i.name, i.full_name_key, i.type, i.parent_id, i.active
 FROM combined b LEFT JOIN items i ON i.id=b.item_id
 WHERE b.income!='0' OR b.quantity!='0'
) """

# The representative who made the sale, taken from the revision the effect was posted
# from rather than from the customer's list record. A reversal batch names the revision it
# reverses and a replacement names the new one, so a correction that moves a sale from one
# rep to another takes the income off the first and puts it on the second, each at its own
# revision, with no arm here for either case.
_BY_REP = _INCOME_BY_PARTY + f""", captured AS (
 {_CAPTURED_REPS}
), keyed AS (
 SELECT p.sales_rep_id AS party, e.amount
 FROM income_lines e
 JOIN posting_batches b ON b.id=e.batch_id
 LEFT JOIN captured p ON p.revision_id=b.revision_id
), grouped AS (
 SELECT party, bookflow_sum_int(amount) AS income FROM keyed GROUP BY party
), selected AS (
 SELECT g.party, g.income, r.id AS sales_rep_id, r.name, r.initials, r.name_key, r.active
 FROM grouped g LEFT JOIN sales_reps r ON r.id=g.party
 WHERE g.income!='0'
) """

# A cost effect is signed the way the statement reads it: debit minus credit, so spending
# is positive and a vendor credit is the negative that takes it back off.
#
# `effect_vendor` is the fallback, and it is deliberately a property of the whole posting
# batch rather than of a neighbouring line: a cheque's payee sits on the line that pays,
# a card charge's on the line that is charged, and a document family that puts it
# somewhere else again still names exactly one vendor on the effect it posts.
#
# The class filter narrows the cost lines and nothing else. `effect_vendor` deliberately
# still reads every vendor-named line of those effects, classed or not: a cheque's payee
# sits on the funding line, which is not a cost line and so is never in the filtered set,
# and dropping it would file every classed cheque under no vendor at all.
_EXPENSE = f"""
WITH expense_lines AS (
 SELECT l.id AS line_id, l.batch_id, l.name_type, l.name_id,
        l.debit_minor_units-l.credit_minor_units AS amount
 FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id
 JOIN accounts a ON a.id=l.account_id
 WHERE b.effective_date>=:date_from AND b.effective_date<=:date_to AND {EXPENSE_ACCOUNTS}
   AND {CLASS_SCOPE}
)
"""

_BY_VENDOR = _EXPENSE + """, effect_vendor AS (
 SELECT batch_id, min(name_id) AS vendor_id, count(DISTINCT name_id) AS named
 FROM posting_lines
 WHERE name_type='vendor' AND batch_id IN (SELECT batch_id FROM expense_lines)
 GROUP BY batch_id
), keyed AS (
 SELECT CASE WHEN e.name_type='vendor' THEN e.name_id
             WHEN v.named=1 THEN v.vendor_id END AS party, e.amount
 FROM expense_lines e LEFT JOIN effect_vendor v ON v.batch_id=e.batch_id
), grouped AS (
 SELECT party, bookflow_sum_int(amount) AS expense FROM keyed GROUP BY party
), selected AS (
 SELECT g.party, g.expense, v.id AS vendor_id, v.name, v.name_key, v.active
 FROM grouped g LEFT JOIN vendors v ON v.id=g.party
 WHERE g.expense!='0'
) """

# Customers read in hierarchy-name order, so a job follows the parent it is named under;
# items likewise; parties and items with no master record follow them all, by stable id.
_CUSTOMER_ORDER = "full_name_key IS NULL, full_name_key, coalesce(party,'')"
_ITEM_ORDER = "full_name_key IS NULL, full_name_key, coalesce(item_id,'')"
_VENDOR_ORDER = "name_key IS NULL, name_key, coalesce(party,'')"
# Sales representatives are a flat list named the same way vendors are.
_REP_ORDER = _VENDOR_ORDER


def _rows(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _percent(amount: int, total: int):
    """This row's share of the report's own total, as an exact percentage coefficient.

    A share of nothing is not zero, so a period whose total is nothing has no percentage
    column at all rather than a column of zeros that reads as "none of it". Rounding is
    the one integer rounding this codebase has, over integers throughout: no ratio of two
    amounts is ever carried as a float.
    """
    if total == 0:
        return None, None
    coefficient = round_ratio_half_even(amount * 100 * 10 ** PERCENTAGE_SCALE, total)
    return format_percentage_millionths(coefficient), coefficient


# Every filter one of these reports can declare, in the order a form shows them. A report
# takes the ones its own input model declares and binds the rest to NULL, so the filter
# names live here once instead of in each report.
FILTERS = ("class_id", "customer")


def _resolve(db, field: str, selector: str) -> str:
    """The stable ID a typed filter names, through the same resolver its list uses."""
    if field == "customer":
        from bookflow.company.parties import resolve_party
        return resolve_party(db, "customer", selector)["id"]
    from bookflow.company import list_service
    from bookflow.company.lists import get_list_definition
    return list_service.resolve_selector(db, c.classes, get_list_definition("class"), selector)["id"]


def _selected(inp, s):
    """This request's filters as stable IDs, resolved once on the first page.

    Page two takes them off the signed cursor rather than resolving the typed name again:
    renaming the class a report is filtered to must stale the continuation, which the
    watermark does, and never turn the next page into a record-not-found.
    """
    declared = [field for field in FILTERS if field in type(inp).model_fields]
    if inp.cursor is not None:
        carried = ledger._decode_cursor(inp.cursor, s.company).filter_ids or {}
        return {field: carried[field][0] for field in declared if carried.get(field)}
    return {field: _resolve(s.company, field, getattr(inp, field))
            for field in declared if getattr(inp, field) is not None}


def _bindings(inp, found):
    """The period, plus every filter bound -- to its ID where in force and NULL where not."""
    return {"date_from": inp.date_from, "date_to": inp.date_to,
            **{field: found.get(field) for field in FILTERS}}


def _total(raw, currency, query, params):
    """One exact sum of stored integers, converted in Python and never in SQLite."""
    total = 0
    for (amount,) in raw.execute(query, params):
        total += money(int(amount), currency).minor_units
    return total


def _scope(raw, currency, query, params, found, included):
    """What the whole period came to, and what this report's filter kept out of it.

    The period is the same query with every filter unbound, which is what makes it the
    figure the profit and loss reports for these dates rather than an agreement with it.
    An unfiltered report must show the whole period, and refuses rather than print a
    total its own unfiltered scan contradicts.
    """
    period = _total(raw, currency, query, {**params, **dict.fromkeys(FILTERS)})
    if not found and period != included:
        raise BookflowError("E_INTERNAL", message="An unfiltered report does not cover its own period")
    return ReportScope(
        filtered=bool(found), period=money(period, currency),
        excluded=money(period - included, currency),
        selected=[FilterSelection(filter=field, **record)
                  for field in FILTERS if field in found
                  for record in ledger.selected_records(raw, field, [found[field]])])


def sales_by_customer(inp: SalesByCustomerInput, s, *, principal_id=None) -> SalesByCustomerOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        found = _selected(inp, s)
        state, offset = ledger._state(s, inp, "sales-by-customer", principal_id, None,
            account_scoped=False, filter_ids={field: [value] for field, value in found.items()} or None)
        raw, currency = s.company.raw, state.metadata.currency
        params = _bindings(inp, found)
        # Two independent sums of the same money: every income line the filter admits, and
        # the grouped rows this report prints. A grouping that lost or duplicated a line
        # shows up here rather than as a percentage column that quietly does not add up.
        income = _INCOME + "SELECT amount FROM income_lines"
        grand = _total(raw, currency, income, params)
        printed = 0
        for row in raw.execute(_BY_CUSTOMER + "SELECT income FROM selected", params):
            printed += money(int(row[0]), currency).minor_units
        if printed != grand:
            raise BookflowError("E_INTERNAL", message="Income by customer does not add up to the period's income")
        page = _rows(raw.execute(_BY_CUSTOMER + f"""SELECT * FROM selected
            ORDER BY {_CUSTOMER_ORDER} LIMIT :limit OFFSET :offset""",
            {**params, "limit": inp.limit + 1, "offset": offset}))
        rows = []
        for row in page[:inp.limit]:
            amount = money(int(row["income"]), currency)
            text, coefficient = _percent(amount.minor_units, grand)
            rows.append(SalesByCustomerRow(
                customer_id=row["customer_id"], current_customer_label=row["full_name"],
                current_customer_name=row["name"],
                display_customer_label=NO_CUSTOMER if row["full_name"] is None else str(row["full_name"]),
                parent_id=row["parent_id"], parent_label=row["parent_full_name"],
                job_status=row["job_status"], is_job=bool(row["job_status"] not in (None, "none")),
                active=None if row["active"] is None else bool(row["active"]),
                income=amount, percent_of_total=text, percent_of_total_millionths=coefficient))
        return SalesByCustomerOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=SalesByCustomerTotals(income=money(grand, currency)),
            scope=_scope(raw, currency, income, params, found, grand),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))


def sales_by_item(inp: SalesByItemInput, s, *, principal_id=None) -> SalesByItemOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        found = _selected(inp, s)
        state, offset = ledger._state(s, inp, "sales-by-item", principal_id, None,
            account_scoped=False, filter_ids={field: [value] for field, value in found.items()} or None)
        raw, currency = s.company.raw, state.metadata.currency
        params = _bindings(inp, found)
        income = _INCOME_BY_PARTY + "SELECT amount FROM income_lines"
        grand = _total(raw, currency, income, params)
        # The reconciliation this report exists to keep: what reached an item, plus what
        # reached none, is the whole income the filter admits. A line attributed to an item
        # for only part of itself satisfies neither side and is caught here, because it is
        # counted once in `attributed` at its part and never in `unattributed` at all.
        attributed = _total(raw, currency, _ITEM_ATTRIBUTION + "SELECT amount FROM attributed", params)
        unattributed = _total(raw, currency, _ITEM_ATTRIBUTION + "SELECT amount FROM unattributed", params)
        if attributed + unattributed != grand:
            raise BookflowError("E_INTERNAL", message="Income by item and income with no item do not add up to the period's income")
        page = _rows(raw.execute(_BY_ITEM + f"""SELECT * FROM selected
            ORDER BY {_ITEM_ORDER} LIMIT :limit OFFSET :offset""",
            {**params, "limit": inp.limit + 1, "offset": offset}))
        rows = []
        for row in page[:inp.limit]:
            amount = money(int(row["income"]), currency)
            quantity, complete = int(row["quantity"]), not row["unknown_quantity"]
            text, coefficient = _percent(amount.minor_units, grand)
            # Income over quantity, rounded once to the cent for reading. It is derived
            # from the two exact figures printed beside it and nothing sums it, so it
            # is a display of this row rather than an amount the books carry.
            average = (money(round_ratio_half_even(amount.minor_units * 10 ** 6, quantity), currency)
                       if quantity and complete else None)
            rows.append(SalesByItemRow(
                item_id=row["item_id"], current_item_label=row["full_name"],
                current_item_name=row["name"],
                display_item_label=NO_ITEM if row["full_name"] is None else str(row["full_name"]),
                item_type=row["type"], parent_id=row["parent_id"],
                active=None if row["active"] is None else bool(row["active"]),
                quantity=format_quantity_micro_units(quantity), quantity_microunits=quantity,
                quantity_complete=complete, income=amount, average_price=average,
                percent_of_total=text, percent_of_total_millionths=coefficient))
        return SalesByItemOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=SalesByItemTotals(income=money(grand, currency),
                item_income=money(attributed, currency), no_item_income=money(unattributed, currency)),
            scope=_scope(raw, currency, income, params, found, grand),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))


def sales_by_rep(inp: SalesByRepInput, s, *, principal_id=None) -> SalesByRepOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        found = _selected(inp, s)
        state, offset = ledger._state(s, inp, "sales-by-rep", principal_id, None,
            account_scoped=False, filter_ids={field: [value] for field, value in found.items()} or None)
        raw, currency = s.company.raw, state.metadata.currency
        params = _bindings(inp, found)
        income = _INCOME_BY_PARTY + "SELECT amount FROM income_lines"
        grand = _total(raw, currency, income, params)
        printed = 0
        for row in raw.execute(_BY_REP + "SELECT income FROM selected", params):
            printed += money(int(row[0]), currency).minor_units
        if printed != grand:
            raise BookflowError("E_INTERNAL", message="Income by sales representative does not add up to the period's income")
        page = _rows(raw.execute(_BY_REP + f"""SELECT * FROM selected
            ORDER BY {_REP_ORDER} LIMIT :limit OFFSET :offset""",
            {**params, "limit": inp.limit + 1, "offset": offset}))
        rows = []
        for row in page[:inp.limit]:
            amount = money(int(row["income"]), currency)
            text, coefficient = _percent(amount.minor_units, grand)
            rows.append(SalesByRepRow(
                sales_rep_id=row["sales_rep_id"], current_sales_rep_name=row["name"],
                current_sales_rep_initials=row["initials"],
                display_sales_rep_label=NO_SALES_REP if row["name"] is None else str(row["name"]),
                active=None if row["active"] is None else bool(row["active"]),
                income=amount, percent_of_total=text, percent_of_total_millionths=coefficient))
        return SalesByRepOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=SalesByRepTotals(income=money(grand, currency)),
            scope=_scope(raw, currency, income, params, found, grand),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))


def expenses_by_vendor(inp: ExpensesByVendorInput, s, *, principal_id=None) -> ExpensesByVendorOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        found = _selected(inp, s)
        state, offset = ledger._state(s, inp, "expenses-by-vendor", principal_id, None,
            account_scoped=False, filter_ids={field: [value] for field, value in found.items()} or None)
        raw, currency = s.company.raw, state.metadata.currency
        params = _bindings(inp, found)
        expense = _EXPENSE + "SELECT amount FROM expense_lines"
        grand = _total(raw, currency, expense, params)
        printed = 0
        for row in raw.execute(_BY_VENDOR + "SELECT expense FROM selected", params):
            printed += money(int(row[0]), currency).minor_units
        if printed != grand:
            raise BookflowError("E_INTERNAL", message="Expense by vendor does not add up to the period's expense")
        page = _rows(raw.execute(_BY_VENDOR + f"""SELECT * FROM selected
            ORDER BY {_VENDOR_ORDER} LIMIT :limit OFFSET :offset""",
            {**params, "limit": inp.limit + 1, "offset": offset}))
        rows = []
        for row in page[:inp.limit]:
            amount = money(int(row["expense"]), currency)
            text, coefficient = _percent(amount.minor_units, grand)
            rows.append(ExpensesByVendorRow(
                vendor_id=row["vendor_id"], current_vendor_name=row["name"],
                display_vendor_label=NO_VENDOR if row["name"] is None else str(row["name"]),
                active=None if row["active"] is None else bool(row["active"]),
                expense=amount, percent_of_total=text, percent_of_total_millionths=coefficient))
        return ExpensesByVendorOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=ExpensesByVendorTotals(expense=money(grand, currency)),
            scope=_scope(raw, currency, expense, params, found, grand),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))
