"""Billable work recorded against a customer or job and not yet invoiced.

The report a business runs before it bills. Its rows are work lines, because a work
line is where billable work is recorded against a job in this system, and the state
of each one -- unbilled, partly billed, fully billed -- is the state its own billing
window shows, read through ``billing_queries`` rather than derived a second time
here. Two reports that compute what is left to bill from the same allocations by
two routes would agree until the first time an allocation rule changed under one of
them; this one has no second route to drift.

What is listed is what could be billed today. A fully billed line and a line marked
not billable are omitted; so is a source that cannot be billed at all -- an estimate
nobody has accepted, an estimate whose work order has taken the work over, a
cancelled document, and a deactivated one, which has to be reactivated before it can
be rebilled. That is the same eligibility the billing command applies, taken from
the same expression.

Each customer or job carries a subtotal row of its own, and the report's totals cover
every row the filter selects rather than the page being read.
"""
from __future__ import annotations

from typing import Literal, get_args

from pydantic import Field, field_validator

from bookflow.company import billing_queries as query
from bookflow.company import ledger_reports as ledger
from bookflow.company import schema as c
from bookflow.company import work
from bookflow.company.billing_outputs import BillingLineState
from bookflow.company.ledger_reports import MoneyOutput, StrictModel, iso_date, money
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units

# A row is listed unless the work behind it is finished or was never chargeable.
# Derived from the declared billing states so a new state is listed rather than
# silently filed under "nothing to do".
SETTLED_STATES = frozenset({"billed", "nonbillable"})
LISTED_STATES = frozenset(get_args(BillingLineState)) - SETTLED_STATES

NO_CUSTOMER = "No name"


class UnbilledCostsInput(StrictModel):
    as_of: str = Field(min_length=10, max_length=10, description="Inclusive accounting as-of date, YYYY-MM-DD; work dated after it is not yet recorded.")
    customer: str | None = Field(default=None, min_length=1, max_length=1000, description="Optional customer or job ID or canonical full name; a job is its own customer and is not included with its parent.")
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _as_of = field_validator("as_of")(iso_date)

    @property
    def date_to(self) -> str:
        """The report period's single inclusive bound, named as every report names it."""
        return self.as_of


class UnbilledCostsTotals(StrictModel):
    billed: MoneyOutput
    remaining: MoneyOutput


class UnbilledCostRow(StrictModel):
    kind: Literal["line", "subtotal"]
    customer_id: str | None
    current_customer_label: str | None
    current_customer_name: str | None
    display_customer_label: str
    parent_id: str | None
    active: bool | None
    source_id: str | None = None
    source_kind: str | None = None
    source_number: str | None = None
    source_date: str | None = None
    line_id: str | None = None
    root_document_id: str | None = None
    root_line_id: str | None = None
    item_id: str | None = None
    item_label: str | None = None
    account_id: str | None = None
    account_label: str | None = None
    description: str | None = None
    state: BillingLineState | None = None
    quantity: str | None = None
    billed_quantity: str | None = None
    remaining_quantity: str | None = None
    billed: MoneyOutput
    remaining: MoneyOutput


class UnbilledCostsOutput(ledger.Page):
    totals: UnbilledCostsTotals
    rows: list[UnbilledCostRow]


def _label(full_name):
    return NO_CUSTOMER if full_name is None else str(full_name)


def _sources(s, as_of, customer_id):
    """The work documents whose remaining work is billable from them today.

    A document that has handed its work to another -- an estimate with a work order
    -- is skipped here and read through the owner instead, so one line's remaining
    scope is never counted twice.
    """
    documents = work.rows(s, c.work_documents, c.work_documents.c.kind.in_(query.BILLING_KINDS),
                          order=c.work_documents.c.id)
    for header in documents:
        if not query.billable_source(header):
            continue
        if query.current_owner(s, header)["id"] != header["id"]:
            continue
        revision = work.revision(s, header)
        if revision["date"] > as_of:
            continue
        if customer_id is not None and revision["customer_id"] != customer_id:
            continue
        yield header, revision


def _lines(s, header, revision):
    """Each saved line of this revision with the billing state its own window shows."""
    from bookflow.company import billing_allocations as alloc, billing_math as math, tax_policy
    identities = query.root_identities(s, header)
    policy = tax_policy.effective(work.facts(revision).profile)
    for row in work.saved_lines(s, revision):
        facts = work.line_facts(row)
        identity = identities[row["line_id"]]
        root = (identity["root_document_id"], identity["root_line_id"])
        free_length, free_net, _spans = query.free_amounts(s, root, facts, policy)
        used = alloc.active_totals(s, root)
        remaining = free_net if facts.billable else 0
        state = query.line_state(free_length, used["count"], facts.billable, remaining)
        if state in SETTLED_STATES:
            continue
        if state not in LISTED_STATES:
            raise BookflowError("E_INTERNAL", message="A billing state has no disposition in the unbilled-cost report")
        billed_quantity, remaining_quantity, _percent = query.scope_fractions(facts, free_length)
        yield dict(line_id=row["line_id"], root_document_id=root[0], root_line_id=root[1],
                   item_id=facts.item_id, description=facts.description, state=state,
                   position=row["position"],
                   quantity=format_quantity_micro_units(facts.quantity_microunits),
                   billed_quantity=math.format_fraction(billed_quantity),
                   remaining_quantity=math.format_fraction(remaining_quantity),
                   billed=used["net"], remaining=remaining)


