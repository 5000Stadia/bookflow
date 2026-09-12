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

- **Negative stock.** The replay walks every chronological prefix, so a backdated issue or a
  void that would take an earlier prefix below zero is refused by date, not just the state
  as it stands today.
- **A closed period.** Every date this change would write to -- the document's own and every
  correction's -- is checked against the closing date first, and the whole change is refused
  naming the period. Deltas are never moved to today.
- **A worthless movement.** Every movement must carry a real value, because every movement is
  one side of a real posting and Bookflow posts no zero-amount entries.

## Parity limit, recorded deliberately

The anchor product permits stock to go negative and warns. Bookflow refuses, because a
warning-only mode needs a provisional-cost rule and a later settlement pass, and an undefined
or silently-zero cost is not a finished implementation. This is the first increment; that
mode can be added on purpose later.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlalchemy as sa

from bookflow.company import accounts, items as item_service, journals, list_service, schema as c
from bookflow.company.document_effects import allocate
from bookflow.company.inventory_costing import StockRefusal, replay
from bookflow.company.inventory_models import (
    InventoryAdjustmentSummary, InventoryCorrectionOutput, InventoryOutput, InventoryWriteOutput,
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
    return [dict(row) for row in s.company.conn.execute(query).mappings()]


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
    return s.company.conn.execute(sa.select(c.inventory_documents.c.kind).where(
        c.inventory_documents.c.transaction_id == transaction_id)).scalar_one_or_none()


# ---------------------------------------------------------------- building one change


@dataclass
class _Movement:
    """A movement waiting for the posting-line identities its document has not minted yet."""

    values: dict
    line_index: int                       # 1-based entered-line position of its asset line
    reverses_line_id: str | None = None   # on a void, the original asset posting line


@dataclass
class _Document:
    journal: object
    operation: str
    kind: str
    movements: list[_Movement] = field(default_factory=list)
    journal_date: str | None = None   # a void carries the date of what it reverses


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


def _home(s):
    return s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar_one()


def next_sequence(s):
    return int(s.company.conn.execute(
        sa.select(sa.func.coalesce(sa.func.max(c.inventory_movements.c.sequence), 0))).scalar_one()) + 1


def _tracked_item(s, selector):
    item = item_service.resolve_item(s.company, selector)
    if item['type'] not in TRACKED_TYPES:
        readable = ' and '.join(name.replace('_', ' ') for name in TRACKED_TYPES)
        raise invalid('item', f'a {item["type"].replace("_", " ")} item carries no stock; only '
                              f'{readable} items have a quantity and a value to adjust')
    if not item['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': 'item', 'record_id': item['id']})
    return item


def _asset_account(s, item):
    if item['asset_account_id'] is None:
        raise invalid('item', 'this item has no inventory-asset account')
    row = accounts.resolve_account(s.company, item['asset_account_id'])
    if row.get('system_role') != ASSET_ROLE:
        raise invalid('item', "this item's asset account is not the inventory-asset account")
    return journals.active(row, 'account')


def _offset_account(s, selector, asset):
    row = journals.active(accounts.resolve_account(s.company, selector), 'account')
    if row['type'] == 'non_posting':
        raise invalid('adjustment_account', 'must be a posting account')
    if row['id'] == asset['id']:
        raise invalid('adjustment_account', 'the other side of an inventory adjustment cannot be '
                                            'the inventory-asset account itself')
    if row.get('system_role') in journals.OWNED_SYSTEM_ROLES:
        raise invalid('adjustment_account', 'that account is written by the ledger that owns it, '
                                            'not by an inventory adjustment')
    return row


def _class_row(s, class_id):
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
            f'This change would take "{item["full_name"]}" below zero on {when}: {problem}. '
            'Bookflow refuses negative stock; receive the quantity first, or take out less.'),
            details={'fields': [{'field': quantity_field, 'problem': problem}], **details})
    return BookflowError('E_VALIDATION', message=(
        f'This change cannot stand for "{item["full_name"]}" on {when}: {problem}.'),
        details={'fields': [{'field': value_field, 'problem': problem}], **details})


