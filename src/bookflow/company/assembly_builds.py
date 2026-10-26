"""Assembly builds: building a finished item from its bill of materials (R103).

A build consumes each component -- its quantity per assembly times the build quantity -- at that
component's weighted-average cost on the build date, and receives the assembly at the sum of what it
consumed. As in the anchor, a non-stock component (a service, non-inventory part or other charge) on
the bill of materials adds its purchase cost to the assembly, crediting its own expense account; nothing
else is added.

## How a build posts

A build is a journal entry the inventory ledger owns, the way an inventory adjustment is. Line one
debits the assembly's inventory asset account for the assembly cost and carries the assembly's
``receipt``; each stock component's line credits its own inventory asset account and carries its
``issue``; a non-stock component's line credits its expense account. Every inventory-asset line is
claimed by exactly one movement, so stock status, inventory valuation and the balance sheet keep
telling the same story. When the components and the assembly share one inventory asset account (the
usual case) the lines net to nothing on that account -- the general ledger does not move -- and where
the accounts differ the entry is the balanced transfer between them.

## When a component is later recosted

A purchase entered behind a build changes what the build's components cost. The replay owes the
component's issue a dated correction, as it owes a sale one -- but the other side of a build's issue is
not cost of goods sold, it is the assembly. So ``replay_items`` pairs every correction of a build's
issue with an equal and opposite cost correction of the build's receipt of the assembly, on the same
date and in the same correction entry, and then replays the assembly itself, components before the
assemblies built from them: the assembly's own sales and the builds it went into are recosted in turn.
Every path that recalculates stock (documents through ``inventory_effects.plan`` and adjustments
through ``inventory``) goes through it.

A change that would leave a build short of a component on its own date is refused, naming the build:
a build consumes stock on hand, so its cost is never provisional.

## Pending builds

When the components on hand are short of the build, the anchor offers to mark it pending: nothing posts
and the components are reserved for it. ``assembly finalize`` builds it when they are there. A pending
build has no journal entry; its revisions alone say what it is.
"""
from __future__ import annotations

import json

import sqlalchemy as sa

from bookflow.core import unit_price
from bookflow.company import inventory, inventory_effects, items as item_service, journals, placements, schema as c
from bookflow.company.assembly_build_models import (
    AssemblyBuildListOutput, AssemblyBuildListRow, AssemblyBuildOutput, AssemblyComponentOutput,
    AssemblyCorrectionOutput,
)
from bookflow.company.inventory_costing import MICRO, StockRefusal, replay
from bookflow.company.journal_models import JournalPostInput, JournalUpdateInput, JournalVoidInput
from bookflow.company.ledger_reports import money
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import (
    convert_quantity_micro_units, format_quantity_micro_units, parse_quantity_micro_units,
    round_ratio_half_even,
)
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.registry import Applied, Plan, Touched

KIND = 'assembly build'


def _invalid(field, problem, message=None, **details):
    return BookflowError('E_VALIDATION', message=message,
                         details={'fields': [{'field': field, 'problem': problem}], **details})


# ---------------------------------------------------------------- the recalculation hook


def build_transactions(s):
    """Every journal entry that is, or was, a posted build."""
    t = c.assembly_builds
    return set(s.company.conn.execute(sa.select(t.c.transaction_id).where(
        t.c.transaction_id.is_not(None)).distinct()).scalars())


def is_build(s, transaction_id):
    t = c.assembly_builds
    return s.company.conn.execute(sa.select(t.c.id).where(
        t.c.transaction_id == transaction_id).limit(1)).first() is not None


def _edges(s, exclude=None):
    """Assembly -> the stock components its standing (built) builds consumed."""
    t = c.assembly_builds
    latest = sa.select(t.c.build_id, sa.func.max(t.c.revision_number).label('n')).group_by(t.c.build_id).subquery()
    edges: dict[str, set] = {}
    for build_id, item_id, components in s.company.conn.execute(
            sa.select(t.c.build_id, t.c.item_id, t.c.components).join(latest, sa.and_(
                latest.c.build_id == t.c.build_id, latest.c.n == t.c.revision_number)).where(t.c.status == 'built')):
        if build_id != exclude:
            edges.setdefault(item_id, set()).update(
                row['item_id'] for row in json.loads(components) if row['kind'] == 'stock')
    return edges


def _require_no_cycle(s, item, components, exclude=None):
    """Refuse a build that would consume an item its own assembly went into.

    Build A from B and, after re-nesting the bills of materials, B from A, and neither one's cost can be
    settled before the other's: a purchase entered behind either would have no order to recost them in.
    """
    edges = _edges(s, exclude)
    for component in components:
        if component['kind'] != 'stock':
            continue
        seen, stack = set(), [component['item_id']]
        while stack:
            current = stack.pop()
            if current == item['id']:
                name = inventory.item_name(s, component['item_id'])
                raise _invalid('item', f'"{name}" was built from "{item["full_name"]}"', message=(
                    f'"{name}" has been built from "{item["full_name"]}", directly or through other assemblies, so '
                    f'"{item["full_name"]}" cannot also be built from it: neither cost could be settled before the '
                    f'other. Remove "{name}" from the bill of materials, or void the builds that made it from '
                    f'"{item["full_name"]}" first.'), item_id=component['item_id'], reason='assembly_cycle')
            if current not in seen:
                seen.add(current)
                stack.extend(edges.get(current, ()))


def _depths(s):
    """How deep each built assembly sits: 1 + the deepest component any standing build of it consumed."""
    edges = _edges(s)
    memo: dict[str, int] = {}

    def depth(item_id, seen=()):
        if item_id in memo:
            return memo[item_id]
        if item_id not in edges or item_id in seen:
            return 0
        memo[item_id] = 1 + max((depth(child, (*seen, item_id)) for child in edges[item_id]), default=0)
        return memo[item_id]
    return depth


