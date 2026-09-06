"""Company-scoped work consumption projections over immutable current sale revisions."""
import json
from datetime import datetime, timezone
from fractions import Fraction
import sqlalchemy as sa
from bookflow.company import schema as c, work, sales
from bookflow.company.billing_outputs import BillingOutput
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units as quantity
from bookflow.hub.access import require_resource


def root_identities(s, header):
    current = sa.select(c.work_lines.c.line_id).where(c.work_lines.c.document_id == header['id'],
        c.work_lines.c.revision_id == header['current_revision_id'])
    return {row['id']: row for row in work.rows(s, c.work_line_identities,
        c.work_line_identities.c.document_id == header['id'], c.work_line_identities.c.id.in_(current))}


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
    from bookflow.company.billing_allocations import source_output
    return [BillingSourceOutput(**source_output(row)) for row in values]


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


def latest_consumption_changes(s, roots):
    """Latest real allocation/release events, including roots absent after removal."""
    a, r, t, p, e = (c.work_billing_allocations, c.transaction_revisions,
                     c.transactions, c.posting_batches, c.audit_events)
    newer, carried = r.alias('newer'), a.alias('carried')
    selected = lambda: sa.tuple_(a.c.root_document_id, a.c.root_line_id).in_(roots)
    keys = (a.c.root_document_id, a.c.root_line_id, a.c.transaction_id)
    created = sa.select(*keys, r.c.audit_event_id).join(r, r.c.id == a.c.revision_id).where(selected())
    removed = sa.select(*keys, newer.c.audit_event_id).join(newer,
        newer.c.supersedes_revision_id == a.c.revision_id).where(selected(), ~sa.exists(sa.select(carried.c.id).where(
            carried.c.revision_id == newer.c.id, carried.c.root_document_id == a.c.root_document_id,
            carried.c.root_line_id == a.c.root_line_id)))
    voided = sa.select(*keys, p.c.audit_event_id).join(t, sa.and_(t.c.id == a.c.transaction_id,
        t.c.current_revision_id == a.c.revision_id, t.c.status == 'voided')).join(p,
        p.c.id == t.c.void_posting_batch_id).where(selected())
    events = sa.union_all(created, removed, voided).subquery()
    ranked = sa.select(events, e.c.actor_id, e.c.interface, e.c.at, e.c.command,
        sa.func.row_number().over(partition_by=(events.c.root_document_id, events.c.root_line_id),
                                  order_by=e.c.seq.desc()).label('rank')).join(e,
                                      e.c.id == events.c.audit_event_id).subquery()
    now = datetime.now(timezone.utc)
    rows = s.company.conn.execute(sa.select(ranked).where(ranked.c.rank == 1).limit(200)).mappings()
    return [dict(root_document_id=row['root_document_id'], root_line_id=row['root_line_id'],
        transaction_id=row['transaction_id'], audit_event_id=row['audit_event_id'],
        updated_by=row['actor_id'], updated_via=row['interface'],
        seconds_since_update=max(0, int((now-datetime.fromisoformat(row['at'])).total_seconds())),
        changed_fields=['billing_consumption', 'status' if row['command'].endswith(' void') else 'allocation_revision'])
        for row in rows]


