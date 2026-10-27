"""Inventory adjustments, the movements they write, and the corrections they owe.

An inventory adjustment posts as a journal entry, the way a cheque does: two lines, the
inventory-asset account and the account carrying the other side, with
``inventory_documents`` recording which document a person actually wrote. What it adds over a
hand-typed journal entry is the movement -- the row that says *which item* the asset value
belongs to -- and the recalculation that keeps every earlier issue's cost true when something
lands behind it.

## One change, one transaction

A write builds an ordered list of documents: the adjustment itself, then one correction
document per affected date. Each is prepared and persisted through the journal writer in
turn, inside dispatch's single company transaction, so either every movement, every balancing
posting and every correction is durable, or none of them is. A retry with the same
idempotency key replays the stored output; a retry without one recomputes the corrections
against what is already posted and finds nothing left to correct, which is the same answer.

## Why corrections are their own documents

A posting batch belongs to one revision and carries one effective date, and a revision may
own only one business batch. So the deltas a backdated purchase owes at three earlier dates
cannot hang off the purchase: they would all carry the purchase's date, which is precisely
the distortion the costing decision rejected. Each affected date gets its own dated document
instead, one line pair per corrected movement, using that movement's own asset account,
offset account and class -- read off the movement, never resolved again.

## What is refused, and when

Everything is decided before anything is built, so a refusal leaves nothing behind:

- **A closed period.** Every date this change would write to -- the document's own and every
  correction's -- is checked against the closing date first, and the whole change is refused
  naming the period. Deltas are never moved to today.
- **A worthless movement.** Every movement must carry a real value, because every movement is
  one side of a real posting and Bookflow posts no zero-amount entries.

## Stock taken below zero

As in the anchor product, an adjustment may take stock below zero, and the result says so in
``warnings``. The units below zero are costed provisionally by ``inventory_costing.replay``
(the average, else the purchase cost captured on the issue, else zero) and a later quantity
increase trues each such issue up with a correction dated at that increase. An issue that
would be worth nothing at all is still refused, because Bookflow posts no zero-amount entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlalchemy as sa

from bookflow.core import unit_price
from bookflow.company import accounts, items as item_service, journals, list_service, placements, schema as c
from bookflow.company.document_effects import allocate
from bookflow.company.inventory_costing import StockRefusal, replay, shortfall_warning
from bookflow.company.inventory_models import (
    InventoryAdjustmentLineSummary, InventoryAdjustmentSummary, InventoryCorrectionOutput, InventoryOutput,
    InventoryWriteOutput,
)
from bookflow.company.inventory_schema import INPUT_KINDS
from bookflow.company.journal_models import (
    JournalLineInput, JournalPostInput, JournalVoidInput, parse_domestic_amount,
)
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units, parse_quantity_micro_units
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan

# The system role this module owns. `journals.OWNED_SYSTEM_ROLES` maps it back here, which is
# what stops a hand-typed entry putting an unattributed amount into the inventory asset.
ASSET_ROLE = 'inventory_asset'
OWNER = 'inventory'

# The item types that carry stock, derived from the item master's own profile registry rather
# than written out again: a later stock-carrying type is one that requires both an asset and a
# cost-of-goods account, and it must not be silently left out of the ledger that values it.
TRACKED_TYPES = item_service.TRACKED_TYPES


def invalid(field_name, problem, **details):
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field_name, 'problem': problem}], **details})


# ---------------------------------------------------------------- reading


def movements(s, *, item_id=None, transaction_id=None, as_of=None):
    """Movement rows; ordering is the caller's, because replay owns what the order means."""
    t = c.inventory_movements
    query = sa.select(t)
    if item_id is not None:
        query = query.where(t.c.item_id == item_id)
    if transaction_id is not None:
        query = query.where(t.c.transaction_id == transaction_id)
    if as_of is not None:
        query = query.where(t.c.effective_date <= as_of)
    rows = [dict(row) for row in s.company.conn.execute(query).mappings()]
    landed = [row for row in rows if row['kind'] == 'landed']
    if landed:
        # A receipt restated unchanged by an edit of its document carries its landed cost on (R104 increment 6):
        # replay reads each landed cost on the receipt that stands for it now.
        from bookflow.company.landed_costs import successors
        moved = successors(s, {row['landed_on_movement_id'] for row in landed})
        for row in landed:
            row['landed_on_movement_id'] = moved.get(row['landed_on_movement_id'], row['landed_on_movement_id'])
    return rows


def fallback_cost(s, item_id, currency):
    """The item's purchase cost per base unit, captured on an issue as its provisional fallback,
    exactly, in R150 price units (thousandths of a minor unit): 0.004 is a cost, not none.

    ``None`` when the item records no purchase cost -- a stocked item must record one, so in
    practice that is a cost of zero -- or one in another currency: a stock value is always home
    currency and Bookflow never converts one implicitly.
    """
    row = s.company.conn.execute(sa.select(c.items.c.cost_minor_units, c.items.c.cost_currency,
                                           c.items.c.cost_fraction)
                                 .where(c.items.c.id == item_id)).first()
    if row is None or row.cost_minor_units is None or row.cost_currency != currency:
        return None
    units = unit_price.join(int(row.cost_minor_units), row.cost_fraction)
    return units if units > 0 else None


def fallback_columns(s, item_id, currency, kind):
    """The two movement columns an issue captures its fallback cost in (both None otherwise)."""
    minor, fraction = unit_price.split(fallback_cost(s, item_id, currency) if kind == 'issue' else None)
    return dict(fallback_unit_cost_minor_units=minor, fallback_unit_cost_fraction=fraction)


