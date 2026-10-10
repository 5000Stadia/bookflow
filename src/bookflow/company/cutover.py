"""Moving a company in from its old books at a period boundary.

`cutover plan` reads the export files and works out every write, `cutover apply` makes them through
the ordinary commands, and `cutover tie-out` compares the books with the old books as of the
cutover date.

**The accounting.** One opening journal, dated the cutover date, carries every trial-balance
account except the ones documents own -- receivables and payables, which the open invoices and
bills carry, and inventory, which an inventory adjustment per item carries. Its balancing line
goes to a clearing account. Each open invoice comes in for its open balance with its own number,
date, due date and terms, as one line of the `Opening balance` Other Charge item, which credits the
same clearing account; each credit comes in as a credit memo debiting it; each bill and vendor
credit carries one expense line on it; each item's opening stock is credited to it. So the
clearing account nets to 0.00 exactly when the documents equal the trial balance's receivables,
payables and inventory. Receivables and payables never appear in the journal, so they cannot be
entered twice, and any difference between the documents and the trial balance stays in plain sight
as the clearing balance.

**Outside ids.** Every write carries the source reference `cutover:` plus the record's identity in
the old books -- its list and full name, or a document's side, type, name, number, date and
occurrence. Before a write is planned, the company audit trail is asked whether a write with that
reference already happened, with no actor filter, exactly as memorized entries recover. A rerun
therefore makes only what is missing, whoever runs it, and a run stopped part way continues where
it stopped.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from bookflow.company import cutover_sources as src
from bookflow.company import schema as c
from bookflow.company.cutover_models import (
    CutoverApplyOutput, CutoverBankRow, CutoverBankSection, CutoverCheck, CutoverClearing, CutoverClearingPart, CutoverCount,
    CutoverException, CutoverFileOutput, CutoverJournal, CutoverJournalLine, CutoverListRow, CutoverListSection,
    CutoverMappingsOutput, CutoverPlanOutput, CutoverStep, CutoverStockRow, CutoverStockSection, CutoverTieOutOutput,
    CutoverTieRow, CutoverTieSection,
)
from bookflow.company.ledger_reports import money
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Applied, Plan

SOURCE_MARK = "cutover:"
CLEARING_NAME = "Cutover Clearing"
OPENING_ITEM = "Opening balance"
REASON = "Move-in from the old books"
JOURNAL_LINES = 199  # `journal post` takes at most 200 lines; each part keeps one for its clearing line
CLEARING_TYPES = frozenset({"other_current_asset", "fixed_asset", "other_asset"})
DOCUMENT_ACCOUNT_TYPES = frozenset({"accounts_receivable", "accounts_payable"})
SYSTEM_NAMES = {
    "opening balance equity": ("equity", "opening_balance_equity"),
    "opening bal equity": ("equity", "opening_balance_equity"),
    "retained earnings": ("equity", "retained_earnings"),
    "undeposited funds": ("other_current_asset", "undeposited_funds"),
    "sales tax payable": ("other_current_liability", "sales_tax_payable"),
    "inventory asset": ("other_current_asset", "inventory_asset"),
    "cost of goods sold": ("cost_of_goods_sold", "cost_of_goods_sold"),
}
SKIPPED_ITEM_TYPES = {"group": "its members", "sales_tax_group": "its member tax items",
                      "inventory_assembly": "its bill of materials", "payment": "its payment method and deposit account"}
JOB_STATUS = {"awarded": "awarded", "closed": "closed", "in progress": "in_progress", "none": "none",
              "not awarded": "not_awarded", "pending": "pending"}


class Ref(str):
    """A placeholder in a step's input for a record another step makes; replaced by its ID at apply."""


# ------------------------------------------------------------------ the books as they stand

class Books:
    """What the company already holds, read once for a plan."""

    def __init__(self, s, as_of: str):
        conn = s.company.conn
        rows = lambda table: [dict(r) for r in conn.execute(sa.select(table)).mappings()]
        self.accounts = rows(c.accounts)
        self.customers = rows(c.customers)
        self.vendors = rows(c.vendors)
        self.items = rows(c.items)
        self.terms = rows(c.terms)
        self.classes = rows(c.classes)
        self.codes = rows(c.sales_tax_codes)
        self.employees = rows(c.employees)
        self.other_names = rows(c.other_names)
        self.payment_methods = rows(c.payment_methods)
        self.openings_1099 = {(row["vendor_id"], row["year"]): row for row in rows(c.vendor_1099_openings)}
        # Each account's reconciliation as it stands: the opening it adopted, and its open opening drafts.
        self.reconciliation: dict[str, dict[str, Any]] = defaultdict(lambda: {"opening": None, "drafts": []})
        for row in conn.execute(sa.select(c.reconciliation_accounts.c.account_id, c.reconciliation_accounts.c.opening_id)):
            self.reconciliation[row.account_id]["opening"] = row.opening_id
        for row in conn.execute(sa.select(c.reconciliation_drafts.c.id, c.reconciliation_drafts.c.account_id).where(
                c.reconciliation_drafts.c.kind == "opening", c.reconciliation_drafts.c.state == "open")):
            self.reconciliation[row.account_id]["drafts"].append(row.id)
        self.info = dict(conn.execute(sa.select(c.company_info)).mappings().one())
        self.currency = self.info["home_currency"]
        from bookflow.core.money import minor_units_of
        self.places = minor_units_of(self.currency)
        self.roles = {a["system_role"]: a for a in self.accounts if a["system_role"]}
        self.links: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for ref, record_type, record_id in s.company.raw.execute(
                "SELECT e.source_ref, n.record_type, n.record_id FROM audit_events e "
                "JOIN audit_entries n ON n.event_id = e.id WHERE e.source_ref LIKE 'cutover:%' ORDER BY e.seq, n.rowid"):
            self.links[ref[len(SOURCE_MARK):]].append((record_type, record_id))
        # Entries dated on or before the cutover that the move-in did not make and that still move an
        # account: a voided or deleted document nets to nothing and does not count.
        self.foreign = [dict(zip(("id", "type", "number", "date"), row)) for row in s.company.raw.execute(
            "SELECT t.id, t.type, t.number, MIN(m.first) FROM transactions t "
            "JOIN transaction_revisions r ON r.transaction_id = t.id AND r.revision_number = 1 "
            "LEFT JOIN audit_events e ON e.id = r.audit_event_id "
            "JOIN (SELECT b.transaction_id AS tid, l.account_id, SUM(l.debit_minor_units - l.credit_minor_units) AS net, "
            "      MIN(b.effective_date) AS first FROM posting_batches b JOIN posting_lines l ON l.batch_id = b.id "
            "      WHERE b.effective_date <= ? GROUP BY b.transaction_id, l.account_id HAVING net != 0) m ON m.tid = t.id "
            "WHERE e.source_ref IS NULL OR e.source_ref NOT LIKE 'cutover:%' "
            "GROUP BY t.id ORDER BY MIN(m.first), t.id LIMIT 6", (as_of,))]
        self.totals = {row[0]: row[1] for row in s.company.raw.execute(
            "SELECT t.id, r.total_minor_units FROM transactions t JOIN transaction_revisions r ON r.id = t.current_revision_id")}

    def link(self, outside_id: str, record_type: str) -> str | None:
        return next((record_id for kind, record_id in self.links.get(outside_id, ()) if kind == record_type), None)

    def by_key(self, rows: list[dict], name_field: str, value: str) -> dict | None:
        wanted = src.key(value)
        return next((r for r in rows if r.get(name_field + "_key") == wanted), None)

    def account(self, selector: str) -> dict | None:
        """An account by ID, number or full name."""
        selector = selector.strip()
        for row in self.accounts:
            if row["id"] == selector or (row["number"] and row["number"] == selector):
                return row
        return self.by_key(self.accounts, "full_name", selector)


@dataclass
class Step:
    kind: str
    outside_id: str
    name: str
    command: str | None
    payload: dict[str, Any]
    action: str
    record_id: str | None = None
    record_type: str = "transaction"
    amount: int | None = None
    date: str | None = None
    detail: str | None = None
    ref: str | None = None
    clearing: int | None = None  # what the record posts to the clearing account, debit positive, when its kind has no fixed sign
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class BankDetail:
    """A bank or card account brought in from its last reconciliation: the statement's ending balance,
    and each check, deposit and charge no statement had shown yet, one document each."""
    target: dict[str, Any]
    label: str
    card: bool
    summary: src.ReconciliationSummary | None
    items: list[src.OpenItem]
    statement: int  # the statement's ending balance, debit positive, as the opening journal carries it
    journal: str | None = None  # the outside id of the journal that carries it
    item_steps: list[Step] = field(default_factory=list)

    @property
    def statement_date(self) -> str | None:
        return self.summary.statement_date if self.summary else None


@dataclass
class Built:
    sources: src.Sources
    books: Books
    steps: list[Step] = field(default_factory=list)
    exceptions: list[src.Problem] = field(default_factory=list)
    checks: list[tuple[str, int, int]] = field(default_factory=list)
    mappings: dict[str, dict[str, str]] = field(default_factory=lambda: {k: {} for k in ("accounts", "customers", "vendors", "items", "terms")})
    journal: dict[str, Any] | None = None
    targets: dict[str, dict[str, Any]] = field(default_factory=dict)  # outside id -> {"id", "ref", "type", "name"}
    clearing_ref: str | None = None
    clearing_id: str | None = None
    piece: bool = False  # `cutover plan`: the files may be checked in pieces, without the trial balance
    no_account_list: list[tuple[str, str]] = field(default_factory=list)  # (item, account) skipped for want of the account list
    bank: dict[str, BankDetail] = field(default_factory=dict)  # account outside id -> its last reconciliation and uncleared items
    tax_item_skips: list[tuple[str, str]] = field(default_factory=list)  # (customer, TAXITEM) the move-in cannot give it
    payee_skips: list[str] = field(default_factory=list)  # uncleared items' payees in no name list
    payer_skips: list[str] = field(default_factory=list)  # undeposited receipts' payers that are no customer
    undeposited: set[str] = field(default_factory=set)  # Undeposited Funds accounts whose balance comes in as receipts


def _problem(built: Built, severity: str, code: str, problem: str, fix: str | None = None, *, file=None, line=None, subject=None):
    built.exceptions.append(src.Problem(severity, code, problem, fix, file, line, subject))


# ------------------------------------------------------------------ reading the files

def _files(s, inp) -> list[src.SourceFile]:
    files = []
    for position, given in enumerate(inp.files, start=1):
        if given.attachment is not None:
            row, text = _attachment_text(s, given.attachment)
            name = given.name or row["original_filename"]
        else:
            text, name = given.content, given.name or f"file {position}"
        files.append(src.SourceFile(name, given.kind, src.digest(text), text, attachment=given.attachment))
    return files


def _file_id(file: src.SourceFile) -> str:
    """The outside id of an export file given as text: its content, so the same text is kept once."""
    return "file:" + file.sha256


def _attachment_text(s, selector: str) -> tuple[dict, str]:
    from bookflow.company.attachment_text import attachment_text
    return attachment_text(s, selector, "files.attachment")


# ------------------------------------------------------------------ planning

def build(s, inp, *, journal_number: str | None = None, piece: bool = False) -> Built:
    files = _files(s, inp)
    books = Books(s, inp.as_of)
    for file in files:
        if file.attachment is None:
            file.attachment = books.link(_file_id(file), "attachment")
    sources = src.read(files, books.places)
    built = Built(sources, books, piece=piece)
    built.exceptions.extend(sources.problems)
    _file_checks(built, inp)
    if not {"accounts_receivable", "accounts_payable", "opening_balance_equity", "undeposited_funds",
            "sales_tax_payable", "inventory_asset"} <= set(books.roles):
        _problem(built, "blocking", "no_chart", "this company has no chart of accounts with the system accounts a move-in posts to",
                 "Apply a chart first (`chart apply general`), or create the company with one.")
        return built
    _sales_tax(built)
    _accounts(built, inp)
    _terms(built, inp)
    _parties(built, inp, "vendor")
    _items(built, inp)
    _parties(built, inp, "customer")  # after the items: a customer's sales tax item is one of them
    _no_account_list(built)
    _clearing(built, inp)
    _documents(built, inp)
    _stock(built, inp)
    _bank_items(built, inp)
    _undeposited(built, inp)
    _journal(built, inp, journal_number)
    _reconciliation_openings(built, inp)
    _vendor_1099(built, inp)
    _deactivations(built)
    _closing(built, inp)
    _default_tax_note(built)
    return built


def _sales_tax(built: Built) -> None:
    lists = built.sources.lists
    uses = (any(r.get("INVITEMTYPE").upper() in ("STAX", "COMPTAX") for r in lists["item"])
            or any(r.get("TAXABLE").upper() == "Y" for r in lists["customer"]))
    if uses and not built.books.info.get("sales_tax_enabled"):
        _problem(built, "warning", "sales_tax_disabled",
                 "the old books charge sales tax but sales tax is turned off in this company: customers come in without tax codes",
                 "Turn sales tax on in the company setup before the move-in to bring the customers' tax codes.")


TAX_TYPES = ("sales_tax_item", "sales_tax_group")


def _sole_tax_item(built: Built) -> str | None:
    """The name of the only active sales tax item or group the company will hold, when it has no default for them yet.

    The first invoice of a company with sales tax and no default fails until a tax item is named
    (the blind July trial), so the move-in sets the default when there is exactly one choice.
    """
    info = built.books.info
    if info.get("default_sales_tax_item_id") or not info.get("sales_tax_enabled"):
        return None
    names = [row["full_name"] for row in built.books.items if row["type"] in TAX_TYPES and row["active"]]
    names += [step.name for step in built.steps
              if step.kind == "item" and step.action == "create" and step.payload.get("type") in TAX_TYPES]
    return names[0] if len(names) == 1 else None


def _default_tax_note(built: Built) -> None:
    name = _sole_tax_item(built)
    if name:
        _problem(built, "note", "sales_tax_default",
                 f"{name} is the only sales tax item and the company has no default one: the move-in makes it the company's "
                 "default sales tax item, so the first invoice does not need one named",
                 "Change it with `company update` default_sales_tax_item_id.", subject=name)


def _file_checks(built: Built, inp) -> None:
    sources = built.sources
    seen: dict[str, str] = {}
    for file in sources.files:
        if file.sha256 in seen:
            _problem(built, "blocking", "duplicate_file", f"{file.name} is the same file as {seen[file.sha256]}",
                     "Give each export once.", file=file.name)
        seen.setdefault(file.sha256, file.name)
    if not sources.trial_balances and built.piece:
        # Files checked in pieces: one note, not a blocking exception on every partial check.
        _problem(built, "note", "no_trial_balance",
                 "no trial balance file was given, so these files were checked on their own; `cutover apply` needs "
                 "the trial balance with all the files",
                 "Give the Trial Balance export (accrual basis, as of the cutover date, to CSV) with the rest before applying.")
    elif not sources.trial_balances:
        _problem(built, "blocking", "no_trial_balance", "no trial balance file was given",
                 "Export the Trial Balance report, accrual basis, as of the cutover date, to CSV.")
    elif len(sources.trial_balances) > 1:
        _problem(built, "blocking", "several_trial_balances", "more than one trial balance file was given", "Give one.")
    for tb in sources.trial_balances:
        if tb.basis == "cash":
            _problem(built, "blocking", "cash_basis_trial_balance", "the trial balance is on the cash basis",
                     "Export it on the accrual basis: receivables and payables come in as documents.", file=tb.file)
        if tb.as_of and tb.as_of != inp.as_of:
            _problem(built, "blocking", "as_of_mismatch", f"the trial balance is as of {tb.as_of}, not {inp.as_of}",
                     "Export it as of the cutover date, or give that date as as_of.", file=tb.file)
        debit, credit = sum(r.debit for r in tb.rows), sum(r.credit for r in tb.rows)
        if debit != credit:
            _problem(built, "blocking", "trial_balance_unbalanced",
                     f"the trial balance's debits ({_show(built, debit)}) and credits ({_show(built, credit)}) differ",
                     "Export it again with every account.", file=tb.file)
        if tb.total_debit is not None and (tb.total_debit, tb.total_credit) != (debit, credit):
            _problem(built, "blocking", "total_mismatch", "the trial balance rows do not add up to its TOTAL row",
                     "Export the whole report again; the file may have been cut short.", file=tb.file)
        built.checks.append(("trial_balance_total", debit, credit))
    for doc in sources.documents:
        if doc.date is None:
            _problem(built, "blocking", "document_without_date", f"{doc.type} {doc.number or ''} for {doc.party} has no date",
                     file=doc.file, line=doc.line, subject=doc.party)
        elif doc.date > inp.as_of:
            _problem(built, "blocking", "document_after_cutover",
                     f"{doc.type} {doc.number or ''} for {doc.party} is dated {doc.date}, after the cutover date",
                     "Export the open documents as of the cutover date.", file=doc.file, line=doc.line, subject=doc.party)


def _show(built: Built, minor: int) -> str:
    return src._show(minor, built.books.places)


def _amount_text(built: Built, minor: int) -> str:
    places = built.books.places
    whole, fraction = divmod(abs(minor), 10 ** places)
    return f"{whole}.{fraction:0{places}d}" if places else str(whole)


_CREATE = re.compile(r"create(?:\s*[: ]\s*([0-9]{1,7}))?", re.IGNORECASE)


def _mapped(mapping: dict[str, str], *candidates: str | None) -> str | None:
    folded = {src.key(k): v for k, v in mapping.items()}
    for candidate in candidates:
        if candidate and src.key(candidate) in folded:
            return folded[src.key(candidate)]
    return None


def _target(built: Built, outside_id: str, *, record_id: str | None, ref: str | None, record_type: str, name: str, extra: dict | None = None):
    built.targets[outside_id] = {"id": record_id, "ref": ref, "type": record_type, "name": name, **(extra or {})}


def _already(built: Built, kind: str, outside_id: str, name: str, record_id: str, record_type: str) -> None:
    """A record an earlier run made: shown as a step, made again by nothing."""
    built.steps.append(Step(kind, outside_id, name, None, {}, "already_in", record_id, record_type))


def _id_or_ref(built: Built, outside_id: str):
    target = built.targets.get(outside_id)
    if target is None:
        return None
    return target["id"] if target["id"] else Ref(target["ref"])


# ---------------------------------------------------------------- accounts

@dataclass
class _SourceAccount:
    path: str
    type: str | None
    number: str | None
    description: str
    hidden: bool
    role: str | None
    labels: list[str]
    file: str | None
    line: int | None
    balance: int = 0


def _source_accounts(built: Built) -> dict[str, _SourceAccount]:
    found: dict[str, _SourceAccount] = {}
    for row in built.sources.lists["account"]:
        code = row.get("ACCNTTYPE").upper()
        kind = src.ACCOUNT_TYPES.get(code)
        if kind is None:
            _problem(built, "blocking", "unknown_account_type", f"{row.path} has account type {code!r}",
                     file=row.file, line=row.line, subject=row.path)
        role = src.SPECIAL_ACCOUNTS.get(row.get("EXTRA").upper())
        number = row.get("ACCNUM") or None
        found[src.key(row.path)] = _SourceAccount(row.path, kind, number, row.get("DESC"), row.get("HIDDEN").upper() == "Y",
                                                  role, [row.path], row.file, row.line)
    for tb in built.sources.trial_balances[:1]:
        for row in tb.rows:
            entry = found.get(src.key(row.path))
            if entry is None:
                entry = found[src.key(row.path)] = _SourceAccount(row.path, None, row.number, "", False, None, [], row.file, row.line)
            entry.labels.append(row.label)
            entry.balance += row.net
            if entry.number is None:
                entry.number = row.number
    return found


