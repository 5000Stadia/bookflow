"""Deposit detail and the transaction list by date: two ways of reading posted documents.

Both read posting effects dated in the period and nothing else, so a correction or a void
appears as the reversal and replacement batches it posted, exactly as it does in
``report transaction-detail`` and the general ledger.

**Deposit detail** is each deposit's effect on its bank account, followed by what it
gathered: every other line of the same posting, grouped by the document a deposited line
came from (the payment or sales receipt waiting in Undeposited Funds) and the account it
was taken from. A line is signed debit minus credit, so money taken out of Undeposited
Funds is negative under a positive deposit, and cash back is positive; a deposit's lines
and its bank amount always net to nothing. A voided deposit nets to nothing on its own
date and has no rows.

**The transaction list by date** is one row per posting batch: the document's type, number
and memo, who it names, the account it posts to and the other side of the entry. Which line
is "the account" is read off the entry the way a register reads it: an entry with one line
on one side and several on the other is that one line (the bank on a check, Accounts
Payable on a bill, Accounts Receivable on an invoice); an entry with one line on each side
is the line on a bank, card, receivable or payable account, in that order, and the credit
line where both or neither are; an entry with several lines on each side is its first line.
The split is the other side when it is one account and ``-SPLIT-`` when it is more. The
amount is the entry's total, negative for a reversal.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import Field, field_validator, model_validator

from bookflow.company import ledger_reports as ledger
from bookflow.company.ledger_reports import (
    MANY_SPLITS, MoneyOutKind, MoneyOutput, StrictModel, TransactionType, iso_date, money,
)
from bookflow.core.errors import BookflowError


class _Period(StrictModel):
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


class DepositDetailInput(_Period):
    pass


class TransactionListByDateInput(_Period):
    pass


class DepositDetailTotals(StrictModel):
    deposited: MoneyOutput
    deposits: int = Field(ge=0)


class DepositDetailRow(StrictModel):
    """A deposit's bank row, or one thing it gathered."""
    kind: Literal["deposit", "line"]
    deposit_transaction_id: str
    deposit_number: str
    date: str
    transaction_id: str | None
    transaction_type: TransactionType | None
    money_out_kind: MoneyOutKind | None
    number: str | None
    party_name: str | None
    account_id: str
    current_account_label: str
    display_account_label: str
    memo: str | None
    amount: MoneyOutput


class TransactionListTotals(StrictModel):
    transactions: int = Field(ge=0)


class TransactionListRow(StrictModel):
    date: str
    transaction_id: str
    transaction_type: TransactionType
    money_out_kind: MoneyOutKind | None
    number: str
    batch_id: str
    batch_kind: Literal["original", "reversal", "replacement"]
    party_name: str | None
    memo: str | None
    account_id: str
    current_account_label: str
    display_account_label: str
    split_account_id: str | None
    split_account_label: str
    amount: MoneyOutput


class DepositDetailOutput(ledger.Page):
    totals: DepositDetailTotals
    rows: list[DepositDetailRow]


class TransactionListByDateOutput(ledger.Page):
    totals: TransactionListTotals
    rows: list[TransactionListRow]


