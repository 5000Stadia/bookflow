"""Delete authority over the existing deposit cancellation writer, never public void."""
import hashlib
import json
import sqlalchemy as sa
from bookflow.company import schema as c, journals
from bookflow.company import document_effects as effects
from bookflow.company import deposit_dependencies as dependencies
from bookflow.company import deposit_lifecycle as lifecycle, deposit_persistence as persistence
from bookflow.company import reconciliation_adapters as reconciliation
from bookflow.company.deposit_deletion_models import DepositDeleteOutput, DepositDeletionInfo
from bookflow.core import audit, clock
from bookflow.core.deletion_families import DEPOSIT_FAMILIES, capability
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Plan, Applied, Touched, MatchedRecovery
from bookflow.hub import access

FAMILY, = DEPOSIT_FAMILIES
NOUN = 'deposit'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def stored(s):
    # Historical migrations may call an older coordinator before co0054 exists.
    return sa.inspect(s.company.conn).has_table('deposit_deletions')


def deleted_ids(s, identities):
    """Which of these deposits are retained deleted history, in one read."""
    if not stored(s) or not identities:
        return set()
    ordered = sorted(set(identities))
    found = set()
    for offset in range(0, len(ordered), 200):
        found.update(s.company.conn.execute(sa.select(c.deposit_deletions.c.transaction_id).where(
            c.deposit_deletions.c.transaction_id.in_(ordered[offset:offset+200]))).scalars())
    return found


def require_not_deleted(s, identity):
    if identity and stored(s) and s.company.conn.execute(sa.select(c.deposit_deletions.c.transaction_id).where(
            c.deposit_deletions.c.transaction_id == identity)).first():
        raise journals.invalid('transaction', 'This deposit was deleted; its retained history cannot be edited, voided or coordinated.')


def admit(s):
    access.require_explicit_grant(s, capability(FAMILY))
    access.require_resource(s, 'ledger.read', 'member')


def request(inp, ctx, s):
    """The business intent, without the evidence that is allowed to differ on a retry.

    A deposit write is confirmed against a dependency guard and a facts fingerprint that
    a fresh preview mints fresh, so a permanent retry legitimately carries new ones. They
    are concurrency evidence about the moment, not what the person asked for, and hashing
    them would make every retry look like a different request. ``deposit_operations``
    excludes the same two for the same reason.
    """
    value = dict(command=NOUN+' delete', company_id=s.company_row['id'],
                 input=inp.model_dump(mode='json', exclude={'dependency_guard', 'expected_facts_fingerprint'}),
                 reason=ctx.reason)
    return value, hashlib.sha256(canonical(value).encode()).hexdigest()


def deletion_info(s, identity, include_deleted, *, record_type='deposit'):
    """Owning read overlay: hidden by default, attributed when explicitly asked for."""
    if not stored(s):
        return None
    row = s.company.conn.execute(sa.select(c.deposit_deletions).where(
        c.deposit_deletions.c.transaction_id == identity)).mappings().first()
    if row is None:
        return None
    if not include_deleted:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': record_type, 'selector': identity})
    from bookflow.company.info import principal_names
    names = principal_names(s.company, {x for x in (row['created_by'], row['principal_id']) if x})
    return DepositDeletionInfo(**{key: row[key] for key in DepositDeletionInfo.model_fields if key in row},
        created_by_name=names.get(row['created_by']), principal_name=names.get(row['principal_id']))


def recover(inp, ctx, s):
    admit(s)
    key = inp.operation_key or ctx.idempotency_key
    if not key or not stored(s):
        return None
    row = s.company.conn.execute(sa.select(c.deposit_deletions).where(
        c.deposit_deletions.c.created_by == s.actor.id,
        c.deposit_deletions.c.operation_key == key)).mappings().first()
    if row is None:
        return None
    snapshot = json.loads(row['request_snapshot'])
    reconciliation.authority(s, snapshot['authority_transactions'])
    dependencies.authorize(s, row['transaction_id'], snapshot['authority_transactions'])
    if row['family'] != FAMILY or row['request_hash'] != request(inp, ctx, s)[1]:
        raise BookflowError('E_IDEMPOTENCY_MISMATCH')
    return MatchedRecovery(DepositDeleteOutput.model_validate(json.loads(row['result_snapshot'])).model_copy(
        update={'changed': False, 'idempotent_replay': True}))


def resolve(s, identity):
    found = effects.rows(s, c.transactions, c.transactions.c.id == identity,
                         c.transactions.c.type == 'deposit')
    if len(found) != 1:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'deposit', 'selector': identity})
    return found[0]


def held_receipts(s, identity):
    """The receipts this deposit still claims; deletion gives every one of them back."""
    return sorted(s.company.conn.execute(sa.select(
        c.deposit_current_memberships.c.source_transaction_id).where(
        c.deposit_current_memberships.c.transaction_id == identity)).scalars())


def graph(s, old):
    """Current owning closure through its existing owners, at read thresholds only.

    This command's authority is the family Delete grant, which is deliberately
    independent of ``ledger.post``: the whole graph is admitted here at the thresholds
    a reader needs, and the deposit writer below is told not to demand posting again.
    """
    identifiers = reconciliation.authority(s, [old['id']])
    dependencies.authorize(s, old['id'], identifiers)
    return identifiers


