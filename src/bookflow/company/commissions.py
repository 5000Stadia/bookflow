"""Per-line recipient selection on invoices, sales receipts and POS sales; reports over captured immutable facts."""
import sqlalchemy as sa
from bookflow.company import schema as c, list_service
from bookflow.company.lists import get_list_definition
from bookflow.company.commission_models import (
    CommissionSnapshot, CapturedRecipient, RecipientOptionsOutput, CommissionAttributionOutput,
)
from bookflow.core.errors import BookflowError


def require_capability(s, capability, threshold='standard'):
    from bookflow.hub import access, capabilities
    access.require_resource(s, capability, threshold)
    capabilities.require_company(s, 'commission recipient-options', capability)


def enabled(s):
    return bool(s.company.conn.execute(sa.select(c.company_info.c.commission_attribution_enabled)).scalar_one())


def disabled():
    return BookflowError('E_FEATURE_DISABLED', message='Commission attribution is off; enable it with company update --commission-attribution-enabled true.',
        details={'setting': 'commission_attribution_enabled', 'enable_command': 'company update --commission-attribution-enabled true'})


def resolve(s, noun, table, selector):
    return list_service.resolve_selector(s.company, table, get_list_definition(noun), selector)


DOCUMENT_TYPES = ('invoice', 'sales_receipt')
# Selecting a recipient is a `member` act, so a readonly POS clerk can hold it by explicit grant
# (the "Clerk with commission attribution" preset); standard people hold it by default (commission-pos-v1).
ATTRIBUTE_THRESHOLD = 'member'


def recipient_options(s, inp):
    on = enabled(s)
    if inp.item is not None:
        on = on and bool(resolve(s, 'item', c.items, inp.item)['commission_enabled'])
    if not on:
        return RecipientOptionsOutput(commission_enabled=False, items=[])
    query = sa.select(c.sales_reps.c.id, c.sales_reps.c.name).where(c.sales_reps.c.active.is_(True))
    if inp.search:
        query = query.where(c.sales_reps.c.name.icontains(inp.search, autoescape=True))
    rows = [dict(row) for row in s.company.conn.execute(query.order_by(c.sales_reps.c.name_key, c.sales_reps.c.id).limit(inp.limit)).mappings()]
    suggested = None
    suggested_selector = inp.sales_rep
    if suggested_selector is None and inp.customer:
        from bookflow.company.parties import project_party_record
        customer = resolve(s, 'customer', c.customers, inp.customer)
        party = project_party_record(s.company, 'customer', customer, custom_values=())
        suggested_selector = party.get('effective_sales_rep_id')
    if suggested_selector:
        rep = resolve(s, 'sales-rep', c.sales_reps, suggested_selector)
        if rep['active']:
            suggested = rep['id']
    return RecipientOptionsOutput(commission_enabled=True, items=rows, suggested_recipient_id=suggested)


def capture(s, document_type, inputs, lines, prior, currency):
    """Resolve selection after final net is known, before fingerprints or persisted effects."""
    on = enabled(s)
    missing, missing_items = [], []
    for position, (inp, line) in enumerate(zip(inputs, lines), 1):
        facts = line['profile']
        selection = inp.commission
        previous = prior.get(line['line_id'])
        old = None
        same_item = False
        if previous:
            from bookflow.company.sales_facts import SalesLineProfile
            old_facts = SalesLineProfile.model_validate_json(previous['item_snapshot'])
            if old_facts.item.id == facts.item.id:
                same_item = True
                old = old_facts.commission
        if document_type not in DOCUMENT_TYPES:
            if selection is not None:
                raise BookflowError('E_VALIDATION', message='Commission attribution supports invoices and sales receipts (and so POS sales) only.', details={'field': 'lines.commission'})
            continue
        facts.commission = None
        item_enabled = bool(s.company.conn.execute(sa.select(c.items.c.commission_enabled).where(c.items.c.id == line['item_id'])).scalar_one())
        if selection is not None:
            require_capability(s, 'commission.attribute', ATTRIBUTE_THRESHOLD)
            if not on:
                raise disabled()
            if not item_enabled:
                raise BookflowError('E_VALIDATION', message='This item is not enabled for commission attribution.', details={'item_id': line['item_id']})
            rep = resolve(s, 'sales-rep', c.sales_reps, selection.recipients[0].recipient)
            if not rep['active']:
                raise BookflowError('E_INACTIVE_REFERENCE', details={'field': 'lines.commission', 'recipient_id': rep['id'], 'lookup_command': 'commission recipient-options'})
            chosen = [CapturedRecipient(recipient_id=rep['id'], recipient_label=rep['name'])]
        elif old is not None:
            # Historical corrections preserve attribution and labels; item/default changes do not rewrite them.
            chosen = old.recipients
        elif same_item:
            # Old unattributed lines remain byte-compatible until explicitly selected.
            facts.commission = None
            continue
        elif on and item_enabled:
            require_capability(s, 'commission.attribute', ATTRIBUTE_THRESHOLD)
            key = inp.line_id or getattr(inp, '_commission_key', None) or f'line_{position}'
            missing.append(key)
            missing_items.append(dict(line_key=key, item_id=line['item_id'], item=facts.item.label))
            continue
        else:
            facts.commission = None
            continue
        facts.commission = CommissionSnapshot(recipients=chosen, net_minor_units=line['net_minor_units'], currency=currency)
    if missing:
        raise BookflowError('E_COMMISSION_REQUIRED', message='Choose who gets each enabled line’s commission.',
            details={'line_keys': missing, 'lines': missing_items, 'lookup_command': 'commission recipient-options',
                     'pos_lookup_command': 'pos commission-options'})


