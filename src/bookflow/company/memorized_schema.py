"""Memorized transactions: the template, its versions, its derived edges, and its occurrences.

A memorized transaction is not a document. It is **the canonical input of one registered
create/post command, kept under version**, with a schedule beside it. Nothing here mirrors a
document's shape, because a second document model is exactly the drift this design exists to
avoid: ``memorized_transaction_revisions.payload`` is the same JSON the ordinary command
validates, and entry replays it through that command.

**Seven tables, four jobs.**

*The template.* ``memorized_transactions`` is the mutable header a person edits -- its name,
which command it replays, its schedule and its mode -- and ``memorized_transaction_revisions``
is the immutable history of what it replays. The header points at its current revision;
an occurrence points at the revision it captured, so a later edit can never rewrite an entry
that has already been made.

*The derived index.* ``memorized_references`` is one row per stable id the payload names,
computed at capture time from the same ``ReferenceDefinition`` declarations the ordinary form
and the ordinary command resolve through. It is **an index, not integrity**: ``target_id``
carries no foreign key, because the row it names lives in whichever list ``target_noun``
selects and SQLite cannot express a polymorphic reference. It exists to make breakage visible
early. It never authorises anything -- entry revalidates through the normal command, which is
the only thing that can prove an account, a rate or a period is still usable.

*The occurrences.* ``memorized_occurrences`` is one row per **template plus nominal scheduled
slot**. ``slot_date`` is the accounting date the entry will carry and is the identity;
``due_date`` is ``slot_date`` less the template's days-in-advance and is only when the entry
becomes enterable, so entering five days early cannot mint a second occurrence. The partial
unique index over ``(template_id, slot_date)`` for scheduled rows is what makes that identity
a storage fact rather than a convention; a manual extra entry has ``origin = 'manual'`` and
its own id, outside that index. ``entry_key`` is the occurrence's command idempotency
identity, stable across every retry, so a retry can never post a second document and can
never silently substitute a newly edited template.

*The groups.* ``memorized_groups`` is an ordered collection with optional shared scheduling.
``memorized_group_runs`` and ``memorized_group_run_members`` freeze the membership and the
order captured for one scheduled slot, so editing the group afterwards cannot rewrite what
that run did. Each member still gets its own occurrence and its own result: a group is not an
all-or-nothing financial batch.
"""

import sqlalchemy as sa

# The schedules a template may carry. Each maps to how one slot advances to the next:
# a fixed number of days, a number of months anchored on a day of the month, or the
# semi-monthly pair. `never` is a template that only ever enters on demand.
FREQUENCIES: dict[str, tuple[str | None, int]] = {
    'never': (None, 0),
    'daily': ('day', 1),
    'weekly': ('day', 7),
    'every_other_week': ('day', 14),
    'every_four_weeks': ('day', 28),
    'twice_a_month': ('semimonth', 0),
    'monthly': ('month', 1),
    'every_other_month': ('month', 2),
    'quarterly': ('month', 3),
    'twice_a_year': ('month', 6),
    'annually': ('month', 12),
    'every_other_year': ('month', 24),
}

# What a template does when a slot comes due. `remind` leaves a pending occurrence for a
# person to see and enter; `enter_automatically` is the only mode `memorized process` enters.
MODES = ('on_demand', 'remind', 'enter_automatically')
TEMPLATE_STATUSES = ('active', 'paused', 'finished', 'deleted')
OCCURRENCE_STATUSES = ('pending', 'entered', 'blocked', 'skipped')
ORIGINS = ('scheduled', 'manual')

IMMUTABLE = ('memorized_transaction_revisions', 'memorized_references',
             'memorized_group_runs', 'memorized_group_run_members')


def _in(column, values):
    listed = ', '.join("'" + value + "'" for value in values)
    return f'{column} IN ({listed})'