def item_name(s, item_id):
    return s.company.conn.execute(sa.select(c.items.c.full_name).where(
        c.items.c.id == item_id)).scalar_one_or_none() or item_id


def resolve(s, selector):
    """The inventory adjustment this selector names, by stable id or by document number."""
    t, m = c.transactions, c.inventory_documents
    key = selector.upper() if is_ulid(selector) else selector
    base = sa.select(t).join(m, m.c.transaction_id == t.c.id).where(m.c.kind == 'adjustment')
    found = [dict(r) for r in s.company.conn.execute(base.where(t.c.id == key)).mappings()]
    if not found:
        found = [dict(r) for r in s.company.conn.execute(base.where(t.c.number == selector)).mappings()]
    if not found:
        raise BookflowError('E_RECORD_NOT_FOUND',
                            details={'record_type': 'inventory_adjustment', 'selector': selector})
    return found[0]


def owning_document_kind(s, transaction_id):
    """Which inventory document a transaction was written as, or None.

    ``journals.prepare`` asks this before it lets the journal editor touch a transaction: an
    adjustment's accounting and its movements are one fact, and editing half of it in the
    journal editor would leave the stock ledger describing a posting that no longer exists.
    """
    kind = s.company.conn.execute(sa.select(c.inventory_documents.c.kind).where(
        c.inventory_documents.c.transaction_id == transaction_id)).scalar_one_or_none()
    if kind is None:
        from bookflow.company import assembly_builds
        if assembly_builds.is_build(s, transaction_id):
            return assembly_builds.KIND
    return kind


# ---------------------------------------------------------------- building one change


@dataclass
class _Movement:
    """A movement waiting for the posting-line identities its document has not minted yet.

    ``line_index`` is the 1-based entered-line position of its asset line, or None for a movement
    worth nothing, which carries no posting line and hangs off the document's first entered line
    (R104 increment 2: a line worth nothing moves quantity with no journal lines).
    """

    values: dict
    line_index: int | None = None
    reverses_line_id: str | None = None   # on a void, the original asset posting line


@dataclass
class _Document:
    journal: object
    operation: str
    kind: str
    movements: list[_Movement] = field(default_factory=list)
    journal_date: str | None = None   # a void carries the date of what it reverses
    extra_placements: list = field(default_factory=list)   # a count's own, moving no quantity
    after_persist: object = None      # callback(fresh, rows, provenance) -> extra persist entries
    reason: str | None = None         # a generated void's reason, when the caller gave none
    placements: list | None = None    # its own movements' placements, worked out when planned
    trailing: object = None           # callback(fresh, rows, provenance) -> persist entries after the movements


@dataclass
class _Line:
    """One adjusted line, as written: what moved, where, and on which basis it was valued."""

    item: dict
    movement_id: str
    site_id: str | None
    basis: str


@dataclass
class _Change:
    documents: list[_Document]
    item: dict
    state: object
    currency: str
    quantity_microunits: int = 0
    value_minor_units: int = 0
    movement_kind: str = 'reversal'
    asset_account_id: str = ''
    offset_account_id: str = ''
    warnings: list = field(default_factory=list)
    sites: dict = field(default_factory=dict)   # movement id -> inventory site (R104)
    items: dict = field(default_factory=dict)   # item id -> item, every item this change moves
    states: dict = field(default_factory=dict)  # item id -> its replay with this change
    lines: list = field(default_factory=list)   # _Line per adjusted line
    placements: list = field(default_factory=list)  # planned placements for its own movements
    numbers: dict = field(default_factory=dict)  # movement id -> what its line says of lot and serial numbers


def _warnings(item, history, state, own):
    """One line per issue this change leaves below zero: its own, or one it newly leaves short."""
    if not state.shortfalls:
        return []
    try:
        before = {shortfall.movement['id'] for shortfall in replay(history).shortfalls}
    except StockRefusal:
        before = set()
    return [shortfall_warning(item['full_name'], shortfall) for shortfall in state.shortfalls
            if shortfall.movement['id'] in own or shortfall.movement['id'] not in before]


def found_warnings(item, state, own):
    """A found quantity valued at nothing says so: no average, no last average, no purchase cost."""
    return [f'{item["full_name"]}: the units found on {found.movement["effective_date"]} are valued at zero, '
            'because the item has no average cost and no purchase cost; a later receipt does not revalue them.'
            for found in state.found if found.movement['id'] in own and found.basis == 'none']


def _home(s):
    return s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar_one()


def next_sequence(s):
    """The next number of the company's one stock counter, shared by movements and placements (R104)."""
    moved = int(s.company.conn.execute(
        sa.select(sa.func.coalesce(sa.func.max(c.inventory_movements.c.sequence), 0))).scalar_one())
    placed = int(s.company.conn.execute(
        sa.select(sa.func.coalesce(sa.func.max(c.inventory_placements.c.sequence), 0))).scalar_one())
    return max(moved, placed) + 1


def _tracked_item(s, selector, field_name='item'):
    item = item_service.resolve_item(s.company, selector)
    if item['type'] not in TRACKED_TYPES:
        readable = ' and '.join(name.replace('_', ' ') for name in TRACKED_TYPES)
        raise invalid(field_name, f'a {item["type"].replace("_", " ")} item carries no stock; only '
                                  f'{readable} items have a quantity and a value to adjust')
    if not item['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': 'item', 'record_id': item['id'],
                                                             'field': field_name})
    return item


