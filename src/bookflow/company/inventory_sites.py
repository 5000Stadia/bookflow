"""Inventory sites and inventory transfers (R104 increment 1, design/advanced-inventory.md §2).

**Sites** are the places a company keeps its own stock: a warehouse, a van, a consignment location.
Every company has one from co0089, "Main", the default; renaming it is an ordinary update. Exactly
one active site is the default, where a stock line naming no site is placed. A site holding any
quantity today cannot be deactivated (E_RECORD_IN_USE names the items), nor can the default.

**Transfers** move units between sites, as the anchor's Transfer Inventory between locations does.
A transfer writes placements only -- the line's quantity out of the from site and into the to site,
on its date -- and nothing else: no journal entry, no movement, no value. Cost is one weighted
average per item, company-wide, so a transfer cannot change it, and the inventory asset and the
general ledger are untouched. An update replaces the document under its expected version (the old
revision's placements are reversed and the new ones written in one transaction); a void, with a
reason, reverses them and is final. A transfer dated on or before the closing date, or an update or
void reaching such a date, is refused. Taking the from site below zero is saved with a warning that
names where else the item is (design question 5).
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bookflow.company import inventory, items as item_service, journals, placements, schema as c
from bookflow.company.inventory_site_models import (
    SiteOutput, SitePageOutput, SiteStockRow, SiteWriteOutput, TransferLineOutput, TransferOutput,
    TransferPageOutput, TransferSiteStock, TransferWriteOutput)
from bookflow.company.lists import normalize_display_name
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units, parse_quantity_micro_units
from bookflow.core.ids import is_ulid, new_id, normalize_ulid
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

SEQUENCE = 'inventory_transfer'


def _invalid(field: str, problem: str, **details) -> BookflowError:
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}], **details})


def _row(s, table, *where) -> dict[str, Any] | None:
    found = s.company.conn.execute(sa.select(table).where(*where)).mappings().first()
    return dict(found) if found else None


def _today(s) -> str:
    from bookflow.company.memorized_schedule import today
    return today(getattr(s, 'company_tz', None))


def _version_check(record_type: str, row: dict[str, Any], expected: int | None) -> None:
    if expected is not None and expected != row['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': record_type, 'record_id': row['id'], 'current_version': row['version'],
            'expected_version': expected, 'updated_at': row['updated_at'], 'updated_by': row['updated_by']})


def _bumped(row: dict[str, Any], s, ctx) -> dict[str, Any]:
    return dict(row, version=row['version'] + 1, updated_at=clock.now_iso(), updated_by=s.actor.id,
                updated_via=ctx.interface.value)


def _q(units: int) -> str:
    return format_quantity_micro_units(int(units))


# ---------------------------------------------------------------------------- sites

def _site_name(s, name: str, own: str | None = None) -> tuple[str, str]:
    display, key = normalize_display_name(name, field='name')
    query = sa.select(c.inventory_sites.c.id).where(c.inventory_sites.c.name_key == key)
    if own:
        query = query.where(c.inventory_sites.c.id != own)
    clash = s.company.conn.execute(query).first()
    if clash:
        raise BookflowError('E_NAME_TAKEN', details={'record_type': 'inventory_site', 'name': display, 'record_id': clash[0]})
    return display, key


def _held_today(s, site_id):
    """Items with a nonzero quantity at the site on any date up to the latest placement (today's stock)."""
    return {item: quantity for (item, _), quantity in placements.on_hand(s, site_id=site_id).items()}


def site_output(s, row, *, cls=SiteOutput, detail=False, as_of=None, **extra):
    held = placements.on_hand(s, as_of, site_id=row['id']) if detail or as_of else None
    held = {item: q for (item, _), q in (held if held is not None else placements.on_hand(s, site_id=row['id'])).items()}
    stock, transfers = [], []
    if detail:
        stock = sorted((SiteStockRow(item_id=item, item_name=inventory.item_name(s, item), quantity_on_hand=_q(q))
                        for item, q in held.items()), key=lambda r: r.item_name.casefold())
        transfers = [dict(number=t.number, date=t.date, status=t.status, from_site=t.from_site_name,
                          to_site=t.to_site_name, lines=len(t.lines))
                     for t in query_rows(s, site_id=row['id'], limit=20)]
    return cls(id=row['id'], version=row['version'], name=row['name'], description=row['description'],
               address=row['address'], is_default=bool(row['is_default']), tracks_bins=bool(row['tracks_bins']),
               active=bool(row['active']), item_count=len(held), stock=stock, transfers=transfers, **extra)


def plan_site_create(s, ctx, inp) -> Plan:
    display, key = _site_name(s, inp.name)
    row = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()), name=display, name_key=key,
               description=(inp.description or '').strip() or None, address=(inp.address or '').strip() or None,
               is_default=bool(inp.is_default), tracks_bins=False, active=True)
    previous = placements.default_site(s) if inp.is_default else None
    return Plan(site_output(s, row, cls=SiteWriteOutput), dict(row=row, before=None, previous_default=previous))


