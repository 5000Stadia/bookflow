"""Display captured receipt sources and seed editable selections, without posting."""
from bookflow.core.exact import format_quantity_micro_units


def receipt_selection(receipts):
    attempted = {'collection:receipts': '1'}
    index = 0
    for receipt in receipts:
        for line in receipt['items']:
            remaining = line['unbilled_quantity_microunits']
            if not remaining:
                continue
            for key, value in dict(receipt_line=line['id'], expected_receipt_version=receipt['version'],
                quantity=format_quantity_micro_units(remaining)).items():
                attempted[f'c:receipts:{index}:{key}'] = str(value)
            index += 1
    return attempted


def order_selection(order):
    remaining = {row['order_line_id']: row['remaining_quantity_microunits'] for row in order['receiving']}
    attempted = {'f:vendor': order['vendor_id'], 'f:purchase_order': order['id'],
                 'f:purchase_order_version': str(order['version']), 'collection:items': '1'}
    index = 0
    for line in order['revision']['lines']:
        quantity = remaining.get(line['line_id'], 0)
        if quantity <= 0:
            continue
        for key, value in dict(item=line['item_id'], order_line_id=line['line_id'],
            quantity=format_quantity_micro_units(quantity), unit_cost=line['rate']['amount']).items():
            attempted[f'c:items:{index}:{key}'] = str(value)
        index += 1
    return attempted
