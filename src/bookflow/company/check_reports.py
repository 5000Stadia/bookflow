"""Where a bank account's check numbers stop being consecutive, and where one repeats.

The report answers one question: which checks are not in the books? A check that was
written and never entered leaves nothing behind except the hole its number makes in the
sequence, so the hole is the only evidence there is, and printing the used numbers on
either side of it is what lets a person walk to the checkbook and look.

**It reads the cheque's own number, never the document's.** Every check carries a
``check_instruments`` row: the bank account whose chequebook the number came out of, and the
number itself. That is what this counts. ``transactions.number`` is the shared document
series every journal entry draws from, and reading it here is what made this report name
false gaps on any real company file -- a number that series gave a transfer, a card charge or
a hand-typed entry is not a missing cheque, and until the instrument existed there was no way
to tell the two apart.

**A voided check is a used number.** Voiding reverses the accounting and keeps the
document, and the paper it was written on is still gone, so the number stays occupied. A
voided check that counted as missing would send someone hunting for a check they
deliberately destroyed.

**A number a correction gave up is retired, not missing.** Renumbering a cheque leaves its
old number on the revision that carried it and on no current cheque. Nothing holds it, but
somebody accounted for it, and automatic allocation will not hand it out again -- so it is
printed as its own kind of row rather than counted as a hole. The cheque that gave it up is
named, with the number it carries now.

**Which account a check belongs to is the chequebook it was written from.** That is the
instrument's account, which ``check update`` moves when a correction moves the cheque to
another bank account. A marked check whose lines were later rearranged in the journal editor
until they no longer credit that account keeps its number and is counted in
``checks_off_a_bank_account`` rather than being dropped without a word. The
``money_out_documents`` marker is still joined, so what the report reads is a cheque by both
facts: somebody entered it as one, and it carries a chequebook number.

**A check number is a place in a sequence only when it is a plain run of digits.** A
check numbered ``EFT`` or ``1001-A`` is a real check with a real number and no position
between two others, so it is counted as entered and left out of the arithmetic; the
totals say how many there were. ``1001`` and ``01001`` are one place and not two, and on a
number Bookflow issued they are refused as a duplicate before they can be written.

**What the report cannot know about history.** Before check numbering existed a check's
number came from the shared document series, so a hole below the highest number co0040
carried over may be a number that series gave to something that was never a cheque. Those
gaps are marked ``legacy_uncertain`` and counted separately, and the report says so in
``disclosure``. Naming them confidently as missing cheques would be worse than admitting the
evidence does not reach that far.

**And what it cannot yet see at all.** A cheque written to pay a bill carries its number on
the bill payment, typed by the person rather than drawn from a chequebook sequence, and this
report does not place those numbers. When the file holds any, ``disclosure`` says how many,
because a hole this report prints may be one of them sitting unaccounted for.
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

# What a carried-over number is marked as. A gap among these cannot be told apart from a
# number the shared document series gave to something that was never a cheque.
LEGACY_ORIGIN = "migrated"

LEGACY_DISCLOSURE = (
    "Check numbers written before this company file was upgraded came from the shared "
    "document series every journal entry draws from, so a hole among them may be a number "
    "that series gave to a transfer, a card charge or a hand-typed entry rather than a "
    "cheque that was never entered. Rows marked legacy_uncertain are those, and what is "
    "stored cannot confirm them as missing cheques."
)

BILL_PAYMENT_DISCLOSURE = (
    "Cheques written to pay bills carry their number on the bill payment rather than in a "
    "chequebook sequence, and this company has {count} of them drawn on a bank account. "
    "Their numbers are not counted here, so a hole this report shows may be one of them."
)

# Cheques that were written but whose numbers this report cannot yet place in a sequence.
# Counting them is a later increment; saying nothing about them would let the report name a
# number as missing when a bill payment is sitting on it.
_BILL_PAYMENT_CHEQUES = """
SELECT count(*) FROM ap_payment_profiles p
JOIN transactions t ON t.id=p.transaction_id AND t.current_revision_id=p.revision_id
JOIN transaction_revisions r ON r.id=t.current_revision_id
JOIN accounts a ON a.id=p.funding_account_id
WHERE p.check_number IS NOT NULL AND a.type=:bank AND r.date<=:as_of
  AND (:account IS NULL OR p.funding_account_id=:account)
"""


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
    """One check that occupies one number, as it stands now."""
    transaction_id: str
    number: str
    sequence_number: int
    date: str
    status: Literal[TRANSACTION_STATUSES]
    party_name: str | None
    memo: str | None
    amount: MoneyOutput


class MissingChecksRow(StrictModel):
    """A hole in one bank account's sequence, a number two checks carry, or one given up."""
    kind: Literal["gap", "duplicate", "retired"]
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
    retired_number: int | None = None
    legacy_uncertain: bool = False
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
    retired_numbers: int = Field(default=0, ge=0)
    legacy_uncertain_gaps: int = Field(default=0, ge=0)
    checks_numbered_before_the_upgrade: int = Field(default=0, ge=0)


