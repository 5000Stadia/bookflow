"""Fixed-asset depreciation schedules, posting and disposal (R119).

The anchor keeps fixed assets on its Fixed Asset Item List (cost, purchase date, accounts, sale
fields) and its accountant tool works out depreciation and posts a journal entry. Here the item
stays the asset; `fixed-asset setup` adds its book depreciation terms (``fixed_asset_setups``),
`fixed-asset schedule` shows every period with what is posted and what is pending,
`fixed-asset depreciate --through DATE` posts the pending periods, and `fixed-asset dispose`
removes the asset with its gain or loss. The arithmetic and the rounding rule are in
``company/amortization.py``.

**Posting.** Debit the item's depreciation expense account, credit its accumulated depreciation
account, in ordinary journal entries: one per period end date covering every asset due that day
(``per_period``), or one dated the through date covering everything pending (``combined``). Each
asset period posted is a ``fixed_asset_depreciations`` row naming its entry, written inside the
entry's own audit event. A period is posted while its entry is posted: running again posts
nothing twice, and voiding (or deleting) the entry reopens its periods for the next run. A date
in a closed period is refused; the dry-run is the preview.

**Disposal.** Debit the net proceeds to the account they went to, debit the accumulated
depreciation posted, credit the asset account with the cost, and the difference is the gain
(credited) or loss (debited) on the item's gain/loss account. Every period ending on or before
the disposal date must be posted first; periods after it stop. The item is marked sold (or
disposed of, when nothing was received) with the date, proceeds and costs, as the anchor's
"Item is sold" box does. Voiding the disposal entry undoes the posting; the item's sale fields
are then put back with `item update`, and depreciation resumes once it reads in service again.

Not in scope: tax bases and MACRS, units of production, mid-month or half-year conventions.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import sqlalchemy as sa

from bookflow.company import amortization as math, schema as c
from bookflow.company.fixed_asset_models import (
    DepreciationEntry, DepreciationLine, DisposalLine, FixedAssetDepreciateOutput, FixedAssetDisposal,
    FixedAssetDisposeOutput, FixedAssetListOutput, FixedAssetPeriod, FixedAssetScheduleOutput, FixedAssetSetupOutput,
    FixedAssetSummary,
)
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

_ACCOUNT_FIELDS = {
    'accumulated_depreciation_account': 'accumulated_depreciation_account_id',
    'depreciation_expense_account': 'depreciation_expense_account_id',
    'gain_loss_account': 'gain_loss_account_id',
}


def _invalid(field: str, problem: str, **details) -> BookflowError:
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}], **details})


def _money(units: int, currency: str):
    from bookflow.company.ledger_reports import money
    return money(units, currency)


def _units(text: str, currency: str, field: str) -> int:
    from bookflow.company.sales_models import money
    return money(text, currency, field).minor_units


def currency_of(s) -> str:
    return s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar_one()


def standing(s, ids) -> dict[str, str]:
    """Which of these transactions are posted and not deleted, with their numbers."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    t = c.transactions
    found = {r.id: r.number for r in s.company.conn.execute(
        sa.select(t.c.id, t.c.number).where(t.c.id.in_(ids), t.c.status == 'posted'))}
    from bookflow.company.journal_deletions import deleted_ids
    gone = deleted_ids(s, set(found))
    inspector = sa.inspect(s.company.conn)
    if found and inspector.has_table('purchase_deletions'):
        gone |= set(s.company.conn.execute(sa.select(c.purchase_deletions.c.transaction_id).where(
            c.purchase_deletions.c.transaction_id.in_(set(found)))).scalars())
    return {k: v for k, v in found.items() if k not in gone}


def _account(s, selector, field: str, types: set[str] | None = None) -> dict[str, Any]:
    from bookflow.company.accounts import resolve_account
    row = resolve_account(s.company, selector, by_number=True, follow_merge=True)
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': 'account', 'record_id': row['id'], 'field': field})
    if types is not None and row['type'] not in types:
        raise _invalid(field, f"must be an account of type {', '.join(sorted(types))}")
    return dict(row)


