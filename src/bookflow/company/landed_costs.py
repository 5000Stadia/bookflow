"""Landed cost: spreading a later freight, duty or broker's bill over the stock it came with (R104 increment 6).

design/advanced-inventory.md §5. A charge is an expense line on **Landed Cost Clearing** (system role
``landed_cost_clearing``) of a bill, check, card charge or vendor credit (a freight credit, negative); its
unallocated amount is its amount less its live allocation lines. ``landed-cost post`` shares charges over target
receipts by a basis and posts one journal entry dated at the allocation: per target receipt a ``landed``
movement debiting its Inventory Asset (one movement carries every charge's share of that receipt), per account
target a debit of that account, and per charge a credit of the clearing account. So the clearing account
always equals the unallocated charges, on every date.

Replay walks a landed movement at its receipt's position but recognises it at its own date
(``inventory_costing.replay``): stock issued before the allocation owes forward true-ups dated at it, written
by the same correction machinery every stock change uses (``inventory.complete``), through builds too.

**Exact shares.** Each charge is shared over the targets by cumulative endpoints in target order, rounded half
up at each endpoint -- the rule an item receipt has always spread its shipping by (``receiving_intervals``), so
a receipt's shipping reads exactly as before -- and so the shares sum exactly to the charge. Each charge is
shared on its own, so allocating two charges in either order gives the same shares; ``value`` reads each
receipt's own cost before any landed cost, so earlier allocations never change later shares. With ``amounts``
the stated shares are laid end to end against the charges, so both sum exactly.

**Guards.** A charge is never allocated beyond its amount, and a freight credit never takes a receipt's landed
total below zero. Voiding or reducing a charge line (``check_charges``, after every company write), and voiding
or changing a target receipt (``check_targets``, wherever stock is reversed), are refused with
E_WORK_DEPENDENCY while a live allocation line names it.
"""
from __future__ import annotations

import sqlalchemy as sa

from bookflow.company import accounts as account_service, inventory, journals, schema as c
from bookflow.company import items as item_service
from bookflow.company.inventory_costing import StockRefusal, replay
from bookflow.company.journal_models import JournalLineInput, JournalPostInput, JournalVoidInput, parse_domestic_amount
from bookflow.company.landed_cost_models import (
    AllocationLineOutput, AllocationSummary, ChargeOutput, ChargesOutput, LandedCostListOutput, LandedCostListRow,
    LandedCostOutput, LandedCostSetupOutput, LandedCostWriteOutput, TargetShareOutput, TrueUpOutput,
)
from bookflow.company.landed_cost_schema import CLEARING_ROLE
from bookflow.company.receiving_intervals import cumulative
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units, parse_quantity_micro_units, round_ratio_half_even
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched

ROLE = CLEARING_ROLE
ACCOUNT_NAME = 'Landed Cost Clearing'
SEQUENCE = 'landed_cost'
KIND = 'landed_cost'
TARGET_KINDS = ('bill', 'check', 'card_charge', 'item_receipt')


def _invalid(field, problem, message=None, **details):
    """A refusal naming its field; the message is the problem, said as a sentence, unless given."""
    message = message or f'{field}: {problem}.'
    return BookflowError('E_VALIDATION', message=message,
                         details={'fields': [{'field': field, 'problem': problem}], **details})


def _home(s):
    return s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar_one()


def _money(minor, currency):
    return Money(int(minor), currency).to_dict()


# ---------------------------------------------------------------- the clearing account


def find_account(db):
    row = db.conn.execute(sa.select(c.accounts).where(c.accounts.c.system_role == ROLE)).mappings().first()
    return dict(row) if row is not None else None


def ensure_account(s, ctx):
    """The clearing account, made on first use; returns (account, touched)."""
    account = find_account(s.company)
    if account is not None:
        return account, []
    from bookflow.company.lists import normalize_display_name
    name = ACCOUNT_NAME
    for candidate in [ACCOUNT_NAME] + [f'{ACCOUNT_NAME} {n}' for n in range(2, 50)]:
        _, key = normalize_display_name(candidate)
        if s.company.conn.execute(sa.select(c.accounts.c.id).where(c.accounts.c.full_name_key == key)).first() is None:
            name = candidate
            break
    mutation = account_service.plan_account_create(
        s.company, {'name': name, 'type': 'other_current_asset',
                    'description': 'Freight, duty and other landed costs waiting to be spread over the stock they came with'},
        actor_id=s.actor.id, via=ctx.interface.value, at=clock.now_iso())
    mutation = account_service.AccountMutation(mutation.action, mutation.before, dict(mutation.after, system_role=ROLE))
    account_service.persist_account_mutation(s.company, mutation)
    account = find_account(s.company)
    return account, [Touched('account', account['id'], 'create', None, account['version'], dict(account), None, db='company')]


def plan_setup(s, ctx, inp):
    account = find_account(s.company)
    if account is not None:
        return Plan(LandedCostSetupOutput(account_id=account['id'], account=account['full_name'], created=False,
                                          message=f'"{account["full_name"]}" already exists.'))
    return Plan(LandedCostSetupOutput(account_id='', account=ACCOUNT_NAME, created=True, message=(
        f'Makes the "{ACCOUNT_NAME}" account (other current asset). Charge freight and duty to it on bill, check, '
        'card charge and vendor credit expense lines, then spread it with `landed-cost post`.')))


def apply_setup(plan, ctx, s):
    account, touched = ensure_account(s, ctx)
    if not touched:
        return Applied(plan.preview, [], 'no change')
    audit.write_event_to(s.company, ctx, 'landed-cost setup', f'create account {account["full_name"]}', touched,
                         actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None))
    return Applied(LandedCostSetupOutput(account_id=account['id'], account=account['full_name'], created=True,
                                         message=f'Made "{account["full_name"]}".'), touched,
                   f'create account {account["full_name"]}', audited=True)


# ---------------------------------------------------------------- charges


def _landed_journals():
    t = c.inventory_documents
    return sa.select(t.c.transaction_id).where(t.c.kind == KIND)


def charge_entries(s, clearing_id, transaction_ids=None):
    """Every clearing posting of a charge document, by document: ``{transaction id: [(date, signed, row)]}``.

    A charge is the document's Landed Cost Clearing lines taken together, named by the document's id: an edit
    gives a line a new identity, reorders it or adds another, and the charge is still the same charge. Its amount
    on a date is the sum of its postings dated by then, so a correction or a void moves it as it moves the account.
    """
    pl, pb, src, dl = c.posting_lines, c.posting_batches, c.posting_line_sources, c.document_lines
    query = (sa.select(pl.c.transaction_id, dl.c.line_id, pb.c.effective_date, pb.c.id.label('batch_id'), pb.c.kind,
                       pl.c.debit_minor_units, src.c.amount_minor_units, dl.c.description, pl.c.name_id)
             .select_from(pl.join(pb, pb.c.id == pl.c.batch_id).join(src, src.c.posting_line_id == pl.c.id)
                          .join(dl, dl.c.id == src.c.document_line_id))
             .where(pl.c.account_id == clearing_id, pl.c.transaction_id.not_in(_landed_journals())))
    if transaction_ids is not None:
        query = query.where(pl.c.transaction_id.in_(list(transaction_ids)))
    out: dict[str, list] = {}
    for row in s.company.conn.execute(query).mappings():
        signed = int(row['amount_minor_units']) * (1 if int(row['debit_minor_units']) else -1)
        out.setdefault(row['transaction_id'], []).append((row['effective_date'], signed, dict(row)))
    return out


