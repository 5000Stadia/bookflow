"""Billing groups, and the durable record of what one batch of invoices actually did.

**A billing group is a name and a set of customers.** ``billing_groups`` carries the name and
its case-folded uniqueness key; ``billing_group_members`` is the set, one row per customer, in
the order a person put them in. A customer may belong to any number of groups, which is why
membership is its own table rather than a column on the customer. Neither table is document
history, so neither is immutable: a group is renamed, added to, taken from and deleted like the
list record it is.

**Deleting a customer that belongs to a group is refused, not silently absorbed.** The
membership carries a real foreign key to ``customers``, so storage alone makes an orphan
membership impossible, and ``undo.py`` names this table as a hard reference on the customer so
the refusal arrives as a readable conflict rather than as a foreign-key error. Deactivating a
customer is a different act and is left alone: the membership survives, and the next batch
reports that customer as a failed row naming ``E_INACTIVE_REFERENCE`` instead of quietly
dropping an invoice somebody expected to be sent.

**A batch is history and is immutable.** ``invoice_batches`` is one row per run: what was
asked for (``request_snapshot`` holds the exact lines and header words the run was given, so a
retry re-asks the same question), which group it came from -- captured as an id *and* the name
the group carried that day, with no foreign key, because a batch has to keep reading back after
the group it was addressed to is deleted -- and the three counts. Its results
are ``invoice_batch_results``, one row per customer in the order they were invoiced, each
either a created invoice -- naming the transaction, its allocated number and its own resolved
total -- or a failure naming the error code and message the customer's own invoice raised.
That is the whole point of storing it: a batch that half worked is a normal outcome, and the
list of who was missed has to outlive the response the user might have closed.

The two counts and the total are stored rather than derived because they are the facts of that
run. A later correction, void or deletion of one of the invoices changes what the books say
today; it must not rewrite what this batch did on the day it ran.
"""

import sqlalchemy as sa


