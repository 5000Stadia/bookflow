"""Bins: where inside a site stock is (R104 increment 5, design/advanced-inventory.md §2).

A site that ``tracks_bins`` locates its stock to bins, up to three levels deep ("A:3:Top"), as the anchor's
Advanced Inventory bin locations do. Bins live in ``inventory_bins``; where units are is the bin column of
their placements, so a site's bins (with "no bin", which is a place too) always add up to the site.

- **A line's bin.** The line's ``bin``, else -- on a correction -- the bin the line's stock stood in, else a
  return's issue's bin, else the item's default bin at that site (``item update --site-settings``) when the
  site tracks bins, else no bin. Naming a bin at a site that does not track bins is refused.
- **Resolution.** A bin is named by id, by its full name at its site, or by its label code (``barcode``).
- **The list.** Create, update (a rename renames the bins inside it), show, list, activate and deactivate.
  A bin holding stock (itself or a bin inside it) cannot be deactivated; neither can a bin with active bins
  inside it. A site cannot stop tracking bins while any of its bins holds stock.
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bookflow.company import inventory, placements, schema as c
from bookflow.company.bin_models import (
    BinOutput, BinPageOutput, BinStockRow, BinWriteOutput, ItemsByBinOutput, ItemsByBinRow)
from bookflow.company.lists import normalize_display_name
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units
from bookflow.core.ids import is_ulid, new_id, normalize_ulid
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

MAX_DEPTH = 3
NO_BIN = '(no bin)'
_DEFAULT = UNSET = object()
ANY = object()


def _invalid(field, problem, message=None, **details):
    return BookflowError('E_VALIDATION', message=message, details={'fields': [{'field': field, 'problem': problem}], **details})


def _q(units):
    return format_quantity_micro_units(int(units))


def _key(text):
    import unicodedata
    return unicodedata.normalize('NFC', str(text).strip().casefold())


# ---------------------------------------------------------------- lookups

def _rows(s, *where):
    t = c.inventory_bins
    return [dict(row) for row in s.company.conn.execute(
        sa.select(t).where(*where).order_by(t.c.site_id, t.c.full_name_key, t.c.id)).mappings()]


def bin_row(s, bin_id):
    found = _rows(s, c.inventory_bins.c.id == bin_id)
    return found[0] if found else None


def bin_names(s, bin_ids=None):
    """``{bin_id: full name}``."""
    t = c.inventory_bins
    query = sa.select(t.c.id, t.c.full_name)
    if bin_ids is not None:
        ids = [b for b in dict.fromkeys(bin_ids) if b]
        if not ids:
            return {}
        query = query.where(t.c.id.in_(ids))
    return dict(s.company.conn.execute(query).all())


def site_tracks_bins(s, site_id):
    return bool(s.company.conn.execute(sa.select(c.inventory_sites.c.tracks_bins).where(
        c.inventory_sites.c.id == site_id)).scalar_one_or_none())


def any_bins(s):
    """Whether any site tracks bins: bin fields show only then."""
    return s.company.conn.execute(sa.select(c.inventory_sites.c.id).where(
        c.inventory_sites.c.tracks_bins.is_(True), c.inventory_sites.c.active.is_(True)).limit(1)).first() is not None


def _site_bins_text(s, site_id):
    names = [row['full_name'] for row in _rows(s, c.inventory_bins.c.site_id == site_id, c.inventory_bins.c.active.is_(True))]
    if not names:
        return 'it has no bins yet (add one with `bin create`)'
    return 'its bins are: ' + ', '.join(names[:20]) + (f' and {len(names) - 20} more' if len(names) > 20 else '')


def resolve_bin(s, site_id, selector, *, field='bin', active=True, require_tracking=True):
    """A bin at ``site_id`` (None: any site, when only one has it) by id, full name or label code."""
    t = c.inventory_bins
    text = str(selector).strip()
    if site_id and require_tracking and not site_tracks_bins(s, site_id):
        raise _not_tracking(s, site_id, field)
    where = [t.c.site_id == site_id] if site_id else []
    found = []
    if is_ulid(text):
        found = _rows(s, t.c.id == normalize_ulid(text), *where)
    if not found and text:
        found = _rows(s, t.c.full_name_key == _key(text), *where)
    if not found and text:
        found = _rows(s, t.c.barcode == text, t.c.active.is_(True), *where)
    site_names = placements.site_names(s)
    if not found:
        if site_id:
            raise _invalid(field, f'no bin "{text}" at {site_names.get(site_id, site_id)}', message=(
                f'{site_names.get(site_id, site_id)} has no bin "{text}"; {_site_bins_text(s, site_id)}.'),
                selector=text)
        raise _invalid(field, f'no bin "{text}"', selector=text)
    if len(found) > 1:
        where_ = ', '.join(f'{row["full_name"]} at {site_names.get(row["site_id"], row["site_id"])}' for row in found)
        raise _invalid(field, f'"{text}" names more than one bin ({where_}); name the site too', candidates=[
            dict(id=row['id'], full_name=row['full_name'], site=site_names.get(row['site_id'])) for row in found])
    row = found[0]
    if require_tracking and not site_tracks_bins(s, row['site_id']):
        raise _not_tracking(s, row['site_id'], field)
    if active and not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': 'inventory_bin', 'record_id': row['id'],
                                                             'field': field})
    return row


def _not_tracking(s, site_id, field):
    name = placements.site_names(s).get(site_id, site_id)
    return _invalid(field, f'{name} does not track bins', message=(
        f'{name} does not track bins, so its lines name none. Turn bins on with `inventory-site update '
        f'"{name}" --tracks-bins true` first.'), site=name)


def names_bin(row, selector):
    """Whether ``selector`` names this stored bin (by id, full name or label)."""
    text = str(selector).strip()
    return text == row['id'] or (is_ulid(text) and normalize_ulid(text) == row['id']) \
        or _key(text) == row['full_name_key'] or (row['barcode'] is not None and text == row['barcode'])


def default_bin_id(s, item_id, site_id):
    """The item's default bin at the site, when the site tracks bins and that bin is active; else None."""
    t, b = c.item_site_settings, c.inventory_bins
    row = s.company.conn.execute(sa.select(b.c.id, b.c.active).join(t, t.c.default_bin_id == b.c.id).where(
        t.c.item_id == item_id, t.c.site_id == site_id)).first()
    if row is None or not row.active or not site_tracks_bins(s, site_id):
        return None
    return row.id