def live_lines(s, *, charge_ids=None, target_ids=None, allocation_ids=None):
    """Allocation lines no reversal names, and that reverse nothing."""
    t = c.landed_cost_allocation_lines
    x = t.alias('x')
    query = sa.select(t, c.landed_cost_allocations.c.date, c.landed_cost_allocations.c.kind,
                      c.landed_cost_allocations.c.number).join(
        c.landed_cost_allocations, c.landed_cost_allocations.c.id == t.c.allocation_id).where(
        t.c.reverses_line_id.is_(None), ~sa.exists(sa.select(x.c.id).where(x.c.reverses_line_id == t.c.id)))
    if charge_ids is not None:
        query = query.where(t.c.charge_line_id.in_(list(charge_ids)))
    if allocation_ids is not None:
        query = query.where(t.c.allocation_id.in_(list(allocation_ids)))
    rows = [dict(row) for row in s.company.conn.execute(query).mappings()]
    # Where each share stands now: a receipt reposted with its stock unchanged carries its landed cost on.
    moved = successors(s, {row['target_movement_id'] for row in rows if row['target_movement_id']})
    for row in rows:
        row['current_target'] = moved.get(row['target_movement_id'], row['target_movement_id'])
    if target_ids is not None:
        wanted = set(target_ids)
        rows = [row for row in rows if row['current_target'] in wanted]
    return rows


def successors(s, ids):
    """``{movement id: the receipt movement that carries it now}`` for receipts reposted with stock unchanged."""
    ids = {i for i in ids if i}
    if not ids or not sa.inspect(s.company.conn).has_table('landed_cost_carries'):
        return {}
    t = c.landed_cost_carries
    out, frontier = {}, dict.fromkeys(ids)
    current = {i: i for i in ids}
    while frontier:
        found = dict(s.company.conn.execute(sa.select(t.c.from_movement_id, t.c.to_movement_id).where(
            t.c.from_movement_id.in_(list({current[i] for i in frontier})))).all())
        frontier = {i: None for i in frontier if current[i] in found}
        for i in frontier:
            current[i] = found[current[i]]
            out[i] = current[i]
    return out


def charges(s, clearing_id, *, as_of=None, transaction_ids=None):
    """Each charge (a document's clearing lines): its date, amount, what live allocations took of it and what is left."""
    entries = charge_entries(s, clearing_id, transaction_ids)
    allocated: dict[str, int] = {}
    for line in live_lines(s, charge_ids=list(entries)):
        if as_of is None or line['date'] <= as_of:
            allocated[line['charge_line_id']] = allocated.get(line['charge_line_id'], 0) + int(line['amount_minor_units'])
    batches = {row[2]['batch_id'] for rows in entries.values() for row in rows}
    reversed_batches = set(s.company.conn.execute(sa.select(c.posting_batches.c.reverses_batch_id).where(
        c.posting_batches.c.reverses_batch_id.in_(list(batches)))).scalars()) if batches else set()
    out = {}
    for charge, rows in entries.items():
        dated = [row for row in rows if as_of is None or row[0] <= as_of]
        if not dated:
            continue
        standing = [row for row in dated if row[2]['kind'] != 'reversal' and row[2]['batch_id'] not in reversed_batches]
        first = (standing or dated)[-1][2]
        amount = sum(row[1] for row in dated)
        descriptions = list(dict.fromkeys(row[2]['description'] for row in (standing or dated) if row[2]['description']))
        out[charge] = dict(charge=charge, transaction_id=charge,
                           date=max(row[0] for row in (standing or dated)), amount=amount,
                           allocated=allocated.get(charge, 0), unallocated=amount - allocated.get(charge, 0),
                           description='; '.join(descriptions) or None, vendor_id=first['name_id'])
    return out


def _documents(s, transaction_ids):
    """``{transaction id: (type, number, vendor name)}`` for the documents charges and targets live on."""
    if not transaction_ids:
        return {}
    ids = list(transaction_ids)
    rows = s.company.conn.execute(sa.select(c.transactions.c.id, c.transactions.c.type, c.transactions.c.number)
                                  .where(c.transactions.c.id.in_(ids))).all()
    from bookflow.company import journals as j
    kinds = j.document_kinds(s, [row.id for row in rows if row.type == 'journal_entry'])
    receipts = dict(s.company.conn.execute(sa.select(c.item_receipts.c.transaction_id, c.item_receipts.c.number).where(
        c.item_receipts.c.transaction_id.in_(ids))).all())
    vendors = {}
    for tid, name in s.company.conn.execute(
            sa.select(c.posting_lines.c.transaction_id, c.vendors.c.name).join(
                c.vendors, c.vendors.c.id == c.posting_lines.c.name_id).where(
                c.posting_lines.c.transaction_id.in_(ids), c.posting_lines.c.name_type == 'vendor')):
        vendors.setdefault(tid, name)
    out = {}
    for row in rows:
        if row.id in receipts:
            out[row.id] = ('item_receipt', receipts[row.id], vendors.get(row.id))
        else:
            out[row.id] = (kinds.get(row.id) or row.type, row.number, vendors.get(row.id))
    return out


def _label(document):
    kind, number, _ = document
    return f'{kind.replace("_", " ")} {number}' if number else kind.replace('_', ' ')


def unallocated_by_date(s, clearing_id):
    """``(clearing balance, unallocated charges)`` on each date either moves, as running totals."""
    pl, pb = c.posting_lines, c.posting_batches
    moves: dict[str, list] = {}
    for date, debit, credit in s.company.conn.execute(
            sa.select(pb.c.effective_date, pl.c.debit_minor_units, pl.c.credit_minor_units).join(
                pb, pb.c.id == pl.c.batch_id).where(pl.c.account_id == clearing_id)):
        moves.setdefault(date, [0, 0])[0] += int(debit) - int(credit)
    for rows in charge_entries(s, clearing_id).values():
        for date, signed, _ in rows:
            moves.setdefault(date, [0, 0])[1] += signed
    for line in live_lines(s):
        if line['kind'] == 'allocation':
            moves.setdefault(line['date'], [0, 0])[1] -= int(line['amount_minor_units'])
    balance = unallocated = 0
    out = []
    for date in sorted(moves):
        balance += moves[date][0]
        unallocated += moves[date][1]
        out.append((date, balance, unallocated))
    return out


def check_tie(s, clearing_id):
    """Refuse, as the stock reports do, when the clearing account and the unallocated charges differ on a date."""
    for date, balance, unallocated in unallocated_by_date(s, clearing_id):
        if balance != unallocated:
            currency = _home(s)
            raise BookflowError('E_VALIDATION', message=(
                f'Landed Cost Clearing holds {Money(balance, currency)} on {date} but the unallocated charges total '
                f'{Money(unallocated, currency)}; something other than a charge or an allocation posted to it.'),
                details={'date': date, 'clearing_balance_minor_units': balance,
                         'unallocated_minor_units': unallocated, 'reason': 'clearing_tie'})


