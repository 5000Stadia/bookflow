"""Timekeeping (R106): weekly timesheets, the time clock and the job schedule.

The work lives in ``company/timesheets.py``, ``company/time_clocks.py``, ``company/job_schedule.py``
and ``company/timekeeping.py``. Timesheets and clock-outs write ordinary time entries, so
`time-activity invoice`, `report unbilled-costs` and job profitability read them unchanged.
"""
from bookflow.company import job_schedule, time_clocks, timekeeping, timesheets
from bookflow.company import timekeeping_models as m
from bookflow.core.registry import Plan, command

_WRITE_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALUE_RANGE']


def _write(name, description, inp, out, planner, applier, *, errors, positional=(), version=None, role='standard'):
    cmd = command(name, scope='company', description=description, input_model=inp, output_model=out,
                  writes={'company'}, required_role=role, capability='customer-work', accepts_idempotency_key=True,
                  positional=list(positional), version_source=version, error_codes=errors)(planner)
    cmd.applier(applier)
    return cmd


def _read(name, description, inp, out, planner, *, errors, positional=()):
    return command(name, scope='company', description=description, input_model=inp, output_model=out,
                   writes=set(), required_role='member', capability='customer-work', positional=list(positional),
                   error_codes=errors)(planner)


# ---------------------------------------------------------------------------- timesheets

def plan_timesheet_show(inp, ctx, s) -> Plan:
    return Plan(timesheets.show(s, ctx, inp))


def plan_timesheet_copy_last(inp, ctx, s) -> Plan:
    return Plan(timesheets.copy_last(s, ctx, inp))


def plan_timesheet_save(inp, ctx, s) -> Plan:
    return timesheets.plan_save(s, ctx, inp)


def plan_timesheet_preferences(inp, ctx, s) -> Plan:
    return Plan(timekeeping.preferences_show(s, inp))


def plan_timesheet_set_preferences(inp, ctx, s) -> Plan:
    return timekeeping.plan_preferences(s, ctx, inp)


timesheet_show = _read(
    'timesheet show', "One person's work week as the anchor's Weekly Timesheet shows it: one row per customer:job,"
    ' service item, class, billable and notes, hours for each day, totals by day and for the week. Each cell lists'
    ' its time entries; a cell a posted sale carries is locked, with a note naming the sale. The week starts on'
    ' the company work-week start (`timesheet preferences`). Leave employee out for your own sheet.',
    m.TimesheetShowInput, m.TimesheetOutput, plan_timesheet_show, errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])
timesheet_copy_last = _read(
    'timesheet copy-last', "The anchor's Copy Last Sheet: this week's sheet plus the rows of the person's last"
    ' earlier timesheet (customer:job, item, class, billable, notes) without hours, ready to fill in and pass to'
    ' `timesheet save`. Writes nothing.',
    m.TimesheetShowInput, m.TimesheetOutput, plan_timesheet_copy_last, errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])
timesheet_save = _write(
    'timesheet save', "Save one person's week: the whole sheet, each row a customer:job, service item, class,"
    ' billable, notes and seven daily hours. Each row and day with hours is one time entry, created, corrected or'
    ' withdrawn so the entries match the sheet exactly; a cell whose hours did not move writes nothing, so saving'
    ' the same sheet twice changes nothing. Hours a posted sale carries stay billed: lowering a cell below them is'
    ' refused (E_WORK_DEPENDENCY); more hours in a billed cell become a new entry. A row left out'
    ' withdraws its time. An agent may save any employee\'s sheet, as its principal could. Posts nothing. Example: 8 hours on the Smith job'
    ' yesterday is one row with 8 in that day.',
    m.TimesheetSaveInput, m.TimesheetWriteOutput, plan_timesheet_save, timesheets.apply_save,
    errors=[*_WRITE_ERRORS, 'E_VERSION_CONFLICT', 'E_WORK_DEPENDENCY', 'E_REASON_REQUIRED'])
timesheet_preferences = _read(
    'timesheet preferences', 'The timekeeping preferences: the first day of the work week, how clocked time is'
    ' rounded (none, 6 or 15 minutes) and the maximum shift a clock may run.',
    m.TimekeepingPreferencesInput, m.TimekeepingPreferencesOutput, plan_timesheet_preferences, errors=[])
timesheet_set_preferences = _write(
    'timesheet set-preferences', 'Set the first day of the work week (monday by default), clock rounding (none, the'
    ' default, or the nearest 6 or 15 minutes) and the maximum shift in hours (16 by default). Name only what'
    ' changes. Time already recorded keeps its hours.',
    m.TimekeepingPreferencesSetInput, m.TimekeepingPreferencesOutput, plan_timesheet_set_preferences,
    timekeeping.apply_preferences, errors=['E_VALIDATION'])


# ---------------------------------------------------------------------------- clock

def plan_clock_in(inp, ctx, s) -> Plan:
    return time_clocks.plan_in(s, ctx, inp)


def plan_clock_out(inp, ctx, s) -> Plan:
    return time_clocks.plan_out(s, ctx, inp)


def plan_clock_cancel(inp, ctx, s) -> Plan:
    return time_clocks.plan_cancel(s, ctx, inp)


