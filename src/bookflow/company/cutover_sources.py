"""Reading the old books' export files: IIF list exports and report CSVs.

Pure text in, typed rows out; nothing here reads or writes a database. The desktop product
exports lists (chart of accounts, customers, vendors, items) to IIF, tab-separated text with one
``!KEYWORD`` header row per list, but cannot export transactions to IIF, so the trial balance and
the open documents arrive as report CSVs. Those CSVs are read tolerantly: title rows may or may
not be present, leading empty columns are indent levels, an account cell may read
``6700 · Utilities:6710 · Telephone``, amounts may carry thousands separators, a leading minus or
parentheses, and every ``Total`` row is checked against the rows above it so a truncated export is
caught rather than read short.

The rest of the old books arrives the same way: each bank and card account's Reconciliation
Summary (the last statement it was reconciled to), a transaction report filtered to what has not
cleared (the checks, deposits and card charges no statement has shown yet, and the receipts still
waiting in Undeposited Funds), and the 1099 Summary for the year so far.

Every problem is returned as a :class:`Problem` naming its file and line; nothing is raised for
the content of a file.
"""
from __future__ import annotations

import calendar
import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

KINDS = ("iif", "trial_balance", "open_invoices", "unpaid_bills", "ar_aging", "ap_aging", "inventory_valuation",
         "reconciliation_summary", "uncleared", "vendor_1099")

# The desktop product's account type codes, and the Bookflow type each one is.
ACCOUNT_TYPES = {
    "BANK": "bank", "AR": "accounts_receivable", "OCASSET": "other_current_asset", "FIXASSET": "fixed_asset",
    "OASSET": "other_asset", "AP": "accounts_payable", "CCARD": "credit_card", "OCLIAB": "other_current_liability",
    "LTLIAB": "long_term_liability", "EQUITY": "equity", "INC": "income", "COGS": "cost_of_goods_sold",
    "EXP": "expense", "EXINC": "other_income", "EXEXP": "other_expense", "NONPOSTING": "non_posting",
}
# Item type codes, and the Bookflow item type each one is.
ITEM_TYPES = {
    "SERV": "service", "PART": "non_inventory_part", "OTHC": "other_charge", "INVENTORY": "inventory_part",
    "ASSEMBLY": "inventory_assembly", "DISC": "discount", "SUBT": "subtotal", "GRP": "group", "PMT": "payment",
    "STAX": "sales_tax_item", "COMPTAX": "sales_tax_group",
}
# Account-list markers for the accounts the desktop product creates for itself.
SPECIAL_ACCOUNTS = {
    "ACCRCV": "accounts_receivable", "ACCPAY": "accounts_payable", "UNDEPOSIT": "undeposited_funds",
    "OPENBAL": "opening_balance_equity", "RETEARNINGS": "retained_earnings", "SALESTAX": "sales_tax_payable",
    "INVENTORYASSET": "inventory_asset", "COGS": "cost_of_goods_sold",
}
BUCKETS = ("current", "days_1_30", "days_31_60", "days_61_90", "over_90")


@dataclass
class Problem:
    severity: str  # "blocking" or "warning"
    code: str
    problem: str
    fix: str | None = None
    file: str | None = None
    line: int | None = None
    subject: str | None = None


@dataclass
class SourceFile:
    name: str
    kind: str | None
    sha256: str
    text: str
    attachment: str | None = None
    detected: bool = False
    rows: int = 0


@dataclass
class ListRow:
    """One entry of an IIF list: its full colon path and the raw fields by column name."""
    list: str  # account, customer, vendor, item, term
    path: str
    fields: dict[str, str]
    file: str
    line: int

    def get(self, name: str) -> str:
        return (self.fields.get(name) or "").strip()


@dataclass
class TrialBalanceRow:
    label: str
    number: str | None
    path: str
    debit: int
    credit: int
    file: str
    line: int

    @property
    def net(self) -> int:
        return self.debit - self.credit


@dataclass
class TrialBalance:
    file: str
    rows: list[TrialBalanceRow] = field(default_factory=list)
    total_debit: int | None = None
    total_credit: int | None = None
    basis: str | None = None
    as_of: str | None = None


@dataclass
class OpenDocument:
    """One row of Open Invoices or Unpaid Bills Detail."""
    side: str  # receivable or payable
    party: str
    type: str
    date: str | None
    number: str | None
    due_date: str | None
    terms: str | None
    po_number: str | None
    class_name: str | None
    memo: str | None
    open_balance: int
    amount: int | None
    file: str
    line: int


@dataclass
class AgingRow:
    party: str
    buckets: dict[str, int]
    total: int
    file: str
    line: int


@dataclass
class StockRow:
    item: str
    quantity: str  # decimal text, as the report printed it
    value: int
    file: str
    line: int


@dataclass
class ReconciliationSummary:
    """The Reconciliation Summary of the last statement an account was reconciled to in the old books.

    Balances are in the account's own sign, as the report prints them: money in the bank for a bank
    account, what is owed for a credit card. The statement's ending balance is the report's Cleared
    Balance; its Ending Balance is the register's, with everything entered after the statement.
    """
    label: str  # the account as the report names it: `1000 · Checking`
    number: str | None
    path: str
    statement_date: str | None
    file: str
    line: int
    beginning: int | None = None
    cleared_total: int | None = None
    cleared_balance: int | None = None
    uncleared_total: int = 0
    uncleared_count: int = 0
    register_balance: int | None = None
    new_total: int = 0
    ending_balance: int | None = None