def charges_report(s, ctx, inp):
    currency = _home(s)
    as_of = inp.as_of or '9999-12-31'   # every date, unless the caller asks for one
    account = find_account(s.company)
    if account is None:
        return Plan(ChargesOutput(as_of=inp.as_of or '', clearing_account_id=None, clearing_account=None, rows=[],
                                  total_unallocated=_money(0, currency), clearing_balance=_money(0, currency),
                                  message=(f'No {ACCOUNT_NAME} account yet: `landed-cost setup` makes it; then '
                                           'charge freight and duty to it on bill, check or card charge expense lines.')))
    check_tie(s, account['id'])
    found = charges(s, account['id'], as_of=as_of)
    documents = _documents(s, {row['transaction_id'] for row in found.values()})
    vendor_id = None
    if inp.vendor is not None:
        from bookflow.company.parties import resolve_party
        vendor_id = resolve_party(s.company, 'vendor', inp.vendor)['id']
    rows = []
    for row in sorted(found.values(), key=lambda row: (row['date'], documents[row['transaction_id']][1] or '', row['charge'])):
        if (not inp.include_allocated and not row['unallocated']) or not (row['amount'] or row['allocated']):
            continue
        if vendor_id is not None and row['vendor_id'] != vendor_id:
            continue
        kind, number, vendor = documents[row['transaction_id']]
        rows.append(ChargeOutput(charge=row['charge'], transaction_id=row['transaction_id'], document_type=kind,
                                 document_number=number, vendor=vendor, date=row['date'],
                                 description=row['description'], amount=_money(row['amount'], currency),
                                 allocated=_money(row['allocated'], currency),
                                 unallocated=_money(row['unallocated'], currency)))
    balance = next((b for date, b, _ in reversed(unallocated_by_date(s, account['id'])) if date <= as_of), 0)
    total = sum(row['unallocated'] for row in found.values())
    return Plan(ChargesOutput(as_of=inp.as_of or '', clearing_account_id=account['id'], clearing_account=account['full_name'],
                              rows=rows, total_unallocated=_money(total, currency),
                              clearing_balance=_money(balance, currency)))


# ---------------------------------------------------------------- allocating


def share(amount, measures):
    """``amount`` over ``measures`` by cumulative endpoints in order, rounded half up at each; sums exactly."""
    total = sum(measures)
    sign, magnitude = (1, amount) if amount >= 0 else (-1, -amount)
    out, position = [], 0
    for measure in measures:
        end = position + measure
        out.append(sign * (cumulative(total, magnitude, end) - cumulative(total, magnitude, position)))
        position = end
    return out


def lay_end_to_end(charge_amounts, target_amounts):
    """With stated amounts: charges and targets laid end to end; each pair takes their overlap (same sign)."""
    sign = -1 if any(a < 0 for a in charge_amounts) else 1
    shares = [[0] * len(target_amounts) for _ in charge_amounts]
    c_edges, t_edges, position = [], [], 0
    for amount in charge_amounts:
        c_edges.append((position, position + abs(amount)))
        position += abs(amount)
    position = 0
    for amount in target_amounts:
        t_edges.append((position, position + abs(amount)))
        position += abs(amount)
    for i, (a0, a1) in enumerate(c_edges):
        for j, (b0, b1) in enumerate(t_edges):
            shares[i][j] = sign * max(0, min(a1, b1) - max(a0, b0))
    return shares


def _retired_ids(s, ids):
    if not ids:
        return set()
    t = c.inventory_movements
    return set(s.company.conn.execute(sa.select(t.c.reverses_movement_id).where(
        t.c.reverses_movement_id.in_(list(ids)))).scalars())


def target_kind(s, transaction_id):
    """Which purchase document a transaction is, when its item lines may take landed cost; else None."""
    row = s.company.conn.execute(sa.select(c.transactions.c.type).where(c.transactions.c.id == transaction_id)).scalar()
    if row == 'bill':
        return 'bill'
    if row != 'journal_entry':
        return None
    if journals.item_receipt_entry(s, transaction_id):
        return 'item_receipt'
    from bookflow.company import money_out
    kind = money_out.owning_document_kind(s, transaction_id)
    return kind if kind in ('check', 'card_charge') else None


def _transaction_by_selector(s, selector, field, kinds):
    """A document by id or number: an item receipt's own number, then a transaction id or number."""
    t = c.transactions
    receipt = s.company.conn.execute(sa.select(c.item_receipts.c.transaction_id).where(sa.or_(
        c.item_receipts.c.id == selector, c.item_receipts.c.number == selector))).scalar()
    if receipt is not None:
        return receipt
    key = selector.upper() if is_ulid(selector) else selector
    ids = list(s.company.conn.execute(sa.select(t.c.id).where(sa.or_(t.c.id == key, t.c.number == selector))).scalars())
    ids = [tid for tid in ids if kinds(tid)]
    if not ids:
        raise _invalid(field, f'no document "{selector}" here can take part in a landed cost allocation')
    if len(ids) > 1:
        raise _invalid(field, f'"{selector}" names {len(ids)} documents; give the id', candidates=ids)
    return ids[0]


def _bought_receipts(s, transaction_id):
    rows = [row for row in inventory.movements(s, transaction_id=transaction_id)
            if row['kind'] == 'receipt' and row.get('returns_movement_id') is None]
    retired = _retired_ids(s, [row['id'] for row in rows])
    return sorted((row for row in rows if row['id'] not in retired), key=lambda row: int(row['sequence']))


def _resolve_targets(s, inp, currency):
    """Each target: ``dict(kind='receipt', movement=row, item=item, document=label)`` or an account."""
    out, seen = [], set()
    for index, target in enumerate(inp.targets):
        field = f'targets.{index}'
        chosen = [x for x in ('document', 'movement', 'account') if getattr(target, x) is not None]
        if len(chosen) != 1:
            raise _invalid(field, 'name exactly one of document, movement or account')
        if target.account is not None:
            if inp.basis != 'amounts':
                raise _invalid(field + '.account', 'a share to an account needs basis amounts, with its amount stated')
            account = journals.active(account_service.resolve_account(s.company, target.account), 'account')
            if account['type'] not in ('expense', 'other_expense', 'cost_of_goods_sold') or (
                    account.get('system_role') not in (None, 'cost_of_goods_sold')):
                raise _invalid(field + '.account', 'a charge not capitalised goes to an expense or cost-of-goods account')
            out.append(dict(kind='account', account=account, document=None, field=field, input=target))
            continue
        if target.movement is not None:
            row = s.company.conn.execute(sa.select(c.inventory_movements).where(
                c.inventory_movements.c.id == target.movement.upper())).mappings().first()
            if row is None or row['kind'] != 'receipt' or row['returns_movement_id'] is not None \
                    or _retired_ids(s, [row['id']]) or not target_kind(s, row['transaction_id']):
                raise _invalid(field + '.movement', 'not a standing receipt of a bill, check, card charge or item receipt')
            rows = [dict(row)]
            transaction_id = row['transaction_id']
        else:
            transaction_id = _transaction_by_selector(s, target.document, field + '.document', lambda tid: target_kind(s, tid))
            rows = _bought_receipts(s, transaction_id)
            if target.line is not None:
                if target.line > len(rows):
                    raise _invalid(field + '.line', f'the document has {len(rows)} stock lines')
                rows = [rows[target.line - 1]]
            if target.item is not None:
                item = item_service.resolve_item(s.company, target.item)
                rows = [row for row in rows if row['item_id'] == item['id']]
            if not rows:
                raise _invalid(field, 'that document brought no stock in')
        for row in rows:
            if row['id'] in seen:
                raise _invalid(field, 'a receipt line is named twice')
            seen.add(row['id'])
            item = item_service.resolve_item(s.company, row['item_id'])
            out.append(dict(kind='receipt', movement=row, item=item, transaction_id=transaction_id, field=field,
                            input=target))
    return out


def receipt_own_value(s, row):
    """A receipt line's own cost: its value with its price corrections, less its receipt's own shipping share."""
    t = c.inventory_movements
    recosts = [dict(r) for r in s.company.conn.execute(sa.select(t).where(
        t.c.corrects_movement_id == row['id'], t.c.kind == 'recost', t.c.filled_by_movement_id.is_(None))).mappings()]
    retired = _retired_ids(s, [r['id'] for r in recosts])
    value = int(row['value_minor_units']) + sum(int(r['value_minor_units']) for r in recosts if r['id'] not in retired)
    shipping = sum(int(line['amount_minor_units']) for line in live_lines(s, target_ids=[row['id']])
                   if line['kind'] == 'receipt_shipping')
    return value - shipping