def _role_of(account: _SourceAccount) -> str | None:
    if account.role:
        return account.role
    if account.type == "accounts_receivable":
        return "accounts_receivable"
    if account.type == "accounts_payable":
        return "accounts_payable"
    by_name = SYSTEM_NAMES.get(src.key(account.path))
    if by_name and account.type in (None, by_name[0]):
        return by_name[1]
    return None


def _accounts(built: Built, inp) -> None:
    books = built.books
    accounts = _source_accounts(built)
    taken_numbers = {a["number"] for a in books.accounts if a["number"]}
    claimed_roles: dict[str, str] = {}
    skipped = []
    for entry in sorted(accounts.values(), key=lambda a: (a.path.count(":"), a.line or 0)):
        outside_id = "account:" + src.key(entry.path)
        label = entry.labels[-1] if entry.labels else entry.path
        decided = _mapped(inp.mappings.accounts, *entry.labels, entry.path, entry.number)
        create_number = None
        if decided is not None and (chosen := _CREATE.fullmatch(decided.strip())):
            decided, create_number = "create", chosen.group(1)
        record = None
        if entry.type == "non_posting" and decided is None:
            skipped.append(entry.path)
            continue
        if decided is not None and decided.lower() != "create":
            record = books.account(decided)
            if record is None:
                _problem(built, "blocking", "mapping_not_found", f"mappings.accounts maps {label} to {decided!r}, which is no account here",
                         "Map it to an account ID, number or full name.", file=entry.file, line=entry.line, subject=label)
                continue
        if record is None and decided in (None, "create"):
            # An account an earlier run made: found by what the run recorded, whatever number it came in under.
            linked = books.link(outside_id, "account")
            if linked:
                record = next((a for a in books.accounts if a["id"] == linked), None)
        role = _role_of(entry)
        if record is None and decided is None and role:
            if role in claimed_roles:
                if role in ("accounts_receivable", "accounts_payable") and entry.balance:
                    _problem(built, "blocking", "several_control_accounts",
                             f"{label} is a second {role.replace('_', ' ')} account in the old books, and the open documents do not say which one they sit in",
                             f"Map it to the same Bookflow account as {claimed_roles[role]} with mappings.accounts.", file=entry.file,
                             line=entry.line, subject=label)
                    continue
            else:
                record = books.roles.get(role)
                claimed_roles[role] = label
        if record is None and decided is None:
            same = books.by_key(books.accounts, "full_name", entry.path)
            if same is not None:
                if entry.type is not None and same["type"] != entry.type:
                    _problem(built, "blocking", "type_conflict",
                             f"{label} is {entry.type.replace('_', ' ')} in the old books but {same['full_name']} is {same['type'].replace('_', ' ')} here",
                             "Map it to another account with mappings.accounts, or rename one of them.", file=entry.file,
                             line=entry.line, subject=label)
                    continue
                record = same
            elif entry.number and entry.number in taken_numbers:
                holder = next(a for a in books.accounts if a["number"] == entry.number)
                _problem(built, "blocking", "number_taken",
                         f"number {entry.number} is {holder['full_name']} here and {entry.path} in the old books",
                         f"Map it to {holder['number']} (or another account) with mappings.accounts, renumber {holder['full_name']} here with `account update`, "
                         "give `create` to make it without the number, or give `create <number>` to make it under another number.",
                         file=entry.file, line=entry.line, subject=label)
                continue
        if record is not None and entry.type is not None and record["type"] != entry.type:
            if entry.type in DOCUMENT_ACCOUNT_TYPES or record["type"] in DOCUMENT_ACCOUNT_TYPES:
                _problem(built, "blocking", "control_account_mapping",
                         f"{label} is {entry.type.replace('_', ' ')} in the old books and cannot stand for {record['full_name']} ({record['type'].replace('_', ' ')})",
                         "A receivable or payable account maps only to a receivable or payable account; its documents carry it.",
                         file=entry.file, line=entry.line, subject=label)
                continue
            if decided is None:
                _problem(built, "blocking", "type_conflict",
                         f"{label} is {entry.type.replace('_', ' ')} in the old books but {record['full_name']} is {record['type'].replace('_', ' ')} here",
                         "Map it to another account with mappings.accounts.", file=entry.file, line=entry.line, subject=label)
                continue
        if record is not None:
            made = books.link(outside_id, "account") == record["id"]
            if entry.number and entry.number != (record["number"] or None) and not made:
                _problem(built, "note", "account_number_differs",
                         f"{label} is {record['full_name']} here, which keeps its number {record['number'] or '(none)'}; the old books number it {entry.number}",
                         "Renumber it with `account update` if the old number should carry over.", file=entry.file,
                         line=entry.line, subject=label)
            _target(built, outside_id, record_id=record["id"], ref=None, record_type=record["type"], name=record["full_name"],
                     extra={"role": record["system_role"], "label": label, "balance": entry.balance,
                            "made": made, "hidden": entry.hidden, "active": record["active"]})
            if made:
                _already(built, "account", outside_id, entry.path, record["id"], "account")
            built.mappings["accounts"][label] = record["id"]
            continue
        if books.by_key(books.accounts, "full_name", entry.path) is not None:
            _problem(built, "blocking", "name_taken", f"{label} cannot be made: an account here already has the name {entry.path}",
                     "Map it to that account with mappings.accounts, or rename one of them.", file=entry.file, line=entry.line,
                     subject=label)
            continue
        if entry.type is None:
            _problem(built, "blocking", "unmapped_account",
                     f"{label} is on the trial balance but in no account list given, and no account here has that name",
                     "Give the chart of accounts IIF export, or map it with mappings.accounts.", file=entry.file,
                     line=entry.line, subject=label)
            continue
        parent_path = entry.path.rsplit(":", 1)[0] if ":" in entry.path else None
        parent = None
        if parent_path:
            parent = built.targets.get("account:" + src.key(parent_path))
            if parent is None:
                _problem(built, "blocking", "unmapped_parent", f"{label}'s parent account {parent_path} is not in the account list",
                         "Give the whole chart of accounts.", file=entry.file, line=entry.line, subject=label)
                continue
            if parent["type"] != entry.type:
                _problem(built, "blocking", "parent_type_conflict",
                         f"{label} is {entry.type.replace('_', ' ')} but its parent stands for a {parent['type'].replace('_', ' ')} account here",
                         "Map the parent to an account of the same type.", file=entry.file, line=entry.line, subject=label)
                continue
        number = entry.number
        if create_number:
            if create_number in taken_numbers:
                holder = next(a for a in books.accounts if a["number"] == create_number)
                _problem(built, "blocking", "number_taken",
                         f"{label} is to be made as number {create_number}, which is {holder['full_name']} here",
                         "Give `create` with another number, or plain `create` to make it without one.",
                         file=entry.file, line=entry.line, subject=label)
                continue
            number = create_number
            if entry.number and entry.number != number:
                _problem(built, "note", "account_number_changed",
                         f"{label} comes in as number {number}; the old books number it {entry.number}", file=entry.file,
                         line=entry.line, subject=label)
        elif number and (not re.fullmatch(r"[0-9]{1,7}", number) or number in taken_numbers):
            if re.fullmatch(r"[0-9]{1,7}", number):
                holder = next((a for a in books.accounts if a["number"] == number), None)
                why = f"{number} is {holder['full_name']} here" if holder else "the number is taken"
                fix = (f"To keep the number, first renumber {holder['full_name']} here with `account update` and plan again. "
                       if holder else "") + ("Or give `create <number>` in mappings.accounts to bring it in under a different number, "
                       "or leave it as it is to bring it in unnumbered.")
            else:
                why, fix = "Bookflow numbers are one to seven digits", None
            _problem(built, "warning", "account_number_dropped",
                     f"{label} comes in without its number {number}: {why}", fix,
                     file=entry.file, line=entry.line, subject=label)
            number = None
        if number:
            taken_numbers.add(number)
        leaf = entry.path.rsplit(":", 1)[-1]
        payload = {"name": leaf, "type": entry.type, "number": number, "description": (entry.description or None) and entry.description[:200]}
        if parent is not None:
            payload["parent_id"] = parent["id"] or Ref(parent["ref"])
        linked = books.link(outside_id, "account")
        step = Step("account", outside_id, entry.path, "account create", {k: v for k, v in payload.items() if v is not None},
                    "already_in" if linked else "create", linked, "account", ref=outside_id,
                    detail=entry.type.replace("_", " ") + (f", number {number}" if number else ""))
        built.steps.append(step)
        _target(built, outside_id, record_id=linked, ref=outside_id, record_type=entry.type, name=entry.path,
                extra={"role": None, "label": label, "balance": entry.balance, "hidden": entry.hidden, "made": True, "active": True})
        built.mappings["accounts"][label] = linked or "create"
    if skipped:
        _problem(built, "warning", "non_posting_accounts",
                 "not brought in: " + ", ".join(skipped) + " (non-posting accounts the old books keep for estimates and purchase orders)")


def _account_for(built: Built, path: str | None):
    if not path:
        return None
    return built.targets.get("account:" + src.key(path))


# ---------------------------------------------------------------- terms

_NET = re.compile(r"^net\s+(\d{1,3})$", re.IGNORECASE)
_DISCOUNT = re.compile(r"^(\d{1,2}(?:\.\d{1,4})?)\s*%\s*(\d{1,3})\s+net\s+(\d{1,3})$", re.IGNORECASE)


def _whole_number(text: str | None) -> int | None:
    text = (text or "").strip()
    return int(text) if text.isdigit() else None


def _term_settings(name: str, row: src.ListRow | None) -> dict[str, Any] | None:
    """What a term means in the old books: from its !TERMS row when the list was given (either
    export layout), else read off its name (`Net 30`, `1% 10 Net 30`, `Due on receipt`); None when
    neither says. Percent is exact millionths."""
    from bookflow.core.exact import parse_percentage_millionths
    percent = lambda text: parse_percentage_millionths(text.strip().rstrip("%").strip() or "0")
    if row is not None:
        due = _whole_number(row.get("DUEDAYS") or row.get("STDDUEDAYS"))
        discount_days = _whole_number(row.get("DISCDAYS") or row.get("STDDISCDAYS"))
        try:
            discount = percent(row.get("DISCPER")) if row.get("DISCPER") else 0
        except BookflowError:
            discount = None
        if row.get("TERMSTYPE") == "1":
            return {"kind": "date_driven", "due_day_of_month": _whole_number(row.get("DAYOFMONTHDUE")),
                    "due_next_month_if_within_days": _whole_number(row.get("DATEMINDAYS")),
                    "discount_day_of_month": _whole_number(row.get("DISCDAYOFMONTH")) if discount else None,
                    "discount_percent_millionths": discount or None}
        if due is not None:
            return {"kind": "standard", "due_days": due, "discount_percent_millionths": discount or None,
                    "discount_days": discount_days if discount else None}
    found = _NET.match(name.strip())
    if found:
        return {"kind": "standard", "due_days": int(found.group(1)), "discount_percent_millionths": None, "discount_days": None}
    found = _DISCOUNT.match(name.strip())
    if found:
        return {"kind": "standard", "due_days": int(found.group(3)), "discount_percent_millionths": percent(found.group(1)),
                "discount_days": int(found.group(2))}
    if src.key(name) == "due on receipt":
        return {"kind": "standard", "due_days": 0, "discount_percent_millionths": None, "discount_days": None}
    return None


def _term_differences(wanted: dict[str, Any], record: dict[str, Any]) -> list[str]:
    """Each setting where the term here says something other than the old books' term."""
    words = {"kind": "kind", "due_days": "due in days", "discount_percent_millionths": "discount percent",
             "discount_days": "discount days", "due_day_of_month": "due day of month",
             "due_next_month_if_within_days": "next month within days", "discount_day_of_month": "discount day of month"}
    from bookflow.core.exact import format_percentage_millionths
    found = []
    for field_, value in wanted.items():
        mine = record.get(field_)
        if (mine or None) != (value or None):
            shown = lambda v: ("none" if v in (None, 0) else
                               format_percentage_millionths(v) + "%" if field_ == "discount_percent_millionths" else v)
            found.append(f"{words[field_]} {shown(mine)} here, {shown(value)} in the old books")
    return found


def _term_payload(name: str, settings: dict[str, Any]) -> dict[str, Any]:
    from bookflow.core.exact import format_percentage_millionths
    payload: dict[str, Any] = {"name": name, "kind": settings["kind"]}
    for field_ in ("due_days", "discount_days", "due_day_of_month", "due_next_month_if_within_days", "discount_day_of_month"):
        if settings.get(field_) is not None:
            payload[field_] = settings[field_]
    if settings.get("discount_percent_millionths"):
        payload["discount_percent"] = format_percentage_millionths(settings["discount_percent_millionths"])
    return payload


def _terms(built: Built, inp) -> None:
    books = built.books
    names: dict[str, tuple[str, str | None, int | None]] = {}
    rows = {src.key(row.path): row for row in built.sources.lists["term"]}
    for kind in ("customer", "vendor"):
        for row in built.sources.lists[kind]:
            if row.get("TERMS"):
                names.setdefault(src.key(row.get("TERMS")), (row.get("TERMS"), row.file, row.line))
    for doc in built.sources.documents:
        if doc.terms:
            names.setdefault(src.key(doc.terms), (doc.terms, doc.file, doc.line))
    for row in built.sources.lists["term"]:
        names.setdefault(src.key(row.path), (row.path, row.file, row.line))
    for folded, (name, file, line) in names.items():
        outside_id = "term:" + folded
        settings = _term_settings(name, rows.get(folded))
        decided = _mapped(inp.mappings.terms, name)
        record = None
        if decided is not None and decided.lower() != "create":
            record = next((t for t in books.terms if t["id"] == decided), None) or books.by_key(books.terms, "name", decided)
            if record is None:
                _problem(built, "blocking", "mapping_not_found", f"mappings.terms maps {name} to {decided!r}, which is no term here",
                         file=file, line=line, subject=name)
                continue
        if record is None and decided is None:
            linked = books.link(outside_id, "term")
            record = next((t for t in books.terms if t["id"] == linked), None) if linked else None
            if record is None:
                record = books.by_key(books.terms, "name", name)
        if record is not None:
            differences = _term_differences(settings, record) if settings else []
            if differences:
                _problem(built, "blocking", "term_settings_differ",
                         f"terms {name!r} here are not the old books' {name!r}: " + "; ".join(differences),
                         "Correct the term here with `term update`, or map the name to a term with the old books' settings "
                         "with mappings.terms.", file=file, line=line, subject=name)
                continue
            _target(built, outside_id, record_id=record["id"], ref=None, record_type="term", name=record["name"],
                    extra={"settings": settings})
            built.mappings["terms"][name] = record["id"]
            if books.link(outside_id, "term") == record["id"]:
                _already(built, "term", outside_id, name, record["id"], "term")
            continue
        if settings is None:
            _problem(built, "warning", "unknown_terms",
                     f"terms {name!r} are not in this company's terms list and neither a terms list nor the name says "
                     "what they mean; documents keep their due dates and come in without terms",
                     "Give the terms list IIF export, add the term, or map it with mappings.terms.", file=file, line=line,
                     subject=name)
            continue
        payload = _term_payload(name.strip(), settings)
        built.steps.append(Step("term", outside_id, name, "term create", payload, "create", record_type="term", ref=outside_id,
                                detail=("due in %d days" % payload["due_days"]) if "due_days" in payload else "date driven"))
        _target(built, outside_id, record_id=None, ref=outside_id, record_type="term", name=name, extra={"settings": settings})
        built.mappings["terms"][name] = "create"


def _term_for(built: Built, name: str | None):
    if not name:
        return None
    return _id_or_ref(built, "term:" + src.key(name))


# ---------------------------------------------------------------- customers and vendors

_CITY = re.compile(r"^(?P<city>.+?),\s*(?P<state>[A-Za-z]{2})\.?\s+(?P<postal>\d{5}(?:-\d{4})?)$")


def _address(lines: list[str], *names: str) -> dict[str, str] | None:
    lines = [line.strip() for line in lines if line and line.strip()]
    folded = {src.key(n) for n in names if n}
    if lines and src.key(lines[0]) in folded:
        lines = lines[1:]
    if not lines:
        return None
    address: dict[str, str] = {}
    country = None
    if lines and lines[-1].upper() in ("USA", "US", "UNITED STATES", "U.S.A."):
        country = lines.pop().upper()
    if lines:
        found = _CITY.match(lines[-1])
        if found:
            lines.pop()
            address.update(city=found.group("city"), state=found.group("state").upper(), postal_code=found.group("postal"))
    if lines:
        address["line1"] = lines[0][:200]
    if len(lines) > 1:
        address["line2"] = ", ".join(lines[1:])[:200]
    if country:
        address["country"] = "US"
    return address or None