def _labels(s, ids) -> dict[str, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    a = c.accounts
    return {r.id: r.full_name for r in s.company.conn.execute(sa.select(a.c.id, a.c.full_name).where(a.c.id.in_(ids)))}


def resolve_asset(s, selector: str) -> dict[str, Any]:
    from bookflow.company.items import resolve_item
    item = dict(resolve_item(s.company, selector))
    if item['type'] != 'fixed_asset':
        raise _invalid('item', 'must be a fixed-asset item')
    return item


def _setup(s, item_id: str) -> dict[str, Any] | None:
    row = s.company.conn.execute(sa.select(c.fixed_asset_setups).where(
        c.fixed_asset_setups.c.item_id == item_id)).mappings().first()
    return dict(row) if row else None


def cost_of(item) -> int | None:
    for field in ('book_basis_minor_units', 'original_cost_minor_units'):
        if item.get(field) is not None:
            return int(item[field])
    return None


def _posted(s, item_ids) -> dict[str, dict[int, dict]]:
    """Standing posted periods per item: number -> row (with its journal number)."""
    d = c.fixed_asset_depreciations
    rows = [dict(r) for r in s.company.conn.execute(sa.select(d).where(d.c.item_id.in_(set(item_ids)))).mappings()]
    live = standing(s, {r['transaction_id'] for r in rows})
    out: dict[str, dict[int, dict]] = defaultdict(dict)
    for row in rows:
        if row['transaction_id'] in live:
            out[row['item_id']][row['period_number']] = dict(row, journal_number=live[row['transaction_id']])
    return out


def _disposal(s, item_id: str) -> dict[str, Any] | None:
    d = c.fixed_asset_disposals
    rows = [dict(r) for r in s.company.conn.execute(sa.select(d).where(d.c.item_id == item_id).order_by(d.c.created_at)).mappings()]
    live = standing(s, {r['transaction_id'] for r in rows})
    for row in reversed(rows):
        if row['transaction_id'] in live:
            return dict(row, journal_number=live[row['transaction_id']])
    return None


def _stop_date(item, disposal) -> str | None:
    if disposal is not None:
        return disposal['disposal_date']
    if item.get('disposal_status') not in (None, 'in_service'):
        return item.get('disposal_date')
    return None


def compute(s, item, setup, posted, disposal) -> list[dict]:
    """Every period with amount, basis, status, accumulated and book value."""
    cost = cost_of(item)
    rows = math.periods(setup['in_service_date'], setup['life_months'], setup['period'])
    amounts = {n: int(r['amount_minor_units']) for n, r in posted.items()}
    out = math.depreciation(cost, int(setup['salvage_minor_units']), setup['method'], setup['declining_percent'],
                            setup['life_months'], rows, amounts)
    stop = _stop_date(item, disposal)
    for row in out:
        link = posted.get(row['number'])
        row['transaction_id'] = link['transaction_id'] if link else None
        row['journal_number'] = link['journal_number'] if link else None
        row['status'] = 'posted' if link else 'stopped' if stop is not None and row['end'] > stop else 'pending'
    return out


def _schedule_output(s, item, setup, rows, disposal) -> FixedAssetScheduleOutput:
    currency = currency_of(s)
    m = lambda units: _money(units, currency)  # noqa: E731
    labels = _labels(s, [item.get('asset_account_id'), *(item.get(f) for f in _ACCOUNT_FIELDS.values()),
                         disposal and disposal.get('deposit_account_id')])
    cost = cost_of(item)
    status = 'disposed' if _stop_date(item, disposal) is not None else 'in_service'
    out = dict(item_id=item['id'], item=item['full_name'], asset_number=item.get('asset_number'), set_up=setup is not None,
               status=status, cost=None if cost is None else m(cost),
               asset_account=labels.get(item.get('asset_account_id')),
               **{name: labels.get(item.get(field)) for name, field in _ACCOUNT_FIELDS.items()})
    if disposal is not None:
        out['disposal'] = FixedAssetDisposal(
            date=disposal['disposal_date'], proceeds=m(disposal['proceeds_minor_units']), costs=m(disposal['costs_minor_units']),
            cost=m(disposal['cost_minor_units']), accumulated=m(disposal['accumulated_minor_units']),
            gain=m(disposal['gain_minor_units']), deposit_account=labels.get(disposal['deposit_account_id']),
            transaction_id=disposal['transaction_id'], journal_number=disposal['journal_number'])
    if setup is not None:
        posted = sum(r['amount'] for r in rows if r['status'] == 'posted')
        out.update(version=setup['version'], method=setup['method'], declining_percent=setup['declining_percent'],
                   salvage=m(setup['salvage_minor_units']), in_service_date=setup['in_service_date'],
                   life_months=setup['life_months'], period=setup['period'], accumulated_posted=m(posted),
                   book_value=None if cost is None else m(cost - posted),
                   pending_periods=sum(r['status'] == 'pending' for r in rows),
                   rows=[FixedAssetPeriod(number=r['number'], start=r['start'], end=r['end'], months=r['months'],
                                          amount=m(r['amount']), basis=r['basis'], status=r['status'],
                                          transaction_id=r['transaction_id'], journal_number=r['journal_number'],
                                          accumulated=m(r['accumulated']), book_value=m(r['book_value'])) for r in rows])
    return FixedAssetScheduleOutput(**out)


def schedule(s, inp) -> FixedAssetScheduleOutput:
    item = resolve_asset(s, inp.item)
    setup = _setup(s, item['id'])
    disposal = _disposal(s, item['id'])
    rows = compute(s, item, setup, _posted(s, [item['id']]).get(item['id'], {}), disposal) if setup and cost_of(item) else []
    return _schedule_output(s, item, setup, rows, disposal)


def list_assets(s, inp) -> FixedAssetListOutput:
    i = c.items
    items = [dict(r) for r in s.company.conn.execute(sa.select(i).where(i.c.type == 'fixed_asset').order_by(
        i.c.full_name_key).limit(inp.limit)).mappings()]
    posted = _posted(s, [x['id'] for x in items])
    currency = currency_of(s)
    out = []
    for item in items:
        setup, disposal = _setup(s, item['id']), _disposal(s, item['id'])
        status = 'disposed' if _stop_date(item, disposal) is not None else 'in_service'
        if status == 'disposed' and not inp.include_disposed:
            continue
        cost = cost_of(item)
        rows = compute(s, item, setup, posted.get(item['id'], {}), disposal) if setup and cost is not None else []
        done = sum(r['amount'] for r in rows if r['status'] == 'posted')
        pending = [r for r in rows if r['status'] == 'pending']
        out.append(FixedAssetSummary(
            item_id=item['id'], item=item['full_name'], asset_number=item.get('asset_number'), set_up=setup is not None,
            status=status, cost=None if cost is None else _money(cost, currency),
            accumulated_posted=_money(done, currency) if setup else None,
            book_value=_money(cost - done, currency) if setup and cost is not None else None,
            pending_periods=len(pending), next_period_end=pending[0]['end'] if pending else None))
    return FixedAssetListOutput(items=out)


# --------------------------------------------------------------------------------------- setup

def prepare_setup(s, ctx, inp) -> Plan:
    from bookflow.company import items as item_service
    item = resolve_asset(s, inp.item)
    currency = currency_of(s)
    before = _setup(s, item['id'])
    if before is not None and inp.expected_version is not None and inp.expected_version != before['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_type': 'fixed_asset_setup', 'expected_version': inp.expected_version,
                                                           'current_version': before['version']})
    old = before or {}
    method = inp.method or old.get('method')
    if method is None:
        raise _invalid('method', 'is required the first time an asset is set up')
    percent = inp.declining_percent if inp.declining_percent is not None else (
        old.get('declining_percent') if method == old.get('method') else None)
    if method == 'declining_balance' and percent is None:
        raise _invalid('declining_percent', 'is required with declining_balance: 200 or 150')
    if method == 'straight_line':
        if inp.declining_percent is not None:
            raise _invalid('declining_percent', 'applies only to declining_balance')
        percent = None
    in_service = inp.in_service_date or old.get('in_service_date') or item.get('purchase_date')
    if in_service is None:
        raise _invalid('in_service_date', "is required: the item has no purchase date")
    life = inp.life_months or old.get('life_months') or item.get('useful_life_months')
    if life is None:
        raise _invalid('life_months', 'is required: the item has no useful life')
    salvage = _units(inp.salvage, currency, 'salvage') if inp.salvage is not None else int(old.get('salvage_minor_units', 0))
    period = inp.period or old.get('period') or 'month'
    cost = cost_of(item)
    if cost is None:
        raise _invalid('item', 'has no original cost or book basis to depreciate')
    if salvage >= cost:
        raise _invalid('salvage', 'must be less than the cost basis')
    posted = _posted(s, [item['id']]).get(item['id'], {})
    if posted and before and (in_service != before['in_service_date'] or period != before['period']):
        raise _invalid('in_service_date' if in_service != before['in_service_date'] else 'period',
                       'cannot change once periods are posted; void their entries first')
    if posted and max(posted) > len(math.periods(in_service, life, period)):
        raise _invalid('life_months', 'is shorter than the periods already posted')
    item_changes: dict[str, Any] = {}
    for name, field in _ACCOUNT_FIELDS.items():
        selector = getattr(inp, name)
        if selector is not None:
            account = _account(s, selector, name)
            if account['id'] != item.get(field):
                item_changes[field] = account['id']
    if item.get('depreciation_method') != method:
        item_changes['depreciation_method'] = method
    if item.get('useful_life_months') != life:
        item_changes['useful_life_months'] = life
    at = clock.now_iso()
    values = dict(method=method, declining_percent=percent, salvage_minor_units=salvage, in_service_date=in_service,
                  life_months=life, period=period, currency=currency)
    changed = before is None or any(before[k] != v for k, v in values.items())
    if before is None:
        after = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, at), item_id=item['id'], **values)
    elif changed:
        after = dict(before, **values, version=before['version'] + 1, updated_at=at, updated_by=s.actor.id,
                     updated_via=ctx.interface.value)
    else:
        after = before
    item_plan = None
    shown = item
    if item_changes:
        item_plan = item_service.plan_item_update(s.company, item['id'], item_changes, actor_id=s.actor.id,
                                                  via=ctx.interface.value, at=at)
        if item_plan.mutation is not None:
            shown = dict(item_plan.mutation.after)
    disposal = _disposal(s, item['id'])
    rows = compute(s, shown, after, posted, disposal)
    output = FixedAssetSetupOutput(schedule=_schedule_output(s, shown, after, rows, disposal),
                                   changed=changed or bool(item_plan and item_plan.mutation))
    return Plan(output, dict(before=before, after=after, changed=changed, item_plan=item_plan))


