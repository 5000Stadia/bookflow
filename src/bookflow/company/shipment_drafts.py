"""Draft shipments and packing commands (R181 increment 4; design/pick-pack-ship.md §5).

`shipment create` writes a draft from an order (what `shipment post` would propose), explicit lines or a pick list;
`shipment pack` records boxes on a draft or a shipped shipment (a revision that posts nothing); `shipment cancel`
ends a draft. All three are the packing layer: refused with E_FEATURE_DISABLED while the company switch is off.
"""
from __future__ import annotations

import json

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company import shipment_packing as packing
from bookflow.company import shipments as sh
from bookflow.company.shipment_models import ShipmentHistoryOutput, ShipmentLineOutput, ShipmentOutput
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched


def _invalid(field_name, problem, message=None, **details):
    return BookflowError('E_VALIDATION', message=message,
                         details={'fields': [{'field': field_name, 'problem': problem}], **details})


def _version(draft, expected):
    if expected is not None and expected != draft['revision_number']:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_id': draft['id'], 'expected_version': expected,
                                                          'current_version': draft['revision_number']})


# ---------------------------------------------------------------- output


def draft_output(s, draft, *, warnings=(), boxes=None, status=None):
    from bookflow.company import bins, placements, work
    order = work.resolve(s, draft['sales_order_id'], 'sales_order')
    rev = work.revision(s, order)
    currency = rev['currency']
    zero = sh._money(0, currency)
    rows = packing.draft_lines(s, draft['id'])
    names = dict(s.company.conn.execute(sa.select(c.items.c.id, c.items.c.full_name).where(
        c.items.c.id.in_([row['item_id'] for row in rows] or ['']))).all())
    bin_names = bins.bin_names(s, [row['bin_id'] for row in rows if row['bin_id']])
    lines = [ShipmentLineOutput(
        line_identity=row['line_identity'], position=row['position'], order_line_id=row['root_line_id'],
        item_id=row['item_id'], item_name=names.get(row['item_id'], row['item_id']),
        quantity=sh._plain(row['quantity_microunits']), bin=bin_names.get(row['bin_id']), lot=row['lot'],
        serial_numbers=json.loads(row['serial_numbers']) if row['serial_numbers'] else None, cost=zero,
        invoiced='0', not_invoiced=sh._plain(row['quantity_microunits']), waiting=zero) for row in rows]
    after = {row['line']['line_id']: sh._plain(row['to_ship']) for row in sh._order_lines(s, order, rev)}
    history = [ShipmentHistoryOutput(version=h['revision_number'], status=h['status'], date=h['ship_date'] or '',
                                     reason=h['reason'], created_at=h['created_at'], created_by=h['created_by'],
                                     created_via=h['created_via']) for h in packing.draft_history(s, draft['id'])]
    customer = s.company.conn.execute(sa.select(c.customers.c.full_name).where(
        c.customers.c.id == draft['customer_id'])).scalar_one_or_none()
    boxes = packing.load(s, draft['revision_id']) if boxes is None else boxes
    shown = status or draft['status']
    next_commands = []
    if shown == 'draft':
        next_commands = [f'bookflow shipment pack {draft["number"]} --expected-version {draft["revision_number"]} '
                         '--unit <code> --box 1 --json',
                         f'bookflow shipment post --shipment {draft["number"]} --expected-version '
                         f'{draft["revision_number"]} --json']
    facts = dict(freight_minor_units=draft['freight_minor_units'], freight_item_id=draft['freight_item_id'])
    return ShipmentOutput(
        id=draft['id'], number=draft['number'], version=draft['revision_number'], status=shown,
        sales_order_id=order['id'], sales_order_number=order['number'], customer_id=draft['customer_id'],
        customer_name=customer, date=draft['ship_date'] or '', count_position=draft['count_position'],
        site_id=draft['site_id'], site_name=placements.site_names(s).get(draft['site_id']),
        ship_method_id=draft['ship_method_id'], carrier=draft['carrier'],
        tracking_numbers=json.loads(draft['tracking_numbers']), memo=draft['memo'], lines=lines, cost=zero,
        shipped_not_invoiced=zero, order_to_ship=after, history=history,
        packing=packing.output(s, boxes, packing.lines_of_draft(s, draft['id']), show_empty=True),
        freight=sh._freight_output(s, None, facts, currency), warnings=list(dict.fromkeys(warnings)),
        next_commands=next_commands)


