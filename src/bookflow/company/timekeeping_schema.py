"""Timekeeping (co0090, R106): preferences, who a login is, the time clock and the job schedule.

The weekly timesheet has no table of its own. As in the anchor, a timesheet is a view over the time
entries a person already has: one row per customer:job, service item, class, billable and notes, one
column per day, each cell the entries of that row and day. Saving a sheet writes those entries
(customer-work documents of kind ``time_activity``), so billing and every report read time exactly
as before.

``timekeeping_preferences`` is the company's one choice of the first day of the work week, how
clocked time is rounded and the longest shift a clock may run before it must be closed with a
stated end time.

``timekeeping_people`` says which hub login is which employee. It is what lets an agent clock a
person in or out only on that person's own instruction: the agent's principal must be the
employee's login.

``time_clocks`` holds each clock-in. Clocking out closes it and records the time entries it wrote;
a clock is never rewritten after it closes.

``schedule_entries`` assigns an employee to a job, or to a work order, over a local date and time
range. Nothing here posts.
"""
import sqlalchemy as sa

WEEK_DAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
ROUNDINGS = (0, 6, 15)
CLOCK_STATUSES = ('open', 'closed', 'cancelled')
SCHEDULE_STATUSES = ('scheduled', 'cancelled')


def define_tables(metadata, C, T, common):
    preferences = T('timekeeping_preferences',
        *common(),
        C('singleton', sa.Integer, 'Always 1: the company has one timekeeping preference row.', nullable=False),
        C('week_start', sa.String(9), 'First day of the work week on weekly timesheets: monday through sunday.', nullable=False),
        C('clock_rounding_minutes', sa.Integer, 'Clocked time is rounded to the nearest this many minutes: 0 (none), 6 or 15.', nullable=False),
        C('max_shift_minutes', sa.Integer, 'Longest a clock may run; after this it must be closed with a stated end time.', nullable=False),
        sa.UniqueConstraint('singleton', name='uq_timekeeping_preferences_singleton'),
        sa.CheckConstraint('singleton = 1', name='ck_timekeeping_preferences_singleton'),
        sa.CheckConstraint("week_start IN ('monday','tuesday','wednesday','thursday','friday','saturday','sunday')",
                           name='ck_timekeeping_week_start'),
        sa.CheckConstraint('clock_rounding_minutes IN (0,6,15)', name='ck_timekeeping_rounding'),
        sa.CheckConstraint('max_shift_minutes BETWEEN 60 AND 1440', name='ck_timekeeping_max_shift'),
        description='Timekeeping preferences: work-week start, clock rounding and the maximum shift.')

    people = T('timekeeping_people',
        *common(),
        C('employee_id', sa.String(26), 'Employee this login is.', sa.ForeignKey('employees.id'), nullable=False),
        C('user_id', sa.String(26), 'Hub user id of the login that is this employee; null once unlinked.', nullable=True),
        sa.UniqueConstraint('employee_id', name='uq_timekeeping_people_employee'),
        sa.UniqueConstraint('user_id', name='uq_timekeeping_people_user'),
        description='Which hub login is which employee, for clocking in on a person\'s own instruction.')

    clocks = T('time_clocks',
        *common(),
        C('employee_id', sa.String(26), 'Who clocked in.', sa.ForeignKey('employees.id'), nullable=False),
        C('customer_id', sa.String(26), 'Customer or job the time is for; may be given at clock-out instead.', sa.ForeignKey('customers.id'), nullable=True),
        C('item_id', sa.String(26), 'Service item the time is charged as; may be given at clock-out instead.', sa.ForeignKey('items.id'), nullable=True),
        C('class_id', sa.String(26), 'Class of the time.', sa.ForeignKey('classes.id'), nullable=True),
        C('billable', sa.Boolean, 'Whether the recorded time can be billed.', nullable=False),
        C('notes', sa.Text, 'What the work was; it becomes the time entry note.', nullable=True),
        C('clock_in_at', sa.String(32), 'UTC timestamp the clock started.', nullable=False),
        C('clock_out_at', sa.String(32), 'UTC timestamp the clock stopped; null while open or when cancelled.', nullable=True),
        C('end_stated', sa.Boolean, 'Whether the stop time was stated by the person rather than read from the clock.', nullable=False),
        C('status', sa.String(16), 'open, closed (time recorded) or cancelled (nothing recorded).', nullable=False),
        C('cancel_reason', sa.String(140), 'Why a clock was cancelled.', nullable=True),
        C('entries', sa.Text, 'JSON array of the time entry ids clocking out wrote, oldest day first.', nullable=True),
        sa.CheckConstraint("status IN ('open','closed','cancelled')", name='ck_time_clock_status'),
        sa.CheckConstraint('billable IN (0,1) AND end_stated IN (0,1)', name='ck_time_clock_flags'),
        sa.CheckConstraint("(status = 'closed') = (clock_out_at IS NOT NULL)", name='ck_time_clock_out'),
        sa.CheckConstraint('clock_out_at IS NULL OR clock_out_at > clock_in_at', name='ck_time_clock_order'),
        description='Time clock: each clock-in, and the time entries its clock-out wrote.')
    sa.Index('ux_time_clocks_open', clocks.c.employee_id, unique=True, sqlite_where=clocks.c.status == 'open')
    sa.Index('ix_time_clocks_employee', clocks.c.employee_id, clocks.c.clock_in_at)

    schedule = T('schedule_entries',
        *common(),
        C('employee_id', sa.String(26), 'Employee scheduled.', sa.ForeignKey('employees.id'), nullable=False),
        C('customer_id', sa.String(26), 'Customer or job the employee is scheduled on (a work order\'s own customer when one is named).', sa.ForeignKey('customers.id'), nullable=False),
        C('work_document_id', sa.String(26), 'Work order the employee is scheduled on, if any.', sa.ForeignKey('work_documents.id'), nullable=True),
        C('starts_at', sa.String(16), 'Company-local start, YYYY-MM-DDTHH:MM.', nullable=False),
        C('ends_at', sa.String(16), 'Company-local end, YYYY-MM-DDTHH:MM; after the start.', nullable=False),
        C('notes', sa.Text, 'What the visit is for.', nullable=True),
        C('status', sa.String(16), 'scheduled, or cancelled (kept readable with its reason).', nullable=False),
        C('cancel_reason', sa.String(140), 'Why the entry was cancelled.', nullable=True),
        sa.CheckConstraint('length(starts_at) = 16 AND length(ends_at) = 16 AND starts_at < ends_at', name='ck_schedule_range'),
        sa.CheckConstraint("status IN ('scheduled','cancelled')", name='ck_schedule_status'),
        description='Job schedule: an employee on a job or work order over a local date and time range.')
    sa.Index('ix_schedule_employee', schedule.c.employee_id, schedule.c.starts_at)
    sa.Index('ix_schedule_customer', schedule.c.customer_id, schedule.c.starts_at)
    return dict(timekeeping_preferences=preferences, timekeeping_people=people, time_clocks=clocks,
                schedule_entries=schedule)
