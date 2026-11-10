"""Freeze native bill inputs and compare native effects without reserving any source."""
from copy import deepcopy
import json

from bookflow.company import bills, purchase_order_billing, purchase_orders
from bookflow.company.bill_models import BillPostInput, BillUpdateInput
from bookflow.core import unit_price
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units


def _money(value, currency):
    return {'minor_units': value, 'currency': currency}


def _reference(value):
    return value['id'] if value else None


def _line(value, currency, *, item=False):
    facts = value['line_snapshot']
    out = dict(customer=_reference(facts.get('customer')), billable=facts['billable'],
               class_id=_reference(facts.get('class_id')),
               class_mode='value' if facts.get('class_id') else 'none')
    if item:
        out.update(item=facts['item']['id'], description=value['description'],
                   quantity=format_quantity_micro_units(facts['quantity_microunits']))
        rate = unit_price.join(facts.get('unit_cost_minor_units'), facts.get('unit_cost_fraction'))
        if rate is not None:
            out['unit_cost'] = unit_price.to_dict(rate, currency)
        else:
            out['amount'] = _money(value['amount_minor_units'], currency)
    else:
        out.update(account=facts['account']['id'], memo=value['description'],
                   amount=_money(value['amount_minor_units'], currency))
    return out


def _stock(change):
    """Capture amounts and dependency identities, excluding newly allocated movement ids."""
    if change is None:
        return None
    fields = ('item_id', 'kind', 'effective_date', 'quantity_microunits', 'value_minor_units',
              'asset_account_id', 'offset_account_id', 'class_id', 'currency',
              'reverses_movement_id', 'corrects_movement_id', 'returns_movement_id', 'count_position')
    def movement(value):
        return {key: value.values.get(key) for key in fields}
    def line(value):
        return value.model_dump(mode='json') if hasattr(value, 'model_dump') else dict(value)
    return dict(movements=[movement(m) for m in change.movements],
                corrections=[dict(date=c.date, lines=[line(x) for x in c.lines],
                                  movements=[movement(m) for m in c.movements]) for c in change.corrections])


