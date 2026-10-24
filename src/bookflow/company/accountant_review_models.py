"""Inputs and outputs of the accountant review commands (R121)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bookflow.company.ledger_reports import MoneyOutput, iso_date
from bookflow.core.models import WriteOutput

_DATE = dict(min_length=10, max_length=10)


class _In(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


# ------------------------------------------------------------------ the external accountant

class AccountantAddInput(_In):
    user: str = Field(min_length=1, max_length=64, description='Username or user id of a person holding the standard role on this company (no admin, as the anchor's External Accountant).')
    through: str = Field(**_DATE, description='End of the review period, YYYY-MM-DD: the accountant posts only on or before it.')
    note: str | None = Field(None, max_length=500, description='What the engagement is, such as 2026 year-end review.')
    _through = field_validator('through')(iso_date)


class AccountantUserInput(_In):
    user: str = Field(min_length=1, max_length=64, description='Username or user id of the external accountant.')


class AccountantOut(BaseModel):
    id: str
    user_id: str
    username: str | None
    display_name: str | None
    through: str = Field(description='End of the review period.')
    active: bool
    note: str | None
    version: int


class AccountantWriteOutput(WriteOutput, AccountantOut):
    pass


class AccountantListOutput(BaseModel):
    accountants: list[AccountantOut]


class AccountantListInput(_In):
    include_inactive: bool = Field(False, description='Also list removed designations.')


class ReclassifyInput(_In):
    transaction: str = Field(min_length=1, max_length=26, description='Id of the entry whose amount is moved (any posted document).')
    from_account: str = Field(min_length=1, max_length=1004, description='The account the amount is in now: id, full name or number.')
    to_account: str = Field(min_length=1, max_length=1004, description='The account it belongs in: id, full name or number.')
    amount: str | None = Field(None, max_length=32, description="Amount to move; default all of the entry's amount in from_account.")
    date: str | None = Field(None, **_DATE, description="Date of the adjusting entry; default the entry's own date.")
    memo: str | None = Field(None, max_length=2000, description='Memo; default says what was moved from where to where.')
    _date = field_validator('date')(lambda v: v if v is None else iso_date(v))


class MarkEntryInput(_In):
    transaction: str = Field(min_length=1, max_length=26, description='Transaction id of an entry on the entries-to-review list.')
    note: str | None = Field(None, max_length=1000, description='What the accountant found, kept with the mark.')


class MarkEntryOutput(BaseModel):
    transaction_id: str
    revision_id: str
    flags: list[str]
    note: str | None
    changed: bool


class ReviewedThroughInput(_In):
    through: str = Field(**_DATE, description='The accountant has reviewed the books through this date, YYYY-MM-DD.')
    note: str | None = Field(None, max_length=1000, description='What the accountant found or adjusted.')
    _through = field_validator('through')(iso_date)


class ReviewedThroughOut(BaseModel):
    through: str
    note: str | None
    accountant_user_id: str
    accountant_name: str | None
    at: str
    from_copy: bool = Field(description="True when the mark came back in an accountant's change file.")


class ReviewedThroughWriteOutput(WriteOutput, ReviewedThroughOut):
    pass


# ------------------------------------------------------------------ the accountant's copy

class CopyExportInput(_In):
    accountant: str | None = Field(None, min_length=1, max_length=64, description='Username or user id of the designated external accountant the copy is for; default the one designated. Every change the copy sends back is posted as them.')
    through: str = Field(**_DATE, description='Dividing date, YYYY-MM-DD: the accountant works on or before it; these books then refuse changes dated on or before it until the copy comes back or is cancelled.')
    _through = field_validator('through')(iso_date)


class CopyOut(BaseModel):
    copy_id: str
    side: Literal['owner', 'accountant'] = Field(description="owner: these books made the copy; accountant: these books are the accountant's copy.")
    status: Literal['out', 'returned', 'cancelled', 'working']
    dividing_date: str
    origin_company_id: str
    accountant_name: str | None = Field(None, description='The external accountant the copy was made for; its changes are posted as them.')
    created_at: str
    created_by_name: str | None
    file_name: str | None = None
    archive_sha256: str | None = None
    closed_at: str | None = None
    closed_by_name: str | None = None
    close_reason: str | None = None


class CopyExportOutput(WriteOutput, CopyOut):
    path: str | None = Field(None, description='Full path of the archive; shown to installation administrators only.')
    manifest_file: str | None = Field(None, description='The small manifest written beside the archive, naming the dividing date.')
    size_bytes: int | None = None


class CopyStatusOutput(BaseModel):
    current: CopyOut | None = Field(description='The copy out (owner) or this copy (accountant); null when none.')
    history: list[CopyOut] = Field(description='Earlier copies, newest first.')
    locked_through: str | None = Field(description='These books refuse changes dated on or before this date while the copy is out.')
    restricted_to: str | None = Field(description="This is an accountant's copy: it posts only on or before this date.")


class CopyCancelOutput(WriteOutput, CopyOut):
    pass


class FileInput(_In):
    file: str = Field(min_length=1, max_length=4096, json_schema_extra={'x-bookflow-local-path': True},
                      description="Full path of the accountant's change file on the machine running Bookflow.")


class CopyImportInput(FileInput):
    changes: list[str] | None = Field(None, max_length=1000, description='Keys of the changes to take (from `accountant-copy preview`); default every new change.')


class ChangeLine(BaseModel):
    account_id: str
    account_name: str | None
    side: Literal['debit', 'credit']
    amount: str
    description: str | None = None


class ChangeOut(BaseModel):
    key: str = Field(description='Stable key of the change in its file.')
    kind: Literal['journal', 'reclassification', 'note', 'review_mark', 'reviewed_through']
    date: str | None = Field(description='Accounting date (an entry) or reviewed-through date; null for a note.')
    summary: str
    reason: str | None = Field(description="The accountant's reason, as their copy recorded it.")
    by_name: str | None
    lines: list[ChangeLine] = Field(default_factory=list)
    state: Literal['new', 'imported', 'refused'] = Field(description='new: can be taken; imported: already taken (by result_id); refused: cannot be taken, see problem.')
    problem: str | None = None
    result_id: str | None = None


class ChangesFileOutput(BaseModel):
    copy_id: str
    company_id: str
    dividing_date: str
    accountant_name: str | None
    created_at: str
    changes: list[ChangeOut]
    counts: dict[str, int] = Field(description='Changes by state.')


class CopyChangesOutput(WriteOutput, ChangesFileOutput):
    file_name: str = Field(description="The change file, in this copy's company folder exports/.")
    path: str | None = Field(None, description='Full path; shown to installation administrators only.')
    skipped: list[str] = Field(default_factory=list, description='What the change file could not carry, and why.')


class CopyImportOutput(WriteOutput, ChangesFileOutput):
    imported: list[str] = Field(description='Keys of the changes taken by this import.')
    status: str = Field(description="The copy's status after the import.")


# ------------------------------------------------------------------ review support

class ReviewChecklistInput(_In):
    as_of: str = Field(**_DATE, description='End of the period under review, YYYY-MM-DD.')
    _as_of = field_validator('as_of')(iso_date)


class ChecklistEntry(BaseModel):
    transaction_id: str
    type: str
    number: str | None
    date: str
    flags: list[str]


class ChecklistAccount(BaseModel):
    account_id: str
    name: str
    type: str
    last_reconciled: str | None = Field(description='Statement date of the latest finished reconciliation; null when never reconciled.')
    balance: MoneyOutput


class ChecklistBalance(BaseModel):
    account_id: str
    name: str
    count: int | None = None
    balance: MoneyOutput


class ChecklistItem(BaseModel):
    key: Literal['entries_to_review', 'uncategorized', 'unreconciled', 'open_items', 'reviewed_through']
    title: str
    done: bool = Field(description='True when nothing is left to look at.')
    detail: str


class ReviewChecklistOutput(BaseModel):
    as_of: str
    items: list[ChecklistItem]
    entries_to_review: list[ChecklistEntry] = Field(description='Up to 50 entries on the entries-to-review list, newest first.')
    entries_to_review_count: int
    uncategorized: list[ChecklistBalance]
    unreconciled: list[ChecklistAccount] = Field(description='Active bank and card accounts with activity, not reconciled through as_of.')
    open_items: list[ChecklistBalance] = Field(description='Accounts receivable, accounts payable and undeposited funds as of as_of.')
    reviewed_through: ReviewedThroughOut | None
    accountant_changes: int = Field(description='Changes an accountant made in these books or that came back from a copy (`report accountant-changes`).')


class AccountantChangesInput(_In):
    limit: int = Field(100, ge=1, le=500, description='Most changes to list, newest first.')


class AccountantChangeRow(BaseModel):
    at: str
    command: str
    summary: str
    accountant_user_id: str
    accountant_name: str | None
    reason: str | None
    source_ref: str | None
    from_copy: bool
    records: list[str] = Field(description='record_type:record_id of what the change wrote.')


class AccountantChangesOutput(BaseModel):
    count: int = Field(description='Every change found; rows list up to limit.')
    rows: list[AccountantChangeRow]
    journal_entries: int
    reclassifications: int
    notes: int
    review_marks: int
