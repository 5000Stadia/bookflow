"""Where a bank account's check numbers stop being consecutive, and where one repeats.

The report answers one question: which checks are not in the books? A check that was
written and never entered leaves nothing behind except the hole its number makes in the
sequence, so the hole is the only evidence there is, and printing the used numbers on
either side of it is what lets a person walk to the checkbook and look.

**A voided check is a used number.** Voiding reverses the accounting and keeps the
document, and the paper it was written on is still gone, so the number stays occupied. A
voided check that counted as missing would send someone hunting for a check they
deliberately destroyed.

**Which account a check belongs to is the account it is drawn on.** That is the first
entered line of its current revision, which is what ``company/checks.py`` reads as the
funding line; a correction that moves a check to another bank account moves it to that
account's sequence here too. A marked check whose lines were rearranged in the journal
editor until it no longer credits a bank account is counted in the totals rather than
being dropped without a word.

**A check number is a place in a sequence only when it is a plain run of digits.** A
check numbered ``EFT`` or ``1001-A`` is a real check with a real number and no position
between two others, so it is counted as entered and left out of the arithmetic; the
totals say how many there were.

**What a hole can also mean today.** A check number is ``transactions.number``, and a
check posts as a journal entry, so checks share one number series with card charges,
transfers and hand-typed journal entries -- ``document_effects.allocate`` hands out the
next free number in that series, not the bank account's own ``next_check_number``. A
check left unnumbered therefore takes whatever number the series is on, and a number the
series gave to something that is not a check reads here as a hole. Type the number on the
face of the cheque and the report says exactly what it means.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import Field, field_validator

from bookflow.company import ledger_reports as ledger
from bookflow.company.ledger_reports import (
    MoneyOutput, StrictModel, _account_display, account_order, iso_date, money,
)
from bookflow.company.ledger_schema import TRANSACTION_STATUSES
from bookflow.company.money_out import KIND
from bookflow.core.errors import BookflowError

# The stored spelling of the document this report reads, taken from the one place that
# maps a noun to its marker rather than retyped as a literal.
CHECK_KIND = KIND["check"]

# How wide a run of digits still reads back from SQLite's CAST as the integer it spells.
# A longer number is a check number, but it is not a position in a sequence, so it is
# counted as entered and left out of the arithmetic rather than silently truncated.
SEQUENCE_DIGITS = 18


def _bank_type():
    from bookflow.company.check_models import FUNDING_TYPE
    return FUNDING_TYPE["check"]


class MissingChecksInput(StrictModel):
    as_of: str = Field(min_length=10, max_length=10, description="Inclusive accounting as-of date, YYYY-MM-DD; a check dated after it is not examined, which can show a hole where a later-dated check sits.")
    account: str | None = Field(default=None, min_length=1, max_length=1000, description="Optional bank account ID or canonical full name; includes inactive accounts. Omit for every bank account.")
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _as_of = field_validator("as_of")(iso_date)

    @property
    def date_to(self) -> str:
        """The report period's single inclusive bound, named as every report names it."""
        return self.as_of


class CheckUse(StrictModel):
    """One check that occupies one number."""
    transaction_id: str
    number: str
    sequence_number: int
    date: str
    status: Literal[TRANSACTION_STATUSES]
    party_name: str | None
    memo: str | None
    amount: MoneyOutput


class MissingChecksRow(StrictModel):
    """A hole in one bank account's sequence, or a number two checks both carry."""
    kind: Literal["gap", "duplicate"]
    account_id: str
    current_account_label: str
    current_account_name: str
    current_account_number: str | None
    display_account_label: str
    first_missing: int | None = None
    last_missing: int | None = None
    missing_count: int = Field(default=0, ge=0)
    duplicate_number: int | None = None
    times_used: int | None = None
    before: CheckUse | None = None
    after: CheckUse | None = None
    checks: list[CheckUse] = Field(default_factory=list)