def is_no_bin(selector):
    """Whether a line names "no bin" itself: the token ``(no bin)`` (any case), the place a site's units sit in
    outside every bin."""
    return selector is not None and str(selector).strip().casefold() == NO_BIN


def line_bin(s, item_id, site_id, selector=None, *, field='bin', fallback=_DEFAULT):
    """The bin a stock line's units go to or leave from at ``site_id`` (design §2, "A line's place").

    ``selector`` is what the line names (``(no bin)`` names no bin); ``fallback`` the bin a correction's stock
    stood in, or a return's issue's (when it is a bin of this site). Else the item's default bin at the site,
    else no bin."""
    if is_no_bin(selector):
        return None
    if selector is not None and str(selector).strip():
        return resolve_bin(s, site_id, selector, field=field)['id']
    if fallback is not _DEFAULT:
        if fallback is None:
            return None
        row = bin_row(s, fallback)
        if row is not None and row['site_id'] == site_id:
            return fallback
    return default_bin_id(s, item_id, site_id)


def available(s, item_id, site_id, date, position='before', *, lot_id=ANY, made=()):
    """``{bin_id or None: quantity}`` of an item at a site's places just before a line at ``date`` and
    ``position``: every placement dated earlier, and on ``date`` those at or before ``position`` -- plus what
    this change has already placed (``made``). ``lot_id`` narrows to one lot, or None to the unassigned units
    (no lot, no serial number); ANY reads every unit."""
    rank = {'before': 0, 'count': 1, 'after': 2}
    t = c.inventory_placements
    positions = [name for name, value in rank.items() if value <= rank.get(position or 'before', 0)]
    query = sa.select(t.c.bin_id, sa.func.sum(t.c.quantity_microunits)).where(
        t.c.item_id == item_id, t.c.site_id == site_id,
        sa.or_(t.c.effective_date < date, sa.and_(t.c.effective_date == date, t.c.count_position.in_(positions))))
    if lot_id is not ANY:
        query = query.where(t.c.serial_number_id.is_(None),
                            t.c.lot_id.is_(None) if lot_id is None else t.c.lot_id == lot_id)
    out = {b: int(q) for b, q in s.company.conn.execute(query.group_by(t.c.bin_id))}
    for row in made:
        if row['item_id'] != item_id or row['site_id'] != site_id:
            continue
        if lot_id is not ANY and (row.get('serial_number_id') or row.get('lot_id') != lot_id):
            continue
        out[row.get('bin_id')] = out.get(row.get('bin_id'), 0) + int(row['quantity_microunits'])
    return out