@dataclass
class OpenItem:
    """One row of a transaction report filtered to what has not cleared: a check, deposit or card
    charge no statement had shown yet, or a receipt still waiting in Undeposited Funds."""
    label: str  # the account the row is listed under, as the report names it
    number: str | None
    path: str
    type: str
    date: str | None
    num: str | None
    name: str | None
    memo: str | None
    cleared: str | None
    amount: int  # debit positive, as the report prints it: a check is negative, a deposit positive
    file: str
    line: int


@dataclass
class Vendor1099Row:
    vendor: str
    boxes: dict[str, int]
    total: int
    file: str
    line: int


@dataclass
class Vendor1099Summary:
    """The 1099 Summary: what each 1099 vendor was paid between two dates, by 1099 box."""
    file: str
    date_from: str | None
    date_to: str | None
    boxes: list[str]
    rows: list[Vendor1099Row] = field(default_factory=list)


@dataclass
class Sources:
    files: list[SourceFile] = field(default_factory=list)
    lists: dict[str, list[ListRow]] = field(default_factory=lambda: {k: [] for k in ("account", "customer", "vendor", "item", "term")})
    ignored_lists: dict[str, int] = field(default_factory=dict)
    trial_balances: list[TrialBalance] = field(default_factory=list)
    documents: list[OpenDocument] = field(default_factory=list)
    receivable_files: list[str] = field(default_factory=list)
    payable_files: list[str] = field(default_factory=list)
    ar_aging: list[AgingRow] = field(default_factory=list)
    ap_aging: list[AgingRow] = field(default_factory=list)
    stock: list[StockRow] = field(default_factory=list)
    stock_files: list[str] = field(default_factory=list)
    reconciliations: list[ReconciliationSummary] = field(default_factory=list)
    open_items: list[OpenItem] = field(default_factory=list)
    open_item_files: list[str] = field(default_factory=list)
    vendor_1099: list[Vendor1099Summary] = field(default_factory=list)
    product: str | None = None
    problems: list[Problem] = field(default_factory=list)


# ---------------------------------------------------------------- text and values

def decode(raw: bytes) -> str:
    """UTF-8 (with or without a byte order mark), else Windows-1252, as the desktop product writes."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def key(path: str) -> str:
    """The lookup key of a colon path: each level trimmed, NFC and case-folded, as Bookflow keys names."""
    return ":".join(unicodedata.normalize("NFC", unicodedata.normalize("NFC", part.strip()).casefold())
                    for part in path.split(":"))


def clean_path(path: str) -> str:
    return ":".join(unicodedata.normalize("NFC", part.strip()) for part in path.split(":"))


def parse_amount(text: str, places: int = 2) -> int | None:
    """Exact minor units from a report amount: ``1,234.56``, ``-1,234.56``, ``(1,234.56)``, ``$12``; blank is None."""
    value = (text or "").strip().replace(" ", "").replace(" ", "")
    if not value:
        return None
    negative = False
    if value.startswith("(") and value.endswith(")"):
        negative, value = True, value[1:-1]
    if value.endswith("-"):
        negative, value = not negative, value[:-1]
    if value.startswith("-"):
        negative, value = not negative, value[1:]
    value = value.replace("$", "").replace(",", "")
    if value.startswith("-"):
        negative, value = not negative, value[1:]
    if not re.fullmatch(r"\d+(\.\d*)?|\.\d+", value):
        raise ValueError(f"not an amount: {text!r}")
    whole, _, fraction = value.partition(".")
    if len(fraction) > places:
        if fraction[places:].strip("0"):
            raise ValueError(f"more than {places} decimal places: {text!r}")
        fraction = fraction[:places]
    minor = int(whole or "0") * 10 ** places + int(fraction.ljust(places, "0") or "0")
    return -minor if negative else minor


_MONTHS = {name: index for index, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


def _year(text: str) -> int:
    year = int(text)
    if len(text) <= 2:
        year += 2000 if year < 70 else 1900
    return year


def parse_date(text: str) -> str | None:
    """ISO date from ``MM/DD/YYYY``, ``M/D/YY``, ``YYYY-MM-DD`` or ``December 31, 2025``; None when not a date."""
    value = (text or "").strip()
    if not value:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value).isoformat()
        found = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})", value)
        if found:
            return date(_year(found.group(3)), int(found.group(1)), int(found.group(2))).isoformat()
        found = re.fullmatch(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{2}|\d{4})", value)
        if found and found.group(1).lower() in _MONTHS:
            return date(_year(found.group(3)), _MONTHS[found.group(1).lower()], int(found.group(2))).isoformat()
    except ValueError:
        return None
    return None


_ACCOUNT_SEGMENT = re.compile(r"^\s*([0-9A-Za-z.\-]+)\s*[·•●·]\s*(.+?)\s*$")


def split_account_label(label: str) -> tuple[str | None, str]:
    """``6700 · Utilities:6710 · Telephone`` -> (``6710``, ``Utilities:Telephone``)."""
    names, number = [], None
    for segment in label.split(":"):
        found = _ACCOUNT_SEGMENT.match(segment)
        if found:
            number, name = found.group(1), found.group(2)
        else:
            number, name = None, segment.strip()
        names.append(name)
    return number, clean_path(":".join(names))


# ---------------------------------------------------------------- detection

_TITLES = (
    ("trial balance", "trial_balance"),
    ("open invoices", "open_invoices"),
    ("unpaid bills", "unpaid_bills"),
    ("a/r aging summary", "ar_aging"),
    ("ar aging summary", "ar_aging"),
    ("a/p aging summary", "ap_aging"),
    ("ap aging summary", "ap_aging"),
    ("inventory valuation summary", "inventory_valuation"),
    ("reconciliation summary", "reconciliation_summary"),
    ("1099 summary", "vendor_1099"),
)
_RECEIVABLE_TYPES = {"invoice", "credit memo", "payment", "statement charge", "sales receipt"}
_PAYABLE_TYPES = {"bill", "credit", "bill pmt -check", "bill pmt -ccard", "bill pmt-check", "bill pmt-ccard", "item receipt"}


def _rows(text: str) -> list[list[str]]:
    return [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text))]


def _norm(cell: str) -> str:
    return re.sub(r"[^a-z0-9>/%#-]+", " ", cell.lower()).strip()


def detect(text: str) -> str | None:
    """The kind of export this text is, from its own headings; None when it cannot tell."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    if lines[0].lstrip().startswith("!") or any(line.startswith(("!ACCNT", "!CUST", "!VEND", "!INVITEM", "!HDR", "!TERMS")) for line in lines[:50]):
        return "iif"
    rows = _rows(text)
    for row in rows[:12]:
        joined = " ".join(cell.lower() for cell in row if cell)
        for title, kind in _TITLES:
            if title in joined and len([cell for cell in row if cell]) <= 2:
                return kind
    for row in rows[:40]:
        cells = {_norm(cell) for cell in row if cell}
        if {"debit", "credit"} <= cells:
            return "trial_balance"
        if "on hand" in cells and "asset value" in cells:
            return "inventory_valuation"
        if "current" in cells and ("1 - 30" in cells or "1-30" in cells or "1 30" in cells):
            return None  # an aging summary names neither side in its columns
        if "type" in cells and "open balance" in cells:
            if "p o #" in cells or "terms" in cells:
                return "open_invoices"
            types = {_norm(r[_index(row, "type")]) for r in rows if len(r) > _index(row, "type") and r[_index(row, "type")]}
            if types & _PAYABLE_TYPES and not types & _RECEIVABLE_TYPES:
                return "unpaid_bills"
            if types & _RECEIVABLE_TYPES:
                return "open_invoices"
        if {"type", "date", "amount"} <= cells and "open balance" not in cells:
            return "uncleared"
        if "total" in cells and any(re.search(r"\bbox\b|compensation|rents|royalties", cell) for cell in cells):
            return "vendor_1099"
        if "cleared balance" in cells:
            return "reconciliation_summary"
    return None


