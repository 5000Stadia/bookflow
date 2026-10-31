"""Inputs and outputs of `opening-draft` (R107 increment 7, design/bulk-import.md §6)."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bookflow.company.cutover_models import CutoverApplyOutput, CutoverPlanOutput, CutoverTieOutOutput, IsoDate
from bookflow.company.ledger_reports import MoneyOutput

Step = Literal['balances', 'open_invoices', 'unpaid_bills', 'stock']
State = Literal['draft', 'planned', 'applying', 'applied', 'rolled_back', 'failed', 'reversed', 'discarded']
_ID = dict(min_length=1, max_length=26)


class _Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Grid(_Input):
    content: str | None = Field(None, max_length=2_000_000, description=(
        'The rows as pasted or typed, with a header row: tab text from a spreadsheet, or comma or semicolon separated. '
        'Give this or `attachment`; an empty string clears the step.'))
    attachment: str | None = Field(None, **_ID, description='An attachment of this company holding the rows as a CSV file.')
    date_order: Literal['MDY', 'DMY', 'YMD'] | None = Field(None, description=(
        'The order of day and month in the dates when no date tells (03/04/2026): MDY month first, DMY day first.'))

    @model_validator(mode='after')
    def one_source(self):
        if self.content is not None and self.attachment is not None:
            raise ValueError('give content or attachment, not both')
        return self


class Given(_Input):
    receivables: str | None = Field(None, max_length=40, description=(
        'Total receivables as a statement or report shows it, to check the open invoices against; omitted, it is '
        'taken from the invoices and labelled derived.'))
    payables: str | None = Field(None, max_length=40, description=(
        'Total payables, to check the unpaid bills against; omitted, taken from the bills (derived).'))
    stock: str | None = Field(None, max_length=40, description=(
        'Total stock value, to check the stock rows against; omitted, taken from the rows (derived).'))


_STEPS = ('`balances` (Account, Balance: every bank, card, loan and other account balance you know, on the account\'s own '
          'side unless a Side column says debit or credit; type equity accounts only if you know them, else equity is '
          'worked out), `open_invoices` (Customer, Number, Date, Due date or Terms, Open amount, PO number, Memo), '
          '`unpaid_bills` (Vendor, Reference, Date, Due date or Terms, Open amount, Memo), `stock` (Item, Quantity, Total '
          'value or Unit cost)')


class CreateInput(_Input):
    as_of: IsoDate = Field(min_length=10, max_length=10, description=(
        'The cutover date: balances are as of the end of this day; open invoices and bills keep their own earlier '
        'dates, and new work starts the next day.'))
    label: str | None = Field(None, max_length=200, description='A name for the draft; omitted, "Opening balances as of <date>".')
    balances: Grid = Field(None, description='The balances step\'s rows; omitted, the step starts empty.')
    open_invoices: Grid = Field(None, description='The open invoices step\'s rows; omitted, the step starts empty.')
    unpaid_bills: Grid = Field(None, description='The unpaid bills step\'s rows; omitted, the step starts empty.')
    stock: Grid = Field(None, description='The stock step\'s rows; omitted, the step starts empty.')
    given: Given = Field(default_factory=Given, description='Totals you know, to check the rows against.')
    make_items: list[str] = Field(default_factory=list, max_length=500, description=(
        'Stock items that are not yet items here and that you confirm may be made as new inventory parts with the '
        "company's default accounts (an income account named for sales, Cost of Goods Sold, Inventory Asset)."))
    clearing_account: str | None = Field(None, max_length=200, description=(
        'The account the opening balances against (an other current asset); omitted, Cutover Clearing, made on first '
        'use. It ends at 0.00 when everything ties.'))


class UpdateInput(_Input):
    draft: str = Field(**_ID, description='The draft.')
    expected_version: int = Field(ge=1, description='The version you read; a draft changed meanwhile is refused.')
    as_of: IsoDate | None = Field(None, min_length=10, max_length=10, description='A new cutover date.')
    label: str | None = Field(None, max_length=200)
    balances: Grid = Field(None, description='The balances step\'s rows; omitted, left as it is.')
    open_invoices: Grid = Field(None, description='The open invoices step\'s rows; omitted, left as it is.')
    unpaid_bills: Grid = Field(None, description='The unpaid bills step\'s rows; omitted, left as it is.')
    stock: Grid = Field(None, description='The stock step\'s rows; omitted, left as it is.')
    given: Given = Field(None, description='Replaces the totals given; a field left out is taken from the rows.')
    make_items: list[str] | None = Field(None, max_length=500, description='Replaces the confirmed list.')
    clearing_account: str | None = Field(None, max_length=200)


class DraftInput(_Input):
    draft: str = Field(**_ID, description='The draft.')


class ApplyInput(DraftInput):
    accept_differences: bool = Field(False, description=(
        "Post the opening although a total you gave does not tie to its rows (receivables, payables or stock), or the "
        "equity you typed does not balance: the difference stays in the clearing account in plain sight and tie-out "
        "says not tied until it is cleared. A person's act: an agent needs the capability opening.accept-differences "
        "granted by its principal."))


class TieOutInput(DraftInput):
    detail: Literal['differences', 'all'] = Field('differences', description=(
        '`differences` lists only rows that do not match; `all` lists every compared row.'))


class ReverseInput(DraftInput):
    fingerprint: str | None = Field(None, max_length=128, description=(
        'From a --dry-run of this reversal: refused if the opening changed since.'))


class QueryInput(_Input):
    state: State | None = Field(None, description='Only drafts in this state.')
    limit: int = Field(50, ge=1, le=200)


class StepOutput(BaseModel):
    step: Step
    kind: str = Field(description='The cutover file kind the rows are read as')
    source: Literal['content', 'attachment'] = Field(description='Pasted text, or an attachment')
    attachment: str | None
    date_order: str | None
    rows: int = Field(description='Rows read')
    content: str | None = Field(description='The pasted text, as kept; null for an attachment')
    problems: list[dict[str, Any]] = Field(description=(
        'What the rows alone show to be wrong, each with its line, code and problem; the plan checks them against '
        'the books'))


class DraftOutput(BaseModel):
    id: str
    label: str
    as_of: str
    state: State
    version: int
    steps: list[StepOutput] = Field(description='Each step given, in the order of the guided page')
    given: dict[str, MoneyOutput | None] = Field(description='Totals given to check against; null where left to the rows')
    make_items: list[str]
    clearing_account: str | None
    batch: str | None = Field(description='The batch the opening posted in; reverse it as one unit')
    summary: dict[str, Any] | None = Field(description='The last plan or apply: ready, counts, checks, blocking lines')
    created_at: str
    updated_at: str
    applied_at: str | None
    next: str | None = Field(None, description='The command to run next, or null')


class QueryOutput(BaseModel):
    rows: list[DraftOutput]


class PlanOutput(BaseModel):
    draft: DraftOutput
    plan: CutoverPlanOutput = Field(description='The cutover engine\'s plan of this draft: what apply would post')


class ApplyOutput(BaseModel):
    draft: DraftOutput
    apply: CutoverApplyOutput | None = Field(description='What the cutover engine posted; null for a dry run of a blocked plan')
    outside_batch: list[str] = Field(default_factory=list, description=(
        'What the opening changed that undoing it leaves as it is: the clearing account (at 0.00 once undone), a '
        'default sales tax item it set, the next invoice number it carried on, the steps kept as attachments'))


class TieOutOutput(BaseModel):
    draft: DraftOutput
    tie_out: CutoverTieOutOutput


class ReverseOutput(BaseModel):
    draft: DraftOutput
    batch: dict[str, Any] = Field(description='The batch after this call.')
    state: str = Field(description='live (dry run), reversing (more slices to run) or reversed.')
    reversed_now: int = Field(description='Records reversed by this call.')
    remaining: int = Field(description='Records still to reverse.')
    steps: list[dict[str, Any]] = Field(description='The plan: per record, the inverse commands still to run.')
    blockers: list[dict[str, Any]] = Field(description='What blocks the reversal (empty when it can run).')
    fingerprint: str | None = Field(description='Pass to the real run so it refuses if the opening changed since.')
    reversal_batch: str | None = Field(description='The batch recording the reversal, once begun.')
    event_id: str | None = Field(description='Audit event that finished the reversal.')
    stays: list[str] = Field(default_factory=list, description=(
        'What the opening changed outside its batch, which this undo leaves as it is'))


STEPS_HELP = _STEPS