def _adjustment_change(s, ctx, inp, currency, sequence):
    """The adjustment document and its one movement, with the value the average decides."""
    item = _tracked_item(s, inp.item)
    asset = _asset_account(s, item)
    offset = _offset_account(s, inp.adjustment_account, asset)
    klass = _class_row(s, inp.class_id)
    quantity = 0 if inp.quantity_change is None else parse_quantity_micro_units(
        inp.quantity_change, field='quantity_change')
    stated = None
    if inp.value_change is not None:
        stated = parse_domestic_amount(inp.value_change, currency, 'value_change').minor_units
        if inp.negative_value:
            stated = -stated
    history = movements(s, item_id=item['id'])
    identity = new_id()
    kind = 'receipt' if quantity > 0 else 'issue' if quantity < 0 else 'value'
    proposed = dict(id=identity, kind=kind, quantity_microunits=quantity,
                    value_minor_units=stated if stated is not None else -1,
                    effective_date=inp.date, sequence=sequence,
                    corrects_movement_id=None, reverses_movement_id=None)
    if kind == 'issue':
        # What it is worth is the average's answer, not the caller's, so the value is read off
        # a first replay and written back before the corrections are worked out.
        try:
            proposed['value_minor_units'] = replay(history + [proposed]).targets[identity]
        except StockRefusal as refusal:
            raise refuse_stock(refusal, item) from None
    if proposed['value_minor_units'] == 0:
        raise invalid('quantity_change' if kind == 'issue' else 'value_change',
                      'this adjustment is worth nothing at the current average cost, and '
                      'Bookflow posts no zero-amount entry')
    try:
        state = replay(history + [proposed])
    except StockRefusal as refusal:
        raise refuse_stock(refusal, item) from None
    values = dict(proposed, item_id=item['id'], currency=currency,
                  asset_account_id=asset['id'], offset_account_id=offset['id'],
                  class_id=klass['id'] if klass else None)
    description = inp.memo or f'Inventory adjustment for {item["full_name"]}'
    journal = JournalPostInput(
        date=inp.date, memo=inp.memo, custom_fields=inp.custom_fields,
        custom_field_kinds=inp.custom_field_kinds,
        lines=pair(asset['id'], offset['id'], values['value_minor_units'], currency,
                    values['class_id'], description),
        **({'number': inp.number} if inp.number is not None else {}))
    document = _Document(journal, 'post', 'adjustment', [_Movement(values, 1)])
    return _Change([document], item, state, currency, quantity, values['value_minor_units'],
                   kind, asset['id'], offset['id'])


def _void_change(s, inp, currency, sequence):
    """The reversal of one adjustment: its movements inverted exactly, as the batch is."""
    header = resolve(s, inp.adjustment)
    own = [row for row in movements(s, transaction_id=header['id']) if row['kind'] in INPUT_KINDS]
    if not own:
        raise BookflowError('E_INTERNAL', message='An inventory adjustment has no movement to reverse.')
    item = item_service.resolve_item(s.company, own[0]['item_id'])
    retired = {row['reverses_movement_id'] for row in movements(s, item_id=item['id'])
               if row['kind'] == 'reversal'}
    if header['status'] == 'voided' or all(row['id'] in retired for row in own):
        # Already reversed; the journal writer answers "no change" and nothing else runs.
        return _Change([], item, replay(movements(s, item_id=item['id'])), currency)
    revision = journals.revision(s, header)
    reversals = []
    for row in own:
        reversals.append(_Movement(dict(
            id=new_id(), kind='reversal', item_id=row['item_id'],
            quantity_microunits=-int(row['quantity_microunits']),
            value_minor_units=-int(row['value_minor_units']),
            effective_date=row['effective_date'], sequence=sequence,
            currency=row['currency'], asset_account_id=row['asset_account_id'],
            offset_account_id=row['offset_account_id'], class_id=row['class_id'],
            corrects_movement_id=None, reverses_movement_id=row['id'],
            revision_id=revision['id'], document_line_id=row['document_line_id']),
            line_index=0, reverses_line_id=row['posting_line_id']))
        sequence += 1
    try:
        state = replay(movements(s, item_id=item['id']) + [m.values for m in reversals])
    except StockRefusal as refusal:
        raise refuse_stock(refusal, item) from None
    journal = JournalVoidInput(journal=header['id'], expected_version=inp.expected_version)
    first = reversals[0].values
    change = _Change([_Document(journal, 'void', 'adjustment', reversals)], item, state, currency,
                     first['quantity_microunits'], first['value_minor_units'], 'reversal',
                     first['asset_account_id'], first['offset_account_id'])
    change.documents[0].journal_date = revision['date']
    return change


