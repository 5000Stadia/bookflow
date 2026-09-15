"""Delete authority over the existing bill cancellation writer, never public void."""
import hashlib
import json
import sqlalchemy as sa
from bookflow.company import schema as c, bills, journals
from bookflow.company import reconciliation_adapters as reconciliation
from bookflow.company.bill_deletion_models import BillDeleteOutput, BillDeletionInfo
from bookflow.core import audit, clock
from bookflow.core.deletion_families import BILL_FAMILIES, capability
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Plan, Applied, Touched, MatchedRecovery
from bookflow.hub import access

FAMILY, = BILL_FAMILIES
NOUN = 'bill'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def stored(s):
    # Historical migrations may call an older coordinator before co0052 exists.
    return sa.inspect(s.company.conn).has_table('bill_deletions')


def require_not_deleted(s, identity):
    if stored(s) and s.company.conn.execute(sa.select(c.bill_deletions.c.transaction_id).where(
            c.bill_deletions.c.transaction_id == identity)).first():
        raise journals.invalid('transaction', 'This bill was deleted; its retained history cannot be edited or voided.')


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
    row = s.company.conn.execute(sa.select(c.bill_deletions).where(
        c.bill_deletions.c.transaction_id == header['id'])).mappings().first()
    if row is None:
        return None
    if not include_deleted:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': header['type'], 'selector': header['id']})
    from bookflow.company.info import principal_names
    names = principal_names(s.company, {x for x in (row['created_by'], row['principal_id']) if x})
    return BillDeletionInfo(**{key: row[key] for key in BillDeletionInfo.model_fields if key in row},
        created_by_name=names.get(row['created_by']), principal_name=names.get(row['principal_id']))


def recover(inp, ctx, s):
    admit(s)
    key = inp.operation_key or ctx.idempotency_key
    if not key or not stored(s):
        return None
    row = s.company.conn.execute(sa.select(c.bill_deletions).where(
        c.bill_deletions.c.created_by == s.actor.id,
        c.bill_deletions.c.operation_key == key)).mappings().first()
    if row is None:
        return None
    snapshot = json.loads(row['request_snapshot'])
    reconciliation.authority(s, snapshot['authority_transactions'])
    if row['family'] != FAMILY or row['request_hash'] != request(inp, ctx, s)[1]:
        raise BookflowError('E_IDEMPOTENCY_MISMATCH')
    return MatchedRecovery(BillDeleteOutput.model_validate(json.loads(row['result_snapshot'])).model_copy(
        update={'changed': False, 'idempotent_replay': True}))


def dependencies(s, old):
    """Current owning graph and claimed statement keys; no posting admission.

    The settlement refusal runs here rather than being left to the void writer's own
    coarser one, because a person who cannot delete a bill needs to be told *which*
    payment or credit still answers it. The ids are disclosed only after the whole
    settlement closure has been authorized through its existing owner, exactly as a
    live credit return's ids are on the sales side.

    A bill posts no bank or card leg -- it is absent from
    ``reconciliation_models.PRODUCER_ROLES`` -- so the held-statement-key read below
    is the cheap proof of that rather than an assumption about it.
    """
    identifiers = reconciliation.authority(s, [old['id']])
    a = c.ap_applications
    inverse = a.alias()
    live = s.company.conn.execute(sa.select(a.c.id, a.c.source_transaction_id).where(
        a.c.obligation_transaction_id == old['id'], a.c.kind == 'apply',
        ~sa.exists(sa.select(inverse.c.id).where(inverse.c.reverses_application_id == a.c.id)))).mappings().all()
    if live:
        sources = sorted({row['source_transaction_id'] for row in live})
        reconciliation.authority(s, sources)
        raise BookflowError('E_HAS_APPLICATIONS',
            message='A live settlement claims this bill; deletion cannot detach it.',
            details={'bill_id': old['id'], 'settlement_transaction_ids': sources,
                     'application_ids': sorted(row['id'] for row in live),
                     'next': 'Take the money back off this bill with `bill payment unapply` or '
                             '`vendor-credit unapply`, then delete it.'})
    keys, members = c.reconciliation_keys, c.reconciliation_current_members
    held = s.company.conn.execute(sa.select(keys.c.id).join(members, members.c.key_id == keys.c.id).where(
        keys.c.transaction_id == old['id'])).first()
    if held is not None:
        raise BookflowError('E_RECONCILIATION_DEPENDENCY', details={'bill_id': old['id'],
            'next': 'Undo the bank reconciliation that holds this bill before deleting it.'})
    return identifiers


