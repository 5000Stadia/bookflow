"""To-dos and overdue notices: what the host's scheduler and the people of a company leave for each other (co0085, R111).

**To-dos** are the anchor's To Do notes: a title, details, a due date, a priority and someone it is
assigned to -- a person or an agent -- optionally about one record (a customer, a vendor, a
document, a memorized transaction). Anyone who may write notes may create, change and complete
one. The scheduler creates one itself when an entry it was trusted to make needs a person (a
blocked memorized occurrence), stamped with that occurrence as ``source_ref`` so a second run
cannot create it twice.

**Overdue notices** are the anchor's collection letters, generated rather than mailed. On the
company's notice schedule the scheduler writes one notice per customer whose overdue balance meets
the company's policy, for that schedule's period (``period_key``: the first day of the month, or
the Monday of the week). A notice is a document ready to send -- a statement, or a short letter --
that waits in the ``notice`` queue until a person marks it sent or dismisses it. Nothing here
sends anything: delivery belongs to document delivery (R112), which reads the same queue. The
unique ``(customer_id, period_key)`` pair is what makes generation idempotent, and what keeps a
dismissed notice dismissed for the rest of its period.
"""
import sqlalchemy as sa

TODO_STATUSES = ('open', 'done', 'inactive')
PRIORITIES = ('high', 'medium', 'low')
LINK_TYPES = ('customer', 'vendor', 'transaction', 'memorized')
NOTICE_STATUSES = ('ready', 'sent', 'dismissed')
NOTICE_TEMPLATES = ('statement', 'letter')
NOTICE_FREQUENCIES = ('weekly', 'monthly')