def _correction_documents(s, change, sequence):
    """One dated document per affected date, one line pair per corrected movement."""
    by_date: dict[str, list] = {}
    for correction in change.state.corrections:
        by_date.setdefault(correction.effective_date, []).append(correction)
    for date in sorted(by_date):
        lines, pending = [], []
        for correction in by_date[date]:
            target = correction.target_movement
            lines.extend(pair(
                target['asset_account_id'], target['offset_account_id'],
                correction.delta_minor_units, change.currency, target['class_id'],
                f'Weighted-average cost correction for movement {target["id"]} '
                f'dated {target["effective_date"]}'))
            pending.append(_Movement(dict(
                id=new_id(), kind='recost', item_id=change.item['id'],
                quantity_microunits=0, value_minor_units=correction.delta_minor_units,
                effective_date=date, sequence=sequence, currency=change.currency,
                asset_account_id=target['asset_account_id'],
                offset_account_id=target['offset_account_id'], class_id=target['class_id'],
                corrects_movement_id=target['id'], reverses_movement_id=None),
                line_index=len(pending) * 2 + 1))
            sequence += 1
        change.documents.append(_Document(JournalPostInput(
            date=date, memo=f'Weighted-average cost correction for {change.item["full_name"]}',
            lines=lines), 'post', 'recost', pending))
    return sequence


def _build(s, ctx, inp, operation):
    """Every document this change writes, decided in full before any of it is built."""
    currency = _home(s)
    sequence = next_sequence(s)
    change = (_adjustment_change(s, ctx, inp, currency, sequence) if operation == 'post'
              else _void_change(s, inp, currency, sequence))
    if not change.documents:
        return change
    sequence = max(int(movement.values['sequence']) for document in change.documents
                   for movement in document.movements) + 1
    _correction_documents(s, change, sequence)
    dates = {document.journal_date or document.journal.date
             for document in change.documents}
    journals.open_dates(s, sorted(dates))
    return change


# ---------------------------------------------------------------- persistence


def _attach(document, fresh):
    """Give each pending movement the posting and document line identities just minted."""
    pending = fresh.data['pending']
    if document.operation == 'void':
        by_original = {line['reversed_line_id']: line for line in pending['posting_lines']}
        for movement in document.movements:
            leg = by_original.get(movement.reverses_line_id)
            if leg is None:
                raise BookflowError('E_INTERNAL', message='A void did not reverse its inventory posting line.')
            movement.values.update(transaction_id=leg['transaction_id'],
                                   posting_batch_id=leg['batch_id'], posting_line_id=leg['id'])
        return
    revision = pending['transaction_revisions'][0]
    entered = {line['position']: line for line in pending['document_lines']}
    legs = {line['line_no']: line for line in pending['posting_lines']}
    for movement in document.movements:
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
    claimed = list(claimed)
    control = {line['id'] for line in posting_lines
               if line['account_id'] in control_account_ids}
    if len(claimed) != len(set(claimed)) or control != set(claimed):
        raise BookflowError('E_INTERNAL', message=(
            'An inventory-asset posting is not attributed to exactly one item; refusing to '
            'write a balance the stock reports could not explain.'))


def asset_account_ids(s):
    return set(s.company.conn.execute(sa.select(c.accounts.c.id).where(
        c.accounts.c.system_role == ASSET_ROLE)).scalars())


def _summary(s, change, corrections):
    state = change.state
    return InventoryAdjustmentSummary(
        kind='adjustment', item_id=change.item['id'], item_name=change.item['full_name'],
        item_type=change.item['type'], currency=change.currency,
        quantity_change=format_quantity_micro_units(change.quantity_microunits),
        value_change=Money(change.value_minor_units, change.currency).to_dict(),
        quantity_on_hand=format_quantity_micro_units(state.quantity_microunits),
        average_cost=Money(state.average_cost_minor_units, change.currency).to_dict(),
        inventory_value=Money(state.value_minor_units, change.currency).to_dict(),
        asset_account_id=change.asset_account_id, adjustment_account_id=change.offset_account_id,
        movement_kind=change.movement_kind, corrections=corrections)


