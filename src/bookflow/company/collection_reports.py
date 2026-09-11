"""Who is overdue, what they owe, and how to reach them.

The A/R aging summary answers what is owed. This report answers the question a
person actually has in front of them on a Monday morning: which customers are late,
which invoices made them late, and whose telephone number to dial. So it is the
aging report restricted to customers with something past due, with each one's
overdue invoices listed beneath and the contacts recorded against them attached.

The aging columns here are ``ar_aging``'s columns. Not the same shape computed
again -- the same expression, read from ``receivable_reports``, because two reports
that disagreed about what one customer owes would be worse than either of them
alone. What differs is only which customers are printed and what is printed beside
them. The totals are therefore the overdue part of receivables and are not Accounts
Receivable, which is what the aging report reports in full.

Contacts follow the customer's own contact rule: a job that inherits its contacts
is chased through the customer it is named under, which is where those contacts are
recorded, rather than showing as having nobody to call.
"""
from __future__ import annotations

from typing import Literal

import sqlalchemy as sa
from pydantic import Field, field_validator

from bookflow.company import ledger_reports as ledger
from bookflow.company import receivable_reports as receivable
from bookflow.company import schema as c
from bookflow.company.aging import BUCKETS, COLUMNS as _COLUMNS, bucket_edges, days_past_due
from bookflow.company.ledger_reports import MoneyOutput, StrictModel, iso_date, money
from bookflow.company.receivable_reports import ArAgingTotals
from bookflow.core.errors import BookflowError

# A customer is being chased when something sits at or past the chosen column. The
# current column is not a chase, so it is never a starting point.
OVERDUE_BUCKETS = BUCKETS[1:]

# The contact fields a person needs to make the call, in the order they are read.
CONTACT_FIELDS = ("role", "display_name", "salutation", "first_name", "last_name", "job_title",
                  "work_phone", "home_phone", "mobile_phone", "other_phone",
                  "work_fax", "home_fax", "primary_email", "secondary_email", "website")


class CollectionsInput(StrictModel):
    as_of: str = Field(min_length=10, max_length=10, description="Inclusive accounting as-of date, YYYY-MM-DD; receivables are aged against it.")
    basis: Literal["accrual"] = "accrual"
    minimum_bucket: Literal["days_1_30", "days_31_60", "days_61_90", "over_90"] = Field(default="days_1_30", description="The oldest-first aging column a customer must reach before it is chased; anything in that column or past it counts.")
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _as_of = field_validator("as_of")(iso_date)

    @property
    def date_to(self) -> str:
        """The report period's single inclusive bound, named as every report names it."""
        return self.as_of


class ContactPointOutput(StrictModel):
    contact_point_id: str
    kind: str
    custom_label: str | None
    value: str


class CustomerContactOutput(StrictModel):
    contact_id: str
    owner_customer_id: str
    inherited: bool
    role: str
    display_name: str | None
    salutation: str | None
    first_name: str | None
    last_name: str | None
    job_title: str | None
    work_phone: str | None
    home_phone: str | None
    mobile_phone: str | None
    other_phone: str | None
    work_fax: str | None
    home_fax: str | None
    primary_email: str | None
    secondary_email: str | None
    website: str | None
    points: list[ContactPointOutput]


class CollectionRow(StrictModel):
    kind: Literal["customer", "invoice"]
    customer_id: str | None
    current_customer_label: str | None
    current_customer_name: str | None
    display_customer_label: str
    parent_id: str | None
    active: bool | None
    current: MoneyOutput
    days_1_30: MoneyOutput
    days_31_60: MoneyOutput
    days_61_90: MoneyOutput
    over_90: MoneyOutput
    total: MoneyOutput
    overdue: MoneyOutput
    contacts: list[CustomerContactOutput] = Field(default_factory=list)
    billing_address: str | None = None
    transaction_id: str | None = None
    number: str | None = None
    date: str | None = None
    due_date: str | None = None
    days_past_due: int | None = None
    aging_bucket: Literal["current", "days_1_30", "days_31_60", "days_61_90", "over_90"] | None = None
    balance: MoneyOutput | None = None


class CollectionsTotals(ArAgingTotals):
    overdue: MoneyOutput


class CollectionsOutput(ledger.Page):
    totals: CollectionsTotals
    customer_count: int = Field(ge=0)
    rows: list[CollectionRow]


_ADDRESS_FIELDS = ("billing_line1", "billing_line2", "billing_city", "billing_state",
                   "billing_postal_code", "billing_country")


def _address(row):
    parts = [str(row[field]) for field in _ADDRESS_FIELDS if row[field]]
    return ", ".join(parts) or None


def _contact_owners(s, ids):
    """Each customer's effective contact owner: itself, or the ancestor it inherits from."""
    from bookflow.commands import party_cmds as party
    if not ids:
        return {}
    table = c.customers
    owner = party._customer_collection_owner("contact_mode")
    rows = s.company.conn.execute(
        sa.select(table.c.id, owner, *(table.c[field] for field in _ADDRESS_FIELDS))
        .where(table.c.id.in_(sorted(ids)))).all()
    return {row[0]: dict(owner=row[1], **dict(zip(_ADDRESS_FIELDS, row[2:]))) for row in rows}


