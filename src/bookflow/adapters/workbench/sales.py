"""Read-only sales presentation; never resolve defaults or recalculate money.

Integration: use editable_values for BOTH rendered originals and POST comparisons.
After forms.translate, call preserve_line_origins(raw, originals) on updates.
Render sales_detail.html with sale=detail_context(result, company_id, preview=...).
"""
from copy import deepcopy
from decimal import Decimal
from urllib.parse import quote
from bookflow.core.money import Money

from bookflow.company.tax_policy import POLICY_LABELS, POLICY_EXPLANATIONS


def _id(value):
    return value['id'] if isinstance(value, dict) and 'id' in value else value


def editable_values(record):
    """Display resolved values, retaining null/false/zero/empty and stable line ids.

    This is a form comparison baseline, NOT a replacement command payload.
    Unchanged leaves must be omitted so the writer retains their saved origins.
    """
    revision, profile = record['revision'], record['revision']['profile']
    result = {key: deepcopy(revision.get(key)) for key in ('number', 'date', 'memo')}
    fields = ('billing_address', 'shipping_address', 'shipping_address_id', 'ship_date',
              'ship_method', 'sales_rep', 'class_id', 'customer_tax_code', 'sales_tax_item',
              'price_level', 'customer_message', 'customer_message_item', 'customer_purchase_order')
    fields += ('terms', 'due_date') if record['type'] == 'invoice' else ('payment_method', 'payment_reference')
    result.update({key: deepcopy(_id(profile.get(key))) for key in fields})
    # Mutually exclusive selector/manual inputs must not both appear in a form baseline.
    if profile.get('shipping_address_id'):
        result.pop('shipping_address')
    if profile.get('customer_message_item'):
        result.pop('customer_message')
    if revision.get('tax_calculation_details'):
        result['sales_tax_calculation'] = revision['tax_calculation_details']['policy']
    result['customer'] = profile['customer']['id']
    result['ar_account' if record['type'] == 'invoice' else 'deposit_to'] = profile['control_account']['id']
    result['custom_fields'] = {f['definition_id']: deepcopy(f['value']) for f in revision.get('custom_fields', [])}
    result['lines'] = []
    for line in revision['lines']:
        facts = line['item_snapshot']
        if line.get('pricing_basis') == 'allocated':
            result['lines'].append({'line_id': line['line_id'], 'item': facts['item']['id']})
            continue
        row = {key: deepcopy(line[key]) for key in ('line_id', 'quantity', 'description')}
        row['item'] = facts['item']['id']
        if line.get('pricing_basis') == 'amount':
            row['net_amount'] = line['net']['amount']
        else:
            row['unit_price'] = line['unit_price']['amount']
        row.update({key: _id(facts.get(key)) for key in ('unit', 'class_id', 'tax_code')})
        row['price_level'] = _id(facts.get('price_rule'))
        if facts.get('price_basis_minor_units') is not None:
            from bookflow.core.money import Money
            row['price_basis_amount'] = Money(facts['price_basis_minor_units'], line['currency']).to_dict()['amount']
        if line.get('pricing_basis') == 'amount':
            row.pop('price_level', None)
            row.pop('price_basis_amount', None)
        result['lines'].append(row)
    return result


def preserve_line_origins(raw, originals):
    """Copy translated update; strip equal optional leaves in retained line rows.

    Generic collections are replacements: changing one quantity resubmits all
    rows. Compare by stable identity, never position. Explicit clears and changed
    values survive; use_defaults and refresh flags always reach the writer.
    """
    result = deepcopy(raw)
    prior = {line['line_id']: line for line in originals.get('lines', [])}
    for row in result.get('lines') or []:
        old = prior.get(row.get('line_id'))
        if old is None:
            continue
        for key in list(row):
            if key not in ('line_id', 'item', 'refresh_defaults', 'use_defaults') and key in old and row[key] == old[key]:
                del row[key]
    return result


def address_lines(address):
    return [str(address[key]) for key in ('line1', 'line2', 'city', 'state', 'postal_code', 'country')
            if address and address.get(key) not in (None, '')]


def detail_context(record, company_id, *, preview=False):
    """Template context from the selected revision, including safe local links."""
    record = deepcopy(record)
    revision = record['revision']
    noun = 'invoice' if record['type'] == 'invoice' else 'sales-receipt'
    base = '/c/' + quote(str(company_id), safe='') + '/' + noun
    url = base + '/' + quote(str(record['id']), safe='')
    links = []
    if not preview:
        number = revision['revision_number']
        if number > 1:
            links.append(('Previous revision', url + '?revision_number=' + str(number - 1)))
        if revision['id'] != record['current_revision_id']:
            links += [('Next revision', url + '?revision_number=' + str(number + 1)), ('Current revision', url)]
        links.append(('History', url + '/history'))
        if revision.get('billing_sources') and record['status'] != 'voided':
            links.append(('Add an unlinked line', url + '/update#ordinary-lines'))
    for line in revision['lines']:
        for component in line.get('tax_components', []):
            component['rate_display'] = format(Decimal(component['rate_percent_millionths']) / Decimal(1_000_000), 'f')
    issuer = revision.get('issuer_snapshot', {})
    settlement = deepcopy(record.get('settlement_current'))
    if settlement:
        for field in ('gross', 'applied', 'due'):
            settlement[field] = Money(settlement[field + '_minor_units'], settlement['currency']).to_dict()
    return dict(record=record, revision=revision, profile=revision['profile'], preview=preview,
                tax_labels=POLICY_LABELS, tax_explanations=POLICY_EXPLANATIONS,
                settlement=settlement, settlement_url=url+'/settlement',
                title='Invoice' if noun == 'invoice' else 'Sales receipt', links=links,
                issuer=issuer, billing=address_lines(revision['profile'].get('billing_address')),
                shipping=address_lines(revision['profile'].get('shipping_address')),
                issuer_address=address_lines({k.removeprefix('address_'): v for k, v in issuer.items() if k.startswith('address_')}))
