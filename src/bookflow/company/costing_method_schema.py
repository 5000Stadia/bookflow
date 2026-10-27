"""Costing method timeline (co0100, R104 increment 7; design/advanced-inventory.md §3).

``costing_methods`` is append-only: each row is a dated policy event, a switch to ``average`` or ``fifo``
from ``effective_from`` for every stocked item, made by a person. No row means average from the beginning.
The method on a date is the standing row with the latest ``effective_from`` on or before it. A switch whose
date has not come may be withdrawn: its status moves ``standing`` -> ``withdrawn`` once, and nothing else
about it ever changes. Rows are never deleted.
"""
import sqlalchemy as sa

METHODS = ('average', 'fifo')
STATUSES = ('standing', 'withdrawn')


def define_tables(metadata, C, T):
    def quoted(values):
        return ', '.join(f"'{value}'" for value in values)

    methods = T('costing_methods',
        C('id', sa.String(26), 'Stable ULID of this switch.', primary_key=True),
        C('method', sa.String(8), 'average or fifo, in force from effective_from.', nullable=False),
        C('effective_from', sa.String(10), 'First date the method costs, YYYY-MM-DD.', nullable=False),
        C('status', sa.String(10), 'standing, or withdrawn before its date came.', nullable=False),
        C('reason', sa.Text, 'Why the method was changed, as the person gave it.', nullable=True),
        C('created_at', sa.String(32), 'UTC time this switch was recorded.', nullable=False),
        C('created_by', sa.String(26), 'Company principal (a person) that recorded this switch.', nullable=False),
        C('created_via', sa.String(16), 'Interface that recorded this switch.', nullable=False),
        C('audit_event_id', sa.String(26), 'Audit event that committed this switch.', sa.ForeignKey('audit_events.id'),
          nullable=False),
        C('withdrawn_at', sa.String(32), 'UTC time the switch was withdrawn; null while standing.', nullable=True),
        C('withdrawn_by', sa.String(26), 'Company principal that withdrew the switch; null while standing.', nullable=True),
        C('withdrawn_via', sa.String(16), 'Interface the switch was withdrawn through; null while standing.', nullable=True),
        C('withdraw_reason', sa.Text, 'Why the switch was withdrawn; null while standing.', nullable=True),
        C('withdraw_audit_event_id', sa.String(26), 'Audit event that withdrew the switch; null while standing.',
          sa.ForeignKey('audit_events.id'), nullable=True),
        sa.CheckConstraint(f'method IN ({quoted(METHODS)})', name='ck_costing_method_method'),
        sa.CheckConstraint(f'status IN ({quoted(STATUSES)})', name='ck_costing_method_status'),
        sa.CheckConstraint("effective_from LIKE '____-__-__'", name='ck_costing_method_date'),
        sa.CheckConstraint("(status = 'withdrawn') = (withdrawn_at IS NOT NULL)", name='ck_costing_method_withdrawn'),
        sa.Index('uq_costing_methods_standing_date', 'effective_from', unique=True,
                 sqlite_where=sa.text("status = 'standing'")),
        description='Dated costing-method switches (average or FIFO), append-only; none means average throughout.')
    return {'costing_methods': methods}


# The guards: rows are never deleted, and a row changes only standing -> withdrawn, once.
GUARDS = (
    "CREATE TRIGGER costing_methods_immutable_delete BEFORE DELETE ON costing_methods "
    "BEGIN SELECT RAISE(ABORT, 'immutable costing methods'); END",
    "CREATE TRIGGER costing_methods_withdraw_only BEFORE UPDATE ON costing_methods WHEN NOT "
    "(OLD.status = 'standing' AND NEW.status = 'withdrawn' AND NEW.id IS OLD.id AND NEW.method IS OLD.method "
    "AND NEW.effective_from IS OLD.effective_from AND NEW.reason IS OLD.reason AND NEW.created_at IS OLD.created_at "
    "AND NEW.created_by IS OLD.created_by AND NEW.created_via IS OLD.created_via "
    "AND NEW.audit_event_id IS OLD.audit_event_id) "
    "BEGIN SELECT RAISE(ABORT, 'a costing method switch only moves from standing to withdrawn'); END",
)
