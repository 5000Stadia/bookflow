"""Delete authority over the existing sales cancellation writer, never public void."""
import hashlib
import json
import sqlalchemy as sa
from bookflow.company import schema as c, sales, journals
from bookflow.company import billing_queries, reconciliation_adapters as reconciliation
from bookflow.company.sales_deletion_models import SalesDeleteOutput
from bookflow.core import audit, clock
from bookflow.core.deletion_families import capability
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Plan, Applied, Touched, MatchedRecovery
from bookflow.hub import access


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def require_not_deleted(s, identity):
    # Historical migrations may call an older coordinator before co0050 exists.
    if sa.inspect(s.company.conn).has_table('sales_deletions') and s.company.conn.execute(
            sa.select(c.sales_deletions.c.transaction_id).where(c.sales_deletions.c.transaction_id == identity)).first():
        raise journals.invalid('transaction', 'This sale was deleted; its retained history cannot be edited or voided.')


def admit(s, noun):
    access.require_explicit_grant(s, capability(noun.replace('-', '_')))
    access.require_resource(s, 'ledger.read', 'member')


def request(inp, ctx, s, noun):
    value = dict(command=noun+' delete', company_id=s.company_row['id'],
                 input=inp.model_dump(mode='json'), reason=ctx.reason)
    return value, hashlib.sha256(canonical(value).encode()).hexdigest()


def recover(inp, ctx, s, noun):
    admit(s, noun)
    billing_queries.authorize_sale(inp, ctx, s, noun.replace('-', '_'), True)
    key = inp.operation_key or ctx.idempotency_key
    if not key or not sa.inspect(s.company.conn).has_table('sales_deletions'):
        return None
    row = s.company.conn.execute(sa.select(c.sales_deletions).where(
        c.sales_deletions.c.created_by == s.actor.id,
        c.sales_deletions.c.operation_key == key)).mappings().first()
    if row is None:
        return None
    snapshot = json.loads(row['request_snapshot'])
    reconciliation.authority(s, snapshot['authority_transactions'])
    if row['family'] != noun.replace('-', '_') or row['request_hash'] != request(inp, ctx, s, noun)[1]:
        raise BookflowError('E_IDEMPOTENCY_MISMATCH')
    return MatchedRecovery(SalesDeleteOutput.model_validate(json.loads(row['result_snapshot'])).model_copy(
        update={'changed': False, 'idempotent_replay': True}))


def dependencies(s, ctx, header, inner):
    """Current owning graph and claimed statement keys; no posting admission."""
    from bookflow.company import deposit_dependencies
    from bookflow.company.reconciliation_preparation import adapter_errors
    identifiers = reconciliation.authority(s, [header['id']])
    if deposit_dependencies.active_claim(s, header['id']) is not None:
        raise BookflowError('E_DEPOSIT_DEPENDENCY')
    if header['type'] == 'invoice':
        from bookflow.company import credits
        line_ids = s.company.conn.execute(sa.select(c.credit_source_claims.c.source_line_id).where(
            c.credit_source_claims.c.source_transaction_id == header['id']).distinct()).scalars()
        claims = [claim for line in line_ids for claim in credits.active_source_claims(s, header['id'], line)]
        if claims:
            creditors = sorted({claim['credit_transaction_id'] for claim in claims})
            reconciliation.authority(s, creditors)
            raise BookflowError('E_SOURCE_CORRECTION_CONFLICT', message='A live credit return claims this invoice; deletion cannot detach it.',
                details={'credit_memo_ids': creditors, 'next': 'Inspect these credit memos and void the return through credit-memo void, resolving its dependencies first.'})
    with adapter_errors():
        changes = reconciliation.prepare_prospective(s, ctx, inner).changes
    if isinstance(changes, reconciliation.UnsupportedPopulation):
        raise journals.invalid('transaction', 'The statement effects cannot be safely cancelled.')
    refs = {v.ref for v in changes.before}
    keys, members = c.reconciliation_keys, c.reconciliation_current_members
    held = s.company.conn.execute(sa.select(keys).join(members, members.c.key_id == keys.c.id).where(
        keys.c.transaction_id.in_(identifiers))).mappings()
    for row in held:
        ref = reconciliation.StatementEffectRef(producer=row['producer'], transaction_id=row['transaction_id'],
            component_id=row['deposit_key_id'] or row['commercial_line_id'] or row['transaction_id'], role=row['role'])
        if ref in refs:
            raise BookflowError('E_RECONCILIATION_DEPENDENCY')
    return identifiers


