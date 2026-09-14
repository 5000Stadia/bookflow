"""Saved check/card presentation from captured command output, without new defaults."""
from copy import deepcopy

from bookflow.core.errors import BookflowError
from bookflow.core.money import Money


def owning_record(run, company_id, transaction_id, revision_number=None):
    """Resolve only explicit purchase markers through the owning read commands."""
    for noun, selector in (('check', 'check'), ('card-charge', 'card_charge')):
        raw = {selector: transaction_id}
        if revision_number is not None:
            raw['revision_number'] = revision_number
        try:
            return noun, run(noun + ' show', raw, company_id)
        except BookflowError as error:
            if error.code != 'E_RECORD_NOT_FOUND':
                raise
    return None


def _party(line):
    return dict(name_type=line['name_type'], name_id=line['name_id']) if line['name_id'] else None


def editable_values(record):
    revision, document = record['revision'], record['document']
    funding = revision['lines'][0]
    values = dict(account=document['account_id'], pay_to=_party(funding),
        date=revision['date'], amount=document['amount']['amount'], memo=revision['memo'],
        custom_fields={f['definition_id']: deepcopy(f['value']) for f in revision.get('custom_fields', [])},
        expenses=[], items=[])
    if document['kind'] == 'check':
        values['number'] = document['check_number']
    # There is no separately captured header class. Display each resolved line class
    # explicitly, so replacing a grid cannot reclassify its untouched neighbours.
    item_ids = {item['line_id'] for item in document['items']}
    for line in revision['lines'][1:]:
        if line['line_id'] in item_ids:
            continue
        values['expenses'].append(dict(line_id=line['line_id'], account=line['account_id'],
            amount=line['amount']['amount'], memo=line['description'], party=_party(line),
            class_id=line['class_id'], class_mode='value' if line['class_id'] else 'none'))
    for item in document['items']:
        profile = item['profile']
        row = dict(line_id=item['line_id'], item=profile['item']['id'],
            description=item['description'], quantity=item['quantity'],
            customer=profile['customer']['id'] if profile['customer'] else None,
            billable=profile['billable'],
            class_id=profile['class_id']['id'] if profile['class_id'] else None,
            class_mode='value' if profile['class_id'] else 'none')
        if profile['amount_basis'] == 'amount':
            row['amount'] = item['amount']['amount']
        else:
            row['unit_cost'] = Money(profile['unit_cost_minor_units'], document['currency']).to_dict()['amount']
        values['items'].append(row)
    return values


def detail_context(record):
    document = record['document']
    ids = {item['line_id'] for item in document['items']}
    return dict(document=document, revision=record['revision'],
        title='Check' if document['kind'] == 'check' else 'Credit card charge',
        items=[dict(item, unit_cost=Money(item['profile']['unit_cost_minor_units'], document['currency']).to_dict()
                    if item['profile']['unit_cost_minor_units'] is not None else None)
               for item in document['items']],
        expenses=[line for line in record['revision']['lines'][1:] if line['line_id'] not in ids])
