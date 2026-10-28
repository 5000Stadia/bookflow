"""Input and output shapes for document delivery, increment 3: flags, the queue and batches (R112;
design/document-delivery.md §5)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from bookflow.company.shipment_models import IsoDate, Selector, _Strict

FlagKind = Literal['invoice', 'sales-receipt', 'estimate']


class FlagInput(_Strict):
    kind: FlagKind = Field(description='invoice, sales-receipt or estimate.')
    document: Selector = Field(description='The document, by number or id.')


class UnflagInput(FlagInput):
    clear: bool = Field(default=False, description=(
        'Remove the flag instead: back to neutral, where the auto-flag date decides. Without it, an explicit '
        'opt-out (never email this).'))


class FlagOutput(BaseModel):
    kind: str
    document_id: str
    number: str | None = None
    customer_id: str
    customer_name: str
    flag: Literal['email-later', 'not-to-email'] | None = Field(default=None, description=(
        "email-later (flagged), not-to-email (an explicit opt-out, which wins over the auto-flag date) or null."))
    queued: bool = Field(description='Whether it is to be emailed now (delivery queue lists it).')
    why: str = Field(description='Why it is or is not to be emailed, in words.')
    message: str | None = Field(default=None, description='The message already carrying it, if any.')
    version: int | None = None
    warnings: list[str] = Field(default_factory=list)


class QueueInput(_Strict):
    kind: FlagKind | None = Field(default=None, description='Only this kind.')
    method: Literal['email', 'mail'] = Field(default='email', description=(
        'email: the documents to be emailed; mail: the customers who prefer mail (lists only).'))
    customer: Selector | None = Field(default=None, description='Only this customer and its jobs.')
    document: Selector | None = Field(default=None, description='Only this document id.')
    limit: int = Field(default=200, ge=1, le=2000)


class QueueRow(BaseModel):
    kind: str
    document_id: str
    number: str | None = None
    date: str
    customer_id: str
    customer_name: str
    method: str = Field(description="The customer's effective delivery method (a job inherits its parent's).")
    why: Literal['flagged', 'auto'] = Field(description='flagged (Email later) or auto (the auto-flag date and the '
                                            "customer's email preference).")
    to: list[str] = Field(default_factory=list)
    problem: str | None = Field(default=None, description='no address: the customer has no email.')


class MailCustomer(BaseModel):
    customer_id: str
    customer_name: str


class QueueOutput(BaseModel):
    rows: list[QueueRow] = Field(default_factory=list)
    customers: list[MailCustomer] = Field(default_factory=list, description='method mail: who prefers mail.')
    count: int
    truncated: bool = False
    auto_flag_from: str | None = Field(default=None, description="The company's auto-flag date; null is off.")
    flag: Literal['email-later', 'not-to-email'] | None = Field(default=None, description=(
        "With document: that document's flag (null: neutral, the auto-flag date decides)."))


class BatchInput(_Strict):
    batch: Selector | None = Field(default=None, description='Continue this batch: its next chunk (B-1001).')
    kind: Literal['invoice', 'sales-receipt', 'estimate', 'statement', 'notice'] | None = None
    queued: bool = Field(default=False, description='Every to-be-emailed document (of kind, if given).')
    unsent: bool = Field(default=False, description='Documents of kind with no live or sent message.')
    ready: bool = Field(default=False, description='kind notice: every ready overdue notice.')
    period: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}$', description='Statements: a month, 2026-09.')
    date_from: IsoDate | None = Field(default=None, description='Documents dated from; statements: period start.')
    date_to: IsoDate | None = Field(default=None, description='Documents dated to; statements: period end.')
    customer: Selector | None = Field(default=None, description='Only this customer and its jobs.')
    customer_type: Selector | None = Field(default=None, description='Only customers of this type.')
    balance_over: str | None = Field(default=None, max_length=24, description=(
        'Statements: a balance over this amount (default 0).'))
    method: Literal['email'] | None = Field(default=None, description=(
        'Statements: only customers whose method is email.'))
    combine: bool = Field(default=False, description="One message per customer with all its documents.")
    send: bool = Field(default=False, description='Release each draft too.')
    template: Selector | None = Field(default=None, description="Template; default the kind's default.")
    exclude: list[Selector] = Field(default_factory=list, max_length=5000, description=(
        "Leave these out: the dry run's keys (a combined row's key covers its whole group), or document ids."))
    fingerprint: str | None = Field(default=None, max_length=64, description=(
        "The dry run's fingerprint: the run is refused if the set changed since. Continuing: the batch's."))
    seen: list[str] = Field(default_factory=list, max_length=5000, description=(
        "Optional: the dry run's row fingerprints, so a refusal names what changed."))


class BatchItem(BaseModel):
    key: str = Field(description='What exclude takes: the document id, a combined row\'s group key, or the customer '
                     'id for a statement.')
    kind: str
    customer_id: str
    customer_name: str
    documents: list[str] = Field(default_factory=list, description='Invoice 1007, Statement 2026-09-01 to ...')
    to: list[str] = Field(default_factory=list)
    reason: str | None = Field(default=None, description=(
        'Skipped: no_address, prefers_mail, not_email, opted_out, in_outbox, sent, excluded, refused, not_queued or '
        'changed (since the preview).'))
    reason_text: str | None = None
    message: str | None = Field(default=None, description='The message drafted for it, or the one already there.')
    status: str | None = None
    fingerprint: str | None = Field(default=None, description='This row: documents, versions and recipients.')


class BatchOutput(BaseModel):
    batch_id: str | None = None
    number: str | None = None
    state: Literal['preview', 'drafting', 'done', 'cancelled', 'nothing'] = Field(description=(
        'preview (dry run), drafting (run next), done, cancelled, or nothing (nothing to draft; no batch kept).'))
    kind: str | None = None
    selection: dict = Field(default_factory=dict)
    planned: int = Field(description='Messages to draft.')
    drafted: int = 0
    released: int = 0
    remaining: int = 0
    would_draft: list[BatchItem] = Field(default_factory=list, description='Preview and show: who gets what.')
    skipped: list[BatchItem] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list, description='Messages drafted by this call.')
    cancelled: list[str] = Field(default_factory=list, description='Cancel: the messages cancelled.')
    kept: list[BatchItem] = Field(default_factory=list, description=(
        'Cancel: the messages that block (released, exported or sent), each with why.'))
    next: dict | None = Field(default=None, description='Input for the next chunk (delivery batch), while drafting.')
    fingerprint: str | None = Field(default=None, description=(
        'The exact set (documents, versions, recipients); pass it to the run so a changed set is refused.'))
    go: bool = Field(default=False, description="Whether its releases carry a person's go (decided when it began).")
    open_batches: list[str] = Field(default_factory=list, description=(
        'Dry run: unfinished batches over this same selection; continue one instead of starting another.'))
    version: int | None = None
    warnings: list[str] = Field(default_factory=list)
    next_commands: list[str] = Field(default_factory=list)


class BatchSelector(_Strict):
    batch: Selector = Field(description='Batch number (B-1001) or id.')


class BatchCancelInput(BatchSelector):
    expected_version: int | None = Field(default=None, ge=1)


class BatchQueryInput(_Strict):
    state: Literal['unfinished', 'all'] = Field(default='unfinished', description=(
        'unfinished: still drafting, or with drafts to send or messages waiting for a go; all: every batch.'))
    limit: int = Field(default=50, ge=1, le=500)


class BatchRow(BaseModel):
    batch_id: str
    number: str
    kind: str
    state: str
    planned: int
    position: int
    drafts: int = Field(description='Messages still drafts.')
    waiting: int = Field(description="Ready messages waiting for a person's go.")
    created_at: str
    next_commands: list[str] = Field(default_factory=list)


class BatchQueryOutput(BaseModel):
    rows: list[BatchRow] = Field(default_factory=list)
    count: int
    truncated: bool = False
