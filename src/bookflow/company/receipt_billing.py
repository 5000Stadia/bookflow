"""Match bills to received intervals; transfer AP without receiving goods twice."""
import json
import sqlalchemy as sa
from bookflow.company import bills, document_effects as effects, inventory_effects, journals, receiving, schema as c
from bookflow.company.bill_facts import BillItemProfile
from bookflow.company.bill_models import ReceiptBillingInput
from bookflow.company.journal_models import JournalVoidInput
from bookflow.company.receiving_intervals import allocate, cumulative
from bookflow.company.sales_models import money
from bookflow.core import audit
from bookflow.core.errors import BookflowError
from bookflow.core.exact import parse_quantity_micro_units, format_quantity_micro_units
from bookflow.core.ids import new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Touched


def source(s, inp, operation, old_header, old_revision):
    prior = receiving.active_bill_claims(s, bill_id=old_header['id']) if old_header else []
    selections = getattr(inp, 'receipts', None)
    if selections is None and not prior:
        return None, inp
    if getattr(inp, 'purchase_order', None) or getattr(inp, 'items', None) is not None:
        raise journals.invalid('receipts', 'A linked bill selects received lines; it cannot also receive new items or consume a whole order.')
    if operation == 'void':
        return dict(prior=prior, selections=[], items=[], sources=[]), inp
    kept = {}
    if selections is None:
        for claim in prior:
            kept.setdefault(claim['bill_line_id'], []).append(claim)
        prior_lines = {line['id']: line for line in bills.saved_lines(s, old_revision)}
        selections = []
        for line_id, claims in kept.items():
            line = prior_lines[line_id]
            receipt_line = effects.rows(s, c.item_receipt_lines, c.item_receipt_lines.c.id == claims[0]['receipt_line_id'])[0]
            header = receiving.resolve(s, receipt_line['receipt_id'])
            selections.append(ReceiptBillingInput(receipt_line=receipt_line['id'], expected_receipt_version=header['version'],
                quantity=format_quantity_micro_units(sum(claim['end_microunits'] - claim['start_microunits'] for claim in claims)),
                amount={'minor_units': line['amount_minor_units'], 'currency': old_revision['currency']}))
    items, sources, seen = [], [], set()
    vendor = ap = currency = None
    for index, selection in enumerate(selections):
        if selection.receipt_line in seen:
            raise journals.invalid('receipts', 'Select each received line once; its quantity may span disjoint available intervals.')
        seen.add(selection.receipt_line)
        found = effects.rows(s, c.item_receipt_lines, c.item_receipt_lines.c.id == selection.receipt_line)
        if not found:
            raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'item_receipt_line', 'selector': selection.receipt_line})
        line = found[0]
        header = receiving.resolve(s, line['receipt_id'])
        rev = receiving.revision(s, header)
        if header['version'] != selection.expected_receipt_version:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_id': header['id'], 'current_version': header['version']})
        if header['status'] != 'posted' or rev['financial_revision_id'] != line['financial_revision_id']:
            receiving._dependency('This receipt line is voided or superseded; select current received goods.', receipt_id=header['id'])
        captured = json.loads(rev['snapshot'])['profile']
        if vendor is None:
            vendor, ap, currency = rev['vendor_id'], rev['ap_account_id'], captured['currency']
        if (vendor, ap, currency) != (rev['vendor_id'], rev['ap_account_id'], captured['currency']):
            raise journals.invalid('receipts', 'Selected receipts must have the same vendor, payable account and currency.')
        qty = parse_quantity_micro_units(selection.quantity, field='receipts.quantity')
        occupied = [(claim['start_microunits'], claim['end_microunits']) for claim in receiving.active_bill_claims(s, line_ids=[line['id']])
                    if not old_header or claim['bill_id'] != old_header['id']]
        try:
            spans = allocate(line['quantity_microunits'], line['value_minor_units'], occupied, qty)
        except ValueError as exc:
            receiving._dependency(str(exc), receipt_line_id=line['id'])
        # Omitted selection preserves the exact original intervals, including a middle
        # rounding penny, rather than reallocating through holes another bill released.
        if kept:
            old_spans = list(kept.values())[index]
            spans = [(x['start_microunits'], x['end_microunits'], x['original_minor_units']) for x in old_spans]
        original = sum(span[2] for span in spans)
        if selection.unit_cost is not None:
            rate = money(selection.unit_cost, currency).minor_units
            amount = bills.extension(qty, rate)
        elif selection.amount is not None:
            rate = None
            amount = money(selection.amount, currency).minor_units
        else:
            rate, amount = None, original
        view = json.loads(line['snapshot'])
        facts = BillItemProfile.model_validate(view['profile']).model_copy(update={
            'quantity_microunits': qty, 'unit_cost_minor_units': rate,
            'amount_basis': 'unit_cost' if rate is not None else 'amount'})
        stable_id = prior_lines[list(kept)[index]]['line_id'] if kept else None
        items.append(dict(line_id=stable_id, family='item', amount_minor_units=amount,
            memo=view['description'], profile=facts))
        sources.append(dict(line=line, header=header, spans=spans, amount=amount, quantity=qty,
                            original=original, vendor_id=vendor))
    if getattr(inp, 'vendor', None) and bills.resolve_party(s.company, 'vendor', inp.vendor)['id'] != vendor:
        raise journals.invalid('vendor', 'must match selected receipts')
    if getattr(inp, 'ap_account', None) and bills._account_row(s, inp.ap_account, 'ap_account')['id'] != ap:
        raise journals.invalid('ap_account', 'must match selected receipts')
    return dict(prior=prior, selections=selections, items=items, sources=sources), inp.model_copy(update={'vendor': vendor, 'ap_account': ap})