def replay_items(s, working, load, refuse):
    """Replay every item in ``working``, components before the assemblies built from them.

    ``working`` maps item id to its movement rows, planned ones included; ``load(item_id)`` returns
    (creating if needed) that list for an item not yet in it; ``refuse(refusal, item_id)`` turns a
    ``StockRefusal`` into the caller's error. Returns the ``(item_id, Replay)`` pairs in the order they
    were replayed, and ``partners``: for each correction of a build's component issue, keyed by (issue id,
    filling movement id or None), the cost correction -- or, for a landed cost's forward true-up, the landed
    cost -- of that build's assembly receipt that carries the other side of it.
    """
    builds = build_transactions(s)
    order, partners = [], {}
    if not builds:
        for item_id in list(working):
            try:
                order.append((item_id, replay(working[item_id])))
            except StockRefusal as refusal:
                raise refuse(refusal, item_id) from None
        return order, partners
    depth = _depths(s)
    receipts: dict[tuple, dict] = {}
    waiting, done = set(working), set()
    while waiting:
        item_id = min(waiting, key=lambda key: (depth(key), key))
        waiting.discard(item_id)
        history = load(item_id)
        try:
            state = replay(history)
        except StockRefusal as refusal:
            raise refuse(refusal, item_id) from None
        issues = {row['id']: row for row in history
                  if row['kind'] == 'issue' and row.get('transaction_id') in builds}
        for shortfall in state.shortfalls:
            if shortfall.movement['id'] in issues:
                row = shortfall.movement
                raise BookflowError('E_VALIDATION', message=(
                    f'This change would leave the assembly build dated {row["effective_date"]} short of '
                    f'"{inventory.item_name(s, item_id)}": a build consumes stock on hand, so it cannot be '
                    'left below zero. Void or edit that build first, or date this change after it.'),
                    details={'fields': [{'field': 'date', 'problem': 'would leave an earlier build short of a component'}],
                             'item_id': item_id, 'build_transaction_id': row['transaction_id'],
                             'effective_date': row['effective_date'], 'reason': 'build_left_short'})
        for correction in state.corrections:
            target = correction.target_movement
            if target['id'] not in issues:
                continue
            key = (target['transaction_id'], target['revision_id'])
            if key not in receipts:
                receipts[key] = next(row for row in inventory.movements(s, transaction_id=target['transaction_id'])
                                     if row['kind'] == 'receipt' and row['revision_id'] == target['revision_id'])
            receipt = receipts[key]
            if receipt['item_id'] in done:
                raise BookflowError('E_VALIDATION', message=(
                    'Assembly builds consume one another in a cycle, so the cost of one cannot be settled before the '
                    'other. Void one of the builds that made an assembly from an item it was itself built into, then '
                    'enter this change again.'), details={'reason': 'assembly_cycle', 'item_id': receipt['item_id']})
            # A landed cost's forward true-up of a component issue (R104 increment 6) is carried by a landed
            # cost of the assembly receipt at the same date, so the assembly's own forward true-ups follow;
            # any other correction by a cost correction of the assembly receipt, at the receipt's position.
            landed = correction.filled_by is not None and correction.filled_by['kind'] == 'landed'
            partner = dict(
                id=new_id(), item_id=receipt['item_id'], kind='landed' if landed else 'recost', quantity_microunits=0,
                value_minor_units=-correction.delta_minor_units, effective_date=correction.effective_date,
                sequence=0, currency=receipt['currency'], asset_account_id=receipt['asset_account_id'],
                offset_account_id=receipt['offset_account_id'], class_id=receipt['class_id'],
                corrects_movement_id=None if landed else receipt['id'], reverses_movement_id=None,
                returns_movement_id=None, landed_on_movement_id=receipt['id'] if landed else None,
                filled_by_movement_id=None, fallback_unit_cost_minor_units=None, fallback_unit_cost_fraction=None,
                count_position=receipt.get('count_position') or 'before')
            load(receipt['item_id']).append(partner)
            partners[(target['id'], correction.filled_by['id'] if correction.filled_by is not None else None)] = partner
            waiting.add(receipt['item_id'])
        order.append((item_id, state))
        done.add(item_id)
    return order, partners


# ---------------------------------------------------------------- reading


def _home(s):
    return s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar_one()


def _revisions(s, build_id):
    t = c.assembly_builds
    return [dict(row) for row in s.company.conn.execute(
        sa.select(t).where(t.c.build_id == build_id).order_by(t.c.revision_number)).mappings()]


def resolve(s, selector):
    """The current revision of the build this selector names: its id, or the journal number it posted as."""
    t = c.assembly_builds
    key = selector.upper() if is_ulid(selector) else selector
    build_id = s.company.conn.execute(sa.select(t.c.build_id).where(t.c.build_id == key).limit(1)).scalar()
    if build_id is None:
        build_id = s.company.conn.execute(
            sa.select(t.c.build_id).join(c.transactions, c.transactions.c.id == t.c.transaction_id).where(
                c.transactions.c.number == selector).order_by(t.c.revision_number.desc()).limit(1)).scalar()
    if build_id is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'assembly_build', 'selector': selector})
    return _revisions(s, build_id)[-1]


def _check_version(current, expected):
    if expected is not None and expected != current['revision_number']:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': 'assembly_build', 'record_id': current['build_id'],
            'expected_version': expected, 'current_version': current['revision_number']})