def define_tables(metadata, column, table, common):
    C, T = column, table

    def identifier(name, description, target=None, *, primary_key=False, nullable=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints,
                 primary_key=primary_key, nullable=nullable)

    def created():
        return [C('created_at', sa.String(32), 'UTC time this row was written.', nullable=False),
                identifier('created_by', 'Company principal that wrote this row.'),
                C('created_via', sa.String(16), 'Interface that wrote this row.', nullable=False)]

    def date(name, description, *, nullable=True):
        return C(name, sa.String(10), description, nullable=nullable)

    def schedule_columns(subject):
        """The schedule a template or a group carries; identical on both, deliberately."""
        return [
            C('status', sa.String(16), f'Whether this {subject} is active, paused by its owner, finished, or deleted from the list.', nullable=False),
            C('mode', sa.String(24), f'What this {subject} does when a slot comes due: nothing until asked, remind, or enter.', nullable=False),
            C('frequency', sa.String(24), f'How this {subject} advances from one slot to the next; never for on-demand only.', nullable=False),
            C('anchor_day', sa.Integer, 'Day of the month a month-family schedule keeps, clamped to a short month; null otherwise.', nullable=True),
            date('start_date', f'First nominal slot date of this {subject}; null when it has no schedule.'),
            date('next_date', 'Next nominal slot date, in company-local dates; null when nothing more is scheduled.'),
            date('stop_date', 'Last date a slot may fall on; null when the schedule has no end.'),
            C('remaining_count', sa.Integer, 'Scheduled slots still to be created; one is spent each time a slot is minted. Null when uncounted.', nullable=True),
            C('days_in_advance', sa.Integer, 'How many days before its slot date an occurrence becomes enterable.', nullable=False),
        ]

    def schedule_checks(prefix):
        return [
            sa.CheckConstraint(_in('status', TEMPLATE_STATUSES), name=f'ck_{prefix}_status'),
            sa.CheckConstraint(_in('mode', MODES), name=f'ck_{prefix}_mode'),
            sa.CheckConstraint(_in('frequency', tuple(FREQUENCIES)), name=f'ck_{prefix}_frequency'),
            sa.CheckConstraint("anchor_day IS NULL OR (typeof(anchor_day) = 'integer' AND anchor_day BETWEEN 1 AND 31)",
                               name=f'ck_{prefix}_anchor_day'),
            sa.CheckConstraint("typeof(days_in_advance) = 'integer' AND days_in_advance BETWEEN 0 AND 365",
                               name=f'ck_{prefix}_days_in_advance'),
            sa.CheckConstraint("remaining_count IS NULL OR (typeof(remaining_count) = 'integer' AND remaining_count >= 0)",
                               name=f'ck_{prefix}_remaining_count'),
            sa.CheckConstraint("(start_date IS NULL OR length(start_date) = 10) AND (next_date IS NULL OR length(next_date) = 10)"
                               " AND (stop_date IS NULL OR length(stop_date) = 10)", name=f'ck_{prefix}_dates'),
            sa.CheckConstraint("frequency <> 'never' OR next_date IS NULL", name=f'ck_{prefix}_unscheduled_has_no_next'),
        ]

    def object_check(name, prefix):
        return sa.CheckConstraint(f"json_valid({name}) AND json_type({name}) = 'object'",
                                  name=f'ck_{prefix}_{name}_object')

    memorized_groups = T('memorized_groups',
        *common(),
        C('name', sa.String(160), 'Name a person gave this group.', nullable=False),
        C('name_key', sa.String(320), 'NFC-normalized, trimmed and case-folded name used to keep group names unique.', nullable=False),
        *schedule_columns('group'),
        identifier('audit_event_id', 'Company audit event that created this group.', 'audit_events.id'),
        sa.UniqueConstraint('name_key', name='uq_memorized_group_name_key'),
        *schedule_checks('memorized_group'),
        sa.Index('ix_memorized_groups_status_next_date', 'status', 'next_date'),
        description='Ordered collections of memorized transactions, with optional shared scheduling.')

    memorized_transactions = T('memorized_transactions',
        *common(),
        C('name', sa.String(160), 'Name a person gave this memorized transaction.', nullable=False),
        C('name_key', sa.String(320), 'NFC-normalized, trimmed and case-folded name used to keep template names unique.', nullable=False),
        C('command', sa.String(64), 'Registered create or post command this template replays.', nullable=False),
        identifier('current_revision_id', 'Revision holding the payload and schedule this template replays now.'),
        *schedule_columns('template'),
        identifier('group_id', 'Group this template belongs to; null when it stands alone.', 'memorized_groups.id', nullable=True),
        C('group_ordinal', sa.Integer, 'Position of this template within its group; null when it stands alone.', nullable=True),
        identifier('audit_event_id', 'Company audit event that created this template.', 'audit_events.id'),
        sa.UniqueConstraint('name_key', name='uq_memorized_transaction_name_key'),
        sa.ForeignKeyConstraint(['id', 'current_revision_id'],
            ['memorized_transaction_revisions.template_id', 'memorized_transaction_revisions.id'],
            name='fk_memorized_transaction_current_revision', deferrable=True, initially='DEFERRED'),
        *schedule_checks('memorized_transaction'),
        sa.CheckConstraint("(group_id IS NULL) = (group_ordinal IS NULL)", name='ck_memorized_transaction_group_pair'),
        sa.CheckConstraint("group_ordinal IS NULL OR (typeof(group_ordinal) = 'integer' AND group_ordinal >= 1)",
                           name='ck_memorized_transaction_group_ordinal'),
        sa.Index('ix_memorized_transactions_status_next_date', 'status', 'next_date'),
        sa.Index('ix_memorized_transactions_group_id_group_ordinal', 'group_id', 'group_ordinal'),
        description='Memorized transaction templates: which command they replay, and on what schedule.')

    memorized_transaction_revisions = T('memorized_transaction_revisions',
        identifier('id', 'Immutable revision of one memorized transaction.', primary_key=True),
        identifier('template_id', 'Template this revision belongs to.', 'memorized_transactions.id'),
        C('version', sa.BigInteger, 'Version of the template this revision recorded.', nullable=False),
        identifier('previous_revision_id', 'Revision this one supersedes; null on the first.', nullable=True),
        C('command', sa.String(64), 'Registered create or post command captured by this revision.', nullable=False),
        C('payload', sa.Text, 'Canonical validated input of that command, as a JSON object, with references stored as stable ids.', nullable=False),
        C('payload_hash', sa.String(64), 'SHA-256 of the canonical payload, so an unchanged edit is visible as unchanged.', nullable=False),
        C('schedule_snapshot', sa.Text, 'JSON object of the schedule and mode this revision captured.', nullable=False),
        C('reference_schema', sa.String(16), "Whether the command declares reference paths (declared) or none are declared (none).", nullable=False),
        *created(),
        identifier('audit_event_id', 'Company audit event that wrote this revision.', 'audit_events.id'),
        sa.UniqueConstraint('template_id', 'id', name='uq_memorized_revision_owner'),
        sa.UniqueConstraint('template_id', 'version', name='uq_memorized_revision_version'),
        sa.ForeignKeyConstraint(['template_id', 'previous_revision_id'],
            ['memorized_transaction_revisions.template_id', 'memorized_transaction_revisions.id'],
            name='fk_memorized_revision_previous'),
        sa.CheckConstraint("typeof(version) = 'integer' AND version > 0", name='ck_memorized_revision_version'),
        sa.CheckConstraint('length(payload_hash) = 64', name='ck_memorized_revision_hash'),
        sa.CheckConstraint(_in('reference_schema', ('declared', 'none')), name='ck_memorized_revision_reference_schema'),
        object_check('payload', 'memorized_revision'),
        object_check('schedule_snapshot', 'memorized_revision'),
        description='Immutable versions of a memorized transaction: the command input and the schedule it captured.')

    memorized_references = T('memorized_references',
        identifier('id', 'Row of the derived dependency index.', primary_key=True),
        identifier('template_id', 'Template whose payload named this record.'),
        identifier('revision_id', 'Exact revision whose payload named this record.'),
        C('field_path', sa.String(120), 'Declared reference path in the command input that named it, such as lines.item.', nullable=False),
        C('ordinal', sa.Integer, 'Position within a repeated field; 0 for a field that occurs once.', nullable=False),
        C('target_noun', sa.String(40), 'List the reference declaration points at, such as customer or account.', nullable=False),
        C('target_record_type', sa.String(40), 'Record type of the named row, for the audit and activity trails.', nullable=False),
        identifier('target_id', 'Stable id of the named row. Deliberately not a foreign key: this is an index over several lists, not integrity.'),
        C('target_active', sa.Boolean, 'Whether the named row was active when this revision was captured.', nullable=False),
        C('created_at', sa.String(32), 'UTC time this index row was derived.', nullable=False),
        sa.UniqueConstraint('revision_id', 'field_path', 'ordinal', name='uq_memorized_reference_path'),
        sa.ForeignKeyConstraint(['template_id', 'revision_id'],
            ['memorized_transaction_revisions.template_id', 'memorized_transaction_revisions.id'],
            name='fk_memorized_reference_revision'),
        sa.CheckConstraint("typeof(ordinal) = 'integer' AND ordinal >= 0", name='ck_memorized_reference_ordinal'),
        sa.Index('ix_memorized_references_target', 'target_record_type', 'target_id'),
        description='Derived index of the stable ids a memorized payload names. An index, never integrity.')

    memorized_group_runs = T('memorized_group_runs',
        identifier('id', 'One scheduled run of a group.', primary_key=True),
        identifier('group_id', 'Group this run belongs to.', 'memorized_groups.id'),
        date('slot_date', 'Nominal scheduled date this run answers.', nullable=False),
        date('entry_date', 'Company-local date this run was started.', nullable=False),
        *created(),
        identifier('audit_event_id', 'Company audit event that opened this run.', 'audit_events.id'),
        sa.UniqueConstraint('group_id', 'slot_date', name='uq_memorized_group_run_slot'),
        sa.UniqueConstraint('id', 'group_id', name='uq_memorized_group_run_owner'),
        sa.CheckConstraint('length(slot_date) = 10 AND length(entry_date) = 10', name='ck_memorized_group_run_dates'),
        description='Frozen runs of a memorized group: one per group and nominal slot.')

    memorized_group_run_members = T('memorized_group_run_members',
        identifier('run_id', 'Run whose membership this row froze.', 'memorized_group_runs.id', primary_key=True),
        C('ordinal', sa.Integer, 'Position of this member in the order the run captured.', nullable=False, primary_key=True),
        identifier('template_id', 'Template that was a member when the run opened.'),
        identifier('revision_id', 'Exact template revision the run captured.'),
        C('created_at', sa.String(32), 'UTC time this membership was frozen.', nullable=False),
        sa.UniqueConstraint('run_id', 'template_id', name='uq_memorized_group_run_member'),
        sa.ForeignKeyConstraint(['template_id', 'revision_id'],
            ['memorized_transaction_revisions.template_id', 'memorized_transaction_revisions.id'],
            name='fk_memorized_group_run_member_revision'),
        sa.CheckConstraint("typeof(ordinal) = 'integer' AND ordinal >= 1", name='ck_memorized_group_run_member_ordinal'),
        description='Frozen membership and order of one group run; a later edit to the group cannot rewrite it.')

    memorized_occurrences = T('memorized_occurrences',
        *common(),
        identifier('template_id', 'Template this occurrence belongs to.', 'memorized_transactions.id'),
        identifier('revision_id', 'Exact template revision captured before dispatch; a retry never substitutes a newer one.'),
        C('command', sa.String(64), 'Registered command this occurrence enters.', nullable=False),
        C('origin', sa.String(12), 'Whether the schedule minted this occurrence or a person entered one extra by hand.', nullable=False),
        date('slot_date', 'Nominal scheduled date, which is the accounting date the entry carries. Never moved by a failure.', nullable=False),
        date('due_date', 'Slot date less the days in advance: when this occurrence becomes enterable.', nullable=False),
        date('entry_date', 'Company-local date the entry was actually made; null until it is.'),
        C('status', sa.String(12), 'pending, entered, blocked with a reason, or skipped by a person.', nullable=False),
        C('entry_key', sa.String(128), 'Command idempotency identity of this occurrence, reused by every retry.', nullable=False),
        identifier('transaction_id', 'Document the entry produced, when the command produces one; null otherwise.', nullable=True),
        C('document_number', sa.String(64), 'Number the entry was allocated, which happens only on success.', nullable=True),
        C('result', sa.Text, 'JSON object of the command output the entry returned; null until entered.', nullable=True),
        C('error_code', sa.String(40), 'Stable error code that blocked this occurrence; null when it is not blocked.', nullable=True),
        C('error_message', sa.String(512), 'Message of the error that blocked this occurrence; null when it is not blocked.', nullable=True),
        C('attempt_count', sa.Integer, 'How many times entry has been attempted for this occurrence.', nullable=False),
        identifier('group_run_id', 'Group run this occurrence was part of; null when it stands alone.', 'memorized_group_runs.id', nullable=True),
        C('group_ordinal', sa.Integer, 'Position within that group run; null when it stands alone.', nullable=True),
        identifier('audit_event_id', 'Company audit event that created this occurrence.', 'audit_events.id'),
        sa.UniqueConstraint('entry_key', name='uq_memorized_occurrence_entry_key'),
        sa.ForeignKeyConstraint(['template_id', 'revision_id'],
            ['memorized_transaction_revisions.template_id', 'memorized_transaction_revisions.id'],
            name='fk_memorized_occurrence_revision'),
        sa.CheckConstraint(_in('status', OCCURRENCE_STATUSES), name='ck_memorized_occurrence_status'),
        sa.CheckConstraint(_in('origin', ORIGINS), name='ck_memorized_occurrence_origin'),
        sa.CheckConstraint('length(slot_date) = 10 AND length(due_date) = 10'
                           ' AND (entry_date IS NULL OR length(entry_date) = 10)', name='ck_memorized_occurrence_dates'),
        sa.CheckConstraint("typeof(attempt_count) = 'integer' AND attempt_count >= 0", name='ck_memorized_occurrence_attempts'),
        sa.CheckConstraint("(status = 'entered') = (result IS NOT NULL)", name='ck_memorized_occurrence_entered_has_result'),
        sa.CheckConstraint("(status = 'blocked') = (error_code IS NOT NULL)", name='ck_memorized_occurrence_blocked_has_reason'),
        sa.CheckConstraint("result IS NULL OR (json_valid(result) AND json_type(result) = 'object')",
                           name='ck_memorized_occurrence_result_object'),
        sa.CheckConstraint('(group_run_id IS NULL) = (group_ordinal IS NULL)', name='ck_memorized_occurrence_group_pair'),
        sa.CheckConstraint('length(entry_key) BETWEEN 1 AND 128', name='ck_memorized_occurrence_entry_key'),
        # Identity: one occurrence per template and nominal slot. A manual extra entry is
        # outside this index and carries its own id, so entering early or by hand can never
        # mint a second scheduled occurrence for a slot that already has one.
        sa.Index('ux_memorized_occurrence_slot', 'template_id', 'slot_date', unique=True,
                 sqlite_where=sa.text("origin = 'scheduled'")),
        sa.Index('ix_memorized_occurrences_status_due_date', 'status', 'due_date'),
        sa.Index('ix_memorized_occurrences_group_run_id_group_ordinal', 'group_run_id', 'group_ordinal'),
        description='One entry of a memorized transaction, identified by its template and nominal slot.')

    return {name: value for name, value in locals().items() if isinstance(value, sa.Table)}


