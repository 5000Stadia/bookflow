"""The costing method: average or FIFO, by dated switches (R104 increment 7, design/advanced-inventory.md §3).

``costing_methods`` holds the company's timeline, append-only; ``timeline(s)`` reads it for replay, and every
caller of ``inventory_costing.replay`` that has a session passes it, so the method decides every value replay
decides. ``costing-method set`` is a person's act (the people-only list): it records a switch dated after the
closing date and after the last switch, and posts, each at its own date, the corrections it owes to movements
already dated on or after it. The switch itself posts nothing and revalues nothing. ``costing-method withdraw``
takes back a switch whose date has not come, with the corrections that takes. ``report inventory-layers`` lists
each item's layers on a date.
"""
from __future__ import annotations

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company.costing_method_models import (
    CorrectionEntry, CostingMethodOutput, CostingMethodWriteOutput, InventoryLayersOutput, ItemEffect, LayerRow, SwitchRow,
)
from bookflow.company.inventory_costing import MICRO, StockRefusal, method_on, replay
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units, round_ratio_half_even
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched

ADVICE = ('A costing method is meant to be kept; change it only on your accountant\'s advice. The switch revalues '
          'nothing: from its date on, cost of goods sold follows the new method.')


def _invalid(field, problem, message=None, **details):
    return BookflowError('E_VALIDATION', message=message or f'{field}: {problem}.',
                         details={'fields': [{'field': field, 'problem': problem}], **details})


def _home(s):
    return s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar_one()


def _text(minor, currency):
    return str(Money(int(minor), currency))


def _today(s):
    from bookflow.company.memorized_schedule import today
    return today(s.company_tz)


# ---------------------------------------------------------------- the timeline


def rows(s):
    t = c.costing_methods
    return [dict(row) for row in s.company.conn.execute(
        sa.select(t).order_by(t.c.effective_from, t.c.created_at, t.c.id)).mappings()]


def timeline(s) -> tuple[tuple[str, str], ...]:
    """The standing switches, oldest first, as replay takes them. A switch being previewed stands in for the
    stored timeline while its change is worked out (``_working``)."""
    override = getattr(s, '_costing_timeline', None)
    if override is not None:
        return override
    t = c.costing_methods
    return tuple((row.effective_from, row.method) for row in s.company.conn.execute(
        sa.select(t.c.effective_from, t.c.method).where(t.c.status == 'standing').order_by(t.c.effective_from)))


def method_today(s) -> str:
    return method_on(timeline(s), _today(s))


def _switch_row(row) -> SwitchRow:
    return SwitchRow(id=row['id'], method=row['method'], effective_from=row['effective_from'], status=row['status'],
                     reason=row['reason'], created_at=row['created_at'], created_by=row['created_by'],
                     created_via=row['created_via'], withdrawn_at=row['withdrawn_at'],
                     withdraw_reason=row['withdraw_reason'])


def show(s, ctx, inp) -> Plan:
    stored = rows(s)
    today = method_today(s)
    scheduled = [row for row in stored if row['status'] == 'standing' and row['effective_from'] > _today(s)]
    message = f'{"FIFO" if today == "fifo" else "Average cost"} today.'
    if scheduled:
        message += ' Scheduled: ' + '; '.join(
            f'{"FIFO" if row["method"] == "fifo" else "average"} from {row["effective_from"]}' for row in scheduled) + '.'
    return Plan(CostingMethodOutput(method_today=today, switches=[_switch_row(row) for row in stored],
                                    message=message))


# ---------------------------------------------------------------- what a switch changes


def _items_from(s, date):
    """Every item with a movement dated on or after ``date``: only those can owe a correction."""
    t = c.inventory_movements
    return sorted(row[0] for row in s.company.conn.execute(
        sa.select(t.c.item_id).where(t.c.effective_date >= date).distinct()))