def _index(row: list[str], name: str) -> int:
    for index, cell in enumerate(row):
        if _norm(cell) == name:
            return index
    return -1


# ---------------------------------------------------------------- IIF lists

_LISTS = {"ACCNT": "account", "CUST": "customer", "VEND": "vendor", "INVITEM": "item", "TERMS": "term"}
# Columns with a fixed form in every list. A row whose tabs were added or dropped puts the wrong
# value in them, which is how a shifted row is caught even when its width still looks right.
_YES_NO = frozenset({"HIDDEN", "TAXABLE", "1099", "USEID", "ISPASSEDTHRU"})
_WHOLE = frozenset({"REFNUM", "TIMESTAMP", "DELCOUNT"})
_FIX = "Export the list again from the old books rather than retyping it; a retyped file needs exactly one tab between fields, empty ones included."


def _iif_cells(line: str) -> list[str]:
    """One IIF line's cells. A quoted field may hold tabs; the quotes are not part of the value."""
    return next(csv.reader([line], delimiter="\t", quotechar='"', doublequote=True, strict=False), [])


def _shifted(header: list[str], cells: list[str]) -> list[str]:
    """The fixed-form columns whose values cannot be right, as `COLUMN 'value'` phrases."""
    found = []
    for column, value in zip(header[1:], cells[1:]):
        value = value.strip()
        if column in _YES_NO and value.upper() not in ("", "Y", "N"):
            found.append(f"{column} {value!r} (Y or N)")
        elif column in _WHOLE and value and not value.isdigit():
            found.append(f"{column} {value!r} (a whole number)")
    return found