def holds_read(s):
    """Whether this reader may see attribution amounts (and, from increment 3, rates and earnings)."""
    if getattr(s, 'company_row', None) is None or getattr(s, 'hub', None) is None:
        return True
    try:
        require_capability(s, 'commission.read')
    except BookflowError as exc:
        if exc.code != 'E_PERMISSION':
            raise
        return False
    return True


_SELLER_FACTS = ('enabled_at_entry', 'net_minor_units', 'currency')


def project_snapshot(s, item_snapshot, *, readable=None):
    """A captured line fact as this reader may see it. Without commission.read only the attribution facts remain: who
    was chosen, the share and the line's own final net (already printed on the line), never a rate, earning or payout
    field a later increment adds to the snapshot; an allow-list, so a field added later is hidden until listed."""
    captured = item_snapshot.get('commission') if isinstance(item_snapshot, dict) else None
    if not captured:
        return item_snapshot
    if readable is None:
        readable = holds_read(s)
    if readable:
        return item_snapshot
    kept = {key: captured[key] for key in _SELLER_FACTS if key in captured}
    kept['recipients'] = [{key: allocation[key] for key in ('recipient_id', 'recipient_label', 'share') if key in allocation}
                          for allocation in captured.get('recipients', [])]
    return dict(item_snapshot, commission=kept)


def labels(captured):
    """Recipient display names only: the projection a clerk may see on the till."""
    if not captured:
        return None
    recipients = captured.get('recipients', []) if isinstance(captured, dict) else captured.recipients
    return {'recipients': [{'recipient_id': r['recipient_id'] if isinstance(r, dict) else r.recipient_id,
                            'recipient_label': r['recipient_label'] if isinstance(r, dict) else r.recipient_label}
                           for r in recipients]}


def review_ticket(s, lines):
    """Revalidate a held cart's selections against today's recipients and item flags (design §3, hold/resume)."""
    from bookflow.company.commission_models import CommissionReview
    on = enabled(s)
    if not on and not any(line.get('commission') or line.get('member_commissions') for line in lines):
        return []
    found = []
    item_rows = {}

    def item_row(selector):
        if selector not in item_rows:
            try:
                item_rows[selector] = resolve(s, 'item', c.items, selector)
            except BookflowError:
                item_rows[selector] = None
        return item_rows[selector]

    def check(position, member, item, selection):
        if item is None:
            return
        if selection is not None and not item['commission_enabled']:
            found.append(CommissionReview(line=position, member=member, item_id=item['id'], problem='item_not_enabled'))
            return
        if selection is None:
            if on and item['commission_enabled']:
                found.append(CommissionReview(line=position, member=member, item_id=item['id'], problem='selection_required'))
            return
        try:
            rep = resolve(s, 'sales-rep', c.sales_reps, selection['recipients'][0]['recipient'])
        except BookflowError:
            rep = None
        if rep is None or not rep['active']:
            found.append(CommissionReview(line=position, member=member, item_id=item['id'], problem='recipient_inactive',
                recipient_id=rep['id'] if rep else None, recipient_label=rep['name'] if rep else None))

    for position, line in enumerate(lines, 1):
        item = item_row(line['item'])
        if item is not None and item['type'] == 'group':
            chosen = {entry['member']: entry['commission'] for entry in line.get('member_commissions') or ()}
            members = s.company.conn.execute(sa.select(c.item_members.c.component_item_id).where(
                c.item_members.c.owner_item_id == item['id'], c.item_members.c.active.is_(True)).order_by(
                c.item_members.c.position, c.item_members.c.id)).scalars().all()
            for member, component in enumerate(members, 1):
                check(position, member, item_row(component), chosen.get(member))
        else:
            check(position, None, item, line.get('commission'))
    return found