def _measures(s, targets, basis, field_of):
    out = []
    for target in targets:
        if target['kind'] == 'account':
            out.append(0)
            continue
        row, item, given = target['movement'], target['item'], target['input']
        quantity = int(row['quantity_microunits'])
        if basis in ('quantity', 'amounts'):
            out.append(quantity)
        elif basis == 'value':
            out.append(max(receipt_own_value(s, row), 0))
        else:
            override = given.unit_weight if basis == 'weight' else given.unit_volume
            per = (parse_quantity_micro_units(override, field=f'{target["field"]}.unit_{basis}') if override is not None
                   else item.get(f'unit_{basis}_microunits'))
            if not per:
                raise _invalid(f'{target["field"]}.unit_{basis}', (
                    f'"{item["full_name"]}" has no unit {basis}; set it with `item update --unit-{basis}` or give '
                    f'unit_{basis} on this target'), item_id=item['id'])
            out.append(quantity * int(per))
    return out


def _resolve_charges(s, inp, clearing_id, currency):
    table = charges(s, clearing_id)
    chosen, seen = [], set()
    for index, given in enumerate(inp.charges):
        field = f'charges.{index}'
        if (given.charge is None) == (given.document is None):
            raise _invalid(field, 'name a charge or a document')
        if given.charge is not None:
            key = given.charge.upper()
            if key not in table:
                raise _invalid(field + '.charge', 'not a charge on Landed Cost Clearing; `landed-cost charges` lists them')
            found = [table[key]]
        else:
            transaction_id = _transaction_by_selector(s, given.document, field + '.document', lambda tid: tid in table)
            found = [table[transaction_id]]
        labels = _documents(s, {row['transaction_id'] for row in found})
        for row in found:
            if row['charge'] in seen:
                raise _invalid(field, 'a charge is named twice')
            seen.add(row['charge'])
            left = row['unallocated']
            if not left:
                raise _invalid(field, f'nothing left to allocate on {_label(labels[row["transaction_id"]])}',
                               charge=row['charge'])
            amount = left
            if given.amount is not None:
                magnitude = parse_domestic_amount(given.amount, currency, field + '.amount').minor_units
                amount = abs(magnitude) * (1 if left > 0 else -1)
                if not amount or abs(amount) > abs(left):
                    raise _invalid(field + '.amount', (
                        f'at most {Money(abs(left), currency)} of that charge is unallocated'), charge=row['charge'])
            chosen.append(dict(row, take=amount, field=field))
    if not chosen:
        raise _invalid('charges', 'nothing left to allocate on the charges named')
    if len({amount['take'] > 0 for amount in chosen}) > 1:
        raise _invalid('charges', 'allocate charges and freight credits in separate allocations')
    return chosen


def _number(s, explicit):
    t = c.landed_cost_allocations

    def taken(number):
        return s.company.conn.execute(sa.select(t.c.id).where(t.c.number == number, t.c.kind == 'allocation')).first() is not None
    if explicit is not None:
        if taken(explicit):
            raise BookflowError('E_DUPLICATE_NUMBER', details={'number': explicit, 'type': 'landed_cost_allocation'})
        return explicit, None
    seq = s.company.conn.execute(sa.select(c.sequences).where(c.sequences.c.name == SEQUENCE)).mappings().first()
    number, prefix = (seq['next_number'], seq['prefix']) if seq else (1, 'LC-')
    while taken(f'{prefix}{number}'):
        number += 1
    return f'{prefix}{number}', dict(name=SEQUENCE, next_number=number + 1, prefix=prefix)


def _landed_totals(s, receipt_ids):
    totals: dict[str, int] = {}
    for line in live_lines(s, target_ids=receipt_ids):
        totals[line['current_target']] = totals.get(line['current_target'], 0) + int(line['amount_minor_units'])
    return totals


def _default_date(s, chosen, targets, given):
    latest_receipt = max((t['movement']['effective_date'] for t in targets if t['kind'] == 'receipt'), default=None)
    if given is not None:
        if latest_receipt and given < latest_receipt:
            raise _invalid('date', f'an allocation is never dated before a receipt it lands on ({latest_receipt})')
        return given
    date = max(row['date'] for row in chosen)
    if latest_receipt and date < latest_receipt:
        date = latest_receipt
    closing = s.company.conn.execute(sa.select(c.company_info.c.closing_date)).scalar_one()
    if closing and date <= closing:
        from datetime import date as calendar, timedelta
        date = (calendar.fromisoformat(closing) + timedelta(days=1)).isoformat()
    return date


