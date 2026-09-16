"""Customer-work browser projections. Commands remain the sole business authority."""
from copy import deepcopy
from decimal import Decimal
from bookflow.core.money import Money
from bookflow.company.tax_policy import POLICY_LABELS, POLICY_EXPLANATIONS
from bookflow.company.sales_contract import FORM_DEFINITIONS, SalesFormDefinition
from bookflow.company.lists import ReferenceDefinition
from bookflow.adapters.workbench.sales import _id, preserve_line_origins
from bookflow.adapters.workbench.document_print import document_url

# The nouns whose *write* surface is the quoting form: a customer, a scope, many priced lines,
# a schedule. Recorded time is deliberately not one of them -- a person records who, for whom,
# when, how long and as what, and the translation into a work document happens behind the
# command -- so it keeps the small form its own input model describes.
NOUNS = ('proposal', 'estimate', 'work-order')
# The nouns whose *records* are customer-work documents. Recorded time is one: same table, same
# immutable revisions, same billing, so every read projection below covers it and a reader gets
# the document view, the revision history and the remaining-work panel rather than a field dump.
DOCUMENTS = NOUNS + ('time-activity',)
# What a kind is called where a person reads it. Only the ones whose noun does not already say
# it: "Time activity" is the table's spelling of the kind, not anything a bookkeeper would type.
LABELS = {'time-activity': ('Time entry', 'Time entries')}
KIND_TITLES = {'time_activity': 'Time entry'}
# The statuses a kind's list offers as a filter. The three quoting kinds have always shared one
# dropdown carrying the union of their states; recorded time holds neither set, so it gets its
# own rather than being offered ten statuses its own query would refuse by name.
QUOTE_AND_WORK_STATUSES = ('draft', 'open', 'accepted', 'declined', 'superseded',
                           'scheduled', 'in_progress', 'on_hold', 'complete', 'cancelled')
LIST_STATUSES = {'time-activity': ('recorded', 'voided')}


def list_statuses(noun):
    return LIST_STATUSES.get(noun, QUOTE_AND_WORK_STATUSES)

FORM = SalesFormDefinition(tuple(r for r in FORM_DEFINITIONS['invoice'].references
    if r.field != 'ar_account') + (ReferenceDefinition('assignees', 'employee'),
        ReferenceDefinition('ar_account', 'account'), ReferenceDefinition('deposit_to', 'account'),
        ReferenceDefinition('payment_method', 'payment-method')))
# Recorded time names four records and no lines, so its form is these and nothing else: who did
# it, who it was for, what it is charged as, and which class it belongs to. The last three
# entries are the accounts and method the billing conversions ask for, which are rendered from
# this same definition because they are reached from this noun.
TIME_FORM = SalesFormDefinition((
    ReferenceDefinition('employee', 'employee'), ReferenceDefinition('customer', 'customer'),
    ReferenceDefinition('item', 'item'), ReferenceDefinition('class_id', 'class'),
    ReferenceDefinition('ar_account', 'account'), ReferenceDefinition('deposit_to', 'account'),
    ReferenceDefinition('payment_method', 'payment-method')))


def meta(noun, original):
    if noun not in DOCUMENTS:
        return original
    spelled = noun.replace('-', ' ').capitalize()
    singular, plural = LABELS.get(noun, (spelled, spelled + 's'))
    projected = dict(original, record_type='work_document', identifier=noun.replace('-', '_'),
        output_identifier='id', ui_group='Customer work', display_field='number',
        singular_label=singular, plural_label=plural)
    projected['form_definition'] = FORM if noun in NOUNS else TIME_FORM
    return projected


