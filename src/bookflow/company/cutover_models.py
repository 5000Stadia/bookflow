"""Input and output models of `cutover plan`, `cutover apply` and `cutover tie-out`."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from bookflow.company.ledger_reports import MoneyOutput, iso_date

FileKind = Literal["iif", "trial_balance", "open_invoices", "unpaid_bills", "ar_aging", "ap_aging", "inventory_valuation"]
IsoDate = Annotated[str, AfterValidator(iso_date)]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CutoverFile(_Input):
    attachment: str | None = Field(None, min_length=1, max_length=26, description=(
        "An attachment in this company holding the export file, as `attachment add company_info <company id> FILE` "
        "returns it. Give this or `content`."))
    content: str | None = Field(None, min_length=1, max_length=8_000_000, description=(
        "The export file's text, when it is not an attachment. Give this or `attachment`."))
    name: str | None = Field(None, min_length=1, max_length=255, description=(
        "A label for this file in exceptions; defaults to the attachment's file name, else `file N`."))
    kind: FileKind | None = Field(None, description=(
        "What the file is; read from its own headings when omitted. `iif`: a list export (chart of accounts, "
        "customers, vendors, items, terms). Report CSV exports: `trial_balance` (Trial Balance, accrual, as of the "
        "cutover date), `open_invoices` (Open Invoices), `unpaid_bills` (Unpaid Bills Detail, all dates), `ar_aging` "
        "and `ap_aging` (A/R and A/P Aging Summary, optional, used by tie-out), `inventory_valuation` (Inventory "
        "Valuation Summary, required when the trial balance carries inventory)."))

    @model_validator(mode="after")
    def one_source(self):
        if (self.attachment is None) == (self.content is None):
            raise ValueError("give exactly one of attachment or content")
        return self


class CutoverMappings(_Input):
    accounts: dict[str, str] = Field(default_factory=dict, description=(
        "Old-books account, as its file names it (`6700 · Utilities:6710 · Telephone`, `Utilities:Telephone` or "
        "`6710`), to a Bookflow account (ID, number or full name), or `create` to make it from the account list."))
    customers: dict[str, str] = Field(default_factory=dict, description=(
        "Old-books customer or `Customer:Job` to a Bookflow customer or job (ID or full name), or `create`."))
    vendors: dict[str, str] = Field(default_factory=dict, description=(
        "Old-books vendor to a Bookflow vendor (ID or name), or `create`."))
    items: dict[str, str] = Field(default_factory=dict, description=(
        "Old-books item to a Bookflow item (ID or full name), or `create`."))
    terms: dict[str, str] = Field(default_factory=dict, description=(
        "Old-books terms name to a Bookflow term (ID or name), or `create`."))


class CutoverInput(_Input):
    as_of: IsoDate = Field(min_length=10, max_length=10, description=(
        "The old books' trial balance date, YYYY-MM-DD: the opening journal is dated this day, open invoices and "
        "bills keep their own earlier dates, and new work starts the next day."))
    files: list[CutoverFile] = Field(min_length=1, max_length=20, description=(
        "The export files from the old books; the same files on every run."))
    mappings: CutoverMappings = Field(default_factory=CutoverMappings, description=(
        "Decisions for names the plan could not match on its own. `cutover plan` returns the complete mapping in "
        "this shape; passing it back pins every target."))
    clearing_account: str | None = Field(None, min_length=1, max_length=200, description=(
        "The account the opening journal balances against and the open documents post to; it is 0.00 once "
        "everything ties. Omitted: `Cutover Clearing`, an other current asset account, made on first use."))


class CutoverApplyInput(CutoverInput):
    journal_number: str | None = Field(None, min_length=1, max_length=40, description=(
        "Number of the opening journal; omitted, the next journal number."))


class CutoverTieOutInput(CutoverInput):
    detail: Literal["differences", "all"] = Field("differences", description=(
        "`differences` lists only rows that do not match; `all` lists every compared row."))


# ------------------------------------------------------------------ output

class CutoverFileOutput(BaseModel):
    name: str = Field(description="The file's label")
    kind: str | None = Field(description="What the file was read as; null when it could not be told")
    decided_by: Literal["given", "headings"] | None = Field(description=(
        "How the kind was decided: `given` by the file's own `kind`, `headings` read from the file; null when it could not be"))
    attachment: str | None = Field(description=(
        "The attachment holding the file: the one it was read from, or for inline text the one `cutover apply` kept it as. "
        "Pass `{\"attachment\": \"<id>\"}` in `files` on later calls instead of the text"))
    sha256: str = Field(description="SHA-256 of the file's text")
    rows: int = Field(description="Data rows read from it")


class CutoverException(BaseModel):
    severity: Literal["blocking", "warning", "note"] = Field(description=(
        "`blocking` stops `cutover apply` until it is fixed or mapped; `warning` is reported and does not stop it; "
        "`note` says how something here differs from the old books by design"))
    code: str = Field(description="Stable name of the exception, e.g. unmapped_account or receivables_do_not_tie")
    problem: str = Field(description="What is wrong, in a sentence")
    fix: str | None = Field(description="What resolves it")
    file: str | None = Field(description="The file it was found in")
    line: int | None = Field(description="Line of that file")
    subject: str | None = Field(description="The old-books name it concerns")


class CutoverCount(BaseModel):
    kind: str = Field(description="account, customer, vendor, item, term, invoice, credit_memo, bill, vendor_credit, "
                                  "inventory_adjustment, journal, deactivation")
    create: int = Field(description="Records this run makes")
    already_in: int = Field(description="Records an earlier run of the cutover made, found by their outside id")
    matched: int = Field(description="Old-books records that are existing Bookflow records")
    amount: MoneyOutput | None = Field(description=(
        "For documents, stock and the journal: their total open amount, value or journal total, made and already in; "
        "null for lists"))


class CutoverStep(BaseModel):
    order: int = Field(description="Position in the run")
    kind: str = Field(description="What the step makes; see counts")
    outside_id: str = Field(description="The record's identity in the old books; the write carries `cutover:` plus this as its source reference")
    name: str = Field(description="The record as a person reads it")
    action: Literal["create", "already_in", "matched", "deactivate"] = Field(description=(
        "`create`: this run makes it; `already_in`: an earlier run made it; `matched`: an existing Bookflow record "
        "stands for it; `deactivate`: inactive in the old books, made inactive here after the documents"))
    command: str | None = Field(description="The command that makes it")
    record_id: str | None = Field(description="The Bookflow record; null for one not yet made")
    amount: MoneyOutput | None = Field(description="The document's open amount or the adjustment's value")
    date: str | None = Field(description="The document's accounting date")
    detail: str | None = Field(description="What else the step carries, in a phrase")


class CutoverJournalLine(BaseModel):
    account: str = Field(description="The Bookflow account, as its full name")
    account_id: str | None = Field(description="Its ID; null when this run makes the account")
    side: Literal["debit", "credit"]
    amount: MoneyOutput
    description: str = Field(description="The old-books account the line carries")


class CutoverJournal(BaseModel):
    date: str = Field(description="The cutover date")
    number: str | None = Field(description="The opening journal's number; null for the next number")
    parts: int = Field(description="How many journals carry the lines (at most 200 lines each)")
    lines: list[CutoverJournalLine] = Field(description="Every account line, then the clearing line")
    clearing: MoneyOutput = Field(description="The clearing line, debit positive: what the documents must carry")


class CutoverCheck(BaseModel):
    name: str = Field(description="receivables, payables, inventory, trial_balance_total, receivables_total, payables_total, aging")
    source: MoneyOutput = Field(description="The old books' figure")
    compared: MoneyOutput = Field(description="What it was compared with: the documents, the rows or the other report")
    difference: MoneyOutput = Field(description="source less compared; 0.00 ties")


class CutoverMappingsOutput(BaseModel):
    accounts: dict[str, str] = Field(description="Every old-books account to its Bookflow account ID, or `create`")
    customers: dict[str, str] = Field(description="Every old-books customer and job to its Bookflow ID, or `create`")
    vendors: dict[str, str] = Field(description="Every old-books vendor to its Bookflow ID, or `create`")
    items: dict[str, str] = Field(description="Every old-books item to its Bookflow ID, or `create`")
    terms: dict[str, str] = Field(description="Every old-books terms name to its Bookflow ID, or `create`")


class CutoverPlanOutput(BaseModel):
    dry_run: bool = False
    warnings: list[str] = Field(default_factory=list)
    as_of: str = Field(description="The cutover date")
    ready: bool = Field(description="True when nothing blocks `cutover apply`")
    source: str | None = Field(description="The product and version the IIF files name, when they do")
    summary: str = Field(description="One line: what the run makes and what blocks it")
    counts: list[CutoverCount] = Field(description="Records and totals by kind: the whole run at a glance")
    exceptions: list[CutoverException] = Field(description="Every problem found, blocking first, then warnings and notes")
    checks: list[CutoverCheck] = Field(description="The tie checks the plan can make before anything is written")
    files: list[CutoverFileOutput] = Field(description="Each file read, with its attachment id and SHA-256 to reuse")
    journal: CutoverJournal | None = Field(description="The opening journal; null when the trial balance carries only document-owned accounts")
    steps: list[CutoverStep] = Field(description="Every write in order, with what each one makes")
    mappings: CutoverMappingsOutput = Field(description="The complete resolved mapping, in the input's shape")


class CutoverApplyOutput(CutoverPlanOutput):
    created: int = Field(description="Records this run made")
    already_in: int = Field(description="Records earlier runs had made")
    clearing_account_id: str | None = Field(description="The clearing account")


class CutoverTieRow(BaseModel):
    name: str = Field(description="The account, customer, job or vendor")
    record_id: str | None = Field(description="The Bookflow record compared; null when nothing in the books stands for it")
    column: str = Field(description="`balance` for the trial balance; total, current, days_1_30, days_31_60, days_61_90 or over_90 for an aging")
    source: MoneyOutput = Field(description="The old books' figure, debit positive on the trial balance")
    books: MoneyOutput = Field(description="Bookflow's figure as of the cutover date")
    difference: MoneyOutput = Field(description="source less books; 0.00 ties")


class CutoverTieSection(BaseModel):
    source: str = Field(description="What the old books' side was read from")
    source_total: MoneyOutput
    books_total: MoneyOutput
    differences: int = Field(description="Rows that do not tie")
    rows: list[CutoverTieRow]


class CutoverStockRow(BaseModel):
    name: str = Field(description="The item")
    record_id: str | None = Field(description="The Bookflow item compared; null when none stands for it")
    source_quantity: str = Field(description="Quantity on hand in the old books")
    books_quantity: str = Field(description="Quantity on hand here as of the cutover date")
    source_value: MoneyOutput = Field(description="Asset value in the old books")
    books_value: MoneyOutput = Field(description="Asset value here as of the cutover date")
    difference: MoneyOutput = Field(description="source_value less books_value; 0.00 ties")


class CutoverStockSection(BaseModel):
    source: str = Field(description="What the old books' side was read from, or `none` when no valuation was given")
    source_total: MoneyOutput
    books_total: MoneyOutput
    differences: int = Field(description="Items whose quantity or value does not tie")
    rows: list[CutoverStockRow]


class CutoverListRow(BaseModel):
    list: Literal["account", "customer", "vendor", "item", "term"]
    name: str = Field(description="The record as the old books name it")
    record_id: str | None = Field(description="The Bookflow record compared; null when none stands for it")
    field: str = Field(description="active, type, number, job_status, job_description, terms, credit_limit, eligible_1099, price, cost, due_days, discount_percent or discount_days")
    source: str | None = Field(description="The old books' value")
    books: str | None = Field(description="The value here")


class CutoverListSection(BaseModel):
    source: str = Field(description="What the old books' side was read from")
    compared: int = Field(description="Records compared")
    differences: int = Field(description="Fields that do not match")
    rows: list[CutoverListRow] = Field(description="Every field that does not match")
    notes: list[CutoverListRow] = Field(description="Fields that differ by design, such as an account matched to one here that keeps its own number")


class CutoverTieOutOutput(BaseModel):
    as_of: str
    tied: bool = Field(description=(
        "True when every compared figure ties to the cent, the clearing account is 0.00, and every list field compared matches"))
    summary: str
    trial_balance: CutoverTieSection
    receivables: CutoverTieSection
    payables: CutoverTieSection
    inventory: CutoverStockSection
    lists: CutoverListSection
    clearing: MoneyOutput = Field(description="The clearing account's balance as of the cutover date; 0.00 ties")
    exceptions: list[CutoverException] = Field(description="Problems that kept a figure from being compared")
