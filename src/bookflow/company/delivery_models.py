"""Input and output shapes for document delivery, increment 1 (R112; design/document-delivery.md §2, §3, §8)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from bookflow.company.shipment_models import IsoDate, Selector, _Strict

Kind = Literal['invoice', 'sales-receipt', 'estimate', 'statement', 'notice']
Address = str
_ADDRESSES = dict(max_length=20)


class _Document(_Strict):
    kind: Kind = Field(description='invoice, sales-receipt, estimate, statement or notice.')
    document: Selector | None = Field(default=None, description=(
        'The invoice, sales receipt or estimate (number or id), or the overdue notice (id).'))
    customer: Selector | None = Field(default=None, description='A statement: the customer or job, by name or id.')
    date_from: IsoDate | None = Field(default=None, description='A statement: first day of the period.')
    date_to: IsoDate | None = Field(default=None, description='A statement: last day of the period.')


class DraftInput(_Document):
    template: Selector | None = Field(default=None, description="Template by name or id; default the kind's default.")
    to: list[Address] | None = Field(default=None, description="To; default the customer's email (a job without one "
                                     "uses its parent's).", **_ADDRESSES)
    cc: list[Address] | None = Field(default=None, description="Cc; default the customer's cc email.", **_ADDRESSES)
    bcc: list[Address] | None = Field(default=None, description="Bcc; default the company's copy-to.", **_ADDRESSES)
    subject: str | None = Field(default=None, min_length=1, max_length=500, description='Subject, instead of the '
                                "template's (plain text).")
    body: str | None = Field(default=None, max_length=20000, description="Body, instead of the template's.")
    again: bool = Field(default=False, description='A new message for a document already sent or already in the '
                        'outbox; it names the earlier one.')


class UpdateInput(_Strict):
    message: Selector = Field(description='Message number (M-1001) or id.')
    to: list[Address] | None = Field(default=None, **_ADDRESSES)
    cc: list[Address] | None = Field(default=None, **_ADDRESSES)
    bcc: list[Address] | None = Field(default=None, **_ADDRESSES)
    subject: str | None = Field(default=None, min_length=1, max_length=500)
    body: str | None = Field(default=None, max_length=20000)
    refresh: bool = Field(default=False, description=(
        'Attach the document as it is now (same message and number); refills subject and body not hand-written.'))
    expected_version: int | None = Field(default=None, ge=1)


class SendInput(_Strict):
    messages: list[Selector] = Field(min_length=1, max_length=100, description='Draft messages to release.')


class CancelInput(_Strict):
    message: Selector = Field(description='Message number or id.')
    expected_version: int | None = Field(default=None, ge=1)


class MarkSentInput(_Strict):
    message: Selector = Field(description='Message number or id.')
    method: Literal['mail-program', 'webmail', 'other'] = Field(
        default='mail-program', description='How it was sent: mail-program, webmail or other.')
    sent_at: str | None = Field(default=None, min_length=10, max_length=32, description=(
        'When it was sent (a date, or an ISO date and time); default now. Never in the future.'))
    expected_version: int | None = Field(default=None, ge=1)


class ExportInput(_Strict):
    messages: list[Selector] = Field(min_length=1, max_length=50, description=(
        'Messages to export: one gives a .eml file, several a .zip of them.'))


class MessageSelector(_Strict):
    message: Selector = Field(description='Message number (M-1001) or id.')


class MessageShowInput(MessageSelector):
    include_files: bool = Field(default=False, description='Also return each frozen file, base64.')


class QueryInput(_Strict):
    status: Literal['draft', 'ready', 'waiting', 'sending', 'sent', 'held', 'failed', 'cancelled',
                    'attention', 'live', 'all'] = Field(default='live', description=(
        'live (every unsent, uncancelled message; the default), draft, ready, waiting (ready, waiting for a '
        "person's go), sent, attention (held or failed), held, failed, cancelled or all."))
    kind: Kind | None = None
    customer: Selector | None = Field(default=None, description='Only this customer or job.')
    source: Selector | None = Field(default=None, description="Only messages carrying this document's id.")
    date_from: IsoDate | None = Field(default=None, description='Drafted on or after this day.')
    date_to: IsoDate | None = Field(default=None, description='Drafted on or before this day.')
    limit: int = Field(default=50, ge=1, le=500)


class TemplateCreateInput(_Strict):
    kind: Kind
    name: str = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=500, description='Subject with merge fields.')
    body: str = Field(max_length=20000, description='Plain-text body with merge fields.')
    is_default: bool = Field(default=False, description="Make it the kind's default.")


class TemplateUpdateInput(_Strict):
    template: Selector = Field(description='Template name or id (with kind when two kinds share a name).')
    kind: Kind | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    subject: str | None = Field(default=None, min_length=1, max_length=500)
    body: str | None = Field(default=None, max_length=20000)
    is_default: bool | None = Field(default=None, description="true makes it the kind's default (false is refused: "
                                    'make another template the default instead).')
    expected_version: int | None = Field(default=None, ge=1)


class TemplateSelector(_Strict):
    template: Selector = Field(description='Template name or id.')
    kind: Kind | None = None


class TemplateDeactivateInput(TemplateSelector):
    expected_version: int | None = Field(default=None, ge=1)


class TemplateQueryInput(_Strict):
    kind: Kind | None = None
    include_inactive: bool = False


class TemplateFieldsInput(_Strict):
    kind: Kind | None = Field(default=None, description='Only this kind.')


class TemplatePreviewInput(_Document):
    template: Selector | None = Field(default=None, description="Template by name or id; default the kind's default.")


# ---------------------------------------------------------------- outputs

class AttachedOutput(BaseModel):
    position: int
    role: str
    source_kind: str
    source_id: str
    source_number: str | None = None
    source_revision: str | None = None
    statement_from: str | None = None
    statement_to: str | None = None
    file_name: str
    media_type: str
    size_bytes: int
    sha256: str
    pdf_path: str | None = Field(default=None, description='Browser path of the frozen PDF.')
    content_base64: str | None = Field(default=None, description='The frozen bytes (show --include-files).')


class MessageOutput(BaseModel):
    id: str | None
    version: int
    number: str
    kind: str
    customer_id: str
    customer_name: str
    status: str
    status_label: str = Field(description="The state in words: Draft, Waiting for your go, Sent by hand, ...")
    hold_reason: str | None = None
    from_name: str | None = None
    from_address: str | None = None
    reply_to: str | None = None
    to: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    body: str
    message_id: str
    template_id: str | None = None
    template_name: str | None = None
    resend_of: str | None = Field(default=None, description='Number of the earlier message this sends again.')
    released_by: str | None = None
    released_at: str | None = None
    go_by: str | None = None
    go_at: str | None = None
    waiting_for_go: bool = False
    sent_at: str | None = None
    sent_via: str | None = None
    sent_method: str | None = None
    delivery: str | None = Field(default=None, description="For a sent message: unknown (nothing has said it "
                                 "arrived), accepted, delivered or bounced. Never 'opened'.")
    cancelled_reason: str | None = None
    stale: bool = Field(default=False, description='A source changed or was voided since it was drafted.')
    stale_reason: str | None = None
    documents: list[AttachedOutput] = Field(default_factory=list)
    created_at: str
    created_by: str
    updated_at: str
    updated_by: str
    existing: bool = Field(default=False, description='Draft: the message already in the outbox for this document.')
    connection: str = Field(default='none', description="The sending connection: none in this build, so nothing "
                            "leaves this machine.")
    warnings: list[str] = Field(default_factory=list)
    next_commands: list[str] = Field(default_factory=list)


class MessagesOutput(BaseModel):
    messages: list[MessageOutput]
    count: int
    warnings: list[str] = Field(default_factory=list)


class MessageRow(BaseModel):
    id: str
    number: str
    kind: str
    customer_id: str
    customer_name: str
    status: str
    status_label: str
    hold_reason: str | None = None
    to: list[str]
    subject: str
    documents: list[str] = Field(description='Source numbers (or statement periods) carried.')
    source_ids: list[str] = Field(default_factory=list)
    stale: bool = False
    waiting_for_go: bool = False
    updated_at: str
    updated_by: str
    sent_at: str | None = None


class MessagePageOutput(BaseModel):
    rows: list[MessageRow]
    count: int
    truncated: bool
    counts: dict[str, int] = Field(description='Messages per outbox tab: waiting, ready, sent, attention, draft, '
                                   'cancelled.')
    connection: str = 'none'


class EventOutput(BaseModel):
    ordinal: int
    event: str
    status: str
    at: str
    actor_id: str
    actor_name: str | None = None
    actor_kind: str
    via: str
    details: dict


class HistoryOutput(BaseModel):
    message_id: str
    number: str
    status: str
    events: list[EventOutput]


class ExportOutput(BaseModel):
    filename: str
    media_type: str
    size_bytes: int
    content_base64: str | None = Field(description='The .eml (or .zip) bytes, base64; null when nothing could be '
                                       'exported.')
    exported: list[str] = Field(description='Message numbers in the file.')
    held: list[str] = Field(default_factory=list, description='Messages held instead: a document changed or was '
                            'voided since drafting.')
    warnings: list[str] = Field(default_factory=list)


class TemplateOutput(BaseModel):
    id: str
    version: int
    kind: str
    name: str
    subject: str
    body: str
    is_default: bool
    active: bool
    updated_at: str
    updated_by: str
    warnings: list[str] = Field(default_factory=list)


class TemplatePageOutput(BaseModel):
    rows: list[TemplateOutput]
    count: int


class FieldOutput(BaseModel):
    field: str
    description: str
    kinds: list[str]


class FieldsOutput(BaseModel):
    fields: list[FieldOutput]
    conditional: str = '{{if field}} ... {{end}} shows its text only when the field has a value; no nesting.'


class PreviewOutput(BaseModel):
    kind: str
    source_id: str
    source_label: str
    customer_id: str
    customer_name: str | None = None
    to: list[str] = Field(default_factory=list, description="Who a draft would go to: the customer's email.")
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    template_id: str
    template_name: str
    subject: str
    body: str
    empty_fields: list[str]
    warnings: list[str] = Field(default_factory=list)