def plan_site_update(s, ctx, inp) -> Plan:
    old = placements.resolve_site(s, inp.site, active=False)
    _version_check('inventory_site', old, inp.expected_version)
    new = dict(old)
    if inp.name is not None:
        new['name'], new['name_key'] = _site_name(s, inp.name, old['id'])
    for field in ('description', 'address'):
        if field in inp.model_fields_set:
            new[field] = (getattr(inp, field) or '').strip() or None
    previous = None
    if inp.is_default and not old['is_default']:
        if not old['active']:
            raise _invalid('is_default', 'an inactive site cannot be the default; activate it first')
        new['is_default'] = True
        previous = placements.default_site(s)
    changed = any(new[k] != old[k] for k in ('name', 'description', 'address', 'is_default'))
    if changed:
        new = _bumped(new, s, ctx)
    out = site_output(s, new, cls=SiteWriteOutput, changed=changed)
    return Plan(out, dict(row=new, before=old if changed else None, changed=changed, previous_default=previous))


def plan_site_active(s, ctx, inp, active: bool) -> Plan:
    old = placements.resolve_site(s, inp.site, active=False)
    _version_check('inventory_site', old, inp.expected_version)
    if not active:
        if old['is_default']:
            raise _invalid('site', f'"{old["name"]}" is the default site and cannot be deactivated; make another '
                                   'site the default first')
        held = {item: q for item, q in _held_today(s, old['id']).items() if q}
        if held:
            names = sorted(inventory.item_name(s, item) for item in held)
            raise BookflowError('E_RECORD_IN_USE', message=(
                f'"{old["name"]}" still holds stock of {", ".join(names[:10])}'
                + (f' and {len(names) - 10} more' if len(names) > 10 else '')
                + '. Transfer it to another site with `inventory-transfer post` first.'),
                details={'record_type': 'inventory_site', 'record_id': old['id'], 'items': names})
        incoming = _open_order_lines(s, old['id'])
        if incoming:
            listed = ', '.join(f"{number} line {position} ({quantity} {item})" for number, position, quantity, item in incoming[:10])
            raise BookflowError('E_RECORD_IN_USE', message=(
                f'"{old["name"]}" is where open purchase-order lines are to arrive: {listed}'
                + (f' and {len(incoming) - 10} more' if len(incoming) > 10 else '')
                + '. Receive or close those lines, or point them at another site, first.'),
                details={'record_type': 'inventory_site', 'record_id': old['id'], 'site': old['name'],
                         'purchase_order_lines': [dict(number=n, position=p, open_quantity=q, item=i)
                                                  for n, p, q, i in incoming]})
    changed = bool(old['active']) != active
    new = _bumped(dict(old, active=active), s, ctx) if changed else dict(old)
    return Plan(site_output(s, new, cls=SiteWriteOutput, changed=changed),
                dict(row=new, before=old if changed else None, changed=changed, previous_default=None))