def _rows(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _labeller(raw):
    """The company's own rule for showing an account on a report row."""
    numbers, lowest = raw.execute(
        "SELECT use_account_numbers, show_lowest_subaccount_only FROM company_info").fetchone()
    return lambda full_name, name, number: ledger._account_display(full_name, name, number, numbers, lowest)


# ---------------------------------------------------------------- deposit detail

# One row per deposit line group. `source` is the document a deposited line came from, read
# off the deposit component its posting line is attributed to; a line with none (an
# additional line, cash back) is its own group. Sums are over stored INTEGER columns.
_DEPOSIT = """
WITH lines AS (
 SELECT l.id, l.transaction_id AS deposit_id, l.account_id, l.party_name, l.description,
        l.debit_minor_units-l.credit_minor_units AS amount, b.effective_date,
        (l.account_id=p.bank_account_id) AS is_bank,
        (SELECT dc.source_transaction_id FROM posting_line_sources s
         JOIN deposit_components dc ON dc.id=s.deposit_component_id
         WHERE s.posting_line_id=l.id ORDER BY s.id LIMIT 1) AS source
 FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id
 JOIN transactions t ON t.id=l.transaction_id AND t.type='deposit'
 JOIN deposit_profiles p ON p.revision_id=b.revision_id
 WHERE b.effective_date>=:date_from AND b.effective_date<=:date_to
)
SELECT deposit_id, is_bank, coalesce(source, CASE WHEN is_bank THEN NULL ELSE id END) AS grp,
       source, account_id, min(party_name) AS party_name, min(description) AS description,
       bookflow_sum_int(amount) AS amount
FROM lines GROUP BY deposit_id, is_bank, grp, account_id
"""


def deposit_detail(inp: DepositDetailInput, s, *, principal_id=None) -> DepositDetailOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        state, offset = ledger._state(s, inp, "deposit-detail", principal_id, None, account_scoped=False)
        raw, currency = s.company.raw, state.metadata.currency
        label = _labeller(raw)
        groups = _rows(raw.execute(_DEPOSIT, {"date_from": inp.date_from, "date_to": inp.date_to}))
        documents = {row["id"]: row for row in _rows(raw.execute("""
            SELECT t.id, t.type, t.number AS stored_number, r.number, r.date, r.memo, m.kind AS money_out_kind
            FROM transactions t JOIN transaction_revisions r ON r.id=t.current_revision_id
            LEFT JOIN money_out_documents m ON m.transaction_id=t.id AND m.type=t.type
            WHERE t.id IN (SELECT value FROM json_each(:ids))""",
            {"ids": json.dumps(sorted({g["deposit_id"] for g in groups}
                                                     | {g["source"] for g in groups if g["source"]}))}))}
        accounts = {row["id"]: row for row in _rows(raw.execute(
            "SELECT id, full_name, name, number FROM accounts"))}
        by_deposit: dict[str, list[dict]] = {}
        for group in groups:
            group["amount"] = money(int(group["amount"]), currency).minor_units
            by_deposit.setdefault(group["deposit_id"], []).append(group)
        found, deposited, deposits = [], 0, 0
        for deposit_id, members in by_deposit.items():
            if not any(member["amount"] for member in members):
                continue
            deposit = documents[deposit_id]
            bank = [member for member in members if member["is_bank"]]
            gathered = [member for member in members if not member["is_bank"] and member["amount"]]
            bank_amount = sum(member["amount"] for member in bank)
            if bank_amount + sum(member["amount"] for member in gathered) != 0:
                raise BookflowError("E_INTERNAL", message="A deposit's lines do not net to its bank amount")
            deposited += bank_amount
            deposits += 1
            account = accounts[bank[0]["account_id"]] if bank else None
            head = dict(kind="deposit", deposit_transaction_id=deposit_id, deposit_number=deposit["number"],
                        date=deposit["date"], transaction_id=deposit_id, transaction_type="deposit",
                        money_out_kind=None, number=deposit["number"], party_name=None,
                        account_id=account["id"] if account else members[0]["account_id"],
                        memo=deposit["memo"], amount=bank_amount)
            lines = []
            for member in gathered:
                source = documents.get(member["source"]) if member["source"] else None
                lines.append(dict(kind="line", deposit_transaction_id=deposit_id,
                    deposit_number=deposit["number"],
                    date=source["date"] if source else deposit["date"],
                    transaction_id=source["id"] if source else None,
                    transaction_type=source["type"] if source else None,
                    money_out_kind=source["money_out_kind"] if source else None,
                    number=source["number"] if source else None, party_name=member["party_name"],
                    account_id=member["account_id"],
                    memo=(source["memo"] if source else member["description"]), amount=member["amount"]))
            lines.sort(key=lambda row: (row["date"], row["number"] or "", row["transaction_id"] or "", row["account_id"]))
            found.append(((deposit["date"], deposit["number"], deposit_id), [head, *lines]))
        found.sort(key=lambda item: item[0])
        flat = [row for _, rows in found for row in rows]
        page = flat[offset:offset + inp.limit + 1]
        rows = []
        for row in page[:inp.limit]:
            account = accounts[row["account_id"]]
            rows.append(DepositDetailRow(**{**row, "amount": money(row["amount"], currency),
                "current_account_label": account["full_name"],
                "display_account_label": label(account["full_name"], account["name"], account["number"])}))
        return DepositDetailOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=DepositDetailTotals(deposited=money(deposited, currency), deposits=deposits),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))