class MissingChecksOutput(ledger.Page):
    totals: MissingChecksTotals
    disclosure: str | None = None
    rows: list[MissingChecksRow]


# Every entered check, the chequebook its number came out of, and where it stands now.
# ``check_instruments`` is the only place a cheque number lives, so a hand-typed journal
# entry that happens to credit a bank account never appears here and a number the document
# series gave to something else is never mistaken for one.
_CHECKS = f"""
WITH dated AS (
 SELECT i.transaction_id AS tx, i.account_id AS account_id, i.check_number AS number,
        i.check_sequence AS seq, i.origin AS origin, t.status AS status,
        r.id AS revision_id, r.date AS date, r.memo AS memo
 FROM check_instruments i
 JOIN money_out_documents m ON m.transaction_id=i.transaction_id AND m.type=i.type
                           AND m.kind=:kind
 JOIN transactions t ON t.id=i.transaction_id AND t.type=i.type
 JOIN transaction_revisions r ON r.id=t.current_revision_id
 WHERE r.date<=:as_of
), funded AS (
 SELECT e.*, d.account_id AS funding_account_id, d.side AS side, d.party_name AS party_name,
        d.amount_minor_units AS amount_minor_units, d.currency AS currency
 FROM dated e LEFT JOIN document_lines d ON d.revision_id=e.revision_id AND d.position=1
), examined AS (
 SELECT * FROM funded WHERE (:account IS NULL OR account_id=:account)
), drawn AS (
 SELECT f.* FROM examined f
 WHERE f.side='credit' AND f.funding_account_id=f.account_id
), used AS (
 SELECT d.* FROM drawn d WHERE d.seq IS NOT NULL
), per_number AS (
 SELECT account_id, seq, count(*) AS uses FROM used GROUP BY account_id, seq
), retired AS (
 -- Joined to every dated check rather than to the ones this page filtered to: a cheque a
 -- correction moved to another account gave its number up on the account it left, and
 -- looking only at that account's current cheques would read it back as a hole there.
 SELECT v.account_id AS account_id, v.check_sequence AS seq
 FROM check_instrument_revisions v JOIN dated d ON d.tx=v.transaction_id
 WHERE v.check_sequence IS NOT NULL
   AND (:account IS NULL OR v.account_id=:account)
   AND NOT EXISTS (SELECT 1 FROM check_instruments h
                   WHERE h.account_id=v.account_id AND h.check_sequence=v.check_sequence)
 GROUP BY v.account_id, v.check_sequence
), accounted AS (
 SELECT account_id, seq FROM per_number
 UNION
 SELECT account_id, seq FROM retired
), legacy AS (
 -- How far up this account's book the shared document series reached. Read from every
 -- examined cheque rather than only the ones still drawn on it: where a number came from is
 -- a fact about the number, not about the shape the entry has today.
 SELECT account_id, max(seq) AS high_water FROM examined
 WHERE origin='{LEGACY_ORIGIN}' AND seq IS NOT NULL GROUP BY account_id
), sequenced AS (
 SELECT a.account_id, a.seq,
   lag(a.seq) OVER (PARTITION BY a.account_id ORDER BY a.seq) AS previous
 FROM accounted a
), finding AS (
 SELECT s.account_id, 'gap' AS kind, s.previous+1 AS first_missing, s.seq-1 AS last_missing,
        s.seq-s.previous-1 AS missing_count, s.previous AS before_seq, s.seq AS after_seq,
        NULL AS duplicate_number, NULL AS times_used, NULL AS retired_number,
        CASE WHEN s.previous+1 <= coalesce(l.high_water, 0) THEN 1 ELSE 0 END AS legacy_uncertain,
        s.previous+1 AS sort_key
 FROM sequenced s LEFT JOIN legacy l ON l.account_id=s.account_id
 WHERE s.previous IS NOT NULL AND s.seq>s.previous+1
 UNION ALL
 SELECT account_id, 'duplicate', NULL, NULL, 0, NULL, NULL, seq, uses, NULL, 0, seq
 FROM per_number WHERE uses>1
 UNION ALL
 SELECT r.account_id, 'retired', NULL, NULL, 0,
        (SELECT max(p.seq) FROM accounted p WHERE p.account_id=r.account_id AND p.seq<r.seq),
        (SELECT min(p.seq) FROM accounted p WHERE p.account_id=r.account_id AND p.seq>r.seq),
        NULL, NULL, r.seq, 0, r.seq
 FROM retired r
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

# The cheques that once carried a retired number, as they stand now. Their current number is
# what a person needs, because that is where the entry actually is today.
_RETIRED = _CHECKS + """, asked AS (
 SELECT json_extract(value,'$[0]') AS account_id, json_extract(value,'$[1]') AS seq
 FROM json_each(:pairs)
)
SELECT k.account_id AS account_id, k.seq AS seq, f.tx AS tx, f.number AS number,
       f.seq AS current_seq, f.status AS status, f.date AS date, f.memo AS memo,
       f.party_name AS party_name, f.amount_minor_units AS amount_minor_units,
       f.currency AS currency