def prepare(s, ctx, inp, noun):
    admit(s, noun)
    # A conflicting permanent key must refuse before any cancellation preparation.
    found = recover(inp, ctx, s, noun)
    if found is not None:
        return Plan(found.output, dict(recovered=True, input=inp, noun=noun))
    kind = noun.replace('-', '_')
    old = sales.resolve(s, getattr(inp, kind), kind)
    if inp.expected_version != old['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={'expected_version': inp.expected_version, 'current_version': old['version']})
    require_not_deleted(s, old['id'])
    from bookflow.company.sales_models import InvoiceVoidInput, SalesReceiptVoidInput
    model = InvoiceVoidInput if kind == 'invoice' else SalesReceiptVoidInput
    intent = model.model_validate({kind: old['id'], 'expected_version': inp.expected_version})
    inner = sales.prepare(s, ctx, intent, kind, 'void')
    stock = inner.data.get('stock')
    if not inner.data['changed']:
        inner.data.update(header=old, before=old, pending={})
    identifiers = dependencies(s, ctx, old, inner)
    header = inner.data['header']
    output = SalesDeleteOutput(id=old['id'], family=noun.replace('-', '_'),
        version=old['version']+1, revision_id=old['current_revision_id'], from_status=old['status'],
        cancellation_batch_id=header['void_posting_batch_id'],
        cancelled_stock_movements=len(stock.movements) if stock else 0)
    return Plan(output, dict(input=inp, noun=noun, inner=inner, stock=stock,
                             old=old, authority_transactions=identifiers))


def persist_tombstone(s, row):
    """Final persistence boundary, inside the same transaction as exact cancellation."""
    s.company.conn.execute(c.sales_deletions.insert(), row)


def apply(plan, ctx, s):
    fresh = prepare(s, ctx, plan.data['input'], plan.data['noun'])
    if fresh.data.get('recovered'):
        return Applied(fresh.preview, [], 'Sales deletion already completed')
    d = fresh.data
    inner, old, noun = d['inner'], d['old'], d['noun']
    command = noun+' delete'
    at, event = clock.now_iso(), new_id()
    if inner.data['changed']:
        applied = sales.persist_prepared(inner, ctx, s, command_name=command)
        touched = list(applied.touched)
    else:
        header = dict(old, version=old['version']+1, updated_at=at,
                      updated_by=s.actor.id, updated_via=ctx.interface.value)
        s.company.conn.execute(c.transactions.update().where(c.transactions.c.id == old['id']).values(**header))
        touched = [Touched('transaction', old['id'], 'update', old['version'], header['version'], header, db='company')]
    original, digest = request(d['input'], ctx, s, noun)
    original['authority_transactions'] = list(d['authority_transactions'])
    row = dict(transaction_id=old['id'], family=noun.replace('-', '_'), revision_id=old['current_revision_id'],
        from_status=old['status'], from_version=old['version'], result_version=fresh.preview.version,
        cancellation_batch_id=fresh.preview.cancellation_batch_id,
        operation_key=d['input'].operation_key or ctx.idempotency_key or new_id(), request_hash=digest,
        request_snapshot=canonical(original), result_snapshot=canonical(fresh.preview.model_dump(mode='json')),
        created_at=at, created_by=s.actor.id, principal_id=ctx.on_behalf_of,
        created_via=ctx.interface.value, reason=ctx.reason.strip(), audit_event_id=event)
    marker = Touched('sales_deletion', old['id'], 'create', None, 1, row, db='company')
    audit.write_event_to(s.company, ctx, command, 'Delete sale; retain its history',
        [*touched, marker] if not inner.data['changed'] else [marker],
        actor_id=s.actor.id, actor_kind=s.actor.kind, event_id=event)
    persist_tombstone(s, row)
    return Applied(fresh.preview, [*touched, marker], 'Deleted sale', audited=True)