def reserved(s, *, exclude=None, as_of=None):
    """Component quantities reserved by pending builds, by item id (the anchor's 'pending builds').

    With `as_of`, only builds dated on or before it count, as a report of that date reads open orders.
    """
    t = c.assembly_builds
    latest = sa.select(t.c.build_id, sa.func.max(t.c.revision_number).label('n')).group_by(t.c.build_id).subquery()
    rows = s.company.conn.execute(sa.select(t).join(latest, sa.and_(
        latest.c.build_id == t.c.build_id, latest.c.n == t.c.revision_number)).where(t.c.status == 'pending')).mappings()
    held: dict[str, int] = {}
    for row in rows:
        if row['build_id'] == exclude or (as_of is not None and row['build_date'] > as_of):
            continue
        for component in json.loads(row['components']):
            if component['kind'] == 'stock':
                need = _need(component['quantity_microunits'], row['quantity_microunits'])
                held[component['item_id']] = held.get(component['item_id'], 0) + need
    return held


def reserved_by_site(s, *, as_of=None):
    """``reserved`` by ``(item_id, site_id)``: a pending build reserves its components at its site (R104)."""
    t = c.assembly_builds
    latest = sa.select(t.c.build_id, sa.func.max(t.c.revision_number).label('n')).group_by(t.c.build_id).subquery()
    rows = s.company.conn.execute(sa.select(t).join(latest, sa.and_(
        latest.c.build_id == t.c.build_id, latest.c.n == t.c.revision_number)).where(t.c.status == 'pending')).mappings()
    default = None
    held: dict[tuple, int] = {}
    for row in rows:
        if as_of is not None and row['build_date'] > as_of:
            continue
        site = row['site_id']
        if site is None:
            default = default or placements.default_site_id(s)
            site = default
        for component in json.loads(row['components']):
            if component['kind'] == 'stock':
                key = (component['item_id'], site)
                held[key] = held.get(key, 0) + _need(component['quantity_microunits'], row['quantity_microunits'])
    return held


# ---------------------------------------------------------------- the bill of materials


def _need(per, quantity):
    return round_ratio_half_even(int(per) * int(quantity), MICRO)


def _assembly(s, selector):
    item = item_service.resolve_item(s.company, selector)
    if item['type'] != 'inventory_assembly':
        raise _invalid('item', f'a {item["type"].replace("_", " ")} is not an inventory assembly; only an '
                               'inventory assembly is built from components')
    if not item['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': 'item', 'record_id': item['id']})
    if item['asset_account_id'] is None:
        raise _invalid('item', 'this assembly has no inventory asset account')
    return item


def capture(s, item, currency):
    """The assembly's bill of materials now, in base units, as a build records it."""
    m = c.item_members
    members = [dict(row) for row in s.company.conn.execute(sa.select(m).where(
        m.c.owner_item_id == item['id'], m.c.active.is_(True)).order_by(m.c.position)).mappings()]
    captured = []
    for member in members:
        component = item_service.resolve_item(s.company, member['component_item_id'])
        per = int(member['quantity_microunits'])
        if member['unit_id'] is not None:
            factor = s.company.conn.execute(sa.select(c.unit_conversions.c.base_factor_nanounits).where(
                c.unit_conversions.c.id == member['unit_id'])).scalar_one()
            per = convert_quantity_micro_units(per, int(factor), 10 ** 9)
        if component['type'] in inventory.TRACKED_TYPES:
            if component['asset_account_id'] is None:
                raise _invalid('item', f'component "{component["full_name"]}" has no inventory asset account')
            captured.append(dict(item_id=component['id'], kind='stock', quantity_microunits=per,
                                 account_id=component['asset_account_id']))
        else:
            # R150: the exact cost, so 0.004 is a cost (rounded alone it would be 0 cents).
            cost = unit_price.row_units(component, 'cost')
            usable = cost is not None and component.get('cost_currency') == currency and cost > 0 \
                and component.get('expense_account_id') is not None
            minor, fraction = unit_price.split(cost) if usable else (0, None)
            row = dict(item_id=component['id'], kind='cost', quantity_microunits=per,
                       unit_cost_minor_units=minor,
                       account_id=component.get('expense_account_id') if usable else None)
            if fraction:
                row['unit_cost_fraction'] = fraction  # only when sub-cent, so whole-cent captures keep their shape
            captured.append(row)
    if not any(row['kind'] == 'stock' or unit_price.row_units(row, 'unit_cost') for row in captured):
        raise _invalid('item', 'this assembly has no components with stock or a cost to build it from; '
                               'add its bill of materials with `item update --members`')
    return captured


def _quantity(text, field='quantity'):
    value = parse_quantity_micro_units(text, field=field)
    if value <= 0:
        raise _invalid(field, 'must be greater than zero')
    return value


# ---------------------------------------------------------------- planning one posting


class _Build:
    """Everything one build implies, decided before anything is written."""

    def __init__(self, item, components, quantity, date, currency):
        self.item, self.components, self.quantity, self.date, self.currency = item, components, quantity, date, currency
        self.rows = []          # one per component, for the output
        self.max_buildable = 0
        self.shortfall = False
        self.change = None
        self.lines = []
        self.amounts = []       # (account, signed amount) per line
        self.line_of = {}       # entry key -> line position
        self.total = 0
        self.on_hand_after = 0
        self.site_id = None     # the inventory site it takes from and puts at (R104); None is the default


def _on_hand(history, date):
    return sum(int(row['quantity_microunits']) for row in history if row['effective_date'] <= date)


def build_numbers(s, inp):
    """``{'assembly': Numbers, component item id: Numbers}`` a build input names (R104 increment 4)."""
    from bookflow.company import serial_lots
    out = {}
    numbers = serial_lots.from_line(inp)
    if numbers is not None:
        out['assembly'] = numbers
    for index, entry in enumerate(getattr(inp, 'components', None) or ()):
        component = item_service.resolve_item(s.company, entry.item)
        stated = serial_lots.from_line(entry)
        if stated is not None:
            out[component['id']] = stated
    return out