def prepare(s, ctx, inp):
    admit(s)
    # A conflicting permanent key must refuse before any cancellation preparation.
    found = recover(inp, ctx, s)
    if found is not None:
        return Plan(found.output, dict(recovered=True, input=inp))
    old = bills.resolve(s, inp.bill)
    if inp.expected_version != old['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={'expected_version': inp.expected_version, 'current_version': old['version']})
    require_not_deleted(s, old['id'])
    identifiers = dependencies(s, old)
    from bookflow.company.bill_models import BillVoidInput
    intent = BillVoidInput.model_validate({'bill': old['id'], 'expected_version': inp.expected_version})
    inner = bills.prepare(s, ctx, intent, 'void')
    if not inner.data['changed']:
        # Complete exact no-money aggregate for the already-voided bill: one more
        # version, no second inverse.
        inner.data.update(header=old, before=old, pending={})
    header, stock = inner.data['header'], inner.data.get('stock')
    received = inner.data.get('received') or {}
    output = BillDeleteOutput(id=old['id'], version=old['version'] + 1,
        revision_id=old['current_revision_id'], number=old['number'], from_status=old['status'],
        cancellation_batch_id=header['void_posting_batch_id'],
        cancelled_stock_movements=len(stock.movements) if stock else 0,
        released_receipt_claims=len(received.get('prior') or ()),
        purchase_order_id=bills.order_ids(s, [old['id']]).get(old['id']))
    return Plan(output, dict(input=inp, inner=inner, old=old, authority_transactions=identifiers))


def persist_tombstone(s, row):
    """Final persistence boundary, inside the same transaction as exact cancellation."""
    s.company.conn.execute(c.bill_deletions.insert(), row)


def apply(plan, ctx, s):
    fresh = prepare(s, ctx, plan.data['input'])
    if fresh.data.get('recovered'):
        return Applied(fresh.preview, [], 'Bill deletion already completed')
    d = fresh.data
    inner, old = d['inner'], d['old']
    command = NOUN + ' delete'
    at, event = clock.now_iso(), new_id()
    if inner.data['changed']:
        applied = bills.persist_prepared(inner, ctx, s, command_name=command)
        touched = list(applied.touched)
    else:
        header = dict(old, version=old['version'] + 1, updated_at=at,
                      updated_by=s.actor.id, updated_via=ctx.interface.value)
        s.company.conn.execute(c.transactions.update().where(c.transactions.c.id == old['id']).values(**header))
        touched = [Touched('transaction', old['id'], 'update', old['version'], header['version'], header, db='company')]
    original, digest = request(d['input'], ctx, s)
    original['authority_transactions'] = list(d['authority_transactions'])
    row = dict(transaction_id=old['id'], family=FAMILY, revision_id=old['current_revision_id'],
        from_status=old['status'], from_version=old['version'], result_version=fresh.preview.version,
        cancellation_batch_id=fresh.preview.cancellation_batch_id,
        operation_key=d['input'].operation_key or ctx.idempotency_key or new_id(), request_hash=digest,
        request_snapshot=canonical(original), result_snapshot=canonical(fresh.preview.model_dump(mode='json')),
        created_at=at, created_by=s.actor.id, principal_id=ctx.on_behalf_of,
        created_via=ctx.interface.value, reason=ctx.reason.strip(), audit_event_id=event)
    marker = Touched('bill_deletion', old['id'], 'create', None, 1, row, db='company')
    audit.write_event_to(s.company, ctx, command, 'Delete vendor bill; retain its history',
        [*touched, marker] if not inner.data['changed'] else [marker],
        actor_id=s.actor.id, actor_kind=s.actor.kind, event_id=event)
    persist_tombstone(s, row)
    return Applied(fresh.preview, [*touched, marker], 'Deleted vendor bill', audited=True)