def plan_clock_status(inp, ctx, s) -> Plan:
    return Plan(time_clocks.status(s, ctx, inp))


def plan_clock_link(inp, ctx, s) -> Plan:
    return timekeeping.plan_link(s, ctx, inp)


_AGENT = (' Live clocking is the one act an agent may do only for the person it acts for, on their instruction:'
          " its principal must be the employee's linked login (`clock link`). Recording anyone's time after the fact"
          ' is `timesheet save`, open to an agent as to its principal.')
clock_in = _write(
    'clock in', 'Start the clock for a person: now, or at a stated time today. Optionally the customer:job, service'
    ' item, class, billable and notes; they may be given at clock-out instead. One clock per person; it shows on'
    ' the home page while it runs. Records nothing yet.' + _AGENT,
    m.ClockInInput, m.ClockWriteOutput, plan_clock_in, time_clocks.apply_in, errors=_WRITE_ERRORS)
clock_out = _write(
    'clock out', "Stop a person's clock and record the time: one time entry per company-local day (a night shift is"
    ' split at midnight), rounded per the company preference to exact hours. Needs a customer:job and service item'
    ' from clock-in or here. A clock that ran past the maximum shift was probably forgotten: give the end time the'
    ' person stopped.' + _AGENT,
    m.ClockOutInput, m.ClockWriteOutput, plan_clock_out, time_clocks.apply_out,
    errors=[*_WRITE_ERRORS, 'E_REASON_REQUIRED'])
clock_cancel = _write(
    'clock cancel', 'Throw away a running clock started by mistake, with a reason. Records no time; the clock stays'
    ' readable.' + _AGENT,
    m.ClockCancelInput, m.ClockWriteOutput, plan_clock_cancel, time_clocks.apply_cancel,
    errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_REASON_REQUIRED'])
clock_status = _read(
    'clock status', 'Who is clocked in: each running clock with its start, hours so far, and whether it has passed'
    ' the maximum shift and must be closed with a stated end time.',
    m.ClockStatusInput, m.ClockStatusOutput, plan_clock_status, errors=['E_RECORD_NOT_FOUND'])
clock_link = _write(
    'clock link', "Say which person's login is an employee, or unlink it (no user). Then that person's own"
    ' timesheet and clock need no employee named, and an agent acting for them may clock them in and out on their'
    ' instruction. A person\'s act.',
    m.ClockLinkInput, m.ClockLinkOutput, plan_clock_link, timekeeping.apply_link,
    errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND'])


# ---------------------------------------------------------------------------- schedule

def plan_schedule_create(inp, ctx, s) -> Plan:
    return job_schedule.plan_write(s, ctx, inp, 'create')


def plan_schedule_update(inp, ctx, s) -> Plan:
    return job_schedule.plan_write(s, ctx, inp, 'update')


def plan_schedule_cancel(inp, ctx, s) -> Plan:
    return job_schedule.plan_write(s, ctx, inp, 'cancel')


def plan_schedule_show(inp, ctx, s) -> Plan:
    return Plan(job_schedule.show(s, inp))


def plan_schedule_calendar(inp, ctx, s) -> Plan:
    return Plan(job_schedule.calendar(s, ctx, inp))


_VERSION = ('schedule show', 'entry', 'version')
schedule_create = _write(
    'schedule create', 'Put an employee on a customer:job, or on a work order, from a company-local start to an end'
    ' (YYYY-MM-DDTHH:MM), with notes. Double-booking the employee -- another entry or a scheduled work order they'
    ' are assigned to, overlapping -- saves with a warning naming it. Posts nothing and records no time.',
    m.ScheduleCreateInput, m.ScheduleWriteOutput, plan_schedule_create, job_schedule.apply_write,
    errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE'])
schedule_update = _write(
    'schedule update', 'Move or reassign a schedule entry: name only what changes. Overlaps are warned about.',
    m.ScheduleUpdateInput, m.ScheduleWriteOutput, plan_schedule_update, job_schedule.apply_write,
    errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VERSION_CONFLICT'],
    positional=['entry'], version=_VERSION)
schedule_cancel = _write(
    'schedule cancel', 'Cancel a schedule entry with a reason; it stays readable.',
    m.ScheduleCancelInput, m.ScheduleWriteOutput, plan_schedule_cancel, job_schedule.apply_write,
    errors=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_REASON_REQUIRED'], positional=['entry'], version=_VERSION)
schedule_show = _read('schedule show', 'Show a schedule entry with any double-bookings it has.',
                      m.ScheduleShowInput, m.ScheduleEntryOutput, plan_schedule_show, errors=['E_RECORD_NOT_FOUND'],
                      positional=['entry'])
schedule_calendar = _read(
    'schedule calendar', "The calendar for a day, a work week or a month, for one employee (\"what is Dave doing"
    " tomorrow?\"), one customer:job, or everyone: schedule entries and work orders' scheduled times with their"
    ' assignees, by day, each with any double-booking.',
    m.ScheduleCalendarInput, m.ScheduleCalendarOutput, plan_schedule_calendar,
    errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])