def _contacts(raw, owner_ids):
    """Active contacts and their active contact points, by the customer that owns them."""
    if not owner_ids:
        return {}
    found: dict[str, list[dict]] = {}
    by_id: dict[str, dict] = {}
    ordered = sorted(owner_ids)
    for start in range(0, len(ordered), 64):
        chunk = ordered[start:start + 64]
        marks = ",".join("?" * len(chunk))
        for row in raw.execute(f"""SELECT customer_id, id, {', '.join(CONTACT_FIELDS)}
            FROM customer_contacts WHERE active=1 AND customer_id IN ({marks})
            ORDER BY customer_id, position, id""", chunk):
            contact = dict(zip(("customer_id", "contact_id", *CONTACT_FIELDS), row))
            contact["points"] = []
            found.setdefault(contact["customer_id"], []).append(contact)
            by_id[contact["contact_id"]] = contact
        for row in raw.execute(f"""SELECT p.contact_id, p.id, p.kind, p.custom_label, p.value
            FROM customer_contact_points p JOIN customer_contacts k ON k.id=p.contact_id
            WHERE p.active=1 AND k.active=1 AND k.customer_id IN ({marks})
            ORDER BY p.contact_id, p.position, p.id""", chunk):
            contact = by_id.get(row[0])
            if contact is not None:
                contact["points"].append(ContactPointOutput(contact_point_id=row[1], kind=row[2],
                                                            custom_label=row[3], value=row[4]))
    return found


def collections(inp: CollectionsInput, s, *, principal_id=None) -> CollectionsOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        state, offset = ledger._state(s, inp, "collections", principal_id, None)
        raw, currency = s.company.raw, state.metadata.currency
        params = {"as_of": inp.as_of, "customer": None, "past_due_only": 1, **bucket_edges(inp.as_of)}
        chased = _COLUMNS[BUCKETS.index(inp.minimum_bucket):len(BUCKETS)]

        # Every customer's aging, from the aging report's own expression, then only
        # the ones with something at or past the chosen column.
        selected = []
        for row in receivable._rows(raw.execute(receivable.AGING_ROWS, params)):
            columns = {name: money(int(row[column]), currency).minor_units
                       for name, column in zip((*BUCKETS, "total"), _COLUMNS)}
            if sum(columns[name] for name in BUCKETS) != columns["total"]:
                raise BookflowError("E_INTERNAL", message="Aging columns do not sum to the aging total")
            # What is actually being chased: the chosen column and everything past
            # it, netted. A customer whose aged columns net to nothing owes nothing
            # overdue -- an unapplied credit aged on its own date can do that -- and
            # is not someone to telephone.
            overdue = sum(money(int(row[column]), currency).minor_units for column in chased)
            if overdue <= 0:
                continue
            selected.append((row, columns, overdue))

        parties = {row["party"] for row, _columns, _overdue in selected if row["party"]}
        owners = _contact_owners(s, parties)
        contacts = _contacts(raw, {value["owner"] for value in owners.values() if value["owner"]})

        # The overdue invoices behind those balances, read from the open-invoice
        # report's own rows so an invoice's column here is the column it has there.
        invoices: dict[str, list[dict]] = {}
        for row in receivable._rows(raw.execute(receivable.OPEN_INVOICE_ROWS, params)):
            invoices.setdefault(row["party"], []).append(row)

        totals = dict.fromkeys((*BUCKETS, "total", "overdue"), 0)
        rows = []
        for row, columns, overdue in selected:
            for name in (*BUCKETS, "total"):
                totals[name] += columns[name]
            totals["overdue"] += overdue
            party = row["party"]
            owner = owners.get(party) or {}
            attached = contacts.get(owner.get("owner")) if owner.get("owner") else None
            rows.append(CollectionRow(kind="customer", customer_id=row["customer_id"],
                current_customer_label=row["full_name"], current_customer_name=row["name"],
                display_customer_label=receivable._label(row["full_name"]),
                parent_id=row["parent_id"],
                active=None if row["active"] is None else bool(row["active"]),
                overdue=money(overdue, currency),
                billing_address=_address(owner) if owner else None,
                contacts=[CustomerContactOutput(owner_customer_id=owner["owner"],
                    inherited=owner["owner"] != party,
                    **{key: value for key, value in contact.items() if key != "customer_id"})
                    for contact in (attached or [])],
                **{name: money(columns[name], currency) for name in (*BUCKETS, "total")}))
            for invoice in invoices.get(party, []):
                bucket = BUCKETS[invoice["bucket"]]
                if bucket not in OVERDUE_BUCKETS:
                    continue
                zero = money(0, currency)
                rows.append(CollectionRow(kind="invoice", customer_id=invoice["customer_id"],
                    current_customer_label=invoice["full_name"], current_customer_name=invoice["name"],
                    display_customer_label=receivable._label(invoice["full_name"]),
                    parent_id=invoice["parent_id"],
                    active=None, overdue=money(int(invoice["net"]), currency),
                    transaction_id=invoice["tx"], number=invoice["document_number"],
                    date=invoice["document_date"], due_date=invoice["aging_date"],
                    days_past_due=days_past_due(inp.as_of, invoice["aging_date"]),
                    aging_bucket=bucket, balance=money(int(invoice["net"]), currency),
                    **{name: (money(int(invoice["net"]), currency) if name == bucket else zero)
                       for name in BUCKETS}, total=money(int(invoice["net"]), currency)))

        page = rows[offset:offset + inp.limit + 1]
        shown = page[:inp.limit]
        return CollectionsOutput(metadata=state.metadata, rows=shown, count=len(shown),
            customer_count=len(selected),
            totals=CollectionsTotals(**{name: money(totals[name], currency)
                                        for name in (*BUCKETS, "total", "overdue")}),
            next_cursor=ledger._continuation(state, offset, len(shown), len(page) > inp.limit, s.company))