def _post_change(s, ctx, inp):
    currency = _home(s)
    account = find_account(s.company)
    if account is None:
        raise _invalid('charges', f'there is no {ACCOUNT_NAME} account yet', message=(
            f'There is no {ACCOUNT_NAME} account yet, so there are no charges to allocate. Run `landed-cost setup`, '
            'charge the freight to it on the bill, then allocate it.'))
    if not account['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': 'account', 'record_id': account['id']})
    chosen = _resolve_charges(s, inp, account['id'], currency)
    targets = _resolve_targets(s, inp, currency)
    if inp.basis == 'amounts':
        stated = []
        for target in targets:
            if target['input'].amount is None:
                raise _invalid(target['field'] + '.amount', 'basis amounts needs each target\'s amount')
            stated.append(abs(parse_domestic_amount(target['input'].amount, currency, target['field'] + '.amount').minor_units))
        sign = 1 if chosen[0]['take'] > 0 else -1
        if sum(stated) != sum(abs(row['take']) for row in chosen):
            raise _invalid('targets', (f'the stated amounts total {Money(sum(stated), currency)} but the charges '
                                       f'total {Money(sum(abs(row["take"]) for row in chosen), currency)}'))
        matrix = lay_end_to_end([row['take'] for row in chosen], [sign * a for a in stated])
        measures = stated
    else:
        if any(target['kind'] == 'account' for target in targets):
            raise _invalid('targets', 'a share to an account needs basis amounts')
        measures = _measures(s, targets, inp.basis, None)
        if not sum(measures):
            raise _invalid('targets', f'the targets weigh nothing by {inp.basis}; choose another basis')
        matrix = [share(row['take'], measures) for row in chosen]
    date = _default_date(s, chosen, targets, inp.date)
    # A freight credit never takes a receipt's landed total below zero.
    receipt_ids = [t['movement']['id'] for t in targets if t['kind'] == 'receipt']
    totals = _landed_totals(s, receipt_ids)
    for j, target in enumerate(targets):
        if target['kind'] != 'receipt':
            continue
        after = totals.get(target['movement']['id'], 0) + sum(matrix[i][j] for i in range(len(chosen)))
        if after < 0:
            raise _invalid(target['field'], (
                f'this freight credit would take the landed cost of {target["item"]["full_name"]} on that receipt '
                f'below zero (it has {Money(totals.get(target["movement"]["id"], 0), currency)})'))
    number, sequence_row = _number(s, inp.number)
    documents = _documents(s, {row['transaction_id'] for row in chosen} | {t['transaction_id'] for t in targets
                                                                          if t['kind'] == 'receipt'})
    sequence = inventory.next_sequence(s)
    lines, movements, items, line_plan = [], [], {}, []
    for j, target in enumerate(targets):
        amount = sum(matrix[i][j] for i in range(len(chosen)))
        if target['kind'] == 'account':
            if amount:
                lines.append(JournalLineInput(account=target['account']['id'], side='debit' if amount > 0 else 'credit',
                                              amount=_money(abs(amount), currency),
                                              description=f'Landed cost {number} not capitalised'))
            continue
        row, item = target['movement'], target['item']
        items.setdefault(item['id'], item)
        landed_id = None
        if amount:
            label = _label(documents[target['transaction_id']])
            lines.append(JournalLineInput(account=row['asset_account_id'], side='debit' if amount > 0 else 'credit',
                                          amount=_money(abs(amount), currency), class_id=row['class_id'],
                                          description=f'Landed cost {number} on {label}, {item["full_name"]}'))
            landed_id = new_id()
            movements.append(inventory._Movement(dict(
                id=landed_id, item_id=item['id'], kind='landed', quantity_microunits=0, value_minor_units=amount,
                effective_date=date, sequence=sequence, currency=currency, asset_account_id=row['asset_account_id'],
                offset_account_id=account['id'], class_id=row['class_id'], corrects_movement_id=None,
                reverses_movement_id=None, returns_movement_id=None, filled_by_movement_id=None,
                fallback_unit_cost_minor_units=None, fallback_unit_cost_fraction=None, count_position='before',
                landed_on_movement_id=row['id']), line_index=len(lines)))
            sequence += 1
        target['landed_id'] = landed_id
    for i, row in enumerate(chosen):
        amount = sum(matrix[i])
        lines.append(JournalLineInput(account=account['id'], side='credit' if amount > 0 else 'debit',
                                      amount=_money(abs(amount), currency),
                                      description=f'Landed cost {number}: {_label(documents[row["transaction_id"]])}'
                                                  + (f', {row["description"]}' if row['description'] else '')))
    position = 0
    for i, row in enumerate(chosen):
        for j, target in enumerate(targets):
            if not matrix[i][j]:
                continue
            position += 1
            line_plan.append(dict(position=position, charge_transaction_id=row['transaction_id'],
                                  charge_line_id=row['charge'],
                                  target_movement_id=target['movement']['id'] if target['kind'] == 'receipt' else None,
                                  target_account_id=target['account']['id'] if target['kind'] == 'account' else None,
                                  item_id=target['item']['id'] if target['kind'] == 'receipt' else None,
                                  basis=inp.basis, measure=measures[j], amount_minor_units=matrix[i][j],
                                  currency=currency, landed_movement_id=target.get('landed_id'), reverses_line_id=None))
    if not line_plan:
        raise _invalid('targets', 'every share rounds to nothing; allocate more, or to fewer targets')
    allocation_id = new_id()
    memo = inp.memo or f'Landed cost {number}'
    header = dict(id=allocation_id, number=number, kind='allocation', status='posted', date=date, basis=inp.basis,
                  item_receipt_id=None, memo=inp.memo, reason=(ctx.reason or '').strip() or None,
                  voided_at=None, voided_by=None, voided_via=None, void_reason=None)

    def trailing(fresh, rows, provenance):
        if sequence_row is not None:
            from sqlalchemy.dialects.sqlite import insert
            stmt = insert(c.sequences).values(**sequence_row)
            s.company.conn.execute(stmt.on_conflict_do_update(index_elements=['name'], set_=sequence_row))
        made = dict(header, transaction_id=fresh.data['header']['id'], **provenance)
        lines_rows = [dict(line, id=new_id(), allocation_id=allocation_id, **provenance) for line in line_plan]
        return [('landed_cost_allocations', 'landed_cost_allocation', 'id', [made]),
                ('landed_cost_allocation_lines', 'landed_cost_allocation_line', 'id', lines_rows)]

    first_item = next(iter(items.values()), None) or {'id': '', 'full_name': ACCOUNT_NAME}
    document = inventory._Document(JournalPostInput(date=date, memo=memo, lines=lines, number=number), 'post', KIND, movements,
                                   trailing=trailing)
    change = inventory._Change([document], first_item, None, currency, items=items)
    change.allocation = dict(header, lines=line_plan, targets=targets, chosen=chosen, documents=documents)
    return change


def _complete(s, change):
    if not change.documents[0].movements:
        # Every share went to an account: nothing reaches stock, so nothing is replayed.
        journals.open_dates(s, [change.documents[0].journal.date])
        return change
    return inventory.complete(s, change)


def _summary(s, change, *, status=None):
    """The allocation as a person reads it: lines, each target split into stock and issued, and the true-ups."""
    alloc = change.allocation
    currency = change.currency
    documents = alloc['documents']
    names = {}
    by_landed = {}
    for document in change.documents[1:]:
        for movement in document.movements:
            values = movement.values
            if values['kind'] == 'recost' and values.get('filled_by_movement_id'):
                by_landed.setdefault(values['filled_by_movement_id'], []).append(values)
    true_ups = []
    for landed_id, rows in by_landed.items():
        for values in rows:
            names.setdefault(values['item_id'], inventory.item_name(s, values['item_id']))
            true_ups.append(TrueUpOutput(item_id=values['item_id'], item_name=names[values['item_id']],
                                         effective_date=values['effective_date'],
                                         corrects_movement_id=values['corrects_movement_id'],
                                         landed_movement_id=landed_id,
                                         delta=_money(values['value_minor_units'], currency),
                                         transaction_id=values.get('transaction_id', ''), number=values.get('number', '')))
    own = {m.values['id'] for m in change.documents[0].movements if m.values['kind'] == 'landed'}
    targets = []
    for target in alloc['targets']:
        amount = sum(line['amount_minor_units'] for line in alloc['lines']
                     if (line['target_movement_id'] and target['kind'] == 'receipt'
                         and line.get('current_target', line['target_movement_id']) == target['movement']['id'])
                     or (line['target_account_id'] and target['kind'] == 'account'
                         and line['target_account_id'] == target['account']['id']))
        issued = -sum(int(v['value_minor_units']) for v in by_landed.get(target.get('landed_id'), ())
                      if target.get('landed_id') in own) if target['kind'] == 'receipt' else amount
        if target['kind'] == 'receipt':
            targets.append(TargetShareOutput(
                target_document=_label(documents[target['transaction_id']]), target_movement_id=target['movement']['id'],
                item_name=target['item']['full_name'], account_name=None, amount=_money(amount, currency),
                to_stock=_money(amount - issued, currency), issued_before=_money(issued, currency)))
        else:
            targets.append(TargetShareOutput(
                target_document=None, target_movement_id=None, item_name=None,
                account_name=target['account']['full_name'], amount=_money(amount, currency),
                to_stock=_money(0, currency), issued_before=_money(0, currency)))
    charge_labels = {row['charge']: _label(documents[row['transaction_id']]) for row in alloc['chosen']}
    out_lines = []
    for line in alloc['lines']:
        target = next((t for t in alloc['targets'] if (t['kind'] == 'receipt'
                                                         and t['movement']['id'] == line.get('current_target', line['target_movement_id']))
                       or (t['kind'] == 'account' and t['account']['id'] == line['target_account_id'])), None)
        out_lines.append(AllocationLineOutput(
            position=line['position'], charge=line['charge_line_id'], charge_document=charge_labels.get(line['charge_line_id']),
            target_document=_label(documents[target['transaction_id']]) if target and target['kind'] == 'receipt' else None,
            target_movement_id=line.get('current_target', line['target_movement_id']), item_id=line['item_id'],
            target_transaction_id=target['transaction_id'] if target and target['kind'] == 'receipt' else None,
            target_quantity=format_quantity_micro_units(int(target['movement']['quantity_microunits']))
            if target and target['kind'] == 'receipt' else None,
            item_name=target['item']['full_name'] if target and target['kind'] == 'receipt' else None,
            account_id=line['target_account_id'],
            account_name=target['account']['full_name'] if target and target['kind'] == 'account' else None,
            basis=line['basis'], measure=_measure_text(line['basis'], line['measure'], currency),
            amount=_money(line['amount_minor_units'], currency), landed_movement_id=line['landed_movement_id'],
            reversed=bool(line.get('reversed'))))
    return AllocationSummary(
        id=alloc.get('id'), number=alloc['number'], kind=alloc['kind'], status=status or alloc['status'],
        date=alloc['date'], basis=alloc['basis'], memo=alloc.get('memo'), reason=alloc.get('reason'),
        total=_money(sum(line['amount_minor_units'] for line in alloc['lines']), currency), lines=out_lines,
        targets=targets, true_ups=true_ups, void_reason=alloc.get('void_reason'))