def read_iif(source: SourceFile, out: Sources) -> None:
    headers: dict[str, list[str]] = {}
    count = 0
    for number, raw in enumerate(source.text.splitlines(), start=1):
        if not raw.strip():
            continue
        cells = _iif_cells(raw)
        if not cells:
            continue
        word = cells[0].strip()
        if word.startswith("!"):
            headers[word[1:]] = [cell.strip() for cell in cells]
            continue
        header = headers.get(word)
        if header is None:
            out.problems.append(Problem("warning", "iif_row_without_header",
                f"line {number} starts with {word!r} but no !{word} header row came before it; the row is skipped",
                file=source.name, line=number))
            continue
        name = cells[1].strip() if len(cells) > 1 else ""
        if word in _LISTS:
            # A genuine export may leave out an empty last field (one cell, always trailing); any
            # other difference is a tab added or dropped, which moves every field after it.
            width, expected = len(cells), len(header)
            if width > expected or width < expected - 1:
                out.problems.append(Problem("blocking", "row_width",
                    f"the {word} row {name!r} has {width} fields but its !{word} header names {expected}: a tab was "
                    "added or dropped, so every field after it would land in the wrong column", _FIX,
                    file=source.name, line=number, subject=name or None))
                continue
            cells = cells + [""] * (expected - width)
            wrong = _shifted(header, cells)
            if wrong:
                out.problems.append(Problem("blocking", "row_shifted",
                    f"the {word} row {name!r} has " + ", ".join(wrong) + ": a tab was added or dropped earlier in "
                    "the row, so its fields are in the wrong columns", _FIX,
                    file=source.name, line=number, subject=name or None))
                continue
        fields: dict[str, str] = {}
        for column, value in zip(header[1:], cells[1:]):
            if column and column not in fields:  # the item list really does name QNTY twice; the first wins
                fields[column] = value
        if word == "HDR":
            out.product = " ".join(part for part in (fields.get("PROD"), fields.get("VER")) if part) or None
            continue
        kind = _LISTS.get(word)
        if kind is None:
            out.ignored_lists[word] = out.ignored_lists.get(word, 0) + 1
            continue
        path = clean_path(fields.get("NAME", ""))
        if not path:
            out.problems.append(Problem("blocking", "iif_row_without_name", f"a {word} row has no NAME",
                                        file=source.name, line=number))
            continue
        out.lists[kind].append(ListRow(kind, path, fields, source.name, number))
        count += 1
    source.rows = count
    if not count:
        out.problems.append(Problem("blocking", "iif_without_lists",
            "the file holds no chart of accounts, customer, vendor, item or terms rows",
            "Export the lists with File > Utilities > Export > Lists to IIF Files.", file=source.name))


# ---------------------------------------------------------------- report CSVs

def _header(rows: list[list[str]], required: set[str]) -> int:
    for index, row in enumerate(rows[:60]):
        if required <= {_norm(cell) for cell in row if cell}:
            return index
    return -1


def _widths(source: SourceFile, out: Sources, rows: list[list[str]], start: int) -> bool:
    """Every row from the column headings through TOTAL has as many cells as the headings.

    A comma added or dropped moves every cell after it into the wrong column, so the report is
    refused rather than read. Blank lines are not rows.
    """
    expected = len(rows[start])
    clean = True
    for line, row in enumerate(rows[start + 1:], start=start + 2):
        if not any(cell for cell in row):
            continue
        if len(row) != expected:
            label = next((cell for cell in row if cell), "")
            out.problems.append(Problem("blocking", "row_width",
                f"the row {label!r} has {len(row)} cells but the column headings have {expected}: a comma was added "
                "or dropped, so every cell after it would land in the wrong column",
                "Export the report again from the old books rather than retyping it.",
                file=source.name, line=line, subject=label or None))
            clean = False
        if row and row[0].strip().upper() == "TOTAL":
            break
    return clean


def _title_facts(rows: list[list[str]], until: int) -> tuple[str | None, str | None]:
    basis = as_of = None
    for row in rows[:max(until, 0)]:
        for cell in row:
            lower = cell.lower()
            if "accrual basis" in lower:
                basis = "accrual"
            elif "cash basis" in lower:
                basis = "cash"
            found = re.search(r"as of\s+(.+)$", cell, re.IGNORECASE)
            if found and parse_date(found.group(1)):
                as_of = parse_date(found.group(1))
    return basis, as_of


def _amount(source: SourceFile, out: Sources, text: str, line: int, places: int) -> int | None:
    try:
        return parse_amount(text, places)
    except ValueError as error:
        out.problems.append(Problem("blocking", "unreadable_amount", str(error), file=source.name, line=line))
        return None


def read_trial_balance(source: SourceFile, out: Sources, places: int) -> None:
    rows = _rows(source.text)
    start = _header(rows, {"debit", "credit"})
    if start < 0:
        out.problems.append(Problem("blocking", "unreadable_report", "no row with Debit and Credit column headings",
                                    "Export the Trial Balance report to CSV.", file=source.name))
        return
    if not _widths(source, out, rows, start):
        return
    debit_at, credit_at = _index(rows[start], "debit"), _index(rows[start], "credit")
    basis, as_of = _title_facts(rows, start)
    report = TrialBalance(source.name, basis=basis, as_of=as_of)
    for offset, row in enumerate(rows[start + 1:], start=start + 2):
        label = next((cell for cell in row[:min(debit_at, credit_at)] if cell), "")
        if not label:
            continue
        debit = _amount(source, out, row[debit_at] if debit_at < len(row) else "", offset, places) or 0
        credit = _amount(source, out, row[credit_at] if credit_at < len(row) else "", offset, places) or 0
        if label.strip().upper() == "TOTAL":
            report.total_debit, report.total_credit = debit, credit
            continue
        number, path = split_account_label(label)
        report.rows.append(TrialBalanceRow(label.strip(), number, path, debit, credit, source.name, offset))
    source.rows = len(report.rows)
    out.trial_balances.append(report)


_DOCUMENT_COLUMNS = {
    "type": "type", "date": "date", "num": "num", "p o #": "po", "terms": "terms", "due date": "due_date",
    "class": "class", "aging": "aging", "open balance": "open_balance", "amount": "amount", "name": "name",
    "memo": "memo",
}


