"""Read retained refund revisions and effects inside the command's read snapshot."""
import sqlalchemy as sa

from bookflow.company import document_effects as effects, info, refunds, schema as c
from bookflow.company.query import continuation, page_state
from bookflow.company.refund_models import (
    CustomerRefundHistoryEventOutput, CustomerRefundHistoryOutput,
    CustomerRefundHistoryRevisionOutput,
)
from bookflow.core.errors import BookflowError


def _events(s, identifiers):
    rows = effects.rows(s, c.audit_events, c.audit_events.c.id.in_(identifiers),
                        order=c.audit_events.c.seq)
    if {row['id'] for row in rows} != identifiers:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'audit_event'})
    names = info.principal_names(s.company, {row[key] for row in rows
        for key in ('actor_id', 'on_behalf_of') if row[key]})
    return [CustomerRefundHistoryEventOutput(
        **{key: row[key] for key in CustomerRefundHistoryEventOutput.model_fields
           if key not in ('actor_name', 'on_behalf_of_name')},
        actor_name=names.get(row['actor_id']), on_behalf_of_name=names.get(row['on_behalf_of']))
        for row in rows]


def history(s, ctx, inp):
    class Contract:
        cursor = inp.cursor
        query = None

        def model_dump(self, **kw):
            return inp.model_dump(**kw)

    state = page_state(s, 'customer-refund history', Contract(), ctx.on_behalf_of)
    header = refunds.resolve(s, inp.refund)
    query = sa.select(c.transaction_revisions).where(
        c.transaction_revisions.c.transaction_id == header['id']).order_by(
        c.transaction_revisions.c.revision_number)
    found = [dict(row) for row in s.company.conn.execute(
        query.offset(state.offset).limit(inp.limit + 1)).mappings()]
    more, found = len(found) > inp.limit, found[:inp.limit]
    items = []
    for revision in found:
        shown = refunds.revision_output(s, header, revision, refunds.profile_row(s, revision))
        # Releases retain the consumed revision ID, including a later correction or void.
        rows = effects.rows(s, c.customer_refund_consumptions,
            c.customer_refund_consumptions.c.revision_id == revision['id'],
            order=c.customer_refund_consumptions.c.id)
        event_ids = {revision['audit_event_id'], *(batch.audit_event_id for batch in shown.batches),
                     *(row['audit_event_id'] for row in rows)}
        items.append(CustomerRefundHistoryRevisionOutput(**shown.model_dump(),
            consumptions=refunds._consumption_output(s, rows, refunds._source_labels(s, rows)),
            events=_events(s, event_ids)))
    return CustomerRefundHistoryOutput(
        **{key: header[key] for key in ('id', 'version', 'current_revision_id', 'number', 'status',
            'voided_at', 'voided_by', 'void_reason', 'void_posting_batch_id')},
        items=items, count=len(items), has_more=more,
        next_cursor=continuation(state, len(items), more), audit_watermark=state.sequence)