def _measure_text(basis, measure, currency):
    """A measure in words: an amount for value and amounts, else a quantity (weight and volume are quantity times
    a per-unit figure, both in micro-units, so they are shown to six places)."""
    if basis in ('value', 'amounts'):
        return Money(int(measure), currency).amount
    if basis in ('weight', 'volume'):
        return format_quantity_micro_units(round_ratio_half_even(int(measure), 1_000_000))
    return format_quantity_micro_units(int(measure))


def _run_post(s, ctx, inp, *, persist):
    change = _complete(s, _post_change(s, ctx, inp))
    primary, touched = inventory.persist_change(s, ctx, change, command_name='landed-cost post', persist=persist,
                                                nouns={'landed_cost': 'landed cost allocation'})
    output = LandedCostWriteOutput(**primary.model_dump(), allocation=_summary(s, change))
    if change.warnings:
        output.warnings = list(output.warnings) + [w for w in dict.fromkeys(change.warnings) if w not in output.warnings]
    return output, touched


def prepare_post(s, ctx, inp):
    output, _ = _run_post(s, ctx, inp, persist=False)
    return Plan(output, {'input': inp})


def apply_post(plan, ctx, s):
    output, touched = _run_post(s, ctx, plan.data['input'], persist=True)
    return Applied(output, touched, f'allocate landed cost {output.allocation.number}', audited=True)


# ---------------------------------------------------------------- reading one back


def resolve(s, selector):
    t = c.landed_cost_allocations
    key = selector.upper() if is_ulid(selector) else selector
    rows = [dict(r) for r in s.company.conn.execute(sa.select(t).where(sa.or_(t.c.id == key, sa.and_(
        t.c.number == selector, t.c.kind == 'allocation')))).mappings()]
    if not rows:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'landed_cost_allocation', 'selector': selector})
    return rows[0]


def _stored(s, header):
    """The stored allocation, shaped as a change's ``allocation`` so the one summary serves both."""
    currency = _home(s)
    t = c.landed_cost_allocation_lines
    rows = [dict(r) for r in s.company.conn.execute(sa.select(t).where(t.c.allocation_id == header['id']).order_by(
        t.c.position, t.c.reverses_line_id.is_not(None))).mappings()]
    reversed_ids = {r['reverses_line_id'] for r in rows if r['reverses_line_id']}
    originals = [dict(r, reversed=r['id'] in reversed_ids) for r in rows if r['reverses_line_id'] is None]
    moved = successors(s, {r['target_movement_id'] for r in originals})
    for r in originals:
        r['current_target'] = moved.get(r['target_movement_id'], r['target_movement_id'])
    receipt_ids = {r['current_target'] for r in originals if r['current_target']}
    movements = {} if not receipt_ids else {r['id']: dict(r) for r in s.company.conn.execute(
        sa.select(c.inventory_movements).where(c.inventory_movements.c.id.in_(list(receipt_ids)))).mappings()}
    targets, seen = [], set()
    for r in originals:
        key = r['current_target'] or r['target_account_id']
        if key in seen:
            continue
        seen.add(key)
        if r['current_target']:
            row = movements[r['current_target']]
            targets.append(dict(kind='receipt', movement=row, transaction_id=row['transaction_id'],
                                item=item_service.resolve_item(s.company, row['item_id']), landed_id=r['landed_movement_id']))
        else:
            targets.append(dict(kind='account', account=account_service.resolve_account(s.company, r['target_account_id'])))
    chosen = []
    for r in originals:
        if r['charge_line_id'] and r['charge_line_id'] not in {x['charge'] for x in chosen}:
            chosen.append(dict(charge=r['charge_line_id'], transaction_id=r['charge_transaction_id']))
    documents = _documents(s, {t['transaction_id'] for t in targets if t['kind'] == 'receipt'}
                           | {x['transaction_id'] for x in chosen})
    return dict(header, lines=originals, targets=targets, chosen=chosen, documents=documents), currency


def _stored_change(s, header):
    alloc, currency = _stored(s, header)
    change = inventory._Change([inventory._Document(None, 'post', KIND, [])], {'id': '', 'full_name': ''}, None, currency)
    change.allocation = alloc
    # The forward true-ups it owns: corrections filled by its landed movements, still standing.
    landed_ids = [t['landed_id'] for t in alloc['targets'] if t['kind'] == 'receipt' and t.get('landed_id')]
    if landed_ids:
        t = c.inventory_movements
        rows = [dict(r) for r in s.company.conn.execute(sa.select(t).where(
            t.c.filled_by_movement_id.in_(landed_ids), t.c.kind == 'recost')).mappings()]
        numbers = dict(s.company.conn.execute(sa.select(c.transactions.c.id, c.transactions.c.number).where(
            c.transactions.c.id.in_({r['transaction_id'] for r in rows}))).all()) if rows else {}
        for r in rows:
            r['number'] = numbers.get(r['transaction_id'], '')
        change.documents.append(inventory._Document(None, 'post', 'recost', [inventory._Movement(r) for r in rows]))
        change.documents[0].movements = [inventory._Movement({'id': i, 'kind': 'landed'}) for i in landed_ids]
    return change


def show(s, ctx, inp):
    header = resolve(s, inp.allocation)
    change = _stored_change(s, header)
    transaction = s.company.conn.execute(sa.select(c.transactions).where(
        c.transactions.c.id == header['transaction_id'])).mappings().first()
    requested = journals.revision(s, dict(transaction))
    return Plan(LandedCostOutput(**journals.summary(dict(transaction), requested),
                                 revision=journals.revision_output(s, requested),
                                 allocation=_summary(s, change)))


def query(s, ctx, inp):
    currency = _home(s)
    t = c.landed_cost_allocations
    q = sa.select(t)
    if inp.status:
        q = q.where(t.c.status == inp.status)
    if inp.kind:
        q = q.where(t.c.kind == inp.kind)
    if inp.date_from:
        q = q.where(t.c.date >= inp.date_from)
    if inp.date_to:
        q = q.where(t.c.date <= inp.date_to)
    if inp.document is not None:
        tid = _transaction_by_selector(s, inp.document, 'document', lambda tid: True)
        lines = c.landed_cost_allocation_lines
        q = q.where(t.c.id.in_(sa.select(lines.c.allocation_id).join(
            c.inventory_movements, c.inventory_movements.c.id == lines.c.target_movement_id, isouter=True).where(
            sa.or_(lines.c.charge_transaction_id == tid, c.inventory_movements.c.transaction_id == tid))))
    q = q.order_by(t.c.date.desc(), t.c.created_at.desc()).limit(inp.limit)
    headers = [dict(r) for r in s.company.conn.execute(q).mappings()]
    rows = []
    for header in headers:
        lines = [dict(r) for r in s.company.conn.execute(sa.select(c.landed_cost_allocation_lines).where(
            c.landed_cost_allocation_lines.c.allocation_id == header['id'],
            c.landed_cost_allocation_lines.c.reverses_line_id.is_(None))).mappings()]
        rows.append(LandedCostListRow(id=header['id'], number=header['number'], kind=header['kind'],
                                      status=header['status'], date=header['date'], basis=header['basis'],
                                      total=_money(sum(int(r['amount_minor_units']) for r in lines), currency),
                                      targets=len({r['target_movement_id'] or r['target_account_id'] for r in lines}),
                                      memo=header['memo']))
    return Plan(LandedCostListOutput(rows=rows, count=len(rows)))