def _grouped(rows: list[list[str]], start: int, first_data: int):
    """Walk a report grouped by name: yield (kind, line, path, row) for figure rows and total rows.

    A row holding only names, left of the figure columns, is a group heading at the indent level
    of its cell; ``Total <name>`` closes the group of that name; ``TOTAL`` is the grand total. A
    named row that also carries figures (an aging or valuation summary line) yields its own name
    as the last level of its path.
    """
    stack: list[tuple[int, str]] = []
    for line, row in enumerate(rows[start + 1:], start=start + 2):
        filled = [(index, cell) for index, cell in enumerate(row) if cell]
        if not filled:
            continue
        index, cell = filled[0]
        if index >= first_data:
            yield "row", line, ":".join(name for _, name in stack), row
            continue
        if cell.upper() == "TOTAL":
            yield "grand_total", line, None, row
            continue
        if cell.startswith("Total "):
            name = cell[len("Total "):].strip()
            hit = max((position for position, (_, entry) in enumerate(stack) if entry == name), default=None)
            if hit is None:
                path = name
            else:
                path = ":".join(entry for _, entry in stack[:hit + 1])
                del stack[hit:]
            yield "total", line, path, row
            continue
        while stack and stack[-1][0] >= index:
            stack.pop()
        if all(position < first_data for position, _ in filled):
            stack.append((index, cell))
            continue
        yield "row", line, ":".join([name for _, name in stack] + [cell]), row
        stack.append((index, cell))  # rows indented under it are its jobs or sub-items


def read_documents(source: SourceFile, out: Sources, side: str, places: int) -> None:
    rows = _rows(source.text)
    start = _header(rows, {"type", "open balance"})
    if start < 0:
        out.problems.append(Problem("blocking", "unreadable_report", "no row with Type and Open Balance column headings",
            "Export the Open Invoices or Unpaid Bills Detail report to CSV.", file=source.name))
        return
    if not _widths(source, out, rows, start):
        return
    columns = {_DOCUMENT_COLUMNS[_norm(cell)]: index for index, cell in enumerate(rows[start]) if _norm(cell) in _DOCUMENT_COLUMNS}
    first_data = min(columns.values())
    cell = lambda row, name: row[columns[name]].strip() if name in columns and columns[name] < len(row) else ""
    totals: dict[str, int] = {}
    parsed: list[OpenDocument] = []
    grand = None
    for kind, line, path, row in _grouped(rows, start, first_data):
        balance = _amount(source, out, cell(row, "open_balance"), line, places)
        if kind == "grand_total":
            grand = balance
            continue
        if kind == "total":
            if balance is not None:
                totals[path] = totals.get(path, 0) + balance
            continue
        party = clean_path(cell(row, "name")) if cell(row, "name") else path
        if not cell(row, "type"):
            continue
        if not party:
            out.problems.append(Problem("blocking", "document_without_name",
                f"{cell(row, 'type')} {cell(row, 'num')} has no customer or vendor above it",
                file=source.name, line=line))
            continue
        if balance is None:
            continue
        amount = _amount(source, out, cell(row, "amount"), line, places) if "amount" in columns else None
        parsed.append(OpenDocument(side, party, cell(row, "type"), parse_date(cell(row, "date")),
                                   cell(row, "num") or None, parse_date(cell(row, "due_date")),
                                   cell(row, "terms") or None, cell(row, "po") or None, cell(row, "class") or None,
                                   cell(row, "memo") or None, balance, amount, source.name, line))
    for path, total in totals.items():
        under = sum(d.open_balance for d in parsed if d.party == path or d.party.startswith(path + ":"))
        if path and under != total:
            out.problems.append(Problem("blocking", "subtotal_mismatch",
                f"the rows under {path} add up to {_show(under, places)}, but its Total row says {_show(total, places)}",
                "Export the report again; rows may be missing.", file=source.name, subject=path))
    found = sum(d.open_balance for d in parsed)
    if grand is not None and grand != found:
        out.problems.append(Problem("blocking", "total_mismatch",
            f"the rows add up to {_show(found, places)}, but the report's TOTAL row says {_show(grand, places)}",
            "Export the whole report again; the file may have been cut short.", file=source.name))
    if grand is None:
        out.problems.append(Problem("warning", "no_total_row",
            "the report has no TOTAL row, so a cut-short export cannot be caught", file=source.name))
    source.rows = len(parsed)
    out.documents.extend(parsed)
    (out.receivable_files if side == "receivable" else out.payable_files).append(source.name)


_AGING_COLUMNS = {"current": "current", "1 - 30": "days_1_30", "1-30": "days_1_30", "1 30": "days_1_30",
                  "31 - 60": "days_31_60", "31-60": "days_31_60", "31 60": "days_31_60",
                  "61 - 90": "days_61_90", "61-90": "days_61_90", "61 90": "days_61_90",
                  "> 90": "over_90", ">90": "over_90", "over 90": "over_90", "91 and over": "over_90",
                  "total": "total"}