# ---------------------------------------------------------------- transaction list by date

# One row per (batch, account, side): what the entry did to each account on each side.
_SIDES = """
SELECT b.id AS batch_id, b.kind AS batch_kind, b.effective_date, b.created_at,
       t.id AS tx, t.type, m.kind AS money_out_kind, r.number, r.memo,
       l.account_id, a.type AS account_type, (l.debit_minor_units>0) AS is_debit,
       bookflow_sum_int(l.debit_minor_units+l.credit_minor_units) AS amount,
       min(l.line_no) AS first_line, min(l.party_name) AS party_name
FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id
JOIN transactions t ON t.id=l.transaction_id
JOIN transaction_revisions r ON r.id=b.revision_id
JOIN accounts a ON a.id=l.account_id
LEFT JOIN money_out_documents m ON m.transaction_id=t.id AND m.type=t.type
WHERE b.effective_date>=:date_from AND b.effective_date<=:date_to
GROUP BY b.id, l.account_id, is_debit
ORDER BY b.effective_date, b.created_at, b.id, first_line
"""
# The accounts a register is kept for, in the order a reader looks for them on an entry.
_REGISTER_RANK = {"bank": 0, "credit_card": 1, "accounts_receivable": 2, "accounts_payable": 3}


def _main(debits, credits):
    """(the line the entry is listed under, the other side's single line or None)."""
    if len(debits) == 1 and len(credits) > 1:
        return debits[0], None
    if len(credits) == 1 and len(debits) > 1:
        return credits[0], None
    if len(debits) == 1 and len(credits) == 1:
        debit, credit = debits[0], credits[0]
        if _REGISTER_RANK.get(debit["account_type"], 9) < _REGISTER_RANK.get(credit["account_type"], 9):
            return debit, credit
        return credit, debit
    first = min(debits + credits, key=lambda side: side["first_line"])
    return first, None


def transaction_list_by_date(inp: TransactionListByDateInput, s, *, principal_id=None) -> TransactionListByDateOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        state, offset = ledger._state(s, inp, "transaction-list-by-date", principal_id, None, account_scoped=False)
        raw, currency = s.company.raw, state.metadata.currency
        label = _labeller(raw)
        accounts = {row["id"]: row for row in _rows(raw.execute(
            "SELECT id, full_name, name, number FROM accounts"))}
        batches: dict[str, list[dict]] = {}
        for side in _rows(raw.execute(_SIDES, {"date_from": inp.date_from, "date_to": inp.date_to})):
            batches.setdefault(side["batch_id"], []).append(side)
        entries = []
        for sides in batches.values():
            debits = [side for side in sides if side["is_debit"]]
            credits = [side for side in sides if not side["is_debit"]]
            total = sum(int(side["amount"]) for side in debits)
            if total != sum(int(side["amount"]) for side in credits):
                raise BookflowError("E_INTERNAL", message="A posting batch does not balance")
            main, split = _main(debits, credits)
            parties = {side["party_name"] for side in sides if side["party_name"]}
            entries.append(dict(main=main, split=split, total=total,
                                party=main["party_name"] or (parties.pop() if len(parties) == 1 else None)))
        page = entries[offset:offset + inp.limit + 1]
        rows = []
        for entry in page[:inp.limit]:
            main, split = entry["main"], entry["split"]
            account = accounts[main["account_id"]]
            other = accounts[split["account_id"]] if split else None
            sign = -1 if main["batch_kind"] == "reversal" else 1
            rows.append(TransactionListRow(date=main["effective_date"], transaction_id=main["tx"],
                transaction_type=main["type"], money_out_kind=main["money_out_kind"], number=main["number"],
                batch_id=main["batch_id"], batch_kind=main["batch_kind"], party_name=entry["party"],
                memo=main["memo"], account_id=account["id"], current_account_label=account["full_name"],
                display_account_label=label(account["full_name"], account["name"], account["number"]),
                split_account_id=other["id"] if other else None,
                split_account_label=label(other["full_name"], other["name"], other["number"]) if other else MANY_SPLITS,
                amount=money(sign * entry["total"], currency)))
        return TransactionListByDateOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=TransactionListTotals(transactions=len(entries)),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))