# ---------------------------------------------------------------- voiding


def _void_change(s, ctx, inp):
    header = resolve(s, inp.allocation)
    if header['kind'] != 'allocation':
        raise _invalid('allocation', "an item receipt's own shipping changes with the receipt, not here")
    change = _stored_change(s, header)
    alloc = change.allocation
    change.documents = change.documents[:1]
    if header['status'] == 'voided':
        change.documents = []
        return change, header
    currency = change.currency
    # A charge's credit allocated after this one may need this one to stand.
    receipt_ids = [t['movement']['id'] for t in alloc['targets'] if t['kind'] == 'receipt']
    totals = _landed_totals(s, receipt_ids)
    for line in alloc['lines']:
        if line['target_movement_id']:
            totals[line['current_target']] -= int(line['amount_minor_units'])
    for target in alloc['targets']:
        if target['kind'] == 'receipt' and totals.get(target['movement']['id'], 0) < 0:
            raise BookflowError('E_WORK_DEPENDENCY', message=(
                f'Voiding {header["number"]} would leave the landed cost of {target["item"]["full_name"]} on '
                f'{_label(alloc["documents"][target["transaction_id"]])} below zero: a freight credit allocated to it '
                'needs this charge. Void that credit\'s allocation first.'), details={'allocation': header['number']})
    sequence = inventory.next_sequence(s)
    landed = [dict(r) for r in s.company.conn.execute(sa.select(c.inventory_movements).where(
        c.inventory_movements.c.transaction_id == header['transaction_id'],
        c.inventory_movements.c.kind == 'landed')).mappings()]
    reversals, items = [], {}
    for row in sorted(landed, key=lambda r: int(r['sequence'])):
        values = inventory.reversal_values(row, sequence)
        values.update(transaction_id=row['transaction_id'], landed_on_movement_id=None,
                      fallback_unit_cost_fraction=None)
        reversals.append(inventory._Movement(values, line_index=0, reverses_line_id=row['posting_line_id']))
        items.setdefault(row['item_id'], item_service.resolve_item(s.company, row['item_id']))
        sequence += 1
    reason = (ctx.reason or '').strip() or None

    def trailing(fresh, rows, provenance):
        out = []
        for line in alloc['lines']:
            if line.get('reversed'):
                continue
            out.append(dict({k: line[k] for k in (
                'position', 'charge_transaction_id', 'charge_line_id', 'target_movement_id', 'target_account_id',
                'item_id', 'basis', 'measure', 'currency', 'landed_movement_id')},
                id=new_id(), allocation_id=header['id'], amount_minor_units=-int(line['amount_minor_units']),
                reverses_line_id=line['id'], **provenance))
        return [('landed_cost_allocation_lines', 'landed_cost_allocation_line', 'id', out)]

    document = inventory._Document(JournalVoidInput(journal=header['transaction_id']), 'void', KIND, reversals,
                                   journal_date=header['date'], trailing=trailing, reason=reason)
    change.documents = [document]
    change.items = items
    change.item = next(iter(items.values()), {'id': '', 'full_name': ACCOUNT_NAME})
    return change, header


def _run_void(s, ctx, inp, *, persist):
    change, header = _void_change(s, ctx, inp)
    if not change.documents:
        transaction = s.company.conn.execute(sa.select(c.transactions).where(
            c.transactions.c.id == header['transaction_id'])).mappings().first()
        fresh = journals.prepare(s, ctx, JournalVoidInput(journal=header['transaction_id']), 'void', owner=inventory.OWNER)
        return LandedCostWriteOutput(**fresh.preview.model_dump(), allocation=_summary(s, _stored_change(s, header))), [], header
    if change.documents[0].movements:
        inventory.complete(s, change)
    else:
        journals.open_dates(s, [header['date']])
    primary, touched = inventory.persist_change(s, ctx, change, command_name='landed-cost void', persist=persist,
                                                nouns={'landed_cost': 'landed cost allocation'})
    for document in change.documents[1:]:
        document.kind = 'recost'
    change.documents = [inventory._Document(None, 'post', KIND, [])] + change.documents[1:]
    output = LandedCostWriteOutput(**primary.model_dump(), allocation=_summary(s, change, status='voided'))
    if change.warnings:
        output.warnings = list(output.warnings) + [w for w in dict.fromkeys(change.warnings) if w not in output.warnings]
    return output, touched, header


def prepare_void(s, ctx, inp):
    output, _, _ = _run_void(s, ctx, inp, persist=False)
    return Plan(output, {'input': inp})


def apply_void(plan, ctx, s):
    output, touched, header = _run_void(s, ctx, plan.data['input'], persist=True)
    if header['status'] == 'voided':
        return Applied(output, [], 'no change')
    at = clock.now_iso()
    after = dict(header, status='voided', voided_at=at, voided_by=s.actor.id, voided_via=ctx.interface.value,
                 void_reason=(ctx.reason or '').strip() or None)
    record = [Touched('landed_cost_allocation', header['id'], 'update', 1, 2, after, header, db='company')]
    audit.write_event_to(s.company, ctx, 'landed-cost void', f'void landed cost {header["number"]}', record,
                         actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None))
    s.company.conn.execute(c.landed_cost_allocations.update().where(
        c.landed_cost_allocations.c.id == header['id']).values(
        status='voided', voided_at=at, voided_by=s.actor.id, voided_via=ctx.interface.value,
        void_reason=after['void_reason']))
    return Applied(output, touched + record, f'void landed cost {header["number"]}', audited=True)


# ---------------------------------------------------------------- guards


def check_targets(s, reversing, change=None, *, places_known=True):
    """The receipts a live allocation lands on that this change retires: each must come back with its stock
    unchanged -- the same entered line, item, quantity, cost, date, position, accounts and places (site, bin,
    lot, serial numbers) -- and then carries its landed cost on (a memo or supplier reference edit reposts a
    bill); any other change of one is refused, as is a void. Returns ``[(retired id, new id)]`` to record.

    With ``places_known`` false (before the change's placements are worked out, so replay can read each landed
    cost on its restatement) it compares everything but the places; the places are compared once known."""
    ids = [row['id'] for row in reversing if row['kind'] == 'receipt' and row.get('returns_movement_id') is None]
    if not ids or not sa.inspect(s.company.conn).has_table('landed_cost_allocation_lines'):
        return []
    named = [line for line in live_lines(s, target_ids=ids) if line['kind'] == 'allocation']
    if not named:
        return []
    from bookflow.company import placements
    by_id = {row['id']: row for row in reversing}
    targets = sorted({line['current_target'] for line in named})
    old_lines = dict(s.company.conn.execute(sa.select(c.document_lines.c.id, c.document_lines.c.line_id).where(
        c.document_lines.c.id.in_([by_id[i]['document_line_id'] for i in targets]))).all())
    old_places = placements.movement_placements(s, targets)
    new_places: dict[str, list] = {}
    for row in (getattr(change, 'placements', None) or ()):
        new_places.setdefault(row['movement_id'], []).append(row)

    def places(rows):
        return sorted((p['site_id'], p.get('bin_id'), p.get('lot_id'), p.get('serial_number_id'),
                       int(p['quantity_microunits'])) for p in rows if not p.get('reverses_placement_id'))
    carried, refused = [], []
    for old_id in targets:
        old = by_id[old_id]
        identity = old_lines.get(old['document_line_id'])
        new = next((m.values for m in (getattr(change, 'movements', None) or ())
                    if m.values['kind'] == 'receipt' and m.values.get('returns_movement_id') is None
                    and identity and getattr(change, 'identities', {}).get(m.values['id']) == identity), None)
        same = new is not None and all(new.get(key) == old.get(key) for key in (
            'item_id', 'quantity_microunits', 'value_minor_units', 'effective_date', 'asset_account_id',
            'offset_account_id', 'class_id', 'currency')) and (new.get('count_position') or 'before') == (
            old.get('count_position') or 'before') and (not places_known or places(new_places.get(new['id'], ()))
                                                        == places(old_places.get(old_id, ())))
        (carried if same else refused).append((old_id, new['id'] if new else None))
    if refused:
        numbers = sorted({line['number'] for line in named if line['current_target'] in {o for o, _ in refused}})
        raise BookflowError('E_WORK_DEPENDENCY', message=(
            f'Landed cost {", ".join(numbers)} {"is" if len(numbers) == 1 else "are"} allocated to this receipt, so its '
            'stock (lines, quantities, costs, site or bin, date) cannot be changed and it cannot be voided; a memo or '
            f'reference may change. Void the allocation first (`landed-cost void {numbers[0]}`), then allocate the '
            'freight again afterwards.'), details={'allocations': numbers, 'reason': 'landed_cost_target'})
    return carried