def apply_setup(plan: Plan, ctx, s) -> Applied:
    from bookflow.company import items as item_service
    before, after, item_plan = plan.data['before'], plan.data['after'], plan.data['item_plan']
    touched = []
    if plan.data['changed']:
        if before is None:
            s.company.conn.execute(c.fixed_asset_setups.insert().values(**after))
            touched.append(Touched('fixed_asset_setup', after['id'], 'create', None, 1, after, db='company'))
        else:
            s.company.conn.execute(c.fixed_asset_setups.update().where(c.fixed_asset_setups.c.id == after['id']).values(**after))
            touched.append(Touched('fixed_asset_setup', after['id'], 'update', before['version'], after['version'], after, before, db='company'))
    if item_plan is not None and item_plan.mutation is not None:
        item_service.persist_item_update(s.company, item_plan)
        mutation = item_plan.mutation
        touched.append(Touched(item_service.ITEM_RECORD_TYPE, str(mutation.after['id']), mutation.action,
                               int(mutation.before['version']) if mutation.before else None, int(mutation.after['version']),
                               mutation.after_snapshot, mutation.before_snapshot, db='company'))
    if not touched:
        return Applied(plan.preview, [], 'no change')
    return Applied(plan.preview, touched, f"set up depreciation for {plan.preview.schedule.item}")