def show(s, selector):
    draft = packing.find_draft(s, selector)
    if draft is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'shipment', 'selector': str(selector)})
    return draft_output(s, draft)


# ---------------------------------------------------------------- create


def _write(s, ctx, command_name, summary, inserts):
    """Audit, then insert ``[(table, kind, rows)]``; returns touched."""
    touched = [Touched(kind, row['id'], 'create', None, 1, dict(row), None, db='company')
               for _, kind, rows in inserts for row in rows]
    audit.write_event_to(s.company, ctx, command_name, summary, touched, actor_id=s.actor.id,
                         actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None),
                         event_id=inserts[0][2][0]['audit_event_id'])
    for table, _, rows in inserts:
        if rows:
            s.company.conn.execute(getattr(c, table).insert(), rows)
    return touched


def _provenance(s, ctx):
    return dict(created_at=clock.now_iso(), created_by=s.actor.id, created_via=ctx.interface.value,
                audit_event_id=new_id())


def _create(s, ctx, inp, *, persist):
    packing.require_enabled(s)
    from bookflow.company import bins, pick_lists
    picked = pick_current = None
    if inp.pick_list is not None:
        if inp.lines is not None:
            raise _invalid('lines', 'draft from the pick list or name the lines, not both')
        pick_current, order_id, picked = pick_lists.shipping_plan(s, inp.pick_list, inp.sales_order, inp.site)
    elif inp.sales_order is None:
        raise _invalid('sales_order', 'name the sales order to draft (or the pick_list it was picked on)')
    header, rev = sh._order(s, order_id if picked is not None else inp.sales_order)
    if header['status'] != 'open' or not header['active']:
        raise BookflowError('E_WORK_DEPENDENCY', details=dict(source_id=header['id'],
            problem='a closed or inactive sales order ships nothing; reopen it first'))
    site_id = pick_current['site_id'] if pick_current else sh._site(s, inp.site, sh._order_lines(s, header, rev))
    position = inp.count_position or 'before'
    date = inp.date or clock.today_iso()
    planned = sh._plan(s, ctx, header=header, rev=rev, date=date, site_id=site_id, position=position,
                       entries_in=inp.lines, sni_id=sh._PLACEHOLDER, picked=picked, draft=True,
                       held=packing.drafted(s, header['id']))
    if picked is None:
        sh._guard_picked(s, header, planned, [])
    facts = dict(ship_method_id=sh._ship_method(s, inp.ship_method, rev), carrier=inp.carrier,
                 tracking_numbers=json.dumps(list(inp.tracking_numbers or [])), memo=inp.memo)
    facts.update(sh._freight_facts(s, inp, rev, None, None, 'post'))
    pack_lines = packing.planned_lines(s, planned.lines)
    boxes = packing.from_packages(s, inp.packages, pack_lines) if inp.packages is not None else []
    draft = dict(id=None, number=None, sales_order_id=header['id'], customer_id=planned.customer_id, site_id=site_id,
                 pick_list_id=pick_current['id'] if pick_current else None, revision_id=None, revision_number=0,
                 status='draft', ship_date=inp.date, count_position=position, **facts)
    if not persist:
        out = _preview(s, draft, planned, boxes, pack_lines, rev)
        return out, [], 'preview'
    provenance = _provenance(s, ctx)
    number, sequence = sh._number(s)
    draft.update(id=new_id(), number=number)
    head = dict(id=draft['id'], number=number, sales_order_id=header['id'], customer_id=planned.customer_id,
                site_id=site_id, pick_list_id=draft['pick_list_id'], **provenance)
    revision = dict(id=new_id(), draft_id=draft['id'], revision_number=1, status='draft', ship_date=inp.date,
                    count_position=position, reason=(ctx.reason or '').strip() or None, **facts, **provenance)
    lines = []
    for position_, line in enumerate(planned.lines, 1):
        numbers = line.numbers
        # The bin a line named; none named, the post places the units as a post from the order would.
        bin_id = bins.line_bin(s, line.facts.item_id, site_id, numbers.bin) \
            if numbers and numbers.bin and not bins.is_no_bin(numbers.bin) else None
        line.identity = new_id()
        lines.append(dict(id=new_id(), draft_id=draft['id'], line_identity=line.identity, position=position_,
                          root_document_id=line.root[0], root_line_id=line.root[1], item_id=line.facts.item_id,
                          quantity_microunits=line.base, bin_id=bin_id,
                          lot=numbers.lot if numbers and numbers.lot else None,
                          serial_numbers=json.dumps(list(numbers.serial_numbers))
                          if numbers and numbers.serial_numbers else None,
                          description=line.facts.description, **provenance))
    inserts = [('shipment_drafts', 'shipment_draft', [head]), ('shipment_draft_revisions', 'shipment_draft_revision',
                                                                 [revision]),
               ('shipment_draft_lines', 'shipment_draft_line', lines)]
    for table, kind, _, rows in packing.rows(boxes, draft['id'], revision['id'], provenance, identities={
            f'#{row["position"]}': row['line_identity'] for row in lines}):
        inserts.append((table, kind, rows))
    touched = _write(s, ctx, 'shipment create', f'create draft shipment {number}', inserts)
    from sqlalchemy.dialects.sqlite import insert
    stmt = insert(c.sequences).values(**sequence)
    s.company.conn.execute(stmt.on_conflict_do_update(index_elements=['name'], set_=sequence))
    return draft_output(s, packing.find_draft(s, draft['id']), warnings=planned.warnings), touched, \
        f'create draft shipment {number}'