def _in(column, values):
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def define_tables(metadata, C, T, common):
    todos = T('todos',
        *common(),
        C('title', sa.String(160), 'What is to be done, in a few words.', nullable=False),
        C('notes', sa.Text, 'Details: what to do and anything the person doing it needs to know; null when none.', nullable=True),
        C('due_date', sa.String(10), 'Company-local date the to-do is due, YYYY-MM-DD.', nullable=False),
        C('priority', sa.String(8), 'high, medium or low.', nullable=False),
        C('assignee_id', sa.String(26), 'Hub user (a person or an agent) the to-do is assigned to; null when unassigned.', nullable=True),
        C('status', sa.String(10), 'open until done; inactive when it no longer needs doing.', nullable=False),
        C('link_type', sa.String(16), 'Kind of record the to-do is about: customer, vendor, transaction or memorized; null when none.', nullable=True),
        C('link_id', sa.String(26), 'Stable id of that record; null when none.', nullable=True),
        C('completed_at', sa.String(32), 'UTC time it was marked done; null while it is not.', nullable=True),
        C('completed_by', sa.String(26), 'User who marked it done; null while it is not.', nullable=True),
        C('source_ref', sa.String(64), 'What created it when the scheduler did (memorized-occurrence:<id>); null for one a person or agent made.', nullable=True),
        sa.CheckConstraint("length(trim(title)) > 0", name='ck_todo_title'),
        sa.CheckConstraint('length(due_date) = 10', name='ck_todo_due_date'),
        sa.CheckConstraint(_in('priority', PRIORITIES), name='ck_todo_priority'),
        sa.CheckConstraint(_in('status', TODO_STATUSES), name='ck_todo_status'),
        sa.CheckConstraint('link_type IS NULL OR ' + _in('link_type', LINK_TYPES), name='ck_todo_link_type'),
        sa.CheckConstraint('(link_type IS NULL) = (link_id IS NULL)', name='ck_todo_link_pair'),
        sa.CheckConstraint("(status = 'done') = (completed_at IS NOT NULL)", name='ck_todo_completed'),
        sa.Index('ix_todos_assignee_status_due', 'assignee_id', 'status', 'due_date'),
        sa.Index('ix_todos_status_due', 'status', 'due_date'),
        sa.Index('ux_todos_source_ref', 'source_ref', unique=True, sqlite_where=sa.text('source_ref IS NOT NULL')),
        description='To-do notes: something to do by a date, assigned to a person or an agent, optionally about one record.')

    notices = T('overdue_notices',
        *common(),
        C('customer_id', sa.String(26), 'Customer or job the notice is to.', sa.ForeignKey('customers.id'), nullable=False),
        C('period_key', sa.String(10), 'First day of the notice schedule period this notice answers (a month, or a week from Monday).', nullable=False),
        C('as_of', sa.String(10), 'Company-local date the overdue balance was measured.', nullable=False),
        C('template', sa.String(12), 'What is sent: statement (the customer statement) or letter (a short collection letter).', nullable=False),
        C('statement_from', sa.String(10), 'First day of the statement period printed with the notice.', nullable=False),
        C('overdue_minor_units', sa.BigInteger, 'Overdue balance on as_of, in home-currency minor units.', nullable=False),
        C('balance_minor_units', sa.BigInteger, 'Whole open balance on as_of, in home-currency minor units.', nullable=False),
        C('currency', sa.String(3), 'Home currency of the amounts.', nullable=False),
        C('oldest_due_date', sa.String(10), 'Due date of the oldest overdue document.', nullable=False),
        C('days_overdue', sa.Integer, 'Days the oldest overdue document is past due on as_of.', nullable=False),
        C('documents', sa.Text, 'JSON array of the overdue documents: id, type, number, date, due date, open balance, days overdue.', nullable=False),
        C('status', sa.String(10), 'ready to send, sent, or dismissed.', nullable=False),
        C('sent_at', sa.String(32), 'UTC time it was marked sent; null until then.', nullable=True),
        C('sent_by', sa.String(26), 'User who marked it sent; null until then.', nullable=True),
        C('sent_method', sa.String(16), 'How it went out, as the person marking it said: print, email or other; null until sent.', nullable=True),
        C('dismissed_reason', sa.String(280), 'Why it was dismissed; null unless dismissed.', nullable=True),
        sa.UniqueConstraint('customer_id', 'period_key', name='uq_overdue_notice_period'),
        sa.CheckConstraint('length(period_key) = 10 AND length(as_of) = 10 AND length(statement_from) = 10'
                           ' AND length(oldest_due_date) = 10', name='ck_overdue_notice_dates'),
        sa.CheckConstraint(_in('template', NOTICE_TEMPLATES), name='ck_overdue_notice_template'),
        sa.CheckConstraint(_in('status', NOTICE_STATUSES), name='ck_overdue_notice_status'),
        sa.CheckConstraint("sent_method IS NULL OR sent_method IN ('print', 'email', 'other')", name='ck_overdue_notice_sent_method'),
        sa.CheckConstraint("(status = 'sent') = (sent_at IS NOT NULL)", name='ck_overdue_notice_sent'),
        sa.CheckConstraint("typeof(overdue_minor_units) = 'integer' AND overdue_minor_units > 0", name='ck_overdue_notice_overdue'),
        sa.CheckConstraint("typeof(balance_minor_units) = 'integer'", name='ck_overdue_notice_balance'),
        sa.CheckConstraint('days_overdue >= 1', name='ck_overdue_notice_days'),
        sa.CheckConstraint("json_valid(documents) AND json_type(documents) = 'array'", name='ck_overdue_notice_documents'),
        sa.Index('ix_overdue_notices_status_as_of', 'status', 'as_of'),
        description='Overdue notices the scheduler generated: one per customer and notice period, waiting to be sent or dismissed.')

    authority = T('scheduled_revision_authority',
        C('revision_id', sa.String(26), 'Memorized transaction revision an agent saved.', primary_key=True),
        C('agent_id', sa.String(26), 'The agent that saved it.', nullable=False),
        C('principal_id', sa.String(26), 'The person the agent acted for when it saved it.', nullable=True),
        C('authority_epoch', sa.Integer, "The agent's authority epoch at the save; the scheduler replays the revision only while it is unchanged, as a bearer token is honoured only for its own epoch.", nullable=True),
        C('created_at', sa.String(32), 'UTC time the revision was saved.', nullable=False),
        description="The authority an agent held when it saved a memorized transaction revision, so the scheduler applies a token's checks to it.")

    group_authority = T('scheduled_group_authority',
        C('group_id', sa.String(26), 'Memorized group whose schedule or mode was set.', primary_key=True),
        C('set_by', sa.String(26), 'The person or agent who last set the group\'s schedule, mode or status.', nullable=False),
        C('principal_id', sa.String(26), 'The person an agent setter acted for; null for a person.', nullable=True),
        C('authority_epoch', sa.Integer, "An agent setter's authority epoch when it set the schedule; null for a person.", nullable=True),
        C('set_at', sa.String(32), 'UTC time it was set.', nullable=False),
        description="Who last set each memorized group's schedule and mode: the scheduler enters a member only while both "
                    "the member's author and this setter may, and records the setter on the posting.")

    holds = T('scheduled_holds',
        C('occurrence_id', sa.String(26), 'Memorized occurrence the scheduler would not enter unattended.', primary_key=True),
        C('reason', sa.String(400), 'Why it waits for a person: who memorized it and who scheduled it, a schedule set before '
                                    'the upgrade, or a catch-up window someone else widened.', nullable=False),
        C('created_at', sa.String(32), 'UTC time the scheduler set it aside.', nullable=False),
        C('catch_up_window', sa.Integer, 'The catch-up window, in days, it was left for a person under; null when held for '
                                         'another reason. A later run enters it only if its author set the current window '
                                         'or every widening since.', nullable=True),
        description='Automatic memorized entries the scheduler leaves for a person: entered unattended only when one '
                    'principal decided both their content and their schedule.')

    return {'todos': todos, 'overdue_notices': notices, 'scheduled_revision_authority': authority,
            'scheduled_group_authority': group_authority, 'scheduled_holds': holds}