# ---------------------------------------------------------------------------------- depreciate

def _due(s, inp) -> tuple[list[dict], str]:
    """Pending (asset, period) pairs ending on or before ``through``, with the asset's accounts."""
    currency = currency_of(s)
    if inp.assets:
        items = [resolve_asset(s, sel) for sel in inp.assets]
        if len({i['id'] for i in items}) != len(items):
            raise _invalid('assets', 'name each asset once')
    else:
        i = c.items
        items = [dict(r) for r in s.company.conn.execute(sa.select(i).where(
            i.c.type == 'fixed_asset', i.c.id.in_(sa.select(c.fixed_asset_setups.c.item_id))).order_by(i.c.full_name_key)).mappings()]
    posted = _posted(s, [i['id'] for i in items])
    due = []
    for item in items:
        setup = _setup(s, item['id'])
        if setup is None:
            raise _invalid('assets', f"{item['full_name']} has no depreciation setup: run fixed-asset setup first")
        if cost_of(item) is None:
            raise _invalid('assets', f"{item['full_name']} has no original cost or book basis")
        rows = [r for r in compute(s, item, setup, posted.get(item['id'], {}), _disposal(s, item['id']))
                if r['status'] == 'pending' and r['end'] <= inp.through and r['amount'] > 0]
        if rows:
            for field in ('depreciation_expense_account_id', 'accumulated_depreciation_account_id'):
                if not item.get(field):
                    raise _invalid('assets', f"{item['full_name']} has no {field.removesuffix('_id').replace('_', ' ')}: "
                                             f"set it with fixed-asset setup")
        due.extend(dict(row, item=item) for row in rows)
    return due, currency


