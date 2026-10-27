"""Stock received on a purchase and issued on a sale, and the corrections they owe.

``company/inventory.py`` owns the hand-entered adjustment. This owns the far commoner case:
a bill that buys an inventory part and an invoice or sales receipt that sells one. Both write
the same append-only ledger through the same replay, so there is exactly one definition of
what an item is worth, and a purchase entered behind a sale corrects that sale's cost whether
the purchase arrived as a bill or as an adjustment.

## What each document does

A **purchase** debits the item's own Inventory Asset account for the line amount instead of an
expense account, and writes one ``receipt`` movement carrying the quantity and that amount.
Nothing else about the bill changes: one debit per entered line, Accounts Payable credited
once, the obligation components untouched. Only the account the line names is different, and
it is different because the item says so.

A **sale** adds two legs of its own to the sale's own batch -- debit Cost of Goods Sold,
credit Inventory Asset -- for what the weighted average says the quantity leaving is worth,
and writes one ``issue`` movement against the credit. The revenue side is untouched: what a
thing sold for and what it cost are two independent facts and this changes only the second.

A **return** is that sale's issue read backwards. A credit memo line that gives back a stocked
invoice line adds the same two legs the other way round -- debit Inventory Asset, credit Cost
of Goods Sold -- and writes one ``receipt`` movement against the debit. That movement *names*
the issue it gives back, in ``returns_movement_id``, and states no cost of its own: what it is
worth is the share of that issue's value replay computes, exactly as an issue's own value is.
Naming rather than stating is what makes a purchase entered behind the sale reach the returns
as well -- they are recosted with the issue instead of being stranded at what the average said
on the day the credit was written. The money side of the credit is untouched by this, for the
same reason a sale's revenue is.

A **return to a vendor** is a purchase read backwards, and its value is stated rather than
costed: a vendor-credit item line credits Inventory Asset the amount the vendor credited and
writes one ``vendor_return`` movement carrying the quantity and that amount, so the average cost
of what stays on the shelf moves by the difference between the credit and the average. Nothing
recosts it later, because nobody but the vendor decided what it was worth.

A **correction or a void** reverses those legs exactly, the way it reverses every other leg,
and writes one ``reversal`` movement against each reversing leg. A replacement revision then
receives or issues again at whatever the average says *now*. There is no separate rule for a
void: retiring a movement and replaying is the same operation as entering one.

## Why the movement hangs off the posting line

``inventory_movements`` is unique on ``posting_line_id`` and a database trigger requires the
movement's value to equal that line's debit less its credit at that line's own effective date.
So "the inventory asset on the balance sheet equals the total on the stock reports" is not a
convention this module observes -- it is a constraint the rows cannot violate. What this
module must supply is the other half: every inventory-asset leg a document writes is claimed
by exactly one movement, which ``inventory.check_attribution`` asserts before anything is
written.

## What is refused, and when

Decided before anything is built, so a refusal leaves nothing behind:

- **A closed period**, naming it, for the document's own date and for every correction date
  the change implies -- a true-up's date included. The whole change is refused; deltas are
  never moved to today.

## Stock sold before it arrives

Stock may go below zero, as in the anchor product, and the change says so rather than refusing:
``Change.warnings`` carries one line per sale that takes an item below zero -- this document's
own, and any earlier sale a void or a correction newly leaves short -- and ``settle`` puts them
on the result a person or an agent reads. The units below zero are costed provisionally by
``inventory_costing.replay``; the item's purchase cost is captured on every issue written here
so that fallback never moves when the item record is edited. A later receipt that fills the
shortfall owes each sale it fills a true-up, which ``plan`` writes as its own dated correction
document -- dated at the receipt, one line pair per sale, each movement naming the sale in
``corrects_movement_id`` and the receipt in ``filled_by_movement_id``. A return of a sale still
short gives back its unfilled provisional units first, at their provisional cost, and no
receipt trues those up.
Zero-value quantities retain their commercial ownership and batch with no monetary leg.
Their later recost corrections still target the original movement identity.

## The corrections are separate dated documents

A posting batch belongs to one revision and carries one effective date, so the deltas a
backdated purchase owes at three earlier dates cannot hang off the purchase. Each affected
date gets its own journal entry, marked ``recost`` in ``inventory_documents``, with one line
pair per corrected issue using that issue's own captured asset account, offset account and
class. They are written after the document itself, inside the same company transaction, and a
re-run finds nothing left to correct -- which is what makes a retry harmless.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlalchemy as sa

from bookflow.company import assembly_builds, inventory, item_conversions, journals, placements, schema as c
from bookflow.company.document_effects import allocate
from bookflow.company import costing_methods
from bookflow.company.inventory_costing import StockRefusal, replay, shortfall_warning
from bookflow.company.inventory_schema import INPUT_KINDS
from bookflow.company.journal_models import JournalPostInput
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Touched

# The one journal document kind this module writes. `inventory_schema.DOCUMENT_KINDS` is where
# the spelling is declared; naming the member rather than the string keeps it one declaration.
CORRECTION_KIND = 'recost'


@dataclass
class Entry:
    """One entered line that moves stock, before the document has any posting identity.

    ``key`` is whatever handle the calling document already has for the line -- an envelope id
    on a sale, an index on a bill being planned before its envelopes exist. It comes back on
    the movement and in ``costs`` so the caller can bind the two without matching on amounts.
    """

    key: str
    item_id: str
    item_name: str
    kind: str                      # 'receipt', 'issue' or 'vendor_return'
    quantity_microunits: int       # positive magnitude; the sign comes from the kind
    asset_account_id: str
    offset_account_id: str
    class_id: str | None = None
    value_minor_units: int | None = None   # a bought receipt and a vendor return state it; the rest ask costing
    returns_movement_id: str | None = None # the issue this receipt gives back, on a return
    sends_back_movement_id: str | None = None  # a vendor return's named receipt (R104 increment 7)
    site_id: str | None = None             # the inventory site the line names (else its document's); None defaults
    line_identity: str | None = None       # the entered line's stable identity, to keep its site across revisions
    numbers: object = None                 # serial_lots.Numbers the line states (R104 increment 4); None states none


def purchase_entry(facts, *, key, amount, offset_account_id, class_id):
    """Declare one receipt from captured purchase facts, with its actual funding offset."""
    if facts.item_type not in inventory.TRACKED_TYPES:
        return None
    return Entry(key=key, item_id=facts.item.id, item_name=facts.item.label,
                 kind='receipt', quantity_microunits=facts.quantity_microunits,
                 asset_account_id=facts.account.id, offset_account_id=offset_account_id,
                 class_id=class_id, value_minor_units=amount)


def vendor_return_entry(facts, *, key, amount, offset_account_id, class_id):
    """Declare stock sent back to a supplier, taken out of the asset at the credited amount.

    The inverse of ``purchase_entry``: the same two accounts, used the other way round. What it
    is worth is *stated* -- the amount the vendor credited, which the credit line posts to
    Inventory Asset -- so it is not the weighted average's answer and the average of what stays
    moves with it, exactly as the anchor product does with an item on a vendor credit.
    """
    if facts.item_type not in inventory.TRACKED_TYPES:
        return None
    return Entry(key=key, item_id=facts.item.id, item_name=facts.item.label,
                 kind='vendor_return', quantity_microunits=facts.quantity_microunits,
                 asset_account_id=facts.account.id, offset_account_id=offset_account_id,
                 class_id=class_id, value_minor_units=amount)


def return_entry(facts, *, key, quantity_microunits, issue_id, class_id):
    """Declare one receipt for stock a customer has brought back, naming the issue it undoes.

    The inverse of the issue ``sales._stock_entries`` made, declared here beside it: the same
    two accounts the sale captured, used the other way round, so the quantity goes back on the
    shelf and its cost comes out of cost of goods sold and back into the inventory asset.

    What it is worth is *not* stated. Unlike a bought receipt, whose value somebody knew and
    paid, a return is worth a share of what the issue it names is worth -- so the value is
    read off the replay, the way an issue's is, and a later backdated purchase that recosts
    that issue recosts this return with it.
    """
    if facts.item_type not in inventory.TRACKED_TYPES:
        return None
    return Entry(key=key, item_id=facts.item.id, item_name=facts.item.label,
                 kind='receipt', quantity_microunits=quantity_microunits,
                 asset_account_id=facts.asset_account.id, offset_account_id=facts.cogs_account.id,
                 class_id=class_id, returns_movement_id=issue_id)


def live_issue(s, transaction_id, document_line_id):
    """The stock issue one entered line of a sale is currently standing behind, or ``None``.

    A return names this movement and takes back a share of it. What that share is worth is
    replay's answer and is never computed here, because a figure worked out beside the ledger
    is a figure that goes stale the moment the ledger is recalculated -- which is exactly the
    defect that made a backdated purchase strand an earlier return at the cost it used to be.
    """
    issues = [row for row in own_movements(s, transaction_id)
              if row['kind'] == 'issue' and row['document_line_id'] == document_line_id]
    if not issues:
        return None
    if len(issues) > 1:
        raise BookflowError('E_INTERNAL', message=(
            'One entered line stands behind more than one live stock issue.'))
    return issues[0]


@dataclass
class Movement:
    """A movement waiting for the posting-line identities its document has not minted yet."""

    values: dict
    key: str | None = None                # the entry this came from, on a receipt or an issue
    reverses_line_id: str | None = None   # the original asset leg, on a reversal
    line_index: int | None = None         # entered-line position, inside a correction document


@dataclass
class Correction:
    """One dated correction document: its lines, and the movements they carry."""

    date: str
    memo: str
    lines: list
    movements: list = field(default_factory=list)


@dataclass
class Change:
    """Everything one document's stock activity implies, decided before any of it is built."""

    movements: list = field(default_factory=list)      # this document's own, in write order
    corrections: list = field(default_factory=list)    # one per affected earlier date
    costs: dict = field(default_factory=dict)          # entry key -> signed value minor units
    warnings: list = field(default_factory=list)       # sales this leaves below zero
    placements: list = field(default_factory=list)     # where its own movements' units are (R104)
    sites: dict = field(default_factory=dict)          # movement id -> site, for its own movements
    numbers: dict = field(default_factory=dict)        # movement id -> what its line says of lot and serial numbers
    identities: dict = field(default_factory=dict)     # movement id -> its entered line's stable identity (R104 inc 6)
    carries: list = field(default_factory=list)        # (retired receipt, its restatement) carrying a landed cost

    @property
    def moves_stock(self):
        return bool(self.movements) or bool(self.corrections)

    def by_key(self, key):
        return [movement for movement in self.movements if movement.key == key]

    def rows(self, *, created_at, created_by, created_via, audit_event_id):
        """The document's own movement rows, ready to insert."""
        return [dict({'landed_on_movement_id': None}, **movement.values, created_at=created_at, created_by=created_by,
                     created_via=created_via, audit_event_id=audit_event_id)
                for movement in self.movements]