def _parties(built: Built, inp, kind: str) -> None:
    books = built.books
    rows = {src.key(r.path): r for r in built.sources.lists[kind]}
    side = "receivable" if kind == "customer" else "payable"
    referenced: dict[str, tuple[str, str, int]] = {}
    for doc in built.sources.documents:
        if doc.side == side:
            referenced.setdefault(src.key(doc.party), (doc.party, doc.file, doc.line))
    for aging in (built.sources.ar_aging if kind == "customer" else built.sources.ap_aging):
        referenced.setdefault(src.key(aging.party), (aging.party, aging.file, aging.line))
    if kind == "vendor":
        for row in built.sources.lists["item"]:
            for column in ("TAXVEND", "PREFVEND"):
                if row.get(column):
                    referenced.setdefault(src.key(row.get(column)), (row.get(column), row.file, row.line))
    paths: dict[str, tuple[str, src.ListRow | None, str | None, int | None]] = {}
    for folded, row in rows.items():
        paths[folded] = (row.path, row, row.file, row.line)
    for folded, (path, file, line) in referenced.items():
        if folded not in paths:
            parts = path.split(":")
            for depth in range(1, len(parts) + 1):
                partial = ":".join(parts[:depth])
                paths.setdefault(src.key(partial), (partial, rows.get(src.key(partial)), file, line))
    agencies = {src.key(r.get("TAXVEND")) for r in built.sources.lists["item"] if r.get("TAXVEND")}
    # A vendor on the old books' 1099 Summary that no vendor list describes comes in eligible for a 1099.
    eligible_1099 = {src.key(r.vendor) for report in built.sources.vendor_1099 for r in report.rows} if kind == "vendor" else set()
    if kind == "vendor":
        for report in built.sources.vendor_1099:
            for r in report.rows:
                referenced.setdefault(src.key(r.vendor), (r.vendor, r.file, r.line))
    name_only = []
    table = books.customers if kind == "customer" else books.vendors
    name_field = "full_name" if kind == "customer" else "name"
    mapping = inp.mappings.customers if kind == "customer" else inp.mappings.vendors
    plural = kind + "s"
    for folded, (path, row, file, line) in sorted(paths.items(), key=lambda item: (item[1][0].count(":"), item[1][0].lower())):
        outside_id = f"{kind}:{folded}"
        if kind == "vendor" and ":" in path:
            _problem(built, "blocking", "vendor_path", f"vendor {path!r} contains a colon", file=file, line=line, subject=path)
            continue
        decided = _mapped(mapping, path)
        record = None
        if decided is not None and decided.lower() != "create":
            record = next((r for r in table if r["id"] == decided), None) or books.by_key(table, name_field, decided)
            if record is None:
                _problem(built, "blocking", "mapping_not_found", f"mappings.{plural} maps {path} to {decided!r}, which is not here",
                         file=file, line=line, subject=path)
                continue
        if record is None and decided is None:
            linked = books.link(outside_id, kind)
            record = next((r for r in table if r["id"] == linked), None) if linked else None
            if record is None:
                record = books.by_key(table, name_field, path)
        if record is not None:
            made = books.link(outside_id, kind) == record["id"]
            _target(built, outside_id, record_id=record["id"], ref=None, record_type=kind, name=record[name_field],
                    extra={"hidden": bool(row and row.get("HIDDEN").upper() == "Y"), "active": record["active"], "made": made})
            if made:
                _already(built, kind, outside_id, path, record["id"], kind)
            built.mappings[plural][path] = record["id"]
            continue
        if row is None:
            name_only.append(path)
        if kind == "customer":
            parent_row = rows.get(src.key(path.rsplit(":", 1)[0])) if ":" in path else None
            payload = _customer_payload(built, path, row, parent_row)
        else:
            payload = _vendor_payload(built, path, row, folded in agencies, on_1099=folded in eligible_1099)
        if payload is None:
            continue
        built.steps.append(Step(kind, outside_id, path, f"{kind} create", payload, "create", record_type=kind, ref=outside_id,
                                detail=("job of " + path.rsplit(":", 1)[0]) if ":" in path else None))
        _target(built, outside_id, record_id=None, ref=outside_id, record_type=kind, name=path,
                extra={"hidden": bool(row and row.get("HIDDEN").upper() == "Y"), "active": True, "made": True})
        built.mappings[plural][path] = "create"
    _look_alikes(built, kind, [path for path, *_ in paths.values()], table, name_field, built.mappings[plural])
    if kind == "customer":
        _tax_item_skips(built)
    if name_only:
        _problem(built, "warning", f"{kind}s_by_name_only",
                 f"{len(name_only)} {plural} come in with their names only, because no {kind} list IIF was given: " + ", ".join(name_only[:8]) + ("…" if len(name_only) > 8 else ""),
                 f"Give the {kind} list IIF export to bring addresses, contacts and terms.")


_FILLER = frozenset(("the", "inc", "incorporated", "llc", "ltd", "co", "corp", "corporation", "company", "and"))
MAX_LOOKALIKES = 25


def _name_tokens(name: str) -> list[str]:
    import re
    import unicodedata
    plain = unicodedata.normalize("NFKD", name.lower().replace("&", " and ")).encode("ascii", "ignore").decode()
    return [word for word in re.findall(r"[a-z0-9]+", plain) if word not in _FILLER]