def read_aging(source: SourceFile, out: Sources, side: str, places: int) -> None:
    rows = _rows(source.text)
    start = next((index for index, row in enumerate(rows[:60])
                  if "current" in {_norm(cell) for cell in row} and "total" in {_norm(cell) for cell in row}), -1)
    if start < 0:
        out.problems.append(Problem("blocking", "unreadable_report", "no row with Current and TOTAL column headings",
                                    "Export the A/R or A/P Aging Summary report to CSV.", file=source.name))
        return
    if not _widths(source, out, rows, start):
        return
    columns = {_AGING_COLUMNS[_norm(cell)]: index for index, cell in enumerate(rows[start]) if _norm(cell) in _AGING_COLUMNS}
    first_data = min(columns.values())
    target = out.ar_aging if side == "receivable" else out.ap_aging
    count = 0
    for kind, line, path, row in _grouped(rows, start, first_data):
        if kind != "row":
            continue
        values = {bucket: _amount(source, out, row[columns[bucket]] if bucket in columns and columns[bucket] < len(row) else "", line, places) or 0
                  for bucket in (*BUCKETS, "total")}
        party = clean_path(path)
        if sum(values[b] for b in BUCKETS) != values["total"]:
            out.problems.append(Problem("blocking", "aging_row_mismatch",
                f"{party}'s columns do not add up to its TOTAL", file=source.name, line=line, subject=party))
        target.append(AgingRow(party, {b: values[b] for b in BUCKETS}, values["total"], source.name, line))
        count += 1
    source.rows = count


def read_stock(source: SourceFile, out: Sources, places: int) -> None:
    rows = _rows(source.text)
    start = _header(rows, {"on hand", "asset value"})
    if start < 0:
        out.problems.append(Problem("blocking", "unreadable_report", "no row with On Hand and Asset Value column headings",
                                    "Export the Inventory Valuation Summary report to CSV.", file=source.name))
        return
    if not _widths(source, out, rows, start):
        return
    hand_at, value_at = _index(rows[start], "on hand"), _index(rows[start], "asset value")
    first_data = min(hand_at, value_at)
    count = 0
    grand = None
    for kind, line, path, row in _grouped(rows, start, first_data):
        value = _amount(source, out, row[value_at] if value_at < len(row) else "", line, places)
        if kind == "grand_total":
            grand = value
            continue
        if kind != "row":
            continue
        quantity = (row[hand_at] if hand_at < len(row) else "").replace(",", "").strip() or "0"
        if not re.fullmatch(r"-?\d+(\.\d{1,6})?", quantity):
            out.problems.append(Problem("blocking", "unreadable_quantity", f"not a quantity: {quantity!r}",
                                        file=source.name, line=line))
            continue
        out.stock.append(StockRow(clean_path(path), quantity, value or 0, source.name, line))
        count += 1
    if grand is not None and grand != sum(row.value for row in out.stock if row.file == source.name):
        out.problems.append(Problem("blocking", "total_mismatch",
            "the item rows do not add up to the report's TOTAL asset value",
            "Export the whole report again; the file may have been cut short.", file=source.name))
    source.rows = count
    out.stock_files.append(source.name)


# ---------------------------------------------------------------- the rest of the old books

_PERIOD = re.compile(r"^(?P<account>.+?),\s*period ending\s+(?P<date>\S.*)$", re.IGNORECASE)
_ITEM_COUNT = re.compile(r"-\s*(\d+)\s+items?\s*$", re.IGNORECASE)
_SUMMARY_FIX = "Export the Reconciliation Summary of the account's last reconciled statement (Reports > Banking > Previous Reconciliation, Summary) to CSV."


def read_reconciliation_summary(source: SourceFile, out: Sources, places: int) -> None:
    """The last reconciliation: the account and statement date from `ACCOUNT, Period Ending DATE`,
    the statement's ending balance from Cleared Balance, and what was still uncleared, each total
    checked against the rows it adds up."""
    rows = _rows(source.text)
    summary = None
    for number, row in enumerate(rows, start=1):
        for cell in row:
            found = _PERIOD.match(cell)
            if found and parse_date(found.group("date")):
                account_number, path = split_account_label(found.group("account"))
                summary = ReconciliationSummary(found.group("account").strip(), account_number, path,
                                                parse_date(found.group("date")), source.name, number)
                break
        if summary is not None:
            break
    if summary is None:
        out.problems.append(Problem("blocking", "unreadable_report",
            "no `ACCOUNT, Period Ending DATE` heading names the account and statement this reconciliation is of",
            _SUMMARY_FIX, file=source.name))
        return
    section = None
    for number, row in enumerate(rows, start=1):
        filled = [cell for cell in row if cell]
        if not filled:
            continue
        label = filled[0].lower()
        if label in ("cleared transactions", "uncleared transactions", "new transactions"):
            section = label.split()[0]
            continue
        if len(filled) < 2:
            continue
        value = _amount(source, out, filled[-1], number, places)
        if value is None:
            continue
        if label == "beginning balance":
            summary.beginning = value
        elif label == "cleared balance":
            summary.cleared_balance = value
        elif label.startswith("register balance"):
            summary.register_balance = value
        elif label == "ending balance":
            summary.ending_balance = value
        elif label == "total cleared transactions":
            summary.cleared_total = value
        elif label == "total uncleared transactions":
            summary.uncleared_total = value
        elif label == "total new transactions":
            summary.new_total = value
        elif section == "uncleared" and _ITEM_COUNT.search(label):
            summary.uncleared_count += int(_ITEM_COUNT.search(label).group(1))
    if summary.cleared_balance is None:
        out.problems.append(Problem("blocking", "unreadable_report", "the reconciliation has no Cleared Balance row",
                                    _SUMMARY_FIX, file=source.name, subject=summary.label))
        return
    checks = []
    if summary.beginning is not None and summary.cleared_total is not None:
        checks.append(("the beginning balance and the cleared transactions", summary.beginning + summary.cleared_total,
                       "Cleared Balance", summary.cleared_balance))
    if summary.register_balance is not None:
        checks.append(("the cleared balance and the uncleared transactions", summary.cleared_balance + summary.uncleared_total,
                       "Register Balance", summary.register_balance))
        if summary.ending_balance is not None:
            checks.append(("the register balance and the new transactions", summary.register_balance + summary.new_total,
                           "Ending Balance", summary.ending_balance))
    for what, found, row, said in checks:
        if found != said:
            out.problems.append(Problem("blocking", "total_mismatch",
                f"{what} come to {_show(found, places)}, but its {row} row says {_show(said, places)}",
                "Export the report again; it may have been cut short or edited.", file=source.name, subject=summary.label))
    source.rows = 1
    out.reconciliations.append(summary)