def _preview(s, draft, planned, boxes, pack_lines, rev):
    """What `shipment create` would write, before it has a number."""
    from bookflow.company import placements, work
    currency = rev['currency']
    zero = sh._money(0, currency)
    lines = [ShipmentLineOutput(
        line_identity='', position=position, order_line_id=line.order_line['line_id'], item_id=line.facts.item_id,
        item_name=line.facts.profile.item.label, quantity=sh._plain(line.base),
        bin=line.numbers.bin if line.numbers else None, lot=line.numbers.lot if line.numbers else None,
        serial_numbers=list(line.numbers.serial_numbers) if line.numbers and line.numbers.serial_numbers else None,
        cost=zero, invoiced='0', not_invoiced=sh._plain(line.base), waiting=zero)
        for position, line in enumerate(planned.lines, 1)]
    order = planned.order
    return ShipmentOutput(
        id=None, number=None, version=None, status='will_draft', sales_order_id=order['id'],
        sales_order_number=order['number'], customer_id=planned.customer_id, customer_name=planned.customer_name,
        date=draft['ship_date'] or '', count_position=draft['count_position'], site_id=draft['site_id'],
        site_name=placements.site_names(s).get(draft['site_id']), ship_method_id=draft['ship_method_id'],
        carrier=draft['carrier'], tracking_numbers=json.loads(draft['tracking_numbers']), memo=draft['memo'],
        lines=lines, cost=zero, shipped_not_invoiced=zero,
        packing=packing.output(s, boxes, pack_lines, show_empty=True),
        freight=sh._freight_output(s, None, draft, currency), warnings=list(dict.fromkeys(planned.warnings)))


# ---------------------------------------------------------------- cancel