def calculate(s, ctx, payload, operation='create', original_id=None, original_version=None):
    """Return normalized native input, deterministic effects snapshot, and unpersisted Plan.

    Correct proposals keep omitted grids as native captured survivors. Source selections
    never reserve capacity. A second native preparation proves the normalized input itself,
    so the captured preview is exactly what a later commit revalidates.
    """
    if operation == 'restore':
        raise BookflowError('E_APPROVAL_STATE', message='Native bill restoration is not available.',
                            details={'operation': operation, 'allowed_actions': ['create', 'correct']})
    if operation not in ('create', 'correct'):
        raise BookflowError('E_VALIDATION', message='Unknown bill draft operation.')
    raw = deepcopy(payload)
    if operation == 'correct':
        if not original_id or original_version is None:
            raise BookflowError('E_VALIDATION', message='Correction requires the original bill and exact version.')
        raw.update(bill=original_id, expected_version=original_version)
    model = BillPostInput if operation == 'create' else BillUpdateInput
    native_operation = 'post' if operation == 'create' else 'update'
    inp = model.model_validate(raw)
    # A partial PO selection's omitted quantity means all remaining *now*, not at commit.
    if operation == 'create':
        inp = purchase_order_billing.remaining_selection(s, inp)
    raw = inp.model_dump(mode='json', exclude_unset=True)
    if getattr(inp, 'order_lines', None) is not None:
        partial, entry = purchase_order_billing.source(s, inp)
        raw['purchase_order'] = partial['header']['id']
        ordered = {x['line_id']: x for x in purchase_orders.saved_lines(s, partial['revision'])}
        frozen = []
        for claim, supplied in zip(partial['claims'], inp.order_lines, strict=True):
            selection = {'line': claim['order_line_id']}
            if claim['quantity_microunits'] is not None:
                rate = (unit_price.parse(supplied.unit_cost, partial['revision']['currency'])
                        if supplied.unit_cost is not None else unit_price.row_units(ordered[claim['order_line_id']], 'rate'))
                selection.update(quantity=format_quantity_micro_units(claim['quantity_microunits']),
                                 unit_cost=unit_price.to_dict(rate, partial['revision']['currency']))
            else:
                selection['amount'] = _money(claim['amount_minor_units'], partial['revision']['currency'])
            frozen.append(selection)
        raw['order_lines'] = frozen
        inp = model.model_validate(raw)
    first = bills._prepare_native(s, ctx, inp, native_operation)
    view = first.preview.revision.model_dump(mode='json')
    profile, currency = view['profile'], view['currency']
    raw.update(date=view['date'], vendor=profile['vendor']['id'], ap_account=profile['ap_account']['id'],
               terms=_reference(profile.get('terms')), due_date=profile['due_date'],
               supplier_reference=profile['supplier_reference'], class_id=_reference(profile.get('class_id')),
               memo=view['memo'])
    if getattr(inp, 'purchase_order', None):
        raw['purchase_order'] = purchase_orders.resolve(s, inp.purchase_order)['id']
    # Receipt rows carry native product-only amounts; shipping remains source-owned.
    received = first.data.get('received')
    if received and ('receipts' in raw or operation == 'create'):
        raw['receipts'] = [dict(receipt_line=x['line']['id'], expected_receipt_version=x['header']['version'],
            quantity=format_quantity_micro_units(x['quantity']),
            **({'unit_cost': unit_price.to_dict(x['rate'], currency)} if x['rate'] is not None
               else {'amount': _money(x['product_amount'], currency)})) for x in received['sources']]
    if not getattr(inp, 'order_lines', None):
        semantic = first.data.get('semantic') or bills._saved_semantic(s, first.data.get('old_revision') or
            bills.journals.revision(s, bills.resolve(s, original_id)))
        item_semantics = [x for x in semantic['lines'] if x['family'] == 'item']
        for grid in ('expenses', 'items'):
            if grid == 'items' and received:
                continue
            if operation == 'correct' and grid not in inp.model_fields_set:
                continue
            normalized = []
            originals = raw.get(grid) or []
            for index, value in enumerate(view[grid]):
                line = _line(value, currency, item=grid == 'items')
                if operation == 'correct':
                    # New lines have only preview-generated identities and must remain new.
                    if index < len(originals) and originals[index].get('line_id'):
                        line['line_id'] = value['line_id']
                if grid == 'items':
                    original = originals[index] if index < len(originals) else {}
                    for key in ('site', 'bin', 'lot', 'lot_expiry', 'serial_numbers'):
                        if key in original:
                            line[key] = original[key]
                    facts = item_semantics[index]
                    if facts['profile']['item_type'] in bills.inventory.TRACKED_TYPES:
                        from bookflow.company import placements
                        line['site'] = facts.get('site_id') or placements.default_site_id(s)
                    if (facts.get('numbers') or {}).get('bin'):
                        line['bin'] = facts['numbers']['bin']
                normalized.append(line)
            raw[grid] = normalized
    # Keep automatic numbering automatic: a draft cannot reserve the next document number.
    if inp.number is None:
        raw.pop('number', None)
    normalized = model.model_validate(raw).model_dump(mode='json', exclude_unset=True)
    plan = bills._prepare_native(s, ctx, model.model_validate(normalized), native_operation)
    semantic = deepcopy(plan.data.get('semantic'))
    if semantic is None:
        header = bills.resolve(s, original_id)
        semantic = bills._saved_semantic(s, bills.journals.revision(s, header))
    if inp.number is None:
        semantic.pop('number', None)
    sources = []
    if normalized.get('purchase_order'):
        header = purchase_orders.resolve(s, normalized['purchase_order'])
        revision = purchase_orders.revision(s, header)
        sources.append(dict(kind='purchase_order', id=header['id'], version=header['version'], revision=revision['id'],
                            selections=normalized.get('order_lines'),
                            lines=[{key: line.get(key) for key in ('line_id', 'quantity_microunits',
                                'amount_minor_units', 'rate_minor_units', 'rate_fraction')}
                                for line in purchase_orders.saved_lines(s, revision)]))
    received = plan.data.get('received')
    if received:
        sources.extend(dict(kind='item_receipt', id=x['header']['id'], version=x['header']['version'],
                            line=x['line']['id'], spans=x['spans'], quantity=x['quantity'],
                            product_amount=x['product_amount'], shipping=x['shipping']) for x in received['sources'])
    pending = plan.data.get('pending') or {}
    batches = {x['id']: x for x in pending.get('posting_batches', [])}
    ledger = []
    for leg in pending.get('posting_lines', []):
        batch = batches[leg['batch_id']]
        ledger.append(dict(date=batch['effective_date'], kind=batch['kind'],
            **{key: leg.get(key) for key in ('account_id', 'debit_minor_units', 'credit_minor_units',
                'currency', 'name_type', 'name_id', 'class_id', 'description')}))
    snapshot = dict(commercial=semantic, sources=sources, ledger=ledger,
                    stock=_stock(plan.data.get('stock')),
                    receipt_stock=_stock(received.get('stock')) if received else None,
                    total=plan.preview.total.model_dump(mode='json'))
    return normalized, json.loads(json.dumps(snapshot)), plan