def define_tables(metadata, column, table, common):
    C, T = column, table

    def identifier(name, description, target=None, *, primary_key=False, nullable=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints,
                 primary_key=primary_key, nullable=nullable)

    def created(what):
        return [C('created_at', sa.String(32), f'UTC time this {what} was written.', nullable=False),
                identifier('created_by', f'Company principal that wrote this {what}.'),
                C('created_via', sa.String(16), f'Interface that wrote this {what}.', nullable=False)]

    billing_groups = T('billing_groups',
        *common(),
        C('name', sa.String(200), 'Name a person gave this billing group, as entered.', nullable=False),
        C('name_key', sa.String(400), 'NFC-normalized, trimmed and case-folded name; unique within the company.', nullable=False),
        sa.UniqueConstraint('name_key', name='uq_billing_group_name_key'),
        sa.CheckConstraint("length(trim(name)) > 0", name='ck_billing_group_name_nonblank'),
        description='Reusable named sets of customers or jobs that one batch of invoices is addressed to.')

    billing_group_members = T('billing_group_members',
        identifier('id', 'Stable ULID of this membership.', primary_key=True),
        identifier('group_id', 'Billing group this membership belongs to.', 'billing_groups.id'),
        identifier('customer_id', 'Customer or job that is a member of the group.', 'customers.id'),
        C('position', sa.Integer, 'Order this member was placed in, from 1; the order a batch invoices in.', nullable=False),
        *created('membership'),
        sa.UniqueConstraint('group_id', 'customer_id', name='uq_billing_group_member_customer'),
        sa.UniqueConstraint('group_id', 'position', name='uq_billing_group_member_position'),
        sa.CheckConstraint("typeof(position) = 'integer' AND position > 0", name='ck_billing_group_member_position_positive'),
        description='Membership of a customer or job in a billing group; a customer may belong to several groups.')

    invoice_batches = T('invoice_batches',
        identifier('id', 'Stable ULID of this batch run.', primary_key=True),
        *created('batch record'),
        identifier('audit_event_id', 'Company audit event that recorded this batch run.', 'audit_events.id'),
        C('date', sa.String(10), 'Invoice date every invoice in this batch was asked for.', nullable=False),
        identifier('billing_group_id', 'Billing group the customers came from; null when an explicit list was given. Recorded, not referenced: a batch is history and keeps what it was addressed to even after the group is deleted.', nullable=True),
        C('billing_group_name', sa.String(200), 'Name that billing group carried when this run happened; null when an explicit list was given.', nullable=True),
        identifier('retry_of_batch_id', 'Earlier batch whose failed customers this run retried; null for a first run.', 'invoice_batches.id', nullable=True),
        C('requested_count', sa.Integer, 'Customers this run attempted, one invoice each.', nullable=False),
        C('created_count', sa.Integer, 'Customers whose invoice was created.', nullable=False),
        C('failed_count', sa.Integer, 'Customers whose invoice was refused and not created.', nullable=False),
        C('created_total_minor_units', sa.BigInteger, 'Home-currency sum of the invoices this run created.', nullable=False),
        C('currency', sa.String(3), 'Home currency the created totals are stated in.', nullable=False),
        C('request_snapshot', sa.Text, 'Versioned typed JSON object of the exact request this run was given, so a retry re-asks the same question.', nullable=False),
        sa.CheckConstraint("typeof(requested_count) = 'integer' AND requested_count > 0", name='ck_invoice_batch_requested_positive'),
        sa.CheckConstraint("typeof(created_count) = 'integer' AND created_count >= 0", name='ck_invoice_batch_created_nonnegative'),
        sa.CheckConstraint("typeof(failed_count) = 'integer' AND failed_count >= 0", name='ck_invoice_batch_failed_nonnegative'),
        sa.CheckConstraint('created_count + failed_count = requested_count', name='ck_invoice_batch_counts_agree'),
        sa.CheckConstraint("typeof(created_total_minor_units) = 'integer' AND created_total_minor_units >= 0", name='ck_invoice_batch_total_nonnegative'),
        sa.CheckConstraint("json_valid(request_snapshot) AND json_type(request_snapshot) = 'object'", name='ck_invoice_batch_request_object'),
        sa.CheckConstraint('(billing_group_id IS NULL) = (billing_group_name IS NULL)', name='ck_invoice_batch_group_pair'),
        sa.Index('ix_invoice_batches_group', 'billing_group_id', 'id'),
        description='One run of batch invoicing: what it was asked for, where the customers came from, and what it did.')

    invoice_batch_results = T('invoice_batch_results',
        identifier('id', 'Stable ULID of this per-customer outcome.', primary_key=True),
        identifier('batch_id', 'Batch run this outcome belongs to.', 'invoice_batches.id'),
        C('position', sa.Integer, 'Order this customer was invoiced in, from 1.', nullable=False),
        identifier('customer_id', 'Customer or job this outcome is for.', 'customers.id'),
        C('customer_label', sa.String(1004), 'Full name of the customer at the time of the run.', nullable=False),
        C('status', sa.String(16), 'Outcome for this customer: created or failed.', nullable=False),
        identifier('transaction_id', 'Invoice created for this customer; null when the customer failed.', 'transactions.id', nullable=True),
        C('number', sa.String(64), 'Document number allocated to the created invoice; null when the customer failed.', nullable=True),
        C('total_minor_units', sa.BigInteger, 'Resolved gross total of the created invoice; null when the customer failed.', nullable=True),
        C('currency', sa.String(3), 'Currency of the created total; null when the customer failed.', nullable=True),
        C('error_code', sa.String(64), 'Stable error code the refused invoice raised; null when the customer succeeded.', nullable=True),
        C('error_message', sa.Text, 'Human-readable reason the invoice was refused; null when the customer succeeded.', nullable=True),
        *created('batch outcome'),
        sa.UniqueConstraint('batch_id', 'position', name='uq_invoice_batch_result_position'),
        sa.UniqueConstraint('batch_id', 'customer_id', name='uq_invoice_batch_result_customer'),
        sa.CheckConstraint("status IN ('created', 'failed')", name='ck_invoice_batch_result_status'),
        sa.CheckConstraint("typeof(position) = 'integer' AND position > 0", name='ck_invoice_batch_result_position_positive'),
        sa.CheckConstraint(
            "(status = 'created') = (transaction_id IS NOT NULL AND number IS NOT NULL "
            'AND total_minor_units IS NOT NULL AND currency IS NOT NULL)',
            name='ck_invoice_batch_result_created_facts'),
        sa.CheckConstraint("(status = 'failed') = (error_code IS NOT NULL)", name='ck_invoice_batch_result_failed_facts'),
        sa.CheckConstraint(
            'total_minor_units IS NULL OR '
            "(typeof(total_minor_units) = 'integer' AND total_minor_units > 0)",
            name='ck_invoice_batch_result_total_positive'),
        sa.Index('ix_invoice_batch_results_customer', 'customer_id', 'id'),
        description='One customer of one batch run: the invoice it created, or the error that refused it.')

    return {name: value for name, value in locals().items() if isinstance(value, sa.Table)}


IMMUTABLE = ('invoice_batches', 'invoice_batch_results')


def guard_statements():
    """Storage fences: a recorded batch run is history and never changes."""
    for table in IMMUTABLE:
        for event in ('UPDATE', 'DELETE'):
            yield (f'CREATE TRIGGER {table}_immutable_{event.lower()} BEFORE {event} ON {table} '
                   "BEGIN SELECT RAISE(ABORT, 'immutable invoice batch history'); END")
