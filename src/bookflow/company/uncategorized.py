"""What is waiting in the Uncategorized accounts ("Ask My Accountant").

The standard profile seeds two ordinary accounts, Uncategorized Expense (Ask My Accountant) and
Uncategorized Income. When the right account for an entry is unclear, it is posted there with a
memo saying what is unclear, and someone who knows moves it to its account later by correcting
the document. This read answers what is still waiting: every posting line in those accounts
that is still in effect -- an original or replacement effect no later reversal has taken back.
A recategorised entry has been reversed out of the account by its correction, so it is no
longer counted; a voided or deleted one likewise. The accounts are found by their seed keys,
so renaming one keeps it tracked.
"""
from __future__ import annotations

from pydantic import Field

from bookflow.company import ledger_reports as reports
from bookflow.company.accounts import NORMAL_BALANCE

SEED_KEYS = ("account.uncategorized-expense", "account.uncategorized-income")


class UncategorizedInput(reports.StrictModel):
    limit: int = Field(default=20, ge=1, le=200, description="Most entries to list, oldest first; counts and amounts always cover every entry.")


class UncategorizedAccount(reports.StrictModel):
    account_id: str
    label: str = Field(description="The account as reports show it, under the company's number and subaccount preferences.")
    name: str
    type: str
    active: bool
    count: int = Field(ge=0, description="Entries still in this account.")
    amount: reports.MoneyOutput = Field(description="What those entries come to on the account's normal side; equals the account's balance.")
    oldest_date: str | None = Field(description="Accounting date of the oldest entry still waiting; null when none is.")


class UncategorizedEntry(reports.StrictModel):
    account_id: str
    posting_line_id: str
    transaction_id: str
    transaction_type: reports.TransactionType
    money_out_kind: reports.MoneyOutKind | None = None
    number: str
    date: str
    memo: str | None = Field(description="The document's memo, which should say what is unclear.")
    description: str | None = Field(description="The line's own description.")
    party_name: str | None
    amount: reports.MoneyOutput = Field(description="On the account's normal side: positive is spending in the expense account and money received in the income account.")


class UncategorizedOutput(reports.StrictModel):
    count: int = Field(ge=0, description="Entries still waiting across both accounts.")
    accounts: list[UncategorizedAccount] = Field(description="Each Uncategorized account the company has, expense first.")
    entries: list[UncategorizedEntry] = Field(description="The oldest waiting entries, up to limit.")
    more: bool = Field(description="True when more entries wait than are listed.")


# A line is still waiting when its batch is a business effect (original or replacement) that no
# reversal batch has taken back.
_LIVE = """
  FROM posting_lines pl
  JOIN posting_batches pb ON pb.id = pl.batch_id
  WHERE pl.account_id = :account_id AND pb.kind IN ('original', 'replacement')
    AND NOT EXISTS (SELECT 1 FROM posting_batches r WHERE r.reverses_batch_id = pb.id)
"""


def uncategorized(inp: UncategorizedInput, s) -> UncategorizedOutput:
    raw = s.company.raw
    currency, numbers, lowest = raw.execute(
        "SELECT home_currency, use_account_numbers, show_lowest_subaccount_only FROM company_info").fetchone()
    found = raw.execute(
        "SELECT id, name, full_name, number, type, active, seed_key FROM accounts WHERE seed_key IN (?, ?)",
        SEED_KEYS).fetchall()
    order = {key: index for index, key in enumerate(SEED_KEYS)}
    accounts = sorted(found, key=lambda row: order[row[6]])
    listed: list[UncategorizedAccount] = []
    entries: list[tuple] = []
    total = 0
    for account_id, name, full_name, number, account_type, active, _seed in accounts:
        # Amounts read on the account's normal side: debit for expense, credit for income.
        sign = ("pl.debit_minor_units - pl.credit_minor_units" if NORMAL_BALANCE[account_type] == "debit"
                else "pl.credit_minor_units - pl.debit_minor_units")
        count, amount, oldest = raw.execute(
            f"SELECT count(*), coalesce(sum({sign}), 0), min(pb.effective_date) {_LIVE}",
            {"account_id": account_id}).fetchone()
        total += count
        listed.append(UncategorizedAccount(
            account_id=account_id,
            label=reports._account_display(full_name, name, number, numbers, lowest),
            name=name, type=account_type, active=bool(active), count=count,
            amount=reports.money(int(amount), currency), oldest_date=oldest))
        entries.extend(raw.execute(f"""
            SELECT pb.effective_date, pl.id, pl.account_id, pl.transaction_id, t.type, m.kind, r.number,
                   r.memo, pl.description, pl.party_name, {sign}
            {_LIVE.replace('WHERE', 'JOIN transactions t ON t.id = pl.transaction_id '
                           'JOIN transaction_revisions r ON r.id = pb.revision_id '
                           'LEFT JOIN money_out_documents m ON m.transaction_id = t.id AND m.type = t.type WHERE', 1)}
            ORDER BY pb.effective_date, pl.id LIMIT :limit""",
            {"account_id": account_id, "limit": inp.limit + 1}).fetchall())
    entries.sort(key=lambda row: (row[0], row[1]))
    shown = [UncategorizedEntry(
        date=row[0], posting_line_id=row[1], account_id=row[2], transaction_id=row[3],
        transaction_type=row[4], money_out_kind=row[5], number=row[6], memo=row[7],
        description=row[8], party_name=row[9], amount=reports.money(int(row[10]), currency))
        for row in entries[:inp.limit]]
    return UncategorizedOutput(count=total, accounts=listed, entries=shown, more=total > len(shown))