def _open_order_lines(s, site_id):
    """Open purchase-order lines still to arrive at a site: ``[(number, position, open quantity, item)]``."""
    from bookflow.company import purchase_orders as orders
    from bookflow.company.purchase_order_billing import line_states
    t = c.purchase_orders
    out = []
    for header in s.company.conn.execute(sa.select(t).where(t.c.status.in_(('open', 'partly_received')))).mappings():
        rev = orders.revision(s, dict(header))
        for state in line_states(s, dict(header), rev):
            if state.get('site_id') == site_id and (state['open_quantity_microunits'] or 0) > 0:
                out.append((header['number'], state['position'], _q(state['open_quantity_microunits']),
                            inventory.item_name(s, state['item_id'])))
    return out


def apply_site(plan: Plan, ctx, s) -> Applied:
    row, before, previous = plan.data['row'], plan.data['before'], plan.data.get('previous_default')
    if before is None and plan.data.get('changed') is False:
        return Applied(plan.preview, [], 'no change')
    t = c.inventory_sites
    touched = []
    if previous is not None and previous['id'] != row['id']:
        # The old default steps down first; the partial unique index allows one default at a time.
        demoted = _bumped(dict(previous, is_default=False), s, ctx)
        if s.company.conn.execute(t.update().where(t.c.id == previous['id'], t.c.version == previous['version'])
                                  .values(**demoted)).rowcount != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_id': previous['id']})
        touched.append(Touched('inventory_site', previous['id'], 'update', previous['version'], demoted['version'],
                               demoted, previous, db='company'))
    if before is None:
        s.company.conn.execute(t.insert().values(**row))
        touched.append(Touched('inventory_site', row['id'], 'create', None, 1, row, db='company'))
        return Applied(plan.preview, touched, f"added inventory site {row['name']}")
    if s.company.conn.execute(t.update().where(t.c.id == row['id'], t.c.version == before['version'])
                              .values(**row)).rowcount != 1:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_id': row['id']})
    touched.append(Touched('inventory_site', row['id'], 'update', before['version'], row['version'], row, before,
                           db='company'))
    return Applied(plan.preview, touched, f"updated inventory site {row['name']}")


def site_show(s, inp) -> SiteOutput:
    return site_output(s, placements.resolve_site(s, inp.site, active=False), detail=True, as_of=inp.as_of)


def site_list(s, inp) -> SitePageOutput:
    rows = placements.all_sites(s) if inp.include_inactive else placements.active_sites(s)
    return SitePageOutput(sites=[site_output(s, row) for row in rows])


# ---------------------------------------------------------------------------- transfers

def resolve(s, selector) -> dict[str, Any]:
    t = c.inventory_transfers
    text = str(selector).strip()
    row = _row(s, t, t.c.id == normalize_ulid(text)) if is_ulid(text) else None
    if row is None and text:
        row = _row(s, t, t.c.number == text)
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'inventory_transfer', 'selector': text})
    return row


def _revisions(s, transfer_id):
    t = c.inventory_transfer_revisions
    return [dict(r) for r in s.company.conn.execute(
        sa.select(t).where(t.c.transfer_id == transfer_id).order_by(t.c.revision_number)).mappings()]


def _lines(s, revision_id):
    t = c.inventory_transfer_lines
    return [dict(r) for r in s.company.conn.execute(
        sa.select(t).where(t.c.revision_id == revision_id).order_by(t.c.position)).mappings()]


def _live_placements(s, revision_id):
    t = c.inventory_placements
    return [dict(r) for r in s.company.conn.execute(
        sa.select(t).where(t.c.transfer_revision_id == revision_id).order_by(t.c.sequence, t.c.id)).mappings()]


def _number(s, explicit, own=None):
    t = c.inventory_transfers

    def taken(number):
        query = sa.select(t.c.id).where(t.c.number == number)
        if own:
            query = query.where(t.c.id != own)
        return s.company.conn.execute(query).first() is not None
    if explicit is not None:
        if taken(explicit):
            raise BookflowError('E_DUPLICATE_NUMBER', details={'number': explicit, 'type': 'inventory_transfer'})
        return explicit, None
    seq = _row(s, c.sequences, c.sequences.c.name == SEQUENCE)
    number, prefix = (seq['next_number'], seq['prefix']) if seq else (1, 'T-')
    while taken(f'{prefix}{number}'):
        number += 1
    return f'{prefix}{number}', dict(name=SEQUENCE, next_number=number + 1, prefix=prefix)


