"""Input and output shapes for document delivery, increment 4: recurring schedules (R112;
design/document-delivery.md §6)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from bookflow.company.delivery_batch_models import BatchItem
from bookflow.company.shipment_models import IsoDate, Selector, _Strict

Kind = Literal['invoice', 'sales-receipt', 'estimate', 'statement', 'notice']
Frequency = Literal['daily', 'weekly', 'every_other_week', 'every_four_weeks', 'twice_a_month', 'monthly',
                    'every_other_month', 'quarterly', 'twice_a_year', 'annually']
Period = Literal['previous-month', 'month-to-date', 'since-last-run']


class _Fields(_Strict):
    queued: bool | None = Field(default=None, description='Every to-be-emailed document (of kind).')
    unsent: bool | None = Field(default=None, description='Documents of kind with no live or sent message.')
    ready: bool | None = Field(default=None, description='kind notice: every ready overdue notice.')
    period: Period | None = Field(default=None, description=(
        "Statements: previous-month, month-to-date (to the run's day) or since-last-run (from the day after the last "
        "run's period; the first run takes the previous month)."))
    customer: Selector | None = Field(default=None, description='Only this customer and its jobs.')
    customer_type: Selector | None = Field(default=None, description='Only customers of this type.')
    balance_over: str | None = Field(default=None, max_length=24, description='Statements: a balance over this.')
    method: Literal['email'] | None = Field(default=None, description='Statements: only customers whose method is '
                                                                      'email.')
    combine: bool | None = Field(default=None, description="One message per customer with all its documents.")
    template: Selector | None = Field(default=None, description="Template; default the kind's default.")
    frequency: Frequency | None = None
    start_date: IsoDate | None = Field(default=None, description='The first slot. A month-end start stays at month '
                                                                 'end.')
    stop_date: IsoDate | None = Field(default=None, description='No slot after this day.')
    remaining_runs: int | None = Field(default=None, ge=1, le=10000, description='Stop after this many runs.')
    mode: Literal['draft', 'send'] | None = Field(default=None, description=(
        'draft: each run leaves drafts. send: each run releases them; they leave only once a person has made the '
        'schedule live (delivery-schedule live), and until then wait for a go.'))
    catch_up_days: int | None = Field(default=None, ge=0, le=31, description=(
        'After the host was off, the latest missed slot runs only if no older than this (default 7).'))


class ScheduleCreateInput(_Fields):
    name: str = Field(min_length=1, max_length=100)
    kind: Kind


class ScheduleUpdateInput(_Fields):
    schedule: Selector = Field(description='Schedule name or id.')
    name: str | None = Field(default=None, min_length=1, max_length=100)
    kind: Kind | None = None
    no_end: bool = Field(default=False, description='Clear stop_date and remaining_runs.')
    confirm: str | None = Field(default=None, max_length=64, description=(
        "A live schedule stays live through a change only when a person passes the fingerprint of the check the dry "
        'run shows for the changed schedule; otherwise the change returns it to draft.'))
    expected_version: int | None = Field(default=None, ge=1)


class ScheduleSelector(_Strict):
    schedule: Selector = Field(description='Schedule name or id.')


class ScheduleShowInput(ScheduleSelector):
    runs: int = Field(default=10, ge=1, le=400, description='How many recent runs to list, newest first.')
    check: bool = Field(default=False, description=(
        'Include the check a person sees before making it live: the exact selection, cadence, first run and who it '
        'would reach today.'))


class ScheduleActInput(ScheduleSelector):
    expected_version: int | None = Field(default=None, ge=1)


class ScheduleLiveInput(ScheduleActInput):
    confirm: str | None = Field(default=None, max_length=64, description=(
        "The check's fingerprint, from the dry run: the schedule goes live only with exactly what was shown."))


class ScheduleQueryInput(_Strict):
    kind: Kind | None = None
    limit: int = Field(default=100, ge=1, le=500)


class RunOutput(BaseModel):
    run_id: str
    slot_date: str
    state: str = Field(description='drafting, done, skipped or failed.')
    batch: str | None = Field(default=None, description='The batch it drafted in (B-1001).')
    mode: str
    go: bool = Field(description="Whether its releases carry the establisher's go.")
    date_from: str | None = None
    date_to: str | None = None
    planned: int = 0
    drafted: int = 0
    released: int = 0
    skipped: int = 0
    reason: str | None = None
    reason_text: str | None = None
    started_at: str | None = Field(default=None, description='When the slot was claimed (UTC).')
    finished_at: str | None = None


class LiveCheck(BaseModel):
    """What a person confirms before a send schedule goes live: one check of its exact details."""
    selection: str = Field(description='What each run drafts, in words.')
    cadence: str = Field(description='How often, from when, until when.')
    first_run: str | None = Field(description='The slot that runs next.')
    period: str | None = Field(default=None, description="Statements: the period if it ran today.")
    template: str
    would_reach: list[BatchItem] = Field(default_factory=list, description='If it ran today: who gets what.')
    reach_count: int = 0
    skipped: list[BatchItem] = Field(default_factory=list)
    skipped_count: int = 0
    runs_as: str = Field(description='Whose authority it runs under once live.')
    fingerprint: str = Field(description='Pass it as confirm to make exactly this live.')


class ScheduleOutput(BaseModel):
    schedule_id: str | None = None
    name: str
    kind: str
    selection: dict = Field(default_factory=dict)
    selection_text: str = ''
    template: str | None = None
    combine: bool = False
    frequency: str
    cadence: str = ''
    start_date: str
    stop_date: str | None = None
    remaining_runs: int | None = None
    next_date: str | None = Field(default=None, description='The next slot; null once it has run out.')
    mode: str
    active: bool
    state: str = Field(description='draft, live or paused.')
    status_label: str = ''
    established_by: str | None = None
    established_by_name: str | None = None
    established_at: str | None = None
    paused_reason: str | None = None
    catch_up_days: int
    runs_as_name: str | None = None
    runs: list[RunOutput] = Field(default_factory=list, description='Recent runs, newest first.')
    check: LiveCheck | None = None
    ran: list[RunOutput] = Field(default_factory=list, description='run: the runs this call began, continued or '
                                                                   'skipped.')
    version: int | None = None
    warnings: list[str] = Field(default_factory=list)
    next_commands: list[str] = Field(default_factory=list)


class ScheduleRow(BaseModel):
    schedule_id: str
    name: str
    kind: str
    cadence: str
    next_date: str | None
    mode: str
    active: bool
    state: str
    status_label: str
    last_run: RunOutput | None = None
    version: int


class SchedulePageOutput(BaseModel):
    rows: list[ScheduleRow] = Field(default_factory=list)
    count: int
    truncated: bool = False