def _change(s, proposed, since):
    """The correction documents the timeline ``proposed`` owes, worked out with it standing in for the stored one."""
    from bookflow.company import inventory
    currency = _home(s)
    change = inventory._Change([], {'id': '', 'full_name': 'stock'}, None, currency)
    s._costing_timeline = tuple(proposed)
    try:
        inventory.correction_documents(s, change, inventory.next_sequence(s), also=_items_from(s, since))
        if change.documents:
            from bookflow.company import journals
            journals.open_dates(s, sorted({document.journal.date for document in change.documents}))
    except StockRefusal as refusal:   # pragma: no cover - replay_items turns these into errors
        raise BookflowError('E_VALIDATION', message=str(refusal)) from None
    finally:
        s._costing_timeline = None
    return change


def _effects(s, change):
    from bookflow.company import inventory
    currency = change.currency
    assets = set(inventory.asset_account_ids(s))
    stock, cogs, names = {}, {}, {}
    for document in change.documents:
        for movement in document.movements:
            values = movement.values
            item_id = values['item_id']
            stock[item_id] = stock.get(item_id, 0) + int(values['value_minor_units'])
            if values['kind'] == 'recost' and values['offset_account_id'] not in assets:
                cogs[item_id] = cogs.get(item_id, 0) - int(values['value_minor_units'])
    for item_id in stock:
        names[item_id] = inventory.item_name(s, item_id)
    items = [ItemEffect(item_id=item_id, item=names[item_id], stock_value_change=_text(stock[item_id], currency),
                        stock_value_change_minor_units=stock[item_id],
                        cost_of_goods_sold_change=_text(cogs.get(item_id, 0), currency),
                        cost_of_goods_sold_change_minor_units=cogs.get(item_id, 0))
             for item_id in sorted(stock, key=lambda key: (names[key].casefold(), key))
             if stock[item_id] or cogs.get(item_id, 0)]
    entries = []
    for document in change.documents:
        amount = sum(int(m.values['value_minor_units']) for m in document.movements)
        entries.append(CorrectionEntry(date=document.journal.date, memo=document.journal.memo or '',
                                       items=len({m.values['item_id'] for m in document.movements}),
                                       amount=_text(amount, currency), amount_minor_units=amount))
    total_stock = sum(stock.values())
    total_cogs = sum(cogs.values())
    return items, entries, total_stock, total_cogs


def _output(s, change, *, switch, changed, message, proposed):
    items, entries, total_stock, total_cogs = _effects(s, change)
    currency = change.currency
    return CostingMethodWriteOutput(
        changed=changed, switch=switch, method_today=method_on(proposed, _today(s)), items=items,
        stock_value_change=_text(total_stock, currency), stock_value_change_minor_units=total_stock,
        cost_of_goods_sold_change=_text(total_cogs, currency), cost_of_goods_sold_change_minor_units=total_cogs,
        entries=entries, message=message, warnings=[ADVICE])


# ---------------------------------------------------------------- set


def _check_set(s, inp):
    date = inp.effective_from
    closing = s.company.conn.execute(sa.select(c.company_info.c.closing_date)).scalar_one()
    if closing and date <= closing:
        raise BookflowError('E_PERIOD_CLOSED', message=(
            f'The books are closed through {closing}; a costing method can change only after that, so history '
            'before it replays unchanged.'), details={'date': date, 'closing_date': closing,
                                                     'fields': [{'field': 'effective_from', 'problem': 'on or before the closing date'}]})
    current = timeline(s)
    if current and date <= current[-1][0]:
        raise _invalid('effective_from', 'must be after the last switch', (
            f'The last switch is dated {current[-1][0]}; a new one must be dated after it. Withdraw a switch whose '
            'date has not come with `costing-method withdraw`.'), last_switch=current[-1][0])
    if method_on(current, date) == inp.method:
        raise _invalid('method', 'already in force on that date', (
            f'{"FIFO" if inp.method == "fifo" else "Average cost"} is already the method on {date}.'))
    return current + ((date, inp.method),)


def _summary_message(inp, change):
    name = 'FIFO' if inp.method == 'fifo' else 'average cost'
    count = len(change.documents)
    corrections = (f' It posts {count} correction {"entry" if count == 1 else "entries"} to movements already dated '
                   'on or after it.' if count else ' Nothing already posted is dated on or after it, so it posts nothing.')
    return f'Costs stock by {name} from {inp.effective_from}.{corrections}'


