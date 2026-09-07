"""Unregistered four-family Delete preparation. No persistence or public activation."""
from bookflow.company import document_effects
from bookflow.company import transaction_deletion_facts as facts
from bookflow.company.transaction_deletion_models import DeleteIntent, PreparedDelete, Tombstone, WorkRelease, BlockedDelete
from bookflow.company.payment_models import EffectProvenance
from bookflow.core.ids import new_id
from bookflow.core.session import now_iso
from bookflow.core.publication import OSBinding


def prepare_delete(s, ctx, intent, *, provenance=None, binding=None):
    intent = DeleteIntent.model_validate(intent.model_dump() if isinstance(intent, DeleteIntent) else intent)
    with facts.snapshot(s):
        # Activation precedes even producer capture and guessed record lookup.
        from bookflow.hub.access import require_explicit_grant
        require_explicit_grant(s, 'transaction.' + intent.family + '.delete')
        binding = binding if binding is not None else OSBinding.from_session(s, ctx.on_behalf_of)
        evidence, identity = facts.load(s, ctx, intent, binding)
        if evidence.blockers:
            return BlockedDelete(intent=intent,actor_id=identity[0],actor_kind=identity[1],principal_id=identity[2],
                interface=str(ctx.interface),reason=facts.normalized_reason(ctx),facts=evidence)
        # Caller-provided provenance is trusted private aggregate input. Future
        # persistence must issue its own writer-owned stamp, not public input.
        provenance = (EffectProvenance(at=now_iso(), event_id=new_id(), operation_id=new_id())
            if provenance is None else EffectProvenance.model_validate(provenance.model_dump()))
        header = evidence.header.values()
        pending = {name: [] for name in ('posting_batches', 'posting_lines', 'posting_line_sources')}
        inverse = None
        if header['status'] == 'posted':
            def created():
                return dict(id=new_id(), created_at=provenance.at, created_by=identity[0], created_via=str(ctx.interface))
            inverse = document_effects.reverse(s, header, evidence.revision.values(),
                evidence.business_batch.values(), provenance.event_id, created, pending)
        proposed = []
        for table, rows in pending.items():
            for value in rows:
                if table == 'posting_line_sources':
                    # Exact legacy helper omission contract, not generic null filling.
                    for name in ('payment_component_id', 'deposit_component_id'): value.setdefault(name, None)
                proposed.append(facts.row(table, value))
        prior = evidence.prior_void_batch
        releases = []
        for r in evidence.rows:
            if r.table != 'work_billing_allocations' or r.values()['id'] not in evidence.work_allocation_ids:
                continue
            v = r.values()
            current = v['id'] in evidence.current_work_allocation_ids
            if current:
                event = prior.values()['audit_event_id'] if prior else provenance.event_id
            else:
                event = facts.one([b for b in evidence.rows if b.table == 'posting_batches'
                    and b.values()['transaction_id'] == header['id']
                    and b.values()['revision_id'] == v['revision_id']
                    and b.values()['kind'] == 'reversal']).values()['audit_event_id']
            releases.append(WorkRelease(allocation_id=v['id'], root_document_id=v['root_document_id'],
                root_line_id=v['root_line_id'], event_id=event, newly_released=current and prior is None))
        return PreparedDelete(intent=intent, provenance=provenance, actor_id=identity[0], actor_kind=identity[1],
            principal_id=identity[2], interface=str(ctx.interface), facts=evidence,
            tombstone=Tombstone(transaction_id=header['id'], family=intent.family,
                before_version=header['version'], after_version=header['version']+1,
                current_revision_id=header['current_revision_id'], number=header['number'],
                deleted_from_status=header['status'], deleted_at=provenance.at, deleted_by=identity[0],
                delete_reason=facts.normalized_reason(ctx), delete_audit_event_id=provenance.event_id,
                delete_posting_batch_id=inverse['id'] if inverse else None,
                retained_void_batch_id=prior.values()['id'] if prior else None),
            inverse_rows=tuple(proposed), work_releases=tuple(releases))


def require_ready(result):
    """Standalone caller's actionable error; never discharges an obligation."""
    from bookflow.core.errors import BookflowError
    if isinstance(result,BlockedDelete):
        facts.require(bool(result.facts.blockers))
        blocker=result.facts.blockers[0]
        if blocker.kind=='applications':
            raise BookflowError('E_HAS_APPLICATIONS',details={blocker.family+'_id':blocker.transaction_id,
                'action':'unapply_first','next':'Inspect '+blocker.family+' settlement dependencies and unapply first.'})
        raise BookflowError('E_DEPOSIT_DEPENDENCY',details={'source':blocker.source_id,'deposit':blocker.deposit_id,
            'reason':'A claimed source requires atomic source and deposit cancellation.'})
    return result