def blockers(s, old):
    """A reconciled deposit is cancelled by its own owner, never by a delete behind it.

    A deposit's receipts are the other direction and they are owned handling rather than
    a refusal: deletion releases every claim back to Undeposited Funds, exactly as a void
    does, so each receipt stays posted, stays its own document, and can be banked again.
    """
    keys, members = c.reconciliation_keys, c.reconciliation_current_members
    held = s.company.conn.execute(sa.select(keys.c.id, members.c.claim_id).join(
        members, members.c.key_id == keys.c.id).where(
        keys.c.transaction_id == old['id'])).mappings().first()
    if held is not None:
        raise BookflowError('E_RECONCILIATION_DEPENDENCY',
            message='A bank reconciliation holds this deposit; deletion cannot detach it.',
            details={'deposit_id': old['id'], 'reconciliation_key_id': held['id'],
                     'reconciliation_claim_id': held['claim_id'],
                     'next': 'Undo the bank reconciliation that holds this deposit before deleting it.'})


def prepare(s, ctx, inp):
    admit(s)
    # A conflicting permanent key must refuse before any cancellation preparation.
    found = recover(inp, ctx, s)
    if found is not None:
        return Plan(found.output, dict(recovered=True, input=inp))
    old = resolve(s, inp.deposit)
    if inp.expected_version != old['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={'expected_version': inp.expected_version,
                                                           'current_version': old['version']})
    require_not_deleted(s, old['id'])
    identifiers = graph(s, old)
    blockers(s, old)
    receipts = held_receipts(s, old['id'])
    # The authenticated producer: the host's credential, or -- with no host, as on the
    # CLI -- this login's OS binding, which the lifecycle derives when none is supplied.
    binding = getattr(s, 'credential', None)
    prepared = lifecycle.prepare(s, ctx, inp, 'delete', binding=binding, posting=False)
    from bookflow.company.deposit_lifecycle_models import LifecycleOutput
    if isinstance(prepared, LifecycleOutput):
        # The deposit family's own permanent operation ledger already answered this key.
        raise BookflowError('E_DEPOSIT_OPERATION_KEY_REUSED')
    preview = persistence.preview(s, ctx, prepared)
    output = DepositDeleteOutput(id=old['id'], version=old['version'] + 1,
        revision_id=old['current_revision_id'], number=old['number'], from_status=old['status'],
        cancellation_batch_id=_batch(old, preview), released_receipt_ids=receipts,
        dependency_guard=prepared.dependency_guard, facts_fingerprint=prepared.facts_fingerprint)
    return Plan(output, dict(input=inp, prepared=prepared, old=old, receipts=receipts,
                             authority_transactions=identifiers))


def _batch(old, preview):
    """The exact reversing batch: the new one this deletion posts, or the retained prior
    void's own. A preview mints no physical identity, so the preview's is a logical token
    and the saved row reads the header instead."""
    if old['status'] == 'voided':
        return old['void_posting_batch_id']
    return preview.effect.batch_ids[-1] if preview.effect.batch_ids else None


def persist_tombstone(s, row):
    """Final persistence boundary, inside the same transaction as exact cancellation."""
    s.company.conn.execute(c.deposit_deletions.insert(), row)


def apply(plan, ctx, s):
    d = plan.data
    if d.get('recovered'):
        return Applied(plan.preview, [], 'Deposit deletion already completed')
    old, prepared = d['old'], d['prepared']
    command = NOUN + ' delete'
    at, event = clock.now_iso(), new_id()
    # The aggregate re-resolves, revalidates and audits itself inside this transaction;
    # this writer adds the retained deletion receipt and its own attributed event.
    result = persistence.execute(s, ctx, prepared)
    touched = []
    if not result.changed:
        # Complete exact no-money aggregate for the already-voided deposit: one more
        # version, no second inverse and no second release. The aggregate wrote nothing
        # because there was nothing left to cancel, so the version this deletion occupies
        # is this writer's to take.
        header = dict(old, version=old['version'] + 1, updated_at=at,
                      updated_by=s.actor.id, updated_via=ctx.interface.value)
        s.company.conn.execute(c.transactions.update().where(c.transactions.c.id == old['id']).values(**header))
        touched = [Touched('transaction', old['id'], 'update', old['version'], header['version'],
                           header, db='company')]
    fresh = resolve(s, old['id'])
    original, digest = request(d['input'], ctx, s)
    original['authority_transactions'] = list(d['authority_transactions'])
    output = plan.preview.model_copy(update={
        'version': fresh['version'], 'cancellation_batch_id': fresh['void_posting_batch_id']})
    row = dict(transaction_id=old['id'], family=FAMILY, revision_id=old['current_revision_id'],
        from_status=old['status'], from_version=old['version'], result_version=fresh['version'],
        cancellation_batch_id=fresh['void_posting_batch_id'], operation_id=result.operation_id,
        operation_key=d['input'].operation_key, request_hash=digest,
        request_snapshot=canonical(original), result_snapshot=canonical(output.model_dump(mode='json')),
        created_at=at, created_by=s.actor.id, principal_id=ctx.on_behalf_of,
        created_via=ctx.interface.value, reason=ctx.reason.strip(), audit_event_id=event)
    marker = Touched('deposit_deletion', old['id'], 'create', None, 1, row, db='company')
    audit.write_event_to(s.company, ctx, command, 'Delete bank deposit; retain its history',
        [*touched, marker], actor_id=s.actor.id, actor_kind=s.actor.kind, event_id=event)
    persist_tombstone(s, row)
    return Applied(output, [*touched, marker], 'Deleted bank deposit', audited=True)
