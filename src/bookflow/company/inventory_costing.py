"""Weighted-average replay, and the dated corrections a backdated change owes.

Nothing here touches a database. It takes one item's movement rows in their stable order and
answers two questions: what the books *should* say, and what has to be posted to make them say
it. Keeping that arithmetic in a function with no session is what lets the hard cases be
tested directly instead of through four commands.

## What replay is

Walk the item's *input* movements -- receipts, issues and value adjustments that no reversal
has retired -- in ``(effective_date, sequence, id)`` order, carrying on-hand quantity ``Q`` and
asset value ``V``. A bought receipt adds both, including active receipt-targeted purchase-price
corrections at that original acquisition position. A value adjustment moves value alone. An issue takes
quantity out and consumes value at the running weighted average:

    consumed = V                                   when the issue empties the stock
    consumed = round_half_even(V * |q| / Q)        otherwise

A **return** -- a receipt naming an issue in ``returns_movement_id`` -- is that issue read
backwards, so its value is an output of this walk and not a number anybody states. The returns
of one issue take contiguous spans of its issued quantity in the order they are walked and
divide its consumed value by ``core.exact.endpoint_share``, the same rule the credit memo's own
money goes through:

    target(R) = endpoint_share(consumed(M), |q(M)|, low, low + q(R))

They therefore telescope to exactly ``consumed(M)``, whatever order they were taken in and
however many pieces the line came back in. That is the point of computing them here rather than
beside the ledger: when a backdated purchase moves ``consumed(M)``, every return against ``M``
moves with it and is owed a correction like the issue itself, so nothing is left standing at a
cost that no longer exists. A return whose issue is retired, or dated ahead of it, has no cost
to take a share of; its stated value stands and no correction is computed for it.

The first line is the whole of the zero-residual rule. Computing the last issue from a rounded
average would leave a few minor units of value sitting behind no quantity for ever; taking the
remainder outright cannot.

Neither line ever divides before multiplying and neither ever sees a float: both operands are
exact integers and ``round_half_even`` resolves the single division once.

## Stock sold before it arrives

An issue may take more than is on hand. The part covered by stock consumes the value on hand as
above; the part below zero -- the *shortfall* -- is costed at a **provisional** unit cost, chosen
in this order and stated in ``Shortfall.basis``:

1. ``average`` -- the running weighted average: ``V / Q`` when stock is on hand, otherwise the
   last average the item had while it held stock (or the unit cost of the last receipt that
   filled a shortfall, when that receipt left nothing over);
2. ``purchase_cost`` -- the item's purchase cost as captured on the issue itself in
   ``fallback_unit_cost_minor_units``, when the item has never held stock;
3. ``none`` -- zero, when there is neither.

    provisional = round_half_even(rate_value * shortfall / rate_quantity)

The issue's own target is what it took from the shelf plus that provisional amount, posted at its
own date exactly as a normal sale. While ``Q < 0`` the item's value is minus the provisional cost
still waiting for stock, so the stock ledger and the balance sheet still agree.

A receipt that arrives while ``Q < 0`` **fills the shortfall first**, oldest issue first. For the
units that fill one issue's shortfall it owes a **true-up**: the provisional amount those units
were carrying less what the receipt actually paid for them,

    released = endpoint_share(P, S, filled, filled + u)     # the issue's provisional, telescoping
    actual   = endpoint_share(w, r, a, a + u)               # the receipt's own value, telescoping
    true_up  = released - actual                            # asset delta; minus is more COGS

keyed by ``(issue, receipt)`` and dated **at the receipt**, never back at the sale: the real cost
is not known until the goods arrive, and dating it there never reopens an earlier period. After the
shortfall is filled the receipt's remaining units carry the rest of its value, so the average goes
on from real receipts only.

Nothing about the backdating rule changes. A purchase entered in front of a short sale makes the
sale no longer short on its own date, so its own target moves back to the real average and the
existing per-issue correction carries that at the sale's date, while the true-ups the old walk had
keyed to it fall to a target of zero and are backed out at their receipts' dates.

A **return** of a short issue first cancels that issue's *unfilled* shortfall units, taken from
the back of the shortfall while receipts fill it from the front, at exactly their provisional
value:

    cancelled = endpoint_share(P, S, S - c_before - c, S - c_before)

Those units settle the issue's own shortfall and fill nobody else's, so no receipt ever trues up
a unit that came back. Units returned beyond the unfilled ones take the existing endpoint share
of the issue's settled cost -- its own value plus every true-up keyed to it -- less what was
cancelled, over its quantity less what was cancelled. The unfilled count only falls, so every
cancellation precedes every share and that pool is fixed before a share is taken; an issue that
was never short cancels nothing and the rule is exactly the old one. Either way the returns of
one issue telescope to exactly what that issue cost.

## Why a correction is not an input

``recost`` rows are what replay *produced* last time -- against an issue, and now against a
return too -- and ``reversal`` rows retire what a void took back. Replaying either as if it were a new purchase or a new sale would fold a correction
into the average that the correction was computed from -- the mistake the costing decision
names explicitly. So the walk reads ``INPUT_KINDS`` only, and the two derived kinds are read
to work out what is already posted. A receipt-targeted recost is instead an explicit
purchase-price input attached to the original receipt; it is never walked as a later
value adjustment. Issue-targeted recosts, and the recosts against the returns that mirror
them, remain replay outputs.

## What "already posted" means, and why nothing is corrected twice

For one issue ``M``, and for one return ``R`` alike -- they are the two kinds of movement
whose value this walk decides rather than reads:

    posted_effective(X) = X.value + sum(active recosts of X) + sum(reversals of X)
    target(X)           = 0 when X is retired, else replay's computed value
    delta               = target - posted_effective

A delta of zero writes nothing, which is what makes running the recalculation again a no-op
and a retry harmless. A retired issue's target is zero, so the same subtraction also backs out
the corrections that were posted against it while it stood -- there is no second rule for
voids, because the first one already covers them.

Summing the same identity over every row shows why the stock reports and the balance sheet
cannot drift apart: total posted value is the bought receipts and value adjustments plus each
issue's and each return's ``posted_effective``, which after correction is its target, which is
exactly the ``V`` this replay computed. Because the returns of an issue telescope to what that
issue consumed, a line that was wholly sold and wholly returned leaves nothing behind in the
asset account -- which is the property that a stated return cost silently broke.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from bookflow.company.inventory_schema import INPUT_KINDS
from bookflow.core.exact import QUANTITY_SCALE, endpoint_share, round_ratio_half_even

MICRO = 10 ** QUANTITY_SCALE


class StockRefusal(Exception):
    """A replay that cannot stand: negative stock, negative value, or unvalued stock.

    Carried as an exception rather than returned because every caller refuses the whole
    change on it; the command layer turns it into the error code a person reads.
    """

    def __init__(self, reason: str, movement: Mapping | None, **details):
        self.reason = reason
        self.movement = movement
        self.details = details
        super().__init__(reason)


@dataclass(frozen=True)
class Correction:
    """One dated value delta a caller owes against one issue, or one return of an issue.

    ``filled_by`` is set on a true-up: the receipt whose arrival settled a provisional cost,
    and ``effective_date`` is then that receipt's date rather than the issue's.
    """

    target_movement: Mapping
    delta_minor_units: int
    effective_date: str
    filled_by: Mapping | None = None


@dataclass(frozen=True)
class Shortfall:
    """One issue that took an item below zero on its own date, and how it was costed."""

    movement: Mapping
    quantity_after_microunits: int
    shortfall_microunits: int
    provisional_minor_units: int
    basis: str                      # 'average', 'purchase_cost' or 'none'


@dataclass(frozen=True)
class Replay:
    quantity_microunits: int
    value_minor_units: int
    targets: dict[str, int]
    corrections: tuple[Correction, ...] = ()
    shortfalls: tuple[Shortfall, ...] = ()
    true_ups: dict = field(default_factory=dict)   # (issue id, receipt id) -> target delta

    @property
    def average_cost_minor_units(self) -> int:
        """Value per one whole unit, rounded once for display only."""
        if self.quantity_microunits <= 0:
            return 0
        return round_ratio_half_even(self.value_minor_units * MICRO, self.quantity_microunits)


def ordered(rows: Iterable[Mapping]) -> list[Mapping]:
    """One item's movements in the order replay and every report must read them."""
    return sorted(rows, key=lambda row: (row['effective_date'], int(row['sequence']), row['id']))