def draw(s, home, quantity, held):
    """``[(bin_id, quantity)]`` an outgoing line naming no bin takes ``quantity`` from (R104 increment 5 review):
    its home (the item's default bin, or no bin) when that holds them all; else no bin when that does; else the one
    bin that does; else from each place holding some, home first, then no bin, then the bins by name -- and only
    what nothing at the site holds is taken from home, below zero (the result warns). So a line never drives a bin
    negative while the units sit elsewhere at the site."""
    holding = {b: q for b, q in held.items() if q > 0}
    for place in (home, None):
        if holding.get(place, 0) >= quantity:
            return [(place, quantity)]
    names = bin_names(s, [b for b in holding if b])
    others = sorted((b for b in holding if b not in (home, None)), key=lambda b: (names.get(b, '').casefold(), b))
    whole = [b for b in others if holding[b] >= quantity]
    if whole:
        return [(whole[0], quantity)]
    out, left = [], quantity
    for place in [home] + ([None] if home is not None else []) + others:
        take = min(holding.get(place, 0), left)
        if take > 0:
            out.append((place, take))
            left -= take
    if left:
        out = [(b, q + left) if b == home else (b, q) for b, q in out] if any(b == home for b, _ in out) \
            else out + [(home, left)]
    return out


def descendants(s, bin_id):
    """The bin and every bin inside it: ``[bin_id, ...]``."""
    out, frontier = [bin_id], [bin_id]
    t = c.inventory_bins
    while frontier:
        frontier = list(s.company.conn.execute(sa.select(t.c.id).where(t.c.parent_id.in_(frontier))).scalars())
        out += frontier
    return out


def held(s, as_of=None, *, site_id=None, bin_ids=None, item_ids=None):
    """``{(item, site, bin, lot, serial): quantity}`` from placements dated by ``as_of``."""
    t = c.inventory_placements
    query = sa.select(t.c.item_id, t.c.site_id, t.c.bin_id, t.c.lot_id, t.c.serial_number_id,
                      sa.func.sum(t.c.quantity_microunits)).group_by(
        t.c.item_id, t.c.site_id, t.c.bin_id, t.c.lot_id, t.c.serial_number_id)
    if as_of is not None:
        query = query.where(t.c.effective_date <= as_of)
    if site_id is not None:
        query = query.where(t.c.site_id == site_id)
    if bin_ids is not None:
        query = query.where(t.c.bin_id.in_(list(bin_ids)))
    if item_ids is not None:
        query = query.where(t.c.item_id.in_(list(item_ids)))
    return {tuple(row[:5]): int(row[5]) for row in s.company.conn.execute(query) if int(row[5])}


# ---------------------------------------------------------------- the list

def _version_check(row, expected):
    if expected is not None and expected != row['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': 'inventory_bin', 'record_id': row['id'], 'current_version': row['version'],
            'expected_version': expected, 'updated_at': row['updated_at'], 'updated_by': row['updated_by']})


def _bumped(row, s, ctx):
    return dict(row, version=row['version'] + 1, updated_at=clock.now_iso(), updated_by=s.actor.id,
                updated_via=ctx.interface.value)


def _check_code(s, code, own=None):
    t = c.inventory_bins
    query = sa.select(t.c.id, t.c.full_name, t.c.site_id).where(t.c.barcode == code, t.c.active.is_(True))
    if own:
        query = query.where(t.c.id != own)
    clash = s.company.conn.execute(query).first()
    if clash is not None:
        site = placements.site_names(s).get(clash.site_id, '')
        raise _invalid('barcode', f'bin {clash.full_name} at {site} already carries the code {code}',
                       bin=clash.full_name, site=site)