def _asset_account(s, item, field_name='item'):
    if item['asset_account_id'] is None:
        raise invalid(field_name, 'this item has no inventory-asset account')
    row = accounts.resolve_account(s.company, item['asset_account_id'])
    if row.get('system_role') != ASSET_ROLE:
        raise invalid(field_name, "this item's asset account is not the inventory-asset account")
    return journals.active(row, 'account')


def offset_account(s, selector, asset_id=None):
    row = journals.active(accounts.resolve_account(s.company, selector), 'account')
    if row['type'] == 'non_posting':
        raise invalid('adjustment_account', 'must be a posting account')
    if row['id'] == asset_id or row.get('system_role') == ASSET_ROLE:
        raise invalid('adjustment_account', 'the other side of an inventory adjustment cannot be '
                                            'the inventory-asset account itself')
    if row.get('system_role') in journals.OWNED_SYSTEM_ROLES:
        raise invalid('adjustment_account', 'that account is written by the ledger that owns it, '
                                            'not by an inventory adjustment')
    return row


def class_row(s, class_id):
    if class_id is None:
        return None
    from bookflow.company.lists import get_list_definition
    return journals.active(list_service.resolve_selector(
        s.company, c.classes, get_list_definition('class'), class_id), 'class')


def pair(asset_id, offset_id, amount_minor_units, currency, class_id, description):
    """The two entered lines one signed asset movement is: asset first, offset second.

    A positive amount means the asset went up. Position one is always the asset line, which
    is what lets a movement find its own posting line by index rather than by guessing.
    """
    money = Money(abs(amount_minor_units), currency).to_dict()
    up = amount_minor_units > 0
    return [
        JournalLineInput(account=asset_id, side='debit' if up else 'credit', amount=money,
                         class_id=class_id, description=description),
        JournalLineInput(account=offset_id, side='credit' if up else 'debit', amount=money,
                         class_id=class_id, description=description),
    ]


def refuse_stock(refusal, item, *, quantity_field='quantity_change', value_field='value_change'):
    """The refusal a person reads, naming the item, the date and the field they can change.

    The two field names are the caller's, because the grid a person is looking at is what they
    can act on: an adjustment has a quantity change, a bill has an Items grid and a sale has
    lines, and pointing at a field the form does not have is worse than pointing at nothing.
    """
    details = {'item_id': item['id'], 'item_name': item['full_name'], **refusal.details}
    if refusal.movement is not None:
        details['effective_date'] = refusal.movement['effective_date']
    problem = details.pop('problem')
    when = details.get('effective_date', 'an affected date')
    if refusal.reason == 'negative_stock':
        return BookflowError('E_VALIDATION', message=(
            f'This change cannot stand for "{item["full_name"]}" on {when}: {problem}.'),
            details={'fields': [{'field': quantity_field, 'problem': problem}], **details})
    return BookflowError('E_VALIDATION', message=(
        f'This change cannot stand for "{item["full_name"]}" on {when}: {problem}.'),
        details={'fields': [{'field': value_field, 'problem': problem}], **details})


def value_at(rows, date):
    """An item's replayed value on ``date``: every movement dated by then, corrections included."""
    return sum(int(row['value_minor_units']) for row in rows if row['effective_date'] <= date)


def movement_values(*, item_id, kind, quantity, value, date, sequence, currency, asset_id, offset_id, class_id,
                    position='before', fallback=None):
    """A new input movement's columns, before its document gives it posting identities.

    ``fallback`` is the item's purchase cost in R150 price units (``fallback_cost``), stored as minor
    units and their fraction.
    """
    return dict(id=new_id(), item_id=item_id, kind=kind, quantity_microunits=quantity, value_minor_units=value,
                effective_date=date, sequence=sequence, currency=currency, asset_account_id=asset_id,
                offset_account_id=offset_id, class_id=class_id, corrects_movement_id=None,
                reverses_movement_id=None, returns_movement_id=None, filled_by_movement_id=None,
                count_position=position, **dict(zip(('fallback_unit_cost_minor_units', 'fallback_unit_cost_fraction'),
                                                    unit_price.split(fallback))))


def reversal_values(row, sequence):
    """The exact inverse of one stored movement, at its own date and position."""
    return dict(id=new_id(), kind='reversal', item_id=row['item_id'],
                quantity_microunits=-int(row['quantity_microunits']),
                value_minor_units=-int(row['value_minor_units']),
                effective_date=row['effective_date'], sequence=sequence,
                currency=row['currency'], asset_account_id=row['asset_account_id'],
                offset_account_id=row['offset_account_id'], class_id=row['class_id'],
                corrects_movement_id=None, reverses_movement_id=row['id'], returns_movement_id=None,
                filled_by_movement_id=None, fallback_unit_cost_minor_units=None,
                count_position=row.get('count_position') or 'before',
                revision_id=row['revision_id'], document_line_id=row['document_line_id'])


def value_movements(s, planned, items, refuse):
    """Write each costed movement's value back from replay: an issue's and a found quantity's.

    ``planned`` are new movements in write order; each item is replayed with what came before it,
    so a second line of one item sees the first. Returns ``{item_id: replay}`` with all of them.
    """
    working = {}
    states = {}
    for values in planned:
        item_id = values['item_id']
        if item_id not in working:
            working[item_id] = movements(s, item_id=item_id)
        if values['kind'] in ('issue', 'found'):
            try:
                values['value_minor_units'] = replay(working[item_id] + [values]).targets[values['id']]
            except StockRefusal as refusal:
                raise refuse(refusal, items[item_id]) from None
        working[item_id].append(values)
    for item_id, rows in working.items():
        try:
            states[item_id] = replay(rows)
        except StockRefusal as refusal:
            raise refuse(refusal, items[item_id]) from None
    return working, states