def _resolved_lines(s, lines, date):
    from bookflow.company import item_conversions
    out = []
    for index, line in enumerate(lines):
        field = f'lines.{index}'
        item = item_service.resolve_item(s.company, line.item)
        if item['type'] not in inventory.TRACKED_TYPES:
            raise _invalid(field + '.item', f'"{item["full_name"]}" is a {item["type"].replace("_", " ")} item and '
                                            'carries no stock to move')
        if not item['active']:
            raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': 'item', 'record_id': item['id'],
                                                                 'field': field + '.item'})
        quantity = parse_quantity_micro_units(line.quantity, field=field + '.quantity')
        if quantity <= 0:
            raise _invalid(field + '.quantity', 'must be above 0')
        item_conversions.require_stock_date(s, item['id'], item['full_name'], date, field + '.item')
        from bookflow.company import serial_lots
        out.append(dict(item=item, quantity=quantity, description=(line.description or '').strip() or None,
                        numbers=serial_lots.from_line(line)))
    return out


def _old_numbers(s, revision_id):
    """``{line identity: Numbers}`` a stored revision's lines moved, so an update restating no lines keeps them."""
    from bookflow.company import serial_lots
    serials = serial_lots.line_serials(_live_placements(s, revision_id))
    lines = _lines(s, revision_id)
    names = serial_lots.number_names(s, [line['lot_id'] for line in lines], [i for ids in serials.values() for i in ids])
    out = {}
    for line in lines:
        numbers = serials.get(line['line_id'], [])
        out[line['line_id']] = serial_lots.Numbers(
            lot=names[line['lot_id']]['number'] if line['lot_id'] else None,
            serial_numbers=tuple(names[i]['number'] for i in numbers) if numbers else None)
    return out


def _decide(s, ctx, inp, operation):
    """Everything one transfer command does, decided before anything is written."""
    old = resolve(s, inp.transfer) if operation != 'post' else None
    old_rev = _revisions(s, old['id'])[-1] if old else None
    if old:
        _version_check('inventory_transfer', old, inp.expected_version)
        if old['status'] == 'voided':
            raise _invalid('transfer', 'this transfer is voided; a void is final')
    if operation == 'void':
        if not ctx.reason or not ctx.reason.strip():
            raise BookflowError('E_REASON_REQUIRED')
        journals.open_dates(s, [old_rev['date']])
        return dict(old=old, old_rev=old_rev, date=old_rev['date'], position=old_rev['count_position'],
                    from_site=placements.resolve_site(s, old_rev['from_site_id'], active=False),
                    to_site=placements.resolve_site(s, old_rev['to_site_id'], active=False),
                    memo=old_rev['memo'], lines=[], number=old['number'], sequence=None)
    date = (inp.date or (old_rev['date'] if old_rev else None)) or _today(s)
    from_site = placements.resolve_site(s, inp.from_site if inp.from_site is not None else old_rev['from_site_id'],
                                        field='from_site')
    to_site = placements.resolve_site(s, inp.to_site if inp.to_site is not None else old_rev['to_site_id'],
                                      field='to_site')
    if from_site['id'] == to_site['id']:
        raise _invalid('to_site', 'a transfer moves stock between two different sites (moving between bins of '
                                  'one site arrives with bins)')
    journals.open_dates(s, [date] + ([old_rev['date']] if old_rev else []))
    if inp.lines is not None:
        lines = _resolved_lines(s, inp.lines, date)
    else:
        kept = _old_numbers(s, old_rev['id'])
        lines = [dict(item=item_service.resolve_item(s.company, line['item_id']), quantity=int(line['quantity_microunits']),
                      description=line['description'], line_id=line['line_id'], numbers=kept.get(line['line_id']))
                 for line in _lines(s, old_rev['id'])]
    memo = inp.memo if 'memo' in inp.model_fields_set else (old_rev['memo'] if old_rev else None)
    position = inp.count_position or (old_rev['count_position'] if old_rev else 'before')
    number, sequence = (old['number'], None) if old else _number(s, inp.number)
    return dict(old=old, old_rev=old_rev, date=date, position=position, from_site=from_site, to_site=to_site,
                memo=(memo or '').strip() or None, lines=lines, number=number, sequence=sequence)


