"""Delete authority over the prepared journal-entry kernel, never the public void writer.

The obstacle this family has and no other does: a check, a card charge and a transfer are
all *stored* as ``transactions.type = 'journal_entry'`` and are told apart only by a row in
``money_out_documents``; an inventory adjustment and an item receipt are stored the same way
and told apart by their own markers. Two of those already delete through
``purchase_deletions``, so a journal delete that accepted one would write a second,
conflicting tombstone on a document that already carries one. ``journals.foreign_document``
owns that resolution for the journal editor and for this module alike, and
``journal_deletion_schema`` refuses the same thing again in the storage, so the receipt
cannot exist even if a future writer forgot to ask.
"""
import hashlib
import json
import sqlalchemy as sa
from bookflow.company import schema as c, journals, document_effects as effects
from bookflow.company import reconciliation_adapters as reconciliation
from bookflow.company import transaction_deletion, transaction_deletion_validation as verification
from bookflow.company.journal_deletion_models import JournalDeleteOutput, JournalDeletionInfo
from bookflow.company.transaction_deletion_models import DeleteIntent
from bookflow.core import audit
from bookflow.core.deletion_families import PREPARED_FAMILIES, capability
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Plan, Applied, Touched, MatchedRecovery
from bookflow.hub import access

FAMILY = PREPARED_FAMILIES[0]
NOUN = 'journal'
INVERSE_TABLES = (('posting_batches', 'posting_batch'), ('posting_lines', 'posting_line'),
                  ('posting_line_sources', 'posting_line_source'))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def stored(s):
    # Historical migrations may call an older coordinator before co0056 exists.
    return sa.inspect(s.company.conn).has_table('journal_deletions')


def require_not_deleted(s, identity):
    if stored(s) and s.company.conn.execute(sa.select(c.journal_deletions.c.transaction_id).where(
            c.journal_deletions.c.transaction_id == identity)).first():
        raise journals.invalid('transaction', 'This journal entry was deleted; its retained history cannot be edited or voided.')


def admit(s):
    access.require_explicit_grant(s, capability(FAMILY))
    access.require_resource(s, 'ledger.read', 'member')


def request(inp, ctx, s):
    value = dict(command=NOUN+' delete', company_id=s.company_row['id'],
                 input=inp.model_dump(mode='json'), reason=ctx.reason)
    return value, hashlib.sha256(canonical(value).encode()).hexdigest()


def deletion_info(s, header, include_deleted):
    """Owning read overlay: hidden by default, attributed when explicitly asked for."""
    if not stored(s):
        return None
    row = s.company.conn.execute(sa.select(c.journal_deletions).where(
        c.journal_deletions.c.transaction_id == header['id'])).mappings().first()
    if row is None:
        return None
    if not include_deleted:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': header['type'], 'selector': header['id']})
    from bookflow.company.info import principal_names
    names = principal_names(s.company, {x for x in (row['created_by'], row['principal_id']) if x})
    return JournalDeletionInfo(**{key: row[key] for key in JournalDeletionInfo.model_fields if key in row},
        created_by_name=names.get(row['created_by']), principal_name=names.get(row['principal_id']))


def deleted_ids(s, identifiers):
    """Which of these journal-stored transactions carry a retained deletion."""
    if not stored(s) or not identifiers:
        return set()
    return set(s.company.conn.execute(sa.select(c.journal_deletions.c.transaction_id).where(
        c.journal_deletions.c.transaction_id.in_(list(identifiers)))).scalars())


def recover(inp, ctx, s):
    admit(s)
    key = inp.operation_key or ctx.idempotency_key
    if not key or not stored(s):
        return None
    row = s.company.conn.execute(sa.select(c.journal_deletions).where(
        c.journal_deletions.c.created_by == s.actor.id,
        c.journal_deletions.c.operation_key == key)).mappings().first()
    if row is None:
        return None
    snapshot = json.loads(row['request_snapshot'])
    reconciliation.authority(s, snapshot['authority_transactions'])
    if row['family'] != FAMILY or row['request_hash'] != request(inp, ctx, s)[1]:
        raise BookflowError('E_IDEMPOTENCY_MISMATCH')
    return MatchedRecovery(JournalDeleteOutput.model_validate(json.loads(row['result_snapshot'])).model_copy(
        update={'changed': False, 'idempotent_replay': True}))


# Where a settlement can still answer a journal entry, and what releases it. The prepared
# kernel computes live applications only for the two families that own a settlement of their
# own, so this family reads them itself: a journal line can sit in a customer or vendor
# ledger and be claimed like any other open item.
SETTLEMENTS = (
    ('applications', ('paying_transaction_id', 'paid_transaction_id'), 'payment unapply'),
    ('ap_applications', ('source_transaction_id', 'obligation_transaction_id'),
     'bill payment unapply or vendor-credit unapply'),
)