def _adjustment_change(s, ctx, inp, currency, sequence):
    """The adjustment document and its movements, one per line, with the values the average decides."""
    from bookflow.company import item_conversions
    lines = inp.entered_lines()
    many = inp.lines is not None
    header_site = placements.site_id_for(s, inp.site)
    header_class = class_row(s, inp.class_id)
    offset = None
    position = getattr(inp, 'count_position', None) or 'before'
    items, planned, written = {}, [], []
    held_cache = {}
    histories = {}
    for index, line in enumerate(lines):
        prefix = f'lines.{index}.' if many else ''
        item = _tracked_item(s, line.item, prefix + 'item')
        items.setdefault(item['id'], item)
        item_conversions.require_stock_date(s, item['id'], item['full_name'], inp.date, prefix + 'item' if many else 'date')
        asset = _asset_account(s, item, prefix + 'item')
        offset = offset or offset_account(s, inp.adjustment_account, asset['id'])
        klass = class_row(s, line.class_id) if line.class_id is not None else header_class
        site_id = placements.site_id_for(s, line.site, field=prefix + 'site') if line.site is not None else header_site
        place = site_id or placements.default_site_id(s)
        from bookflow.company import bins
        bin_id = bins.line_bin(s, item['id'], place, getattr(line, 'bin', None), field=prefix + 'bin')
        key = (item['id'], place, bin_id)
        if line.new_quantity is not None:
            # What the place holds on the date, with this adjustment's earlier lines there: the line's bin at a
            # site that tracks bins (R104 increment 5), else the whole site.
            reading = placements.held(s, [item['id']], place, inp.date,
                                      ('before',) if position == 'before' else ('before', 'count', 'after'))
            tracked = bins.site_tracks_bins(s, place)
            books = sum(quantity for (each, where), quantity in reading.items() if each == item['id']
                        and (not tracked or (where.split('|')[0] or None) == bin_id))
            target = parse_quantity_micro_units(line.new_quantity, field=prefix + 'new_quantity')
            quantity = target - books - held_cache.get(key, 0)
        else:
            quantity = 0 if line.quantity_change is None else parse_quantity_micro_units(
                line.quantity_change, field=prefix + 'quantity_change')
        held_cache[key] = held_cache.get(key, 0) + quantity
        stated = None
        if line.value_change is not None:
            stated = parse_domestic_amount(line.value_change, currency, prefix + 'value_change').minor_units
            if line.negative_value:
                stated = -stated
        elif line.new_value is not None:
            if quantity < 0:
                raise invalid(prefix + 'new_value', 'a quantity decrease takes its value from the weighted '
                                                    'average; new_value does not apply')
            if item['id'] not in histories:
                histories[item['id']] = movements(s, item_id=item['id'])
            current = value_at(histories[item['id']] + [p for p in planned if p['item_id'] == item['id']], inp.date)
            stated = parse_domestic_amount(line.new_value, currency, prefix + 'new_value').minor_units - current
        if quantity == 0 and not stated:
            if many:
                continue        # this line already holds what it says: nothing to adjust
            raise invalid('quantity_change', 'an adjustment must change quantity, value, or both')
        kind = ('receipt' if stated is not None else 'found') if quantity > 0 else 'issue' if quantity < 0 else 'value'
        if kind == 'receipt' and stated < 0:
            raise invalid(prefix + ('value_change' if line.value_change is not None else 'new_value'),
                          'a quantity increase cannot be worth a negative amount')
        values = movement_values(
            item_id=item['id'], kind=kind, quantity=quantity, value=stated if stated is not None else -1,
            date=inp.date, sequence=sequence, currency=currency, asset_id=asset['id'], offset_id=offset['id'],
            class_id=klass['id'] if klass else None, position=position,
            fallback=fallback_cost(s, item['id'], currency) if kind in ('issue', 'found') else None)
        sequence += 1
        planned.append(values)
        auto = (quantity < 0 and line.new_quantity is None and getattr(line, 'bin', None) is None
                and bins.site_tracks_bins(s, place))
        written.append((values, item, site_id, line, 'stated' if stated is not None else None, (bin_id, auto)))
    if not planned:
        raise invalid('lines', 'every line already holds what it says; this adjustment changes nothing')

    def refuse(refusal, item):
        return refuse_stock(refusal, item)
    working, states = value_movements(s, planned, items, refuse)
    found_basis = {found.movement['id']: found.basis for state in states.values() for found in state.found}
    if not any(int(values['value_minor_units']) for values in planned):
        raise invalid('quantity_change' if not many else 'lines',
                      'this adjustment is worth nothing at the current average cost, and '
                      'Bookflow posts no zero-amount entry')
    from bookflow.company import serial_lots
    journal_lines, movements_out, change_lines, sites, numbers = [], [], [], {}, {}
    for values, item, site_id, line, basis, (bin_id, auto) in written:
        description = line.description or inp.memo or f'Inventory adjustment for {item["full_name"]}'
        index = None
        if int(values['value_minor_units']):
            journal_lines.extend(pair(values['asset_account_id'], values['offset_account_id'],
                                      values['value_minor_units'], currency, values['class_id'], description))
            index = len(journal_lines) - 1
        movements_out.append(_Movement(values, index))
        if site_id:
            sites[values['id']] = site_id
        stated_numbers = serial_lots.from_line(line)
        numbers[values['id']] = dict(spec=stated_numbers, item_name=item['full_name'], field='lines' if many else 'item',
                                     bin_id=bin_id, bin_auto=auto)
        change_lines.append(_Line(item, values['id'], site_id or placements.default_site_id(s),
                                  basis or found_basis.get(values['id'], 'average')))
    warnings = []
    for item_id, state in states.items():
        own = {values['id'] for values in planned if values['item_id'] == item_id}
        history = [row for row in working[item_id] if row['id'] not in own]
        warnings += _warnings(items[item_id], history, state, own)
        warnings += found_warnings(items[item_id], state, own)
    journal = JournalPostInput(
        date=inp.date, memo=inp.memo, custom_fields=inp.custom_fields,
        custom_field_kinds=inp.custom_field_kinds, lines=journal_lines,
        **({'number': inp.number} if inp.number is not None else {}))
    document = _Document(journal, 'post', 'adjustment', movements_out)
    first = planned[0]
    first_item = items[first['item_id']]
    change = _Change([document], first_item, states[first['item_id']], currency, int(first['quantity_microunits']),
                     int(first['value_minor_units']), first['kind'], first['asset_account_id'], offset['id'],
                     warnings, sites, items, states, change_lines)
    change.numbers = numbers
    return change