def billing(s, ctx, inp, kind):
    from bookflow.company import work_preferences as policy
    from bookflow.company.query import page_state, continuation
    source = work.resolve(s, getattr(inp, kind), kind)
    owner = current_owner(s, source)
    rev = work.revision(s, owner)
    identities = root_identities(s, owner)
    lines = work.saved_lines(s, rev)
    roots = [(identities[line['line_id']]['root_document_id'], identities[line['line_id']]['root_line_id']) for line in lines]
    from bookflow.company import billing_allocations as alloc, billing_math as math, sales_calculations as calc
    from bookflow.company.billing_facts import ExactFraction
    rendered = []
    for line, root in zip(lines, roots):
        lf = work.line_facts(line)
        used = alloc.active_totals(s, root)
        d = math.denominator(lf.quantity_microunits, lf.net_minor_units)
        free_length, free_net = alloc.remaining(s, root, lf)
        billed_quantity = Fraction(lf.quantity_microunits*(d-free_length),d*1_000_000)
        remaining_quantity = Fraction(lf.quantity_microunits*free_length,d*1_000_000)
        percent = Fraction(100*(d-free_length),d)
        state = ('billed' if not free_length else 'partially_billed' if used['count'] else
                 'nonbillable' if not lf.billable else 'no_charge' if free_net == 0 else 'unbilled')
        remaining_net = free_net if lf.billable else 0
        remaining_tax = sum(calc.tax(remaining_net,t.rule.rate_percent_millionths) for t in lf.taxes)
        recovery, recommendation = policy.recovery(s, root, lf, rev['currency'])
        rendered.append(dict(line_id=line['line_id'], source_line_id=line['id'], root_document_id=root[0],
            requires_bounded_recovery=recovery, recommended_net_amount=recommendation,
            root_line_id=root[1], item_id=lf.item_id, description=lf.description, billable=lf.billable, state=state,
            quantity=quantity(lf.quantity_microunits), completed_quantity=quantity(lf.completed_quantity_microunits),
            billed_quantity=math.format_fraction(billed_quantity),
            remaining_quantity=math.format_fraction(remaining_quantity),
            billed_quantity_fraction=ExactFraction.of(billed_quantity),
            remaining_quantity_fraction=ExactFraction.of(remaining_quantity),
            billed_scope_percent_fraction=ExactFraction.of(percent), billed_scope_percent=math.format_fraction(percent),
            net_minor_units=lf.net_minor_units, tax_minor_units=lf.tax_minor_units, gross_minor_units=lf.gross_minor_units,
            billed_net_minor_units=used['net'], billed_tax_minor_units=used['tax'],
            remaining_net_minor_units=remaining_net, remaining_tax_minor_units=remaining_tax,
            destination_id=used['destination_id'], destination_type=used['destination_type']))
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
        from bookflow.company.payment_queries import invoice_current
        due = invoice_current(s, dest['id'])['due_minor_units'] if dest['type'] == 'invoice' else 0
        dest.update(amount_due_minor_units=due, amount_due=Money(due, dest['currency']).to_dict())
    eligible = owner['active'] and (owner['status'] == 'accepted' if owner['kind'] == 'estimate' else owner['status'] != 'cancelled')
    can_bill = eligible and any(line['billable'] and line['remaining_net_minor_units'] > 0 for line in rendered)
    warnings = []
    if not owner['active'] and any(line['remaining_net_minor_units'] > 0 for line in rendered):
        warnings.append('Released or remaining work is available, but the inactive source must be explicitly reactivated before rebilling.')
    if source['id'] != owner['id']:
        warnings.append('This estimate has a work order; continue billing from that work order.')
    if any(line['billed_tax_minor_units'] + line['remaining_tax_minor_units'] != line['tax_minor_units']
           for line in rendered if line['billable']):
        warnings.append('Tax is calculated on each bill; actual installment tax can differ from quoted tax by rounding.')
    if any(line['remaining_net_minor_units'] == 0 and line['remaining_quantity'] != '0' for line in rendered if line['billable']):
        warnings.append('Unallocated physical scope remains without a charge. Billing does not establish physical completion.')
    if any(line['state'] == 'no_charge' for line in rendered) and not any(line['remaining_net_minor_units'] > 0 for line in rendered):
        warnings.append('No charge remains; zero-price lines were not invoiced.')
    return BillingOutput(source_id=source['id'], source_kind=kind, source_version=source['version'],
        preferences=policy.preferences(s), closes_on_remaining_bill=bool(can_bill and source['id'] == owner['id']
            and kind == 'estimate' and policy.preferences(s).auto_close_effective),
        source_revision_id=source['current_revision_id'], owner_id=owner['id'], owner_kind=owner['kind'],
        owner_version=owner['version'], currency=rev['currency'], lines=rendered, destinations=destinations,
        count=len(found), has_more=more, next_cursor=continuation(state, len(found), more), audit_watermark=state.sequence,
        can_invoice=bool(can_bill), can_sales_receipt=bool(can_bill),
        remaining_net_minor_units=sum(line['remaining_net_minor_units'] for line in rendered),
        remaining_tax_minor_units=sum(line['remaining_tax_minor_units'] for line in rendered), warnings=warnings)