def bin_output(s, row, *, cls=BinOutput, detail=False, as_of=None, **extra):
    ids = descendants(s, row['id']) if row.get('id') and bin_row(s, row['id']) else [row['id']]
    stock_rows = held(s, as_of, site_id=row['site_id'], bin_ids=ids)
    items = {key[0] for key in stock_rows}
    stock = []
    if detail and stock_rows:
        from bookflow.company import serial_lots
        names = serial_lots.number_names(s, [k[3] for k in stock_rows], [k[4] for k in stock_rows])
        stock = sorted((BinStockRow(item_id=k[0], item_name=inventory.item_name(s, k[0]),
                                    lot=(names.get(k[3]) or {}).get('number') if k[3] else None,
                                    serial_number=(names.get(k[4]) or {}).get('number') if k[4] else None,
                                    quantity_on_hand=_q(q))
                        for k, q in stock_rows.items()), key=lambda r: (r.item_name.casefold(), r.lot or '', r.serial_number or ''))
    return cls(id=row['id'], version=row['version'], site_id=row['site_id'],
               site_name=placements.site_names(s).get(row['site_id'], ''), name=row['name'], full_name=row['full_name'],
               parent_id=row['parent_id'], depth=row['depth'], barcode=row['barcode'], description=row['description'],
               active=bool(row['active']), item_count=len(items), stock=stock, **extra)


def _resolve_selected(s, inp, *, active=False):
    site_id = placements.resolve_site(s, inp.site, active=False)['id'] if getattr(inp, 'site', None) else None
    return resolve_bin(s, site_id, inp.bin, active=active, require_tracking=False)


def plan_create(s, ctx, inp) -> Plan:
    site = placements.resolve_site(s, inp.site) if inp.site else placements.default_site(s)
    if not site['tracks_bins']:
        raise _not_tracking(s, site['id'], 'site')
    display, _ = normalize_display_name(inp.name, field='name')
    parent = resolve_bin(s, site['id'], inp.parent, field='parent') if inp.parent else None
    depth = (parent['depth'] + 1) if parent else 1
    if depth > MAX_DEPTH:
        raise _invalid('parent', f'bins go {MAX_DEPTH} levels deep; {parent["full_name"]} is already at level {MAX_DEPTH}')
    full = f'{parent["full_name"]}:{display}' if parent else display
    clash = _rows(s, c.inventory_bins.c.site_id == site['id'], c.inventory_bins.c.full_name_key == _key(full))
    if clash:
        raise BookflowError('E_NAME_TAKEN', details={'record_type': 'inventory_bin', 'name': full,
                                                     'record_id': clash[0]['id']})
    code = (inp.barcode or '').strip() or None
    if code:
        _check_code(s, code)
    row = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, clock.now_iso()), site_id=site['id'],
               parent_id=parent['id'] if parent else None, name=display, full_name=full, full_name_key=_key(full),
               depth=depth, barcode=code, description=(inp.description or '').strip() or None, active=True)
    return Plan(bin_output(s, row, cls=BinWriteOutput), dict(rows=[(None, row)]))


def plan_update(s, ctx, inp) -> Plan:
    old = _resolve_selected(s, inp)
    _version_check(old, inp.expected_version)
    new = dict(old)
    renamed = []
    rows = []
    if inp.name is not None:
        display, _ = normalize_display_name(inp.name, field='name')
        if display != old['name']:
            parent = bin_row(s, old['parent_id']) if old['parent_id'] else None
            full = f'{parent["full_name"]}:{display}' if parent else display
            clash = [r for r in _rows(s, c.inventory_bins.c.site_id == old['site_id'],
                                      c.inventory_bins.c.full_name_key == _key(full)) if r['id'] != old['id']]
            if clash:
                raise BookflowError('E_NAME_TAKEN', details={'record_type': 'inventory_bin', 'name': full,
                                                             'record_id': clash[0]['id']})
            new.update(name=display, full_name=full, full_name_key=_key(full))
            prefix = old['full_name'] + ':'
            for child_id in descendants(s, old['id'])[1:]:
                child = bin_row(s, child_id)
                child_full = full + ':' + child['full_name'][len(prefix):]
                after = _bumped(dict(child, full_name=child_full, full_name_key=_key(child_full)), s, ctx)
                rows.append((child, after))
                renamed.append(child_full)
    if 'barcode' in inp.model_fields_set:
        code = (inp.barcode or '').strip() or None
        if code and code != old['barcode']:
            _check_code(s, code, old['id'])
        new['barcode'] = code
    if 'description' in inp.model_fields_set:
        new['description'] = (inp.description or '').strip() or None
    changed = any(new[k] != old[k] for k in ('name', 'full_name', 'barcode', 'description'))
    if changed:
        new = _bumped(new, s, ctx)
        rows.insert(0, (old, new))
    return Plan(bin_output(s, new, cls=BinWriteOutput, changed=changed, renamed=renamed), dict(rows=rows))


