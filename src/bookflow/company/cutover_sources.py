"""Reading the old books' export files: IIF list exports and report CSVs.

Pure text in, typed rows out; nothing here reads or writes a database. The desktop product
exports lists (chart of accounts, customers, vendors, items) to IIF, tab-separated text with one
``!KEYWORD`` header row per list, but cannot export transactions to IIF, so the trial balance and
the open documents arrive as report CSVs. Those CSVs are read tolerantly: title rows may or may
not be present, leading empty columns are indent levels, an account cell may read
``6700 · Utilities:6710 · Telephone``, amounts may carry thousands separators, a leading minus or
parentheses, and every ``Total`` row is checked against the rows above it so a truncated export is
caught rather than read short.

Every problem is returned as a :class:`Problem` naming its file and line; nothing is raised for
the content of a file.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

KINDS = ("iif", "trial_balance", "open_invoices", "unpaid_bills", "ar_aging", "ap_aging", "inventory_valuation")

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
    return None


def _index(row: list[str], name: str) -> int:
    for index, cell in enumerate(row):
        if _norm(cell) == name:
            return index
    return -1


# ---------------------------------------------------------------- IIF lists

_LISTS = {"ACCNT": "account", "CUST": "customer", "VEND": "vendor", "INVITEM": "item", "TERMS": "term"}


def read_iif(source: SourceFile, out: Sources) -> None:
    headers: dict[str, list[str]] = {}
    count = 0
    for number, raw in enumerate(source.text.splitlines(), start=1):
        if not raw.strip():
            continue
        cells = [cell.strip().strip('"') if cell.strip().startswith('"') and cell.strip().endswith('"') else cell
                 for cell in raw.split("\t")]
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
                "Give its kind: iif, trial_balance, open_invoices, unpaid_bills, ar_aging, ap_aging or inventory_valuation.",
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
    return out