def _void_change(s, inp, currency, sequence):
    """The reversal of one adjustment: its movements inverted exactly, as the batch is."""
    header = resolve(s, inp.adjustment)
    own = [row for row in movements(s, transaction_id=header['id']) if row['kind'] in INPUT_KINDS]
    if not own:
        raise BookflowError('E_INTERNAL', message='An inventory adjustment has no movement to reverse.')
    items = {}
    for row in own:
        items.setdefault(row['item_id'], item_service.resolve_item(s.company, row['item_id']))
    item = items[own[0]['item_id']]
    retired = {row['reverses_movement_id'] for item_id in items for row in movements(s, item_id=item_id)
               if row['kind'] == 'reversal'}
    if header['status'] == 'voided' or all(row['id'] in retired for row in own):
        # Already reversed; the journal writer answers "no change" and nothing else runs.
        return _Change([], item, replay(movements(s, item_id=item['id'])), currency, items=items)
    revision = journals.revision(s, header)
    reversals = []
    for row in own:
        reversals.append(_Movement(reversal_values(row, sequence), line_index=0,
                                   reverses_line_id=row['posting_line_id']))
        sequence += 1
    states, warnings = {}, []
    for item_id, each in items.items():
        history = movements(s, item_id=item_id)
        try:
            states[item_id] = replay(history + [m.values for m in reversals if m.values['item_id'] == item_id])
        except StockRefusal as refusal:
            raise refuse_stock(refusal, each) from None
        warnings += _warnings(each, history, states[item_id], set())
    journal = JournalVoidInput(journal=header['id'], expected_version=inp.expected_version)
    first = reversals[0].values
    change = _Change([_Document(journal, 'void', 'adjustment', reversals)], item, states[item['id']], currency,
                     first['quantity_microunits'], first['value_minor_units'], 'reversal',
                     first['asset_account_id'], first['offset_account_id'], warnings, items=items, states=states)
    change.documents[0].journal_date = revision['date']
    return change


def correction_documents(s, change, sequence):
    """One dated document per affected date, one line pair per corrected movement.

    A true-up -- the settlement of a provisional cost, dated at the receipt that settled it --
    goes in a document of its own for that date, so every correction's memo says what it is.
    """
    # The adjusted items first, then any assembly a build made from them (R103): a correction of a
    # build's component issue is paired with one of the build's assembly receipt, and the assembly
    # is replayed with it. With no builds in the company this is each adjusted item's own replay.
    from bookflow.company import assembly_builds, inventory_effects
    planned = [movement.values for document in change.documents for movement in document.movements]
    working = {}
    for values in planned:
        if values['item_id'] not in working:
            working[values['item_id']] = movements(s, item_id=values['item_id'])
        working[values['item_id']].append(values)

    def load(item_id):
        if item_id not in working:
            working[item_id] = movements(s, item_id=item_id)
        return working[item_id]
    states, partners = assembly_builds.replay_items(
        s, working, load, lambda refusal, item_id: refuse_stock(refusal, {'id': item_id, 'full_name': item_name(s, item_id)}))
    by_date: dict[tuple, list] = {}
    for item_id, state in states:
        for correction in state.corrections:
            by_date.setdefault((correction.effective_date, inventory_effects.correction_kind(correction)),
                               []).append((item_id, correction))
    names = sorted({item_name(s, item_id) for item_id, _ in states if any(
        correction for correction in dict(states)[item_id].corrections)})
    label = names[0] if len(names) == 1 else f'{len(names)} items'
    for date, kind in sorted(by_date):
        true_up = kind != 0
        lines, pending = [], []
        for item_id, correction in by_date[(date, kind)]:
            target = correction.target_movement
            lines.extend(pair(
                target['asset_account_id'], target['offset_account_id'],
                correction.delta_minor_units, change.currency, target['class_id'],
                inventory_effects.correction_description(correction)))
            partner = partners.get((target['id'], correction.filled_by['id'] if correction.filled_by is not None else None))
            pending.append(_Movement(dict(
                id=new_id(), kind='recost', item_id=item_id,
                quantity_microunits=0, value_minor_units=correction.delta_minor_units,
                effective_date=date, sequence=sequence, currency=change.currency,
                asset_account_id=target['asset_account_id'],
                offset_account_id=target['offset_account_id'], class_id=target['class_id'],
                corrects_movement_id=target['id'], reverses_movement_id=None,
                returns_movement_id=None,
                filled_by_movement_id=correction.filled_by['id'] if true_up else None,
                fallback_unit_cost_minor_units=None, fallback_unit_cost_fraction=None,
                count_position=target.get('count_position') or 'before'),
                line_index=len(lines) - 1))
            sequence += 1
            if partner is not None:
                partner['sequence'] = sequence
                pending.append(_Movement(partner, line_index=len(lines)))
                sequence += 1
        memo = inventory_effects.CORRECTION_MEMOS[kind]
        change.documents.append(_Document(JournalPostInput(
            date=date, memo=f'{memo} for {label}',
            lines=lines), 'post', 'recost', pending))
    return sequence