def plan_active(s, ctx, inp, active) -> Plan:
    old = _resolve_selected(s, inp)
    _version_check(old, inp.expected_version)
    if not active:
        ids = descendants(s, old['id'])
        stock = held(s, site_id=old['site_id'], bin_ids=ids)
        if stock:
            names = sorted({inventory.item_name(s, key[0]) for key in stock})
            raise BookflowError('E_RECORD_IN_USE', message=(
                f'Bin {old["full_name"]} still holds {", ".join(names[:10])}' + (f' and {len(names) - 10} more' if len(names) > 10 else '')
                + '. Move it to another bin with `inventory-transfer post` first.'),
                details={'record_type': 'inventory_bin', 'record_id': old['id'], 'items': names})
        inside = [r for r in _rows(s, c.inventory_bins.c.parent_id == old['id'], c.inventory_bins.c.active.is_(True))]
        if inside:
            raise BookflowError('E_RECORD_IN_USE', message=(
                f'Bin {old["full_name"]} has active bins inside it ({", ".join(r["full_name"] for r in inside[:10])}); '
                'deactivate them first.'), details={'record_type': 'inventory_bin', 'record_id': old['id']})
    else:
        if old['parent_id']:
            parent = bin_row(s, old['parent_id'])
            if not parent['active']:
                raise _invalid('bin', f'the bin it sits in, {parent["full_name"]}, is inactive; activate it first')
        if old['barcode']:
            _check_code(s, old['barcode'], old['id'])
    changed = bool(old['active']) != active
    new = _bumped(dict(old, active=active), s, ctx) if changed else dict(old)
    return Plan(bin_output(s, new, cls=BinWriteOutput, changed=changed), dict(rows=[(old, new)] if changed else []))


def apply(plan: Plan, ctx, s) -> Applied:
    rows = plan.data['rows']
    if not rows:
        return Applied(plan.preview, [], 'no change')
    t = c.inventory_bins
    touched = []
    for before, after in rows:
        if before is None:
            s.company.conn.execute(t.insert().values(**after))
            touched.append(Touched('inventory_bin', after['id'], 'create', None, 1, after, db='company'))
            continue
        if s.company.conn.execute(t.update().where(t.c.id == before['id'], t.c.version == before['version'])
                                  .values(**after)).rowcount != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_id': before['id']})
        touched.append(Touched('inventory_bin', after['id'], 'update', before['version'], after['version'], after,
                               before, db='company'))
    first = rows[0][1]
    verb = 'added' if rows[0][0] is None else 'updated'
    return Applied(plan.preview, touched, f'{verb} bin {first["full_name"]}')


def show(s, inp):
    return bin_output(s, _resolve_selected(s, inp), detail=True, as_of=inp.as_of)


def listing(s, inp):
    t = c.inventory_bins
    where = []
    if inp.site:
        where.append(t.c.site_id == placements.resolve_site(s, inp.site, active=False)['id'])
    if not inp.include_inactive:
        where.append(t.c.active.is_(True))
    return BinPageOutput(bins=[bin_output(s, row) for row in _rows(s, *where)])


# ---------------------------------------------------------------- items by bin