def _entries(inp, due) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in due:
        groups[inp.through if inp.entry == 'combined' else row['end']].append(row)
    entries = []
    for day in sorted(groups):
        per_item: dict[str, list[dict]] = defaultdict(list)
        for row in groups[day]:
            per_item[row['item']['id']].append(row)
        lines = [dict(item=rows[0]['item'], rows=rows, amount=sum(r['amount'] for r in rows)) for rows in per_item.values()]
        # A journal holds at most 200 lines: two per asset.
        for start in range(0, len(lines), 100):
            entries.append(dict(date=day, lines=lines[start:start + 100]))
    return entries


def _journal_input(inp, entry, currency):
    from bookflow.company.journal_models import JournalPostInput
    lines = []
    for line in entry['lines']:
        item, rows = line['item'], line['rows']
        span = f"period {rows[0]['number']}" if len(rows) == 1 else f"periods {rows[0]['number']}-{rows[-1]['number']}"
        text = f"Depreciation, {item['full_name']}, {span} ({rows[0]['start']} to {rows[-1]['end']})"
        amount = _money(line['amount'], currency).amount
        lines.append(dict(account=item['depreciation_expense_account_id'], side='debit', amount=amount, description=text))
        lines.append(dict(account=item['accumulated_depreciation_account_id'], side='credit', amount=amount, description=text))
    memo = inp.memo or f"Depreciation through {entry['date']}"
    return JournalPostInput.model_validate(dict(date=entry['date'], memo=memo, lines=lines))


def _depreciate_output(inp, entries, currency, *, written: bool) -> FixedAssetDepreciateOutput:
    out_entries, total, count = [], 0, 0
    for entry in entries:
        amount = sum(line['amount'] for line in entry['lines'])
        total += amount
        count += sum(len(line['rows']) for line in entry['lines'])
        out_entries.append(DepreciationEntry(
            date=entry['date'], transaction_id=entry.get('transaction_id'), journal_number=entry.get('number'),
            total=_money(amount, currency),
            lines=[DepreciationLine(item_id=line['item']['id'], item=line['item']['full_name'],
                                    periods=[r['number'] for r in line['rows']], amount=_money(line['amount'], currency))
                   for line in entry['lines']]))
    verb = 'Posted' if written else 'Would post'
    summary = (f"{verb} {count} period{'s' if count != 1 else ''} of depreciation in {len(entries)} "
               f"entr{'ies' if len(entries) != 1 else 'y'}, {_money(total, currency).amount} through {inp.through}"
               if entries else f"Nothing to post: every period through {inp.through} is posted")
    return FixedAssetDepreciateOutput(through=inp.through, entry=inp.entry, entries=out_entries, periods_posted=count,
                                      total=_money(total, currency), summary=summary)


def prepare_depreciate(s, ctx, inp) -> Plan:
    from bookflow.company.journals import open_dates
    due, currency = _due(s, inp)
    entries = _entries(inp, due)
    open_dates(s, sorted({e['date'] for e in entries}))
    for entry in entries:
        _journal_input(inp, entry, currency)
    return Plan(_depreciate_output(inp, entries, currency, written=False), dict(input=inp))


