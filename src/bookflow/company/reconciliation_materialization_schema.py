"""The one place a posting write announces itself to statement effect storage.

`posting_batches` is written by more than fifty modules under `company/`, each with its own
persist loop, so there is no Python function every posting write passes through. There is one
*table* every posting write passes through, and that is what this fences: a SQLite trigger on
`posting_batches` records the owning document in `statement_effect_pending`, and a second one
does the same for `bank_effect_versions`, which is the deposit family's own effect record. A
writer added tomorrow does not have to know this exists -- it inserts a posting batch, which is
what makes it a posting write, and the row appears.

The queue is drained by `reconciliation_materialization.drain`, called once before and once
after every company-writing command applies. A row that survives a command is a posting write
that reached the database without passing the drain, which is exactly the defect this file
exists to make visible rather than silent.

This table deliberately does **not** carry the `reconciliation_` prefix. That prefix is an
inventory: `reconciliation_storage_validation.validate` requires a complete row set for every
table carrying it, and `0022_reconciliation_storage.NEW_TABLES` froze which tables those are.
A queue of work owed is not a reconciliation record and must not join that inventory.
"""
import sqlalchemy as sa


def define_tables(metadata, C, T):
    pending = T('statement_effect_pending',
        C('transaction_id', sa.String(26),
          'Business document whose statement effects have not been materialized yet.',
          nullable=False),
        sa.PrimaryKeyConstraint('transaction_id'),
        description='Posted documents owing statement effect materialization; drained inside the writing command.')
    return {'statement_effect_pending': pending}


# One trigger per ledger table that can carry a new statement effect. Both are unconditional:
# deciding in SQL which documents can touch a statement account would be a second copy of the
# adapter registry, and a wrong copy is worse than a short queue.
GUARDS = (
    "CREATE TRIGGER statement_effect_pending_posting_batch AFTER INSERT ON posting_batches "
    "BEGIN INSERT OR IGNORE INTO statement_effect_pending (transaction_id) VALUES (NEW.transaction_id); END",
    "CREATE TRIGGER statement_effect_pending_bank_effect_version AFTER INSERT ON bank_effect_versions "
    "BEGIN INSERT OR IGNORE INTO statement_effect_pending (transaction_id) VALUES (NEW.transaction_id); END",
)