def _one_edit(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 1 or a == b:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def alike_names(first: str, second: str) -> bool:
    """Two different names that probably mean one party: the same words, or an abbreviation or typo of them.

    `Midland Electric Supply` and `Midland Elec. Supply` (the blind July trial's old books held both)
    are alike; `Route 59 Auto` and `Route 69 Auto` are not, because a number must match exactly.
    """
    a, b = _name_tokens(first), _name_tokens(second)
    if not a or not b:
        return False
    if a == b or "".join(a) == "".join(b):
        return True
    if len(a) != len(b) or len(a) < 2:
        return False
    differs = 0
    for x, y in zip(a, b):
        if x == y:
            continue
        differs += 1
        if any(ch.isdigit() for ch in x + y):
            return False
        short, long_ = sorted((x, y), key=len)
        if not ((len(short) >= 3 and long_.startswith(short)) or (len(short) >= 5 and _one_edit(x, y))):
            return False
    return differs > 0


def _look_alikes(built: Built, kind: str, names: list[str], table: list[dict], name_field: str, ids: dict[str, str]) -> None:
    """Flag top-level names of this move-in that look like another one's, or like a record the company already holds."""
    held = [row[name_field] for row in table if ":" not in row[name_field]]
    coming = sorted({name for name in names if ":" not in name}, key=str.lower)
    known = {src.key(name) for name in coming}
    pairs = []
    for n, first in enumerate(coming):
        for second in coming[n + 1:] + [h for h in held if src.key(h) not in known]:
            if alike_names(first, second) and (ids.get(first) in (None, "create") or ids.get(first) != ids.get(second)):
                pairs.append((first, second))
    for first, second in pairs[:MAX_LOOKALIKES]:
        _problem(built, "warning", f"possible_duplicate_{kind}",
                 f"{kind} {first!r} and {second!r} look like the same {kind}; both stay as they are, so each carries its own balance and history",
                 f"Ask the owner whether they are one {kind}; this move-in does not merge or drop either.", subject=first)
    if len(pairs) > MAX_LOOKALIKES:
        _problem(built, "warning", f"possible_duplicate_{kind}",
                 f"{len(pairs) - MAX_LOOKALIKES} more pairs of {kind}s look alike; only the first {MAX_LOOKALIKES} are listed")


def _money_text(built: Built, row: src.ListRow, column: str) -> str | None:
    value = row.get(column)
    if not value:
        return None
    try:
        minor = src.parse_amount(value, built.books.places)
    except ValueError:
        _problem(built, "warning", "unreadable_amount", f"{row.path}'s {column} {value!r} is not an amount; it is left out",
                 file=row.file, line=row.line, subject=row.path)
        return None
    return _amount_text(built, minor) if minor and minor > 0 else None


def _code_id(built: Built, taxable: bool) -> str | None:
    return next((code["id"] for code in built.books.codes if bool(code["taxable"]) is taxable and code["active"]), None)


def _customer_code(built: Built, row: src.ListRow) -> str | None:
    """The sales tax code a customer's list row names (SALESTAXCODE), else the one its TAXABLE flag means."""
    taxable = row.get("TAXABLE").upper()
    code_name = row.get("SALESTAXCODE")
    code = next((x["id"] for x in built.books.codes if code_name and x["code_key"] == src.key(code_name)), None)
    return code or (_code_id(built, taxable == "Y") if taxable in ("Y", "N") else None)


def _customer_tax_item(built: Built, row: src.ListRow):
    """The sales tax item a customer's list row names (TAXITEM), as it comes in; None when it names none or
    names one that does not come in (a sales tax group, or an item in no list)."""
    name = row.get("TAXITEM")
    if not name:
        return None
    target = built.targets.get("item:" + src.key(name))
    if target is not None:
        return (target["id"] or Ref(target["ref"])) if target.get("item_type") in TAX_TYPES else None
    record = built.books.by_key(built.books.items, "full_name", name)
    return record["id"] if record is not None and record["type"] in TAX_TYPES and record["active"] else None


def _customer_tax(built: Built, row: src.ListRow, parent: src.ListRow | None = None) -> dict[str, Any]:
    """The sales tax code and item the move-in gives a customer, from its list row. A job takes only
    what differs from its customer's, and inherits the rest, as the anchor's jobs do."""
    if not built.books.info.get("sales_tax_enabled"):
        return {}
    found = {"sales_tax_code_id": _customer_code(built, row), "sales_tax_item_id": _customer_tax_item(built, row)}
    if row.get("TAXITEM") and found["sales_tax_item_id"] is None and (parent is None or row.get("TAXITEM") != parent.get("TAXITEM")):
        built.tax_item_skips.append((row.path, row.get("TAXITEM")))
    if parent is not None:
        inherited = {"sales_tax_code_id": _customer_code(built, parent), "sales_tax_item_id": _customer_tax_item(built, parent)}
        found = {k: v for k, v in found.items() if v != inherited[k]}
    return {k: v for k, v in found.items() if v is not None}


def _tax_item_skips(built: Built) -> None:
    if not built.tax_item_skips:
        return
    items = sorted({item for _, item in built.tax_item_skips})
    names = [path for path, _ in built.tax_item_skips]
    _problem(built, "warning", "customer_tax_item_skipped",
             f"{len(names)} customer{'s come' if len(names) != 1 else ' comes'} in without the sales tax item the old books give "
             f"{'them' if len(names) != 1 else 'it'} ({', '.join(items)}), because that item does not come in: "
             + ", ".join(names[:8]) + (f" and {len(names) - 8} more" if len(names) > 8 else "")
             + "; their invoices take the company's default sales tax item",
             "Add the sales tax item, then set it on each customer with `customer update` sales_tax_item_id.")


def _customer_payload(built: Built, path: str, row: src.ListRow | None, parent_row: src.ListRow | None = None) -> dict | None:
    leaf = path.rsplit(":", 1)[-1]
    payload: dict[str, Any] = {"name": leaf}
    if ":" in path:
        parent = built.targets.get("customer:" + src.key(path.rsplit(":", 1)[0]))
        if parent is None:
            return None
        payload["parent_id"] = parent["id"] or Ref(parent["ref"])
    if row is None:
        return payload
    if ":" in path:
        # A job inherits its customer's address and contacts, and its tax settings unless its own differ;
        # it brings its own job facts.
        if parent_row is not None:
            payload.update(_customer_tax(built, row, parent_row))
        status = JOB_STATUS.get(row.get("JOBSTATUS").lower())
        if status:
            payload["job_status"] = status
        if row.get("JOBDESC"):
            payload["job_description"] = row.get("JOBDESC")
        for column, field_ in (("JOBSTART", "job_start"), ("JOBPROJEND", "job_projected_end"), ("JOBEND", "job_end")):
            day = src.parse_date(row.get(column))
            if day:
                payload[field_] = day
        if row.get("NOTEPAD"):
            payload["notes"] = row.get("NOTEPAD")
        return payload
    company = row.get("COMPANYNAME")
    simple = {"company_name": company, "salutation": row.get("SALUTATION"), "first_name": row.get("FIRSTNAME"),
              "middle_name": row.get("MIDINIT"), "last_name": row.get("LASTNAME"), "phone": row.get("PHONE1"),
              "alt_phone": row.get("PHONE2"), "fax": row.get("FAXNUM"), "email": row.get("EMAIL"),
              "contact": row.get("CONT1"), "alt_contact": row.get("CONT2"), "resale_number": row.get("RESALENUM"),
              "notes": row.get("NOTEPAD") or row.get("NOTE")}
    payload.update({k: v for k, v in simple.items() if v})
    person = " ".join(part for part in (row.get("FIRSTNAME"), row.get("LASTNAME")) if part)
    billing = _address([row.get(f"BADDR{n}") for n in range(1, 6)], leaf, company, person)
    if billing:
        payload["billing_address"] = billing
    shipping = _address([row.get(f"SADDR{n}") for n in range(1, 6)], leaf, company, person)
    if shipping and shipping != billing:
        payload["shipping_addresses"] = [{"label": "Ship to", "is_default": True, **shipping}]
    terms = _term_for(built, row.get("TERMS"))
    if terms:
        payload["terms_id"] = terms
    payload.update(_customer_tax(built, row))
    limit = _money_text(built, row, "LIMIT")
    if limit:
        payload["credit_limit"] = limit
    return payload


def _vendor_payload(built: Built, path: str, row: src.ListRow | None, agency: bool, *, on_1099: bool = False) -> dict:
    """A vendor as its list row gives it. `on_1099`: on the old books' 1099 Summary, so a vendor that no
    list row describes comes in eligible for a 1099."""
    payload: dict[str, Any] = {"name": path}
    if agency:
        payload["is_tax_agency"] = True
    if row is None:
        if on_1099:
            payload["eligible_1099"] = True
        return payload
    company = row.get("COMPANYNAME")
    simple = {"company_name": company, "salutation": row.get("SALUTATION"), "first_name": row.get("FIRSTNAME"),
              "middle_name": row.get("MIDINIT"), "last_name": row.get("LASTNAME"), "phone": row.get("PHONE1"),
              "alt_phone": row.get("PHONE2"), "fax": row.get("FAXNUM"), "email": row.get("EMAIL"),
              "contact": row.get("CONT1"), "alt_contact": row.get("CONT2"), "notes": row.get("NOTEPAD") or row.get("NOTE")}
    payload.update({k: v for k, v in simple.items() if v})
    if row.get("PRINTAS") and row.get("PRINTAS") != path:
        payload["print_name_on_check_as"] = row.get("PRINTAS")
    person = " ".join(part for part in (row.get("FIRSTNAME"), row.get("LASTNAME")) if part)
    address = _address([row.get(f"ADDR{n}") for n in range(1, 6)], path, company, person, row.get("PRINTAS"))
    if address:
        payload["address"] = address
    terms = _term_for(built, row.get("TERMS"))
    if terms:
        payload["terms_id"] = terms
    limit = _money_text(built, row, "LIMIT")
    if limit:
        payload["credit_limit"] = limit
    if row.get("1099").upper() == "Y":
        payload["eligible_1099"] = True
    return payload


def _party_for(built: Built, kind: str, path: str):
    return _id_or_ref(built, f"{kind}:{src.key(path)}")


# ---------------------------------------------------------------- items

def _percent(text: str) -> str | None:
    value = text.strip().rstrip("%").strip().lstrip("-")
    return value if re.fullmatch(r"\d{1,3}(\.\d{1,4})?", value) else None


def _items(built: Built, inp) -> None:
    books = built.books
    for row in sorted(built.sources.lists["item"], key=lambda r: (r.path.count(":"), r.line)):
        outside_id = "item:" + src.key(row.path)
        code = row.get("INVITEMTYPE").upper()
        kind = src.ITEM_TYPES.get(code)
        decided = _mapped(inp.mappings.items, row.path)
        record = None
        if decided is not None and decided.lower() != "create":
            record = next((i for i in books.items if i["id"] == decided), None) or books.by_key(books.items, "full_name", decided)
            if record is None:
                _problem(built, "blocking", "mapping_not_found", f"mappings.items maps {row.path} to {decided!r}, which is no item here",
                         file=row.file, line=row.line, subject=row.path)
                continue
        if record is None and decided is None:
            linked = books.link(outside_id, "item")
            record = next((i for i in books.items if i["id"] == linked), None) if linked else None
            if record is None:
                record = books.by_key(books.items, "full_name", row.path)
                if record is not None and kind is not None and record["type"] != kind:
                    _problem(built, "blocking", "type_conflict",
                             f"item {row.path} is {kind.replace('_', ' ')} in the old books but {record['type'].replace('_', ' ')} here",
                             "Map it to another item with mappings.items, or rename one of them.", file=row.file, line=row.line,
                             subject=row.path)
                    continue
        if record is not None:
            made = books.link(outside_id, "item") == record["id"]
            if not made:
                differences = _item_differences(built, row, record)
                if differences:
                    _problem(built, "warning", "item_settings_differ",
                             f"item {row.path} is {record['full_name']} here, which differs from the old books: " + "; ".join(differences),
                             "Update the item here, or map the name to another item with mappings.items.", file=row.file,
                             line=row.line, subject=row.path)
            _target(built, outside_id, record_id=record["id"], ref=None, record_type="item", name=record["full_name"],
                    extra={"item_type": record["type"], "hidden": row.get("HIDDEN").upper() == "Y", "active": record["active"],
                           "made": made})
            if made:
                _already(built, "item", outside_id, row.path, record["id"], "item")
            built.mappings["items"][row.path] = record["id"]
            continue
        if kind is None:
            _problem(built, "warning", "item_skipped", f"item {row.path} has item type {code!r}, which is not brought in",
                     file=row.file, line=row.line, subject=row.path)
            continue
        if kind in SKIPPED_ITEM_TYPES:
            _problem(built, "warning", "item_skipped",
                     f"{kind.replace('_', ' ')} item {row.path} is not brought in: the IIF list does not carry {SKIPPED_ITEM_TYPES[kind]}",
                     "Add it in Bookflow after the move-in.", file=row.file, line=row.line, subject=row.path)
            continue
        payload = _item_payload(built, row, kind)
        if payload is None:
            continue
        built.steps.append(Step("item", outside_id, row.path, "item create", payload, "create", record_type="item",
                                ref=outside_id, detail=kind.replace("_", " ")))
        _target(built, outside_id, record_id=None, ref=outside_id, record_type="item", name=row.path,
                extra={"item_type": kind, "hidden": row.get("HIDDEN").upper() == "Y", "active": True, "made": True})
        built.mappings["items"][row.path] = "create"


def _no_account_list(built: Built) -> None:
    """Items whose accounts are neither here nor in the files, because no chart of accounts was given: said once.

    A plan of the item list alone said "'Service Income' is not an account that comes in" for each
    item, when the cause was the missing chart of accounts export (the cutover trial, third run).
    """
    if not built.sources.lists["item"] or not built.no_account_list:
        return
    named = sorted({account for _, account in built.no_account_list})
    items = [path for path, _ in built.no_account_list]
    shown = ", ".join(items[:8]) + (f" and {len(items) - 8} more" if len(items) > 8 else "")
    _problem(built, "warning", "no_account_list",
             f"no chart of accounts (an IIF file with !ACCNT rows) was given, so {len(items)} item{'s' if len(items) != 1 else ''} "
             f"whose account{'s are' if len(named) != 1 else ' is'} not already here cannot be planned: {shown} "
             f"(account{'s' if len(named) != 1 else ''} {', '.join(repr(n) for n in named[:8])}"
             f"{f' and {len(named) - 8} more' if len(named) > 8 else ''})",
             "Give the chart of accounts IIF export with the item list; those items then come in with their accounts.")


def _source_money(built: Built, text: str) -> int | None:
    """An amount column as minor units, or None when it is blank, a percent or unreadable."""
    if not text or text.strip().endswith("%"):
        return None
    try:
        return src.parse_amount(text, built.books.places)
    except ValueError:
        return None


def _item_differences(built: Built, row: src.ListRow, record: dict[str, Any]) -> list[str]:
    """Price and cost where an item here says something other than the old books' item."""
    found = []
    for column, field_, word in (("PRICE", "price_minor_units", "price"), ("COST", "cost_minor_units", "cost")):
        wanted = _source_money(built, row.get(column))
        mine = record.get(field_)
        if wanted is not None and (wanted or None) != (mine or None):
            found.append(f"{word} {_show(built, mine or 0)} here, {_show(built, wanted)} in the old books")
    return found


def _item_account(built: Built, row: src.ListRow, column: str, allowed: set[str], what: str):
    target = _account_for(built, row.get(column))
    if target is None:
        if row.get(column) and not built.sources.lists["account"]:
            built.no_account_list.append((row.path, row.get(column)))
        elif row.get(column):
            _problem(built, "warning", "item_skipped", f"item {row.path}'s {what} {row.get(column)!r} is not an account that comes in",
                     file=row.file, line=row.line, subject=row.path)
        return False
    if target["type"] not in allowed:
        _problem(built, "warning", "item_skipped",
                 f"item {row.path} posts to {target['name']}, a {target['type'].replace('_', ' ')} account, which Bookflow does not allow as its {what}",
                 "Add the item in Bookflow with an eligible account.", file=row.file, line=row.line, subject=row.path)
        return False
    return target["id"] or Ref(target["ref"])


def _item_payload(built: Built, row: src.ListRow, kind: str) -> dict | None:
    from bookflow.company.items import sold_account_types
    leaf = row.path.rsplit(":", 1)[-1]
    payload: dict[str, Any] = {"name": leaf, "type": kind}
    if ":" in row.path:
        parent = built.targets.get("item:" + src.key(row.path.rsplit(":", 1)[0]))
        if parent is None:
            _problem(built, "warning", "item_skipped", f"item {row.path}'s parent is not brought in", file=row.file, line=row.line, subject=row.path)
            return None
        payload["parent_id"] = parent["id"] or Ref(parent["ref"])
    description = row.get("DESC") or leaf
    taxable = row.get("TAXABLE").upper() == "Y" or row.get("SALESTAXCODE").lower() == "tax"
    code = _code_id(built, taxable)
    if kind == "subtotal":
        return payload | {"description": description}
    if kind == "sales_tax_item":
        rate = _percent(row.get("PRICE"))
        agency = _party_for(built, "vendor", row.get("TAXVEND")) if row.get("TAXVEND") else None
        liability = built.books.roles.get("sales_tax_payable")
        if rate is None or agency is None:
            _problem(built, "warning", "item_skipped", f"sales tax item {row.path} needs a rate and a tax agency vendor",
                     file=row.file, line=row.line, subject=row.path)
            return None
        return payload | {"description": description, "tax_percent": rate, "tax_agency_vendor_id": agency,
                          "liability_account_id": liability["id"]}
    if kind == "discount":
        account = _account_for(built, row.get("ACCNT"))
        price = row.get("PRICE")
        if account is None and row.get("ACCNT") and not built.sources.lists["account"]:
            built.no_account_list.append((row.path, row.get("ACCNT")))
            return None
        if account is None or account["type"] not in ("income", "other_income", "expense", "other_expense"):
            _problem(built, "warning", "item_skipped", f"discount item {row.path} needs an income or expense account",
                     file=row.file, line=row.line, subject=row.path)
            return None
        field_ = "income_account_id" if account["type"] in ("income", "other_income") else "expense_account_id"
        payload |= {"description": description, field_: account["id"] or Ref(account["ref"])}
        if code:
            payload["sales_tax_code_id"] = code
        if price.endswith("%"):
            percent = _percent(price)
            if percent:
                payload["discount_percent"] = percent
        else:
            try:
                minor = src.parse_amount(price, built.books.places)
            except ValueError:
                minor = None
            if minor:
                payload["discount_amount"] = _amount_text(built, minor)
        return payload
    price = _money_text(built, row, "PRICE") or _amount_text(built, 0)
    if row.get("PRICE").endswith("%"):
        if kind == "other_charge" and _percent(row.get("PRICE")):
            payload["charge_percent"] = _percent(row.get("PRICE"))
            price = None
    income = _item_account(built, row, "ACCNT", set(sold_account_types(kind)), "income account")
    if not income:
        return None
    payload |= {"description": description, "income_account_id": income}
    if price is not None:
        payload["price"] = price
    if code:
        payload["sales_tax_code_id"] = code
    cost = _money_text(built, row, "COST")
    vendor = _party_for(built, "vendor", row.get("PREFVEND")) if row.get("PREFVEND") else None
    if kind == "inventory_part":
        cogs = _item_account(built, row, "COGSACCNT", {"cost_of_goods_sold"}, "cost of goods sold account")
        asset = _account_for(built, row.get("ASSETACCNT"))
        if not cogs or asset is None or asset.get("role") != "inventory_asset":
            _problem(built, "warning", "item_skipped",
                     f"inventory item {row.path} needs the inventory asset account and a cost of goods sold account",
                     file=row.file, line=row.line, subject=row.path)
            return None
        payload |= {"cogs_account_id": cogs, "asset_account_id": asset["id"], "cost": cost or _amount_text(built, 0),
                    "purchase_description": row.get("PURCHASEDESC") or description}
        if vendor:
            payload["preferred_vendor_id"] = vendor
        return payload
    if row.get("COGSACCNT"):
        expense = _item_account(built, row, "COGSACCNT", {"expense", "other_expense", "cost_of_goods_sold"}, "expense account")
        if expense:
            payload |= {"purchase_enabled": True, "expense_account_id": expense, "cost": cost or _amount_text(built, 0),
                        "purchase_description": row.get("PURCHASEDESC") or description}
            if vendor:
                payload["preferred_vendor_id"] = vendor
    return payload


# ---------------------------------------------------------------- the clearing account and the opening item

def _clearing(built: Built, inp) -> None:
    books = built.books
    owned = sum(t.get("balance", 0) for key_, t in built.targets.items() if key_.startswith("account:")
                and (t["type"] in DOCUMENT_ACCOUNT_TYPES or t.get("role") == "inventory_asset"))
    undeposited = any(t.get("balance") for key_, t in built.targets.items() if key_.startswith("account:")
                      and t.get("role") == "undeposited_funds")
    needed = bool(built.sources.documents or built.sources.stock or owned or undeposited
                  or built.sources.open_items or built.sources.reconciliations)
    if inp.clearing_account:
        record = books.account(inp.clearing_account)
        if record is None or record["type"] not in CLEARING_TYPES or record["system_role"]:
            _problem(built, "blocking", "clearing_account_unusable",
                     f"clearing_account {inp.clearing_account!r} is not an other current asset, fixed asset or other asset account without a system role",
                     "Name such an account, or omit clearing_account to use Cutover Clearing.")
            return
        built.clearing_id = record["id"]
        return
    linked = books.link("clearing-account", "account")
    record = next((a for a in books.accounts if a["id"] == linked), None) if linked else books.by_key(books.accounts, "full_name", CLEARING_NAME)
    if record is not None:
        if record["type"] not in CLEARING_TYPES or record["system_role"]:
            _problem(built, "blocking", "clearing_account_unusable",
                     f"an account named {CLEARING_NAME} exists and is a {record['type'].replace('_', ' ')} account",
                     "Rename it, or name another account as clearing_account.")
            return
        built.clearing_id = record["id"]
        if linked == record["id"]:
            _already(built, "account", "clearing-account", CLEARING_NAME, record["id"], "account")
        return
    if not needed:
        return
    built.steps.append(Step("account", "clearing-account", CLEARING_NAME, "account create",
                            {"name": CLEARING_NAME, "type": "other_current_asset",
                             "description": "Opening balances from the old books post against this account; it is 0.00 once the cutover ties."},
                            "create", record_type="account", ref="clearing-account", detail="other current asset, nets to 0.00"))
    built.clearing_ref = "clearing-account"



def _clearing_value(built: Built):
    return built.clearing_id or (Ref(built.clearing_ref) if built.clearing_ref else None)


def _opening_item(built: Built):
    books = built.books
    if not built.targets.get("opening-balance-item"):
        linked = books.link("opening-balance-item", "item")
        record = next((i for i in books.items if i["id"] == linked), None) if linked else books.by_key(books.items, "full_name", OPENING_ITEM)
        clearing = _clearing_value(built)
        if record is not None:
            if record["type"] != "other_charge" or (built.clearing_id and record["income_account_id"] != built.clearing_id):
                _problem(built, "blocking", "opening_item_taken",
                         f"an item named {OPENING_ITEM} exists and does not post to the clearing account",
                         "Rename that item; the move-in makes its own.")
                return None
            _target(built, "opening-balance-item", record_id=record["id"], ref=None, record_type="item", name=OPENING_ITEM)
            if linked == record["id"]:
                _already(built, "item", "opening-balance-item", OPENING_ITEM, record["id"], "item")
        else:
            if clearing is None:
                return None
            payload = {"name": OPENING_ITEM, "type": "other_charge", "description": "Open balance brought in from the old books",
                       "price": _amount_text(built, 0), "income_account_id": clearing}
            code = _code_id(built, False)
            if code:
                payload["sales_tax_code_id"] = code
            built.steps.append(Step("item", "opening-balance-item", OPENING_ITEM, "item create", payload, "create",
                                    record_type="item", ref="opening-balance-item", detail="other charge on the clearing account"))
            _target(built, "opening-balance-item", record_id=None, ref="opening-balance-item", record_type="item", name=OPENING_ITEM)
    return _id_or_ref(built, "opening-balance-item")


# ---------------------------------------------------------------- open documents

def _documents(built: Built, inp) -> None:
    books = built.books
    occurrences: dict[str, int] = defaultdict(int)
    seen: dict[tuple, src.OpenDocument] = {}
    receivable = payable = 0
    if any(doc.side == "receivable" and doc.open_balance for doc in built.sources.documents):
        _opening_item(built)
    for doc in sorted(built.sources.documents, key=lambda d: (d.side, d.date or "", d.party.lower(), d.number or "", d.line)):
        if doc.open_balance == 0 or doc.date is None or doc.date > inp.as_of:
            continue
        same = (doc.side, src.key(doc.type), src.key(doc.party), doc.number, doc.date, doc.open_balance)
        if same in seen and seen[same].file != doc.file:
            _problem(built, "blocking", "duplicate_document",
                     f"{doc.type} {doc.number or ''} for {doc.party} on {doc.date} is in both {seen[same].file} and {doc.file}",
                     "Give each open document once.", file=doc.file, line=doc.line, subject=doc.party)
            continue
        seen.setdefault(same, doc)
        base = f"{doc.side}:{src.key(doc.type)}:{src.key(doc.party)}:{doc.number or ''}:{doc.date}"
        occurrences[base] += 1
        outside_id = f"{base}:{occurrences[base]}"
        if doc.side == "receivable":
            receivable += doc.open_balance
        else:
            payable += doc.open_balance
        kind = {("receivable", True): "invoice", ("receivable", False): "credit_memo",
                ("payable", True): "bill", ("payable", False): "vendor_credit"}[(doc.side, doc.open_balance > 0)]
        party_kind = "customer" if doc.side == "receivable" else "vendor"
        party = _party_for(built, party_kind, doc.party)
        label = f"{doc.type} {doc.number}" if doc.number else doc.type
        linked = books.link(outside_id, "transaction")
        if linked:
            built.steps.append(Step(kind, outside_id, f"{label} · {doc.party}", None, {}, "already_in", linked,
                                    amount=abs(doc.open_balance), date=doc.date))
            if books.totals.get(linked) not in (None, abs(doc.open_balance)):
                _problem(built, "warning", "open_balance_changed",
                         f"{label} for {doc.party} came in at {_show(built, books.totals[linked])} and the old books now say {_show(built, abs(doc.open_balance))}",
                         "Record what changed (a payment or a credit) in Bookflow.", file=doc.file, line=doc.line, subject=doc.party)
            continue
        if party is None:
            continue
        amount = _amount_text(built, abs(doc.open_balance))
        memo = f"Open {'balance' if doc.open_balance > 0 else 'credit'} from the old books: {label} dated {doc.date}"
        if doc.po_number:
            memo += f", P.O. {doc.po_number}"
        if doc.side == "receivable":
            item = _opening_item(built)
            if item is None:
                continue
            ar = _control(built, "accounts_receivable")
            line = {"item": item, "quantity": "1", "unit_price": amount, "description": memo}
            code = _code_id(built, False)
            if code and built.books.info.get("sales_tax_enabled"):
                line["tax_code"] = code
            payload: dict[str, Any] = {"customer": party, "date": doc.date, "memo": memo, "lines": [line]}
            if ar:
                payload["ar_account"] = ar
            if kind == "invoice":
                payload["number"] = doc.number
                payload["due_date"] = doc.due_date or doc.date
                terms = _term_for(built, doc.terms)
                if terms:
                    payload["terms"] = terms
                if doc.po_number:
                    payload["customer_purchase_order"] = doc.po_number[:100]
            elif src.key(doc.type) == "credit memo" and doc.number:
                payload["number"] = doc.number
            command = "invoice post" if kind == "invoice" else "credit-memo post"
        else:
            clearing = _clearing_value(built)
            if clearing is None:
                continue
            ap = _control(built, "accounts_payable")
            payload = {"vendor": party, "date": doc.date, "memo": memo,
                       "expenses": [{"account": clearing, "amount": amount, "memo": memo}]}
            if ap:
                payload["ap_account"] = ap
            if doc.number:
                payload["supplier_reference"] = doc.number[:100]
            if kind == "bill":
                payload["due_date"] = doc.due_date or doc.date
                terms = _term_for(built, doc.terms)
                if terms:
                    payload["terms"] = terms
            command = "bill post" if kind == "bill" else "vendor-credit post"
        klass = built.books.by_key(built.books.classes, "full_name", doc.class_name) if doc.class_name else None
        if doc.class_name and klass is None:
            _problem(built, "warning", "unknown_class", f"class {doc.class_name!r} on {label} is not in this company's class list; it comes in without a class",
                     file=doc.file, line=doc.line, subject=doc.class_name)
        elif klass is not None:
            payload["class_id"] = klass["id"]
        built.steps.append(Step(kind, outside_id, f"{label} · {doc.party}", command, payload, "create", amount=abs(doc.open_balance),
                                date=doc.date, detail=f"due {payload['due_date']}" if payload.get("due_date") else None))
    _tie_check(built, "receivables", "accounts_receivable", receivable, bool(built.sources.receivable_files), "open_invoices")
    _tie_check(built, "payables", "accounts_payable", payable, bool(built.sources.payable_files), "unpaid_bills")


def _control(built: Built, role: str):
    """The receivable or payable account the old books' control account stands for, unless it is the default one."""
    for target in built.targets.values():
        if target["type"] == role and target.get("balance") and target["id"] and target["id"] != built.books.roles[role]["id"]:
            return target["id"]
    return None


def _tie_check(built: Built, name: str, account_type: str, documents: int, given: bool, export: str) -> None:
    sign = 1 if account_type == "accounts_receivable" else -1
    trial = sign * sum(t.get("balance", 0) for t in built.targets.values() if t["type"] == account_type)
    if not built.sources.trial_balances:
        return  # nothing to tie to; the trial balance's own exception says so
    built.checks.append((name, trial, documents))
    if trial and not given:
        what = "Open Invoices" if export == "open_invoices" else "Unpaid Bills Detail (Dates: All)"
        _problem(built, "blocking", f"missing_{export}",
                 f"the trial balance carries {account_type.replace('_', ' ')} of {_show(built, trial)} and no {what} file was given",
                 f"Export the {what} report as of the cutover date to CSV.")
    elif trial != documents:
        what = "open invoices and credits" if export == "open_invoices" else "unpaid bills and credits"
        _problem(built, "blocking", f"{name}_do_not_tie",
                 f"the {what} add up to {_show(built, documents)} but the trial balance carries {account_type.replace('_', ' ')} of {_show(built, trial)}; difference {_show(built, trial - documents)}",
                 "Export both reports as of the cutover date" + (" (Unpaid Bills Detail with Dates: All, so bills not yet due are included)" if export == "unpaid_bills" else "")
                 + ". Receivables and payables come in only as documents, never in the opening journal.")


# ---------------------------------------------------------------- opening stock

def _stock(built: Built, inp) -> None:
    trial = sum(t.get("balance", 0) for t in built.targets.values() if t.get("role") == "inventory_asset")
    total = sum(row.value for row in built.sources.stock)
    if built.sources.trial_balances:
        built.checks.append(("inventory", trial, total))
    if trial and not built.sources.stock_files:
        _problem(built, "blocking", "missing_inventory_valuation",
                 f"the trial balance carries Inventory Asset of {_show(built, trial)} and no Inventory Valuation Summary was given",
                 "Export the Inventory Valuation Summary as of the cutover date to CSV; each item's stock comes in by item.")
        return
    if trial != total and built.sources.trial_balances:
        _problem(built, "blocking", "inventory_does_not_tie",
                 f"the items' asset values add up to {_show(built, total)} but the trial balance carries Inventory Asset of {_show(built, trial)}",
                 "Export both reports as of the cutover date.")
    for row in built.sources.stock:
        if row.value == 0 and not row.quantity.strip("-0."):
            continue
        target = built.targets.get("item:" + src.key(row.item))
        outside_id = "inventory:" + src.key(row.item)
        if target is None or target.get("item_type") != "inventory_part":
            _problem(built, "blocking", "unmapped_item", f"{row.item} has stock in the old books but is no inventory item that comes in",
                     "Give the item list IIF export, or map it with mappings.items.", file=row.file, line=row.line, subject=row.item)
            continue
        if row.quantity.startswith("-") or row.value < 0:
            _problem(built, "blocking", "negative_stock", f"{row.item} has negative stock in the old books",
                     "Correct it in the old books before the cutover.", file=row.file, line=row.line, subject=row.item)
            continue
        linked = built.books.link(outside_id, "transaction")
        payload = {"item": target["id"] or Ref(target["ref"]), "date": inp.as_of, "adjustment_account": _clearing_value(built),
                   "quantity_change": row.quantity, "value_change": _amount_text(built, row.value),
                   "memo": "Opening stock from the old books"}
        built.steps.append(Step("inventory_adjustment", outside_id, row.item, "inventory adjust", payload,
                                "already_in" if linked else "create", linked, amount=row.value, date=inp.as_of,
                                detail=f"{row.quantity} on hand"))


# ---------------------------------------------------------------- bank and card accounts: the last reconciliation

BANK_TYPES = frozenset({"bank", "credit_card"})


def _natural(detail_card: bool, debit: int) -> int:
    """An amount in the account's own sign: money in the bank, or what is owed on the card."""
    return -debit if detail_card else debit


def _signed_text(built: Built, minor: int) -> str:
    return ("-" if minor < 0 else "") + _amount_text(built, abs(minor))


def _bank_target(built: Built, path: str, label: str, file: str, line: int | None, what: str):
    target = _account_for(built, path)
    if target is None:
        _problem(built, "blocking", "unmapped_account",
                 f"{label} is in {file} but in no account list given and on no trial balance line, and no account here has that name",
                 "Give the chart of accounts IIF export, or map it with mappings.accounts.", file=file, line=line, subject=label)
    elif target["type"] not in BANK_TYPES and not (what == "items" and target.get("role") == "undeposited_funds"):
        _problem(built, "blocking", "not_a_bank_account",
                 f"{label} in {file} stands for {target['name']}, a {target['type'].replace('_', ' ')} account: a reconciliation "
                 "and its uncleared items belong to a bank or credit card account" + (", or receipts to Undeposited Funds" if what == "items" else ""),
                 "Leave the file out, or map the account to the bank or card account it is with mappings.accounts.",
                 file=file, line=line, subject=label)
        return None
    return target


def _bank_items(built: Built, inp) -> None:
    """Each bank and card account given its Reconciliation Summary or its uncleared items.

    The opening journal carries the statement's ending balance rather than the trial balance's
    figure, and each check, deposit and charge the statement had not shown comes in as its own
    document against the clearing account, dated and numbered as in the old books, with its payee.
    So the account still ties to the trial balance, and the first reconciliation clears the items
    one by one. Without either file an account comes in as one amount, as before, and a note says so.
    """
    sources = built.sources
    summaries: dict[str, src.ReconciliationSummary] = {}
    for summary in sources.reconciliations:
        target = _bank_target(built, summary.path, summary.label, summary.file, summary.line, "summary")
        if target is None:
            continue
        key_ = "account:" + src.key(summary.path)
        if key_ in summaries:
            _problem(built, "blocking", "several_reconciliation_summaries",
                     f"{summary.label} has more than one Reconciliation Summary ({summaries[key_].file} and {summary.file})",
                     "Give only the summary of the last statement the old books reconciled.", file=summary.file,
                     subject=summary.label)
            continue
        if summary.statement_date and summary.statement_date > inp.as_of:
            _problem(built, "blocking", "statement_after_cutover",
                     f"{summary.label}'s reconciled statement is dated {summary.statement_date}, after the cutover date",
                     "Give the summary of the last statement dated on or before the cutover date.", file=summary.file,
                     subject=summary.label)
            continue
        summaries[key_] = summary
    items: dict[str, list[src.OpenItem]] = defaultdict(list)
    late: dict[str, int] = defaultdict(int)
    for item in sources.open_items:
        target = _bank_target(built, item.path, item.label, item.file, item.line, "items")
        if target is None:
            continue
        if item.date is None:
            _problem(built, "blocking", "item_without_date", f"{item.type} {item.num or ''} under {item.label} has no date",
                     file=item.file, line=item.line, subject=item.label)
            continue
        if item.date > inp.as_of:
            late[item.file] += 1
            continue
        if target["type"] in BANK_TYPES:  # a receipt in Undeposited Funds is `_undeposited`'s
            items["account:" + src.key(item.path)].append(item)
    for file, count in late.items():
        _problem(built, "warning", "items_after_cutover",
                 f"{count} row{'s' if count != 1 else ''} of {file} {'are' if count != 1 else 'is'} dated after the cutover date "
                 "and stay out: they belong to the new books", file=file)
    for key_ in sorted(set(summaries) | set(items), key=lambda k: built.targets[k]["name"].lower()):
        target, summary, rows = built.targets[key_], summaries.get(key_), items.get(key_, [])
        card = target["type"] == "credit_card"
        label = summary.label if summary else rows[0].label
        trial = target.get("balance", 0)
        uncleared = sum(item.amount for item in rows)
        show = lambda debit: _show(built, _natural(card, debit))
        if summary is not None:
            statement = _natural(card, summary.cleared_balance)  # debit positive
            if not rows and summary.uncleared_total and not sources.open_item_files:
                _problem(built, "blocking", "missing_uncleared_items",
                         f"{label}'s Reconciliation Summary lists {summary.uncleared_count or 'some'} uncleared transactions "
                         f"({_show(built, summary.uncleared_total)}) and no uncleared items were given",
                         "Export the transactions that had not cleared (Reports > Custom Reports > Transaction Detail, Cleared: No, "
                         "as of the cutover date, totalled by account) to CSV, or leave out the Reconciliation Summary to bring the "
                         "account in as one amount.", file=summary.file, subject=label)
                continue
            if statement + uncleared != trial:
                _problem(built, "blocking", "uncleared_do_not_tie",
                         f"{label}: the statement's ending balance ({show(statement)}) and the uncleared items ({show(uncleared)}) come to "
                         f"{show(statement + uncleared)}, but the trial balance carries {show(trial)}; difference {show(trial - statement - uncleared)}",
                         "Export the Reconciliation Summary of the last statement the old books reconciled and the uncleared "
                         "items as of the cutover date, both from the same books.", file=summary.file, subject=label)
                continue
            before = sum(_natural(card, item.amount) for item in rows if item.date <= summary.statement_date)
            if before != summary.uncleared_total:
                _problem(built, "blocking", "uncleared_do_not_tie",
                         f"{label}'s Reconciliation Summary has {_show(built, summary.uncleared_total)} uncleared on "
                         f"{summary.statement_date}, but the uncleared items dated by then come to {_show(built, before)}",
                         "Export the Reconciliation Summary of the last statement the old books reconciled; an older one "
                         "lists items a later reconciliation cleared.", file=summary.file, subject=label)
                continue
        else:
            statement = trial - uncleared
        detail = BankDetail(target, label, card, summary, rows, statement)
        built.bank[key_] = detail
        occurrences: dict[str, int] = defaultdict(int)
        for item in sorted(rows, key=lambda i: (i.date, i.num or "", i.line)):
            base = f"uncleared:{src.key(item.path)}:{src.key(item.type)}:{item.num or ''}:{item.date}:{item.amount}"
            occurrences[base] += 1
            step = _uncleared_step(built, detail, item, f"{base}:{occurrences[base]}")
            if step is not None:
                built.steps.append(step)
                detail.item_steps.append(step)
        if summary is None:
            _problem(built, "note", "no_reconciliation_summary",
                     f"{label}'s uncleared items come in one by one, but without its Reconciliation Summary the move-in does not "
                     "know the last statement, so the first reconciliation starts with `reconcile opening start`",
                     "Give the account's Reconciliation Summary to bring the last reconciliation too.", subject=label)
    lumps = [t["name"] for key_, t in built.targets.items() if key_.startswith("account:") and t["type"] in BANK_TYPES
             and t.get("balance") and key_ not in built.bank]
    if lumps and sources.trial_balances:
        _problem(built, "note", "no_reconciliation",
                 f"{', '.join(sorted(lumps))} come{'s in as one opening amount' if len(lumps) == 1 else ' in as one opening amount each'}, "
                 "with no uncleared items and no last reconciliation, so a first reconciliation starts with `reconcile opening start`",
                 "Give each account's Reconciliation Summary and the uncleared items (Transaction Detail, Cleared: No) to bring "
                 "the last reconciliation and each uncleared check, deposit and charge.")
    if built.payee_skips:
        names = sorted(set(built.payee_skips))
        _problem(built, "warning", "payee_not_found",
                 f"{len(names)} payee{'s' if len(names) != 1 else ''} of uncleared items {'are' if len(names) != 1 else 'is'} in "
                 f"no name list that comes in, so {'they come' if len(names) != 1 else 'it comes'} in the item's memo: "
                 + ", ".join(names[:8]) + (f" and {len(names) - 8} more" if len(names) > 8 else ""),
                 "Give the customer and vendor list IIF exports to bring the payees.")


def _payee(built: Built, name: str | None, money_in: bool):
    """(name_type, id or Ref) of the party an item names: a vendor for money out, a customer for money
    in, either one otherwise, then an employee or other name here; None when no list has it."""
    if not name:
        return None
    order = ("customer", "vendor") if money_in else ("vendor", "customer")
    for kind in order:
        value = _party_for(built, kind, name)
        if value is not None:
            return kind, value
    for kind, rows in (("employee", built.books.employees), ("other_name", built.books.other_names)):
        record = built.books.by_key(rows, "name", name)
        if record is not None and record["active"]:
            return kind, record["id"]
    built.payee_skips.append(name)
    return None


def _uncleared_step(built: Built, detail: BankDetail, item: src.OpenItem, outside_id: str) -> Step | None:
    """One uncleared check, deposit or charge as its own document against the clearing account."""
    clearing = _clearing_value(built)
    if clearing is None:
        return None
    account = detail.target["id"] or Ref(detail.target["ref"])
    amount = _amount_text(built, abs(item.amount))
    out = item.amount < 0  # money out of the bank, or a charge on the card
    party = _payee(built, item.name, money_in=not out)
    named = f"{item.type} {item.num}" if item.num else item.type
    memo = item.memo or f"{named} from the old books"
    if party is None and item.name:
        memo = f"{item.name}: {memo}"
    line_memo = f"Not yet on a statement at the move-in: {named} dated {item.date}"[:2000]
    expenses = [{"account": clearing, "amount": amount, "memo": line_memo}]
    payee = {"name_type": party[0], "name_id": party[1]} if party else None
    if not detail.card and out and item.num and item.num.isdigit():
        command, what = "check post", f"check {item.num}"
        payload = {"account": account, "date": item.date, "number": item.num, "amount": amount, "memo": memo[:2000],
                   "expenses": expenses, **({"pay_to": payee} if payee else {})}
    elif detail.card and out:
        command, what = "card-charge post", "card charge"
        payload = {"account": account, "date": item.date, "amount": amount, "expenses": expenses, **({"pay_to": payee} if payee else {}),
                   "memo": (f"{memo} (ref {item.num})" if item.num else memo)[:2000]}
    elif detail.card and re.search(r"credit|refund", item.type, re.IGNORECASE):
        command, what = "card-credit post", "card credit"
        payload = {"account": account, "date": item.date, "amount": amount, "expenses": expenses, **({"pay_to": payee} if payee else {}),
                   "memo": (f"{memo} (ref {item.num})" if item.num else memo)[:2000]}
    else:
        # A deposit, a transfer, a payment without a check number or to the card: a register entry.
        command = "register post"
        what = ("deposit" if not out else "payment") if not detail.card else ("charge" if out else "payment or credit")
        payload = {"account": account, "date": item.date, "amount": amount, "memo": memo[:2000], "category": clearing,
                   "direction": ("increase" if out else "decrease") if detail.card else ("decrease" if out else "increase")}
        if item.num:
            payload["number"] = item.num[:64]
        if payee:
            payload["payee"] = payee
    linked = built.books.link(outside_id, "transaction")
    return Step("uncleared_item", outside_id, f"{named} · {item.name or detail.label}", None if linked else command, payload,
                "already_in" if linked else "create", linked, amount=abs(item.amount), date=item.date,
                detail=f"{what} on {detail.target['name']}, not yet on a statement", clearing=-item.amount,
                extra={"account": "account:" + src.key(item.path), "payee": party, "bank": not detail.card})


# ---------------------------------------------------------------- Undeposited Funds: receipts waiting to be deposited

OPENING_CUSTOMER = "Opening balance"


def _opening_customer(built: Built):
    """The customer a receipt is from when the old books do not say: made on first use."""
    outside_id = "opening-balance-customer"
    if outside_id not in built.targets:
        books = built.books
        linked = books.link(outside_id, "customer")
        record = (next((r for r in books.customers if r["id"] == linked), None) if linked
                  else books.by_key(books.customers, "full_name", OPENING_CUSTOMER))
        if record is not None:
            _target(built, outside_id, record_id=record["id"], ref=None, record_type="customer", name=record["full_name"])
            if linked == record["id"]:
                _already(built, "customer", outside_id, OPENING_CUSTOMER, record["id"], "customer")
        else:
            built.steps.append(Step("customer", outside_id, OPENING_CUSTOMER, "customer create",
                                    {"name": OPENING_CUSTOMER, "notes": "Receipts brought in from the old books' Undeposited Funds"},
                                    "create", record_type="customer", ref=outside_id, detail="receipts waiting for deposit"))
            _target(built, outside_id, record_id=None, ref=outside_id, record_type="customer", name=OPENING_CUSTOMER)
    return _id_or_ref(built, outside_id)


def _undeposited(built: Built, inp) -> None:
    """Undeposited Funds as the receipts waiting in it, so Make Deposits picks them.

    With the account's QuickReport (Cleared: No) each receipt comes in as a sales receipt into
    Undeposited Funds from its customer, dated and referenced as in the old books, of one `Opening
    balance` line on the clearing account. Without it the balance comes in as one such receipt.
    Either way the opening journal leaves the account out.
    """
    funds = {key_: t for key_, t in built.targets.items() if key_.startswith("account:") and t.get("role") == "undeposited_funds"}
    rows: dict[str, list[src.OpenItem]] = defaultdict(list)
    for item in built.sources.open_items:
        key_ = "account:" + src.key(item.path)
        if key_ in funds and item.date is not None and item.date <= inp.as_of:
            rows[key_].append(item)
    for key_, target in sorted(funds.items(), key=lambda pair: pair[1]["name"].lower()):
        trial, given = target.get("balance", 0), rows.get(key_, [])
        label = target.get("label") or target["name"]
        if given:
            found = sum(item.amount for item in given)
            if found != trial:
                _problem(built, "blocking", "undeposited_do_not_tie",
                         f"the receipts waiting in {label} add up to {_show(built, found)}, but the trial balance carries "
                         f"{_show(built, trial)}; difference {_show(built, trial - found)}",
                         "Export Undeposited Funds' QuickReport (Dates: All, Cleared: No) and the trial balance from the same books.",
                         file=given[0].file, subject=label)
                continue
            if any(item.amount <= 0 for item in given):
                bad = next(item for item in given if item.amount <= 0)
                _problem(built, "blocking", "undeposited_not_a_receipt",
                         f"{bad.type} {bad.num or ''} in {label} takes money out of Undeposited Funds; only receipts come in as receipts",
                         "Deposit or clear it in the old books before the cutover, and export the report again.",
                         file=bad.file, line=bad.line, subject=label)
                continue
        elif trial <= 0:
            continue  # nothing waiting, or a credit balance the opening journal carries as before
        if _opening_item(built) is None or _clearing_value(built) is None:
            continue
        built.undeposited.add(key_)
        occurrences: dict[str, int] = defaultdict(int)
        if not given:
            _problem(built, "warning", "undeposited_funds",
                     f"Undeposited Funds holds {_show(built, trial)} in the old books and the receipts in it were not given: it "
                     f"comes in as one receipt from {OPENING_CUSTOMER!r} that Make Deposits picks whole",
                     "Give Undeposited Funds' QuickReport (Dates: All, Cleared: No; kind `uncleared`) to bring each receipt "
                     "on its own, so each deposit picks the checks it holds.", subject=label)
            built.steps.append(_receipt_step(built, target, None, trial, inp.as_of, f"undeposited:{inp.as_of}:1"))
            continue
        for item in sorted(given, key=lambda i: (i.date, i.num or "", i.line)):
            base = f"undeposited:{src.key(item.type)}:{src.key(item.name or '')}:{item.num or ''}:{item.date}:{item.amount}"
            occurrences[base] += 1
            built.steps.append(_receipt_step(built, target, item, item.amount, item.date, f"{base}:{occurrences[base]}"))
    if built.payer_skips:
        names = sorted(set(built.payer_skips))
        _problem(built, "warning", "receipt_payer_unknown",
                 f"{len(names)} receipt{'s' if len(names) != 1 else ''} waiting in Undeposited Funds {'name' if len(names) != 1 else 'names'} "
                 f"a payer that is no customer, so {'they come' if len(names) != 1 else 'it comes'} in from {OPENING_CUSTOMER!r} with "
                 "the payer in the memo: " + ", ".join(names[:8]) + (f" and {len(names) - 8} more" if len(names) > 8 else ""),
                 "Give the customer list IIF export, or change the receipt's customer with `sales-receipt update` before depositing it.")


def _payment_method(built: Built, number: str | None) -> str | None:
    """A receipt's payment method: Check when it carries a check number, else Other, as the old books'
    report does not say; whichever of them the company has, else its first active one."""
    names = ("Check", "Other") if number and number.isdigit() else ("Other", "Check")
    for name in names:
        method = built.books.by_key(built.books.payment_methods, "name", name)
        if method is not None and method["active"]:
            return method["id"]
    return next((method["id"] for method in built.books.payment_methods if method["active"]), None)


def _receipt_step(built: Built, target: dict, item: src.OpenItem | None, amount: int, date: str, outside_id: str) -> Step:
    """A receipt waiting in Undeposited Funds: a sales receipt of one `Opening balance` line."""
    customer = _party_for(built, "customer", item.name) if item is not None and item.name else None
    payer = item.name if item is not None and item.name else None
    memo = (item.memo if item is not None and item.memo else
            f"{item.type}{' ' + item.num if item.num else ''} received before the move-in" if item is not None else
            f"Undeposited Funds from the old books as of {date}")
    if customer is None:
        customer = _opening_customer(built)
        if payer:
            memo = f"{payer}: {memo}"
            built.payer_skips.append(payer)
    text = _amount_text(built, amount)
    line = {"item": _opening_item(built), "quantity": "1", "unit_price": text,
            "description": "Waiting for deposit at the move-in" + (f": {item.type} {item.num or ''}".rstrip() if item is not None else "")}
    code = _code_id(built, False)
    if code and built.books.info.get("sales_tax_enabled"):
        line["tax_code"] = code
    payload: dict[str, Any] = {"customer": customer, "date": date, "deposit_to": target["id"] or Ref(target["ref"]),
                               "memo": memo[:2000], "lines": [line]}
    if item is not None and item.num:
        payload["payment_reference"] = item.num[:128]
    method = _payment_method(built, item.num if item is not None else None)
    if method:
        payload["payment_method"] = method
    linked = built.books.link(outside_id, "transaction")
    name = f"{item.type} {item.num or ''}".rstrip() + f" · {payer or OPENING_CUSTOMER}" if item is not None else f"Undeposited Funds · {OPENING_CUSTOMER}"
    return Step("undeposited_receipt", outside_id, name, None if linked else "sales-receipt post", payload,
                "already_in" if linked else "create", linked, amount=amount, date=date,
                detail="waiting in Undeposited Funds for a deposit", clearing=-amount,
                extra={"account": "account:" + src.key(item.path) if item is not None else None, "funds": target["name"]})


# ---------------------------------------------------------------- the opening sales tax

def _tax_agencies(built: Built) -> list[str]:
    """The old books' sales tax agencies: the vendors their sales tax items are owed to."""
    return sorted({row.get("TAXVEND") for row in built.sources.lists["item"]
                   if row.get("INVITEMTYPE").upper() in ("STAX", "COMPTAX") and row.get("TAXVEND")})


def _opening_sales_tax(built: Built, inp, target: dict[str, Any], row: src.TrialBalanceRow) -> bool:
    """How the old books' Sales Tax Payable balance comes in. True: as a line of the opening journal.

    When the old books owe exactly one sales tax agency, it comes in as that agency's own balance:
    one `sales-tax adjust` dated `as_of` against the clearing account (an increase for what is owed,
    a reduction for a debit balance), so `sales-tax liability` shows it under the agency and
    `sales-tax pay` can pay it. The clearing account still nets to 0.00: the opening journal's
    clearing line grows by exactly what the adjustment posts back to it.

    Otherwise -- several agencies or none in the source, an agency that is an existing vendor not
    flagged as a tax agency, no clearing account, or a company on the payment_receipt sales tax
    basis -- it stays a line of the opening journal, not tied to an agency, and the plan says how
    to pay it.
    """
    agencies = _tax_agencies(built)
    owed = -row.net  # what the old books owe the agency: Sales Tax Payable is credit-normal
    agency = _party_for(built, "vendor", agencies[0]) if len(agencies) == 1 else None
    clearing = _clearing_value(built)
    flagged = agency is not None and (isinstance(agency, Ref) or any(
        v["id"] == agency and v["is_tax_agency"] and v["active"] for v in built.books.vendors))
    if flagged and clearing is not None and built.books.info.get("sales_tax_liability_basis") == "invoice_date":
        outside_id = f"sales-tax:{inp.as_of}"
        linked = built.books.link(outside_id, "transaction")
        payload = {"agency": agency, "date": inp.as_of, "adjustment_account": clearing,
                   "direction": "increase" if owed > 0 else "reduce", "amount": _amount_text(built, abs(owed)),
                   "memo": f"Opening sales tax owed to {agencies[0]} from the old books as of {inp.as_of}"}
        built.steps.append(Step("sales_tax_adjustment", outside_id, f"Opening sales tax · {agencies[0]}", "sales-tax adjust",
                                payload, "already_in" if linked else "create", linked, amount=abs(owed), date=inp.as_of,
                                detail=f"{'owed to' if owed > 0 else 'credit with'} {agencies[0]}"))
        return False
    to = f" to {agencies[0]}" if len(agencies) == 1 else " to the agency"
    _problem(built, "warning", "sales_tax_payable",
             f"The old books owe {_show(built, owed)} of sales tax. It comes in as one opening amount on Sales Tax "
             f"Payable that is not tied to a tax agency, so `sales-tax pay` cannot pay it and `sales-tax liability` lists "
             f"it as not attributed to an agency.",
             f"Pay it{to} with a check (or a journal entry) whose account is Sales Tax Payable.",
             file=row.file, line=row.line, subject=row.label)
    return True


# ---------------------------------------------------------------- the opening journal

@dataclass
class _Line:
    target: dict[str, Any]
    label: str  # the old-books account the line carries
    net: int  # debit positive
    key: str  # the account's outside id


def _journal(built: Built, inp, number: str | None) -> None:
    """The opening journal: every trial balance account the documents, stock and receipts do not carry.

    A bank or card account brought in from its last reconciliation carries the statement's ending
    balance, not the trial balance's figure: its uncleared items carry the rest. When that statement
    is dated before the cutover, its balance goes in a journal of its own dated the statement date,
    so the first reconciliation in Bookflow starts from it, as the anchor's opening balance does.
    """
    lines: list[_Line] = []
    dated: dict[str, list[_Line]] = defaultdict(list)
    for tb in built.sources.trial_balances[:1]:
        for row in tb.rows:
            key_ = "account:" + src.key(row.path)
            target = built.targets.get(key_)
            if target is None or not row.net or key_ in built.bank:
                continue
            if target["type"] in DOCUMENT_ACCOUNT_TYPES or target.get("role") == "inventory_asset":
                continue
            if target["type"] == "non_posting":
                continue
            if key_ in built.undeposited:
                continue  # its receipts carry it
            if target.get("role") == "sales_tax_payable" and not _opening_sales_tax(built, inp, target, row):
                continue
            lines.append(_Line(target, row.label, row.net, key_))
            if target.get("role") == "undeposited_funds":
                _problem(built, "warning", "undeposited_funds",
                         f"Undeposited Funds holds {_show(built, row.net)} in the old books, a credit balance no receipt can carry; "
                         "it comes in as one opening amount that Make Deposits cannot pick",
                         "Correct it in the old books before the cutover, or clear it with a journal entry afterwards.",
                         file=row.file, line=row.line, subject=row.label)
    for key_, detail in built.bank.items():
        label = detail.target.get("label") or detail.label
        line = _Line(detail.target, label, detail.statement, key_)
        when = detail.statement_date if detail.statement_date and detail.statement_date < inp.as_of else inp.as_of
        if detail.statement:
            (lines if when == inp.as_of else dated[when]).append(line)
    if not lines and not dated:
        return
    clearing = _clearing_value(built)
    batches = [(inp.as_of, f"journal:{inp.as_of}:{index}", part, index, -(-len(lines) // JOURNAL_LINES))
               for index, part in enumerate((lines[i:i + JOURNAL_LINES] for i in range(0, len(lines), JOURNAL_LINES)), start=1)]
    for when in sorted(dated):
        batches.append((when, f"journal:statement:{when}", dated[when], 0, 0))
    shown = []
    total_clearing = 0
    for when, outside_id, part, index, count in batches:
        net = sum(line.net for line in part)
        payload_lines = [{"account": line.target["id"] or Ref(line.target["ref"]), "side": "debit" if line.net > 0 else "credit",
                          "amount": _amount_text(built, abs(line.net)), "description": line.label[:200]} for line in part]
        if net:
            if clearing is None:
                _problem(built, "blocking", "no_clearing_account", "the opening journal needs a clearing account",
                         "Name one as clearing_account.")
                return
            payload_lines.append({"account": clearing, "side": "credit" if net > 0 else "debit",
                                  "amount": _amount_text(built, abs(net)), "description": "Balanced by the open documents and opening stock"})
        total_clearing -= net
        if index:
            name = f"Opening journal{'' if count == 1 else f' part {index}'}"
            memo = f"Opening balances from the old books as of {inp.as_of}"
        else:
            name, memo = f"Statement balances of {when}", f"Bank and card balances of the statements the old books last reconciled, dated {when}"
        payload = {"date": when, "memo": memo, "lines": payload_lines}
        if number:
            payload["number"] = (number if count == 1 else f"{number}-{index}") if index else f"{number}-S{when.replace('-', '')}"
        linked = built.books.link(outside_id, "transaction")
        built.steps.append(Step("journal", outside_id, name, "journal post", payload, "already_in" if linked else "create", linked,
                                amount=(sum(abs(line.net) for line in part) + abs(net)) // 2,
                                date=when, detail=f"{len(payload_lines)} lines"))
        for line in part:
            if line.key in built.bank:
                built.bank[line.key].journal = outside_id
            shown.append(CutoverJournalLine(account=line.target["name"], account_id=line.target["id"],
                                            side="debit" if line.net > 0 else "credit", amount=money(abs(line.net), built.books.currency),
                                            description=line.label if index else f"{line.label} (statement of {when})"))
        if net:
            shown.append(CutoverJournalLine(account=CLEARING_NAME if built.clearing_ref else _account_name(built, built.clearing_id),
                                            account_id=built.clearing_id, side="credit" if net > 0 else "debit",
                                            amount=money(abs(net), built.books.currency), description="Balanced by the open documents and opening stock"))
    built.journal = {"date": inp.as_of, "number": number, "parts": len(batches), "lines": shown, "clearing": total_clearing}


def _operation_key(outside_id: str, *salt: Any) -> str:
    """A stable reconciliation operation key for one move-in write, as its pattern allows."""
    import hashlib
    return "cutover-" + hashlib.sha256(":".join([outside_id, *map(str, salt)]).encode("utf-8")).hexdigest()[:40]


def _reconciliation_openings(built: Built, inp) -> None:
    """The last reconciliation of each account given its Reconciliation Summary, as an opening draft.

    It opens at the statement's date and ending balance; the journal line carrying that balance is
    marked `covered` and each uncleared item dated by then `outstanding`, so it proves, and the next
    `reconcile start` on the account follows it. The opening is certified with that first statement,
    never alone. An account that already has an opening here, or an opening draft the move-in did
    not make, keeps it and is named.
    """
    for key_, detail in built.bank.items():
        summary = detail.summary
        if summary is None or summary.statement_date is None:
            continue
        outside_id = "reconciliation:" + key_.split(":", 1)[1]
        account_id = detail.target["id"]
        linked = built.books.link(outside_id, "reconciliation_draft")
        state = built.books.reconciliation.get(account_id) if account_id else None
        if state is not None and state["opening"]:
            _problem(built, "warning", "reconciliation_exists",
                     f"{detail.label} is reconciled here already, so the old books' last reconciliation ({summary.statement_date}) does not "
                     "come in; its uncleared items still do", subject=detail.label)
            continue
        others = [draft for draft in (state["drafts"] if state else []) if draft != linked]
        if others:
            _problem(built, "warning", "opening_draft_exists",
                     f"{detail.label} has an opening reconciliation draft here the move-in did not make ({others[0]}), so the old books' "
                     "last reconciliation does not come in as another",
                     "Finish that opening through its first statement, or make the opening from the move-in's figures: "
                     f"`reconcile opening start` at {summary.statement_date} with {_show(built, summary.cleared_balance)}.",
                     subject=detail.label)
            continue
        balance = summary.cleared_balance
        covered = [detail.journal] if detail.journal and detail.statement else []
        outstanding = [step.outside_id for step in detail.item_steps if step.date and step.date <= summary.statement_date]
        payload = {"operation_key": _operation_key(outside_id), "account": account_id or Ref(detail.target["ref"]),
                   "opening_date": summary.statement_date, "entered_balance": _signed_text(built, balance),
                   "evidence": {"format": 1, "statement_reference": f"{detail.label} statement ending {summary.statement_date}"[:200],
                                "entered_text": f"Cleared Balance of the old books' Reconciliation Summary ({summary.file})"[:500]},
                   "references": []}
        built.steps.append(Step("reconciliation_opening", outside_id, f"Last reconciliation · {detail.label}",
                                None if linked else "reconcile opening start", payload, "already_in" if linked else "create",
                                linked, "reconciliation_draft", date=summary.statement_date,
                                detail=(f"statement of {summary.statement_date} ending at {_show(built, balance)}; "
                                        f"{len(outstanding)} uncleared item{'s' if len(outstanding) != 1 else ''} outstanding"),
                                extra={"covered": covered, "outstanding": outstanding}))


def _vendor_1099(built: Built, inp) -> None:
    """What each 1099 vendor was paid so far this year, from the old books' 1099 Summary.

    It comes in as the vendor's opening 1099 amount for the year (`vendor 1099-opening`), through
    the cutover date, less what the move-in itself brings in as paid to the vendor that year -- its
    uncleared checks, which the 1099 summary counts on their own dates -- so the year's summary here
    shows exactly the old books' figure as of the cutover, and the year whole from then on.
    """
    reports = built.sources.vendor_1099
    if not reports:
        return
    if len(reports) > 1:
        _problem(built, "blocking", "several_1099_summaries", "more than one 1099 Summary was given", "Give one, for January 1 to the cutover date.",
                 file=reports[1].file)
        return
    report, year = reports[0], int(inp.as_of[:4])
    if (report.date_from, report.date_to) != (f"{year}-01-01", inp.as_of):
        covers = f"{report.date_from} to {report.date_to}" if report.date_from else "dates its heading does not say"
        _problem(built, "blocking", "vendor_1099_dates", f"the 1099 Summary covers {covers}, not {year}-01-01 to {inp.as_of}, the year so far",
                 src._1099_FIX, file=report.file)
        return
    rows = {src.key(row.path): row for row in built.sources.lists["vendor"]}
    for row in report.rows:
        if not row.total:
            continue
        target = built.targets.get("vendor:" + src.key(row.vendor))
        if target is None:
            continue
        if row.total < 0:
            _problem(built, "blocking", "vendor_1099_negative", f"{row.vendor} was paid {_show(built, row.total)} this year in the old books",
                     "Correct the vendor's payments in the old books and export the 1099 Summary again.", file=row.file, line=row.line,
                     subject=row.vendor)
            continue
        listed = rows.get(src.key(row.vendor))
        record = next((v for v in built.books.vendors if v["id"] == target["id"]), None) if target["id"] else None
        eligible = (bool(record["eligible_1099"]) if record is not None and not target.get("made")
                    else listed is None or listed.get("1099").upper() == "Y")
        if not eligible:
            _problem(built, "warning", "not_1099_vendor",
                     f"{row.vendor} is on the old books' 1099 Summary ({_show(built, row.total)}) but is not marked eligible for a 1099, so "
                     "its payments so far do not come in",
                     "Mark the vendor eligible for a 1099 (`vendor update` eligible_1099), then set them with `vendor 1099-opening`.",
                     file=row.file, line=row.line, subject=row.vendor)
            continue
        vendor = target["id"] or Ref(target["ref"])
        own = sum(step.clearing for step in built.steps if step.kind == "uncleared_item" and step.extra.get("bank")
                  and step.extra.get("payee") == ("vendor", vendor) and f"{year}-01-01" <= (step.date or "") <= inp.as_of)
        opening = row.total - own
        if opening < 0:
            _problem(built, "blocking", "vendor_1099_below_payments",
                     f"{row.vendor}'s 1099 total this year is {_show(built, row.total)}, less than the {_show(built, own)} of uncleared "
                     "checks to it the move-in brings in", "Export the 1099 Summary and the uncleared items from the same books.",
                     file=row.file, line=row.line, subject=row.vendor)
            continue
        outside_id = f"1099:{src.key(row.vendor)}:{year}"
        linked = built.books.link(outside_id, "vendor_1099_opening")
        held = built.books.openings_1099.get((target["id"], year)) if target["id"] else None
        if held is not None and held["id"] != linked:
            _problem(built, "warning", "vendor_1099_opening_exists",
                     f"{row.vendor} already has {_show(built, held['amount_minor_units'])} set as paid in {year} before the books began "
                     f"here; the move-in leaves it, and the old books' figure less the uncleared checks is {_show(built, opening)}",
                     "Set it with `vendor 1099-opening` if the old books' figure is right.", file=row.file, line=row.line, subject=row.vendor)
            continue
        if held is not None and held["amount_minor_units"] != opening:
            _problem(built, "warning", "vendor_1099_changed",
                     f"{row.vendor}'s payments before the move-in came in as {_show(built, held['amount_minor_units'])} and the old books "
                     f"now say {_show(built, opening)}", "Set it again with `vendor 1099-opening` if the old books' figure is right.",
                     file=row.file, line=row.line, subject=row.vendor)
        if not opening:
            continue
        less = f", less {_show(built, own)} of uncleared checks that count on their own dates" if own else ""
        built.steps.append(Step("vendor_1099_opening", outside_id, f"{year} 1099 payments so far · {row.vendor}",
                                None if linked else "vendor 1099-opening",
                                {"vendor": vendor, "year": year, "as_of": inp.as_of, "amount": _amount_text(built, opening)},
                                "already_in" if linked else "create", linked, "vendor_1099_opening", amount=opening, date=inp.as_of,
                                detail=f"{_show(built, row.total)} in the old books' 1099 Summary{less}"))


def _vendor_1099_section(s, ctx, built: Built, inp) -> CutoverTieSection:
    """Each vendor on the old books' 1099 Summary against this company's 1099 summary for the same dates."""
    currency = built.books.currency
    reports = built.sources.vendor_1099
    if not reports:
        return CutoverTieSection(source="none", source_total=money(0, currency), books_total=money(0, currency), differences=0, rows=[])
    year = inp.as_of[:4]
    books = {row["vendor_id"]: row for row in _whole(s, ctx, "report vendor-1099-summary", {
        "date_from": f"{year}-01-01", "date_to": inp.as_of, "above_threshold_only": False})}
    rows, differences, seen = [], 0, set()
    for row in sorted(reports[0].rows, key=lambda r: r.vendor.lower()):
        target = built.targets.get("vendor:" + src.key(row.vendor))
        record_id = target["id"] if target else None
        seen.add(record_id)
        books_value = books[record_id]["payments"]["minor_units"] if record_id in books else 0
        difference = row.total - books_value
        differences += bool(difference)
        rows.append(CutoverTieRow(name=row.vendor, record_id=record_id, column="payments", source=money(row.total, currency),
                                  books=money(books_value, currency), difference=money(difference, currency)))
    for vendor_id, mine in books.items():
        value = mine["payments"]["minor_units"]
        if vendor_id not in seen and value:
            differences += 1
            rows.append(CutoverTieRow(name=mine["current_vendor_name"], record_id=vendor_id, column="payments",
                                      source=money(0, currency), books=money(value, currency), difference=money(-value, currency)))
    return CutoverTieSection(source="1099 Summary", source_total=money(sum(r.total for r in reports[0].rows), currency),
                             books_total=money(sum(mine["payments"]["minor_units"] for mine in books.values()), currency),
                             differences=differences, rows=rows)


def _account_name(built: Built, account_id: str | None) -> str:
    return next((a["full_name"] for a in built.books.accounts if a["id"] == account_id), CLEARING_NAME)


# ---------------------------------------------------------------- inactive list records

def _deactivations(built: Built) -> None:
    commands = {"account": ("account deactivate", "account"), "customer": ("customer deactivate", "customer"),
                "vendor": ("vendor deactivate", "vendor"), "item": ("item deactivate", "item")}
    for outside_id, target in list(built.targets.items()):
        kind = outside_id.split(":", 1)[0]
        if kind not in commands or not target.get("hidden") or not target.get("made"):
            continue
        if kind == "account" and target.get("balance"):
            continue
        command, field_ = commands[kind]
        done = built.books.link(outside_id + ":inactive", kind) or (target["id"] if not target.get("active", True) else None)
        built.steps.append(Step("deactivation", outside_id + ":inactive", target["name"], command,
                                {field_: target["id"] or Ref(target["ref"])}, "already_in" if done else "deactivate",
                                done, record_type=kind, detail="inactive in the old books"))


def _closing(built: Built, inp) -> None:
    closing = built.books.info.get("closing_date")
    dated = [step.date for step in built.steps if step.date and step.action == "create"]
    if closing and dated and min(dated) <= closing:
        _problem(built, "blocking", "period_closed",
                 f"the closing date is {closing} and the move-in posts documents dated from {min(dated)}",
                 "Clear the closing date for the move-in and set it again afterwards.")
    if built.books.foreign:
        first = built.books.foreign[0]
        _problem(built, "blocking", "entries_before_cutover",
                 f"the books already hold entries dated on or before {inp.as_of} that the move-in did not make, such as {first['type'].replace('_', ' ')} {first['number'] or first['id']} on {first['date']}",
                 "A move-in starts from books that are empty up to the cutover date: void or delete those entries, or choose a cutover date before them.")


# ------------------------------------------------------------------ output

def _counts(built: Built) -> list[CutoverCount]:
    order = ("account", "term", "customer", "vendor", "item", "invoice", "credit_memo", "bill", "vendor_credit",
             "inventory_adjustment", "uncleared_item", "undeposited_receipt", "sales_tax_adjustment", "journal",
             "reconciliation_opening", "vendor_1099_opening", "deactivation")
    tally = {kind: [0, 0, 0] for kind in order}
    totals: dict[str, int] = {}
    for step in built.steps:
        slot = {"create": 0, "deactivate": 0, "already_in": 1, "matched": 2}[step.action]
        tally.setdefault(step.kind, [0, 0, 0])[slot] += 1
        if step.amount is not None:
            totals[step.kind] = totals.get(step.kind, 0) + step.amount
    stepped = {step.outside_id for step in built.steps}
    for outside_id, target in built.targets.items():
        kind = outside_id.split(":", 1)[0]
        if kind in ("account", "term", "customer", "vendor", "item") and target["id"] and outside_id not in stepped:
            tally[kind][2] += 1
    currency = built.books.currency
    return [CutoverCount(kind=kind, create=v[0], already_in=v[1], matched=v[2],
                         amount=money(totals[kind], currency) if kind in totals else None)
            for kind, v in tally.items() if any(v)]


def _exceptions(built: Built) -> list[CutoverException]:
    ordered = sorted(built.exceptions, key=lambda p: {"blocking": 0, "warning": 1}.get(p.severity, 2))
    return [CutoverException(severity=p.severity, code=p.code, problem=p.problem, fix=p.fix, file=p.file, line=p.line,
                             subject=p.subject) for p in ordered]


def _step_out(built: Built, order: int, step: Step) -> CutoverStep:
    return CutoverStep(order=order, kind=step.kind, outside_id=step.outside_id, name=step.name, action=step.action,
                       command=step.command, record_id=step.record_id,
                       amount=money(step.amount, built.books.currency) if step.amount is not None else None,
                       date=step.date, detail=step.detail)


def _blocking_lines(built: Built, limit: int = 40) -> list[str]:
    """Each blocking exception in one line naming its file, line and problem.

    They lead the output, so an MCP result compacted to fit its client still carries them: the
    cutover trial's full plan said "14 blocking exceptions" and compaction dropped the list.
    """
    lines = []
    for p in built.exceptions:
        if p.severity != "blocking":
            continue
        where = (p.file or "") + (f" line {p.line}" if p.line else "")
        lines.append(f"{where + ': ' if where else ''}{p.code}: {p.problem}")
    if len(lines) > limit:
        lines = lines[:limit] + [f"and {len(lines) - limit} more; `exceptions` lists them all"]
    return lines


def _whole_set(built: Built) -> bool:
    """A full set of files: `cutover apply` reads the trial balance with them."""
    return bool(built.sources.trial_balances)


def _summary(built: Built, *, applied: bool = False) -> str:
    blocking = sum(1 for p in built.exceptions if p.severity == "blocking")
    create = sum(1 for s in built.steps if s.action in ("create", "deactivate"))
    already = sum(1 for s in built.steps if s.action == "already_in")
    if built.piece and not _whole_set(built):
        files = len(built.sources.files)
        found = (f"{blocking} blocking exception{'s' if blocking != 1 else ''} in them, listed in `blocking`" if blocking
                 else "nothing in them blocks")
        return (f"checked {files} file{'s' if files != 1 else ''} on their own: {found}; `cutover apply` needs the "
                "trial balance with all the files")
    if blocking and not applied:
        return f"{blocking} blocking exception{'s' if blocking != 1 else ''}; nothing can be brought in until they are fixed or mapped"
    verb = "made" if applied else "would make"
    return f"{verb} {create} record{'s' if create != 1 else ''}" + (f"; {already} already in from an earlier run" if already else "")


CLEARING_PARTS = {"invoice": ("invoices_and_credit_memos", -1), "credit_memo": ("invoices_and_credit_memos", 1),
                  "bill": ("bills_and_vendor_credits", 1), "vendor_credit": ("bills_and_vendor_credits", -1),
                  "inventory_adjustment": ("opening_stock", -1)}
# Kinds whose records post either way to the clearing account; each step carries its own signed amount.
SIGNED_PARTS = {"uncleared_item": "uncleared_items", "undeposited_receipt": "undeposited_receipts"}


def _clearing_out(built: Built) -> CutoverClearing | None:
    """What posts to the clearing account, by part, debit positive; the parts net to 0.00 once everything ties.

    Plan and apply build it the same way, so a clearing line that differs between them (the cutover
    trial's plan and apply differed by the opening sales tax) is explained part by part.
    """
    parts: dict[str, list[int]] = {}
    if built.journal and built.journal["clearing"]:
        parts["opening_journal"] = [built.journal["clearing"], sum(1 for s in built.steps if s.kind == "journal")]
    for step in built.steps:
        if step.action not in ("create", "already_in") or step.amount is None:
            continue
        if step.clearing is not None and step.kind in SIGNED_PARTS:
            entry = parts.setdefault(SIGNED_PARTS[step.kind], [0, 0])
            entry[0] += step.clearing
            entry[1] += 1
            continue
        if step.kind == "sales_tax_adjustment":
            name, sign = "opening_sales_tax", 1 if step.payload.get("direction", "increase") == "increase" else -1
        elif step.kind in CLEARING_PARTS:
            name, sign = CLEARING_PARTS[step.kind]
        else:
            continue
        entry = parts.setdefault(name, [0, 0])
        entry[0] += sign * step.amount
        entry[1] += 1
    if not parts:
        return None
    order = ("opening_journal", "invoices_and_credit_memos", "bills_and_vendor_credits", "opening_stock", "opening_sales_tax",
             "uncleared_items", "undeposited_receipts")
    currency = built.books.currency
    return CutoverClearing(
        account=CLEARING_NAME if built.clearing_ref or not built.clearing_id else _account_name(built, built.clearing_id),
        account_id=built.clearing_id,
        parts=[CutoverClearingPart(part=name, amount=money(parts[name][0], currency), records=parts[name][1])
               for name in order if name in parts],
        net=money(sum(value for value, _ in parts.values()), currency))


def plan_output(built: Built, inp, model=CutoverPlanOutput, **extra) -> Any:
    books = built.books
    checks = [CutoverCheck(name=name, source=money(a, books.currency), compared=money(b, books.currency),
                           difference=money(a - b, books.currency)) for name, a, b in built.checks]
    journal = None
    if built.journal:
        journal = CutoverJournal(date=built.journal["date"], number=built.journal["number"], parts=built.journal["parts"],
                                 lines=built.journal["lines"], clearing=money(built.journal["clearing"], books.currency))
    files = [CutoverFileOutput(name=f.name, kind=f.kind, attachment=f.attachment, sha256=f.sha256, rows=f.rows,
                               decided_by=("headings" if f.detected else "given") if f.kind else None)
             for f in built.sources.files]
    ready = _whole_set(built) and not any(p.severity == "blocking" for p in built.exceptions)
    return model(as_of=inp.as_of, ready=ready, source=built.sources.product,
                 summary=extra.pop("summary", None) or _summary(built), blocking=_blocking_lines(built), files=files,
                 counts=_counts(built), exceptions=_exceptions(built), checks=checks, clearing=_clearing_out(built),
                 mappings=CutoverMappingsOutput(**built.mappings), journal=journal,
                 steps=[_step_out(built, n, step) for n, step in enumerate(built.steps, start=1)], **extra)


def plan(s, ctx, inp) -> Plan:
    built = build(s, inp, journal_number=getattr(inp, "journal_number", None), piece=True)
    return Plan(plan_output(built, inp))


# ------------------------------------------------------------------ apply

def prepare_apply(s, ctx, inp) -> Plan:
    from bookflow.core import idempotency
    built = build(s, inp, journal_number=inp.journal_number)
    preview = plan_output(built, inp, CutoverApplyOutput, created=0,
                          already_in=sum(1 for step in built.steps if step.action == "already_in"),
                          clearing_account_id=built.clearing_id, dry_run=s.dry_run)
    blocking = [p for p in preview.exceptions if p.severity == "blocking"]
    if blocking and not s.dry_run:
        raise BookflowError("E_CUTOVER_BLOCKED",
                            message=f"The plan has {len(blocking)} blocking exception{'s' if len(blocking) != 1 else ''}; fix or map them, then run again.",
                            details={"exceptions": [p.model_dump(mode="json") for p in blocking]})
    data = {"built": built, "input": inp,
            "input_hash": idempotency.input_hash(inp.model_dump(mode="json"), s.company_row["id"]) if ctx.idempotency_key else None}
    return Plan(preview, data)


def _resolve(value, ids: dict[str, str]):
    if isinstance(value, Ref):
        return ids[str(value)]
    if isinstance(value, dict):
        return {k: _resolve(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, ids) for v in value]
    return value


def _record_id(output: dict, record_type: str) -> str | None:
    for name in ("id", "transaction_id"):
        if isinstance(output.get(name), str):
            return output[name]
    for name in ("record", "account", "customer", "vendor", "item", "term", "transaction", "journal", "adjustment", "draft",
                 "opening"):
        nested = output.get(name)
        if isinstance(nested, dict) and isinstance(nested.get("id"), str):
            return nested["id"]
    return None


def _keep_file(s, ctx, file: src.SourceFile) -> str:
    """Keep an export given as text as an attachment on the company, through `attachment add`;
    later calls pass its id instead of the text. Returns the attachment id."""
    import io
    import unicodedata
    from bookflow.core import registry
    from bookflow.core.dispatch import run_in_session
    from bookflow.core.transfer_resources import TransferLease
    from bookflow.core.transfers import InputBody, TransferResource, prepare
    command = registry.get("attachment add")
    name = "".join(ch for ch in file.name.replace("/", "-").replace("\\", "-") if unicodedata.category(ch) != "Cc").strip()
    name = (name or "export.txt")[:200]
    raw = {"record_type": "company_info", "record_id": s.company_row["id"], "original_filename": name,
           "media_type": "text/csv" if (file.kind or "") != "iif" else "text/plain", "caption": "Old books export (move-in)"}
    nested = ctx.model_copy(update={"idempotency_key": None, "source_ref": (SOURCE_MARK + _file_id(file))[:512],
                                    "reason": (ctx.reason or REASON)[:140]})
    saved = (s.company_touched, s.hub_touched, list(s.warnings), s.dry_run, s.transfer)
    prepared = prepare(command, raw, nested, s)
    lease = TransferLease(s.actor.id, s.company_row["id"], lambda lease: None)
    try:
        s.transfer = TransferResource(lease, prepared.store)
        body = InputBody(s.transfer, prepared.limit, False)
        body.receive(io.BytesIO(file.text.encode("utf-8")))
        body.complete()
        output = run_in_session(command, command.input_model.model_validate(raw), nested, s)
        return output["attachment"]["id"]
    finally:
        s.company_touched, s.hub_touched, s.warnings, s.dry_run, s.transfer = saved
        lease.close()
        if s.company is not None and s.company.write_transaction:
            s.company.raw.rollback()


def _run_step(s, ctx, step: Step, payload: dict) -> dict:
    from bookflow.core import registry
    from bookflow.core.dispatch import run_in_session
    command = registry.get(step.command)
    nested = ctx.model_copy(update={"idempotency_key": None, "source_ref": (SOURCE_MARK + step.outside_id)[:512],
                                    "reason": (ctx.reason or REASON)[:140]})
    saved = (s.company_touched, s.hub_touched, list(s.warnings), s.dry_run)
    try:
        return run_in_session(command, command.input_model.model_validate(payload), nested, s)
    finally:
        s.company_touched, s.hub_touched, s.warnings, s.dry_run = saved
        if s.company is not None and s.company.write_transaction:
            s.company.raw.rollback()


def _stopped(step: Step, error: BookflowError, created: int) -> BookflowError:
    return BookflowError("E_CUTOVER_INCOMPLETE",
        message=(f"The move-in stopped at {step.kind.replace('_', ' ')} {step.name}: {error.message} "
                 f"The {created} record{'s' if created != 1 else ''} made before it stay in; fix this and run cutover apply again to continue."),
        details={"created": created, "step": {"kind": step.kind, "outside_id": step.outside_id, "name": step.name,
                                             "command": step.command},
                 "cause": error.code, "cause_details": error.details})


def _read(s, ctx, name: str, raw: dict) -> dict:
    """A read command inside the move-in, the session left as it was."""
    from bookflow.core import registry
    from bookflow.core.dispatch import run_in_session, validate_input
    command = registry.get(name)
    saved = (s.company_touched, s.hub_touched, list(s.warnings), s.dry_run)
    try:
        return run_in_session(command, validate_input(command, raw), ctx, s)
    finally:
        s.company_touched, s.hub_touched, s.warnings, s.dry_run = saved


def _place_opening(s, ctx, built: Built, step: Step, draft_id: str, made: dict[str, str], created: int) -> int:
    """Mark the opening draft: the statement balance's journal line `covered`, each uncleared item dated
    by the statement `outstanding`. Only the move-in's own records, only what is not placed yet, and only
    while the draft is open; so a rerun writes nothing. Returns the writes made (0 or 1)."""
    row = s.company.conn.execute(sa.select(c.reconciliation_drafts.c.state, c.reconciliation_drafts.c.version).where(
        c.reconciliation_drafts.c.id == draft_id)).first()
    if row is None or row.state != "open":
        return 0
    covered = {made[o] for o in step.extra.get("covered", ()) if made.get(o)}
    outstanding = {made[o] for o in step.extra.get("outstanding", ()) if made.get(o)}
    entries, cursor, version = [], None, row.version
    while True:
        page = _read(s, ctx, "reconcile candidates", {"draft": draft_id, "limit": 200, **({"cursor": cursor} if cursor else {})})
        version = page["draft_version"]
        for candidate in page["items"]:
            moved = candidate["movement"]["transaction_id"]
            if candidate["eligible"] and not candidate["selected"] and moved in covered | outstanding:
                entries.append({"movement": candidate["movement"], "group_fingerprint": candidate["group_fingerprint"],
                                "action": "covered" if moved in covered else "outstanding"})
        cursor = page.get("next_cursor")
        if not cursor:
            break
    if not entries:
        return 0
    marks = Step("reconciliation_opening", step.outside_id + ":marks", step.name, "reconcile mark", {}, "create",
                 record_type="reconciliation_draft")
    for start in range(0, len(entries), 200):
        payload = {"draft": draft_id, "operation_key": _operation_key(marks.outside_id, version),
                   "expected_version": version, "entries": entries[start:start + 200]}
        try:
            version = _run_step(s, ctx, marks, payload)["draft"]["version"]
        except BookflowError as error:
            raise _stopped(marks, error, created) from None
    return 1


def _set_default_tax_item(s, ctx) -> str | None:
    """Make the company's only sales tax item its default when it has none; the item's name, or None.

    It goes through `company update`'s own plan and apply, so the version, the audit event and the
    registry copy are the ones a person's update makes. The move-in carries the right to do it,
    which `company update`'s admin role would not give a standard actor: it is the move-in's own
    consequence, not a setting changed on the side.
    """
    from bookflow.core import registry
    from bookflow.core.dispatch import _apply
    info = dict(s.company.conn.execute(sa.select(c.company_info)).mappings().one())
    if info.get("default_sales_tax_item_id") or not info.get("sales_tax_enabled"):
        return None
    rows = s.company.conn.execute(sa.select(c.items.c.id, c.items.c.full_name).where(
        c.items.c.type.in_(TAX_TYPES), c.items.c.active.is_(True))).all()
    if len(rows) != 1:
        return None
    command = registry.get("company update")
    nested = ctx.model_copy(update={"idempotency_key": None, "source_ref": (SOURCE_MARK + "default-sales-tax-item")[:512],
                                    "reason": (ctx.reason or REASON)[:140]})
    saved = (s.company_touched, s.hub_touched, list(s.warnings), s.dry_run)
    try:
        s.dry_run, s.hub_touched, s.company_touched = False, [], []
        inp = command.input_model.model_validate({"default_sales_tax_item_id": rows[0].id})
        _apply(command, command.plan(inp, nested, s), nested, s)
        return rows[0].full_name
    finally:
        s.company_touched, s.hub_touched, s.warnings, s.dry_run = saved
        if s.company is not None and s.company.write_transaction:
            s.company.raw.rollback()


def apply(plan_: Plan, ctx, s) -> Applied:
    from bookflow.core.dispatch import _upsert_principals
    built: Built = plan_.data["built"]
    inp = plan_.data["input"]
    with s.commits.operation("cutover.apply", s.hub, s.company):
        try:
            _upsert_principals(s, ctx)
            s.commits.commit(s.company, "cutover.apply")
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
        ids: dict[str, str] = {}
        for target in built.targets.values():
            if target["id"] and target.get("ref"):
                ids[target["ref"]] = target["id"]
        if built.clearing_id and built.clearing_ref:
            ids[built.clearing_ref] = built.clearing_id
        for file in built.sources.files:
            if file.attachment is None:
                try:
                    file.attachment = _keep_file(s, ctx, file)
                except BookflowError as error:
                    raise BookflowError("E_CUTOVER_INCOMPLETE",
                        message=f"The move-in could not keep {file.name} as an attachment: {error.message} Nothing was moved in yet.",
                        details={"created": 0, "file": file.name, "cause": error.code, "cause_details": error.details}) from None
        created = 0
        made: dict[str, str] = {}  # outside id -> the record made for it, by this run or an earlier one
        for step in built.steps:
            if step.action in ("already_in", "matched"):
                if step.ref and step.record_id:
                    ids[step.ref] = step.record_id
                if step.record_id:
                    made[step.outside_id] = step.record_id
                if step.kind == "reconciliation_opening" and step.record_id:
                    created += _place_opening(s, ctx, built, step, step.record_id, made, created)
                continue
            try:
                output = _run_step(s, ctx, step, _resolve(step.payload, ids))
            except BookflowError as error:
                raise _stopped(step, error, created) from None
            record_id = _record_id(output, step.record_type)
            step.record_id = record_id
            if step.ref and record_id:
                ids[step.ref] = record_id
            if step.ref == built.clearing_ref and record_id:
                built.clearing_id = record_id
            if record_id:
                made[step.outside_id] = record_id
            created += 1
            if step.kind == "reconciliation_opening" and record_id:
                _place_opening(s, ctx, built, step, record_id, made, created)  # part of making the opening
        already = sum(1 for step in built.steps if step.action == "already_in")
        default_tax = _set_default_tax_item(s, ctx)
        summary = _summary(built, applied=True)
        if default_tax:
            summary += f"; {default_tax}, the only sales tax item, is now the company's default"
        out = plan_output(built, inp, CutoverApplyOutput, created=created, already_in=already,
                          clearing_account_id=built.clearing_id, default_sales_tax_item=default_tax,
                          summary=summary)
        if ctx.idempotency_key and plan_.data.get("input_hash"):
            from bookflow.core import idempotency
            s.company.raw.execute("BEGIN IMMEDIATE")
            try:
                idempotency.store(s.company, s.actor.id, ctx.idempotency_key, "cutover apply", plan_.data["input_hash"],
                                  ctx.request_id, out.model_dump(mode="json"))
                s.commits.commit(s.company, "cutover.apply")
            except BaseException:
                if s.company.write_transaction:
                    s.company.raw.rollback()
                raise
    return Applied(out, [], out.summary, finalized=True, audited=True)


# ------------------------------------------------------------------ tie-out

def _whole(s, ctx, name: str, raw: dict) -> list[dict]:
    from bookflow.core import registry
    from bookflow.core.dispatch import run_in_session, validate_input
    command = registry.get(name)
    rows, cursor = [], None
    while True:
        page = run_in_session(command, validate_input(command, {**raw, "limit": 200, **({"cursor": cursor} if cursor else {})}), ctx, s)
        rows.extend(page["rows"])
        cursor = page.get("next_cursor")
        if not cursor:
            return rows


def _source_aging(built: Built, side: str, as_of: str) -> tuple[str, dict[str, dict[str, int]]]:
    from bookflow.company.aging import BUCKETS, bucket_of
    rows = built.sources.ar_aging if side == "receivable" else built.sources.ap_aging
    if rows:
        return ("A/R Aging Summary" if side == "receivable" else "A/P Aging Summary"), {
            row.party: {**row.buckets, "total": row.total} for row in rows}
    found: dict[str, dict[str, int]] = {}
    for doc in built.sources.documents:
        if doc.side != side or not doc.date:
            continue
        aging_date = (doc.due_date or doc.date) if doc.open_balance > 0 else doc.date
        cells = found.setdefault(doc.party, {**{b: 0 for b in BUCKETS}, "total": 0})
        cells[bucket_of(as_of, aging_date)] += doc.open_balance
        cells["total"] += doc.open_balance
    return ("Open Invoices" if side == "receivable" else "Unpaid Bills Detail"), found


def _tie_section(built: Built, label: str, source: dict[str, dict[str, int]], books_rows: list[dict], id_field: str,
                 name_field: str, kind: str, detail: str) -> CutoverTieSection:
    from bookflow.company.aging import BUCKETS
    currency = built.books.currency
    columns = ("total", *BUCKETS)
    books = {row[id_field]: row for row in books_rows}
    rows, differences, seen = [], 0, set()
    for party, cells in sorted(source.items(), key=lambda item: item[0].lower()):
        target = built.targets.get(f"{kind}:{src.key(party)}")
        record_id = target["id"] if target else None
        mine = books.get(record_id) if record_id else None
        seen.add(record_id)
        for column in columns:
            books_value = mine[column]["minor_units"] if mine else 0
            difference = cells[column] - books_value
            if difference:
                differences += 1
            if difference or detail == "all":
                rows.append(CutoverTieRow(name=party, record_id=record_id, column=column, source=money(cells[column], currency),
                                          books=money(books_value, currency), difference=money(difference, currency)))
    for record_id, row in books.items():
        if record_id in seen:
            continue
        for column in columns:
            value = row[column]["minor_units"]
            if value:
                differences += 1
                rows.append(CutoverTieRow(name=row.get(name_field) or "No name", record_id=record_id, column=column,
                                          source=money(0, currency), books=money(value, currency), difference=money(-value, currency)))
    source_total = sum(cells["total"] for cells in source.values())
    books_total = sum(row["total"]["minor_units"] for row in books_rows)
    return CutoverTieSection(source=label, source_total=money(source_total, currency), books_total=money(books_total, currency),
                             differences=differences, rows=rows)


def _stock_section(s, ctx, built: Built, inp) -> CutoverStockSection:
    """Each item's quantity on hand and asset value here against the Inventory Valuation Summary."""
    from bookflow.core.exact import format_quantity_micro_units, parse_quantity_micro_units
    currency = built.books.currency
    books = {row["item_id"]: row for row in _whole(s, ctx, "report inventory-valuation", {"as_of": inp.as_of})}
    quantity = lambda text: parse_quantity_micro_units(text or "0")
    shown = lambda micro: format_quantity_micro_units(micro)
    rows, differences, seen = [], 0, set()
    for stock in sorted(built.sources.stock, key=lambda row: row.item.lower()):
        target = built.targets.get("item:" + src.key(stock.item))
        record_id = target["id"] if target else None
        seen.add(record_id)
        mine = books.get(record_id)
        source_quantity, books_quantity = quantity(stock.quantity), quantity(mine["quantity_on_hand"]) if mine else 0
        books_value = mine["asset_value"]["minor_units"] if mine else 0
        differs = source_quantity != books_quantity or stock.value != books_value
        differences += differs
        if differs or inp.detail == "all":
            rows.append(CutoverStockRow(name=stock.item, record_id=record_id, source_quantity=shown(source_quantity),
                                        books_quantity=shown(books_quantity), source_value=money(stock.value, currency),
                                        books_value=money(books_value, currency), difference=money(stock.value - books_value, currency)))
    for record_id, mine in books.items():
        if record_id in seen:
            continue
        books_quantity, books_value = quantity(mine["quantity_on_hand"]), mine["asset_value"]["minor_units"]
        if books_quantity or books_value:
            differences += 1
            rows.append(CutoverStockRow(name=mine["item_name"], record_id=record_id, source_quantity=shown(0),
                                        books_quantity=shown(books_quantity), source_value=money(0, currency),
                                        books_value=money(books_value, currency), difference=money(-books_value, currency)))
    return CutoverStockSection(source="Inventory Valuation Summary" if built.sources.stock_files else "none",
                               source_total=money(sum(row.value for row in built.sources.stock), currency),
                               books_total=money(sum(row["asset_value"]["minor_units"] for row in books.values()), currency),
                               differences=differences, rows=rows)


def _opening_proves(s, account_id: str, draft_id: str) -> bool:
    """Whether an opening draft proves as it stands: what `reconcile preview` checks on it."""
    from bookflow.company import reconciliation_drafts as drafts
    from bookflow.company import reconciliation_loading as loading
    from bookflow.company import reconciliation_preparation as preparation
    try:
        snapshot = loading.load(s, account_id)
        preparation.opening(snapshot, drafts.load(snapshot, draft_id, authority_transactions=snapshot.authority_transactions))
        return True
    except BookflowError:
        return False


def _bank_section(s, built: Built, books_tb: dict[str, dict]) -> CutoverBankSection:
    """Each account brought in from its last reconciliation or its uncleared items: the balance here
    against the statement's plus what was in transit, the items, and the opening that follows."""
    currency = built.books.currency
    rows, differences = [], 0
    for key_, detail in sorted(built.bank.items(), key=lambda pair: pair[1].target["name"].lower()):
        account_id, card = detail.target["id"], detail.card
        natural = [_natural(card, item.amount) for item in detail.items]
        statement = _natural(card, detail.statement)
        increase = sum(value for value in natural if value > 0)
        decrease = -sum(value for value in natural if value < 0)
        expected = statement + increase - decrease
        books_value = _natural(card, books_tb[account_id]["signed_net"]["minor_units"]) if account_id in books_tb else 0
        found = sum(1 for step in detail.item_steps if step.action == "already_in" and step.record_id)
        opening, proven = "none", None
        if detail.summary is not None and account_id:
            state = built.books.reconciliation.get(account_id)
            linked = built.books.link("reconciliation:" + key_.split(":", 1)[1], "reconciliation_draft")
            if state is not None and state["opening"]:
                opening = "certified"
            elif linked and state is not None and linked in state["drafts"]:
                opening, proven = "draft", _opening_proves(s, account_id, linked)
        tied = (expected == books_value and found == len(detail.items)
                and (detail.summary is None or opening == "certified" or bool(proven)))
        differences += not tied
        rows.append(CutoverBankRow(
            name=detail.target["name"], record_id=account_id, statement_date=detail.statement_date,
            statement_balance=money(statement, currency), uncleared_increase=money(increase, currency),
            uncleared_decrease=money(decrease, currency), expected_balance=money(expected, currency),
            books_balance=money(books_value, currency), difference=money(expected - books_value, currency),
            items_source=len(detail.items), items_books=found, opening=opening, opening_proven=proven, tied=tied))
    source = ("Reconciliation Summary and uncleared items" if built.sources.reconciliations else "uncleared items") if rows else "none"
    return CutoverBankSection(source=source, differences=differences, rows=rows)


def _undeposited_section(s, ctx, built: Built, inp) -> CutoverTieSection:
    """What the old books held in Undeposited Funds against the receipts the move-in brought into it, which
    Make Deposits picks; a deposit made since does not undo the comparison, which is as of the cutover."""
    currency = built.books.currency
    funds = {key_: t for key_, t in built.targets.items() if key_.startswith("account:") and t.get("role") == "undeposited_funds"
             and (t.get("balance") or key_ in built.undeposited)}
    if not funds:
        return CutoverTieSection(source="none", source_total=money(0, currency), books_total=money(0, currency), differences=0, rows=[])
    rows, differences, source_total, books_total = [], 0, 0, 0
    given = {"account:" + src.key(item.path) for item in built.sources.open_items}
    for key_, target in sorted(funds.items(), key=lambda pair: pair[1]["name"].lower()):
        source_value = target.get("balance", 0)
        books_value = sum(step.amount for step in built.steps if step.kind == "undeposited_receipt" and step.action == "already_in"
                          and step.record_id and step.extra.get("funds") == target["name"])
        if key_ not in built.undeposited:
            books_value = source_value if source_value <= 0 else books_value
        difference = source_value - books_value
        differences += bool(difference)
        source_total, books_total = source_total + source_value, books_total + books_value
        rows.append(CutoverTieRow(name=target["name"], record_id=target["id"], column="receipts", source=money(source_value, currency),
                                  books=money(books_value, currency), difference=money(difference, currency)))
    label = "Undeposited Funds QuickReport" if given & set(funds) else "Trial Balance"
    return CutoverTieSection(source=label, source_total=money(source_total, currency), books_total=money(books_total, currency),
                             differences=differences, rows=rows)


def _lists_section(built: Built) -> CutoverListSection:
    """The list fields that matter, as the old books' lists give them, against the records here."""
    books = built.books
    by_id = {kind: {row["id"]: row for row in table} for kind, table in (
        ("account", books.accounts), ("customer", books.customers), ("vendor", books.vendors), ("item", books.items),
        ("term", books.terms))}
    skipped = {p.subject for p in built.exceptions if p.code in ("item_skipped", "non_posting_accounts")}
    rows: list[CutoverListRow] = []
    notes: list[CutoverListRow] = []
    compared = 0

    def record_for(kind: str, path: str):
        target = built.targets.get(f"{kind}:{src.key(path)}")
        return by_id[kind].get(target["id"]) if target and target["id"] else None

    def add(kind, name, record, field_, source, here, *, note=False):
        (notes if note else rows).append(CutoverListRow(list=kind, name=name, record_id=record["id"] if record else None,
                                                        field=field_, source=source, books=here))

    yes_no = lambda flag: "yes" if flag else "no"
    term_name = lambda term_id: by_id["term"][term_id]["name"] if term_id in by_id["term"] else None
    amount = lambda minor: None if not minor else _show(built, minor)

    def active(kind, row, record, *, note=False):
        hidden = row.get("HIDDEN").upper() == "Y"
        if hidden == bool(record["active"]):
            add(kind, row.path, record, "active", yes_no(not hidden), yes_no(record["active"]), note=note)

    for row in built.sources.lists["account"]:
        kind = src.ACCOUNT_TYPES.get(row.get("ACCNTTYPE").upper())
        if kind == "non_posting":
            continue
        record = record_for("account", row.path)
        compared += 1
        if record is None:
            add("account", row.path, None, "missing", "in the old books", None)
            continue
        if kind and record["type"] != kind:
            add("account", row.path, record, "type", kind, record["type"])
        number = row.get("ACCNUM") or None
        if number and number != record["number"]:
            add("account", row.path, record, "number", number, record["number"], note=True)
        target = built.targets.get("account:" + src.key(row.path)) or {}
        active("account", row, record, note=bool(target.get("balance")))
    customer_rows = {src.key(row.path): row for row in built.sources.lists["customer"]}
    code_name = lambda code_id: next((x["code"] for x in books.codes if x["id"] == code_id), None)
    item_name = lambda item_id: by_id["item"][item_id]["full_name"] if item_id in by_id["item"] else None

    def tax(row, record, parent):
        # The tax code and item the move-in gives the customer; a job is compared on what it takes as its own.
        for field_, wanted in _customer_tax(built, row, parent).items():
            if isinstance(wanted, Ref) or wanted == record.get(field_):
                continue
            name = code_name if field_ == "sales_tax_code_id" else item_name
            add("customer", row.path, record, field_.removesuffix("_id"), name(wanted), name(record.get(field_)))

    for row in built.sources.lists["customer"]:
        record = record_for("customer", row.path)
        compared += 1
        if record is None:
            add("customer", row.path, None, "missing", "in the old books", None)
            continue
        active("customer", row, record)
        if ":" in row.path:
            status = JOB_STATUS.get(row.get("JOBSTATUS").lower(), "none")
            if status != (record.get("job_status") or "none"):
                add("customer", row.path, record, "job_status", status, record.get("job_status") or "none")
            if (row.get("JOBDESC") or None) != (record.get("job_description") or None):
                add("customer", row.path, record, "job_description", row.get("JOBDESC") or None, record.get("job_description"))
            parent = customer_rows.get(src.key(row.path.rsplit(":", 1)[0]))
            if parent is not None:
                tax(row, record, parent)
            continue
        tax(row, record, None)
        wanted = _term_for(built, row.get("TERMS")) if row.get("TERMS") else None
        if row.get("TERMS") and (wanted if isinstance(wanted, str) and not isinstance(wanted, Ref) else None) != record.get("terms_id"):
            add("customer", row.path, record, "terms", row.get("TERMS"), term_name(record.get("terms_id")))
        limit = _source_money(built, row.get("LIMIT"))
        if limit and limit != record.get("credit_limit_minor_units"):
            add("customer", row.path, record, "credit_limit", amount(limit), amount(record.get("credit_limit_minor_units")))
    for row in built.sources.lists["vendor"]:
        record = record_for("vendor", row.path)
        compared += 1
        if record is None:
            add("vendor", row.path, None, "missing", "in the old books", None)
            continue
        active("vendor", row, record)
        wanted = _term_for(built, row.get("TERMS")) if row.get("TERMS") else None
        if row.get("TERMS") and (wanted if isinstance(wanted, str) and not isinstance(wanted, Ref) else None) != record.get("terms_id"):
            add("vendor", row.path, record, "terms", row.get("TERMS"), term_name(record.get("terms_id")))
        if (row.get("1099").upper() == "Y") != bool(record.get("eligible_1099")):
            add("vendor", row.path, record, "eligible_1099", yes_no(row.get("1099").upper() == "Y"), yes_no(record.get("eligible_1099")))
        limit = _source_money(built, row.get("LIMIT"))
        if limit and limit != record.get("credit_limit_minor_units"):
            add("vendor", row.path, record, "credit_limit", amount(limit), amount(record.get("credit_limit_minor_units")))
    for row in built.sources.lists["item"]:
        record = record_for("item", row.path)
        compared += 1
        if record is None:
            add("item", row.path, None, "missing", "in the old books", None, note=row.path in skipped)
            continue
        active("item", row, record)
        kind = src.ITEM_TYPES.get(row.get("INVITEMTYPE").upper())
        if kind and kind != record["type"]:
            add("item", row.path, record, "type", kind, record["type"])
        for column, field_, word in (("PRICE", "price_minor_units", "price"), ("COST", "cost_minor_units", "cost")):
            wanted = _source_money(built, row.get(column))
            if wanted is not None and (wanted or None) != (record.get(field_) or None):
                add("item", row.path, record, word, amount(wanted) or "0.00", amount(record.get(field_)))
    for outside_id, target in built.targets.items():
        if not outside_id.startswith("term:") or not target.get("settings") or not target["id"]:
            continue
        record = by_id["term"].get(target["id"])
        if record is None:
            continue
        compared += 1
        for difference in _term_differences(target["settings"], record):
            field_, _, rest = difference.partition(" here, ")
            add("term", target["name"], record, "settings", rest.removesuffix(" in the old books"), field_)
    return CutoverListSection(source="IIF lists", compared=compared, differences=len(rows), rows=rows, notes=notes)


def tie_out(s, ctx, inp) -> CutoverTieOutOutput:
    built = build(s, inp)
    books = built.books
    currency = books.currency
    problems = [p for p in built.exceptions if p.severity == "blocking" and p.code in (
        "no_trial_balance", "several_trial_balances", "unmapped_account", "mapping_not_found", "unknown_file", "no_chart",
        "type_conflict", "number_taken", "unreadable_report", "unreadable_amount", "cash_basis_trial_balance", "as_of_mismatch",
        "row_width", "row_shifted")]
    trial = _whole(s, ctx, "report trial-balance", {"date_to": inp.as_of})
    books_tb = {row["account_id"]: row for row in trial}
    source_tb: dict[str, int] = defaultdict(int)
    names: dict[str, str] = {}
    unmatched: list[tuple[str, int]] = []
    for tb in built.sources.trial_balances[:1]:
        for row in tb.rows:
            target = built.targets.get("account:" + src.key(row.path))
            if target is None or not target["id"]:
                unmatched.append((row.label, row.net))
                continue
            source_tb[target["id"]] += row.net
            names.setdefault(target["id"], target["name"])
    clearing_id = built.clearing_id
    tb_rows, tb_differences = [], 0
    for account_id in sorted(set(source_tb) | set(books_tb), key=lambda a: (names.get(a) or books_tb.get(a, {}).get("current_account_label") or "")):
        source_value = source_tb.get(account_id, 0)
        books_value = books_tb[account_id]["signed_net"]["minor_units"] if account_id in books_tb else 0
        difference = source_value - books_value
        if difference:
            tb_differences += 1
        if difference or inp.detail == "all":
            name = names.get(account_id) or books_tb.get(account_id, {}).get("current_account_label") or account_id
            tb_rows.append(CutoverTieRow(name=name, record_id=account_id, column="balance", source=money(source_value, currency),
                                         books=money(books_value, currency), difference=money(difference, currency)))
    for label, net in unmatched:
        if net:
            tb_differences += 1
            tb_rows.append(CutoverTieRow(name=label, record_id=None, column="balance", source=money(net, currency),
                                         books=money(0, currency), difference=money(net, currency)))
    debit = lambda values: sum(v for v in values if v > 0)
    trial_section = CutoverTieSection(
        source="Trial Balance", source_total=money(debit([r.net for tb in built.sources.trial_balances[:1] for r in tb.rows]), currency),
        books_total=money(debit([row["signed_net"]["minor_units"] for row in trial]), currency),
        differences=tb_differences, rows=tb_rows)
    label, source_ar = _source_aging(built, "receivable", inp.as_of)
    receivables = _tie_section(built, label, source_ar, _whole(s, ctx, "report ar-aging", {"as_of": inp.as_of}),
                               "customer_id", "current_customer_label", "customer", inp.detail)
    label, source_ap = _source_aging(built, "payable", inp.as_of)
    payables = _tie_section(built, label, source_ap, _whole(s, ctx, "report ap-aging", {"as_of": inp.as_of}),
                            "vendor_id", "current_vendor_name", "vendor", inp.detail)
    inventory = _stock_section(s, ctx, built, inp)
    lists = _lists_section(built)
    bank = _bank_section(s, built, books_tb)
    undeposited = _undeposited_section(s, ctx, built, inp)
    vendor_1099 = _vendor_1099_section(s, ctx, built, inp)
    clearing = books_tb[clearing_id]["signed_net"]["minor_units"] if clearing_id in books_tb else 0
    tied = not problems and not (tb_differences or receivables.differences or payables.differences or inventory.differences
                                 or lists.differences or clearing or bank.differences or undeposited.differences
                                 or vendor_1099.differences)
    if tied:
        extra = [what for what, rows in (("the bank and card accounts with their uncleared items and last reconciliations", bank.rows),
                                         ("the receipts waiting for deposit", undeposited.rows),
                                         ("the 1099 payments so far this year", vendor_1099.rows)) if rows]
        summary = (f"tied: the trial balance, receivables, payables and stock{''.join(', ' + e for e in extra)} match the old "
                   f"books to the cent as of {inp.as_of}, and the {lists.compared} list records compared match")
    else:
        parts = [f"{n} {what}{'s' if n != 1 else ''}" for n, what in (
            (tb_differences, "trial balance difference"), (receivables.differences, "receivables difference"),
            (payables.differences, "payables difference"), (inventory.differences, "stock difference"),
            (lists.differences, "list difference"), (bank.differences, "bank or card account not tied"),
            (undeposited.differences, "undeposited funds difference"), (vendor_1099.differences, "1099 difference")) if n]
        if clearing:
            parts.append(f"clearing account at {_show(built, clearing)}")
        if problems:
            parts.append(f"{len(problems)} exception{'s' if len(problems) != 1 else ''}")
        summary = "not tied: " + ", ".join(parts)
    return CutoverTieOutOutput(as_of=inp.as_of, tied=tied, summary=summary, trial_balance=trial_section,
                               receivables=receivables, payables=payables, inventory=inventory, lists=lists,
                               bank=bank, undeposited=undeposited, vendor_1099=vendor_1099,
                               clearing=money(clearing, currency),
                               exceptions=[CutoverException(severity=p.severity, code=p.code, problem=p.problem, fix=p.fix,
                                                            file=p.file, line=p.line, subject=p.subject) for p in problems])
