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
# One home for the order an address reads in: the printed document uses the same one.
from bookflow.documents.model import address_block
# One home for the print route's shape, shared with the estimate and the statement.
from bookflow.adapters.workbench.document_print import document_url
# One home for turning a stored transaction type into the segment its page lives at.
from bookflow.adapters.workbench.transaction_detail import document_noun
# The settlement contract: which receivables money can be applied to.
from bookflow.company.ledger_schema import SETTLEABLE_RECEIVABLE_TYPES


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




def nouns():
    """The documents this page renders: every noun whose `show` returns `SalesOutput`.

    Derived rather than listed, because the reason these share one page is that they share
    one output shape. A fourth document that adopts it renders on the day it lands, and one
    that stops is dropped on the day it stops -- which is how `statement-charge` came to be
    missing from a hand-kept pair for as long as it did.
    """
    from bookflow.core import registry
    from bookflow.company.sales_outputs import SalesOutput
    return frozenset(command.name.removesuffix(' show')
                     for command in registry.all_commands(include_standalone=True)
                     if command.name.endswith(' show')
                     and getattr(command, 'output_model', None) is SalesOutput)


def detail_context(record, company_id, *, preview=False):
    """Template context from the selected revision, including safe local links."""
    record = deepcopy(record)
    revision = record['revision']
    noun = document_noun(record['type'])
    base = '/c/' + quote(str(company_id), safe='') + '/' + noun
    url = base + '/' + quote(str(record['id']), safe='')
    links = []
    print_url = None
    credit_url = None
    if not preview:
        print_url = document_url(company_id, noun, record['id']) if not record.get('deletion') else None
        if noun == 'invoice' and record['status'] == 'posted':
            # A return is written against this invoice, so it is opened from it: the credit
            # window seeds one returned row per line rather than asking anyone to copy ids.
            credit_url = ('/c/' + quote(str(company_id), safe='') + '/credit-memo/post?invoice='
                          + quote(str(record['id']), safe=''))
        number = revision['revision_number']
        if number > 1:
            links.append(('Previous revision', url + '?revision_number=' + str(number - 1)))
        if revision['id'] != record['current_revision_id']:
            links += [('Next revision', url + '?revision_number=' + str(number + 1)), ('Current revision', url)]
        links.append(('History', url + '/history'))
        if revision.get('billing_sources') and record['status'] == 'posted':
            links.append(('Add an unlinked line', url + '/update#ordinary-lines'))
    for line in revision['lines']:
        for component in line.get('tax_components', []):
            component['rate_display'] = format(Decimal(component['rate_percent_millionths']) / Decimal(1_000_000), 'f')
    if record.get('deletion'):
        links = [(label, target + ('&' if '?' in target else '?') + 'include_deleted=1') for label,target in links]
    issuer = revision.get('issuer_snapshot', {})
    settlement = deepcopy(record.get('settlement_current'))
    if settlement:
        for field in ('gross', 'applied', 'due'):
            settlement[field] = Money(settlement[field + '_minor_units'], settlement['currency']).to_dict()
    from bookflow.core import registry
    return dict(record=record, revision=revision, profile=revision['profile'], preview=preview,
                print_url=print_url, credit_url=credit_url,
                tax_labels=POLICY_LABELS, tax_explanations=POLICY_EXPLANATIONS,
                settlement=settlement,
                # A settlement page exists for what money can be applied to, and the page it
                # lives at is the invoice route for both kinds -- `_settled_document` resolves
                # the real type out of the settlement's own row.
                settleable=record['type'] in SETTLEABLE_RECEIVABLE_TYPES,
                settlement_url='/c/' + quote(str(company_id), safe='') + '/invoice/'
                               + quote(str(record['id']), safe='') + '/settlement',
                title=registry.noun_meta(noun)['singular_label'], links=links,
                issuer=issuer, billing=address_block(revision['profile'].get('billing_address')),
                shipping=address_block(revision['profile'].get('shipping_address')),
                issuer_address=address_block({k.removeprefix('address_'): v for k, v in issuer.items() if k.startswith('address_')}))