def complete(s, change):
    """Add the corrections a change owes, its below-zero warnings by site, and check every date is open."""
    sequence = max(int(movement.values['sequence']) for document in change.documents
                   for movement in document.movements) + 1
    correction_documents(s, change, sequence)
    own = [movement.values for movement in change.documents[0].movements]
    from bookflow.company import serial_lots
    numbering = serial_lots.Numbering(s)
    change.placements = change.placements or placements.for_movements(s, own, change.sites, change.numbers, numbering)
    change.warnings.extend(w for w in numbering.warnings if w not in change.warnings)
    change.warnings.extend(placements.warnings(s, change.placements + list(change.documents[0].extra_placements),
                                               {item_id: item['full_name'] for item_id, item in change.items.items()}
                                               or {change.item['id']: change.item['full_name']}))
    dates = {document.journal_date or document.journal.date
             for document in change.documents}
    journals.open_dates(s, sorted(dates))
    return change


def _build(s, ctx, inp, operation):
    """Every document this change writes, decided in full before any of it is built."""
    currency = _home(s)
    sequence = next_sequence(s)
    change = (_adjustment_change(s, ctx, inp, currency, sequence) if operation == 'post'
              else _void_change(s, inp, currency, sequence))
    if not change.documents:
        return change
    return complete(s, change)


# ---------------------------------------------------------------- persistence


def _attach(document, fresh):
    """Give each pending movement the posting and document line identities just minted.

    A void or a correction reverses the current batch: each reversal movement takes the leg that
    reverses its original's (a movement worth nothing takes the reversing batch alone). A post or a
    correction's new revision gives every other movement its own asset leg by entered position.
    """
    pending = fresh.data['pending']
    header_id = fresh.data['header']['id']
    if document.operation in ('void', 'update'):
        by_original = {line['reversed_line_id']: line for line in pending['posting_lines']
                       if line.get('reversed_line_id')}
        inverse = next((batch for batch in pending['posting_batches'] if batch['kind'] == 'reversal'), None)
        for movement in document.movements:
            if movement.values['kind'] != 'reversal':
                continue
            if movement.reverses_line_id is None:
                # A movement worth nothing had no posting line; its reversal has none either.
                movement.values.update(transaction_id=header_id, posting_batch_id=inverse['id'], posting_line_id=None)
                continue
            leg = by_original.get(movement.reverses_line_id)
            if leg is None:
                raise BookflowError('E_INTERNAL', message='A void did not reverse its inventory posting line.')
            movement.values.update(transaction_id=leg['transaction_id'],
                                   posting_batch_id=leg['batch_id'], posting_line_id=leg['id'])
        if document.operation == 'void':
            return
    revision = pending['transaction_revisions'][0]
    entered = {line['position']: line for line in pending['document_lines']}
    batch = next(batch for batch in pending['posting_batches'] if batch['kind'] != 'reversal')
    legs = {line['line_no']: line for line in pending['posting_lines'] if line['batch_id'] == batch['id']}
    for movement in document.movements:
        if movement.values['kind'] == 'reversal' and document.operation == 'update':
            continue
        if movement.line_index is None:
            movement.values.update(transaction_id=header_id, revision_id=revision['id'],
                                   posting_batch_id=batch['id'], posting_line_id=None,
                                   document_line_id=entered[min(entered)]['id'])
            continue
        line, leg = entered[movement.line_index], legs[movement.line_index]
        movement.values.update(transaction_id=leg['transaction_id'], revision_id=revision['id'],
                               posting_batch_id=leg['batch_id'], posting_line_id=leg['id'],
                               document_line_id=line['id'])


def check_attribution(claimed, posting_lines, control_account_ids):
    """Every inventory-asset posting a document makes is attributed to exactly one item.

    This is the invariant the stock reports and the balance sheet share. A posting line on a
    control account that no movement claims would be a balance nothing could explain, and a
    second movement on one line would double it. ``claimed`` is the posting line every planned
    movement names; ``posting_lines`` is every leg the document is about to write.
    """
    claimed = [line for line in claimed if line is not None]
    control = {line['id'] for line in posting_lines
               if line['account_id'] in control_account_ids}
    if len(claimed) != len(set(claimed)) or control != set(claimed):
        raise BookflowError('E_INTERNAL', message=(
            'An inventory-asset posting is not attributed to exactly one item; refusing to '
            'write a balance the stock reports could not explain.'))


def asset_account_ids(s):
    return set(s.company.conn.execute(sa.select(c.accounts.c.id).where(
        c.accounts.c.system_role == ASSET_ROLE)).scalars())


