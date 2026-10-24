"""Where stock is: placements, sites, and the quantity tie (R104 increment 1, design §2).

``inventory_movements`` says how much of an item the company holds and what it is worth. A
placement says *where* those units are: at which site (and, from later increments, bin, lot or
serial number). Every movement that moves quantity is placed by the writer that writes it, and the
database refuses a movement whose placements do not sum to its quantity. Transfers move units
between places with placements alone; they post nothing and never touch cost, which stays one
weighted average per item, company-wide.

This module owns:

- the site list's lookups (``default_site``, ``resolve_site``, ``active_sites``);
- ``for_movements``: the placement rows for a set of movement rows -- a movement at its stated site
  (else the default), a reversal retiring exactly the placements of the movement it reverses;
- ``on_hand``: quantity by item and site from placements, through an as-of date;
- ``check_tie``: placements per item equal the movements' quantity on a date, else E_INTERNAL;
- ``value_split``: an item's value shared over its sites with ``endpoint_share`` so the sites sum
  exactly to the item and a valuation by site adds up to the account;
- ``warnings``: a site taken below zero says where else the item is.

**Unassigned.** A placement with no lot and no serial number is the item's unassigned bucket at
that place. Nothing in this increment assigns numbers, so every placement is unassigned; later
increments add lots and serial numbers to the same rows without changing what a site holds.
"""
from __future__ import annotations

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.core.errors import BookflowError
from bookflow.core.exact import endpoint_share, format_quantity_micro_units
from bookflow.core.ids import is_ulid, new_id, normalize_ulid

DEFAULT_SITE_NAME = 'Main'


# ---------------------------------------------------------------- sites

def _site_rows(s, *where):
    t = c.inventory_sites
    return [dict(row) for row in s.company.conn.execute(
        sa.select(t).where(*where).order_by(t.c.name_key, t.c.id)).mappings()]


def all_sites(s):
    return _site_rows(s)


def active_sites(s):
    return _site_rows(s, c.inventory_sites.c.active.is_(True))


def default_site(s):
    rows = _site_rows(s, c.inventory_sites.c.is_default.is_(True))
    if not rows:
        raise BookflowError('E_INTERNAL', message='The company has no default inventory site.')
    return rows[0]


def default_site_id(s):
    return default_site(s)['id']


def multi_site(s):
    """Site fields show, and site warnings speak, only once a company has more than one active site."""
    return int(s.company.conn.execute(sa.select(sa.func.count()).select_from(c.inventory_sites).where(
        c.inventory_sites.c.active.is_(True))).scalar_one()) > 1


def site_names(s):
    return {row['id']: row['name'] for row in all_sites(s)}


def resolve_site(s, selector, *, field='site', active=True):
    """A site by id or name; an unknown one is refused with the company's sites listed."""
    from bookflow.company.lists import normalize_display_name
    t = c.inventory_sites
    text = str(selector).strip()
    row = None
    if is_ulid(text):
        found = _site_rows(s, t.c.id == normalize_ulid(text))
        row = found[0] if found else None
    if row is None and text:
        _, key = normalize_display_name(text, field=field)
        found = _site_rows(s, t.c.name_key == key)
        row = found[0] if found else None
    if row is None:
        names = [site['name'] for site in active_sites(s)]
        raise BookflowError('E_VALIDATION', message=(
            f'No inventory site is called "{text}". The company\'s sites are: {", ".join(names)}.'),
            details={'field': field, 'selector': text, 'sites': names})
    if active and not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': 'inventory_site', 'record_id': row['id'],
                                                             'field': field})
    return row


def site_id_for(s, selector, *, field='site'):
    """The id of the site a line or header names, or None when it names none."""
    if selector is None or str(selector).strip() == '':
        return None
    return resolve_site(s, selector, field=field)['id']


# ---------------------------------------------------------------- placements of movements

