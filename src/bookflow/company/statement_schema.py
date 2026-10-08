"""Imported bank statements: the lines a reconciliation was built from, and saved CSV mappings.

Append-only. An import that writes against a statement draft stores one `statement_imports`
row and only the lines that draft does not already hold, so the draft's statement is the union
of what was imported for it, each line once. The same lines answer "was this FITID imported
before" for the account, and `reconcile preview` / `reconcile finish` check every ticked
movement against them. A saved CSV mapping is a named row per account; saving the name again
adds a newer row, and the newest is the one used.
"""
import sqlalchemy as sa


def define_tables(metadata, C, T):
    imports = T('statement_imports',
        C('id', sa.String(26), 'Stable ULID of this import.', primary_key=True),
        C('account_id', sa.String(26), 'Bank or credit card account the statement is for.', sa.ForeignKey('accounts.id'), nullable=False),
        C('draft_id', sa.String(26), 'Statement reconciliation draft the lines were imported into.', sa.ForeignKey('reconciliation_drafts.id'), nullable=False),
        C('format', sa.Text, 'File format: ofx, qfx or csv.', nullable=False),
        C('file_sha256', sa.Text, 'SHA-256 of the file text as given; the file itself is not kept.', nullable=False),
        C('statement_date', sa.Text, 'Statement date the import used.', nullable=False),
        C('ending_balance', sa.Integer, "Ending balance in minor units, in the reconciliation's sign; null when the file gave none.", nullable=True),
        C('suggest_days', sa.Integer, 'Date window, in days, a line may support a ticked movement within.', nullable=False),
        C('line_count', sa.Integer, 'Lines in the file after FITID dedupe.', nullable=False),
        C('created_at', sa.Text, 'UTC timestamp of the import.', nullable=False),
        C('created_by', sa.String(26), 'Actor who imported it.', nullable=True),
        C('created_via', sa.Text, 'Interface it came through.', nullable=False),
        C('audit_event_id', sa.String(26), 'Audit event of the import.', sa.ForeignKey('audit_events.id'), nullable=False),
        sa.CheckConstraint("format IN ('ofx','qfx','csv')", name='ck_statement_import_format'),
        sa.CheckConstraint("length(file_sha256)=64", name='ck_statement_import_sha'),
        sa.CheckConstraint("typeof(line_count)='integer' AND line_count>=0 AND typeof(suggest_days)='integer' AND suggest_days>=0", name='ck_statement_import_counts'),
        sa.Index('ix_statement_imports_draft', 'draft_id'),
        description='One imported bank statement file, kept as the lines a reconciliation draft was built from.')
    lines = T('statement_lines',
        C('import_id', sa.String(26), 'Import that first brought this line in.', sa.ForeignKey('statement_imports.id'), primary_key=True),
        C('ordinal', sa.Integer, "Position among that import's stored lines.", primary_key=True),
        C('account_id', sa.String(26), 'Account the line belongs to (copied from the import for lookup).', sa.ForeignKey('accounts.id'), nullable=False),
        C('draft_id', sa.String(26), 'Draft the line belongs to (copied from the import for lookup).', sa.ForeignKey('reconciliation_drafts.id'), nullable=False),
        C('line_id', sa.Text, "FITID-based id, or a stable hash of the line when the bank gave no FITID.", nullable=False),
        C('fitid', sa.Text, "The bank's own transaction id; null when the file had none.", nullable=True),
        C('date', sa.Text, 'Posted date, YYYY-MM-DD.', nullable=False),
        C('amount', sa.Integer, "Minor units in the reconciliation's sign (a card charge is positive).", nullable=False),
        C('payee', sa.Text, 'Payee or description as the bank printed it.', nullable=False),
        C('memo', sa.Text, 'Memo as the bank printed it.', nullable=False),
        C('number', sa.Text, 'Check or reference number as the bank printed it.', nullable=False),
        sa.UniqueConstraint('draft_id', 'line_id', name='uq_statement_line_draft'),
        sa.Index('ix_statement_lines_account_line', 'account_id', 'line_id'),
        description='One bank statement line, stored once per reconciliation draft.')
    mappings = T('statement_csv_mappings',
        C('id', sa.String(26), 'Stable ULID of this saved mapping row.', primary_key=True),
        C('account_id', sa.String(26), 'Account the mapping is saved for.', sa.ForeignKey('accounts.id'), nullable=False),
        C('name', sa.Text, 'Name the mapping is saved and used under.', nullable=False),
        C('mapping_snapshot', sa.Text, 'The CSV column mapping, as JSON.', nullable=False),
        C('created_at', sa.Text, 'UTC timestamp it was saved; the newest row for a name is used.', nullable=False),
        C('created_by', sa.String(26), 'Actor who saved it.', nullable=True),
        C('audit_event_id', sa.String(26), 'Audit event of the save.', sa.ForeignKey('audit_events.id'), nullable=False),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 80", name='ck_statement_mapping_name'),
        sa.CheckConstraint("json_valid(mapping_snapshot) AND json_type(mapping_snapshot)='object'", name='ck_statement_mapping_json'),
        sa.Index('ix_statement_csv_mappings_name', 'account_id', 'name'),
        description='Named CSV column mappings saved per account for statement import.')
    return {'statement_imports': imports, 'statement_lines': lines, 'statement_csv_mappings': mappings}


# Every row is a fact about a file that was read; none is ever changed or removed.
TRIGGERS = tuple(
    f"CREATE TRIGGER {table}_no_{event.lower()} BEFORE {event} ON {table} "
    f"BEGIN SELECT RAISE(ABORT,'{table} is append-only'); END"
    for table in ('statement_imports', 'statement_lines', 'statement_csv_mappings')
    for event in ('UPDATE', 'DELETE'))