def own_movements(s, transaction_id):
    """The stock a document is currently standing behind: its inputs no reversal has retired.

    A correction and a void both reverse the current business batch, and the reversing legs
    belong to the same transaction, so a movement's retirement is always recorded here beside
    the movement it retires. That is what makes this the whole answer rather than a first
    approximation of it.
    """
    rows = inventory.movements(s, transaction_id=transaction_id)
    retired = {row['reverses_movement_id'] for row in rows if row['kind'] == 'reversal'}
    return [row for row in rows if row['kind'] in INPUT_KINDS and row['id'] not in retired]


def position_moves(s, transaction_id, stated):
    """Whether a correction stating ``stated`` moves the document against its date's counts (R104).

    A correction that states no position keeps the one its stock was placed at, as a line keeps its site.
    """
    if stated is None or transaction_id is None:
        return False
    own = own_movements(s, transaction_id)
    return bool(own) and any((row.get('count_position') or 'before') != stated for row in own
                             if row['kind'] != 'recost')


def document_position(s, transaction_id):
    """The position a document's stock stands at against its date's counts: before, unless it says after."""
    own = own_movements(s, transaction_id) if transaction_id else []
    return 'after' if any(row.get('count_position') == 'after' for row in own) else 'before'


def plan(s, *, entries, reversing=(), date=None, currency, field='lines', sequence=None, repricing=(),
         count_position=None, assembly_key=None):
    """Every movement and every correction this document implies, and nothing written yet.

    ``reversing`` is what the previous revision moved, from ``own_movements``; ``entries`` is
    what the new revision moves. Both are replayed into the item's whole history together, so
    a correction that swaps one quantity for another is one replay and not two, and the
    negative-stock check sees the net effect rather than a transient the caller never asked
    for.
    """
    sequence = inventory.next_sequence(s) if sequence is None else sequence
    working: dict[str, list] = {}
    stored: dict[str, list] = {}

    def rows(item_id):
        if item_id not in working:
            stored[item_id] = list(inventory.movements(s, item_id=item_id))
            working[item_id] = list(stored[item_id])
        return working[item_id]

    def refuse(refusal, item_id, item_name):
        return inventory.refuse_stock(refusal, {'id': item_id, 'full_name': item_name},
                                      quantity_field=field, value_field=field)

    change = Change()
    # Where the units go (R104): a line's stated site, else the issue a return gives back, else where
    # the revision being replaced put the same item, else the default site.
    # Keyed by the entered line's stable identity, so two lines of one item at two sites keep their
    # own; a line with no identity (a build's) falls back to where the same item was.
    prior_sites, prior_item_sites, prior_numbers, prior_bins, prior_when = {}, {}, {}, {}, {}
    if reversing:
        from bookflow.company import serial_lots
        carried = serial_lots.line_numbers(s, [row['id'] for row in reversing if row['kind'] in INPUT_KINDS])
        stored_places = placements.movement_placements(s, [row['id'] for row in reversing])
        identities = dict(s.company.conn.execute(sa.select(c.document_lines.c.id, c.document_lines.c.line_id).where(
            c.document_lines.c.id.in_([row['document_line_id'] for row in reversing]))).all())
        for row in reversing:
            places = sorted(stored_places.get(row['id'], ()), key=lambda p: -abs(int(p['quantity_microunits'])))
            if places:
                identity = identities.get(row['document_line_id'])
                if identity:
                    prior_sites.setdefault(identity, places[0]['site_id'])
                    prior_bins.setdefault(identity, (places[0]['site_id'], places[0]['bin_id']))
                    prior_when.setdefault(identity, (row['effective_date'], row.get('count_position') or 'before'))
                    if row['id'] in carried:
                        prior_numbers.setdefault(identity, carried[row['id']])
                prior_item_sites.setdefault(row['item_id'], places[0]['site_id'])
    # A correction that states no position keeps the one its stock stood at (R104 increment 2).
    stated = count_position
    if count_position is None:
        count_position = next((row.get('count_position') for row in reversing
                               if row['kind'] in INPUT_KINDS and row.get('count_position') in ('before', 'after')),
                              'before')
    for row in reversing:
        values = dict(
            id=new_id(), item_id=row['item_id'], kind='reversal',
            quantity_microunits=-int(row['quantity_microunits']),
            value_minor_units=-int(row['value_minor_units']),
            effective_date=row['effective_date'], sequence=sequence,
            currency=row['currency'], asset_account_id=row['asset_account_id'],
            offset_account_id=row['offset_account_id'], class_id=row['class_id'],
            corrects_movement_id=None, reverses_movement_id=row['id'], returns_movement_id=None,
            filled_by_movement_id=None, fallback_unit_cost_minor_units=None, fallback_unit_cost_fraction=None,
            count_position=row.get('count_position') or 'before',
            transaction_id=row['transaction_id'], revision_id=row['revision_id'], document_line_id=row['document_line_id'],
            posting_line_id=None, posting_batch_id=None)
        rows(row['item_id']).append(values)
        change.movements.append(Movement(values, reverses_line_id=row['posting_line_id']))
        sequence += 1

    for entry in entries:
        # An item converted to an inventory part carries no stock before the conversion (R154).
        item_conversions.require_stock_date(s, entry.item_id, entry.item_name, date, field)
        history = rows(entry.item_id)
        identity = new_id()
        position = count_position
        if entry.returns_movement_id is not None:
            # A return follows the sale it gives back: on the sale's own date it takes the sale's position,
            # so it never replays ahead of what it returns.
            issue = s.company.conn.execute(sa.select(c.inventory_movements.c.effective_date,
                                                     c.inventory_movements.c.count_position).where(
                c.inventory_movements.c.id == entry.returns_movement_id)).first()
            if issue is not None and issue.effective_date == date:
                rank = {'before': 0, 'count': 1, 'after': 2}
                if stated is not None and rank[stated] < rank[issue.count_position]:
                    raise BookflowError('E_VALIDATION', message=(
                        f'A return of {entry.item_name} follows its sale, which is placed {issue.count_position} '
                        f"the count on {date}; leave count_position out, or say {issue.count_position}."),
                        details={'fields': [{'field': 'count_position', 'problem': (
                            f'a return follows its sale, which is placed {issue.count_position} the count')}]})
                if stated is None:
                    position = issue.count_position
        signed = (entry.quantity_microunits if entry.kind == 'receipt'
                  else -entry.quantity_microunits)
        values = dict(
            id=identity, item_id=entry.item_id, kind=entry.kind,
            quantity_microunits=signed,
            value_minor_units=(entry.value_minor_units
                               if entry.kind == 'receipt' and entry.returns_movement_id is None
                               else -entry.value_minor_units if entry.kind == 'vendor_return'
                               else -1),
            effective_date=date, sequence=sequence, currency=currency,
            asset_account_id=entry.asset_account_id, offset_account_id=entry.offset_account_id,
            class_id=entry.class_id, corrects_movement_id=None, reverses_movement_id=None,
            returns_movement_id=(entry.sends_back_movement_id if entry.kind == 'vendor_return'
                                 else entry.returns_movement_id), filled_by_movement_id=None,
            **inventory.fallback_columns(s, entry.item_id, currency, entry.kind),
            count_position=position)
        if entry.kind == 'issue' or entry.returns_movement_id is not None:
            # What it is worth is costing's answer, not the caller's, so the value is read off
            # a first replay and written back before the corrections are worked out. An issue
            # asks what the running average consumes; a return asks for its share of the issue
            # it gives back. Neither is a number this module is entitled to invent.
            try:
                computed = replay(history + [values], costing_methods.timeline(s)).targets.get(identity)
            except StockRefusal as refusal:
                raise refuse(refusal, entry.item_id, entry.item_name) from None
            if computed is None:
                raise BookflowError('E_INTERNAL', message=(
                    'A stock return names an issue this ledger cannot value it against.'))
            values['value_minor_units'] = computed
        site = (entry.site_id
                or (prior_sites.get(entry.line_identity) if entry.line_identity else None)
                or (placements.site_of_movement(s, entry.returns_movement_id) if entry.returns_movement_id else None)
                or (prior_item_sites.get(entry.item_id) if entry.line_identity is None else None))
        if site is not None:
            change.sites[identity] = site
        # The line's bin (R104 increment 5): the one it names, else where its stock stood (a correction) or its
        # sale's (a return) when at this same site, else the item's default bin there, else none.
        from bookflow.company import bins
        placed_at = site or placements.default_site_id(s)
        fallback = bins.UNSET
        prior_place = prior_bins.get(entry.line_identity) if entry.line_identity else None
        if prior_place is not None and prior_place[0] == placed_at:
            fallback = prior_place[1]
        elif entry.returns_movement_id:
            issued = placements.place_of_movement(s, entry.returns_movement_id)
            if issued is not None and issued[0] == placed_at:
                fallback = issued[1]
        bin_id = bins.line_bin(s, entry.item_id, placed_at, entry.numbers.bin if entry.numbers is not None else None,
                               field=f'{field}.bin', fallback=fallback)
        if prior_place is not None and (placed_at, bin_id) != tuple(prior_place):
            # A correction moving a line's stock to another place, after a count found it at the old place, would
            # leave the count's shelf as counted and put the units at the new place too: the stock doubles.
            from bookflow.company import inventory_counts
            when = prior_when.get(entry.line_identity) or (date, 'before')
            number = inventory_counts.covering_count(s, entry.item_id, prior_place[0], prior_place[1], *when)
            if number is not None:
                old, new = bins.place_words(s, *prior_place), bins.place_words(s, placed_at, bin_id)
                raise BookflowError('E_VALIDATION', message=(
                    f'Count {number} found these {entry.item_name} in {old}; record a transfer from {old} to {new} '
                    'dated after the count instead of moving this line.'),
                    details={'fields': [{'field': f'{field}.bin', 'problem': (
                        f'count {number} found these in {old}; record a transfer from {old} to {new} dated after '
                        'the count instead')}], 'count': number})
        # An outgoing line that names no bin, and keeps none from a correction or a sale, takes its units from
        # where they are at the site (R104 increment 5 review).
        named = entry.numbers is not None and entry.numbers.bin is not None
        auto = (not named and fallback is bins.UNSET and entry.kind in ('issue', 'vendor_return')
                and bins.site_tracks_bins(s, placed_at))
        change.numbers[identity] = dict(
            spec=entry.numbers, item_name=entry.item_name, returns=entry.returns_movement_id, field=field,
            prior=prior_numbers.get(entry.line_identity) if entry.line_identity else None, bin_id=bin_id,
            bin_auto=auto)
        if entry.line_identity:
            change.identities[identity] = entry.line_identity
        history.append(values)
        change.movements.append(Movement(values, key=entry.key))
        change.costs[entry.key] = values['value_minor_units']
        sequence += 1

    # Immutable bill-owned deltas target the original physical receipt, never a
    # second receipt or a later value input. Replaying below then owes sales their
    # own dated COGS corrections.
    for target, delta, vendor_id in repricing:
        if not delta:
            continue
        lines = inventory.pair(target['asset_account_id'], target['offset_account_id'],
            delta, currency, target['class_id'], 'Receipt purchase-price correction')
        lines[1] = lines[1].model_copy(update={'name_type': 'vendor', 'name_id': vendor_id})
        values = dict(id=new_id(), item_id=target['item_id'], kind=CORRECTION_KIND,
            quantity_microunits=0, value_minor_units=delta, effective_date=target['effective_date'],
            sequence=sequence, currency=currency, asset_account_id=target['asset_account_id'],
            offset_account_id=target['offset_account_id'], class_id=target['class_id'],
            corrects_movement_id=target['id'], reverses_movement_id=None, returns_movement_id=None,
            filled_by_movement_id=None, fallback_unit_cost_minor_units=None, fallback_unit_cost_fraction=None,
            count_position=target.get('count_position') or 'before')
        rows(target['item_id']).append(values)
        change.corrections.append(Correction(target['effective_date'], 'Receipt purchase-price correction',
            lines, [Movement(values, line_index=1)]))
        sequence += 1

    if reversing:
        # A receipt restated unchanged carries its landed cost on, so replay reads it there (R104 increment 6);
        # whether its places are unchanged too is checked once they are worked out, below.
        from bookflow.company import landed_costs
        for old, new in landed_costs.check_targets(s, reversing, change, places_known=False):
            for history in working.values():
                for index, row in enumerate(history):
                    if row['kind'] == 'landed' and row.get('landed_on_movement_id') == old:
                        history[index] = dict(row, landed_on_movement_id=new)
    owed = []
    own = {movement.values['id'] for movement in change.movements}
    names = {entry.item_id: entry.item_name for entry in entries}
    # Components before the assemblies built from them (R103): a correction of a build's component
    # issue carries a partner correction of the build's assembly receipt, and the assembly is then
    # replayed with it. With no builds in the company this is one replay per item, as before.
    # A build being written (``assembly_key`` names its assembly receipt entry): its own component issues are a
    # build's too, so a landed cost's forward true-up of one is carried by its new assembly receipt (R104 inc 6).
    planned = {}
    if assembly_key is not None:
        made = next((m.values for m in change.movements if m.key == assembly_key), None)
        if made is not None:
            planned = {m.values['id']: made for m in change.movements
                       if m.values['kind'] == 'issue' and m.key != assembly_key}
    states, partners = assembly_builds.replay_items(
        s, working, rows, lambda refusal, item_id: refuse(
            refusal, item_id, names.get(item_id) or inventory.item_name(s, item_id)), planned=planned)
    for item_id, state in states:
        owed.extend((item_id, correction) for correction in state.corrections)
        if state.shortfalls:
            try:
                before = {shortfall.movement['id'] for shortfall in replay(stored[item_id], costing_methods.timeline(s)).shortfalls}
            except StockRefusal:
                before = set()
            for shortfall in state.shortfalls:
                if shortfall.movement['id'] in own or shortfall.movement['id'] not in before:
                    change.warnings.append(shortfall_warning(
                        names.get(item_id) or inventory.item_name(s, item_id), shortfall))

    # One correction document per affected date, and a true-up -- dated at the receipt that
    # settled a provisional cost -- in a document of its own, so the kind of every correction
    # a person reads in the journal is the kind its memo says it is.
    by_date: dict[tuple, list] = {}
    for item_id, correction in owed:
        by_date.setdefault((correction.effective_date, correction_kind(correction)),
                           []).append((item_id, correction))
    for affected, kind in sorted(by_date):
        true_up = kind != 0
        lines, movements = [], []
        for item_id, correction in by_date[(affected, kind)]:
            target = correction.target_movement
            description = correction_description(correction)
            lines.extend(inventory.pair(
                target['asset_account_id'], target['offset_account_id'],
                correction.delta_minor_units, currency, target['class_id'], description))
            partner = partners.get((target['id'], correction.filled_by['id'] if correction.filled_by is not None else None))
            movements.append(Movement(dict(
                id=new_id(), item_id=item_id, kind=CORRECTION_KIND,
                quantity_microunits=0, value_minor_units=correction.delta_minor_units,
                effective_date=affected, sequence=sequence, currency=currency,
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
                # The build's receipt of the assembly takes the other side, on the offset line.
                partner['sequence'] = sequence
                movements.append(Movement(partner, line_index=len(lines)))
                sequence += 1
        change.corrections.append(Correction(affected, CORRECTION_MEMOS[kind], lines, movements))
    from bookflow.company import serial_lots
    numbering = serial_lots.Numbering(s)
    change.placements = placements.for_movements(s, [movement.values for movement in change.movements], change.sites,
                                                 change.numbers, numbering)
    change.warnings.extend(placements.warnings(s, change.placements, names))
    change.warnings.extend(w for w in numbering.warnings if w not in change.warnings)
    if reversing:
        # A receipt a landed-cost allocation lands on is not voided, and its stock is not changed under it; one
        # restated unchanged (a memo edit) carries its landed cost on (R104 increment 6).
        from bookflow.company import landed_costs
        change.carries = landed_costs.check_targets(s, reversing, change)
    return change


# A correction document holds one kind of correction, so its memo says what each line is: a correction at a
# movement's own date, a provisional cost's true-up at the receipt that filled it, or a landed cost's forward
# true-up at the allocation (R104 increment 6).
CORRECTION_MEMOS = ('Weighted-average cost correction', 'Provisional cost true-up', 'Landed cost true-up')


def correction_kind(correction):
    if correction.filled_by is None:
        return 0
    return 2 if correction.filled_by['kind'] == 'landed' else 1


def correction_description(correction):
    """One correction line's description: what it corrects, and what filled or landed on it."""
    target = correction.target_movement
    kind = correction_kind(correction)
    if kind == 2:
        return (f'Landed cost true-up for movement {target["id"]} dated {target["effective_date"]}, '
                f'landed cost movement {correction.filled_by["id"]}')
    if kind == 1:
        return (f'Provisional cost true-up for movement {target["id"]} dated {target["effective_date"]}, '
                f'filled by receipt movement {correction.filled_by["id"]}')
    return f'Weighted-average cost correction for movement {target["id"]} dated {target["effective_date"]}'


def open_dates(s, change, dates=()):
    """Refuse the whole change, naming the period, if any date it writes to is closed."""
    journals.open_dates(s, sorted({*dates, *(m.values['effective_date'] for m in change.movements), *(c.date for c in change.corrections)}))


def bind(movement, leg, *, transaction_id, revision_id, document_line_id, batch=None):
    """Give one planned movement the posting and entered-line identities just minted."""
    movement.values.update(transaction_id=transaction_id, revision_id=revision_id,
                           posting_batch_id=leg['batch_id'] if leg else batch['id'], posting_line_id=leg['id'] if leg else None,
                           document_line_id=document_line_id)


def bind_reversals(change, posting_lines, batches=()):
    """Bind every reversal movement to the leg that reverses the one it names."""
    by_original = {leg['reversed_line_id']: leg for leg in posting_lines
                   if leg['reversed_line_id'] is not None}
    for movement in change.movements:
        if movement.values.get('reverses_movement_id') and movement.reverses_line_id is None:
            inverse = next(b for b in batches if b['kind'] == 'reversal' and b['revision_id'] == movement.values['revision_id'])
            movement.values.update(posting_batch_id=inverse['id'], posting_line_id=None)
            continue
        if movement.reverses_line_id is None:
            continue
        leg = by_original.get(movement.reverses_line_id)
        if leg is None:
            raise BookflowError('E_INTERNAL', message=(
                'A correction did not reverse the inventory posting line one of its stock '
                'movements belongs to.'))
        movement.values.update(transaction_id=leg['transaction_id'],
                               posting_batch_id=leg['batch_id'], posting_line_id=leg['id'])


def check(s, change, posting_lines):
    """Every inventory-asset leg this document writes is claimed by exactly one movement.

    ``posting_lines`` is every leg the document is about to write, the reversing batch
    included: a correction reverses the asset leg it used to have and writes a new one, and
    both halves need a movement or the ledger stops explaining the control account.
    """
    inventory.check_attribution(
        [movement.values['posting_line_id'] for movement in change.movements if movement.values['value_minor_units']],
        posting_lines, inventory.asset_account_ids(s))


def _blocked(error, correction):
    """Explain a refusal of a correction document the caller never wrote."""
    return BookflowError(error.code, message=(
        f'This change owes a stock cost correction dated {correction.date}, and that '
        f'correction could not be posted: {error.message} Nothing was written. A generated '
        'correction carries no per-document entry, so a required journal field needs a '
        'default before a document that moves stock can be backdated.'),
        details={**error.details, 'blocked_correction_date': correction.date})


def write_corrections(s, ctx, change, *, command_name, created_at=None):
    """Post every dated correction this change owes, inside the caller's own transaction."""
    if not change.corrections:
        return []
    at = created_at or clock.now_iso()
    control = inventory.asset_account_ids(s)
    reserved, touched = [], []
    for correction in change.corrections:
        # The document this correction belongs to allocated its own number in its own series;
        # each correction takes the next free journal number after the ones already handed out.
        number, _ = allocate(s, 'journal_entry', None, reserved=reserved)
        journal = JournalPostInput(date=correction.date, memo=correction.memo,
                                   lines=correction.lines, number=number)
        try:
            fresh = journals.prepare(s, ctx, journal, 'post', owner=inventory.OWNER)
        except BookflowError as error:
            raise _blocked(error, correction) from None
        if not fresh.data['changed']:
            raise BookflowError('E_INTERNAL', message='A stock cost correction produced no accounting.')
        reserved.append(fresh.data['header']['number'])
        pending = fresh.data['pending']
        revision = pending['transaction_revisions'][0]
        entered = {line['position']: line for line in pending['document_lines']}
        legs = {line['line_no']: line for line in pending['posting_lines']}
        for movement in correction.movements:
            bind(movement, legs[movement.line_index], transaction_id=fresh.data['header']['id'],
                 revision_id=revision['id'],
                 document_line_id=entered[movement.line_index]['id'])
        inventory.check_attribution(
            [movement.values['posting_line_id'] for movement in correction.movements],
            pending['posting_lines'], control)
        provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
                          audit_event_id=fresh.data['event'])
        marker = dict(transaction_id=fresh.data['header']['id'], type='journal_entry',
                      kind=CORRECTION_KIND, **provenance)
        rows = [dict({'landed_on_movement_id': None}, **movement.values, **provenance) for movement in correction.movements]
        applied = journals.persist_prepared(
            fresh, ctx, s, command_name=command_name,
            extra=(('inventory_documents', 'inventory_document', 'transaction_id', [marker]),
                   *placements.extras(placements.placements_for_rows(s, rows)),
                   ('inventory_movements', 'inventory_movement', 'id', rows)),
            noun='inventory cost correction')
        touched.extend(applied.touched)
    return touched


def write_movements(s, ctx, change, *, command_name, summary, created_at):
    """Insert the document's own movements, once every posting line they name exists.

    They are written after the document rather than beside them for one reason: a sale has
    four persistence paths -- the plain one, the work-billing one, the settlement-carrying
    invoice correction and the deposit coordinate operation -- and this is the one seam all of
    them pass through. One seam is worth more here than one audit event.
    """
    event = new_id()
    rows = change.rows(created_at=created_at, created_by=s.actor.id,
                       created_via=ctx.interface.value, audit_event_id=event)
    if not rows:
        return []
    # Placed first: the database refuses a movement whose placements, already written, do not sum
    # to its quantity (R104).
    placed = placements.placements_for_rows(s, rows, change.sites, change.placements)
    touched, clean = placements.number_touched(placed)
    touched += [Touched('inventory_placement', row['id'], 'create', None, 1, row, db='company')
                for row in clean]
    touched += [Touched('inventory_movement', row['id'], 'create', None, 1, row, db='company')
                for row in rows]
    carries = [dict(id=new_id(), from_movement_id=old, to_movement_id=new, created_at=created_at,
                    created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)
               for old, new in getattr(change, 'carries', ())]
    touched += [Touched('landed_cost_carry', row['id'], 'create', None, 1, row, db='company') for row in carries]
    audit.write_event_to(s.company, ctx, command_name, summary, touched, actor_id=s.actor.id,
                         actor_kind=s.actor.kind,
                         directive_code=getattr(s, 'directive_code', None), event_id=event)
    placements.insert(s, placed)
    s.company.conn.execute(c.inventory_movements.insert(), rows)
    if carries:
        s.company.conn.execute(c.landed_cost_carries.insert(), carries)
    return touched


def settle(applied, change, ctx, s, *, command_name, summary, created_at=None):
    """Write everything one document's stock activity owes, beside the document itself.

    Its own movements first, then the dated corrections earlier sales are owed. Both join the
    company transaction the document was written in, so either all of it is durable or none of
    it is; a re-run of the same change finds nothing left to correct, which is what makes a
    retry harmless.
    """
    at = created_at or clock.now_iso()
    touched = write_movements(s, ctx, change, command_name=command_name, summary=summary,
                              created_at=at)
    touched += write_corrections(s, ctx, change, command_name=command_name, created_at=at)
    output = warn(applied.output, change)
    if not touched:
        return applied if output is applied.output else Applied(
            output, applied.touched, applied.summary, finalized=applied.finalized,
            audited=applied.audited, after_commit=applied.after_commit)
    return Applied(output, list(applied.touched) + touched, applied.summary,
                   finalized=applied.finalized, audited=True, after_commit=applied.after_commit)


def warn(output, change):
    """``output`` with this change's below-zero warnings added once, where it carries any.

    Every result a stock-moving write returns -- its preview and its saved answer alike --
    passes through here, so a sale that takes an item below zero says so on every surface.
    """
    extra = list(getattr(change, 'warnings', None) or ())
    if not extra or not hasattr(output, 'warnings'):
        return output
    current = list(output.warnings or [])
    merged = current + [line for line in dict.fromkeys(extra) if line not in current]
    if merged == current:
        return output
    return output.model_copy(update={'warnings': merged})