def _placements(s, decided, revision_id, line_ids, sequence, state=None):
    """The new revision's placements: the old ones reversed, then -q out and +q in per line, each lot and
    serial number moved with its units (R104 increment 4) and the rest unassigned."""
    from bookflow.company import serial_lots
    state = state if state is not None else serial_lots.Numbering(s)
    out = []
    if decided['old_rev'] is not None:
        live = _live_placements(s, decided['old_rev']['id'])
        retired = {row['reverses_placement_id'] for row in live if row['reverses_placement_id']}
        for original in live:
            if original['reverses_placement_id'] is not None or original['id'] in retired:
                continue   # the revision before it, already retired
            out.append(dict(original, id=new_id(), quantity_microunits=-int(original['quantity_microunits']),
                            sequence=sequence, transfer_revision_id=revision_id,
                            reverses_placement_id=original['id']))
    state.made.extend(out)
    for index, (line, line_id) in enumerate(zip(decided['lines'], line_ids)):
        base = dict(item_id=line['item']['id'], bin_id=None, lot_id=None, serial_number_id=None,
                    effective_date=decided['date'], count_position=decided['position'], sequence=sequence,
                    source='transfer', movement_id=None, transfer_revision_id=revision_id, source_line_id=line_id,
                    reverses_placement_id=None)
        lot_id, parts = serial_lots.transfer_parts(
            state, line['item'], line['quantity'], line.get('numbers'), decided['from_site']['id'],
            decided['to_site']['id'], decided['date'], decided['position'], sequence, f'lines.{index}')
        line['lot_id'] = lot_id
        for site, lot, serial, quantity, made in parts:
            row = dict(base, id=new_id(), site_id=site, lot_id=lot, serial_number_id=serial, quantity_microunits=quantity)
            if made is not None:
                row['_lot_row'] = made
            out.append(row)
            state.made.append(row)
    serial_lots.check(state, out)
    return out


def _on_hand_after(s, decided, new_placements):
    """Each line item at both sites on the transfer date, with this change applied."""
    items = list(dict.fromkeys(line['item']['id'] for line in decided['lines'])) or \
        list(dict.fromkeys(p['item_id'] for p in new_placements))
    stock = placements.on_hand(s, decided['date'], item_ids=items)
    for p in new_placements:
        if p['effective_date'] <= decided['date']:
            key = (p['item_id'], p['site_id'])
            stock[key] = stock.get(key, 0) + int(p['quantity_microunits'])
    rows = []
    for item in items:
        name = inventory.item_name(s, item)
        for site in (decided['from_site'], decided['to_site']):
            rows.append(TransferSiteStock(site_id=site['id'], site_name=site['name'], item_id=item, item_name=name,
                                          quantity_on_hand=_q(stock.get((item, site['id']), 0))))
    return rows


def _history(revisions):
    return [dict(version=r['revision_number'], operation=r['operation'], date=r['date'], memo=r['memo'],
                 reason=r['reason'], created_at=r['created_at'], created_by=r['created_by'],
                 created_via=r['created_via']) for r in revisions]


def _line_outputs(s, lines):
    from bookflow.company import serial_lots
    serials = {}
    for revision_id in {line['revision_id'] for line in lines}:
        serials.update(serial_lots.line_serials(_live_placements(s, revision_id)))
    names = serial_lots.number_names(s, [line.get('lot_id') for line in lines],
                                     [i for ids in serials.values() for i in ids])
    return [TransferLineOutput(line_id=line['line_id'], item_id=line['item_id'],
                               item_name=inventory.item_name(s, line['item_id']),
                               quantity=_q(line['quantity_microunits']), description=line['description'],
                               lot=(names.get(line['lot_id']) or {}).get('number') if line.get('lot_id') else None,
                               serial_numbers=[(names.get(i) or {}).get('number', i) for i in serials.get(line['line_id'], [])])
            for line in lines]


