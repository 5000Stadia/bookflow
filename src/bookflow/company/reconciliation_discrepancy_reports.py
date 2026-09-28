"""The reconciliation discrepancy report: what changed in an account's finished reconciliations.

The anchor's Reconciliation Discrepancy report lists, for one bank or credit card account, the
reconciled transactions that were changed after the reconciliation that cleared them, grouped
under that reconciliation, with the amount it was reconciled at, what it counts for now and the
effect of the change. This is that report, with one more line per reconciliation the anchor
leaves the reader to work out: the reconciliation itself, its statement ending balance against
its cleared balance now, and the difference between them.

**A reconciliation row** is one finished statement of the account dated on or before `as_of`,
plus the opening balance the account adopted. `reconciled` is the statement's ending balance
(the adopted opening balance); `current` is its cleared balance as the movements it cleared
stand now; `difference` is `current - reconciled`, zero while it still ties. A later statement
inherits the difference of any earlier one through its beginning balance, so it shows the same
difference with no change rows of its own.

**A change row** is one transaction the reconciliation above it cleared whose figure on that
reconciliation is no longer what it was reconciled at: `reconciled` is what it was cleared at,
`current` what it counts for now (zero when it was voided, moved to another account or re-dated
after the statement), `difference` the effect of the change. `type_of_change` says which:
`amount`, `date`, `account` or `voided`. The rows under a reconciliation add up to what
changed on it; with no earlier change carried in, that is its difference.

Money is in the statement's own sign: a bank balance, and on a card what is owed. The figures
are the same arithmetic `reconciliation_changes` warns with, so a warning and this report
always agree.
"""
from __future__ import annotations

from typing import Literal

import sqlalchemy as sa
from pydantic import Field, field_validator

from bookflow.company import ledger_reports as ledger
from bookflow.company import reconciliation_changes as changes
from bookflow.company import schema as c
from bookflow.company.ledger_reports import (
    MoneyOutKind, MoneyOutput, StrictModel, TransactionType, iso_date, money,
)
from bookflow.core.errors import BookflowError

REPORT = "reconciliation-discrepancy"


class ReconciliationDiscrepancyInput(StrictModel):
    account: str = Field(min_length=1, max_length=1000, description="Bank or credit card account ID or canonical full name; includes inactive accounts.")
    as_of: str = Field(min_length=10, max_length=10, description="Inclusive statement date, YYYY-MM-DD; reconciliations of statements dated after it are left out.")
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _as_of = field_validator("as_of")(iso_date)

    @property
    def date_to(self) -> str:
        """The report period's single inclusive bound, named as every report names it."""
        return self.as_of


class ReconciliationDiscrepancyTotals(StrictModel):
    reconciliations: int = Field(ge=0)
    out_of_balance: int = Field(ge=0, description="Reconciliations whose cleared balance no longer equals their statement.")
    changes: int = Field(ge=0, description="Reconciled transactions changed since they were reconciled.")


class ReconciliationDiscrepancyRow(StrictModel):
    kind: Literal["reconciliation", "change"]
    reconciliation: Literal["statement", "opening"]
    reconciliation_id: str
    statement_date: str
    transaction_id: str | None
    transaction_type: TransactionType | None
    money_out_kind: MoneyOutKind | None
    number: str | None
    date: str | None
    memo: str | None
    type_of_change: Literal["amount", "date", "account", "voided"] | None
    reconciled: MoneyOutput
    current: MoneyOutput
    difference: MoneyOutput


class ReconciliationDiscrepancyOutput(ledger.Page):
    account_id: str
    display_account_label: str
    totals: ReconciliationDiscrepancyTotals
    rows: list[ReconciliationDiscrepancyRow]


def _statement_account(s, inp):
    if inp.cursor is not None:
        account_id = ledger._decode_cursor(inp.cursor, s.company).account_id
        return dict(s.company.conn.execute(sa.select(c.accounts).where(c.accounts.c.id == account_id)).mappings().one())
    from bookflow.company.accounts import resolve_account
    account = resolve_account(s.company, inp.account)
    if account["type"] not in ("bank", "credit_card"):
        raise BookflowError("E_VALIDATION", message=(
            f'A reconciliation is of a bank or credit card account; "{account["name"]}" is a '
            f'{account["type"].replace("_", " ")} account.'),
            details={"fields": [{"field": "account", "problem": "must name a bank or credit card account"}]})
    return account


def _owners(s, account_id, as_of):
    """The account's opening and its active statements up to as_of, oldest first."""
    found = []
    state = s.company.conn.execute(sa.select(c.reconciliation_accounts).where(
        c.reconciliation_accounts.c.account_id == account_id)).mappings().first()
    if state is not None and state["opening_id"]:
        opening = s.company.conn.execute(sa.select(c.reconciliation_openings).where(
            c.reconciliation_openings.c.id == state["opening_id"])).mappings().one()
        if opening["opening_date"] <= as_of:
            found.append(dict(kind="opening", id=opening["id"], account_id=account_id,
                              cutoff=opening["opening_date"], reconciled=opening["balance"],
                              currency=opening["currency"]))
    active, certificates = c.reconciliation_active_certificates, c.reconciliation_certificates
    for row in s.company.conn.execute(sa.select(certificates).join(
            active, active.c.certificate_id == certificates.c.id).where(
            active.c.account_id == account_id, active.c.statement_date <= as_of)
            .order_by(active.c.statement_date, certificates.c.id)).mappings():
        found.append(dict(kind="statement", id=row["id"], account_id=account_id, cutoff=row["statement_date"],
                          reconciled=row["ending_balance"], currency=row["currency"]))
    return found