class MissingChecksTotals(StrictModel):
    """Counts over every examined check, not over the rows on this page."""
    checks_examined: int = Field(ge=0)
    numbered_checks: int = Field(ge=0)
    unnumbered_checks: int = Field(ge=0)
    checks_off_a_bank_account: int = Field(ge=0)
    gaps: int = Field(ge=0)
    missing_numbers: int = Field(ge=0)
    duplicate_numbers: int = Field(ge=0)
    duplicate_checks: int = Field(ge=0)


class MissingChecksOutput(ledger.Page):
    totals: MissingChecksTotals
    rows: list[MissingChecksRow]


# Every entered check, the bank account it is drawn on, and the number it occupies.
# ``money_out_documents`` is the only thing that says a journal entry was written as a
# check, so a hand-typed entry that happens to credit a bank account never appears here.
_CHECKS = f"""
WITH marked AS (
 SELECT m.transaction_id AS tx, t.number AS number, t.status AS status,
        r.id AS revision_id, r.date AS date, r.memo AS memo
 FROM money_out_documents m
 JOIN transactions t ON t.id=m.transaction_id AND t.type=m.type
 JOIN transaction_revisions r ON r.id=t.current_revision_id
 WHERE m.kind=:kind AND r.date<=:as_of
), funded AS (
 SELECT k.*, d.account_id, d.side, d.party_name, d.amount_minor_units, d.currency
 FROM marked k JOIN document_lines d ON d.revision_id=k.revision_id AND d.position=1
), drawn AS (
 SELECT f.* FROM funded f JOIN accounts a ON a.id=f.account_id
 WHERE f.side='credit' AND a.type=:bank AND (:account IS NULL OR f.account_id=:account)
), used AS (
 SELECT d.*, CAST(d.number AS INTEGER) AS seq FROM drawn d
 WHERE length(d.number) BETWEEN 1 AND {SEQUENCE_DIGITS} AND d.number NOT GLOB '*[^0-9]*'
), per_number AS (
 SELECT account_id, seq, count(*) AS uses FROM used GROUP BY account_id, seq
), sequenced AS (
 SELECT account_id, seq, uses,
   lag(seq) OVER (PARTITION BY account_id ORDER BY seq) AS previous
 FROM per_number
), finding AS (
 SELECT account_id, 'gap' AS kind, previous+1 AS first_missing, seq-1 AS last_missing,
        seq-previous-1 AS missing_count, previous AS before_seq, seq AS after_seq,
        NULL AS duplicate_number, NULL AS times_used, previous+1 AS sort_key
 FROM sequenced WHERE previous IS NOT NULL AND seq>previous+1
 UNION ALL
 SELECT account_id, 'duplicate', NULL, NULL, 0, NULL, NULL, seq, uses, seq
 FROM per_number WHERE uses>1
), selected AS (
 SELECT f.*, a.full_name AS account_full_name, a.name AS account_name,
        a.number AS account_number, a.full_name_key AS account_full_name_key
 FROM finding f JOIN accounts a ON a.id=f.account_id
)
"""

# The checks occupying the numbers this page prints, addressed by account and number
# together because two bank accounts run their own sequences.
_USES = _CHECKS + """, asked AS (
 SELECT json_extract(value,'$[0]') AS account_id, json_extract(value,'$[1]') AS seq
 FROM json_each(:pairs)
)
SELECT u.account_id, u.seq, u.tx, u.number, u.status, u.date, u.memo, u.party_name,
       u.amount_minor_units, u.currency
FROM used u JOIN asked k ON k.account_id=u.account_id AND k.seq=u.seq
ORDER BY u.account_id, u.seq, u.date, u.tx
"""