def editable_values(record):
    r = record['revision']
    f, p = r['facts'], r['facts']['profile']
    out = {k: deepcopy(r.get(k)) for k in ('number', 'date', 'title', 'status', 'active', 'decision_note')}
    out.update({k: deepcopy(v) for k, v in f.items() if k not in ('schema_version', 'profile', 'issuer_snapshot')})
    out.update({k: deepcopy(_id(p.get(k))) for k in (
        'customer', 'billing_address', 'shipping_address', 'shipping_address_id', 'ship_date',
        'ship_method', 'sales_rep', 'class_id', 'customer_tax_code', 'sales_tax_item',
        'price_level', 'customer_message', 'customer_message_item', 'customer_purchase_order', 'terms')})
    if p.get('shipping_address_id'):
        out.pop('shipping_address')
    if p.get('customer_message_item'):
        out.pop('customer_message')
    # A new decision must be deliberately entered, even if its wording repeats.
    if r.get('tax_calculation_details'):out['sales_tax_calculation']=r['tax_calculation_details']['policy']
    out['decision_note'] = None
    out['assignees'] = [v['id'] for v in f['assignees']]
    out['custom_fields'] = {v['definition_id']: deepcopy(v['value']) for v in r['custom_fields']}
    out['lines'] = []
    for line in r['lines']:
        facts, profile = line['facts'], line['facts']['profile']
        row = dict(line_id=line['line_id'], item=facts['item_id'], description=facts['description'],
            quantity=line['quantity'], billable=facts['billable'],
            estimated_unit_cost=line['estimated_unit_cost']['amount'] if line['estimated_unit_cost'] else None)
        row.update({k: _id(profile.get(k)) for k in ('unit', 'class_id', 'tax_code')})
        row['price_level'] = _id(profile.get('price_rule'))
        mode = facts['pricing_basis']
        if mode == 'amount':
            row['net_amount'] = line['net']['amount']
            row.pop('price_level', None)
        elif mode == 'markup':
            row['markup_percent'] = format(Decimal(facts['markup_percent_millionths']) / 1000000, 'f')
            row.pop('price_level', None)
        else:
            row['unit_price'] = line['unit_price']['amount']
        if record['kind'] == 'work_order':
            row['completed_quantity'] = line['completed_quantity']
        out['lines'].append(row)
    return out


def detail_context(record, company_id, *, preview=False):
    record = deepcopy(record)
    r = record['revision']
    for line in r['lines']:
        line['tax_components']=[dict(rule=t['rule'],tax=Money(t['tax_minor_units'],r['currency']).to_dict(),taxable=Money(t['taxable_minor_units'],r['currency']).to_dict(),rate_display=format(Decimal(t['rule']['rate_percent_millionths'])/1000000,'f')) for t in line['facts']['taxes']]
        markup = line['facts']['markup_percent_millionths']
        line['markup_display'] = format(Decimal(markup) / 1000000, 'f') if markup is not None else None
    base = f"/c/{company_id}/{record['kind'].replace('_', '-')}/{record['id']}"
    related = []
    for link in record['links']:
        source = link['destination_document_id'] == record['id']
        side = 'source' if source else 'destination'
        url = f"/c/{company_id}/{link[side + '_kind'].replace('_', '-')}/{link[side + '_document_id']}"
        related.append(dict(label=('Source ' if source else 'Destination ') + link[side + '_number'],
            url=url, revision_url=url + '?revision_number=' + str(link['source_version'] if source else 1),
            status=link[side + '_status'], relation=link['relation'].replace('_', ' ')))
    # An estimate is a document a customer receives, so it prints; a work order, a proposal and
    # a recorded stretch of time are internal and have no customer-facing form to hand over --
    # what a customer sees of somebody's hours is the invoice they become.
    print_url = (document_url(company_id, 'estimate', record['id'])
                 if record['kind'] == 'estimate' and not preview else None)
    return dict(record=record, revision=r, facts=r['facts'], profile=r['facts']['profile'],
        preview=preview, base=base, related=related, print_url=print_url,
        tax_labels=POLICY_LABELS, tax_explanations=POLICY_EXPLANATIONS,
        title=KIND_TITLES.get(record['kind'], record['kind'].replace('_', ' ').capitalize()))