def _line_summaries(s, change):
    names = placements.site_names(s)
    by_id = {movement.values['id']: movement.values for document in change.documents[:1]
             for movement in document.movements}
    out = []
    for line in change.lines:
        values = by_id[line.movement_id]
        state = change.states.get(line.item['id'], change.state)
        out.append(InventoryAdjustmentLineSummary(
            item_id=line.item['id'], item_name=line.item['full_name'], site_id=line.site_id,
            site_name=names.get(line.site_id), quantity_change=format_quantity_micro_units(int(values['quantity_microunits'])),
            value_change=Money(int(values['value_minor_units']), change.currency).to_dict(),
            movement_kind=values['kind'], value_basis=line.basis,
            quantity_on_hand=format_quantity_micro_units(state.quantity_microunits),
            inventory_value=Money(state.value_minor_units, change.currency).to_dict()))
    return out


def _summary(s, change, corrections):
    state = change.state
    return InventoryAdjustmentSummary(
        kind='adjustment', item_id=change.item['id'], item_name=change.item['full_name'],
        item_type=change.item['type'], currency=change.currency,
        quantity_change=format_quantity_micro_units(change.quantity_microunits),
        value_change=Money(change.value_minor_units, change.currency).to_dict(),
        quantity_on_hand=format_quantity_micro_units(state.quantity_microunits),
        # R150: an average is a unit cost, shown to the price scale as the valuation report shows it.
        average_cost=unit_price.to_dict(unit_price.average(state.value_minor_units, state.quantity_microunits),
                                        change.currency),
        inventory_value=Money(state.value_minor_units, change.currency).to_dict(),
        asset_account_id=change.asset_account_id, adjustment_account_id=change.offset_account_id,
        movement_kind=change.movement_kind, corrections=corrections, lines=_line_summaries(s, change))


def _corrections_output(s, change):
    return [InventoryCorrectionOutput(
        item_id=movement.values['item_id'],
        item_name=change.item['full_name'] if movement.values['item_id'] == change.item['id']
        else item_name(s, movement.values['item_id']),
        effective_date=movement.values['effective_date'],
        corrects_movement_id=movement.values['corrects_movement_id'],
        filled_by_movement_id=movement.values.get('filled_by_movement_id'),
        delta=Money(movement.values['value_minor_units'], change.currency).to_dict(),
        transaction_id=movement.values.get('transaction_id', ''),
        number=movement.values.get('number', ''))
        for document in change.documents if document.kind == 'recost'
        for movement in document.movements]


def _blocked_correction(error, change, document):
    """Explain a refusal of a document the caller never wrote.

    The corrections a backdated change owes are generated, so an error naming a field on one
    of them is unanswerable as it stands -- there is no form to fill in. A company-wide
    required journal custom field with no default is the case that reaches here. Saying which
    earlier date could not be corrected, and that nothing was written, is the difference
    between a puzzle and an instruction.
    """
    return BookflowError(error.code, message=(
        f'This change owes "{change.item["full_name"]}" a cost correction dated '
        f'{document.journal.date}, and that correction could not be posted: {error.message} '
        'Nothing was written. A generated correction carries no per-document entry, so a '
        'required journal field needs a default before an inventory entry can be backdated.'),
        details={**error.details, 'blocked_correction_date': document.journal.date,
                 'item_id': change.item['id'], 'item_name': change.item['full_name']})


def persist_change(s, ctx, change, *, command_name, persist, nouns=None):
    """Prepare, and with ``persist`` write, every document of a change in order.

    Returns the first document's preview or saved output, and what was touched. Shared by
    adjustments and physical counts, whose differences post as the same kind of journal entry.
    """
    nouns = nouns or {'adjustment': 'inventory adjustment', 'count': 'inventory count difference'}
    at = clock.now_iso()
    asset_accounts = asset_account_ids(s)
    reserved, touched, primary = [], [], None
    for position, document in enumerate(change.documents):
        if document.kind == 'recost' or (position and getattr(document.journal, 'number', '') is None):
            # The journal writer allocates the first document's own number, and every later
            # document takes the next free one after the numbers this call has already handed out.
            number, _ = allocate(s, 'journal_entry', None, reserved=reserved)
            document.journal = document.journal.model_copy(update={'number': number})
        call = ctx.model_copy(update={'reason': document.reason}) if document.reason and not (
            ctx.reason and ctx.reason.strip()) else ctx
        try:
            fresh = journals.prepare(s, call, document.journal, document.operation, owner=OWNER)
        except BookflowError as error:
            if document.kind != 'recost':
                raise
            raise _blocked_correction(error, change, document) from None
        if not fresh.data['changed']:
            raise BookflowError('E_INTERNAL', message='An inventory document produced no accounting.')
        reserved.append(fresh.data['header']['number'])
        _attach(document, fresh)
        check_attribution([movement.values['posting_line_id'] for movement in document.movements],
                          fresh.data['pending']['posting_lines'], asset_accounts)
        for movement in document.movements:
            movement.values['number'] = fresh.data['header']['number']
        provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
                          audit_event_id=fresh.data['event'])
        marker = dict(transaction_id=fresh.data['header']['id'], type='journal_entry', kind=document.kind,
                      **provenance)
        rows = [dict({'landed_on_movement_id': None}, **{key: value for key, value in movement.values.items() if key != 'number'},
                     **provenance)
                for movement in document.movements]
        if persist:
            extra = [('inventory_documents', 'inventory_document', 'transaction_id', [marker])] \
                if document.operation == 'post' else []
            if document.after_persist is not None:
                extra.extend(document.after_persist(fresh, rows, provenance))
            # Placed before the movements, which the database refuses unless their placements sum to them (R104).
            planned = document.placements if document.placements is not None else (
                change.placements if position == 0 else None)
            placed = placements.placements_for_rows(s, rows, change.sites, planned)
            placed += placements.with_provenance(document.extra_placements, **provenance)
            extra.extend(placements.extras(placed))
            extra.append(('inventory_movements', 'inventory_movement', 'id', rows))
            if document.trailing is not None:
                extra.extend(document.trailing(fresh, rows, provenance))
            applied = journals.persist_prepared(
                fresh, call, s, command_name=command_name, extra=tuple(extra),
                noun=nouns.get(document.kind, 'inventory cost correction'))
            touched.extend(applied.touched)
            primary = primary or applied.output
        else:
            primary = primary or fresh.preview
    return primary, touched