def _person(s, command):
    """A person's act (fifo-v1's people-only list), refused here too for a caller that arrives without an actor."""
    if s.actor is None or s.actor.kind != 'human':
        from bookflow.hub import people_only
        raise people_only.refusal('', command=command, capability='company')


def plan_set(s, ctx, inp) -> Plan:
    _person(s, 'costing-method set')
    proposed = _check_set(s, inp)
    change = _change(s, proposed, inp.effective_from)
    preview = _output(s, change, switch=None, changed=True, message=_summary_message(inp, change), proposed=proposed)
    return Plan(preview, {'input': inp})


def apply_set(plan, ctx, s) -> Applied:
    from bookflow.company import inventory
    inp = plan.data['input']
    proposed = _check_set(s, inp)
    at = clock.now_iso()
    row = dict(id=new_id(), method=inp.method, effective_from=inp.effective_from, status='standing',
               reason=(ctx.reason or '').strip() or None, created_at=at, created_by=s.actor.id,
               created_via=ctx.interface.value, withdrawn_at=None, withdrawn_by=None, withdrawn_via=None,
               withdraw_reason=None, withdraw_audit_event_id=None)
    summary = f'costing method {inp.method} from {inp.effective_from}'
    record = [Touched('costing_method', row['id'], 'create', None, 1, dict(row), None, db='company')]
    event = audit.write_event_to(s.company, ctx, 'costing-method set', summary, record, actor_id=s.actor.id,
                                 actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None))
    # Worked out before the row is written, with the proposed timeline standing in, as the preview was.
    change = _change(s, proposed, inp.effective_from)
    s.company.conn.execute(c.costing_methods.insert().values(**row, audit_event_id=event))
    touched = list(record)
    if change.documents:
        _, more = inventory.persist_change(s, ctx, change, command_name='costing-method set', persist=True)
        touched += more
    stored = dict(row, audit_event_id=event)
    output = _output(s, change, switch=_switch_row(stored), changed=True, message=_summary_message(inp, change),
                     proposed=proposed)
    return Applied(output, touched, summary, audited=True)


# ---------------------------------------------------------------- withdraw


def _resolve(s, selector):
    t = c.costing_methods
    key = selector.upper() if is_ulid(selector) else selector
    found = [dict(row) for row in s.company.conn.execute(sa.select(t).where(sa.or_(
        t.c.id == key, sa.and_(t.c.effective_from == selector, t.c.status == 'standing')))).mappings()]
    if not found:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'costing_method', 'selector': selector})
    return found[0]


def _check_withdraw(s, inp):
    row = _resolve(s, inp.switch)
    if row['status'] != 'standing':
        return row, None
    if row['effective_from'] <= _today(s):
        raise _invalid('switch', 'its date has come', (
            f'The switch to {row["method"]} from {row["effective_from"]} is already in force; set the other method '
            'from a later date instead (`costing-method set`).'))
    proposed = tuple(pair for pair in timeline(s) if pair != (row['effective_from'], row['method']))
    return row, proposed


def plan_withdraw(s, ctx, inp) -> Plan:
    _person(s, 'costing-method withdraw')
    row, proposed = _check_withdraw(s, inp)
    if proposed is None:
        from bookflow.company import inventory
        empty = inventory._Change([], {'id': '', 'full_name': 'stock'}, None, _home(s))
        return Plan(_output(s, empty, switch=_switch_row(row), changed=False, proposed=timeline(s),
                            message='That switch is already withdrawn.'), {'input': inp})
    change = _change(s, proposed, row['effective_from'])
    message = f'Withdraws the switch to {row["method"]} from {row["effective_from"]}.'
    return Plan(_output(s, change, switch=_switch_row(row), changed=True, message=message, proposed=proposed),
                {'input': inp})