def guard_statements():
    """Storage fences: the captured history of a memorized transaction never changes."""
    for table in IMMUTABLE:
        for event in ('UPDATE', 'DELETE'):
            yield (f'CREATE TRIGGER {table}_immutable_{event.lower()} BEFORE {event} ON {table} '
                   "BEGIN SELECT RAISE(ABORT, 'immutable memorized transaction history'); END")
    yield ('CREATE TRIGGER memorized_occurrences_entered_is_final BEFORE UPDATE ON memorized_occurrences\n'
           "WHEN OLD.status = 'entered' AND (NEW.status <> 'entered' OR NEW.slot_date <> OLD.slot_date\n"
           ' OR NEW.transaction_id IS NOT OLD.transaction_id OR NEW.revision_id <> OLD.revision_id\n'
           ' OR NEW.entry_key <> OLD.entry_key)\n'
           "BEGIN SELECT RAISE(ABORT, 'an entered occurrence is final'); END")
    yield ('CREATE TRIGGER memorized_occurrences_slot_is_fixed BEFORE UPDATE ON memorized_occurrences\n'
           'WHEN NEW.slot_date <> OLD.slot_date OR NEW.template_id <> OLD.template_id OR NEW.origin <> OLD.origin\n'
           "BEGIN SELECT RAISE(ABORT, 'an occurrence never moves its accounting date'); END")