def watermark(s):
    """Where the company's posting lines stand before a command runs; None with no clearing account."""
    if s.company is None or find_account(s.company) is None:
        return None
    return int(s.company.conn.execute(sa.select(sa.func.coalesce(sa.func.max(sa.text('posting_lines.rowid')), 0))
                                      .select_from(c.posting_lines)).scalar_one())


def check_charges(s, mark):
    """After a command: refuse when it reduced or voided a charge below what live allocations took of it."""
    if mark is None or s.company is None:  # a command that closed or replaced the company (rename, upgrade)
        return
    account = find_account(s.company)
    touched = set(s.company.conn.execute(sa.select(c.posting_lines.c.transaction_id).where(
        sa.text('posting_lines.rowid > :mark').bindparams(mark=mark),
        c.posting_lines.c.account_id == account['id'])).scalars())
    if not touched:
        return
    for row in charges(s, account['id'], transaction_ids=touched).values():
        if not row['allocated']:
            continue
        if (row['allocated'] > 0 and row['amount'] < row['allocated']) or (
                row['allocated'] < 0 and row['amount'] > row['allocated']):
            numbers = sorted({line['number'] for line in live_lines(s, charge_ids=[row['charge']])})
            raise BookflowError('E_WORK_DEPENDENCY', message=(
                f'Landed cost {", ".join(numbers)} {"has" if len(numbers) == 1 else "have"} allocated '
                f'{Money(abs(row["allocated"]), _home(s))} of this '
                f'freight charge, so it cannot be voided or reduced below that. Void the allocation first '
                f'(`landed-cost void {numbers[0]}`).'), details={'allocations': numbers, 'charge': row['charge'],
                                                                  'reason': 'landed_cost_charge'})


# ---------------------------------------------------------------- an item receipt's own shipping


def receipt_shipping_shares(shipping, items, basis, field='shipping_basis'):
    """An item receipt's shipping over its lines: (shares, measures). Quantity is today's rule exactly."""
    if basis == 'quantity':
        measures = [int(item['profile'].quantity_microunits) for item in items]
    elif basis == 'value':
        measures = [max(int(item['amount_minor_units']), 0) for item in items]
    else:
        measures = []
        for item in items:
            per = item.get(f'unit_{basis}')
            if not per:
                raise _invalid(field, f'"{item["profile"].item.label}" has no unit {basis}; set it on the item or '
                                      'spread shipping by quantity')
            measures.append(int(item['profile'].quantity_microunits) * int(per))
    if not shipping:
        return [0] * len(items), measures
    if not sum(measures):
        raise _invalid(field, f'the lines weigh nothing by {basis}; spread shipping by quantity')
    return share(shipping, measures), measures


def receipt_shipping_rows(s, *, receipt_id, number, transaction_id, date, basis, lines, provenance, currency):
    """The allocation records for a receipt's own shipping: a header and one line per receipt line with a share."""
    shared = [line for line in lines if line['shipping']]
    if not shared:
        return [], []
    allocation_id = new_id()
    header = dict(id=allocation_id, number=number, kind='receipt_shipping', status='posted', date=date, basis=basis,
                  transaction_id=transaction_id, item_receipt_id=receipt_id, memo=None, reason=None,
                  voided_at=None, voided_by=None, voided_via=None, void_reason=None, **provenance)
    rows = [dict(id=new_id(), allocation_id=allocation_id, position=position, charge_transaction_id=None,
                 charge_line_id=None, target_movement_id=line['movement_id'], target_account_id=None,
                 item_id=line['item_id'], basis=basis, measure=line['measure'], amount_minor_units=line['shipping'],
                 currency=currency, landed_movement_id=None, reverses_line_id=None, **provenance)
            for position, line in enumerate(shared, 1)]
    return [header], rows


def plan_void_receipt_shipping(s, receipt_id, provenance):
    """Retiring a receipt's standing shipping allocation when the receipt is changed or voided: ``(work, touched)``,
    where ``work`` is what ``write_receipt_shipping`` writes once the audit event exists."""
    t = c.landed_cost_allocations
    headers = [dict(r) for r in s.company.conn.execute(sa.select(t).where(
        t.c.item_receipt_id == receipt_id, t.c.status == 'posted')).mappings()]
    work, touched = [], []
    for header in headers:
        rows = [dict({k: line[k] for k in (
            'position', 'charge_transaction_id', 'charge_line_id', 'target_movement_id', 'target_account_id',
            'item_id', 'basis', 'measure', 'currency', 'landed_movement_id')},
            id=new_id(), allocation_id=header['id'], amount_minor_units=-int(line['amount_minor_units']),
            reverses_line_id=line['id'], **provenance) for line in live_lines(s, allocation_ids=[header['id']])]
        void = dict(status='voided', voided_at=provenance['created_at'], voided_by=provenance['created_by'],
                    voided_via=provenance['created_via'], void_reason='item receipt changed')
        work.append((header['id'], void, rows))
        touched.append(Touched('landed_cost_allocation', header['id'], 'update', 1, 2, dict(header, **void), header,
                               db='company'))
        touched.extend(Touched('landed_cost_allocation_line', row['id'], 'create', None, 1, row, db='company')
                       for row in rows)
    return work, touched


def write_receipt_shipping(s, retire, made):
    """Write what ``plan_void_receipt_shipping`` and ``receipt_shipping_rows`` planned: reversing lines and the
    old status first, then the new allocation and its lines."""
    t = c.landed_cost_allocations
    for allocation_id, void, rows in retire:
        if rows:
            s.company.conn.execute(c.landed_cost_allocation_lines.insert(), rows)
        s.company.conn.execute(t.update().where(t.c.id == allocation_id).values(**void))
    headers, lines = made
    if headers:
        s.company.conn.execute(t.insert(), headers)
        s.company.conn.execute(c.landed_cost_allocation_lines.insert(), lines)


def landed_by_movement(s, movement_ids):
    """``{receipt movement id: landed total}`` from live allocation lines, receipt shipping included."""
    if not movement_ids:
        return {}
    return _landed_totals(s, list(movement_ids))