def _plan(s, item, components, quantity, date, *, reversing=(), exclude=None, site_id=None, numbers=None):
    from bookflow.company import serial_lots
    numbers = numbers or {}
    currency = _home(s)
    build = _Build(item, components, quantity, date, currency)
    build.site_id = site_id
    planned_reversals = {}
    for row in reversing:
        planned_reversals.setdefault(row['item_id'], []).append(
            dict(row, quantity_microunits=-int(row['quantity_microunits']), value_minor_units=-int(row['value_minor_units'])))
    held = reserved(s, exclude=exclude)
    names = {}
    limits = []
    for component in components:
        component_item = item_service.resolve_item(s.company, component['item_id'])
        names[component['item_id']] = component_item['full_name']
        need = _need(component['quantity_microunits'], quantity)
        entry = dict(component=component, item=component_item, need=need)
        if component['kind'] == 'stock':
            history = inventory.movements(s, item_id=component['item_id']) + planned_reversals.get(component['item_id'], [])
            have = _on_hand(history, date)
            entry.update(on_hand=have, reserved=held.get(component['item_id'], 0))
            per = int(component['quantity_microunits'])
            if per > 0:
                allows = max(have, 0) * MICRO // per
                entry['max'] = allows
                limits.append(allows)
            if need > have:
                build.shortfall = True
        build.rows.append(entry)
    build.max_buildable = min(limits) if limits else quantity
    history = inventory.movements(s, item_id=item['id']) + planned_reversals.get(item['id'], [])
    build.on_hand_after = _on_hand(history, date)
    if build.shortfall:
        return build
    _require_no_cycle(s, item, components, exclude)
    issues = [inventory_effects.Entry(
        key=f'c{index}', item_id=entry['item']['id'], item_name=entry['item']['full_name'], kind='issue',
        quantity_microunits=entry['need'], asset_account_id=entry['component']['account_id'],
        offset_account_id=item['asset_account_id'], site_id=site_id, numbers=numbers.get(entry['item']['id']))
        for index, entry in enumerate(build.rows) if entry['component']['kind'] == 'stock' and entry['need'] > 0]
    # The first pass prices the components. It carries the assembly's receipt too, at a provisional value
    # (what the build being restated received, else nothing): without it, a later build that consumed this
    # assembly would read as short while the receipt it relies on is reversed and not yet replaced.
    provisional = sum(int(row['value_minor_units']) for row in reversing if row['kind'] == 'receipt'
                      and row['item_id'] == item['id'])
    placeholder = inventory_effects.Entry(key='assembly', item_id=item['id'], item_name=item['full_name'],
                                          kind='receipt', quantity_microunits=quantity,
                                          asset_account_id=item['asset_account_id'],
                                          offset_account_id=item['asset_account_id'], value_minor_units=max(provisional, 0),
                                          site_id=site_id)
    first = inventory_effects.plan(s, entries=issues + [placeholder], reversing=reversing, date=date, currency=currency,
                                   field='quantity')
    total = 0
    for index, entry in enumerate(build.rows):
        if entry['component']['kind'] == 'stock':
            cost = -first.costs.get(f'c{index}', 0)
            if entry['need'] > 0 and cost == 0:
                # A small issue of a cheap part can round to nothing at the average; that is a real cost of
                # zero and the issue moves quantity alone. Only stock worth nothing at all is refused.
                history = inventory.movements(s, item_id=entry['item']['id']) + planned_reversals.get(entry['item']['id'], [])
                worth = sum(int(row['value_minor_units']) for row in history if row['effective_date'] <= date)
                if worth <= 0:
                    raise _invalid('item', 'has no value on hand', message=(
                        f'Component "{entry["item"]["full_name"]}" is on hand on {date} but carries no value at all, so '
                        'there is no cost to move into the assembly. Enter the bill that bought it, or record what the '
                        'stock is worth with `inventory adjust --value-change`, then build.'),
                        item_id=entry['item']['id'], reason='component_without_value')
        else:
            cost = unit_price.extension(entry['need'], unit_price.row_units(entry['component'], 'unit_cost'))
        entry['cost'] = cost
        total += cost
    if total <= 0:
        raise _invalid('quantity', 'the components of this build cost nothing, and Bookflow posts no zero-amount entry')
    stocked = [entry for entry in build.rows if entry['component']['kind'] == 'stock']
    offset = stocked[0]['component']['account_id'] if stocked else next(
        entry['component']['account_id'] for entry in build.rows if entry['cost'])
    receipt = inventory_effects.Entry(key='assembly', item_id=item['id'], item_name=item['full_name'], kind='receipt',
                                      quantity_microunits=quantity, asset_account_id=item['asset_account_id'],
                                      offset_account_id=offset, value_minor_units=total, site_id=site_id,
                                      numbers=numbers.get('assembly'))
    build.change = inventory_effects.plan(s, entries=issues + [receipt], reversing=reversing, date=date,
                                          currency=currency, field='quantity')
    for index, entry in enumerate(build.rows):
        if entry['component']['kind'] == 'stock' and build.change.costs.get(f'c{index}', 0) != -entry['cost']:
            raise BookflowError('E_INTERNAL', message='A build component was costed two ways.')
    from bookflow.company.journal_models import JournalLineInput
    from bookflow.core.money import Money
    description = f'Build {format_quantity_micro_units(quantity)} {item["full_name"]}'

    def line(account, side, amount, text):
        build.amounts.append((account, amount if side == 'debit' else -amount))
        return JournalLineInput(account=account, side=side, amount=Money(amount, currency).to_dict(), description=text)
    build.lines = [line(item['asset_account_id'], 'debit', total, description)]
    build.line_of['assembly'] = 1
    for index, entry in enumerate(build.rows):
        if not entry['cost']:
            continue
        build.lines.append(line(entry['component']['account_id'], 'credit', entry['cost'],
                                f'{format_quantity_micro_units(entry["need"])} {entry["item"]["full_name"]} into {item["full_name"]}'))
        build.line_of[f'c{index}'] = len(build.lines)
    build.total = total
    build.on_hand_after += quantity
    return build


