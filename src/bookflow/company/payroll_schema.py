"""Company-local private payroll drafts; no ledger or ordinary employee projection."""
import sqlalchemy as sa


def define_tables(metadata, C, T):
    settings = T('payroll_settings',
        C('id', sa.Integer, 'Singleton key, always one.', primary_key=True),
        C('version', sa.Integer, 'Settings concurrency version.', nullable=False),
        C('enabled', sa.Boolean, 'Opt-in payroll switch; absence means off.', nullable=False),
        *[C(n, sa.String(26), 'Selected active USD account.', sa.ForeignKey('accounts.id', ondelete='RESTRICT'), nullable=True)
          for n in ('wage_account', 'employer_tax_account', 'net_pay_account')],
        sa.CheckConstraint('id = 1 AND version > 0', name='ck_payroll_settings'),
        description='Private payroll setup. No rates or accounts are created automatically.')
    drafts = T('payroll_drafts',
        C('id', sa.String(26), 'Stable paycheck draft ULID.', primary_key=True),
        C('employee_id', sa.String(26), 'Existing employee identity.', sa.ForeignKey('employees.id', ondelete='RESTRICT'), nullable=False),
        C('version', sa.Integer, 'Optimistic draft version.', nullable=False),
        C('state', sa.String(16), 'draft or prepared; neither posts.', nullable=False),
        C('currency', sa.String(3), 'USD only in this increment.', nullable=False),
        C('pay_date', sa.String(10), 'Actual pay date, null when not yet known.', nullable=True),
        C('payload', sa.Text, 'PRIVATE JSON: exact earning and fixed-item inputs, dates and confirmation.', nullable=False),
        C('calculation', sa.Text, 'PRIVATE JSON: last calculated cents and arithmetic trace; null after editing.', nullable=True),
        C('settings_version', sa.Integer, 'Captured settings version; prepare revalidates accounts.', nullable=False),
        C('created_at', sa.String(32), 'UTC creation time.', nullable=False),
        C('updated_at', sa.String(32), 'UTC last-write time.', nullable=False),
        sa.CheckConstraint("version > 0 AND state IN ('draft', 'prepared') AND currency = 'USD'", name='ck_payroll_draft'),
        sa.Index('ix_payroll_draft_employee_date', 'employee_id', 'pay_date'),
        description='PRIVATE paycheck drafts. Readable only with payroll.read; no employee-read joins.')
    revisions = T('payroll_draft_revisions',
        C('draft_id', sa.String(26), 'Draft identity.', sa.ForeignKey('payroll_drafts.id', ondelete='RESTRICT'), primary_key=True),
        C('version', sa.Integer, 'Retained draft version.', primary_key=True),
        C('snapshot', sa.Text, 'PRIVATE JSON: complete input/output at this revision, including cents.', nullable=False),
        C('reason', sa.Text, 'PRIVATE reason supplied for this write.', nullable=False),
        C('actor_id', sa.String(26), 'Acting principal.', nullable=False),
        C('created_at', sa.String(32), 'UTC revision time.', nullable=False),
        description='PRIVATE immutable payroll draft history; never projected into ordinary audit snapshots.')
    operations = T('payroll_operations',
        C('operation_key', sa.String(128), 'PRIVATE permanent retry key, scoped to company.', primary_key=True),
        C('actor_id', sa.String(26), 'Principal owning this retry.', nullable=False),
        C('command', sa.String(60), 'Payroll command that wrote the result.', nullable=False),
        C('input_hash', sa.String(64), 'SHA-256 of canonical input.', nullable=False),
        C('output', sa.Text, 'PRIVATE exact response, including pay amounts; requires current payroll authority.', nullable=False),
        C('reason', sa.Text, 'PRIVATE reason for the operation.', nullable=False),
        description='PRIVATE permanent payroll retry receipts; absent from general operation-key output.')
    return {t.name: t for t in (settings, drafts, revisions, operations)}


GUARDS = tuple(
    f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'payroll history is immutable'); END"
    for table in ('payroll_draft_revisions', 'payroll_operations') for action in ('UPDATE', 'DELETE')
) + ("CREATE TRIGGER payroll_drafts_no_delete BEFORE DELETE ON payroll_drafts BEGIN SELECT RAISE(ABORT, 'payroll drafts are retained'); END",)
