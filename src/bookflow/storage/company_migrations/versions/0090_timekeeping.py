"""Timekeeping (R106): preferences, logins as employees, the time clock and the job schedule.

Four new tables: ``timekeeping_preferences`` (work-week start, clock rounding, maximum shift),
``timekeeping_people`` (which hub login is which employee), ``time_clocks`` (each clock-in and the
time entries its clock-out wrote) and ``schedule_entries`` (an employee on a job or work order over
a local date and time range). Additive: nothing existing changes. Weekly timesheets have no table;
they are a view over the time entries that already exist. DDL below is frozen: this migration never
imports current application metadata.
"""
from alembic import op

revision = 'co0090'
down_revision = 'co0084'
branch_labels = depends_on = None
DDL = (
    "\nCREATE TABLE timekeeping_preferences (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tsingleton INTEGER NOT NULL, \n\tweek_start VARCHAR(9) NOT NULL, \n\tclock_rounding_minutes INTEGER NOT NULL, \n\tmax_shift_minutes INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_timekeeping_preferences_singleton UNIQUE (singleton), \n\tCONSTRAINT ck_timekeeping_preferences_singleton CHECK (singleton = 1), \n\tCONSTRAINT ck_timekeeping_week_start CHECK (week_start IN ('monday','tuesday','wednesday','thursday','friday','saturday','sunday')), \n\tCONSTRAINT ck_timekeeping_rounding CHECK (clock_rounding_minutes IN (0,6,15)), \n\tCONSTRAINT ck_timekeeping_max_shift CHECK (max_shift_minutes BETWEEN 60 AND 1440)\n)\n\n",
    '\nCREATE TABLE timekeeping_people (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\temployee_id VARCHAR(26) NOT NULL, \n\tuser_id VARCHAR(26), \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_timekeeping_people_employee UNIQUE (employee_id), \n\tCONSTRAINT uq_timekeeping_people_user UNIQUE (user_id), \n\tFOREIGN KEY(employee_id) REFERENCES employees (id)\n)\n\n',
    "\nCREATE TABLE time_clocks (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\temployee_id VARCHAR(26) NOT NULL, \n\tcustomer_id VARCHAR(26), \n\titem_id VARCHAR(26), \n\tclass_id VARCHAR(26), \n\tbillable BOOLEAN NOT NULL, \n\tnotes TEXT, \n\tclock_in_at VARCHAR(32) NOT NULL, \n\tclock_out_at VARCHAR(32), \n\tend_stated BOOLEAN NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tcancel_reason VARCHAR(140), \n\tentries TEXT, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_time_clock_status CHECK (status IN ('open','closed','cancelled')), \n\tCONSTRAINT ck_time_clock_flags CHECK (billable IN (0,1) AND end_stated IN (0,1)), \n\tCONSTRAINT ck_time_clock_out CHECK ((status = 'closed') = (clock_out_at IS NOT NULL)), \n\tCONSTRAINT ck_time_clock_order CHECK (clock_out_at IS NULL OR clock_out_at > clock_in_at), \n\tFOREIGN KEY(employee_id) REFERENCES employees (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(class_id) REFERENCES classes (id)\n)\n\n",
    "\nCREATE TABLE schedule_entries (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\temployee_id VARCHAR(26) NOT NULL, \n\tcustomer_id VARCHAR(26) NOT NULL, \n\twork_document_id VARCHAR(26), \n\tstarts_at VARCHAR(16) NOT NULL, \n\tends_at VARCHAR(16) NOT NULL, \n\tnotes TEXT, \n\tstatus VARCHAR(16) NOT NULL, \n\tcancel_reason VARCHAR(140), \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_schedule_range CHECK (length(starts_at) = 16 AND length(ends_at) = 16 AND starts_at < ends_at), \n\tCONSTRAINT ck_schedule_status CHECK (status IN ('scheduled','cancelled')), \n\tFOREIGN KEY(employee_id) REFERENCES employees (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id), \n\tFOREIGN KEY(work_document_id) REFERENCES work_documents (id)\n)\n\n",
)
INDEXES = (
    'CREATE INDEX ix_time_clocks_employee ON time_clocks (employee_id, clock_in_at)',
    "CREATE UNIQUE INDEX ux_time_clocks_open ON time_clocks (employee_id) WHERE status = 'open'",
    'CREATE INDEX ix_schedule_customer ON schedule_entries (customer_id, starts_at)',
    'CREATE INDEX ix_schedule_employee ON schedule_entries (employee_id, starts_at)',
)
NEW_OBJECTS = ('timekeeping_preferences', 'timekeeping_people', 'time_clocks', 'schedule_entries',
               'ix_time_clocks_employee', 'ux_time_clocks_open', 'ix_schedule_customer', 'ix_schedule_employee')


def upgrade():
    db = op.get_bind()
    names = {row[0].casefold() for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & set(NEW_OBJECTS):
        raise RuntimeError('co0090 reserved object exists')
    for statement in DDL + INDEXES:
        db.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