def _posts_to_ledger(build):
    net = {}
    for account, amount in build.amounts:
        net[account] = net.get(account, 0) + amount
    return any(net.values())


def _short(build):
    rows = [dict(item_id=entry['item']['id'], item_name=entry['item']['full_name'],
                 needed=format_quantity_micro_units(entry['need']),
                 on_hand=format_quantity_micro_units(entry['on_hand']))
            for entry in build.rows if entry['component']['kind'] == 'stock' and entry['need'] > entry['on_hand']]
    names = ', '.join(f'{row["item_name"]} (needs {row["needed"]}, {row["on_hand"]} on hand)' for row in rows)
    return BookflowError('E_VALIDATION', message=(
        f'Not enough components on {build.date} to build {format_quantity_micro_units(build.quantity)} '
        f'{build.item["full_name"]}: {names}. The most you can build is '
        f'{format_quantity_micro_units(build.max_buildable)}. Build that many, or mark the build pending '
        '(`pending: true`) to reserve the components and finish it later with `assembly finalize`.'),
        details={'fields': [{'field': 'quantity', 'problem': 'more than the components on hand allow'}],
                 'reason': 'insufficient_components', 'max_buildable': format_quantity_micro_units(build.max_buildable),
                 'shortages': rows})


# ---------------------------------------------------------------- output


def _output(s, build, *, status, current=None, number=None, transaction_id=None, memo=None, corrections=(),
            warnings=(), history=()):
    currency = build.currency
    rows = []
    for entry in build.rows:
        component = entry['component']
        stock = component['kind'] == 'stock'
        rows.append(AssemblyComponentOutput(
            item_id=entry['item']['id'], item_name=entry['item']['full_name'], item_type=entry['item']['type'],
            kind=component['kind'], quantity_per=format_quantity_micro_units(component['quantity_microunits']),
            quantity_needed=format_quantity_micro_units(entry['need']),
            quantity_on_hand=format_quantity_micro_units(entry['on_hand']) if stock else None,
            reserved_for_pending_builds=format_quantity_micro_units(entry['reserved']) if stock else None,
            max_buildable=format_quantity_micro_units(entry['max']) if stock and 'max' in entry else None,
            cost=money(entry.get('cost', 0), currency), account_id=component['account_id'] or ''))
    total = build.total
    return AssemblyBuildOutput(
        build_id=current['build_id'] if current else None, version=current['revision_number'] if current else None,
        status=status, number=number, transaction_id=transaction_id, item_id=build.item['id'],
        item_name=build.item['full_name'], asset_account_id=build.item['asset_account_id'], date=build.date,
        quantity=format_quantity_micro_units(build.quantity), memo=memo,
        max_buildable=format_quantity_micro_units(build.max_buildable), shortfall=build.shortfall, components=rows,
        assembly_cost=money(total, currency),
        unit_cost=money(round_ratio_half_even(total * MICRO, build.quantity) if total else 0, currency),
        assembly_quantity_on_hand=format_quantity_micro_units(build.on_hand_after),
        posts_to_ledger=bool(build.lines) and _posts_to_ledger(build),
        corrections=[AssemblyCorrectionOutput(item_id=values['item_id'], effective_date=values['effective_date'],
                                              corrects_movement_id=values['corrects_movement_id'],
                                              delta=money(int(values['value_minor_units']), currency))
                     for values in corrections],
        reason=current.get('reason') if current else None, warnings=list(dict.fromkeys(warnings)),
        history=[dict(version=row['revision_number'], status=row['status'], date=row['build_date'],
                      quantity=format_quantity_micro_units(row['quantity_microunits']), memo=row['memo'],
                      reason=row['reason'], created_at=row['created_at'], created_by=row['created_by'],
                      created_via=row['created_via']) for row in history],
        site_id=(current or {}).get('site_id') or build.site_id,
        site_name=_site_name(s, (current or {}).get('site_id') or build.site_id))


def _site_name(s, site_id):
    return placements.site_names(s).get(site_id) if site_id else placements.default_site(s)['name']


def _correction_values(change):
    return [movement.values for correction in change.corrections for movement in correction.movements]


# ---------------------------------------------------------------- writing


def _revision(s, ctx, *, build_id, number, status, item, quantity, date, memo, transaction_id, components,
              event, at, site_id=None):
    return dict(id=new_id(), build_id=build_id, revision_number=number, status=status, item_id=item['id'],
                site_id=site_id,
                quantity_microunits=quantity, build_date=date, memo=memo,
                reason=(ctx.reason or '').strip() or None, transaction_id=transaction_id,
                components=json.dumps(components, sort_keys=True, separators=(',', ':')),
                created_at=at, created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)


def _write_revision(s, ctx, row, command_name, summary):
    touched = [Touched('assembly_build', row['id'], 'create', None, 1, row, db='company')]
    audit.write_event_to(s.company, ctx, command_name, summary, touched, actor_id=s.actor.id,
                         actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None),
                         event_id=row['audit_event_id'])
    s.company.conn.execute(c.assembly_builds.insert().values(**row))
    return touched