def items_by_bin(s, inp) -> ItemsByBinOutput:
    """Items by Bin: each item at each bin of a site, with what it holds at no bin, and site totals that the
    bins add up to (the quantity tie is checked first)."""
    from bookflow.company import serial_lots
    from bookflow.company.memorized_schedule import today
    as_of = inp.as_of or today(getattr(s, 'company_tz', None))
    placements.check_tie(s, as_of)
    site = placements.resolve_site(s, inp.site, active=False) if inp.site else None
    if site is not None:
        sites = [site]
    else:
        sites = [row for row in placements.all_sites(s) if row['tracks_bins']]
    bin_filter = None
    if inp.bin:
        chosen = resolve_bin(s, site['id'] if site else None, inp.bin, active=False, require_tracking=False)
        bin_filter = set(descendants(s, chosen['id']))
        sites = [row for row in placements.all_sites(s) if row['id'] == chosen['site_id']]
    item_ids = None
    if inp.item:
        from bookflow.company import items as item_service
        item_ids = [item_service.resolve_item(s.company, inp.item)['id']]
    names = bin_names(s)
    rows, totals = [], []
    for each in sites:
        stock = held(s, as_of, site_id=each['id'], item_ids=item_ids)
        numbers = serial_lots.number_names(s, [k[3] for k in stock], [k[4] for k in stock])
        site_sum = {}
        for key, quantity in stock.items():
            site_sum[key[0]] = site_sum.get(key[0], 0) + quantity
        keys = list(stock)
        if inp.include_zero and bin_filter is None:
            for bin_row_ in _rows(s, c.inventory_bins.c.site_id == each['id'], c.inventory_bins.c.active.is_(True)):
                if not any(k[2] == bin_row_['id'] for k in keys):
                    keys.append((None, each['id'], bin_row_['id'], None, None))
        for key in keys:
            item, _, bin_id, lot, serial = key
            if bin_filter is not None and bin_id not in bin_filter:
                continue
            rows.append(ItemsByBinRow(
                site_id=each['id'], site_name=each['name'], bin_id=bin_id, bin=names.get(bin_id, NO_BIN) if bin_id else NO_BIN,
                item_id=item or '', item_name=inventory.item_name(s, item) if item else '',
                lot=(numbers.get(lot) or {}).get('number') if lot else None,
                serial_number=(numbers.get(serial) or {}).get('number') if serial else None,
                quantity_on_hand=_q(stock.get(key, 0))))
        if bin_filter is None:
            for item, quantity in sorted(site_sum.items(), key=lambda kv: inventory.item_name(s, kv[0]).casefold()):
                totals.append(dict(site=each['name'], item_id=item, item_name=inventory.item_name(s, item),
                                   quantity_on_hand=_q(quantity)))
    rows.sort(key=lambda r: (r.site_name.casefold(), r.bin == NO_BIN, r.bin.casefold(), r.item_name.casefold(),
                             r.lot or '', r.serial_number or ''))
    return ItemsByBinOutput(as_of=as_of, rows=rows, site_totals=totals)


# ---------------------------------------------------------------- sites gaining and losing bins

def check_tracking_off(s, site):
    """A site stops tracking bins only while none of its bins holds stock."""
    t = c.inventory_placements
    holding = s.company.conn.execute(sa.select(t.c.bin_id, sa.func.sum(t.c.quantity_microunits)).where(
        t.c.site_id == site['id'], t.c.bin_id.is_not(None)).group_by(t.c.bin_id, t.c.item_id)
        .having(sa.func.sum(t.c.quantity_microunits) != 0)).first()
    if holding is not None:
        name = bin_names(s, [holding[0]]).get(holding[0], holding[0])
        raise BookflowError('E_RECORD_IN_USE', message=(
            f'{site["name"]} still holds stock in its bins (bin {name}, among others); move it to no bin with '
            '`inventory-transfer post` before turning bins off.'),
            details={'record_type': 'inventory_site', 'record_id': site['id'], 'bin': name})


def place_words(s, site_id, bin_id):
    """A place in words for messages: "A1" at a site's bin, "Warehouse (no bin)", or "Van 1 A2" across sites."""
    site = placements.site_names(s).get(site_id, site_id)
    name = bin_names(s, [bin_id]).get(bin_id, bin_id) if bin_id else NO_BIN
    return name if bin_id else f'{site} {name}'


def place_label(bin_name, lot=None, serial=None):
    parts = [bin_name] if bin_name else []
    if serial:
        parts.append('serial ' + serial)
    elif lot:
        parts.append('lot ' + lot)
    return ', '.join(parts) if parts else 'unassigned'


def bins_of(s, site_id) -> list[dict[str, Any]]:
    return _rows(s, c.inventory_bins.c.site_id == site_id)
