"""Inputs and outputs of weekly timesheets, the time clock and the job schedule (R106)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bookflow.company.journal_models import _Date, _Version
from bookflow.company.sales_models import Selector, Text


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


_EMPLOYEE = ('Employee ID or name. Leave it out for your own: the employee your login is linked to '
             '(`clock link`), or, for an agent, its principal\'s.')
_HOURS = 'Decimal hours of up to six places: 8, 7.5 or 0.25. Empty or 0 is no time that day.'
WeekDay = Literal['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
_LOCAL = ('A time: ISO 8601 with a zone (2026-10-20T08:00:00-07:00), or company-local YYYY-MM-DDTHH:MM.')


# ---------------------------------------------------------------------------- preferences

class TimekeepingPreferencesInput(_Strict):
    pass


class TimekeepingPreferencesSetInput(_Strict):
    week_start: WeekDay | None = Field(None, description='First day of the work week on timesheets (monday by default).')
    clock_rounding: Literal['none', '6', '15'] | None = Field(
        None, description='Round clocked time to the nearest 6 or 15 minutes, or not at all (none, the default).')
    max_shift_hours: int | None = Field(None, ge=1, le=24, description=(
        'Longest a clock may run (16 by default); a clock open longer must be closed with a stated end time.'))


class TimekeepingPreferencesOutput(_Strict):
    week_start: WeekDay
    clock_rounding: Literal['none', '6', '15']
    max_shift_hours: int
    version: int
    changed: bool | None = None


# ---------------------------------------------------------------------------- timesheets

class TimesheetShowInput(_Strict):
    employee: Selector | None = Field(None, description=_EMPLOYEE)
    week: _Date | None = Field(None, description='Any day of the week to show; this week by default.')


class TimesheetRowInput(_Strict):
    customer: Selector = Field(description='Customer:job the time was worked for.')
    item: Selector = Field(description='Service item the time is charged as.')
    class_id: Selector | None = Field(None, description='Class, when classes are on.')
    billable: bool = True
    notes: Text | None = Field(None, description='What was done; it becomes the invoice line description.')
    hours: list[str | None] = Field(min_length=7, max_length=7, description=(
        'Seven cells, the first day of the week first. ' + _HOURS))


class TimesheetSaveInput(_Strict):
    employee: Selector | None = Field(None, description=_EMPLOYEE)
    week: _Date = Field(description='Any day of the week being saved.')
    rows: list[TimesheetRowInput] = Field(max_length=100, description=(
        'The whole sheet. A row or cell left out, or emptied, withdraws the time that was there.'))
    expected_sheet_version: str | None = Field(None, max_length=64, description=(
        'The sheet_version `timesheet show` returned; refused if the week changed since.'))


class TimesheetCell(_Strict):
    date: str
    hours: str | None
    entries: list[str]
    locked: bool
    lock_note: str | None = None


class TimesheetRow(_Strict):
    customer_id: str
    customer_name: str
    item_id: str
    item_name: str
    class_id: str | None
    class_name: str | None
    payroll_item: None = Field(None, description='Reserved for payroll items; always null until payroll exists.')
    billable: bool
    notes: str | None
    hours: list[str | None]
    total: str
    cells: list[TimesheetCell]
    locked: bool


class TimesheetChange(_Strict):
    date: str
    customer_name: str
    item_name: str
    action: Literal['create', 'update', 'void']
    hours: str
    time_activity: str | None


class TimesheetOutput(_Strict):
    employee_id: str
    employee_name: str
    week_start: str
    week_end: str
    week_start_day: WeekDay
    days: list[str]
    rows: list[TimesheetRow]
    day_totals: list[str]
    week_total: str
    sheet_version: str
    copied_from: str | None = None
    lock_note: str | None = None


class TimesheetWriteOutput(TimesheetOutput):
    changed: bool
    changes: list[TimesheetChange] = []
    warnings: list[str] = []


# ---------------------------------------------------------------------------- clock

class ClockInInput(_Strict):
    employee: Selector | None = Field(None, description=_EMPLOYEE)
    customer: Selector | None = Field(None, description='Customer:job; may be given at clock-out instead.')
    item: Selector | None = Field(None, description='Service item; may be given at clock-out instead.')
    class_id: Selector | None = None
    billable: bool = True
    notes: Text | None = None
    at: str | None = Field(None, max_length=40, description='When the person started; now by default. ' + _LOCAL)


class ClockOutInput(_Strict):
    employee: Selector | None = Field(None, description=_EMPLOYEE)
    customer: Selector | None = Field(None, description='Customer:job, if not given at clock-in or to change it.')
    item: Selector | None = Field(None, description='Service item, if not given at clock-in or to change it.')
    class_id: Selector | None = None
    billable: bool | None = None
    notes: Text | None = None
    end: str | None = Field(None, max_length=40, description=(
        'When the person stopped; now by default. Required once the clock has run past the maximum shift. ' + _LOCAL))


class ClockCancelInput(_Strict):
    employee: Selector | None = Field(None, description=_EMPLOYEE)


class ClockStatusInput(_Strict):
    employee: Selector | None = Field(None, description='One employee; every open clock by default.')


class ClockLinkInput(_Strict):
    employee: Selector = Field(description='Employee ID or name.')
    user: str | None = Field(max_length=64, description='Username or user id of the login that is this employee; null unlinks.')


class ClockOutput(_Strict):
    id: str
    version: int
    employee_id: str
    employee_name: str
    customer_id: str | None
    customer_name: str | None
    item_id: str | None
    item_name: str | None
    class_id: str | None
    billable: bool
    notes: str | None
    status: Literal['open', 'closed', 'cancelled']
    clock_in_at: str
    clock_in_local: str
    clock_out_at: str | None
    clock_out_local: str | None
    end_stated: bool
    elapsed_hours: str = Field(description='Hours since clock-in (or to clock-out), to six places, before rounding.')
    over_max_shift: bool
    entries: list[str] = []
    recorded_hours: list[dict] = Field(default_factory=list, description='Per day: date and the rounded hours recorded.')
    cancel_reason: str | None = None


class ClockWriteOutput(ClockOutput):
    changed: bool = True
    warnings: list[str] = []


class ClockStatusOutput(_Strict):
    now: str
    clocks: list[ClockOutput]
    max_shift_hours: int
    clock_rounding: str


class ClockLinkOutput(_Strict):
    employee_id: str
    employee_name: str
    user_id: str | None
    username: str | None
    changed: bool


# ---------------------------------------------------------------------------- schedule

class ScheduleCreateInput(_Strict):
    employee: Selector = Field(description='Employee ID or name.')
    customer: Selector | None = Field(None, description='Customer:job; or name a work order instead.')
    work_order: Selector | None = Field(None, description='Work order ID or number; its customer:job is the job.')
    starts: str = Field(min_length=16, max_length=16, description='Company-local start, YYYY-MM-DDTHH:MM.')
    ends: str = Field(min_length=16, max_length=16, description='Company-local end, YYYY-MM-DDTHH:MM, after the start.')
    notes: Text | None = None

    @model_validator(mode='after')
    def target(self):
        if not self.customer and not self.work_order:
            raise ValueError('name a customer:job or a work order')
        return self


class ScheduleUpdateInput(_Strict):
    entry: Selector = Field(description='Schedule entry ID.')
    expected_version: _Version | None = None
    employee: Selector | None = None
    customer: Selector | None = None
    work_order: Selector | None = Field(None, description='Work order; null takes the entry off its work order.')
    starts: str | None = Field(None, min_length=16, max_length=16)
    ends: str | None = Field(None, min_length=16, max_length=16)
    notes: Text | None = None


class ScheduleCancelInput(_Strict):
    entry: Selector = Field(description='Schedule entry ID.')
    expected_version: _Version | None = None


class ScheduleShowInput(_Strict):
    entry: Selector = Field(description='Schedule entry ID.')


class ScheduleCalendarInput(_Strict):
    employee: Selector | None = Field(None, description='One employee\'s calendar.')
    customer: Selector | None = Field(None, description='One customer:job\'s calendar.')
    view: Literal['day', 'week', 'month'] = 'week'
    date: _Date | None = Field(None, description='Any day in the period; today by default.')
    include_cancelled: bool = False


class ScheduleConflict(_Strict):
    kind: Literal['schedule', 'work_order']
    id: str
    label: str
    starts: str
    ends: str


class ScheduleEntryOutput(_Strict):
    id: str
    kind: Literal['schedule', 'work_order'] = 'schedule'
    version: int
    employee_id: str | None
    employee_name: str | None
    customer_id: str
    customer_name: str
    work_order_id: str | None
    work_order_number: str | None
    starts: str
    ends: str
    notes: str | None
    status: str
    cancel_reason: str | None = None
    conflicts: list[ScheduleConflict] = []


class ScheduleWriteOutput(ScheduleEntryOutput):
    changed: bool = True
    warnings: list[str] = []


class CalendarDay(_Strict):
    date: str
    items: list[ScheduleEntryOutput]


class ScheduleCalendarOutput(_Strict):
    view: str
    date_from: str
    date_to: str
    employee_id: str | None
    employee_name: str | None
    customer_id: str | None
    customer_name: str | None
    days: list[CalendarDay]
    conflicts: int
