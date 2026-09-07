"""Independent proposed-Delete checks against fresh authoritative stored evidence.

This module never invokes preparation or any ordinary void planner.
"""
import sqlalchemy as sa
from bookflow.company import schema as c
from bookflow.company import transaction_deletion_facts as f
from bookflow.company.transaction_deletion_models import PreparedDelete, BlockedDelete
from bookflow.core.publication import OSBinding
from bookflow.core.ids import is_ulid


def validate_delete(s, ctx, prepared, *, binding=None):
    p = (BlockedDelete if isinstance(prepared,BlockedDelete) else PreparedDelete).model_validate(prepared.model_dump())
    with f.snapshot(s):
        from bookflow.hub.access import require_explicit_grant
        require_explicit_grant(s, 'transaction.' + p.intent.family + '.delete')
        binding = binding if binding is not None else OSBinding.from_session(s, ctx.on_behalf_of)
        fresh, identity = f.load(s, ctx, p.intent, binding)
        f.require(fresh == p.facts)
        f.require(identity == (p.actor_id, p.actor_kind, p.principal_id) and p.interface == str(ctx.interface))
        if isinstance(p,BlockedDelete):
            f.require(bool(fresh.blockers) and p.reason==f.normalized_reason(ctx))
            return p
        f.require(not fresh.blockers)
        h, b, t, provenance = fresh.header.values(), fresh.business_batch.values(), p.tombstone, p.provenance
        f.require((t.transaction_id,t.family,t.before_version,t.after_version,t.current_revision_id,t.number,
            t.status,t.deleted_from_status,t.deleted_at,t.deleted_by,t.delete_reason,t.delete_audit_event_id) ==
            (h['id'],p.intent.family,h['version'],h['version']+1,h['current_revision_id'],h['number'],
             'deleted',h['status'],provenance.at,identity[0],f.normalized_reason(ctx),provenance.event_id))
        rows = p.inverse_rows
        f.require(all(r.table in ('posting_batches','posting_lines','posting_line_sources') for r in rows))
        for r in rows: f.require(f.row(r.table,r.values()) == r)
        ids = [r.values()['id'] for r in rows] + [provenance.event_id,provenance.operation_id]
        f.require(len(set(ids)) == len(ids) and all(is_ulid(i) for i in ids))
        # Check IDs in their actual owner namespaces, including reserved event
        # and operation identities. No persisted Delete namespace exists yet.
        for table in (c.posting_batches,c.posting_lines,c.posting_line_sources,c.audit_events,
                      c.payment_operations,c.deposit_operations):
            for start in range(0,len(ids),200):
                f.require(s.company.conn.execute(sa.select(table.c.id).where(table.c.id.in_(ids[start:start+200]))).first() is None)
        for r in rows:
            v=r.values()
            f.require((v['transaction_id'],v['created_at'],v['created_by'],v['created_via']) ==
                (h['id'],provenance.at,identity[0],str(ctx.interface)))
        if h['status'] == 'voided':
            f.require(not rows and t.delete_posting_batch_id is None and t.retained_void_batch_id == h['void_posting_batch_id'])
        else:
            f.require(t.retained_void_batch_id is None)
            batch=f.one([r for r in rows if r.table=='posting_batches']).values()
            f.require((batch['id'],batch['revision_id'],batch['kind'],batch['effective_date'],batch['reverses_batch_id'],
                batch['replaces_batch_id'],batch['audit_event_id']) ==
                (t.delete_posting_batch_id,h['current_revision_id'],'reversal',b['effective_date'],b['id'],None,provenance.event_id))
            legs=[r for r in rows if r.table=='posting_lines']
            oldlegs=[r for r in fresh.rows if r.table=='posting_lines' and r.values()['batch_id']==b['id']]
            f.inverse_matches(oldlegs,legs)
            f.require(all(r.values()['batch_id']==batch['id'] for r in legs))
            links={r.values()['id']:r.values()['reversed_line_id'] for r in legs}
            oldsources=[r for r in fresh.rows if r.table=='posting_line_sources' and r.values()['posting_line_id'] in set(links.values())]
            sources=[r for r in rows if r.table=='posting_line_sources']
            f.inverse_matches(oldsources,sources)
            original={r.values()['id']:r.values() for r in oldsources}
            for r in sources:
                v=r.values()
                f.require(v['posting_line_id'] in links and links[v['posting_line_id']]==original[v['reversed_source_id']]['posting_line_id'])
        allocations={r.values()['id']:r.values() for r in fresh.rows if r.table=='work_billing_allocations'
            and r.values()['id'] in fresh.work_allocation_ids}
        f.require(len(p.work_releases)==len(allocations) and {r.allocation_id for r in p.work_releases}==set(allocations))
        for release in p.work_releases:
            old=allocations[release.allocation_id]
            f.require((release.root_document_id,release.root_line_id)==(old['root_document_id'],old['root_line_id']))
            current = old['revision_id'] == h['current_revision_id']
            f.require(release.newly_released == (current and h['status']=='posted'))
            if current and h['status']=='posted':
                f.require(release.event_id == provenance.event_id)
            else:
                cancellations = [r.values() for r in fresh.rows if r.table=='posting_batches'
                    and r.values()['transaction_id']==h['id'] and r.values()['revision_id']==old['revision_id']
                    and r.values()['kind']=='reversal']
                f.require(release.event_id == f.one(cancellations)['audit_event_id'])
    return p