def dependencies(s, identity):
    """Current owning graph: what still answers this entry, named rather than cascaded.

    Both refusals name the blocking record, because a person who cannot delete an entry needs
    to know *which* settlement or reconciliation is holding it. The counterparty ids are
    disclosed only after the whole closure has been authorized through its existing owner,
    exactly as a live settlement's ids are on the bill side. Called after the kernel has
    prepared and independently validated, so nothing here is what admits the caller.
    """
    for name, fields, release in SETTLEMENTS:
        table = c.metadata.tables[name]
        inverse = table.alias()
        live = s.company.conn.execute(sa.select(table).where(
            table.c.kind == 'apply',
            ~sa.exists(sa.select(inverse.c.id).where(inverse.c.reverses_application_id == table.c.id)),
            sa.or_(*(table.c[field] == identity for field in fields)))).mappings().all()
        if live:
            others = sorted({row[field] for row in live for field in fields} - {identity})
            reconciliation.authority(s, others)
            raise BookflowError('E_HAS_APPLICATIONS',
                message='A live settlement claims this journal entry; deletion cannot detach it.',
                details={'journal_id': identity, 'settlement_transaction_ids': others,
                         'application_ids': sorted(row['id'] for row in live),
                         'next': f'Release these applications with `{release}`, then delete.'})
    keys, members = c.reconciliation_keys, c.reconciliation_current_members
    held = s.company.conn.execute(sa.select(keys.c.id).join(members, members.c.key_id == keys.c.id).where(
        keys.c.transaction_id == identity)).first()
    if held is not None:
        raise BookflowError('E_RECONCILIATION_DEPENDENCY', details={'journal_id': identity,
            'reconciliation_key_id': held[0],
            'next': 'Undo the bank reconciliation that holds this entry before deleting it.'})


def prepare(s, ctx, inp):
    admit(s)
    # A conflicting permanent key must refuse before any cancellation preparation.
    found = recover(inp, ctx, s)
    if found is not None:
        return Plan(found.output, dict(recovered=True, input=inp))
    old = journals.resolve(s, inp.journal)
    require_not_deleted(s, old['id'])
    # What this transaction really is, before anything is prepared against it. A cheque
    # deleted here would carry two tombstones; an inventory document would leave its stock
    # movements behind.
    refusal = journals.foreign_document(s, old['id'], verb='delete')
    if refusal is not None:
        raise refusal
    intent = DeleteIntent(family=FAMILY, transaction_id=old['id'], expected_version=inp.expected_version)
    # The authenticated producer: the host's credential, or -- with no host, as on the
    # CLI -- this login's OS binding, which each owner derives for itself when none is
    # supplied.
    binding = getattr(s, 'credential', None)
    prepared = transaction_deletion.require_ready(transaction_deletion.prepare_delete(s, ctx, intent, binding=binding))
    # Independent reload of the same evidence, by the owner that never prepared it.
    verification.validate_delete(s, ctx, prepared, binding=binding)
    dependencies(s, old['id'])
    tombstone = prepared.tombstone
    output = JournalDeleteOutput(id=tombstone.transaction_id, version=tombstone.after_version,
        revision_id=tombstone.current_revision_id, number=tombstone.number,
        from_status=tombstone.deleted_from_status,
        cancellation_batch_id=tombstone.delete_posting_batch_id or tombstone.retained_void_batch_id)
    return Plan(output, dict(input=inp, prepared=prepared, old=old,
                             authority_transactions=prepared.facts.participants))


def cancelled_header(old, prepared, ctx, s):
    """Exactly one new version; a retained prior void keeps its own original facts."""
    tombstone = prepared.tombstone
    header = dict(old, version=tombstone.after_version, updated_at=tombstone.deleted_at,
                  updated_by=s.actor.id, updated_via=ctx.interface.value)
    if tombstone.delete_posting_batch_id is not None:
        header.update(status='voided', voided_at=tombstone.deleted_at, voided_by=s.actor.id,
                      void_reason=tombstone.delete_reason, void_posting_batch_id=tombstone.delete_posting_batch_id)
    return header


def persist_tombstone(s, row):
    """Final persistence boundary, inside the same transaction as exact cancellation."""
    s.company.conn.execute(c.journal_deletions.insert(), row)


def apply(plan, ctx, s):
    fresh = prepare(s, ctx, plan.data['input'])
    if fresh.data.get('recovered'):
        return Applied(fresh.preview, [], 'Journal entry deletion already completed')
    d = fresh.data
    prepared, old = d['prepared'], d['old']
    command, tombstone = NOUN+' delete', prepared.tombstone
    event = tombstone.delete_audit_event_id
    header = cancelled_header(old, prepared, ctx, s)
    pending = {table: [row.values() for row in prepared.inverse_rows if row.table == table]
               for table, _ in INVERSE_TABLES}
    touched = [Touched('transaction', old['id'], 'update', old['version'], header['version'], header, old, db='company')]
    for table, kind in INVERSE_TABLES:
        touched.extend(Touched(kind, row['id'], 'create', None, 1, effects.decoded(row), db='company')
                       for row in pending[table])
    original, digest = request(d['input'], ctx, s)
    original['authority_transactions'] = list(d['authority_transactions'])
    row = dict(transaction_id=old['id'], family=FAMILY, revision_id=tombstone.current_revision_id,
        from_status=tombstone.deleted_from_status, from_version=tombstone.before_version,
        result_version=tombstone.after_version, cancellation_batch_id=fresh.preview.cancellation_batch_id,
        operation_key=d['input'].operation_key or ctx.idempotency_key or new_id(), request_hash=digest,
        request_snapshot=canonical(original), result_snapshot=canonical(fresh.preview.model_dump(mode='json')),
        created_at=tombstone.deleted_at, created_by=s.actor.id, principal_id=prepared.principal_id,
        created_via=ctx.interface.value, reason=tombstone.delete_reason, audit_event_id=event)
    marker = Touched('journal_deletion', old['id'], 'create', None, 1, row, db='company')
    audit.write_event_to(s.company, ctx, command, 'Delete journal entry; retain its history',
        [*touched, marker], actor_id=s.actor.id, actor_kind=s.actor.kind, event_id=event)
    for table, _ in INVERSE_TABLES:
        if pending[table]:
            s.company.conn.execute(getattr(c, table).insert(), pending[table])
    s.company.conn.execute(c.transactions.update().where(c.transactions.c.id == old['id']).values(**header))
    persist_tombstone(s, row)
    return Applied(fresh.preview, [*touched, marker], 'Deleted journal entry', audited=True)