def movement_placements(s, movement_ids):
    """Stored placements of the given movements, ``{movement_id: [row, ...]}``."""
    ids = [movement_id for movement_id in movement_ids if movement_id]
    out = {}
    if not ids:
        return out
    t = c.inventory_placements
    for start in range(0, len(ids), 500):
        for row in s.company.conn.execute(sa.select(t).where(t.c.movement_id.in_(ids[start:start + 500]))
                                          .order_by(t.c.sequence, t.c.id)).mappings():
            out.setdefault(row['movement_id'], []).append(dict(row))
    return out


def site_of_movement(s, movement_id):
    """The site a stored movement's units are at: its largest placement's (a sale returns there)."""
    rows = movement_placements(s, [movement_id]).get(movement_id) or []
    if not rows:
        return None
    return max(rows, key=lambda row: abs(int(row['quantity_microunits'])))['site_id']


def for_movements(s, rows, sites=None):
    """The placement rows, without provenance, for movement rows about to be written.

    ``sites`` maps a movement id to the site it is placed at; a movement it does not name is placed
    at the default site. A reversal places nothing of its own: it retires exactly the placements of
    the movement it reverses, each one negated and linked by ``reverses_placement_id``. A movement
    that moves no quantity (a value change, a recost) has no placements.
    """
    sites = sites or {}
    default = None
    reversed_ids = [row['reverses_movement_id'] for row in rows
                    if row['kind'] == 'reversal' and int(row['quantity_microunits'])]
    stored = movement_placements(s, reversed_ids)
    made = {}
    out = []
    for row in rows:
        quantity = int(row['quantity_microunits'])
        if not quantity:
            continue
        base = dict(item_id=row['item_id'], bin_id=None, lot_id=None, serial_number_id=None,
                    effective_date=row['effective_date'], count_position='before', sequence=row['sequence'],
                    source='movement', movement_id=row['id'], transfer_revision_id=None, source_line_id=None)
        if row['kind'] == 'reversal':
            originals = stored.get(row['reverses_movement_id']) or made.get(row['reverses_movement_id'])
            if not originals:
                raise BookflowError('E_INTERNAL', message=(
                    'A stock reversal names a movement that has no placements to retire.'))
            own = [dict(base, id=new_id(), site_id=original['site_id'], bin_id=original['bin_id'],
                        lot_id=original['lot_id'], serial_number_id=original['serial_number_id'],
                        quantity_microunits=-int(original['quantity_microunits']),
                        reverses_placement_id=original['id']) for original in originals]
        else:
            site = sites.get(row['id'])
            if site is None:
                default = default or default_site_id(s)
                site = default
            own = [dict(base, id=new_id(), site_id=site, quantity_microunits=quantity, reverses_placement_id=None)]
        if sum(int(p['quantity_microunits']) for p in own) != quantity:
            raise BookflowError('E_INTERNAL', message='A movement\'s placements do not sum to its quantity.')
        made[row['id']] = own
        out.extend(own)
    return out


def with_provenance(placements, *, created_at, created_by, created_via, audit_event_id):
    return [dict(row, created_at=created_at, created_by=created_by, created_via=created_via,
                 audit_event_id=audit_event_id) for row in placements]


def placements_for_rows(s, movement_rows, sites=None, placements=None):
    """Placement rows, with each movement's own provenance, for movement rows ready to insert.

    ``placements`` are the rows ``for_movements`` already worked out at plan time (so a preview and
    its write agree); any movement they do not cover is placed now.
    """
    planned = {}
    for row in placements or ():
        planned.setdefault(row['movement_id'], []).append(row)
    missing = [row for row in movement_rows if int(row['quantity_microunits']) and row['id'] not in planned]
    for row in for_movements(s, missing, sites):
        planned.setdefault(row['movement_id'], []).append(row)
    out = []
    for row in movement_rows:
        for placement in planned.get(row['id'], ()):
            out.append(dict(placement, effective_date=row['effective_date'], sequence=row['sequence'],
                            created_at=row['created_at'], created_by=row['created_by'],
                            created_via=row['created_via'], audit_event_id=row['audit_event_id']))
    return out


def extra(rows):
    """The ``persist_prepared`` extra entry for placement rows; it must come before the movements'."""
    return ('inventory_placements', 'inventory_placement', 'id', rows)