def consumed_value(value_minor_units: int, quantity_microunits: int, issued_microunits: int) -> int:
    """What issuing ``issued_microunits`` takes out of ``value_minor_units``.

    ``issued_microunits`` is a positive magnitude and must not exceed what is on hand; the
    caller checks that, because the refusal it raises names the item and the date.
    """
    if issued_microunits == quantity_microunits:
        return value_minor_units
    return round_ratio_half_even(value_minor_units * issued_microunits, quantity_microunits)


def _retired(rows: Sequence[Mapping]) -> set[str]:
    return {row['reverses_movement_id'] for row in rows if row['kind'] == 'reversal'}


def _posted(rows, retired):
    """What is already posted against each movement, and against each (issue, receipt) true-up."""
    own: dict[str, int] = {}
    true_ups: dict[tuple[str, str], int] = {}
    for row in rows:
        if row['kind'] == 'recost' and row['id'] not in retired:
            filled_by = row.get('filled_by_movement_id')
            if filled_by is None:
                own[row['corrects_movement_id']] = own.get(row['corrects_movement_id'], 0) + int(row['value_minor_units'])
            else:
                key = (row['corrects_movement_id'], filled_by)
                true_ups[key] = true_ups.get(key, 0) + int(row['value_minor_units'])
        elif row['kind'] == 'reversal':
            own[row['reverses_movement_id']] = own.get(row['reverses_movement_id'], 0) + int(row['value_minor_units'])
    return own, true_ups