def _run(s, ctx, inp, operation, *, persist):
    change = _build(s, ctx, inp, operation)
    command_name = 'inventory ' + ('adjust' if operation == 'post' else 'void')
    if not change.documents:
        header = resolve(s, inp.adjustment)
        fresh = journals.prepare(s, ctx, JournalVoidInput(
            journal=header['id'], expected_version=inp.expected_version), 'void', owner=OWNER)
        summary = document_summary(s, header, change.currency)
        return InventoryWriteOutput(**fresh.preview.model_dump(), adjustment=summary), [], False
    primary, touched = persist_change(s, ctx, change, command_name=command_name, persist=persist)
    output = InventoryWriteOutput(**primary.model_dump(), adjustment=_summary(s, change, _corrections_output(s, change)))
    if change.warnings:
        output.warnings = list(output.warnings) + [
            line for line in dict.fromkeys(change.warnings) if line not in output.warnings]
    return output, touched, True


def prepare(s, ctx, inp, operation):
    output, _, changed = _run(s, ctx, inp, operation, persist=False)
    return Plan(output, {'input': inp, 'operation': operation, 'changed': changed})


def apply(plan, ctx, s):
    # Rebuilt inside the writer transaction: references, dates, numbering, the replay and
    # every correction it implies are only decisive here.
    inp, operation = plan.data['input'], plan.data['operation']
    output, touched, changed = _run(s, ctx, inp, operation, persist=True)
    if not changed:
        return Applied(output, [], 'no change')
    verb = 'adjust' if operation == 'post' else 'void'
    many = len(output.adjustment.lines) > 1
    return Applied(output, touched, f'{verb} inventory for '
                   + (f'{len(output.adjustment.lines)} lines' if many else output.adjustment.item_name), audited=True)


# ---------------------------------------------------------------- reads


def document_summary(s, header, currency=None):
    """The adjustment's own footer, derived from the movements it wrote."""
    currency = currency or _home(s)
    own = [row for row in movements(s, transaction_id=header['id']) if row['kind'] in INPUT_KINDS]
    if not own:
        raise BookflowError('E_RECORD_NOT_FOUND',
                            details={'record_type': 'inventory_adjustment', 'selector': header['id']})
    own.sort(key=lambda row: int(row['sequence']))
    movement = own[0]
    items, states = {}, {}
    for row in own:
        if row['item_id'] not in items:
            items[row['item_id']] = item_service.resolve_item(s.company, row['item_id'])
            states[row['item_id']] = replay(movements(s, item_id=row['item_id']))
    item = items[movement['item_id']]
    sites = {}
    for movement_id, rows in placements.movement_placements(s, [row['id'] for row in own]).items():
        sites[movement_id] = max(rows, key=lambda p: abs(int(p['quantity_microunits'])))['site_id']
    bases = {found.movement['id']: found.basis for state in states.values() for found in state.found}
    change = _Change([_Document(None, 'post', 'adjustment', [_Movement(row) for row in own])], item,
                     states[item['id']], currency,
                     int(movement['quantity_microunits']), int(movement['value_minor_units']),
                     movement['kind'], movement['asset_account_id'], movement['offset_account_id'],
                     items=items, states=states,
                     lines=[_Line(items[row['item_id']], row['id'], sites.get(row['id']),
                                  bases.get(row['id'], 'average') if row['kind'] == 'found' else 'stated')
                            for row in own])
    recosts = [row for item_id in items for row in sorted(
        movements(s, item_id=item_id), key=lambda row: (row['effective_date'], int(row['sequence'])))
        if row['kind'] == 'recost']
    numbers = {} if not recosts else {row['id']: row['number'] for row in s.company.conn.execute(
        sa.select(c.transactions.c.id, c.transactions.c.number).where(
            c.transactions.c.id.in_([row['transaction_id'] for row in recosts]))).mappings()}
    corrections = [InventoryCorrectionOutput(
        item_id=row['item_id'], item_name=items[row['item_id']]['full_name'], effective_date=row['effective_date'],
        corrects_movement_id=row['corrects_movement_id'],
        filled_by_movement_id=row.get('filled_by_movement_id'),
        delta=Money(int(row['value_minor_units']), row['currency']).to_dict(),
        transaction_id=row['transaction_id'], number=numbers[row['transaction_id']])
        for row in recosts]
    return _summary(s, change, corrections)


def show(s, inp):
    header = resolve(s, inp.adjustment)
    requested = journals.revision(s, header, inp.revision_number)
    return InventoryOutput(**journals.summary(header, requested),
                           revision=journals.revision_output(s, requested),
                           adjustment=document_summary(s, header))
