"""Guided opening balances (co0117, R107 increment 7; design/bulk-import.md §6).

``opening_drafts``: what an owner with no old books typed toward an opening, kept so a closed browser loses nothing:
the cutover date, each step's pasted rows (or an attachment holding them), the totals the owner gave, the stock items
a person confirmed may be made, and the state of the draft. `opening-draft plan`, `apply` and `tie-out` build the
`cutover` engine's input from it; nothing here posts. Once applied, ``batch_id`` names the batch its records belong
to, so the opening reverses as one unit (``batches``, ``batch_effects``). Never deleted: a draft no longer wanted is
``discarded``.
"""
import sqlalchemy as sa

STATES = ('draft', 'planned', 'applying', 'applied', 'rolled_back', 'failed', 'reversed', 'discarded')


def define_tables(metadata, C, T):
    states = ', '.join(f"'{state}'" for state in STATES)
    drafts = T('opening_drafts',
        C('id', sa.String(26), 'Stable ULID of the draft.', primary_key=True),
        C('label', sa.String(200), 'What the draft is, as a person reads it.', nullable=False),
        C('as_of', sa.String(10), 'The cutover date: balances are as of the end of this day.', nullable=False),
        C('state', sa.String(16), 'draft, planned (a plan was read), applying (posting; the same command continues '
                                  'it), applied, rolled_back (a refusal undid what it posted), failed (that undo was '
                                  'refused; reverse it once cleared), reversed (undone as one unit) or discarded.',
          nullable=False),
        C('files', sa.Text, 'JSON: each step (balances, open_invoices, unpaid_bills, stock) and its rows, as pasted text '
                            'or an attachment id, with the date order when given.', nullable=False),
        C('given', sa.Text, 'JSON: the totals the owner gave to check against (receivables, payables, stock), as '
                            'amounts; a total left out is taken from the rows.', nullable=False),
        C('make_items', sa.Text, 'JSON list of stock items a person confirmed may be made as new inventory parts.',
          nullable=False),
        C('clearing_account', sa.String(200), 'The clearing account named; null for Cutover Clearing.', nullable=True),
        C('batch_id', sa.String(26), 'The batch the opening posted in; null until it is applied.',
          sa.ForeignKey('batches.id'), nullable=True),
        C('summary', sa.Text, 'JSON of the last plan or apply: ready, counts, checks and blocking lines; null before.',
          nullable=True),
        C('version', sa.Integer, 'Optimistic-concurrency version of the draft.', nullable=False),
        C('created_at', sa.String(32), 'UTC time it was made.', nullable=False),
        C('created_by', sa.String(26), 'Principal that made it.', nullable=False),
        C('updated_at', sa.String(32), 'UTC time of the last change.', nullable=False),
        C('applied_at', sa.String(32), 'UTC time it was applied; null before.', nullable=True),
        sa.CheckConstraint(f'state IN ({states})', name='ck_opening_draft_state'),
        sa.Index('ix_opening_drafts_created', 'created_at'),
        description='Guided opening balances: what an owner typed toward an opening, planned, posted and tied out '
                    'through the cutover engine as one reversible batch. Never deleted.')
    return {'opening_drafts': drafts}


GUARDS = (
    "CREATE TRIGGER opening_drafts_no_delete BEFORE DELETE ON opening_drafts BEGIN "
    "SELECT RAISE(ABORT, 'opening drafts are never deleted'); END",
    "CREATE TRIGGER opening_drafts_frozen BEFORE UPDATE ON opening_drafts WHEN NEW.id IS NOT OLD.id "
    "OR NEW.created_at IS NOT OLD.created_at OR NEW.created_by IS NOT OLD.created_by "
    "OR NEW.version <= OLD.version BEGIN "
    "SELECT RAISE(ABORT, 'an opening draft keeps its identity and moves its version forward'); END",
)
