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

- **Negative stock**, checked on every chronological prefix of the item's whole history, not
  on the balance as it stands today. A backdated sale, a correction that raises a quantity and
  a void of a purchase are all the same check. This is the parity limitation the costing
  decision recorded: the anchor product permits negative stock with a warning and Bookflow
  refuses, because a warning-only mode needs a provisional-cost rule and a settlement pass.
- **A closed period**, naming it, for the document's own date and for every correction date
  the change implies. The whole change is refused; deltas are never moved to today.
- **A worthless movement**: Bookflow posts no zero-amount entry, so an issue whose value
  rounds to nothing is refused rather than silently skipped.

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

from bookflow.company import inventory, journals, schema as c
from bookflow.company.document_effects import allocate
from bookflow.company.inventory_costing import StockRefusal, replay
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
    kind: str                      # 'receipt' or 'issue'
    quantity_microunits: int       # positive magnitude; the sign comes from the kind
    asset_account_id: str
    offset_account_id: str
    class_id: str | None = None
    value_minor_units: int | None = None   # a receipt states it; an issue asks the average


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

    @property
    def moves_stock(self):
        return bool(self.movements) or bool(self.corrections)

    def by_key(self, key):
        return [movement for movement in self.movements if movement.key == key]

    def rows(self, *, created_at, created_by, created_via, audit_event_id):
        """The document's own movement rows, ready to insert."""
        return [dict(movement.values, created_at=created_at, created_by=created_by,
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


def _worthless(entry):
    return inventory.invalid(
        'items', f'"{entry.item_name}" is worth nothing at the current average cost on this '
                 'document, and Bookflow posts no zero-amount entry')


def plan(s, *, entries, reversing=(), date=None, currency, field='lines', sequence=None):
    """Every movement and every correction this document implies, and nothing written yet.

    ``reversing`` is what the previous revision moved, from ``own_movements``; ``entries`` is
    what the new revision moves. Both are replayed into the item's whole history together, so
    a correction that swaps one quantity for another is one replay and not two, and the
    negative-stock check sees the net effect rather than a transient the caller never asked
    for.
    """
    sequence = inventory.next_sequence(s) if sequence is None else sequence
    working: dict[str, list] = {}

    def rows(item_id):
        if item_id not in working:
            working[item_id] = list(inventory.movements(s, item_id=item_id))
        return working[item_id]

    def refuse(refusal, item_id, item_name):
        return inventory.refuse_stock(refusal, {'id': item_id, 'full_name': item_name},
                                      quantity_field=field, value_field=field)

    change = Change()
    for row in reversing:
        values = dict(
            id=new_id(), item_id=row['item_id'], kind='reversal',
            quantity_microunits=-int(row['quantity_microunits']),
            value_minor_units=-int(row['value_minor_units']),
            effective_date=row['effective_date'], sequence=sequence,
            currency=row['currency'], asset_account_id=row['asset_account_id'],
            offset_account_id=row['offset_account_id'], class_id=row['class_id'],
            corrects_movement_id=None, reverses_movement_id=row['id'],
            revision_id=row['revision_id'], document_line_id=row['document_line_id'])
        rows(row['item_id']).append(values)
        change.movements.append(Movement(values, reverses_line_id=row['posting_line_id']))
        sequence += 1

    for entry in entries:
        history = rows(entry.item_id)
        identity = new_id()
        signed = (entry.quantity_microunits if entry.kind == 'receipt'
                  else -entry.quantity_microunits)
        values = dict(
            id=identity, item_id=entry.item_id, kind=entry.kind,
            quantity_microunits=signed,
            value_minor_units=entry.value_minor_units if entry.kind == 'receipt' else -1,
            effective_date=date, sequence=sequence, currency=currency,
            asset_account_id=entry.asset_account_id, offset_account_id=entry.offset_account_id,
            class_id=entry.class_id, corrects_movement_id=None, reverses_movement_id=None)
        if entry.kind == 'issue':
            # What it is worth is the average's answer, not the caller's, so the value is read
            # off a first replay and written back before the corrections are worked out.
            try:
                values['value_minor_units'] = replay(history + [values]).targets[identity]
            except StockRefusal as refusal:
                raise refuse(refusal, entry.item_id, entry.item_name) from None
        if not values['value_minor_units']:
            raise _worthless(entry)
        history.append(values)
        change.movements.append(Movement(values, key=entry.key))
        change.costs[entry.key] = values['value_minor_units']
        sequence += 1

    owed = []
    for item_id, history in working.items():
        try:
            state = replay(history)
        except StockRefusal as refusal:
            name = next((entry.item_name for entry in entries if entry.item_id == item_id),
                        item_id)
            raise refuse(refusal, item_id, name) from None
        owed.extend((item_id, correction) for correction in state.corrections)

    by_date: dict[str, list] = {}
    for item_id, correction in owed:
        by_date.setdefault(correction.effective_date, []).append((item_id, correction))
    for affected in sorted(by_date):
        lines, movements = [], []
        for item_id, correction in by_date[affected]:
            target = correction.target_movement
            lines.extend(inventory.pair(
                target['asset_account_id'], target['offset_account_id'],
                correction.delta_minor_units, currency, target['class_id'],
                f'Weighted-average cost correction for movement {target["id"]} '
                f'dated {target["effective_date"]}'))
            movements.append(Movement(dict(
                id=new_id(), item_id=item_id, kind=CORRECTION_KIND,
                quantity_microunits=0, value_minor_units=correction.delta_minor_units,
                effective_date=affected, sequence=sequence, currency=currency,
                asset_account_id=target['asset_account_id'],
                offset_account_id=target['offset_account_id'], class_id=target['class_id'],
                corrects_movement_id=target['id'], reverses_movement_id=None),
                line_index=len(movements) * 2 + 1))
            sequence += 1
        change.corrections.append(Correction(
            affected, 'Weighted-average cost correction', lines, movements))
    return change


def open_dates(s, change, dates=()):
    """Refuse the whole change, naming the period, if any date it writes to is closed."""
    journals.open_dates(s, sorted({*dates, *(c.date for c in change.corrections)}))


def bind(movement, leg, *, transaction_id, revision_id, document_line_id):
    """Give one planned movement the posting and entered-line identities just minted."""
    movement.values.update(transaction_id=transaction_id, revision_id=revision_id,
                           posting_batch_id=leg['batch_id'], posting_line_id=leg['id'],
                           document_line_id=document_line_id)


def bind_reversals(change, posting_lines):
    """Bind every reversal movement to the leg that reverses the one it names."""
    by_original = {leg['reversed_line_id']: leg for leg in posting_lines
                   if leg['reversed_line_id'] is not None}
    for movement in change.movements:
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
        [movement.values['posting_line_id'] for movement in change.movements],
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
        rows = [dict(movement.values, **provenance) for movement in correction.movements]
        applied = journals.persist_prepared(
            fresh, ctx, s, command_name=command_name,
            extra=(('inventory_documents', 'inventory_document', 'transaction_id', [marker]),
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
    touched = [Touched('inventory_movement', row['id'], 'create', None, 1, row, db='company')
               for row in rows]
    audit.write_event_to(s.company, ctx, command_name, summary, touched, actor_id=s.actor.id,
                         actor_kind=s.actor.kind,
                         directive_code=getattr(s, 'directive_code', None), event_id=event)
    s.company.conn.execute(c.inventory_movements.insert(), rows)
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
    if not touched:
        return applied
    return Applied(applied.output, list(applied.touched) + touched, applied.summary,
                   finalized=applied.finalized, audited=True, after_commit=applied.after_commit)