def apply_depreciate(plan: Plan, ctx, s) -> Applied:
    from bookflow.company import batches, journals
    inp = plan.data['input']
    due, currency = _due(s, inp)
    entries = _entries(inp, due)
    journals.open_dates(s, sorted({e['date'] for e in entries}))
    batch_id = batches._insert_batch(
        s, ctx, kind='fixed-asset-depreciation', label=f'Depreciation through {inp.through}',
        scope='fixed-asset:depreciate', ordering='strict', source_type='fixed-asset depreciate') if entries else None
    touched: list[Touched] = []
    for entry in entries:
        fresh = journals.prepare(s, ctx, _journal_input(inp, entry, currency), 'post')
        header = fresh.data['header']
        at = clock.now_iso()
        rows = [dict(id=new_id(), item_id=line['item']['id'], period_number=r['number'], period_start=r['start'],
                     period_end=r['end'], amount_minor_units=r['amount'], currency=currency, transaction_id=header['id'],
                     created_at=at, created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=fresh.data['event'])
                for line in entry['lines'] for r in line['rows']]
        applied = journals.persist_prepared(fresh, ctx, s, command_name='fixed-asset depreciate', noun='depreciation entry',
                                            extra=(('fixed_asset_depreciations', 'fixed_asset_depreciation', 'id', rows),))
        batches.record_effect(s, batch_id, record_type='journal_entry', record_id=header['id'],
                              action='created', version_after=header['version'], event_id=fresh.data['event'])
        touched.extend(applied.touched)
        entry.update(transaction_id=header['id'], number=header['number'])
    output = _depreciate_output(inp, entries, currency, written=True).model_copy(update={
        'batch': batch_id, 'next': f'batch reverse {batch_id} --dry-run' if batch_id else None})
    if not entries:
        return Applied(output, [], 'no depreciation to post')
    return Applied(output, touched, output.summary, audited=True)


# ------------------------------------------------------------------------------------- dispose

def _disposal_plan(s, inp) -> dict:
    item = resolve_asset(s, inp.item)
    currency = currency_of(s)
    if _disposal(s, item['id']) is not None:
        raise _invalid('item', 'is already disposed of; void its disposal entry first')
    cost = cost_of(item)
    if cost is None or cost <= 0:
        raise _invalid('item', 'has no original cost or book basis to remove')
    for field in ('asset_account_id', 'gain_loss_account_id'):
        if not item.get(field):
            raise _invalid('item', f"has no {field.removesuffix('_id').replace('_', ' ')}: set it with fixed-asset setup or item update")
    if item.get('purchase_date') and inp.date < item['purchase_date']:
        raise _invalid('date', 'cannot precede the purchase date')
    setup = _setup(s, item['id'])
    posted = _posted(s, [item['id']]).get(item['id'], {})
    accumulated = sum(int(r['amount_minor_units']) for r in posted.values())
    if setup is not None:
        late = [r for r in compute(s, dict(item, disposal_status='in_service'), setup, posted, None)
                if r['status'] == 'pending' and r['end'] <= inp.date and r['amount'] > 0]
        if late:
            raise _invalid('date', f"depreciation through {late[-1]['end']} is not posted yet: run "
                                   f"fixed-asset depreciate --through {inp.date} first", pending_periods=[r['number'] for r in late])
        beyond = [n for n, r in posted.items() if r['period_start'] > inp.date]
        if beyond:
            raise _invalid('date', 'periods after the disposal date are posted; void their entries first')
    if accumulated and not item.get('accumulated_depreciation_account_id'):
        raise _invalid('item', 'has no accumulated depreciation account')
    proceeds, costs = _units(inp.proceeds, currency, 'proceeds'), _units(inp.costs, currency, 'costs')
    net = proceeds - costs
    deposit = None
    if net != 0:
        if inp.deposit_to is None:
            raise _invalid('deposit_to', 'is required when proceeds less costs is not zero')
        deposit = _account(s, inp.deposit_to, 'deposit_to')
    gain = net - (cost - accumulated)
    lines = []
    if net:
        lines.append((deposit['id'], 'debit' if net > 0 else 'credit', abs(net), 'Net proceeds'))
    if accumulated:
        lines.append((item['accumulated_depreciation_account_id'], 'debit', accumulated, 'Accumulated depreciation removed'))
    lines.append((item['asset_account_id'], 'credit', cost, 'Cost removed'))
    if gain:
        lines.append((item['gain_loss_account_id'], 'credit' if gain > 0 else 'debit', abs(gain),
                      'Gain on disposal' if gain > 0 else 'Loss on disposal'))
    return dict(item=item, currency=currency, cost=cost, accumulated=accumulated, proceeds=proceeds, costs=costs,
                gain=gain, deposit=deposit, lines=lines)