def check_turning_off(s):
    """Attribution goes off only when no held or open POS ticket still carries a pending selection (design §2)."""
    rows = s.company.conn.execute(sa.select(c.pos_tickets.c.id, c.pos_tickets.c.label, c.pos_tickets.c.lines).where(
        c.pos_tickets.c.status.in_(('held', 'open'))).order_by(c.pos_tickets.c.updated_at, c.pos_tickets.c.id)).mappings().all()
    import json
    pending = [dict(id=row['id'], label=row['label']) for row in rows
               if any(line.get('commission') or line.get('member_commissions') for line in json.loads(row['lines']))]
    if pending:
        labels = ', '.join(f"{row['label']} ({row['id']})" for row in pending)
        raise BookflowError('E_WORK_DEPENDENCY', message=(
            f'Held tickets still carry commission selections: {labels}. Complete or discard them before turning commission attribution off.'),
            details={'tickets': pending, 'ticket': pending[0]['id'], 'label': pending[0]['label'],
                     'resume_command': 'pos ticket resume', 'discard_command': 'pos ticket discard',
                     'reason': 'commission_selection_pending'})


def attribution(s, inp):
    """Current posted invoice and sales receipt revisions (POS sales included): voided/deleted ones never count."""
    from bookflow.company.sales_facts import SalesLineProfile
    t, r, l, p, h = c.transactions, c.transaction_revisions, c.document_lines, c.sales_line_profiles, c.sales_profiles
    query = sa.select(t.c.id.label('invoice_id'), t.c.type.label('document_type'), t.c.number, r.c.date, l.c.line_id, l.c.position, p.c.item_id, p.c.item_snapshot).select_from(
        t.join(r, r.c.id == t.c.current_revision_id).join(l, l.c.revision_id == r.c.id).join(p, p.c.document_line_id == l.c.id).join(h, h.c.revision_id == r.c.id)
    ).where(t.c.type.in_(DOCUMENT_TYPES), t.c.status == 'posted',
        ~sa.exists(sa.select(c.sales_deletions.c.transaction_id).where(c.sales_deletions.c.transaction_id == t.c.id)))
    if inp.date_from:
        query = query.where(r.c.date >= inp.date_from)
    if inp.date_to:
        query = query.where(r.c.date <= inp.date_to)
    if inp.customer:
        customer = resolve(s, 'customer', c.customers, inp.customer)
        query = query.where(h.c.customer_id == customer['id'])
    if inp.item:
        item = resolve(s, 'item', c.items, inp.item)
        query = query.where(p.c.item_id == item['id'])
    recipient_id = resolve(s, 'sales-rep', c.sales_reps, inp.recipient)['id'] if inp.recipient else None
    rows, totals = [], {}
    for row in s.company.conn.execute(query.order_by(r.c.date, t.c.id, l.c.position)).mappings():
        facts = SalesLineProfile.model_validate_json(row['item_snapshot'])
        captured = facts.commission
        if captured is None:
            continue
        for rep in captured.recipients:
            if recipient_id and recipient_id != rep.recipient_id:
                continue
            values = dict(recipient_id=rep.recipient_id, recipient_name=rep.recipient_label,
                net_minor_units=captured.net_minor_units, currency=captured.currency)
            rows.append(dict({k: row[k] for k in ('invoice_id', 'document_type', 'number', 'date', 'line_id', 'position', 'item_id')}, item_name=facts.item.label, **values))
            key = rep.recipient_id, captured.currency
            if key not in totals:
                totals[key] = dict(values, net_minor_units=0)
            totals[key]['net_minor_units'] += captured.net_minor_units
    return CommissionAttributionOutput(rows=rows, totals=list(totals.values()))