def attach(s, ctx, plan, received):
    data = plan.data
    pending, header = data['pending'], data['header']
    revision = pending['transaction_revisions'][0] if pending['transaction_revisions'] else data['old_revision']
    provenance = dict(created_at=header['updated_at'], created_by=s.actor.id,
                      created_via=ctx.interface.value, audit_event_id=data['event'])
    claims = []
    items = pending['purchase_item_lines']
    repricing = []
    for source, item in zip(received['sources'], items):
        progress = 0
        for start, end, original in source['spans']:
            billed = cumulative(source['quantity'], source['amount'], progress + end - start) - cumulative(source['quantity'], source['amount'], progress)
            progress += end - start
            claims.append(dict(id=new_id(), receipt_line_id=source['line']['id'], bill_id=header['id'],
                bill_revision_id=revision['id'], bill_line_id=item['document_line_id'], start_microunits=start,
                end_microunits=end, original_minor_units=original, billed_minor_units=billed, **provenance))
        target = effects.rows(s, c.inventory_movements, c.inventory_movements.c.id == source['line']['movement_id'])[0]
        repricing.append((target, source['amount'] - source['original'], source['vendor_id']))
    # Price corrections from the superseded bill revision have exact inverses. All
    # inverses and replacements enter one replay, avoiding transient negative stock.
    old_adjustments = []
    if data['before']:
        for row in effects.rows(s, c.receipt_bill_adjustments, c.receipt_bill_adjustments.c.bill_id == header['id']):
            movement = effects.rows(s, c.inventory_movements, c.inventory_movements.c.id == row['movement_id'])[0]
            if not effects.rows(s, c.inventory_movements, c.inventory_movements.c.reverses_movement_id == movement['id']):
                old_adjustments.append(movement)
    # Header corrections and unchanged prices do not rewrite historical costs.
    # Retain the existing bill-owned delta and its identity when its target and
    # exact amount still stand. This keeps closed, unaffected dates out of gating.
    desired = {target['id']: delta for target, delta, _ in repricing}
    existing = {}
    for movement in old_adjustments:
        key = movement['corrects_movement_id']
        existing[key] = existing.get(key, 0) + movement['value_minor_units']
    retained = {key for key, value in existing.items() if desired.get(key) == value}
    old_adjustments = [m for m in old_adjustments if m['corrects_movement_id'] not in retained]
    repricing = [(target, delta, vendor) for target, delta, vendor in repricing if target['id'] not in retained]
    inverses = []
    for transaction_id in dict.fromkeys(m['transaction_id'] for m in old_adjustments):
        inverses.append(journals.prepare(s, ctx, JournalVoidInput(journal=transaction_id), 'void', owner='inventory'))
    stock = inventory_effects.plan(s, entries=[], reversing=old_adjustments,
        currency=revision['currency'], field='receipts', repricing=repricing)
    inventory_effects.open_dates(s, stock)
    inventory_effects.bind_reversals(stock,
        [line for inverse in inverses for line in inverse.data['pending']['posting_lines']],
        [batch for inverse in inverses for batch in inverse.data['pending']['posting_batches']])
    inventory_effects.check(s, stock, [line for inverse in inverses for line in inverse.data['pending']['posting_lines']])
    plan.preview.revision.receipts = list(received['selections'])
    received.update(claims=claims, releases=[dict(id=new_id(), claim_id=claim['id'], **provenance) for claim in received['prior']],
        stock=stock, inverses=inverses, provenance=provenance)