def _dispose_output(s, inp, d, *, transaction_id=None, number=None) -> FixedAssetDisposeOutput:
    m = lambda units: _money(units, d['currency'])  # noqa: E731
    labels = _labels(s, [line[0] for line in d['lines']])
    lines = [DisposalLine(account=labels.get(a, a), **{side: m(amount)}) for a, side, amount, _ in d['lines']]
    word = 'gain' if d['gain'] >= 0 else 'loss'
    verb = 'Disposed of' if transaction_id else 'Would dispose of'
    return FixedAssetDisposeOutput(
        item_id=d['item']['id'], item=d['item']['full_name'], date=inp.date, proceeds=m(d['proceeds']), costs=m(d['costs']),
        cost=m(d['cost']), accumulated=m(d['accumulated']), gain=m(d['gain']), lines=lines, transaction_id=transaction_id,
        journal_number=number,
        summary=f"{verb} {d['item']['full_name']} on {inp.date}: {word} of {m(abs(d['gain'])).amount}")


def _dispose_journal(inp, d):
    from bookflow.company.journal_models import JournalPostInput
    label = d['item']['full_name']
    lines = [dict(account=a, side=side, amount=_money(amount, d['currency']).amount, description=f"{text}, {label}")
             for a, side, amount, text in d['lines']]
    return JournalPostInput.model_validate(dict(date=inp.date, memo=inp.memo or f"Disposal of {label}", lines=lines))


def prepare_dispose(s, ctx, inp) -> Plan:
    from bookflow.company.journals import open_dates
    d = _disposal_plan(s, inp)
    open_dates(s, [inp.date])
    _dispose_journal(inp, d)
    return Plan(_dispose_output(s, inp, d), dict(input=inp))


def apply_dispose(plan: Plan, ctx, s) -> Applied:
    from bookflow.company import items as item_service, journals
    from bookflow.core import audit
    inp = plan.data['input']
    d = _disposal_plan(s, inp)
    journals.open_dates(s, [inp.date])
    fresh = journals.prepare(s, ctx, _dispose_journal(inp, d), 'post')
    header = fresh.data['header']
    at = clock.now_iso()
    row = dict(id=new_id(), item_id=d['item']['id'], disposal_date=inp.date, proceeds_minor_units=d['proceeds'],
               costs_minor_units=d['costs'], cost_minor_units=d['cost'], accumulated_minor_units=d['accumulated'],
               gain_minor_units=d['gain'], deposit_account_id=d['deposit']['id'] if d['deposit'] else None,
               currency=d['currency'], transaction_id=header['id'], created_at=at, created_by=s.actor.id,
               created_via=ctx.interface.value, audit_event_id=fresh.data['event'])
    applied = journals.persist_prepared(fresh, ctx, s, command_name='fixed-asset dispose', noun='disposal entry',
                                        extra=(('fixed_asset_disposals', 'fixed_asset_disposal', 'id', [row]),))
    touched = list(applied.touched)
    # Mark the item sold (or disposed of), as the anchor's "Item is sold" box does.
    money = lambda units: _money(units, d['currency']).amount  # noqa: E731
    changes: dict[str, Any] = dict(disposal_status='sold' if d['proceeds'] else 'disposed', disposal_date=inp.date,
                                   disposal_proceeds=money(d['proceeds']) if d['proceeds'] else None,
                                   disposal_costs=money(d['costs']) if d['costs'] else None)
    item_plan = item_service.plan_item_update(s.company, d['item']['id'], changes, actor_id=s.actor.id,
                                              via=ctx.interface.value, at=at)
    if item_plan.mutation is not None:
        item_service.persist_item_update(s.company, item_plan)
        mutation = item_plan.mutation
        item_touch = Touched(item_service.ITEM_RECORD_TYPE, str(mutation.after['id']), mutation.action,
                             int(mutation.before['version']) if mutation.before else None, int(mutation.after['version']),
                             mutation.after_snapshot, mutation.before_snapshot, db='company')
        audit.write_event_to(s.company, ctx, 'fixed-asset dispose', f"marked {d['item']['full_name']} {changes['disposal_status']}",
                             [item_touch], actor_id=s.actor.id, actor_kind=s.actor.kind,
                             directive_code=getattr(s, 'directive_code', None), event_id=new_id())
        touched.append(item_touch)
    output = _dispose_output(s, inp, d, transaction_id=header['id'], number=header['number'])
    return Applied(output, touched, output.summary, audited=True)


# Journal inverses preserve the depreciation links as history. Newer runs unwind first.
from bookflow.company import batches as _batches
_batches.register(_batches.Adapter('fixed-asset-depreciation', release_document_numbers=False,
                                   ordering='strict', label='Fixed-asset depreciation'))