def _corrections_output(change):
    return [InventoryCorrectionOutput(
        item_id=change.item['id'], item_name=change.item['full_name'],
        effective_date=movement.values['effective_date'],
        corrects_movement_id=movement.values['corrects_movement_id'],
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


def _run(s, ctx, inp, operation, *, persist):
    change = _build(s, ctx, inp, operation)
    command_name = 'inventory ' + ('adjust' if operation == 'post' else 'void')
    if not change.documents:
        header = resolve(s, inp.adjustment)
        fresh = journals.prepare(s, ctx, JournalVoidInput(
            journal=header['id'], expected_version=inp.expected_version), 'void', owner=OWNER)
        summary = document_summary(s, header, change.currency)
        return InventoryWriteOutput(**fresh.preview.model_dump(), adjustment=summary), [], False

    at = clock.now_iso()
    asset_accounts = asset_account_ids(s)
    reserved, touched, primary = [], [], None
    for document in change.documents:
        if document.kind == 'recost':
            # The journal writer allocates the adjustment's own number, and every correction
            # takes the next free one after the numbers this call has already handed out.
            number, _ = allocate(s, 'journal_entry', None, reserved=reserved)
            document.journal = document.journal.model_copy(update={'number': number})
        try:
            fresh = journals.prepare(s, ctx, document.journal, document.operation, owner=OWNER)
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
        marker = dict(transaction_id=document.movements[0].values['transaction_id'],
                      type='journal_entry', kind=document.kind, created_at=at,
                      created_by=s.actor.id, created_via=ctx.interface.value,
                      audit_event_id=fresh.data['event'])
        rows = [dict({key: value for key, value in movement.values.items() if key != 'number'},
                     created_at=at, created_by=s.actor.id, created_via=ctx.interface.value,
                     audit_event_id=fresh.data['event']) for movement in document.movements]
        if persist:
            extra = [('inventory_documents', 'inventory_document', 'transaction_id', [marker])] \
                if document.operation == 'post' else []
            extra.append(('inventory_movements', 'inventory_movement', 'id', rows))
            applied = journals.persist_prepared(
                fresh, ctx, s, command_name=command_name, extra=tuple(extra),
                noun='inventory adjustment' if document.kind == 'adjustment'
                else 'inventory cost correction')
            touched.extend(applied.touched)
            primary = primary or applied.output
        else:
            primary = primary or fresh.preview
    output = InventoryWriteOutput(**primary.model_dump(), adjustment=_summary(s, change, _corrections_output(change)))
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
    return Applied(output, touched, f'{verb} inventory for {output.adjustment.item_name}', audited=True)


# ---------------------------------------------------------------- reads


def document_summary(s, header, currency=None):
    """The adjustment's own footer, derived from the movements it wrote."""
    currency = currency or _home(s)
    own = [row for row in movements(s, transaction_id=header['id']) if row['kind'] in INPUT_KINDS]
    if not own:
        raise BookflowError('E_RECORD_NOT_FOUND',
                            details={'record_type': 'inventory_adjustment', 'selector': header['id']})
    movement = own[0]
    item = item_service.resolve_item(s.company, movement['item_id'])
    change = _Change([], item, replay(movements(s, item_id=item['id'])), currency,
                     int(movement['quantity_microunits']), int(movement['value_minor_units']),
                     movement['kind'], movement['asset_account_id'], movement['offset_account_id'])
    recosts = [row for row in sorted(movements(s, item_id=item['id']),
                                     key=lambda row: (row['effective_date'], int(row['sequence'])))
               if row['kind'] == 'recost']
    numbers = {} if not recosts else {row['id']: row['number'] for row in s.company.conn.execute(
        sa.select(c.transactions.c.id, c.transactions.c.number).where(
            c.transactions.c.id.in_([row['transaction_id'] for row in recosts]))).mappings()}
    corrections = [InventoryCorrectionOutput(
        item_id=item['id'], item_name=item['full_name'], effective_date=row['effective_date'],
        corrects_movement_id=row['corrects_movement_id'],
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