def replay(rows: Iterable[Mapping]) -> Replay:
    """Canonical on-hand quantity and value, plus the corrections the posted rows still owe."""
    rows = ordered(rows)
    retired = _retired(rows)
    posted, posted_true_ups = _posted(rows, retired)

    quantity = value = 0
    rate: tuple[int, int] | None = None   # (value, quantity) of the last known average
    targets: dict[str, int] = {}
    true_ups: dict[tuple[str, str], int] = {}
    open_shortfalls: list[dict] = []       # oldest first: issue, units, provisional, filled, cancelled
    shortfalls: list[Shortfall] = []
    shortfall_of: dict[str, dict] = {}     # issue id -> its shortfall entry, open or done
    by_id = {row['id']: row for row in rows}
    issued_rows = {row['id']: row for row in rows if row['kind'] == 'issue'}
    given_back: dict[str, int] = {}     # issue id -> quantity its returns have taken back
    cancelled_worth: dict[str, int] = {}  # issue id -> provisional value its returns cancelled
    stale: set[str] = set()             # returns this walk cannot speak for
    for row in rows:
        if row['kind'] not in INPUT_KINDS or row['id'] in retired:
            continue
        change = int(row['quantity_microunits'])
        if row['kind'] == 'receipt':
            issue_id = row.get('returns_movement_id')
            cancelled = cancelled_value = 0     # units handed straight back to their own sale
            if issue_id is None:
                # Receipt-targeted purchase-price corrections belong at acquisition,
                # even when a bill is recorded after intervening issues.
                worth = int(row['value_minor_units']) + posted.get(row['id'], 0)
            elif issue_id not in targets:
                # The issue it mirrors is retired, or is dated after it and so has no cost
                # yet at this point in the walk. Nothing here can say what that return is
                # worth, so its stated value stands and no correction is computed for it.
                stale.add(row['id'])
                worth = int(row['value_minor_units']) + posted.get(row['id'], 0)
            else:
                # A return is the inverse of the issue it names, so it gives back a share of
                # what that issue is worth *now*. That is what carries a backdated purchase
                # through to the returns it displaced instead of stranding them at a stale
                # cost. The share is the next contiguous span of the issued quantity, divided
                # by the same endpoint rule the credit's own money goes through, so the
                # returns of one issue telescope to exactly what that issue consumed.
                #
                # A sale still short on stock first gets back its *unfilled* provisional
                # units, taken from the back of its shortfall at exactly the provisional cost
                # they went out at, so no true-up is ever owed on a unit that came back.
                # Only what is returned beyond them takes a share -- of the sale's settled
                # cost less what was cancelled, over its units less those cancelled. The
                # unfilled count only falls, so every cancellation precedes every share and
                # that pool is fixed before any share is taken; a sale never short cancels
                # nothing and its pool is the whole issue, exactly as before.
                entry = shortfall_of.get(issue_id)
                if entry is not None:
                    unfilled = entry['units'] - entry['filled'] - entry['cancelled']
                    cancelled = min(change, unfilled)
                    top = entry['units'] - entry['cancelled']
                    cancelled_value = endpoint_share(entry['provisional'], entry['units'],
                                                     top - cancelled, top)
                    entry['cancelled'] += cancelled
                    cancelled_worth[issue_id] = cancelled_worth.get(issue_id, 0) + cancelled_value
                    if entry['filled'] + entry['cancelled'] == entry['units'] and entry in open_shortfalls:
                        open_shortfalls.remove(entry)
                pool_units = -int(issued_rows[issue_id]['quantity_microunits']) - (
                    entry['cancelled'] if entry is not None else 0)
                low = given_back.get(issue_id, 0)
                high = low + change - cancelled
                if high > pool_units:
                    raise StockRefusal('over_returned', row,
                                       issued_microunits=-int(issued_rows[issue_id]['quantity_microunits']),
                                       returned_microunits=high + change,
                                       problem='more would come back than that sale took out')
                given_back[issue_id] = high
                shared = 0
                if high > low:
                    settled = targets[issue_id] + sum(
                        delta for (issue, _), delta in true_ups.items() if issue == issue_id)
                    shared = endpoint_share(-settled - cancelled_worth.get(issue_id, 0),
                                            pool_units, low, high)
                worth = cancelled_value + shared
                targets[row['id']] = worth
            quantity += change
            value += worth
            # Cancelled units settle their own sale; only the rest can fill anyone else's.
            incoming, incoming_worth = change - cancelled, worth - cancelled_value
            if open_shortfalls and incoming:
                # Incoming units fill the shortfall first, oldest sale first. Each filled
                # span is valued twice -- what the sale provisionally took, and what this
                # receipt actually paid -- and the difference is that sale's true-up.
                span = 0
                wanting = min(incoming, sum(entry['units'] - entry['filled'] - entry['cancelled']
                                            for entry in open_shortfalls))
                while wanting:
                    entry = open_shortfalls[0]
                    units = min(entry['units'] - entry['filled'] - entry['cancelled'], wanting)
                    released = endpoint_share(entry['provisional'], entry['units'],
                                              entry['filled'], entry['filled'] + units)
                    actual = endpoint_share(incoming_worth, incoming, span, span + units)
                    key = (entry['issue']['id'], row['id'])
                    true_ups[key] = true_ups.get(key, 0) + released - actual
                    value += released - actual
                    entry['filled'] += units
                    span += units
                    wanting -= units
                    if entry['filled'] + entry['cancelled'] == entry['units']:
                        open_shortfalls.pop(0)
                if quantity <= 0:
                    rate = (incoming_worth, incoming)
        elif row['kind'] == 'value':
            if quantity <= 0:
                raise StockRefusal('unvalued_stock', row, quantity_microunits=quantity,
                                   problem='an item with nothing on hand cannot carry inventory value')
            proposed = value + int(row['value_minor_units'])
            if proposed <= 0:
                raise StockRefusal('unvalued_stock', row, quantity_microunits=quantity,
                                   value_minor_units=proposed,
                                   problem='stock on hand must be worth more than nothing; '
                                           'issue the quantity to write it off in full')
            value = proposed
        else:
            issue = -change
            if issue <= quantity:
                taken = consumed_value(value, quantity, issue)
                targets[row['id']] = -taken
                quantity -= issue
                value -= taken
            else:
                on_hand = max(quantity, 0)
                taken = value if on_hand else 0
                short = issue - on_hand
                if quantity > 0:
                    basis, basis_rate = 'average', (value, quantity)
                elif rate is not None:
                    basis, basis_rate = 'average', rate
                elif row.get('fallback_unit_cost_minor_units') is not None:
                    basis, basis_rate = 'purchase_cost', (int(row['fallback_unit_cost_minor_units']), MICRO)
                else:
                    basis, basis_rate = 'none', None
                provisional = 0 if basis_rate is None else round_ratio_half_even(
                    basis_rate[0] * short, basis_rate[1])
                targets[row['id']] = -(taken + provisional)
                quantity -= issue
                value -= taken + provisional
                entry = dict(issue=row, units=short, provisional=provisional, filled=0, cancelled=0)
                open_shortfalls.append(entry)
                shortfall_of[row['id']] = entry
                shortfalls.append(Shortfall(row, quantity, short, provisional, basis))
        if quantity > 0:
            rate = (value, quantity)
        if quantity >= 0 and value < 0:
            raise StockRefusal('negative_stock', row, quantity_microunits=quantity,
                               value_minor_units=value, problem='the value of the stock would go below zero')
        if quantity == 0 and value != 0:
            raise StockRefusal('residual_value', row, value_minor_units=value,
                               problem='no quantity on hand may not leave value behind')
        if quantity < 0 and value != -sum(endpoint_share(
                entry['provisional'], entry['units'], entry['filled'],
                entry['units'] - entry['cancelled']) for entry in open_shortfalls):
            raise StockRefusal('residual_value', row, value_minor_units=value,
                               problem='stock below zero must carry exactly its unsettled provisional cost')

    corrections = []
    for row in rows:
        owns_cost = row['kind'] == 'issue' or (
            row['kind'] == 'receipt' and row.get('returns_movement_id') is not None
            and row['id'] not in stale)
        if not owns_cost:
            continue
        target = targets.get(row['id'], 0)
        delta = target - (int(row['value_minor_units']) + posted.get(row['id'], 0))
        if delta:
            corrections.append(Correction(row, delta, row['effective_date']))
    for key in sorted({*true_ups, *posted_true_ups}, key=lambda key: (
            by_id[key[1]]['effective_date'], int(by_id[key[1]]['sequence']),
            int(by_id[key[0]]['sequence']))):
        delta = true_ups.get(key, 0) - posted_true_ups.get(key, 0)
        if delta:
            receipt = by_id[key[1]]
            corrections.append(Correction(by_id[key[0]], delta, receipt['effective_date'], receipt))
    corrections.sort(key=lambda correction: (correction.effective_date, int(correction.target_movement['sequence'])))
    return Replay(quantity, value, targets, tuple(corrections), tuple(shortfalls),
                  {key: delta for key, delta in true_ups.items()})


def totals(rows: Iterable[Mapping]) -> tuple[int, int]:
    """Posted on-hand quantity and value: the running sums of every row, corrections included.

    This is what a report reads. It agrees with ``replay`` only once the corrections replay
    asked for have been written, which is the whole invariant this increment exists to hold.
    """
    quantity = value = 0
    for row in rows:
        quantity += int(row['quantity_microunits'])
        value += int(row['value_minor_units'])
    return quantity, value


def shortfall_warning(item_name: str, shortfall: Shortfall) -> str:
    """The line a person or an agent reads when a sale takes an item below zero."""
    from bookflow.core.exact import format_quantity_micro_units
    quantity = format_quantity_micro_units(shortfall.quantity_after_microunits)
    how = {'average': 'at the average cost',
           'purchase_cost': 'at the purchase cost on the item record, because it has never had stock',
           'none': 'at zero, because the item has no average cost and no purchase cost'}[shortfall.basis]
    return (f'Takes {item_name} to {quantity} on {shortfall.movement["effective_date"]}; its cost '
            f'is provisional, {how}, until a receipt brings the item back up and trues it up.')