def _customers(raw, ids):
    found = {}
    ordered = sorted(ids)
    for start in range(0, len(ordered), 64):
        chunk = ordered[start:start + 64]
        placeholders = ",".join("?" * len(chunk))
        for row in raw.execute(f"""SELECT id, full_name, name, full_name_key, parent_id, active
            FROM customers WHERE id IN ({placeholders})""", chunk):
            found[row[0]] = dict(zip(("id", "full_name", "name", "full_name_key", "parent_id", "active"), row))
    return found


def _items(raw, ids):
    found = {}
    ordered = sorted(ids)
    for start in range(0, len(ordered), 64):
        chunk = ordered[start:start + 64]
        placeholders = ",".join("?" * len(chunk))
        for row in raw.execute(f"""SELECT i.id, i.full_name, i.income_account_id, a.full_name
            FROM items i LEFT JOIN accounts a ON a.id = i.income_account_id
            WHERE i.id IN ({placeholders})""", chunk):
            found[row[0]] = dict(zip(("id", "item_label", "account_id", "account_label"), row))
    return found


def unbilled_costs(inp: UnbilledCostsInput, s, *, principal_id=None) -> UnbilledCostsOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        customer_id = None
        if inp.cursor is not None:
            # Keep the stable ID resolved for the first page: renaming the selected
            # customer must stale the continuation rather than turn it into a
            # record-not-found on page two.
            customer_id = ledger._decode_cursor(inp.cursor, s.company).account_id
        elif inp.customer is not None:
            from bookflow.company.parties import resolve_party
            customer_id = resolve_party(s.company, "customer", inp.customer)["id"]
        state, offset = ledger._state(s, inp, "unbilled-costs", principal_id, customer_id, account_scoped=False)
        raw, currency = s.company.raw, state.metadata.currency

        collected = []
        for header, revision in _sources(s, inp.as_of, customer_id):
            for line in _lines(s, header, revision):
                collected.append({**line, "customer_id": revision["customer_id"],
                                  "source_id": header["id"], "source_kind": header["kind"],
                                  "source_number": revision["number"], "source_date": revision["date"]})
        customers = _customers(raw, {line["customer_id"] for line in collected})
        items = _items(raw, {line["item_id"] for line in collected if line["item_id"]})

        def sort_key(line):
            customer = customers.get(line["customer_id"]) or {}
            return (customer.get("full_name_key") is None, customer.get("full_name_key") or "",
                    line["customer_id"] or "", line["source_date"], line["source_number"],
                    line["source_id"], line["position"], line["line_id"])

        collected.sort(key=sort_key)

        # Subtotal rows are built with the lines they cover, before any page slice, so
        # a subtotal is what that customer owes in the whole report rather than on
        # whichever page it happens to land.
        rows, totals = [], dict(billed=0, remaining=0)
        current, group = None, []

        def flush():
            if not group:
                return
            customer = customers.get(current) or {}
            rows.append(UnbilledCostRow(kind="subtotal", customer_id=current,
                current_customer_label=customer.get("full_name"), current_customer_name=customer.get("name"),
                display_customer_label=_label(customer.get("full_name")), parent_id=customer.get("parent_id"),
                active=None if customer.get("active") is None else bool(customer["active"]),
                billed=money(sum(line["billed"] for line in group), currency),
                remaining=money(sum(line["remaining"] for line in group), currency)))

        for line in collected:
            if line["customer_id"] != current and group:
                flush()
                group = []
            current = line["customer_id"]
            customer = customers.get(current) or {}
            item = items.get(line["item_id"]) or {}
            totals["billed"] += money(line["billed"], currency).minor_units
            totals["remaining"] += money(line["remaining"], currency).minor_units
            rows.append(UnbilledCostRow(kind="line", customer_id=current,
                current_customer_label=customer.get("full_name"), current_customer_name=customer.get("name"),
                display_customer_label=_label(customer.get("full_name")), parent_id=customer.get("parent_id"),
                active=None if customer.get("active") is None else bool(customer["active"]),
                source_id=line["source_id"], source_kind=line["source_kind"],
                source_number=line["source_number"], source_date=line["source_date"],
                line_id=line["line_id"], root_document_id=line["root_document_id"],
                root_line_id=line["root_line_id"], item_id=line["item_id"],
                item_label=item.get("item_label"), account_id=item.get("account_id"),
                account_label=item.get("account_label"), description=line["description"],
                state=line["state"], quantity=line["quantity"],
                billed_quantity=line["billed_quantity"], remaining_quantity=line["remaining_quantity"],
                billed=money(line["billed"], currency), remaining=money(line["remaining"], currency)))
            group.append(line)
        flush()

        if sum(row.remaining.minor_units for row in rows if row.kind == "subtotal") != totals["remaining"]:
            raise BookflowError("E_INTERNAL", message="Unbilled subtotals do not add to the report total")
        page = rows[offset:offset + inp.limit + 1]
        return UnbilledCostsOutput(metadata=state.metadata, rows=page[:inp.limit], count=len(page[:inp.limit]),
            totals=UnbilledCostsTotals(**{name: money(value, currency) for name, value in totals.items()}),
            next_cursor=ledger._continuation(state, offset, len(page[:inp.limit]),
                                             len(page) > inp.limit, s.company))