def insert(s, rows):
    if rows:
        s.company.conn.execute(c.inventory_placements.insert(), rows)


# ---------------------------------------------------------------- readings

def on_hand(s, as_of=None, *, item_ids=None, site_id=None):
    """Quantity by ``(item_id, site_id)`` from placements dated by ``as_of``."""
    t = c.inventory_placements
    query = sa.select(t.c.item_id, t.c.site_id, sa.func.sum(t.c.quantity_microunits)).group_by(t.c.item_id, t.c.site_id)
    if as_of is not None:
        query = query.where(t.c.effective_date <= as_of)
    if item_ids is not None:
        query = query.where(t.c.item_id.in_(list(item_ids)))
    if site_id is not None:
        query = query.where(t.c.site_id == site_id)
    return {(item, site): int(quantity) for item, site, quantity in s.company.conn.execute(query) if int(quantity)}


def by_item(readings, site_id=None):
    """``{item_id: {site_id: quantity}}`` from an ``on_hand`` reading."""
    out = {}
    for (item, site), quantity in readings.items():
        if site_id is None or site == site_id:
            out.setdefault(item, {})[site] = quantity
    return out


_TIE = """
WITH placed AS (SELECT item_id, sum(quantity_microunits) AS q FROM inventory_placements
                WHERE effective_date <= :as_of GROUP BY item_id),
     moved AS (SELECT item_id, sum(quantity_microunits) AS q FROM inventory_movements
               WHERE effective_date <= :as_of GROUP BY item_id)
SELECT coalesce(p.item_id, m.item_id) AS item_id, coalesce(p.q, 0) AS placed, coalesce(m.q, 0) AS moved
FROM placed p LEFT JOIN moved m ON m.item_id = p.item_id WHERE coalesce(p.q, 0) != coalesce(m.q, 0)
UNION ALL
SELECT m.item_id, 0, m.q FROM moved m WHERE m.q != 0 AND m.item_id NOT IN (SELECT item_id FROM placed)
"""


def tie_gaps(s, as_of):
    """Items whose placements and movements disagree on ``as_of``: ``[(item_id, placed, moved)]``."""
    return [(row[0], int(row[1]), int(row[2])) for row in s.company.conn.execute(sa.text(_TIE), {'as_of': as_of})]


def check_tie(s, as_of):
    """Refuse a reading by site that the movement ledger contradicts (the quantity tie)."""
    gaps = tie_gaps(s, as_of)
    if not gaps:
        return
    from bookflow.company import inventory
    item_id, placed, moved = gaps[0]
    name = inventory.item_name(s, item_id)
    raise BookflowError('E_INTERNAL', message=(
        f'The placements of "{name}" hold {format_quantity_micro_units(placed)} on {as_of} and its movements '
        f'hold {format_quantity_micro_units(moved)}. Stock by site cannot be read until they agree.'),
        details={'item_id': item_id, 'item_name': name, 'as_of': as_of, 'placed_microunits': placed,
                 'moved_microunits': moved, 'difference_microunits': placed - moved})


def value_split(value, quantities, ordered_site_ids, default_site):
    """An item's value by site: shares of ``value`` by quantity, sites in name order, summing exactly.

    The shares are of the sites holding stock: a site below zero holds no value (its units are owed,
    not held), and an item no site holds any of shows its whole value at the default site.
    """
    total = sum(quantity for quantity in quantities.values() if quantity > 0)
    if total <= 0:
        return {default_site: value} if value else {}
    out, start = {}, 0
    for site in ordered_site_ids:
        quantity = quantities.get(site, 0)
        if quantity <= 0:
            continue
        out[site] = endpoint_share(value, total, start, start + quantity)
        start += quantity
    return out


# ---------------------------------------------------------------- warnings