def validate(s, data, indexed, require):
    received = data['received']
    pending = data['pending']
    require(not data['stock'].movements, 'a linked bill must not receive physical stock')
    require(not any(leg['account_id'] in {row['id'] for row in effects.rows(s, c.accounts, c.accounts.c.system_role == 'inventory_asset')}
                    for leg in pending['posting_lines']), 'a linked bill transfer cannot post to inventory')
    require(len(received['sources']) == len(pending['purchase_item_lines']), 'incomplete receipt selection')
    for source, item in zip(received['sources'], pending['purchase_item_lines']):
        claims = [claim for claim in received['claims'] if claim['bill_line_id'] == item['document_line_id']]
        require(item['item_id'] == source['line']['item_id'] and item['quantity_microunits'] ==
                sum(claim['end_microunits'] - claim['start_microunits'] for claim in claims), 'received quantity mapping differs')
        require(item['amount_minor_units'] == sum(claim['billed_minor_units'] for claim in claims), 'received bill value mapping differs')
        for claim in claims:
            q, v = source['line']['quantity_microunits'], source['line']['value_minor_units']
            # Integer half-up endpoint arithmetic, independently read from stored basis.
            expected = (2*v*claim['end_microunits']+q)//(2*q) - (2*v*claim['start_microunits']+q)//(2*q)
            require(expected == claim['original_minor_units'], 'original interval value changed')


def apply(s, ctx, fresh, applied):
    d = fresh.data; received = d['received']; stock = received['stock']
    touched = list(applied.touched)
    for inverse in received['inverses']:
        result = journals.persist_prepared(inverse, ctx, s, command_name='bill ' + d['operation'], noun='receipt price correction')
        touched.extend(result.touched)
    touched.extend(inventory_effects.write_movements(s, ctx, stock, command_name='bill ' + d['operation'],
        summary='Reverse superseded receipt price corrections', created_at=d['header']['updated_at']))
    touched.extend(inventory_effects.write_corrections(s, ctx, stock, command_name='bill ' + d['operation'], created_at=d['header']['updated_at']))
    rows = {'receipt_bill_releases': received['releases'], 'receipt_bill_claims': received['claims'], 'receipt_bill_adjustments': []}
    sources = {source['line']['movement_id']: source for source in received['sources']}
    for correction in stock.corrections:
        for movement in correction.movements:
            source = sources.get(movement.values['corrects_movement_id'])
            if source:
                rows['receipt_bill_adjustments'].append(dict(id=new_id(), bill_id=d['header']['id'],
                    bill_revision_id=d['header']['current_revision_id'], receipt_line_id=source['line']['id'],
                    movement_id=movement.values['id'], **received['provenance']))
    own = [Touched(name, row['id'], 'create', None, 1, row, db='company') for name, group in rows.items() for row in group]
    # Claim changes invalidate previously displayed receipt selections, independently
    # of the immutable commercial revision and physical quantity identities.
    ids = {source['header']['id'] for source in received['sources']}
    for claim in received['prior']:
        ids.add(effects.rows(s, c.item_receipt_lines, c.item_receipt_lines.c.id == claim['receipt_line_id'])[0]['receipt_id'])
    headers = []
    for receipt_id in sorted(ids):
        old = receiving.resolve(s, receipt_id)
        new = dict(old, version=old['version'] + 1, updated_at=d['header']['updated_at'],
            updated_by=s.actor.id, updated_via=ctx.interface.value)
        headers.append((old, new))
        own.append(Touched('item_receipt', receipt_id, 'update', old['version'], new['version'], new, old, db='company'))
    event = new_id()
    # Companion history has its own event rather than pretending it was already
    # among the bill aggregate's audited rows.
    for group in rows.values():
        for row in group:
            row['audit_event_id'] = event
    audit.write_event_to(s.company, ctx, 'bill ' + d['operation'], 'Matched received-goods intervals', own,
        actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None), event_id=event)
    for name, group in rows.items():
        if group:
            s.company.conn.execute(getattr(c, name).insert(), group)
    for old, new in headers:
        if s.company.conn.execute(c.item_receipts.update().where(c.item_receipts.c.id == old['id'],
            c.item_receipts.c.version == old['version']).values(**new)).rowcount != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_id': old['id']})
    return Applied(applied.output, touched + own, applied.summary, audited=True)


def selections_for_revision(s, revision):
    if not sa.inspect(s.company.conn).has_table('receipt_bill_claims'):
        return []
    grouped = {}
    for claim in effects.rows(s, c.receipt_bill_claims, c.receipt_bill_claims.c.bill_revision_id == revision['id']):
        grouped.setdefault(claim['bill_line_id'], []).append(claim)
    found = []
    for line_id, claims in grouped.items():
        line = effects.rows(s, c.item_receipt_lines, c.item_receipt_lines.c.id == claims[0]['receipt_line_id'])[0]
        header = receiving.resolve(s, line['receipt_id'])
        found.append(ReceiptBillingInput(receipt_line=line['id'], expected_receipt_version=header['version'],
            quantity=format_quantity_micro_units(sum(x['end_microunits']-x['start_microunits'] for x in claims)),
            amount=Money(sum(x['billed_minor_units'] for x in claims), revision['currency']).to_dict()))
    return found