def _bind(build, fresh):
    pending = fresh.data['pending']
    header = fresh.data['header']
    revision = pending['transaction_revisions'][0] if pending['transaction_revisions'] else None
    entered = {line['position']: line for line in pending['document_lines']}
    legs = {line['line_no']: line for line in pending['posting_lines']
            if line['batch_id'] in {b['id'] for b in pending['posting_batches'] if b['kind'] != 'reversal'}}
    for movement in build.change.movements:
        if movement.key is None:
            continue
        if movement.key not in build.line_of:
            # A component issue that cost nothing has no posting line; it hangs off the build's batch and its
            # first entered line, as a zero-value movement does on any document.
            batch = next(b for b in pending['posting_batches'] if b['kind'] != 'reversal')
            inventory_effects.bind(movement, None, transaction_id=header['id'], revision_id=revision['id'],
                                   document_line_id=entered[1]['id'], batch=batch)
            continue
        position = build.line_of[movement.key]
        inventory_effects.bind(movement, legs[position], transaction_id=header['id'], revision_id=revision['id'],
                               document_line_id=entered[position]['id'])
    inventory_effects.bind_reversals(build.change, pending['posting_lines'], pending['posting_batches'])


def _post(s, ctx, build, journal, operation, *, command_name, revision_row, persist):
    """Post or restate the build's journal entry with its movements, then the corrections it owes."""
    fresh = journals.prepare(s, ctx, journal, operation, owner=inventory.OWNER)
    if not fresh.data['changed']:
        raise BookflowError('E_INTERNAL', message='An assembly build produced no accounting.')
    if build.change is not None:
        _bind(build, fresh)
        inventory_effects.check(s, build.change, fresh.data['pending']['posting_lines'])
    header = fresh.data['header']
    if not persist:
        return header, []
    at = clock.now_iso()
    event = fresh.data['event']
    row = dict(revision_row, transaction_id=header['id'], audit_event_id=event, created_at=at)
    movements = build.change.rows(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
                                  audit_event_id=event) if build.change is not None else []
    extra = [('assembly_builds', 'assembly_build', 'id', [row])]
    if movements:
        # Placed before the movements, which the database refuses unless their placements sum to them (R104).
        extra.extend(placements.extras(placements.placements_for_rows(
            s, movements, build.change.sites, build.change.placements)))
        extra.append(('inventory_movements', 'inventory_movement', 'id', movements))
    applied = journals.persist_prepared(fresh, ctx, s, command_name=command_name, extra=tuple(extra),
                                        noun='assembly build')
    touched = list(applied.touched)
    if build.change is not None:
        touched += inventory_effects.write_corrections(s, ctx, build.change, command_name=command_name, created_at=at)
    return header, touched


def _journal_version(s, current):
    return s.company.conn.execute(sa.select(c.transactions.c.version).where(
        c.transactions.c.id == current['transaction_id'])).scalar_one()


def _transaction_number(s, transaction_id):
    if transaction_id is None:
        return None
    return s.company.conn.execute(sa.select(c.transactions.c.number).where(
        c.transactions.c.id == transaction_id)).scalar()


def _void_build(s, current):
    """What voiding a posted build implies: its own movements reversed, and the corrections owed."""
    item = item_service.resolve_item(s.company, current['item_id'])
    components = json.loads(current['components'])
    currency = _home(s)
    build = _Build(item, components, int(current['quantity_microunits']), current['build_date'], currency)
    build.change = inventory_effects.plan(s, entries=[], reversing=inventory_effects.own_movements(s, current['transaction_id']),
                                          currency=currency, field='quantity')
    for component in components:
        component_item = item_service.resolve_item(s.company, component['item_id'])
        build.rows.append(dict(component=component, item=component_item,
                               need=_need(component['quantity_microunits'], build.quantity),
                               on_hand=_on_hand(inventory.movements(s, item_id=component['item_id']), build.date),
                               reserved=0, cost=0))
    build.max_buildable = 0
    build.total = sum(int(row['value_minor_units']) for row in inventory_effects.own_movements(s, current['transaction_id'])
                      if row['kind'] == 'receipt')
    build.on_hand_after = _on_hand(inventory.movements(s, item_id=item['id']), build.date) - build.quantity
    return build


def _require_open(s, *dates):
    journals.open_dates(s, sorted({date for date in dates if date}))