def _cancel(s, ctx, inp, *, persist):
    packing.require_enabled(s)
    draft = packing.find_draft(s, inp.shipment)
    if draft is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'draft shipment', 'selector': inp.shipment})
    _version(draft, inp.expected_version)
    if draft['status'] != 'draft':
        raise _invalid('shipment', f'draft {draft["number"]} is {draft["status"]}; only a draft is cancelled')
    if not persist:
        return draft_output(s, draft, status='will_cancel', boxes=[]), [], 'preview'
    provenance = _provenance(s, ctx)
    row = sh._draft_revision(draft, 'cancelled', ctx, provenance)
    touched = _write(s, ctx, 'shipment cancel', f'cancel draft shipment {draft["number"]}',
                     [('shipment_draft_revisions', 'shipment_draft_revision', [row])])
    return draft_output(s, packing.find_draft(s, draft['id'])), touched, f'cancel draft shipment {draft["number"]}'


# ---------------------------------------------------------------- pack


def _facts_of(rev):
    return {name: rev[name] for name in ('ship_method_id', 'carrier', 'tracking_numbers', 'memo',
                                         'freight_minor_units', 'freight_item_id')}


def _pack(s, ctx, inp, *, persist):
    packing.require_enabled(s)
    try:
        current = sh.resolve(s, inp.shipment)
    except BookflowError as error:
        if error.code != 'E_RECORD_NOT_FOUND':
            raise
        current = None
    if current is not None:
        rev = current['revision']
        if inp.expected_version is not None and inp.expected_version != rev['revision_number']:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_id': current['id'],
                                                              'expected_version': inp.expected_version,
                                                              'current_version': rev['revision_number']})
        if rev['status'] != 'shipped':
            raise _invalid('shipment', f'shipment {current["number"]} is {rev["status"]}; it packs nothing')
        lines = packing.shipment_lines(s, rev['id'])
        boxes = packing.load(s, rev['id'])
    else:
        draft = packing.find_draft(s, inp.shipment)
        if draft is None:
            raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'shipment', 'selector': inp.shipment})
        _version(draft, inp.expected_version)
        if draft['status'] != 'draft':
            raise _invalid('shipment', f'draft {draft["number"]} is {draft["status"]}; it packs nothing')
        lines = packing.lines_of_draft(s, draft['id'])
        boxes = packing.load(s, draft['revision_id'])
    if inp.packages is not None:
        if inp.unit is not None or inp.line is not None or inp.box is not None:
            raise _invalid('packages', 'give the whole packing, or move units with unit or line and box, not both')
        fresh = packing.from_packages(s, inp.packages, lines)
    else:
        fresh = packing.move(s, boxes, lines, inp)
    if current is not None:
        if not persist:
            shown = sh.show_output(s, current['id'])
            return shown.model_copy(update=dict(status='will_pack', packing=packing.output(
                s, fresh, lines, show_empty=True))), [], 'preview'
        touched, summary = sh.write_revision(s, ctx, current, sh._revision_lines(s, rev['id']), _facts_of(rev),
                                             'shipment pack', fresh)
        return sh.show_output(s, current['id']), touched, summary
    if not persist:
        return draft_output(s, draft, status='will_pack', boxes=fresh), [], 'preview'
    provenance = _provenance(s, ctx)
    row = sh._draft_revision(draft, 'draft', ctx, provenance)
    inserts = [('shipment_draft_revisions', 'shipment_draft_revision', [row])]
    for table, kind, _, rows in packing.rows(fresh, draft['id'], row['id'], provenance):
        inserts.append((table, kind, rows))
    summary = f'pack draft shipment {draft["number"]}'
    touched = _write(s, ctx, 'shipment pack', summary, inserts)
    return draft_output(s, packing.find_draft(s, draft['id'])), touched, summary


_OPERATIONS = {'create': _create, 'cancel': _cancel, 'pack': _pack}


def prepare(s, ctx, inp, operation):
    output, _, _ = _OPERATIONS[operation](s, ctx, inp, persist=False)
    return Plan(output, {'input': inp, 'operation': operation})


def apply(plan, ctx, s):
    output, touched, summary = _OPERATIONS[plan.data['operation']](s, ctx, plan.data['input'], persist=True)
    return Applied(output, touched, summary, audited=True)
