"""Company-scoped work consumption projections over immutable current sale revisions."""
import json
import sqlalchemy as sa
from bookflow.company import schema as c, work, sales
from bookflow.company.billing_outputs import BillingOutput
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units as quantity
from bookflow.hub.access import require_resource


def root_identities(s, header):
    return {row['id']: row for row in work.rows(s, c.work_line_identities,
        c.work_line_identities.c.document_id == header['id'])}


def active_allocations(s, roots, *, excluding=None):
    a, t = c.work_billing_allocations, c.transactions
    if not roots:
        return []
    query = sa.select(a, t.c.type.label('destination_type')).join(t,
        sa.and_(t.c.id == a.c.transaction_id, t.c.current_revision_id == a.c.revision_id,
                t.c.status == 'posted')).where(sa.tuple_(a.c.root_document_id, a.c.root_line_id).in_(roots))
    if excluding:
        query = query.where(t.c.id != excluding)
    return [dict(row) for row in s.company.conn.execute(query).mappings()]


def current_owner(s, header):
    if header['kind'] == 'estimate':
        links = work.rows(s, c.work_links, c.work_links.c.source_document_id == header['id'],
            c.work_links.c.relation == 'estimate_work_order')
        if links:
            return work.resolve(s, links[0]['destination_document_id'], 'work_order')
    return header


def revision_allocations(s, revision_id):
    return work.rows(s, c.work_billing_allocations,
        c.work_billing_allocations.c.revision_id == revision_id,
        order=c.work_billing_allocations.c.id)


def sale_source_output(s, revision_id, pending=None):
    values = revision_allocations(s, revision_id) if pending is None else pending
    if values:
        require_resource(s, 'customer-work', 'member')
    from bookflow.company.sales_outputs import BillingSourceOutput
    return [BillingSourceOutput(**dict(row, facts_snapshot=json.loads(row['facts_snapshot']))) for row in values]


def sale_source_links(s, revision_id):
    a = c.work_billing_allocations
    values = [dict(r) for r in s.company.conn.execute(sa.select(a.c.source_document_id,
        a.c.source_revision_id).where(a.c.revision_id == revision_id).distinct()
        .order_by(a.c.source_document_id, a.c.source_revision_id)).mappings()]
    if values:
        require_resource(s, 'customer-work', 'member')
    return values


def authorize_sale(inp, ctx, s, kind, write):
    selector = getattr(inp, kind, None)
    if not selector:
        return
    header = sales.resolve(s, selector, kind)
    a = c.work_billing_allocations
    # Any history remains protected, including a voided sale and cached receipts.
    if s.company.conn.execute(sa.select(a.c.id).where(a.c.transaction_id == header['id']).limit(1)).first():
        require_resource(s, 'customer-work', 'standard' if write else 'member')


def billing(s, ctx, inp, kind):
    from bookflow.company.query import page_state, continuation
    source = work.resolve(s, getattr(inp, kind), kind)
    owner = current_owner(s, source)
    rev = work.revision(s, owner)
    identities = root_identities(s, owner)
    lines = work.saved_lines(s, rev)
    roots = [(identities[line['line_id']]['root_document_id'], identities[line['line_id']]['root_line_id']) for line in lines]
    allocated = {(r['root_document_id'], r['root_line_id']): r for r in active_allocations(s, roots)}
    rendered = []
    for line, root in zip(lines, roots):
        lf = work.line_facts(line)
        used = allocated.get(root)
        state = 'billed' if used else 'nonbillable' if not lf.billable else 'no_charge' if lf.gross_minor_units == 0 else 'unbilled'
        remaining = state == 'unbilled'
        rendered.append(dict(line_id=line['line_id'], source_line_id=line['id'], root_document_id=root[0],
            root_line_id=root[1], item_id=lf.item_id, description=lf.description, billable=lf.billable, state=state,
            quantity=quantity(lf.quantity_microunits), completed_quantity=quantity(lf.completed_quantity_microunits),
            billed_quantity=quantity(used['quantity_microunits'] if used else 0),
            remaining_quantity=quantity(lf.quantity_microunits if remaining else 0),
            net_minor_units=lf.net_minor_units, tax_minor_units=lf.tax_minor_units, gross_minor_units=lf.gross_minor_units,
            billed_net_minor_units=used['net_minor_units'] if used else 0,
            billed_tax_minor_units=used['tax_minor_units'] if used else 0,
            remaining_net_minor_units=lf.net_minor_units if remaining else 0,
            remaining_tax_minor_units=lf.tax_minor_units if remaining else 0,
            destination_id=used['transaction_id'] if used else None,
            destination_type=used['destination_type'] if used else None))
    class Contract:
        cursor = inp.cursor
        query = None
        def model_dump(self, **kw):
            return inp.model_dump(**kw)
    state = page_state(s, kind + ' billing', Contract(), ctx.on_behalf_of)
    a, t, i = c.work_billing_allocations, c.transactions, c.work_line_identities
    owned = sa.exists(sa.select(i.c.id).where(i.c.document_id.in_([source['id'], owner['id']]),
        i.c.root_document_id == a.c.root_document_id, i.c.root_line_id == a.c.root_line_id))
    destination_ids = sa.select(a.c.transaction_id).where(owned).distinct()
    query = sa.select(t).where(t.c.id.in_(destination_ids)).order_by(t.c.created_at, t.c.id)
    found = [dict(r) for r in s.company.conn.execute(query.offset(state.offset).limit(inp.limit + 1)).mappings()]
    more, found = len(found) > inp.limit, found[:inp.limit]
    destinations = [sales.summary(h, sales.journals.revision(s, h), sales.profile_row(s, sales.journals.revision(s, h))) for h in found]
    from bookflow.core.money import Money
    for dest in destinations:
        due = dest['total_minor_units'] if dest['type'] == 'invoice' and dest['status'] == 'posted' else 0
        dest.update(amount_due_minor_units=due, amount_due=Money(due, dest['currency']).to_dict())
    eligible = owner['active'] and (owner['status'] == 'accepted' if owner['kind'] == 'estimate' else owner['status'] != 'cancelled')
    can_bill = eligible and any(line['state'] == 'unbilled' for line in rendered)
    warnings = []
    if source['id'] != owner['id']:
        warnings.append('This estimate has a work order; continue billing from that work order.')
    if any(line['state'] == 'no_charge' for line in rendered) and not any(line['state'] == 'unbilled' for line in rendered):
        warnings.append('No charge remains; zero-price lines were not invoiced.')
    return BillingOutput(source_id=source['id'], source_kind=kind, source_version=source['version'],
        source_revision_id=source['current_revision_id'], owner_id=owner['id'], owner_kind=owner['kind'],
        owner_version=owner['version'], currency=rev['currency'], lines=rendered, destinations=destinations,
        count=len(found), has_more=more, next_cursor=continuation(state, len(found), more), audit_watermark=state.sequence,
        can_invoice=bool(can_bill), can_sales_receipt=bool(can_bill),
        remaining_net_minor_units=sum(line['remaining_net_minor_units'] for line in rendered),
        remaining_tax_minor_units=sum(line['remaining_tax_minor_units'] for line in rendered), warnings=warnings)