_ITEM_COLUMNS = {"type": "type", "date": "date", "num": "num", "name": "name", "memo": "memo", "clr": "clr",
                 "split": "split", "amount": "amount", "balance": "balance"}
_ITEMS_FIX = ("Export the report again with the Cleared filter set to No: Reports > Custom Reports > Transaction Detail, "
              "totalled by account, or the account's QuickReport, to CSV.")


def read_open_items(source: SourceFile, out: Sources, places: int) -> None:
    """What had not cleared at the cutover, row by row, under the account each row belongs to."""
    rows = _rows(source.text)
    start = _header(rows, {"type", "date", "amount"})
    if start < 0:
        out.problems.append(Problem("blocking", "unreadable_report", "no row with Type, Date and Amount column headings",
                                    _ITEMS_FIX, file=source.name))
        return
    if not _widths(source, out, rows, start):
        return
    columns = {_ITEM_COLUMNS[_norm(cell)]: index for index, cell in enumerate(rows[start]) if _norm(cell) in _ITEM_COLUMNS}
    first_data = min(columns.values())
    cell = lambda row, name: row[columns[name]].strip() if name in columns and columns[name] < len(row) else ""
    parsed: list[OpenItem] = []
    totals: dict[str, int] = {}
    grand = None
    for kind, line, path, row in _grouped(rows, start, first_data):
        amount = _amount(source, out, cell(row, "amount"), line, places)
        if kind == "grand_total":
            grand = amount
            continue
        if kind == "total":
            if amount is not None and path:
                totals[path] = totals.get(path, 0) + amount
            continue
        if not cell(row, "type"):
            continue
        what = f"{cell(row, 'type')} {cell(row, 'num')}".strip()
        if not path:
            out.problems.append(Problem("blocking", "item_without_account",
                f"{what} on {cell(row, 'date')} is under no account heading, so it cannot be told which account it belongs to",
                "Export the report totalled by account, so each row sits under its account.", file=source.name, line=line))
            continue
        cleared = cell(row, "clr")
        if cleared and cleared != "*":
            out.problems.append(Problem("blocking", "cleared_row",
                f"{what} on {cell(row, 'date')} is marked cleared ({cleared!r}): the report was not filtered to what has not cleared",
                _ITEMS_FIX, file=source.name, line=line, subject=path))
            continue
        if amount is None:
            continue
        account_number, account_path = split_account_label(path)
        parsed.append(OpenItem(path, account_number, account_path, cell(row, "type"), parse_date(cell(row, "date")),
                               cell(row, "num") or None, clean_path(cell(row, "name")) if cell(row, "name") else None,
                               cell(row, "memo") or None, cleared or None, amount, source.name, line))
    for path, total in totals.items():
        under = sum(item.amount for item in parsed if item.label == path or item.label.startswith(path + ":"))
        if under != total:
            out.problems.append(Problem("blocking", "subtotal_mismatch",
                f"the rows under {path} add up to {_show(under, places)}, but its Total row says {_show(total, places)}",
                "Export the report again; rows may be missing.", file=source.name, subject=path))
    found = sum(item.amount for item in parsed)
    if grand is not None and grand != found:
        out.problems.append(Problem("blocking", "total_mismatch",
            f"the rows add up to {_show(found, places)}, but the report's TOTAL row says {_show(grand, places)}",
            "Export the whole report again; the file may have been cut short.", file=source.name))
    if grand is None and not totals:
        out.problems.append(Problem("warning", "no_total_row",
            "the report has no TOTAL row, so a cut-short export cannot be caught", file=source.name))
    source.rows = len(parsed)
    out.open_items.extend(parsed)
    out.open_item_files.append(source.name)


_RANGE = re.compile(r"^\s*(?P<m1>[A-Za-z]+)\.?(?:\s+(?P<d1>\d{1,2}))?(?:,?\s+(?P<y1>\d{4}))?\s+(?:through|thru|to|-|–)\s+"
                    r"(?P<m2>[A-Za-z]+)\.?(?:\s+(?P<d2>\d{1,2}))?,?\s+(?P<y2>\d{4})\s*$", re.IGNORECASE)
_MONTH_YEAR = re.compile(r"^\s*(?P<m>[A-Za-z]+)\.?\s+(?P<y>\d{4})\s*$")