def _rows(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _not_a_bank_account(account):
    return BookflowError("E_VALIDATION", message=(
        f'A check is drawn on a bank account; "{account["name"]}" is a '
        f'{account["type"].replace("_", " ")} account, so it has no check numbers.'),
        details={"fields": [{"field": "account", "problem": "must name a bank account"}]})


def missing_checks(inp: MissingChecksInput, s, *, principal_id=None) -> MissingChecksOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        account_id = None
        if inp.cursor is not None:
            # Keep the stable ID the first page resolved: renaming the selected account
            # must stale the continuation rather than turn page two into a not-found.
            account_id = ledger._decode_cursor(inp.cursor, s.company).account_id
        elif inp.account is not None:
            from bookflow.company.accounts import resolve_account
            account = resolve_account(s.company, inp.account)
            if account["type"] != _bank_type():
                raise _not_a_bank_account(account)
            account_id = account["id"]
        state, offset = ledger._state(s, inp, "missing-checks", principal_id, account_id)
        raw = s.company.raw
        params = {"as_of": inp.as_of, "account": account_id, "kind": CHECK_KIND, "bank": _bank_type()}
        numbers, lowest = raw.execute(
            "SELECT use_account_numbers, show_lowest_subaccount_only FROM company_info").fetchone()
        examined, numbered, drawn = raw.execute(_CHECKS + """SELECT
            (SELECT count(*) FROM funded WHERE :account IS NULL OR account_id=:account),
            (SELECT count(*) FROM used), (SELECT count(*) FROM drawn)""", params).fetchone()
        gaps = missing = duplicates = duplicate_checks = 0
        # Stream every finding, including those past this page, so a page boundary can
        # never hide a hole from the count a person reads at the top.
        for kind, missing_count, times_used in raw.execute(
                _CHECKS + "SELECT kind, missing_count, times_used FROM finding", params):
            if kind == "gap":
                gaps, missing = gaps + 1, missing + missing_count
            else:
                duplicates, duplicate_checks = duplicates + 1, duplicate_checks + times_used
        totals = MissingChecksTotals(
            checks_examined=examined, numbered_checks=numbered,
            unnumbered_checks=drawn - numbered, checks_off_a_bank_account=examined - drawn,
            gaps=gaps, missing_numbers=missing, duplicate_numbers=duplicates,
            duplicate_checks=duplicate_checks)
        page = _rows(raw.execute(_CHECKS + f"""SELECT * FROM selected
            ORDER BY {account_order("account_")}, sort_key, kind
            LIMIT :limit OFFSET :offset""", {**params, "limit": inp.limit + 1, "offset": offset}))
        shown = page[:inp.limit]
        wanted = sorted({(row["account_id"], number) for row in shown
                         for number in (row["before_seq"], row["after_seq"], row["duplicate_number"])
                         if number is not None})
        occupants: dict[tuple[str, int], list[CheckUse]] = {}
        if wanted:
            for use in _rows(raw.execute(_USES, {**params, "pairs": json.dumps([list(pair) for pair in wanted])})):
                occupants.setdefault((use["account_id"], use["seq"]), []).append(CheckUse(
                    transaction_id=use["tx"], number=use["number"], sequence_number=use["seq"],
                    date=use["date"], status=use["status"], party_name=use["party_name"],
                    memo=use["memo"], amount=money(use["amount_minor_units"], use["currency"])))
        rows = []
        for row in shown:
            found = lambda number: occupants.get((row["account_id"], number), [])
            before, after = found(row["before_seq"]), found(row["after_seq"])
            rows.append(MissingChecksRow(
                kind=row["kind"], account_id=row["account_id"],
                current_account_label=row["account_full_name"],
                current_account_name=row["account_name"],
                current_account_number=row["account_number"],
                display_account_label=_account_display(
                    row["account_full_name"], row["account_name"], row["account_number"],
                    numbers, lowest),
                first_missing=row["first_missing"], last_missing=row["last_missing"],
                missing_count=row["missing_count"], duplicate_number=row["duplicate_number"],
                times_used=row["times_used"],
                before=before[-1] if before else None, after=after[0] if after else None,
                checks=found(row["duplicate_number"])))
        return MissingChecksOutput(metadata=state.metadata, totals=totals, rows=rows, count=len(rows),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))
