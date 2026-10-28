"""co0111. Document delivery, increment 4: recurring sends (R112, design/document-delivery.md §6, §8).

Two new tables. ``delivery_schedules`` holds a saved batch with a frequency: the kind and selection a run drafts, the
template, ``combine``, the frequency with its start, stop date or remaining runs, the next slot, ``mode`` (draft or
send), ``active``, and ``state``: a send schedule is a draft until a person makes it live (R187: ``automate.delivery``);
then it runs under its establisher's authority (``established_by``) and is paused, with the reason, when they lose it.
``run_time`` is the company-local time from which a slot is due (default 06:00); ``confirmed`` the rule's fingerprint
the person confirmed (selection, cadence, template and its content, mode); ``draft_reason`` why it last left live.
``run_as`` is who last set what it drafts. ``delivery_schedule_runs`` holds each slot, claimed once (unique
``(schedule_id, slot_date)``): a run drafting in chunks through a delivery batch and ending done with counts, or a slot
skipped with why (a later slot was due, older than the catch-up window, stopped, no authority). Neither is ever
deleted (guards). Moves no stored row. DDL below is frozen: this migration never imports current application metadata.
"""
from alembic import op

revision = 'co0111'
down_revision = 'co0110'
branch_labels = depends_on = None

DDL = ("\nCREATE TABLE delivery_schedules (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\tname_key VARCHAR(100) NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\tselection TEXT NOT NULL, \n\ttemplate_id VARCHAR(26), \n\tcombine BOOLEAN NOT NULL, \n\tfrequency VARCHAR(24) NOT NULL, \n\tstart_date VARCHAR(10) NOT NULL, \n\tstop_date VARCHAR(10), \n\tremaining_runs INTEGER, \n\tnext_date VARCHAR(10), \n\tanchor_day INTEGER, \n\tmode VARCHAR(8) NOT NULL, \n\tactive BOOLEAN NOT NULL, \n\tstate VARCHAR(8) NOT NULL, \n\testablished_by VARCHAR(26), \n\testablished_at VARCHAR(32), \n\tpaused_reason VARCHAR(500), \n\tpaused_at VARCHAR(32), \n\tcatch_up_days INTEGER NOT NULL, \n\trun_time VARCHAR(5) NOT NULL, \n\tconfirmed VARCHAR(32), \n\tdraft_reason VARCHAR(500), \n\trun_as VARCHAR(26) NOT NULL, \n\trun_as_for VARCHAR(26), \n\trun_as_epoch INTEGER, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_delivery_schedule_name CHECK (length(trim(name)) BETWEEN 1 AND 100), \n\tCONSTRAINT ck_delivery_schedule_kind CHECK (kind IN ('invoice', 'sales-receipt', 'estimate', 'statement', 'notice')), \n\tCONSTRAINT ck_delivery_schedule_frequency CHECK (frequency IN ('daily', 'weekly', 'every_other_week', 'every_four_weeks', 'twice_a_month', 'monthly', 'every_other_month', 'quarterly', 'twice_a_year', 'annually')), \n\tCONSTRAINT ck_delivery_schedule_mode CHECK (mode IN ('draft', 'send')), \n\tCONSTRAINT ck_delivery_schedule_state CHECK (state IN ('draft', 'live', 'paused')), \n\tCONSTRAINT ck_delivery_schedule_live CHECK (state != 'live' OR (mode = 'send' AND established_by IS NOT NULL AND confirmed IS NOT NULL)), \n\tCONSTRAINT ck_delivery_schedule_run_time CHECK (run_time GLOB '[0-2][0-9]:[0-5][0-9]' AND run_time <= '23:59'), \n\tCONSTRAINT ck_delivery_schedule_paused CHECK ((state = 'paused') = (paused_reason IS NOT NULL)), \n\tCONSTRAINT ck_delivery_schedule_selection CHECK (json_valid(selection) AND json_type(selection) = 'object'), \n\tCONSTRAINT ck_delivery_schedule_catch_up CHECK (catch_up_days BETWEEN 0 AND 31), \n\tCONSTRAINT ck_delivery_schedule_remaining CHECK (remaining_runs IS NULL OR remaining_runs >= 0), \n\tCONSTRAINT ck_delivery_schedule_anchor CHECK (anchor_day IS NULL OR anchor_day BETWEEN 1 AND 31), \n\tCONSTRAINT uq_delivery_schedule_name UNIQUE (name_key), \n\tFOREIGN KEY(template_id) REFERENCES delivery_templates (id)\n)\n\n", "\nCREATE TABLE delivery_schedule_runs (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tschedule_id VARCHAR(26) NOT NULL, \n\tslot_date VARCHAR(10) NOT NULL, \n\tstate VARCHAR(10) NOT NULL, \n\tbatch_id VARCHAR(26), \n\tmode VARCHAR(8) NOT NULL, \n\tgo_by VARCHAR(26), \n\tdate_from VARCHAR(10), \n\tdate_to VARCHAR(10), \n\tplanned INTEGER NOT NULL, \n\tdrafted INTEGER NOT NULL, \n\treleased INTEGER NOT NULL, \n\tskipped_count INTEGER NOT NULL, \n\treason VARCHAR(24), \n\treason_text VARCHAR(500), \n\tfinished_at VARCHAR(32), \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_delivery_schedule_run_state CHECK (state IN ('drafting', 'done', 'skipped', 'failed')), \n\tCONSTRAINT ck_delivery_schedule_run_mode CHECK (mode IN ('draft', 'send')), \n\tCONSTRAINT ck_delivery_schedule_run_reason CHECK ((state IN ('skipped', 'failed')) = (reason IS NOT NULL)), \n\tCONSTRAINT ck_delivery_schedule_run_counts CHECK (planned >= 0 AND drafted >= 0 AND released >= 0 AND skipped_count >= 0), \n\tCONSTRAINT uq_delivery_schedule_run_slot UNIQUE (schedule_id, slot_date), \n\tFOREIGN KEY(schedule_id) REFERENCES delivery_schedules (id), \n\tFOREIGN KEY(batch_id) REFERENCES delivery_batches (id)\n)\n\n")
INDEXES = ('CREATE INDEX ix_delivery_schedules_due ON delivery_schedules (next_date) WHERE active = 1', "CREATE INDEX ix_delivery_schedule_runs_open ON delivery_schedule_runs (schedule_id) WHERE state = 'drafting'")
GUARDS = ("CREATE TRIGGER delivery_schedules_no_delete BEFORE DELETE ON delivery_schedules BEGIN SELECT RAISE(ABORT, 'schedules are paused, never deleted'); END", "CREATE TRIGGER delivery_schedule_runs_no_delete BEFORE DELETE ON delivery_schedule_runs BEGIN SELECT RAISE(ABORT, 'schedule runs are never deleted'); END")
TABLES = ('delivery_schedule_runs', 'delivery_schedules')
# What a test rewinding a head file drops: the guards, the indexes, then the tables (runs first: they name schedules).
TRIGGERS = tuple(statement.split()[2] for statement in GUARDS)


def upgrade():
    db = op.get_bind()
    names = {row[0].casefold() for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & (set(TABLES) | set(TRIGGERS) | {'ix_delivery_schedules_due', 'ix_delivery_schedule_runs_open'}):
        raise RuntimeError('co0111 reserved object exists')
    for statement in DDL + INDEXES + GUARDS:
        db.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