def _run(s, ctx, inp, operation, *, persist):
    """One build command, decided in full; written only when ``persist``."""
    command_name = 'assembly ' + operation
    at = clock.now_iso()
    if operation == 'build':
        item = _assembly(s, inp.item)
        quantity = _quantity(inp.quantity)
        currency = _home(s)
        components = capture(s, item, currency)
        _require_open(s, inp.date)
        site_id = placements.site_id_for(s, getattr(inp, 'site', None))
        build = _plan(s, item, components, quantity, inp.date, site_id=site_id, numbers=build_numbers(s, inp))
        if build.shortfall and not inp.pending:
            raise _short(build)
        build_id = new_id()
        if build.shortfall:
            row = _revision(s, ctx, build_id=build_id, number=1, status='pending', item=item, quantity=quantity,
                            date=inp.date, memo=inp.memo, transaction_id=None, components=components,
                            event=new_id(), at=at, site_id=site_id)
            if not persist:
                return _output(s, build, status='will_pend', memo=inp.memo), [], 'preview'
            touched = _write_revision(s, ctx, row, command_name, f'mark pending a build of {item["full_name"]}')
            return _output(s, build, status='pending', current=row, memo=inp.memo, history=[row]), touched, \
                f'pending build of {item["full_name"]}'
        row = _revision(s, ctx, build_id=build_id, number=1, status='built', item=item, quantity=quantity,
                        date=inp.date, memo=inp.memo, transaction_id=None, components=components, event=None, at=at,
                        site_id=site_id)
        journal = JournalPostInput(date=inp.date, memo=inp.memo, lines=build.lines)
        inventory_effects.open_dates(s, build.change)
        header, touched = _post(s, ctx, build, journal, 'post', command_name=command_name, revision_row=row,
                                persist=persist)
        if not persist:
            return _output(s, build, status='will_build', memo=inp.memo, number=header['number'],
                           corrections=_correction_values(build.change), warnings=build.change.warnings), [], 'preview'
        row = _revisions(s, build_id)[-1]
        return _output(s, build, status='built', current=row, number=header['number'], transaction_id=header['id'],
                       memo=inp.memo, corrections=_correction_values(build.change), warnings=build.change.warnings,
                       history=[row]), touched, f'build {item["full_name"]}'

    current = resolve(s, inp.build)
    _check_version(current, inp.expected_version)
    history = _revisions(s, current['build_id'])
    item = item_service.resolve_item(s.company, current['item_id'])
    components = json.loads(current['components'])
    status = current['status']
    if status == 'deleted':
        raise _invalid('build', 'this build was deleted')
    next_number = current['revision_number'] + 1

    if operation in ('update', 'finalize'):
        if operation == 'finalize' and status != 'pending':
            raise _invalid('build', f'this build is {status}, not pending; only a pending build is finalized')
        if status == 'voided':
            raise _invalid('build', 'a voided build cannot be edited; build again instead')
        quantity = _quantity(inp.quantity) if getattr(inp, 'quantity', None) is not None else int(current['quantity_microunits'])
        date = inp.date or current['build_date']
        memo = inp.memo if 'memo' in inp.model_fields_set else current['memo']
        _require_open(s, date, current['build_date'] if status == 'built' else None)
        reversing = inventory_effects.own_movements(s, current['transaction_id']) if status == 'built' else ()
        site_id = (placements.site_id_for(s, inp.site) if getattr(inp, 'site', None) is not None
                   else current.get('site_id'))
        build = _plan(s, item, components, quantity, date, reversing=reversing, exclude=current['build_id'],
                      site_id=site_id)
        if status == 'pending' and operation == 'update':
            row = _revision(s, ctx, build_id=current['build_id'], number=next_number, status='pending', item=item,
                            quantity=quantity, date=date, memo=memo, transaction_id=None, components=components,
                            event=new_id(), at=at, site_id=site_id)
            if not persist:
                return _output(s, build, status='pending', current=current, memo=memo, history=history), [], 'preview'
            touched = _write_revision(s, ctx, row, command_name, f'edit pending build of {item["full_name"]}')
            return _output(s, build, status='pending', current=row, memo=memo, history=history + [row]), touched, \
                f'edit pending build of {item["full_name"]}'
        if build.shortfall:
            raise _short(build)
        row = _revision(s, ctx, build_id=current['build_id'], number=next_number, status='built', item=item,
                        quantity=quantity, date=date, memo=memo, transaction_id=None, components=components,
                        event=None, at=at, site_id=site_id)
        inventory_effects.open_dates(s, build.change)
        if status == 'built':
            journal = JournalUpdateInput(journal=current['transaction_id'], expected_version=_journal_version(s, current), date=date, memo=memo, lines=build.lines)
            header, touched = _post(s, ctx, build, journal, 'update', command_name=command_name, revision_row=row,
                                    persist=persist)
        else:
            journal = JournalPostInput(date=date, memo=memo, lines=build.lines)
            header, touched = _post(s, ctx, build, journal, 'post', command_name=command_name, revision_row=row,
                                    persist=persist)
        shown = _revisions(s, current['build_id'])[-1] if persist else current
        return _output(s, build, status='built' if persist else 'will_build', current=shown, number=header['number'],
                       transaction_id=header['id'] if persist else current['transaction_id'], memo=memo,
                       corrections=_correction_values(build.change), warnings=build.change.warnings,
                       history=_revisions(s, current['build_id']) if persist else history), touched, \
            f'{"finalize" if operation == "finalize" else "edit"} build of {item["full_name"]}'

    # void and delete
    if not ctx.reason or not ctx.reason.strip():
        raise BookflowError('E_REASON_REQUIRED')
    if operation == 'void' and status != 'built':
        raise _invalid('build', f'this build is {status}; only a built build is voided'
                       + ('. A pending build posted nothing: delete it with `assembly delete`' if status == 'pending' else ''))
    new_status = 'voided' if operation == 'void' else 'deleted'
    if status == 'built':
        build = _void_build(s, current)
        inventory_effects.open_dates(s, build.change, [current['build_date']])
        row = _revision(s, ctx, build_id=current['build_id'], number=next_number, status=new_status, item=item,
                        quantity=int(current['quantity_microunits']), date=current['build_date'], memo=current['memo'],
                        transaction_id=current['transaction_id'], components=components, event=None, at=at,
                        site_id=current.get('site_id'))
        header, touched = _post(s, ctx, build, JournalVoidInput(journal=current['transaction_id'], expected_version=_journal_version(s, current)), 'void',
                                command_name=command_name, revision_row=row, persist=persist)
        number = header['number']
        corrections = _correction_values(build.change)
        warnings = build.change.warnings
    else:
        build = _plan(s, item, components, int(current['quantity_microunits']), current['build_date'],
                      exclude=current['build_id']) if status == 'pending' else _void_build(s, current)
        if status == 'voided':
            build.change = None
        row = _revision(s, ctx, build_id=current['build_id'], number=next_number, status=new_status, item=item,
                        quantity=int(current['quantity_microunits']), date=current['build_date'], memo=current['memo'],
                        transaction_id=current['transaction_id'], components=components, event=new_id(), at=at,
                        site_id=current.get('site_id'))
        touched = _write_revision(s, ctx, row, command_name, f'{operation} build of {item["full_name"]}') if persist else []
        number, corrections, warnings = _transaction_number(s, current['transaction_id']), [], []
    shown = _revisions(s, current['build_id']) if persist else history
    return _output(s, build, status=new_status, current=shown[-1], number=number,
                   transaction_id=current['transaction_id'], memo=current['memo'], corrections=corrections,
                   warnings=warnings, history=shown), touched, f'{operation} build of {item["full_name"]}'


