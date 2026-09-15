"""Delete authority over the existing credit-memo cancellation writer, never public void."""
import hashlib
import json
import sqlalchemy as sa
from bookflow.company import schema as c, credits, journals
from bookflow.company import document_effects as effects
from bookflow.company import payment_authority
from bookflow.company import reconciliation_adapters as reconciliation
from bookflow.company.credit_deletion_models import CreditMemoDeleteOutput, CreditMemoDeletionInfo
from bookflow.core import audit, clock
from bookflow.core.deletion_families import CREDIT_FAMILIES, capability
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Plan, Applied, Touched, MatchedRecovery
from bookflow.hub import access

FAMILY, = CREDIT_FAMILIES
NOUN = 'credit-memo'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def stored(s):
    # Historical migrations may call an older coordinator before co0053 exists.
    return sa.inspect(s.company.conn).has_table('credit_deletions')


def require_not_deleted(s, identity):
    if stored(s) and s.company.conn.execute(sa.select(c.credit_deletions.c.transaction_id).where(
            c.credit_deletions.c.transaction_id == identity)).first():
        raise journals.invalid('transaction', 'This credit memo was deleted; its retained history cannot be edited, voided, applied or refunded.')


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
    row = s.company.conn.execute(sa.select(c.credit_deletions).where(
        c.credit_deletions.c.transaction_id == header['id'])).mappings().first()
    if row is None:
        return None
    if not include_deleted:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': header['type'], 'selector': header['id']})
    from bookflow.company.info import principal_names
    names = principal_names(s.company, {x for x in (row['created_by'], row['principal_id']) if x})
    return CreditMemoDeletionInfo(**{key: row[key] for key in CreditMemoDeletionInfo.model_fields if key in row},
        created_by_name=names.get(row['created_by']), principal_name=names.get(row['principal_id']))


def recover(inp, ctx, s):
    admit(s)
    key = inp.operation_key or ctx.idempotency_key
    if not key or not stored(s):
        return None
    row = s.company.conn.execute(sa.select(c.credit_deletions).where(
        c.credit_deletions.c.created_by == s.actor.id,
        c.credit_deletions.c.operation_key == key)).mappings().first()
    if row is None:
        return None
    snapshot = json.loads(row['request_snapshot'])
    reconciliation.authority(s, snapshot['authority_transactions'])
    if row['family'] != FAMILY or row['request_hash'] != request(inp, ctx, s)[1]:
        raise BookflowError('E_IDEMPOTENCY_MISMATCH')
    return MatchedRecovery(CreditMemoDeleteOutput.model_validate(json.loads(row['result_snapshot'])).model_copy(
        update={'changed': False, 'idempotent_replay': True}))


def held_claims(s, identity):
    """This credit's own returned-quantity intervals that no release has given back."""
    claims = c.credit_source_claims
    released = sa.select(claims.c.reverses_claim_id).where(claims.c.kind == 'release')
    return effects.rows(s, claims, claims.c.credit_transaction_id == identity,
                        claims.c.kind == 'claim', claims.c.id.notin_(released),
                        order=claims.c.id)


