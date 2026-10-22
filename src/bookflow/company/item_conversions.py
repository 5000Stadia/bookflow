"""Converting a non-inventory part or other charge into an inventory part (R154).

The anchor lets a Non-inventory Part or an Other Charge be changed to an Inventory Part in Edit
Item, and the change cannot be undone. This is that change, as its own audited act:

- **The item keeps its id**, so every document that names it still names it, and its income
  account, descriptions, price, cost, vendors and custom fields come along. It gains the
  Inventory Asset account and a cost-of-goods-sold account; an inventory part has no expense
  account, so the old one stays only on the documents that already posted to it.
- **History is not rewritten.** Nothing posted changes: earlier purchases stay expensed and
  earlier sales stay as they posted, without cost of goods. Stock on hand starts at zero with no
  cost. From the conversion date on, a purchase puts it into stock and a sale relieves stock at
  average cost, exactly as for any inventory part.
- **No stock moves before the conversion date.** The anchor's guidance is to date the opening
  stock after the last transaction that used the item in its non-inventory guise; here the
  conversion date itself must be on or after that last document, and a stock movement dated
  before it -- a back-dated bill, or an old bill re-entered with its items grid -- is refused, so
  an earlier period can never be turned into a stocked one after the fact.
- **One-way.** An item converts once; converting back is refused, as in the anchor.

Refused, before anything is written: an item already carrying stock, a type the anchor cannot
convert, a percentage charge (it has no quantity), an item inside a branch of the item list
(items in one branch share a type), and an item that an open purchase order, estimate, proposal
or work order still names -- those lines were captured as a non-stock purchase or a quote that
cannot hold stock, and receiving or invoicing them after the conversion would change what they
mean. Close or finish them first.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from bookflow.company import items, schema
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError, require_reason
from bookflow.core.ids import new_id
from bookflow.core.registry import Touched

CONVERTIBLE = ('non_inventory_part', 'other_charge')
TARGET = 'inventory_part'
READABLE = {'non_inventory_part': 'non-inventory part', 'other_charge': 'other charge',
            'inventory_part': 'inventory part'}
STOCK_NOTE = ('Stock on hand starts at 0 with no cost. If you have some on the shelf now, count it with '
              '`inventory adjust` dated on or after the conversion date.')

# Every dated document line that names an item, as (item, date, document type, number, id).
_USES = """
 SELECT x.item_id AS item_id, r.date AS date, t.type AS type, t.number AS number, t.id AS id
   FROM {table} x JOIN transactions t ON t.id = x.transaction_id AND t.current_revision_id = x.revision_id
   JOIN transaction_revisions r ON r.id = x.revision_id