def _changed(s, owner):
    """(transaction, reconciled, current, type of change) for each changed movement it cleared."""
    k, m, claims, heads = (c.reconciliation_keys, c.reconciliation_current_members,
                           c.reconciliation_claims, c.reconciliation_effect_heads)
    field = claims.c.certificate_id if owner["kind"] == "statement" else claims.c.opening_id
    held = [dict(row) for row in s.company.conn.execute(
        sa.select(k.c.transaction_id, claims.c.version_id.label("claimed"), heads.c.version_id.label("head"))
        .join(m, m.c.key_id == k.c.id).join(claims, claims.c.id == m.c.claim_id)
        .outerjoin(heads, heads.c.key_id == k.c.id).where(field == owner["id"])).mappings()]
    versions = changes._versions(s, [r["claimed"] for r in held] + [r["head"] for r in held])
    by_transaction = {}
    for row in held:
        claimed, head = versions.get(row["claimed"]), versions.get(row["head"])
        was = changes.amount(claimed, owner["account_id"], owner["cutoff"])
        now = changes.amount(head, owner["account_id"], owner["cutoff"])
        entry = by_transaction.setdefault(row["transaction_id"], dict(reconciled=0, current=0, kind=None))
        entry["reconciled"] += was
        entry["current"] += now
        if was != now and entry["kind"] is None:
            entry["kind"] = ("voided" if head is None or not head["active"] else
                             "account" if head["account_id"] != owner["account_id"] else
                             "date" if head["effective_date"] > owner["cutoff"] else "amount")
    return {t: e for t, e in by_transaction.items() if e["reconciled"] != e["current"]}


def _documents(s, identifiers):
    if not identifiers:
        return {}
    t, r, marker = c.transactions, c.transaction_revisions, c.money_out_documents
    return {row["id"]: dict(row) for row in s.company.conn.execute(
        sa.select(t.c.id, t.c.type, r.c.number, r.c.date, r.c.memo, marker.c.kind.label("money_out_kind"))
        .join(r, r.c.id == t.c.current_revision_id)
        .outerjoin(marker, sa.and_(marker.c.transaction_id == t.c.id, marker.c.type == t.c.type))
        .where(t.c.id.in_(sorted(identifiers)))).mappings()}


def reconciliation_discrepancy(inp: ReconciliationDiscrepancyInput, s, *, principal_id=None) -> ReconciliationDiscrepancyOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        account = _statement_account(s, inp)
        state, offset = ledger._state(s, inp, REPORT, principal_id, account["id"])
        rows, out_of_balance, changed_count = [], 0, 0
        owners = _owners(s, account["id"], inp.as_of)
        for owner in owners:
            now = changes.cleared(s, owner)
            if now != owner["reconciled"]:
                out_of_balance += 1
            common = dict(reconciliation=owner["kind"], reconciliation_id=owner["id"], statement_date=owner["cutoff"])
            rows.append(ReconciliationDiscrepancyRow(
                kind="reconciliation", transaction_id=None, transaction_type=None, money_out_kind=None,
                number=None, date=None, memo=None, type_of_change=None,
                reconciled=money(owner["reconciled"], owner["currency"]), current=money(now, owner["currency"]),
                difference=money(now - owner["reconciled"], owner["currency"]), **common))
            moved = _changed(s, owner)
            documents = _documents(s, moved)
            for identity in sorted(moved, key=lambda i: (documents[i]["date"], i)):
                entry, document = moved[identity], documents[identity]
                changed_count += 1
                rows.append(ReconciliationDiscrepancyRow(
                    kind="change", transaction_id=identity, transaction_type=document["type"],
                    money_out_kind=document["money_out_kind"], number=document["number"],
                    date=document["date"], memo=document["memo"], type_of_change=entry["kind"],
                    reconciled=money(entry["reconciled"], owner["currency"]),
                    current=money(entry["current"], owner["currency"]),
                    difference=money(entry["current"] - entry["reconciled"], owner["currency"]), **common))
        page = rows[offset:offset + inp.limit + 1]
        shown = page[:inp.limit]
        return ReconciliationDiscrepancyOutput(
            metadata=state.metadata, account_id=account["id"], display_account_label=account["full_name"],
            totals=ReconciliationDiscrepancyTotals(reconciliations=len(owners), out_of_balance=out_of_balance,
                                                   changes=changed_count),
            rows=shown, count=len(shown),
            next_cursor=ledger._continuation(state, offset, len(shown), len(page) > inp.limit, s.company))
