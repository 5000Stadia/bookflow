"""Delete authority over the private four-family kernel, never the public void writer."""
import hashlib
import json
import sqlalchemy as sa
from bookflow.company import schema as c, journals, sales, document_effects as effects
from bookflow.company import payment_authority, reconciliation_adapters as reconciliation
from bookflow.company import transaction_deletion, transaction_deletion_validation as verification
from bookflow.company.payment_deletion_models import PaymentDeleteOutput, PaymentDeletionInfo
from bookflow.company.transaction_deletion_models import DeleteIntent
from bookflow.core import audit
from bookflow.core.deletion_families import PAYMENT_FAMILIES, capability, deleted_record_refusal
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Plan, Applied, Touched, MatchedRecovery
from bookflow.hub import access

FAMILY, = PAYMENT_FAMILIES
NOUN = 'payment'
INVERSE_TABLES = (('posting_batches', 'posting_batch'), ('posting_lines', 'posting_line'),
                  ('posting_line_sources', 'posting_line_source'))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def stored(s):
    # Historical migrations may call an older coordinator before co0051 exists.
    return sa.inspect(s.company.conn).has_table('payment_deletions')


def require_not_deleted(s, identity, *, deleting=False):
    if stored(s) and s.company.conn.execute(sa.select(c.payment_deletions.c.transaction_id).where(
            c.payment_deletions.c.transaction_id == identity)).first():
        raise journals.invalid('transaction', deleted_record_refusal('payment', deleting=deleting))


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
    row = s.company.conn.execute(sa.select(c.payment_deletions).where(
        c.payment_deletions.c.transaction_id == header['id'])).mappings().first()
    if row is None:
        return None
    if not include_deleted:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': header['type'], 'selector': header['id']})
    from bookflow.company.info import principal_names
    names = principal_names(s.company, {x for x in (row['created_by'], row['principal_id']) if x})
    return PaymentDeletionInfo(**{key: row[key] for key in PaymentDeletionInfo.model_fields if key in row},
        created_by_name=names.get(row['created_by']), principal_name=names.get(row['principal_id']))


def authorize_graph(s, identifiers):
    """Whole retained closure through its existing owners; read thresholds only."""
    participants = reconciliation.authority(s, identifiers)
    payment_authority.authorize(s, participants)
    return participants


def recover(inp, ctx, s):
    admit(s)
    key = inp.operation_key or ctx.idempotency_key
    if not key or not stored(s):
        return None
    row = s.company.conn.execute(sa.select(c.payment_deletions).where(
        c.payment_deletions.c.created_by == s.actor.id,
        c.payment_deletions.c.operation_key == key)).mappings().first()
    if row is None:
        return None
    snapshot = json.loads(row['request_snapshot'])
    authorize_graph(s, snapshot['authority_transactions'])
    if row['family'] != FAMILY or row['request_hash'] != request(inp, ctx, s)[1]:
        raise BookflowError('E_IDEMPOTENCY_MISMATCH')
    return MatchedRecovery(PaymentDeleteOutput.model_validate(json.loads(row['result_snapshot'])).model_copy(
        update={'changed': False, 'idempotent_replay': True}))


def dependencies(s, identity):
    """What still stands on this receipt and is cancelled by its own owner, never by delete."""
    keys, members = c.reconciliation_keys, c.reconciliation_current_members
    held = s.company.conn.execute(sa.select(keys.c.id).join(members, members.c.key_id == keys.c.id).where(
        keys.c.transaction_id == identity)).first()
    if held is not None:
        raise BookflowError('E_RECONCILIATION_DEPENDENCY', details={'payment_id': identity,
            'next': 'Undo the bank reconciliation that holds this receipt before deleting it.'})
    # A refund that paid this receipt's overpayment back holds capacity it took from here.
    # Deleting the receipt under it would leave the refund debiting a receivable the cash no
    # longer credits, so the refund goes first -- the same order `payment void` requires.
    from bookflow.company.payment_queries import payment_facts
    live = payment_facts(s, identity)['consumptions']
    if live:
        raise BookflowError('E_HAS_REFUND', details={'payment_id': identity,
            'refund_ids': sorted({row['transaction_id'] for row in live}),
            'action': 'void_the_refund_first',
            'next': 'Void the refund that paid this overpayment back before deleting the receipt.'})


def prepare(s, ctx, inp):
    admit(s)
    # A conflicting permanent key must refuse before any cancellation preparation.
    found = recover(inp, ctx, s)
    if found is not None:
        return Plan(found.output, dict(recovered=True, input=inp))
    old = sales.resolve(s, inp.payment, NOUN)
    # Already ahead of the version guard, which lives in the prepared kernel below. The five
    # other families were moved to match this one.
    require_not_deleted(s, old['id'], deleting=True)
    intent = DeleteIntent(family=FAMILY, transaction_id=old['id'], expected_version=inp.expected_version)
    # The authenticated producer: the host's credential, or — with no host, as on the
    # CLI — this login's OS binding, which each owner derives for itself when none is
    # supplied. Deriving one under a hosted session cannot work, because the host holds
    # no OS login: an unsupplied binding refuses every request that is not in-process.
    binding = getattr(s, 'credential', None)
    prepared = transaction_deletion.require_ready(transaction_deletion.prepare_delete(s, ctx, intent, binding=binding))
    # Independent reload of the same evidence, by the owner that never prepared it.
    verification.validate_delete(s, ctx, prepared, binding=binding)
    dependencies(s, old['id'])
    tombstone = prepared.tombstone
    output = PaymentDeleteOutput(id=tombstone.transaction_id, version=tombstone.after_version,
        revision_id=tombstone.current_revision_id, number=tombstone.number,
        from_status=tombstone.deleted_from_status,
        cancellation_batch_id=tombstone.delete_posting_batch_id or tombstone.retained_void_batch_id,
        cancelled_posting_lines=sum(row.table == 'posting_lines' for row in prepared.inverse_rows))
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
    s.company.conn.execute(c.payment_deletions.insert(), row)


def apply(plan, ctx, s):
    fresh = prepare(s, ctx, plan.data['input'])
    if fresh.data.get('recovered'):
        return Applied(fresh.preview, [], 'Payment deletion already completed')
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
    marker = Touched('payment_deletion', old['id'], 'create', None, 1, row, db='company')
    audit.write_event_to(s.company, ctx, command, 'Delete customer payment; retain its history',
        [*touched, marker], actor_id=s.actor.id, actor_kind=s.actor.kind, event_id=event)
    for table, _ in INVERSE_TABLES:
        if pending[table]:
            s.company.conn.execute(getattr(c, table).insert(), pending[table])
    s.company.conn.execute(c.transactions.update().where(c.transactions.c.id == old['id']).values(**header))
    persist_tombstone(s, row)
    return Applied(fresh.preview, [*touched, marker], 'Deleted customer payment', audited=True)