def prepare(s, ctx, inp, operation):
    output, _, _ = _run(s, ctx, inp, operation, persist=False)
    return Plan(output, {'input': inp, 'operation': operation})


def apply(plan, ctx, s):
    # Rebuilt inside the writer transaction: stock, costs, dates and numbering are decisive only here.
    output, touched, summary = _run(s, ctx, plan.data['input'], plan.data['operation'], persist=True)
    return Applied(output, touched, summary, audited=True)


# ---------------------------------------------------------------- reads


def show(s, inp):
    current = resolve(s, inp.build)
    history = _revisions(s, current['build_id'])
    item = item_service.resolve_item(s.company, current['item_id'])
    components = json.loads(current['components'])
    if current['status'] == 'pending':
        build = _plan(s, item, components, int(current['quantity_microunits']), current['build_date'],
                      exclude=current['build_id'])
    else:
        build = _Build(item, components, int(current['quantity_microunits']), current['build_date'], _home(s))
        own = [row for row in inventory.movements(s, transaction_id=current['transaction_id'])
               if row['kind'] in ('receipt', 'issue') and current['transaction_id']] if current['transaction_id'] else []
        live = {row['id'] for row in inventory_effects.own_movements(s, current['transaction_id'])} if current['transaction_id'] else set()
        posted = {}
        for row in own:
            if row['id'] in live or current['status'] != 'built':
                posted.setdefault((row['item_id'], row['kind']), []).append(row)
        recosts = {}
        for row in inventory.movements(s, item_id=item['id']):
            if row['kind'] == 'recost':
                recosts[row['corrects_movement_id']] = recosts.get(row['corrects_movement_id'], 0) + int(row['value_minor_units'])
        for component in components:
            component_item = item_service.resolve_item(s.company, component['item_id'])
            rows = posted.get((component['item_id'], 'issue'), [])
            issue = rows[-1:]
            # What the issue cost now: its posted value and every cost correction posted against it since,
            # the same way the receipt side counts the assembly's.
            cost = -sum(int(row['value_minor_units']) for row in issue) - sum(
                int(row['value_minor_units']) for row in inventory.movements(s, item_id=component['item_id'])
                if row['kind'] == 'recost' and issue and row['corrects_movement_id'] == issue[0]['id'])
            if component['kind'] == 'cost':
                cost = unit_price.extension(_need(component['quantity_microunits'], build.quantity),
                                            unit_price.row_units(component, 'unit_cost'))
            build.rows.append(dict(component=component, item=component_item,
                                   need=_need(component['quantity_microunits'], build.quantity),
                                   on_hand=_on_hand(inventory.movements(s, item_id=component['item_id']), build.date),
                                   reserved=0, cost=cost))
        receipt = posted.get((item['id'], 'receipt'), [])[-1:]
        build.total = sum(int(row['value_minor_units']) + recosts.get(row['id'], 0) for row in receipt)
        build.max_buildable = build.quantity
        build.on_hand_after = _on_hand(inventory.movements(s, item_id=item['id']), build.date)
    return _output(s, build, status=current['status'], current=current,
                   number=_transaction_number(s, current['transaction_id']), transaction_id=current['transaction_id'],
                   memo=current['memo'], history=history)


def page(s, inp):
    t = c.assembly_builds
    latest = sa.select(t.c.build_id, sa.func.max(t.c.revision_number).label('n')).group_by(t.c.build_id).subquery()
    query = sa.select(t).join(latest, sa.and_(latest.c.build_id == t.c.build_id, latest.c.n == t.c.revision_number))
    if inp.status is None:
        query = query.where(t.c.status != 'deleted')
    elif inp.status != 'all':
        query = query.where(t.c.status == inp.status)
    if inp.item is not None:
        query = query.where(t.c.item_id == item_service.resolve_item(s.company, inp.item)['id'])
    if inp.site is not None:
        site = placements.resolve_site(s, inp.site, active=False)
        query = query.where(sa.func.coalesce(t.c.site_id, placements.default_site_id(s)) == site['id'])
    rows = [dict(row) for row in s.company.conn.execute(
        query.order_by(t.c.build_date.desc(), t.c.build_id.desc()).limit(inp.limit)).mappings()]
    currency = _home(s)
    out = []
    for row in rows:
        cost = None
        if row['transaction_id'] and row['status'] == 'built':
            live = inventory_effects.own_movements(s, row['transaction_id'])
            receipt = [m for m in live if m['kind'] == 'receipt']
            if receipt:
                recosts = sum(int(m['value_minor_units']) for m in inventory.movements(s, item_id=row['item_id'])
                              if m['kind'] == 'recost' and m['corrects_movement_id'] == receipt[0]['id'])
                cost = money(int(receipt[0]['value_minor_units']) + recosts, currency)
        out.append(AssemblyBuildListRow(
            build_id=row['build_id'], version=row['revision_number'], status=row['status'],
            number=_transaction_number(s, row['transaction_id']), item_id=row['item_id'],
            item_name=inventory.item_name(s, row['item_id']), date=row['build_date'],
            quantity=format_quantity_micro_units(row['quantity_microunits']), memo=row['memo'], assembly_cost=cost,
            site_name=_site_name(s, row.get('site_id'))))
    return AssemblyBuildListOutput(rows=out, count=len(out), reserved={
        key: format_quantity_micro_units(value) for key, value in reserved(s).items()})