def transfer_output(s, header, rev, lines, *, cls=TransferOutput, status=None, warnings=(), on_hand_after=(),
                    history=None, **extra):
    names = placements.site_names(s)
    number = extra.pop('number', None)
    return cls(id=header['id'] if header else None, number=header['number'] if header else number,
               version=header['version'] if header else 1, status=status or header['status'],
               date=rev['date'], count_position=rev['count_position'], from_site_id=rev['from_site_id'],
               from_site_name=names.get(rev['from_site_id'], ''), to_site_id=rev['to_site_id'],
               to_site_name=names.get(rev['to_site_id'], ''), memo=rev['memo'], reason=rev.get('reason'),
               lines=_line_outputs(s, lines),
               on_hand_after=list(on_hand_after), warnings=list(dict.fromkeys(warnings)),
               history=history if history is not None else [], **extra)


def _run(s, ctx, inp, operation, *, persist):
    decided = _decide(s, ctx, inp, operation)
    old, old_rev = decided['old'], decided['old_rev']
    at = clock.now_iso()
    event = new_id()
    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
    header = dict(old) if old else dict(id=new_id(), **common(s.actor.id, ctx.interface.value, at),
                                        number=decided['number'], status='posted', current_revision_id=None)
    revision_id = new_id()
    rev = dict(id=revision_id, transfer_id=header['id'],
               revision_number=(old_rev['revision_number'] + 1) if old_rev else 1, operation=operation,
               date=decided['date'], count_position=decided['position'], from_site_id=decided['from_site']['id'],
               to_site_id=decided['to_site']['id'], memo=decided['memo'],
               reason=(ctx.reason or '').strip() or None, audit_event_id=event, **provenance)
    lines = []
    for position, line in enumerate(decided['lines'], 1):
        lines.append(dict(id=new_id(), revision_id=revision_id, line_id=line.get('line_id') or new_id(),
                          position=position, item_id=line['item']['id'], quantity_microunits=line['quantity'],
                          unit_id=None, unit_factor_nanounits=1_000_000_000, from_bin_id=None, to_bin_id=None,
                          lot_id=None, description=line['description']))
    sequence = inventory.next_sequence(s)
    from bookflow.company import serial_lots
    numbering = serial_lots.Numbering(s)
    new_placements = [dict(p, **provenance, audit_event_id=event)
                      for p in _placements(s, decided, revision_id, [line['line_id'] for line in lines], sequence,
                                           numbering)]
    for line, decided_line in zip(lines, decided['lines']):
        line['lot_id'] = decided_line.get('lot_id')
    warnings = placements.warnings(s, new_placements) + numbering.warnings
    on_hand_after = _on_hand_after(s, decided, new_placements)
    if operation == 'void':
        header.update(status='voided')
    if old:
        header = _bumped(header, s, ctx)
    header['current_revision_id'] = revision_id
    if not persist:
        shown = rev if operation != 'void' else dict(old_rev, operation='void')
        shown_lines = lines if operation != 'void' else _lines(s, old_rev['id'])
        return transfer_output(s, header if old else None, shown, shown_lines, cls=TransferWriteOutput,
                               status='will_post' if not old else header['status'], warnings=warnings,
                               on_hand_after=on_hand_after, number=decided['number'],
                               history=_history(_revisions(s, old['id'])) if old else []), [], 'preview'
    touched, clean = placements.number_touched(new_placements)
    touched += [Touched('inventory_placement', p['id'], 'create', None, 1, p, db='company') for p in clean]
    touched.append(Touched('inventory_transfer_revision', rev['id'], 'create', None, 1, rev, db='company'))
    touched += [Touched('inventory_transfer_line', line['id'], 'create', None, 1, line, db='company') for line in lines]
    touched.append(Touched('inventory_transfer', header['id'], 'update' if old else 'create',
                           old['version'] if old else None, header['version'], header, old, db='company'))
    summary = f"{ {'post': 'post', 'update': 'correct', 'void': 'void'}[operation] } inventory transfer {header['number']}"
    audit.write_event_to(s.company, ctx, 'inventory-transfer ' + operation, summary, touched, actor_id=s.actor.id,
                         actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None), event_id=event)
    conn = s.company.conn
    if old:
        if conn.execute(c.inventory_transfers.update().where(
                c.inventory_transfers.c.id == header['id'],
                c.inventory_transfers.c.version == old['version']).values(**header)).rowcount != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_id': header['id']})
    else:
        conn.execute(c.inventory_transfers.insert().values(**header))
    # Placements first: the revision row is refused unless they net to zero per item.
    placements.insert(s, new_placements)
    conn.execute(c.inventory_transfer_revisions.insert().values(**rev))
    if lines:
        conn.execute(c.inventory_transfer_lines.insert(), lines)
    if decided['sequence']:
        from sqlalchemy.dialects.sqlite import insert
        stmt = insert(c.sequences).values(**decided['sequence'])
        conn.execute(stmt.on_conflict_do_update(index_elements=['name'], set_=decided['sequence']))
    shown_rev = rev if operation != 'void' else dict(old_rev, operation='void', reason=rev['reason'])
    shown_lines = lines if operation != 'void' else _lines(s, old_rev['id'])
    out = transfer_output(s, header, shown_rev, shown_lines, cls=TransferWriteOutput, warnings=warnings,
                          on_hand_after=on_hand_after, history=_history(_revisions(s, header['id'])))
    return out, touched, summary


