"""Captured item rows and stock effects owned by a check or card purchase."""
import json
import sqlalchemy as sa
from bookflow.company import bills, inventory_effects, journals, schema as c
from bookflow.company.bill_facts import BillItemProfile
from bookflow.company.check_models import MoneyOutItemOutput
from bookflow.core.money import Money
from bookflow.core.exact import format_quantity_micro_units


def stored(s, lines):
    ids = [line['id'] for line in lines]
    rows = s.company.conn.execute(sa.select(c.money_out_item_lines).where(
        c.money_out_item_lines.c.document_line_id.in_(ids))).mappings()
    by_id = {row['document_line_id']: row for row in rows}
    return [dict(line_id=line['line_id'], family='item', amount_minor_units=line['amount_minor_units'],
                 memo=line['description'], profile=BillItemProfile.model_validate_json(
                     by_id[line['id']]['line_snapshot'])) for line in lines if line['id'] in by_id]


def resolve(s, inputs, previous, header_class, currency):
    if inputs is None:
        return previous
    allowed = {line['line_id'] for line in previous}
    seen = set()
    result = []
    for index, item in enumerate(inputs):
        if item.line_id is not None:
            if item.line_id not in allowed or item.line_id in seen:
                raise journals.invalid(f'items.{index}.line_id', 'must identify a distinct existing item row')
            seen.add(item.line_id)
        line = bills._item_line(s, item, header_class, currency, index)
        line['line_id'] = item.line_id
        result.append(line)
    return result


def output(item, line_id, currency):
    return MoneyOutItemOutput(line_id=line_id, quantity=format_quantity_micro_units(item["profile"].quantity_microunits), description=item['memo'], profile=item['profile'],
                             amount=Money(item['amount_minor_units'], currency).to_dict())


def changed(s, header, items):
    if header is None:
        return False
    revision = journals.revision(s, header)
    lines = journals.rows(s, c.document_lines, c.document_lines.c.revision_id == revision['id'],
                          order=c.document_lines.c.position)
    return items != stored(s, lines)


def attach(s, fresh, items):
    """Bind captured facts and stock movements to this exact journal revision's legs."""
    if not fresh.data['changed']:
        return [], None, []
    d = fresh.data
    pending, header = d['pending'], d['header']
    revision = fresh.preview.revision
    lines = pending['document_lines'][-len(items):] if items else []
    rows, entries, outputs = [], [], []
    for item, line in zip(items, lines):
        facts = item['profile']
        rows.append(dict(document_line_id=line['id'], transaction_id=header['id'],
                         revision_id=line['revision_id'], item_id=facts.item.id,
                         line_snapshot=json.dumps(facts.model_dump(), sort_keys=True)))
        entry = inventory_effects.purchase_entry(facts, key=line['id'],
            amount=item['amount_minor_units'], offset_account_id=pending['document_lines'][0]['account_id'],
            class_id=line['class_id'])
        if entry is not None:
            entries.append(entry)
        outputs.append(output(item, line['line_id'], line['currency']))
    stock = inventory_effects.plan(s, entries=entries,
        reversing=inventory_effects.own_movements(s, header['id']) if d['before'] else (),
        date=revision.date, currency=revision.currency, field='items')
    inventory_effects.open_dates(s, stock)
    sources = {source['document_line_id']: source['posting_line_id']
               for source in pending['posting_line_sources'] if source['reversed_source_id'] is None}
    legs = {leg['id']: leg for leg in pending['posting_lines']}
    for movement in stock.movements:
        if movement.key is not None:
            line = next(row for row in lines if row['id'] == movement.key)
            inventory_effects.bind(movement, legs[sources[movement.key]], transaction_id=header['id'],
                                   revision_id=line['revision_id'], document_line_id=line['id'])
    inventory_effects.bind_reversals(stock, pending['posting_lines'])
    inventory_effects.check(s, stock, pending['posting_lines'])
    return rows, stock, outputs
