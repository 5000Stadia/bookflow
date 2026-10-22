"""Assembly builds: one immutable row per revision of a build (R103, co0079).

A build turns components into a finished inventory assembly. A *posted* build is a journal entry
owned by the inventory ledger -- the components' stock issues and the assembly's stock receipt hang
off its inventory-asset lines exactly as an adjustment's movement does -- and this table is what says
that the entry *is* a build: which assembly, how many, the components it was built from, and the
build's own life (pending, built, voided, deleted).

A *pending* build has no journal entry and no movement: the anchor lets a build that is short of
components be marked pending, which posts nothing and reserves the components until it is finished.

Rows are never changed. Each change -- an edit, a finish, a void, a delete -- appends the build's next
revision, so a build's whole history is here, and its current state is its highest revision.
``components`` is the bill of materials captured when the build was entered, as canonical JSON, so
editing the item's bill of materials later never changes what an earlier build consumed.
"""
import sqlalchemy as sa

STATUSES = ('pending', 'built', 'voided', 'deleted')


def define_tables(metadata, C, T):
    builds = T('assembly_builds',
        C('id', sa.String(26), 'Stable ULID of this immutable build revision.', primary_key=True),
        C('build_id', sa.String(26), 'Stable ULID of the build every revision of it shares.', nullable=False),
        C('revision_number', sa.Integer, 'Revision of the build, from 1; the highest is current.', nullable=False),
        C('status', sa.String(16), 'pending (nothing posted, components reserved), built, voided or deleted.', nullable=False),
        C('item_id', sa.String(26), 'Inventory assembly item built.', sa.ForeignKey('items.id'), nullable=False),
        C('quantity_microunits', sa.BigInteger, 'Quantity of the assembly built, in micro-units.', nullable=False),
        C('build_date', sa.String(10), 'Date the build is dated and valued on, YYYY-MM-DD.', nullable=False),
        C('memo', sa.Text, 'Memo entered on the build; null when none.', nullable=True),
        C('reason', sa.Text, 'Why this revision was written, as the caller gave it; null when none.', nullable=True),
        C('transaction_id', sa.String(26), 'Journal entry the build posted as; null while pending and for a pending build deleted.',
          sa.ForeignKey('transactions.id'), nullable=True),
        C('components', sa.Text, 'Canonical JSON of the bill of materials captured for this build: item, kind, quantity per assembly in base micro-units, and for a non-stock component its unit cost and account.', nullable=False),
        C('created_at', sa.String(32), 'UTC time this revision was written.', nullable=False),
        C('created_by', sa.String(26), 'Company principal that wrote this revision.', nullable=False),
        C('created_via', sa.String(16), 'Interface this revision was written through.', nullable=False),
        C('audit_event_id', sa.String(26), 'Audit event that committed this revision.', sa.ForeignKey('audit_events.id'), nullable=False),
        sa.UniqueConstraint('build_id', 'revision_number', name='uq_assembly_build_revision'),
        sa.CheckConstraint("status IN ('pending','built','voided','deleted')", name='ck_assembly_build_status'),
        sa.CheckConstraint("typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0", name='ck_assembly_build_quantity'),
        sa.CheckConstraint('revision_number > 0', name='ck_assembly_build_revision_number'),
        sa.CheckConstraint("build_date LIKE '____-__-__'", name='ck_assembly_build_date'),
        sa.CheckConstraint("status = 'pending' OR status = 'deleted' OR transaction_id IS NOT NULL", name='ck_assembly_build_posted'),
        sa.CheckConstraint("status != 'pending' OR transaction_id IS NULL", name='ck_assembly_build_pending'),
        sa.CheckConstraint("json_valid(components) AND json_type(components) = 'array'", name='ck_assembly_build_components'),
        sa.Index('ix_assembly_builds_transaction', 'transaction_id'),
        sa.Index('ix_assembly_builds_item', 'item_id', 'build_date'),
        description='Immutable revisions of assembly builds: a finished assembly built from its components, pending or posted.')
    return {'assembly_builds': builds}