def prepare(s, ctx, inp, operation) -> Plan:
    output, _, _ = _run(s, ctx, inp, operation, persist=False)
    return Plan(output, {'input': inp, 'operation': operation})


def apply(plan, ctx, s) -> Applied:
    # Decided again inside the writer transaction: stock, numbering and versions are decisive only here.
    output, touched, summary = _run(s, ctx, plan.data['input'], plan.data['operation'], persist=True)
    return Applied(output, touched, summary, audited=True)


def show(s, inp) -> TransferOutput:
    header = resolve(s, inp.transfer)
    revisions = _revisions(s, header['id'])
    current = revisions[-1]
    shown = current if current['operation'] != 'void' else dict(revisions[-2] if len(revisions) > 1 else current,
                                                               reason=current['reason'])
    return transfer_output(s, header, shown, _lines(s, shown['id']), history=_history(revisions))


def query_rows(s, *, site_id=None, item_id=None, date_from=None, date_to=None, include_voided=False, limit=50):
    t, r = c.inventory_transfers, c.inventory_transfer_revisions
    query = sa.select(t, r.c.date.label('rev_date')).join(r, r.c.id == t.c.current_revision_id)
    out = []
    for row in s.company.conn.execute(query.order_by(r.c.date.desc(), t.c.number.desc())).mappings():
        row = dict(row)
        if row['status'] == 'voided' and not include_voided:
            continue
        revisions = _revisions(s, row['id'])
        shown = revisions[-1] if revisions[-1]['operation'] != 'void' else revisions[-2]
        if site_id and site_id not in (shown['from_site_id'], shown['to_site_id']):
            continue
        if date_from and shown['date'] < date_from or date_to and shown['date'] > date_to:
            continue
        lines = _lines(s, shown['id'])
        if item_id and not any(line['item_id'] == item_id for line in lines):
            continue
        header = {k: row[k] for k in row if k != 'rev_date'}
        out.append(transfer_output(s, header, dict(shown, reason=revisions[-1]['reason']), lines))
        if len(out) >= limit:
            break
    return out


def query(s, inp) -> TransferPageOutput:
    site_id = placements.resolve_site(s, inp.site, active=False)['id'] if inp.site else None
    item_id = item_service.resolve_item(s.company, inp.item)['id'] if inp.item else None
    rows = query_rows(s, site_id=site_id, item_id=item_id, date_from=inp.date_from, date_to=inp.date_to,
                      include_voided=inp.include_voided, limit=inp.limit)
    return TransferPageOutput(transfers=rows, count=len(rows))


def history(s, inp) -> TransferOutput:
    return show(s, inp)