def dependencies(s, old):
    """Current owning graph and claimed statement keys; no posting admission.

    A credit memo is claimed from two directions and they are not symmetrical. What
    stands *on* the credit -- an invoice it settled, a refund drawn on its capacity --
    refuses, because releasing either silently would leave an invoice settled by a
    document that is gone or cash paid against capacity that no longer exists. What the
    credit itself holds -- the returned quantity intervals it took off an invoice line --
    is owned handling: deletion gives every one of them back, exactly as void does, so
    the invoice can be returned again.

    The ids are disclosed only after the whole settlement closure has been authorized
    through its existing owner, exactly as a live credit return's ids are on the sales side.

    A credit memo posts a receivable, income and tax and no bank or card leg -- it is
    absent from ``reconciliation_models.PRODUCER_ROLES`` -- so the held-statement-key read
    below is the cheap proof of that rather than an assumption about it.
    """
    identifiers = reconciliation.authority(s, [old['id']])
    # The whole retained closure through its existing owner, at read thresholds only:
    # this command's authority is the family Delete grant, never ledger.post.
    payment_authority.authorize(s, identifiers)
    live = credits.active_applications(s, old['id'])
    if live:
        invoices = sorted({row['paid_transaction_id'] for row in live})
        reconciliation.authority(s, invoices)
        raise BookflowError('E_HAS_APPLICATIONS',
            message='A live application claims this credit memo; deletion cannot detach it.',
            details={'credit_memo_id': old['id'], 'invoice_ids': invoices,
                     'application_ids': sorted(row['id'] for row in live),
                     'next': 'Take the credit back off those invoices with `customer-credit unapply`, '
                             'then delete it.'})
    keys = effects.rows(s, c.credit_source_keys, c.credit_source_keys.c.transaction_id == old['id'])
    consumptions = [row for key in keys for row in credits.active_consumptions(s, key_id=key['id'])]
    if consumptions:
        refunds = sorted({row['transaction_id'] for row in consumptions})
        reconciliation.authority(s, refunds)
        raise BookflowError('E_HAS_REFUND',
            message='A live customer refund was paid out of this credit memo; deletion cannot detach it.',
            details={'credit_memo_id': old['id'], 'refund_ids': refunds,
                     'consumption_ids': sorted(row['id'] for row in consumptions),
                     'next': 'Void the refund that paid this credit out with `customer-refund void`, '
                             'then delete it.'})
    statement_keys, members = c.reconciliation_keys, c.reconciliation_current_members
    held = s.company.conn.execute(sa.select(statement_keys.c.id).join(
        members, members.c.key_id == statement_keys.c.id).where(
        statement_keys.c.transaction_id == old['id'])).first()
    if held is not None:
        raise BookflowError('E_RECONCILIATION_DEPENDENCY', details={'credit_memo_id': old['id'],
            'next': 'Undo the bank reconciliation that holds this credit memo before deleting it.'})
    return identifiers


def prepare(s, ctx, inp):
    admit(s)
    # A conflicting permanent key must refuse before any cancellation preparation.
    found = recover(inp, ctx, s)
    if found is not None:
        return Plan(found.output, dict(recovered=True, input=inp))
    old = credits.resolve(s, inp.credit_memo)
    if inp.expected_version != old['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={'expected_version': inp.expected_version, 'current_version': old['version']})
    require_not_deleted(s, old['id'])
    identifiers = dependencies(s, old)
    claims = held_claims(s, old['id'])
    from bookflow.company.credit_models import CreditMemoVoidInput
    intent = CreditMemoVoidInput.model_validate({'credit_memo': old['id'], 'expected_version': inp.expected_version})
    inner = credits.prepare_void(s, ctx, intent, posting=False)
    if not inner.data['changed']:
        # Complete exact no-money aggregate for the already-voided credit: one more
        # version, no second inverse.
        revision = effects.rows(s, c.transaction_revisions,
                                c.transaction_revisions.c.id == old['current_revision_id'])[0]
        inner.data.update(header=old, before=old, pending={}, revision=revision)
    header = inner.data['header']
    output = CreditMemoDeleteOutput(id=old['id'], version=old['version'] + 1,
        revision_id=old['current_revision_id'], number=old['number'], from_status=old['status'],
        cancellation_batch_id=header['void_posting_batch_id'],
        released_source_claims=len(claims),
        source_invoice_ids=sorted({row['source_transaction_id'] for row in claims}))
    return Plan(output, dict(input=inp, inner=inner, old=old, authority_transactions=identifiers))


def persist_tombstone(s, row):
    """Final persistence boundary, inside the same transaction as exact cancellation."""
    s.company.conn.execute(c.credit_deletions.insert(), row)


def apply(plan, ctx, s):
    fresh = prepare(s, ctx, plan.data['input'])
    if fresh.data.get('recovered'):
        return Applied(fresh.preview, [], 'Credit memo deletion already completed')
    d = fresh.data
    inner, old = d['inner'], d['old']
    command = NOUN + ' delete'
    at, event = clock.now_iso(), new_id()
    if inner.data['changed']:
        from bookflow.company.credit_validation import validate_void
        validate_void(inner, s, ctx)
        applied = effects.persist(inner, ctx, s, command_name=command, table_kinds=credits.TABLE_KINDS)
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
    marker = Touched('credit_memo_deletion', old['id'], 'create', None, 1, row, db='company')
    audit.write_event_to(s.company, ctx, command, 'Delete customer credit memo; retain its history',
        [*touched, marker] if not inner.data['changed'] else [marker],
        actor_id=s.actor.id, actor_kind=s.actor.kind, event_id=event)
    persist_tombstone(s, row)
    return Applied(fresh.preview, [*touched, marker], 'Deleted customer credit memo', audited=True)