"""
_LINE_TABLES = ('purchase_item_lines', 'vendor_credit_item_lines', 'sales_line_profiles',
                'credit_line_profiles', 'money_out_item_lines')
_OPEN_WORK = {'proposal': ('draft', 'open', 'accepted'), 'estimate': ('draft', 'open', 'accepted'),
              'work_order': ('draft', 'scheduled', 'in_progress', 'on_hold'), 'sales_order': ('open',)}
_OPEN_ORDERS = ('open', 'partly_received')


def _refuse(code: str, problem: str, **details: Any) -> BookflowError:
    return BookflowError(code, message=problem, details={'problem': problem, **details})


def _invalid(field: str, problem: str, **details: Any) -> BookflowError:
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}], **details})


def conversion(db, item_id: str) -> dict[str, Any] | None:
    row = db.conn.execute(sa.select(schema.item_conversions).where(
        schema.item_conversions.c.item_id == item_id)).mappings().first()
    return None if row is None else dict(row)


def require_stock_date(s, item_id: str, item_name: str, date: str | None, field: str) -> None:
    """Refuse a stock movement dated before the item became an inventory part.

    Every stock movement a document or an adjustment writes is planned through here, so an
    earlier period cannot be turned into a stocked one after the conversion: the purchases and
    sales entered before it stay as they posted.
    """
    if date is None:
        return
    found = conversion(s.company, item_id)
    if found is None or date >= found['effective_date']:
        return
    since = found['effective_date']
    raise BookflowError('E_VALIDATION', message=(
        f'"{item_name}" became an inventory part on {since}; it carries no stock before then, so a stock '
        f'line dated {date} cannot name it. Date the document on or after {since}, or, to correct a document '
        'entered before the conversion, leave its items as they were captured.'), details={
        'fields': [{'field': field, 'problem': f'is dated before {since}, when "{item_name}" became an inventory part'}],
        'record_type': 'item', 'record_id': item_id, 'inventory_since': since, 'reason': 'before_inventory_conversion'})


def last_use(db, item_id: str) -> dict[str, Any] | None:
    """The latest-dated document naming the item, or None when nothing does yet."""
    union = ' UNION ALL '.join(_USES.format(table=table) for table in _LINE_TABLES)
    row = db.raw.execute(f'SELECT date, type, number, id FROM ({union}) WHERE item_id = ? '
                         'ORDER BY date DESC, id DESC LIMIT 1', (item_id,)).fetchone()
    return None if row is None else dict(zip(('date', 'type', 'number', 'id'), row))


def open_documents(db, item_id: str) -> list[dict[str, Any]]:
    """Open orders and quotes whose current lines name the item."""
    found = []
    for kind, states in _OPEN_WORK.items():
        marks = ','.join('?' * len(states))
        found += [dict(type=kind, number=row[0], id=row[1]) for row in db.raw.execute(
            f'SELECT DISTINCT d.number, d.id FROM work_documents d JOIN work_lines l ON l.revision_id = d.current_revision_id '
            f'WHERE l.item_id = ? AND d.kind = ? AND d.status IN ({marks}) ORDER BY d.number', (item_id, kind, *states))]
    marks = ','.join('?' * len(_OPEN_ORDERS))
    found += [dict(type='purchase_order', number=row[0], id=row[1]) for row in db.raw.execute(
        f'SELECT DISTINCT o.number, o.id FROM purchase_orders o JOIN purchase_order_lines l ON l.revision_id = o.current_revision_id '
        f'WHERE l.item_id = ? AND o.status IN ({marks}) ORDER BY o.number', (item_id, *_OPEN_ORDERS))]
    return found


def _account_name(db, account_id: str | None) -> str | None:
    if account_id is None:
        return None
    return db.conn.execute(sa.select(schema.accounts.c.full_name).where(
        schema.accounts.c.id == account_id)).scalar_one_or_none()


def _default_cogs(db, current: dict[str, Any]) -> str:
    """The cost-of-goods account the item's sales will charge when none is named.

    The item's own expense account when that is a cost-of-goods account; else the one the
    company's inventory parts already use most; else the company's only one. Otherwise the
    choice is a real one and the person names it.
    """
    accounts, stock = schema.accounts, schema.items
    expense = current.get('expense_account_id')
    if expense is not None:
        kind = db.conn.execute(sa.select(accounts.c.type).where(accounts.c.id == expense)).scalar_one_or_none()
        if kind == 'cost_of_goods_sold':
            return expense
    used = db.conn.execute(
        sa.select(stock.c.cogs_account_id, sa.func.count().label('n'))
        .select_from(stock.join(accounts, accounts.c.id == stock.c.cogs_account_id))
        .where(stock.c.type.in_(('inventory_part', 'inventory_assembly')), accounts.c.active.is_(True))
        .group_by(stock.c.cogs_account_id).order_by(sa.desc('n'), accounts.c.full_name)).first()
    if used is not None:
        return used[0]
    rows = db.conn.execute(sa.select(accounts.c.id, accounts.c.full_name).where(
        accounts.c.type == 'cost_of_goods_sold', accounts.c.active.is_(True)).order_by(accounts.c.full_name)).all()
    if len(rows) == 1:
        return rows[0][0]
    names = ', '.join(row[1] for row in rows) or 'none yet; create one with `account create --type cost_of_goods_sold`'
    raise _invalid('cogs_account', f'name the cost-of-goods-sold account its sales will charge (the company has: {names})')


def _account_id(db, selector: str | None) -> str | None:
    if selector is None:
        return None
    from bookflow.company import accounts
    return str(accounts.resolve_account(db, selector)['id'])


@dataclass(frozen=True)
class ConversionPlan:
    item: dict[str, Any]
    update: Any
    effective_date: str
    last_use: dict[str, Any] | None


def plan(s, ctx, inp) -> ConversionPlan:
    db = s.company
    current = dict(items.resolve_item(db, inp.item))
    name, kind = current['full_name'], current['type']
    done = conversion(db, current['id'])
    if kind in ('inventory_part', 'inventory_assembly'):
        problem = (f'"{name}" is already an inventory part' + (f', converted on {done["effective_date"]}' if done else '')
                   + '. The change is one-way: an inventory part cannot be turned back into a non-inventory part.')
        raise _refuse('E_TYPE_CHANGE', problem, record_id=current['id'], **{'from': kind, 'to': TARGET})
    if kind not in CONVERTIBLE:
        raise _refuse('E_TYPE_CHANGE', f'"{name}" is a {kind.replace("_", " ")} item; only a non-inventory part or an '
                      'other charge can be converted to an inventory part.', record_id=current['id'],
                      **{'from': kind, 'to': TARGET, 'convertible': list(CONVERTIBLE)})
    if current.get('other_charge_percent_millionths') is not None:
        raise _refuse('E_TYPE_CHANGE', f'"{name}" is a percentage charge; it has no quantity to keep in stock.',
                      record_id=current['id'], **{'from': kind, 'to': TARGET})
    related = db.conn.execute(sa.select(schema.items.c.full_name).where(sa.or_(
        schema.items.c.parent_id == current['id'],
        schema.items.c.id == (current.get('parent_id') or '')))).scalars().all()
    if related:
        raise _refuse('E_TYPE_CHANGE', f'"{name}" is in a branch of the item list with {", ".join(sorted(related))}; '
                      'items in one branch share a type. Move it out of the branch (item update --parent-id) first.',
                      record_id=current['id'], related=sorted(related), **{'from': kind, 'to': TARGET})
    pending = open_documents(db, current['id'])
    if pending:
        listed = ', '.join(f'{doc["type"].replace("_", " ")} {doc["number"]}' for doc in pending)
        raise _refuse('E_RECORD_IN_USE', f'"{name}" is on open documents ({listed}). Their lines were taken as a '
                      f'{READABLE[kind]}; receiving or invoicing them after the conversion would change what they mean. '
                      'Close, finish or remove the item from them first.', record_id=current['id'],
                      documents=pending)

    from bookflow.company import memorized_schedule
    used = last_use(db, current['id'])
    # Today, or the last document's date when the books already run past today.
    date = inp.date or max(memorized_schedule.today(s.company_tz), used['date'] if used else '')
    if used is not None and date < used['date']:
        raise _invalid('date', f'must be on or after {used["date"]}, the date of the last document that uses "{name}" '
                       f'as a {READABLE[kind]} ({used["type"].replace("_", " ")} {used["number"] or used["id"]})',
                       last_use=used)

    state = items._payload_from_state(current, items._members(db, current['id']), items._vendors(db, current['id']))
    changes: dict[str, Any] = {
        'type': TARGET, 'sales_enabled': True, 'purchase_enabled': True,
        'cogs_account_id': _account_id(db, inp.cogs_account) or _default_cogs(db, current),
    }
    if inp.asset_account is not None:
        changes['asset_account_id'] = _account_id(db, inp.asset_account)
    if inp.income_account is not None:
        changes['income_account_id'] = _account_id(db, inp.income_account)
    for field, fallback in (('description', state.get('purchase_description')),
                            ('purchase_description', state.get('description'))):
        given = getattr(inp, field)
        if given is not None:
            changes[field] = given
        elif state.get(field) is None and fallback is not None:
            changes[field] = fallback
    for field in ('price', 'cost'):
        given = getattr(inp, field)
        if given is not None:
            changes[field] = given
        elif state.get(field) is None:
            changes[field] = '0'
    update = items.plan_item_update(db, current['id'], changes, actor_id=s.actor.id, via=ctx.interface.value,
                                    converting=True)
    return ConversionPlan(current, update, date, used)


def preview(s, planned: ConversionPlan, conversion_id: str | None = None) -> dict[str, Any]:
    db, before = s.company, planned.item
    after = planned.update.mutation.after
    from_type = before['type']
    return dict(
        conversion_id=conversion_id, item_id=before['id'], item_name=before['full_name'],
        from_type=from_type, to_type=TARGET, effective_date=planned.effective_date,
        version=int(after['version']),
        asset_account_id=after['asset_account_id'], asset_account=_account_name(db, after['asset_account_id']),
        cogs_account_id=after['cogs_account_id'], cogs_account=_account_name(db, after['cogs_account_id']),
        income_account_id=after['income_account_id'], income_account=_account_name(db, after['income_account_id']),
        dropped_expense_account=_account_name(db, before.get('expense_account_id')),
        changed_fields=list(planned.update.changed_fields),
        quantity_on_hand='0', last_document_date=None if planned.last_use is None else planned.last_use['date'],
        one_way=True,
        notes=[
            f'"{before["full_name"]}" becomes an inventory part on {planned.effective_date}. This cannot be undone.',
            'Nothing already posted changes: earlier purchases stay expensed and earlier sales stay without cost of '
            'goods. From the conversion date, purchases put it into stock and sales take it out at average cost.',
            STOCK_NOTE,
        ])


def apply(s, ctx, planned: ConversionPlan, command: str) -> tuple[str, list[Touched]]:
    """Write the item's new type and the conversion row under one audit event."""
    require_reason(ctx.reason)
    db = s.company
    mutation = planned.update.mutation
    event, conversion_id = new_id(), new_id()
    touched = Touched(items.ITEM_RECORD_TYPE, str(mutation.after['id']), 'update', int(mutation.before['version']),
                      int(mutation.after['version']), mutation.after_snapshot, mutation.before_snapshot, db='company')
    row = dict(id=conversion_id, item_id=planned.item['id'], from_type=planned.item['type'], to_type=TARGET,
               effective_date=planned.effective_date, reason=ctx.reason.strip(), created_at=clock.now_iso(),
               created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)
    record = Touched('item_conversion', conversion_id, 'create', None, 1, dict(row), db='company')
    audit.write_event_to(db, ctx, command, f'Converted item {planned.item["full_name"]} to an inventory part',
                         [touched, record], actor_id=s.actor.id, actor_kind=s.actor.kind, event_id=event)
    items.persist_item_update(db, planned.update)
    db.conn.execute(schema.item_conversions.insert().values(**row))
    return conversion_id, [touched, record]