FROM asked k
JOIN check_instrument_revisions v ON v.account_id=k.account_id AND v.check_sequence=k.seq
JOIN funded f ON f.tx=v.transaction_id
GROUP BY k.account_id, k.seq, f.tx
ORDER BY k.account_id, k.seq, f.date, f.tx
"""


def _rows(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _not_a_bank_account(account):
    return BookflowError("E_VALIDATION", message=(
        f'A check is drawn on a bank account; "{account["name"]}" is a '
        f'{account["type"].replace("_", " ")} account, so it has no check numbers.'),
        details={"fields": [{"field": "account", "problem": "must name a bank account"}]})


def _use(row, sequence=None):
    """One occupied or once-occupied number, as the cheque carrying it stands now."""
    place = sequence if sequence is not None else row["seq"]
    return CheckUse(
        transaction_id=row["tx"], number=row["number"], sequence_number=int(place),
        date=row["date"], status=row["status"], party_name=row["party_name"],
        memo=row["memo"], amount=money(row["amount_minor_units"], row["currency"]))


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
        examined, numbered, drawn, carried = raw.execute(_CHECKS + f"""SELECT
            (SELECT count(*) FROM examined),
            (SELECT count(*) FROM used), (SELECT count(*) FROM drawn),
            (SELECT count(*) FROM examined WHERE origin='{LEGACY_ORIGIN}')""", params).fetchone()
        gaps = missing = duplicates = duplicate_checks = retired = uncertain = 0
        # Stream every finding, including those past this page, so a page boundary can
        # never hide a hole from the count a person reads at the top.
        for kind, missing_count, times_used, legacy in raw.execute(
                _CHECKS + "SELECT kind, missing_count, times_used, legacy_uncertain FROM finding", params):
            if kind == "gap":
                gaps, missing = gaps + 1, missing + missing_count
                uncertain += 1 if legacy else 0
            elif kind == "duplicate":
                duplicates, duplicate_checks = duplicates + 1, duplicate_checks + times_used
            else:
                retired += 1
        totals = MissingChecksTotals(
            checks_examined=examined, numbered_checks=numbered,
            unnumbered_checks=drawn - numbered, checks_off_a_bank_account=examined - drawn,
            gaps=gaps, missing_numbers=missing, duplicate_numbers=duplicates,
            duplicate_checks=duplicate_checks, retired_numbers=retired,
            legacy_uncertain_gaps=uncertain, checks_numbered_before_the_upgrade=carried)
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
                occupants.setdefault((use["account_id"], use["seq"]), []).append(_use(use))
        gave_up = sorted({(row["account_id"], row["retired_number"]) for row in shown
                          if row["retired_number"] is not None})
        retired_by: dict[tuple[str, int], list[CheckUse]] = {}
        if gave_up:
            for use in _rows(raw.execute(_RETIRED, {**params, "pairs": json.dumps([list(pair) for pair in gave_up])})):
                # Printed at the number it carries now, because that is where the entry is.
                retired_by.setdefault((use["account_id"], use["seq"]), []).append(
                    _use(use, sequence=use["current_seq"] if use["current_seq"] is not None else use["seq"]))
        rows = []
        for row in shown:
            found = lambda number: occupants.get((row["account_id"], number), [])
            before, after = found(row["before_seq"]), found(row["after_seq"])
            carried_by = retired_by.get((row["account_id"], row["retired_number"]), [])
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
                times_used=row["times_used"], retired_number=row["retired_number"],
                legacy_uncertain=bool(row["legacy_uncertain"]),
                before=before[-1] if before else None, after=after[0] if after else None,
                checks=found(row["duplicate_number"]) + carried_by))
        elsewhere = raw.execute(_BILL_PAYMENT_CHEQUES, params).fetchone()[0]
        disclosed = ([LEGACY_DISCLOSURE] if carried else []) + (
            [BILL_PAYMENT_DISCLOSURE.format(count=elsewhere)] if elsewhere else [])
        return MissingChecksOutput(metadata=state.metadata, totals=totals, rows=rows, count=len(rows),
            disclosure=" ".join(disclosed) or None,
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))