def apply_withdraw(plan, ctx, s) -> Applied:
    from bookflow.company import inventory
    inp = plan.data['input']
    row, proposed = _check_withdraw(s, inp)
    if proposed is None:
        return Applied(plan.preview, [], 'no change')
    at = clock.now_iso()
    change = _change(s, proposed, row['effective_from'])
    after = dict(row, status='withdrawn', withdrawn_at=at, withdrawn_by=s.actor.id, withdrawn_via=ctx.interface.value,
                 withdraw_reason=(ctx.reason or '').strip() or None)
    summary = f'withdraw costing method {row["method"]} from {row["effective_from"]}'
    record = [Touched('costing_method', row['id'], 'update', 1, 2, after, row, db='company')]
    event = audit.write_event_to(s.company, ctx, 'costing-method withdraw', summary, record, actor_id=s.actor.id,
                                 actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None))
    s.company.conn.execute(c.costing_methods.update().where(c.costing_methods.c.id == row['id']).values(
        status='withdrawn', withdrawn_at=at, withdrawn_by=s.actor.id, withdrawn_via=ctx.interface.value,
        withdraw_reason=after['withdraw_reason'], withdraw_audit_event_id=event))
    touched = list(record)
    if change.documents:
        _, more = inventory.persist_change(s, ctx, change, command_name='costing-method withdraw', persist=True)
        touched += more
    output = _output(s, change, switch=_switch_row(after), changed=True, proposed=proposed,
                     message=f'Withdrew the switch to {row["method"]} from {row["effective_from"]}.')
    return Applied(output, touched, summary, audited=True)


# ---------------------------------------------------------------- report inventory-layers


def _sources(s, movement_ids):
    """Movement id -> a label for the document that brought its units in."""
    if not movement_ids:
        return {}
    m, t = c.inventory_movements, c.transactions
    out = {}
    for row in s.company.conn.execute(sa.select(m.c.id, m.c.kind, m.c.returns_movement_id, t.c.type, t.c.number)
                                      .select_from(m.join(t, t.c.id == m.c.transaction_id))
                                      .where(m.c.id.in_(sorted(movement_ids)))):
        kind = (row.type or 'document').replace('_', ' ')
        what = 'return on ' if row.kind == 'receipt' and row.returns_movement_id else (
            'found on ' if row.kind == 'found' else '')
        out[row.id] = f'{what}{kind} {row.number}'.strip() if row.number else f'{what}{kind}'
    return out


def inventory_layers(s, ctx, inp) -> Plan:
    from bookflow.company import inventory, items as item_service
    as_of = inp.as_of or _today(s)
    currency = _home(s)
    methods = timeline(s)
    if inp.item:
        item_ids = [item_service.resolve_item(s.company, inp.item)['id']]
    else:
        it = c.items
        item_ids = [row.id for row in s.company.conn.execute(
            sa.select(it.c.id).where(it.c.type.in_(item_service.TRACKED_TYPES)).order_by(it.c.full_name_key, it.c.id))]
    page = item_ids[inp.offset:inp.offset + inp.limit]
    found_rows, total = [], 0
    for item_id in page:
        history = inventory.movements(s, item_id=item_id, as_of=as_of)
        if not history:
            continue
        state = replay(history, methods, until=as_of)
        name = inventory.item_name(s, item_id)
        labels = _sources(s, {layer.source for layer in state.layers if layer.source})
        for layer in state.layers:
            if layer.source is None:
                label = 'Average' if state.method == 'average' else f'Opening (FIFO from {layer.effective_date})'
            else:
                label = labels.get(layer.source, layer.source)
            unit = round_ratio_half_even(layer.value_minor_units * MICRO * 100, layer.quantity_microunits)
            found_rows.append(LayerRow(
                item_id=item_id, item=name, source=label, source_movement_id=layer.source, date=layer.effective_date,
                quantity=format_quantity_micro_units(layer.quantity_microunits),
                value=_text(layer.value_minor_units, currency), value_minor_units=layer.value_minor_units,
                unit_cost=_unit(unit, currency)))
            total += layer.value_minor_units
    more = inp.offset + inp.limit < len(item_ids)
    return Plan(InventoryLayersOutput(as_of=as_of, method=method_on(methods, as_of), currency=currency, rows=found_rows,
                                      total_value=_text(total, currency), total_value_minor_units=total,
                                      next_offset=inp.offset + inp.limit if more else None))


def _unit(hundredths, currency):
    """A unit cost in hundredths of a minor unit, to four places (a two-decimal home currency assumed)."""
    sign = '-' if hundredths < 0 else ''
    whole, part = divmod(abs(hundredths), 10000)
    return f'{sign}{whole:,}.{part:04d} {currency}'
