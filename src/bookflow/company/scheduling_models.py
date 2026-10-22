"""Inputs and outputs of `todo`, `notice` and `reminders` (R111)."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from bookflow.commands.common import CommonOut
from bookflow.company.ledger_reports import MoneyOutput
from bookflow.core.models import WriteOutput

_Date = Annotated[str, Field(min_length=10, max_length=10, pattern=r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$')]
_Selector = Annotated[str, Field(min_length=1, max_length=200)]
_Version = Annotated[int, Field(strict=True, ge=1)]
Priority = Literal['high', 'medium', 'low']
LinkType = Literal['customer', 'vendor', 'transaction', 'memorized']


class _Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


# ---------------------------------------------------------------- to-dos

class TodoCreateInput(_Input):
    title: str = Field(min_length=1, max_length=160, description='What is to be done, in a few words.')
    notes: str | None = Field(None, max_length=4000, description='Details: what to do and anything the person doing it needs to know.')
    due_date: _Date | None = Field(None, description="When it is due; omitted, today in the company's timezone.")
    priority: Priority = Field('medium', description='high, medium or low.')
    assignee: _Selector | None = Field(None, description='Who it is assigned to: a username or user id of a person or an agent who is a member of this company, or "me". Omitted, it is assigned to whoever creates it.')
    link_type: LinkType | None = Field(None, description='Kind of record it is about: customer, vendor, transaction (any document) or memorized (a memorized transaction).')
    link: _Selector | None = Field(None, description='That record: a customer or vendor name or id, a document id, or a memorized transaction name or id. Needs link_type.')


class TodoUpdateInput(_Input):
    todo: _Selector = Field(description='The to-do, by id.')
    expected_version: _Version | None = None
    title: str | None = Field(None, min_length=1, max_length=160, description='New title.')
    notes: str | None = Field(None, max_length=4000, description='New details; null clears them.')
    due_date: _Date | None = Field(None, description='New due date.')
    priority: Priority | None = None
    assignee: _Selector | None = Field(None, description='New assignee: a username or user id, or "me"; null leaves it unassigned.')
    link_type: LinkType | None = Field(None, description='New kind of linked record; null with link null removes the link.')
    link: _Selector | None = None
    status: Literal['open', 'inactive'] | None = Field(None, description='open, or inactive when it no longer needs doing. Use `todo complete` to mark it done.')


class TodoCompleteInput(_Input):
    todo: _Selector = Field(description='The to-do, by id.')
    expected_version: _Version | None = None


class TodoShowInput(_Input):
    todo: _Selector = Field(description='The to-do, by id.')


class TodoListInput(_Input):
    mine: bool = Field(False, description='Only the to-dos assigned to whoever is asking -- how an agent finds its own work.')
    assignee: _Selector | None = Field(None, description='Only the to-dos assigned to this username or user id.')
    status: Literal['open', 'done', 'inactive', 'all'] = Field('open', description='Which to-dos: open (the default), done, inactive or all.')
    due_by: _Date | None = Field(None, description='Only the to-dos due on or before this date.')
    link_type: LinkType | None = Field(None, description='Only the to-dos about this kind of record.')
    link: _Selector | None = Field(None, description='Only the to-dos about this record (needs link_type).')
    limit: int = Field(100, strict=True, ge=1, le=500)


class TodoOutput(CommonOut):
    title: str
    notes: str | None
    due_date: str
    priority: Priority
    status: Literal['open', 'done', 'inactive']
    overdue: bool = Field(description='Open and due before today.')
    assignee_id: str | None
    assignee_name: str | None
    assignee_kind: str | None
    link_type: str | None
    link_id: str | None
    link_label: str | None
    completed_at: str | None
    completed_by: str | None
    source_ref: str | None = Field(description='What created it when the scheduler did; null for one a person or agent made.')


class TodoWriteOutput(TodoOutput, WriteOutput):
    pass


class TodoListOutput(BaseModel):
    as_of: str
    items: list[TodoOutput]
    count: int
    truncated: bool


# ---------------------------------------------------------------- overdue notices

class NoticeDocument(BaseModel):
    transaction_id: str
    document_type: str
    number: str | None
    date: str
    due_date: str
    open_balance: MoneyOutput
    days_overdue: int


class NoticeOutput(CommonOut):
    customer_id: str
    customer_name: str
    period_key: str
    as_of: str
    template: Literal['statement', 'letter']
    statement_from: str
    overdue: MoneyOutput
    balance: MoneyOutput
    oldest_due_date: str
    days_overdue: int
    documents: list[NoticeDocument]
    status: Literal['ready', 'sent', 'dismissed']
    sent_at: str | None
    sent_by: str | None
    sent_method: str | None
    dismissed_reason: str | None
    letter: str = Field(description='The text of the notice as a letter; a statement notice prints the statement with this as its covering note.')
    print_path: str = Field(description='Browser path of the printable PDF (relative to the host).')


class NoticeWriteOutput(NoticeOutput, WriteOutput):
    pass


class NoticeListInput(_Input):
    status: Literal['ready', 'sent', 'dismissed', 'all'] = Field('ready', description='Which notices: ready to send (the default), sent, dismissed or all.')
    customer: _Selector | None = Field(None, description='Only this customer or job, by name or id.')
    limit: int = Field(100, strict=True, ge=1, le=500)


class NoticeListOutput(BaseModel):
    items: list[NoticeOutput]
    count: int
    truncated: bool
    ready_total: MoneyOutput | None = Field(description='Overdue balance across the notices ready to send.')


class NoticeShowInput(_Input):
    notice: _Selector = Field(description='The notice, by id.')


class NoticeMarkSentInput(_Input):
    notice: _Selector = Field(description='The notice, by id.')
    method: Literal['print', 'email', 'other'] = Field('print', description='How it went out: print, email or other. Bookflow did not send it; this records that a person did.')
    expected_version: _Version | None = None


class NoticeDismissInput(_Input):
    notice: _Selector = Field(description='The notice, by id.')
    note: str | None = Field(None, max_length=280, description="Why it is not being sent (e.g. 'called them, paying Friday').")
    expected_version: _Version | None = None


class NoticeGenerateInput(_Input):
    as_of: _Date | None = Field(None, description="Measure overdue balances on this date; omitted, today in the company's timezone.")


class NoticePolicyOutput(BaseModel):
    enabled: bool
    days_overdue: int | None
    frequency: Literal['weekly', 'monthly']
    minimum: MoneyOutput | None
    template: Literal['statement', 'letter']


class NoticeGenerateOutput(WriteOutput):
    as_of: str
    period_key: str | None
    policy: NoticePolicyOutput
    generated: list[NoticeOutput]
    generated_count: int
    already_for_period: int = Field(description='Customers who qualify but already have a notice (ready, sent or dismissed) for this period.')


# ---------------------------------------------------------------- reminders

class RemindersInput(_Input):
    as_of: _Date | None = Field(None, description="What is due as of this date; omitted, today in the company's timezone.")
    bills_days_ahead: int = Field(10, strict=True, ge=0, le=365, description='Bills to pay: list bills due within this many days (the anchor reminds 10 days before).')
    mine: bool = Field(False, description='To-dos: only those assigned to whoever is asking.')
    limit: int = Field(25, strict=True, ge=1, le=200, description='Most rows listed under each heading; every count and total covers them all.')


class MemorizedReminder(BaseModel):
    template_id: str
    template_name: str
    occurrence_id: str | None = Field(description='The waiting or blocked occurrence; null for a reminder slot not yet listed by a run.')
    slot_date: str
    due_date: str
    state: Literal['due', 'blocked']
    mode: str
    error_code: str | None
    error_message: str | None


class InvoiceReminder(BaseModel):
    transaction_id: str
    number: str | None
    customer_id: str | None
    customer_name: str | None
    date: str
    due_date: str
    open_balance: MoneyOutput
    days_overdue: int


class BillReminder(BaseModel):
    transaction_id: str
    number: str | None
    vendor_id: str | None
    vendor_name: str | None
    date: str
    due_date: str
    open_balance: MoneyOutput
    overdue: bool


class ReminderSection(BaseModel):
    key: str
    title: str
    count: int
    total: MoneyOutput | None


class RemindersOutput(BaseModel):
    as_of: str
    sections: list[ReminderSection] = Field(description='One heading per kind of reminder, with its count (and total where it is money), as the anchor summarises them.')
    memorized_due: list[MemorizedReminder]
    memorized_failed: list[MemorizedReminder]
    todos_due: list[TodoOutput]
    notices_ready: list[NoticeOutput]
    overdue_invoices: list[InvoiceReminder]
    bills_to_pay: list[BillReminder]
    total_count: int