def warnings(s, placements, names=None):
    """A site taken below zero by these placements says so, and names where else the item is.

    Spoken only in a company with more than one site; a one-site company's below-zero warning is
    the item's own, which the cost replay already gives.
    """
    touched = {(row['item_id'], row['site_id']) for row in placements if int(row['quantity_microunits']) < 0}
    if not touched or not multi_site(s):
        return []
    items = {item for item, _ in touched}
    stock = on_hand(s, item_ids=items)
    for row in placements:
        key = (row['item_id'], row['site_id'])
        stock[key] = stock.get(key, 0) + int(row['quantity_microunits'])
    site_name = site_names(s)
    from bookflow.company import inventory
    out = []
    for item, site in sorted(touched):
        held = stock.get((item, site), 0)
        if held >= 0:
            continue
        label = (names or {}).get(item) or inventory.item_name(s, item)
        elsewhere = sorted(((site_name[other], quantity) for (each, other), quantity in stock.items()
                            if each == item and other != site and quantity > 0))
        where = '; '.join(f'{name} holds {format_quantity_micro_units(quantity)}' for name, quantity in elsewhere)
        out.append(f'{site_name[site]} now shows {format_quantity_micro_units(held)} {label}; '
                   + (where if where else 'no other site holds any') + '.')
    return out


# ---------------------------------------------------------------- per-site settings

def site_settings(s, item_id):
    t = c.item_site_settings
    return {row['site_id']: dict(row) for row in s.company.conn.execute(
        sa.select(t).where(t.c.item_id == item_id)).mappings()}


def plan_site_settings(s, ctx, item, entries):
    """``[(before, after)]`` rows for ``item update --site-settings``; unchanged entries are dropped."""
    from bookflow.company import inventory
    from bookflow.core import clock
    from bookflow.core.exact import parse_quantity_micro_units
    from bookflow.hub.users import common
    if item['type'] not in inventory.TRACKED_TYPES:
        raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'site_settings', 'problem': (
            'only inventory parts and assemblies carry stock to reorder by site')}]})
    current = site_settings(s, item['id'])
    out, seen = [], set()
    for index, entry in enumerate(entries):
        field = f'site_settings.{index}'
        site = resolve_site(s, entry.site, field=field + '.site', active=False)
        if site['id'] in seen:
            raise BookflowError('E_VALIDATION', details={'fields': [{'field': field + '.site', 'problem': 'name each site once'}]})
        seen.add(site['id'])
        values = {}
        for key in ('reorder_point_min', 'reorder_point_max'):
            text = getattr(entry, key)
            values[key + '_microunits'] = None if text is None else parse_quantity_micro_units(text, field=f'{field}.{key}')
            if values[key + '_microunits'] is not None and values[key + '_microunits'] < 0:
                raise BookflowError('E_VALIDATION', details={'fields': [{'field': f'{field}.{key}', 'problem': 'must not be negative'}]})
        low, high = values['reorder_point_min_microunits'], values['reorder_point_max_microunits']
        if low is not None and high is not None and high < low:
            raise BookflowError('E_VALIDATION', details={'fields': [{'field': f'{field}.reorder_point_max', 'problem': 'must be at least the minimum'}]})
        before = current.get(site['id'])
        at = clock.now_iso()
        if before is None:
            after = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, at), item_id=item['id'],
                         site_id=site['id'], default_bin_id=None, **values)
        else:
            if all(before[key] == value for key, value in values.items()):
                continue
            after = dict(before, **values, version=before['version'] + 1, updated_at=at, updated_by=s.actor.id,
                         updated_via=ctx.interface.value)
        out.append((before, after))
    return out


def apply_site_settings(s, rows):
    from bookflow.core.registry import Touched
    t = c.item_site_settings
    touched = []
    for before, after in rows:
        if before is None:
            s.company.conn.execute(t.insert().values(**after))
            touched.append(Touched('item_site_setting', after['id'], 'create', None, 1, after, db='company'))
        else:
            if s.company.conn.execute(t.update().where(t.c.id == before['id'], t.c.version == before['version'])
                                      .values(**after)).rowcount != 1:
                raise BookflowError('E_VERSION_CONFLICT', details={'record_id': before['id']})
            touched.append(Touched('item_site_setting', after['id'], 'update', before['version'], after['version'],
                                   after, before, db='company'))
    return touched