def prior_payload(s, header):
    """Capture the current bill as a complete native correction input for an inverse.

    This is commercial input, not a bypass: native correction still rejects retired line
    identities, changed source capacity, inactive references and closed dates. The caller
    must compare the inverse's native semantic/effects with its saved prior evidence.
    """
    from bookflow.company import custom_fields, placements, serial_lots
    revision = bills.journals.revision(s, header)
    view = bills.revision_output(s, header, revision).model_dump(mode='json')
    profile, currency = view['profile'], view['currency']
    payload = dict(bill=header['id'], expected_version=header['version'], date=view['date'],
                   number=view['number'], vendor=profile['vendor']['id'],
                   ap_account=profile['ap_account']['id'], terms=_reference(profile.get('terms')),
                   supplier_reference=profile['supplier_reference'], memo=view['memo'],
                   class_id=_reference(profile.get('class_id')))
    # Preserve the captured terms/date basis where possible; an entered due date is explicit.
    if profile['due_date_basis'] == 'entered':
        payload['due_date'] = profile['due_date']
    else:
        payload['due_date'] = None
    places = placements.revision_line_places(s, revision['id'])
    numbers = serial_lots.revision_display(s, revision['id'])
    for grid in ('expenses', 'items'):
        payload[grid] = []
        for value in view[grid]:
            line = _line(value, currency, item=grid == 'items')
            line['line_id'] = value['line_id']
            if grid == 'items':
                place = places.get(value['line_id'])
                if place:
                    line['site'], line['bin'] = place
                line.update(numbers.get(value['line_id']) or {})
            payload[grid].append(line)
    if view['receipts']:
        payload.pop('items')
        payload['receipts'] = [{key: value[key] for key in ('receipt_line', 'expected_receipt_version',
            'quantity', 'unit_cost', 'amount') if key in value} for value in view['receipts']]
    captured = view['custom_fields_snapshot']
    # Include unfilled definitions as explicit clears, so a correction which fills one
    # cannot leave that new value behind when its prior commercial state is restored.
    payload['custom_fields'] = {value['id']: None for value in
        custom_fields._applicable_definitions(s.company.conn, 'bill')}
    payload['custom_fields'].update({key: value['value'] for key, value in captured.items()})
    payload['custom_field_kinds'] = {key: value['kind'] for key, value in captured.items()}
    own = bills.inventory_effects.own_movements(s, header['id'])
    positions = {value.get('count_position') or 'before' for value in own}
    if len(positions) > 1:
        raise BookflowError('E_APPROVAL_STATE', message='A bill with mixed count positions cannot be captured for correction reversal.')
    if positions:
        payload['count_position'] = positions.pop()
    return BillUpdateInput.model_validate(payload).model_dump(mode='json', exclude_unset=True)