def describe(leaves, noun):
    """Business labels and controls over the exact typed contract."""
    for leaf in leaves:
        path = leaf['path']
        leaf['label'] = path.replace('_', ' ').capitalize()
        if path in ('scheduled_start', 'scheduled_end', 'actual_start', 'actual_end'):
            leaf['description'] = 'Date and time with timezone, for example 2026-01-14T10:00:00-08:00.'
        if path == 'decision_note':
            leaf['description'] = 'Record who accepted or declined, how, and what was agreed.'
        if path == 'lines':
            fields = leaf['collection']['item']['fields']
            if noun != 'work-order':
                fields[:] = [v for v in fields if v['name'] != 'completed_quantity']
            for child in fields:
                if child['name'] == 'unit_price':
                    child['description'] = 'Manual unit price. Leave empty for catalog; choose only one price input.'
                if child['name'] == 'net_amount':
                    child['description'] = 'Amount price for the whole line; unit rate stays blank. Choose only one price input.'
                if child['name'] == 'markup_percent':
                    child['description'] = 'Cost markup percent, requires known cost. Choose only one price input.'
                if child['name'] == 'estimated_unit_cost':
                    child['description'] = 'Internal estimate per unit. Clear means unknown, not zero.'
    return leaves


def price_controls(form):
    """Translate an explicit UI mode selection without supplying competing inputs."""
    result = dict(form)
    for key, mode in form.items():
        if not key.startswith('price-mode:lines:') or not mode:
            continue
        path = key.removeprefix('price-mode:')
        selected = {'manual': 'unit_price', 'markup': 'markup_percent', 'amount': 'net_amount', 'catalog': None}.get(mode)
        if selected and not form.get(f'c:{path}:{selected}', '').strip():
            from bookflow.core.errors import BookflowError
            raise BookflowError('E_VALIDATION', details={'fields': [{'field': selected,
                'problem': 'Enter a value for the selected selling-price mode.'}]})
        for field in ('unit_price', 'markup_percent', 'net_amount'):
            if field != selected:
                result.pop(f'c:{path}:{field}', None)
        if mode in ('markup', 'amount'):
            result.pop(f'c:{path}:price_level', None)
            result.pop(f'c:{path}:price_basis_amount', None)
        if mode == 'catalog':
            result[f'collection:{path}:use_defaults'] = '1'
            result[f'c:{path}:use_defaults:999:value'] = 'unit_price'
    return result


def price_originals(originals, form):
    baseline = deepcopy(originals)
    # Collection controls have arbitrary stable UI indexes after insertion/reorder.
    for key, mode in form.items():
        if not key.startswith('price-mode:lines:') or mode not in ('manual', 'markup', 'amount'):
            continue
        path = key.removeprefix('price-mode:')
        identity = form.get(f'c:{path}:line_id')
        field = {'manual': 'unit_price', 'markup': 'markup_percent', 'amount': 'net_amount'}[mode]
        for line in baseline.get('lines', []):
            if line.get('line_id') == identity:
                line.pop(field, None)
    return baseline


def form_groups(leaves):
    groups = {name: [] for name in ('Work and decision', 'Scope', 'Lines and pricing', 'Scheduling', 'Commercial details', 'Saved version')}
    for leaf in leaves:
        path = leaf['path']
        if path in ('expected_version', 'proposal', 'estimate', 'work_order', 'conversion_key'):
            group = 'Saved version'
        elif path in ('scope', 'inclusions', 'exclusions', 'timing', 'commercial_terms', 'memo'):
            group = 'Scope'
        elif path == 'lines':
            group = 'Lines and pricing'
        elif path in ('priority', 'assignees', 'scheduled_start', 'scheduled_end', 'actual_start', 'actual_end') or path.startswith('site_address'):
            group = 'Scheduling'
        elif path in ('date', 'number', 'title', 'customer', 'status', 'active', 'decision_note', 'expires_on', 'acknowledge_expired', 'copy_mode'):
            group = 'Work and decision'
        else:
            group = 'Commercial details'
        groups[group].append(leaf)
    return [dict(title=name, leaves=fields, open=name not in ('Commercial details', 'Saved version'))
            for name, fields in groups.items() if fields]