def date_range(text: str) -> tuple[str, str] | None:
    """(first, last) ISO dates from a report's date heading: `January through June 2026`,
    `January 1 through June 30, 2026`, `June 2026`; None when it names no range."""
    month = lambda name: _MONTHS.get(name[:3].lower())
    try:
        found = _RANGE.match(text or "")
        if found:
            first, last = month(found.group("m1")), month(found.group("m2"))
            if not first or not last:
                return None
            year = int(found.group("y2"))
            start_year = int(found.group("y1")) if found.group("y1") else (year if first <= last else year - 1)
            start_day = int(found.group("d1")) if found.group("d1") else 1
            end_day = int(found.group("d2")) if found.group("d2") else calendar.monthrange(year, last)[1]
            return date(start_year, first, start_day).isoformat(), date(year, last, end_day).isoformat()
        found = _MONTH_YEAR.match(text or "")
        if found and month(found.group("m")):
            year, number = int(found.group("y")), month(found.group("m"))
            return date(year, number, 1).isoformat(), date(year, number, calendar.monthrange(year, number)[1]).isoformat()
    except ValueError:
        return None
    return None


_1099_FIX = ("Export the 1099 Summary (Reports > Vendors & Payables > 1099 Summary) for January 1 to the cutover date, "
             "with thresholds ignored so every 1099 vendor is listed, to CSV.")


def read_vendor_1099(source: SourceFile, out: Sources, places: int) -> None:
    """The year so far for each 1099 vendor: its row's box columns and TOTAL, and the dates the report covers."""
    rows = _rows(source.text)

    def heading(row: list[str]) -> bool:
        cells = [cell for cell in row if cell]
        if "total" not in {_norm(cell) for cell in cells} or len(cells) < 2:
            return False
        try:
            return all(parse_amount(cell, places) is None for cell in cells if _norm(cell) != "total")
        except ValueError:
            return True

    start = next((index for index, row in enumerate(rows[:60]) if heading(row)), -1)
    if start < 0:
        out.problems.append(Problem("blocking", "unreadable_report", "no row of 1099 box headings ending in TOTAL",
                                    _1099_FIX, file=source.name))
        return
    if not _widths(source, out, rows, start):
        return
    header = rows[start]
    total_at = next(index for index, cell in enumerate(header) if _norm(cell) == "total")
    boxes = {index: cell.strip() for index, cell in enumerate(header) if cell.strip() and index != total_at}
    first_data = min([*boxes, total_at])
    covered = next((date_range(cell) for row in rows[:start] for cell in row if date_range(cell)), None)
    report = Vendor1099Summary(source.name, covered[0] if covered else None, covered[1] if covered else None,
                               list(boxes.values()))
    grand = None
    for line, row in enumerate(rows[start + 1:], start=start + 2):
        label = next((cell for cell in row[:first_data] if cell), "")
        if not label:
            continue
        values = {name: _amount(source, out, row[index] if index < len(row) else "", line, places) or 0
                  for index, name in boxes.items()}
        total = _amount(source, out, row[total_at] if total_at < len(row) else "", line, places)
        if label.strip().upper() == "TOTAL":
            grand = total
            continue
        if total is None:
            total = sum(values.values())
        if sum(values.values()) != total:
            out.problems.append(Problem("blocking", "total_mismatch", f"{label}'s box columns do not add up to its TOTAL",
                                        "Export the report again.", file=source.name, line=line, subject=label))
        report.rows.append(Vendor1099Row(clean_path(label), values, total, source.name, line))
    if grand is not None and grand != sum(row.total for row in report.rows):
        out.problems.append(Problem("blocking", "total_mismatch", "the vendor rows do not add up to the report's TOTAL row",
                                    "Export the whole report again; the file may have been cut short.", file=source.name))
    source.rows = len(report.rows)
    out.vendor_1099.append(report)


def _show(minor: int, places: int) -> str:
    sign = "-" if minor < 0 else ""
    whole, fraction = divmod(abs(minor), 10 ** places)
    return f"{sign}{whole:,}.{fraction:0{places}d}" if places else f"{sign}{whole:,}"


def read(files: list[SourceFile], places: int) -> Sources:
    out = Sources(files=files)
    for source in files:
        kind = source.kind
        if kind is None:
            kind = detect(source.text)
            source.kind, source.detected = kind, True
        if kind is None:
            out.problems.append(Problem("blocking", "unknown_file",
                "cannot tell what this file is from its headings",
                "Give its kind: iif, trial_balance, open_invoices, unpaid_bills, ar_aging, ap_aging, inventory_valuation, "
                "reconciliation_summary, uncleared or vendor_1099.",
                file=source.name))
        elif kind == "iif":
            read_iif(source, out)
        elif kind == "trial_balance":
            read_trial_balance(source, out, places)
        elif kind == "open_invoices":
            read_documents(source, out, "receivable", places)
        elif kind == "unpaid_bills":
            read_documents(source, out, "payable", places)
        elif kind in ("ar_aging", "ap_aging"):
            read_aging(source, out, "receivable" if kind == "ar_aging" else "payable", places)
        elif kind == "inventory_valuation":
            read_stock(source, out, places)
        elif kind == "reconciliation_summary":
            read_reconciliation_summary(source, out, places)
        elif kind == "uncleared":
            read_open_items(source, out, places)
        elif kind == "vendor_1099":
            read_vendor_1099(source, out, places)
    return out
