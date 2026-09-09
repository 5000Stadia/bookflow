"""Document-window layout for sales documents: header band, line grid, footer band.

Presentation only. Every control keeps the form name the generated form gives it, so
the browser submits exactly the command input an agent sends over MCP. Nothing here
resolves defaults, computes money or decides accounting: displayed amounts are copied
from the server's own computed result and are never recalculated.

Integration: call ``is_document(noun, verb)`` to select the layout, ``describe`` to put
human labels on the typed leaves, and ``context`` for the template. Leaves this module
does not place reach the reader in the advanced sections rather than disappearing.
"""
from bookflow.core.money import Money

NOUNS = ('invoice', 'sales-receipt', 'estimate')

TITLES = {'invoice': 'Invoice', 'sales-receipt': 'Sales receipt', 'estimate': 'Estimate'}

# Header band, first row: who and when, in the order a document window reads.
PRIMARY = ('customer', 'title', 'class_id', 'date', 'number', 'expires_on', 'status', 'decision_note')

# Header band, third row: the commercial terms of the document.
TERMS = ('customer_purchase_order', 'terms', 'due_date', 'payment_method', 'payment_reference',
         'deposit_to', 'ar_account', 'sales_rep', 'ship_date', 'ship_method')

# Estimates carry written scope beside the priced lines.
SCOPE = ('scope', 'inclusions', 'exclusions', 'timing', 'commercial_terms')

FOOTER = ('customer_message_item', 'customer_message', 'customer_tax_code', 'sales_tax_item', 'memo')

PRICING = ('sales_tax_calculation', 'price_level')

RECORD = ('expected_version', 'active', 'acknowledge_expired', 'refresh_defaults', 'use_defaults')

ADDRESSES = {'sales-receipt': (('Sold To', 'billing_address'), ('Ship To', 'shipping_address'))}
DEFAULT_ADDRESSES = (('Bill To', 'billing_address'), ('Ship To', 'shipping_address'))

NUMBER_LABELS = {'invoice': 'Invoice #', 'sales-receipt': 'Sale #', 'estimate': 'Estimate #'}

# Grid columns, in document order. Every one of them is a value a person reads off the
# line or types into it. ``@amount`` is the exception the reader still expects to see:
# it is not a line field but the server's own computed net, copied here.
#
# The pricing selector is deliberately not among them. It chooses which of the exclusive
# price inputs is live — a rule for the whole line rather than one of its numbers — and
# between Quantity and Rate it read as though it were one. It lives in the row's own
# panel with the other per-line machinery instead, where it is still one click away.
SALE_GRID = (('item', 'Item'), ('description', 'Description'), ('quantity', 'Quantity'),
             ('unit', 'Unit of measure'), ('unit_price', 'Rate'),
             ('class_id', 'Class'), ('@amount', 'Amount'), ('tax_code', 'Tax'))

WORK_GRID = (('item', 'Item'), ('description', 'Description'), ('quantity', 'Quantity'),
             ('unit', 'Unit of measure'), ('estimated_unit_cost', 'Cost'),
             ('markup_percent', 'Markup %'), ('unit_price', 'Rate'), ('class_id', 'Class'),
             ('@amount', 'Amount'), ('tax_code', 'Tax'))

# What each column head means, written for the person who has to tell two of them apart.
# A bookkeeper reading this grid asked what Unit was next to Quantity; a head that needs
# that question asked is a head that has not explained itself. The same words are the
# control's own description wherever the row panel holds it instead of the grid.
COLUMN_HINTS = {
    'item': 'What is charged for',
    'description': 'How the line reads on the document',
    'quantity': 'How many',
    'unit': 'How they are counted — each, hour, foot',
    'unit_price': 'Price of one',
    'estimated_unit_cost': 'Your cost of one; internal',
    'markup_percent': 'Added to your cost',
    'class_id': 'Class this line is tracked under',
    '@amount': 'Quantity × rate, from the server',
    'tax_code': 'Taxable or not',
}

# The pricing selector, in the row's own panel rather than in the columns.
LINE_PRICING = {'label': 'How this line is priced',
                'description': 'Which of the price inputs this line uses. It is a rule for '
                               'the whole line, not a number: the amount stays whatever the '
                               'server computes from it.'}

# Grid track per column, with the rem each one contributes to the grid's own width.
# The grid scrolls inside its container at that width; the page never scrolls sideways.
WIDTHS = {'item': ('minmax(9rem, 1.2fr)', 9), 'description': ('minmax(9rem, 1.5fr)', 9),
          'quantity': ('4.5rem', 4.5), 'unit': ('7rem', 7),
          'unit_price': ('5.5rem', 5.5), 'estimated_unit_cost': ('6rem', 6),
          'markup_percent': ('5rem', 5), 'class_id': ('6.5rem', 6.5),
          '@amount': ('5.5rem', 5.5), 'tax_code': ('6.5rem', 6.5)}
ACTIONS_WIDTH = ('4.5rem', 4.5)

# Per-line controls that belong to pricing machinery rather than the document grid.
LINE_EXTRAS = ('net_amount', 'price_level', 'price_basis_amount', 'estimated_unit_cost',
               'markup_percent', 'billable', 'completed_quantity',
               'refresh_defaults', 'use_defaults')

LABELS = {
    'customer': 'Customer:Job', 'class_id': 'Class', 'date': 'Date', 'number': 'Number',
    'title': 'Job title', 'expires_on': 'Expires on', 'status': 'Acceptance',
    'decision_note': 'Acceptance note', 'active': 'Still open',
    'acknowledge_expired': 'Accept that this document has expired',
    'customer_purchase_order': 'P.O. Number', 'terms': 'Terms', 'due_date': 'Due date',
    'sales_rep': 'Rep', 'ship_date': 'Ship date', 'ship_method': 'Ship via',
    'ar_account': 'Account', 'deposit_to': 'Deposit To',
    'payment_method': 'Payment Method', 'payment_reference': 'Check No. / reference',
    'customer_message': 'Customer Message', 'customer_message_item': 'Saved Customer Message',
    'customer_tax_code': 'Customer tax code', 'sales_tax_item': 'Tax',
    'memo': 'Memo', 'price_level': 'Price level applied to this document',
    'sales_tax_calculation': 'Tax calculation rule',
    'refresh_defaults': 'Reprice unentered fields from the current records',
    'use_defaults': 'Fields to return to their record defaults',
    'expected_version': 'Saved version being corrected',
    'shipping_address_id': 'Saved shipping address',
    'scope': 'Scope', 'inclusions': 'Included', 'exclusions': 'Excluded',
    'timing': 'Timing', 'commercial_terms': 'Commercial terms',
    'line1': 'Address line 1', 'line2': 'Address line 2', 'city': 'City',
    'state': 'State or region', 'postal_code': 'Postal code', 'country': 'Country',
}

LINE_LABELS = {
    'item': 'Item', 'description': 'Description', 'quantity': 'Quantity',
    'unit': 'Unit of measure',
    'unit_price': 'Rate', 'class_id': 'Class', 'tax_code': 'Tax', 'net_amount': 'Whole-line amount',
    'markup_percent': 'Cost markup percent', 'price_level': 'Price level',
    'price_basis_amount': 'Price basis amount', 'estimated_unit_cost': 'Estimated cost per unit',
    'billable': 'Billable', 'completed_quantity': 'Completed quantity',
    'refresh_defaults': 'Reprice this line from the current records',
    'use_defaults': 'Fields on this line to return to their defaults',
}

DESCRIPTIONS = {
    'customer': 'Search by customer or job name, then choose the match.',
    'number': 'Leave empty to take the next number.',
    'sales_tax_item': 'The sales tax applied to this document. The rate comes from the tax record.',
    'customer_tax_code': 'Whether this customer is taxable on this document.',
    'sales_tax_calculation': 'How cents are rounded and attributed when tax is calculated. '
                             'Leaving it alone keeps the rule already recorded.',
    'price_level': 'A price rule applied to every line that has no rate of its own.',
    'refresh_defaults': 'Re-read prices, terms and addresses from the current records for every '
                        'field you have not entered yourself.',
    'use_defaults': 'Fields listed here go back to whatever the customer and item records say.',
    'expected_version': 'The version this correction was opened against. A newer save is rejected '
                        'rather than overwritten.',
    'shipping_address_id': 'One of the addresses saved on the customer record.',
    'ar_account': 'The receivable account this invoice is posted to.',
    'deposit_to': 'The account the money was put into.',
}

# The default-field lists are typed literals; a person needs the field's own name.
DEFAULT_CHOICE_LABELS = {
    'sales_tax_calculation': 'Tax calculation rule', 'billing_address': 'Bill to address',
    'shipping_address': 'Ship to address', 'terms': 'Terms', 'due_date': 'Due date',
    'ship_method': 'Ship via', 'sales_rep': 'Rep', 'class_id': 'Class',
    'customer_tax_code': 'Customer tax code', 'sales_tax_item': 'Sales tax',
    'price_level': 'Price level', 'payment_method': 'Payment method',
    'description': 'Description', 'unit': 'Unit', 'unit_price': 'Rate',
    'tax_code': 'Tax code', 'estimated_unit_cost': 'Estimated cost per unit',
}

CONTEXT_LABELS = {'reason': 'Reason', 'source_ref': 'Source document reference',
                  'directive': 'The instruction this came from',
                  'idempotency_key': 'Retry key'}

HELP = {
    'invoice': 'A posted invoice records the sale in the books. It is not sent or emailed to the customer.',
    'sales-receipt': 'A sales receipt records a sale that was paid at the time. It cannot settle an existing invoice.',
    'estimate': 'An estimate is not posted to the books. It records what the work will cost and what was agreed.',
}


def is_document(noun, verb):
    """Whether this command opens as a document window rather than a generated form."""
    return ((noun in ('invoice', 'sales-receipt') and verb in ('post', 'update'))
            or (noun == 'estimate' and verb in ('create', 'update')))


def heading(noun, verb, originals):
    number = (originals or {}).get('number')
    title = TITLES[noun]
    if verb in ('post', 'create'):
        return 'New ' + title.lower()
    return title + (' ' + str(number) if number else '') + ' — correction'


def describe(leaves, noun):
    """Human labels over the typed leaves; the wire names and controls stay untouched."""
    for leaf in leaves:
        path = leaf['path']
        tail = path.rsplit('.', 1)[-1]
        if path.startswith(('billing_address.', 'shipping_address.')):
            leaf['label'] = LABELS.get(tail, tail.replace('_', ' ').capitalize())
        elif path == 'number':
            leaf['label'] = NUMBER_LABELS[noun]
        elif path in LABELS:
            leaf['label'] = LABELS[path]
        elif not leaf.get('label'):
            leaf['label'] = tail.replace('_id', '').replace('_', ' ').capitalize()
        if leaf['kind'] == 'collection' and path == 'use_defaults':
            leaf['collection']['item']['choice_labels'] = DEFAULT_CHOICE_LABELS
        if path == 'lines':
            for child in leaf['collection']['item']['fields']:
                child['label'] = LINE_LABELS.get(child['name'],
                                                 child['name'].replace('_id', '').replace('_', ' ').capitalize())
                if child['name'] in COLUMN_HINTS and not child.get('description'):
                    child['description'] = COLUMN_HINTS[child['name']]
                if child['kind'] == 'collection' and child['name'] == 'use_defaults':
                    child['collection']['item']['choice_labels'] = DEFAULT_CHOICE_LABELS
        if path in DESCRIPTIONS:
            leaf['description'] = DESCRIPTIONS[path]
    return leaves


def _address_group(title, prefix, leaves, placed):
    group = [leaf for leaf in leaves
             if leaf['path'].startswith(prefix + '.') and leaf['path'] not in placed]
    selector = [leaf for leaf in leaves
                if prefix == 'shipping_address' and leaf['path'] == 'shipping_address_id'
                and leaf['path'] not in placed]
    if not group and not selector:
        return None
    placed.update(leaf['path'] for leaf in group + selector)
    return {'title': title, 'prefix': prefix, 'leaves': selector + group,
            'component_paths': [leaf['path'] for leaf in group]}


def layout(noun, leaves):
    """Bands of the document window, and every leaf placed exactly once."""
    by_path, placed = {leaf['path']: leaf for leaf in leaves}, set()
    grid = WORK_GRID if noun == 'estimate' else SALE_GRID

    def take(paths):
        found = []
        for path in paths:
            leaf = by_path.get(path)
            if leaf is not None and path not in placed:
                placed.add(path)
                found.append(leaf)
        return found

    primary, terms = take(PRIMARY), take(TERMS)
    addresses = [group for group in
                 (_address_group(title, prefix, leaves, placed)
                  for title, prefix in ADDRESSES.get(noun, DEFAULT_ADDRESSES))
                 if group is not None]
    scope = take(SCOPE)
    lines = by_path.get('lines')
    if lines is not None:
        placed.add('lines')
    footer = take(FOOTER)
    pricing = take(PRICING)
    record = take(RECORD)
    # Anything this layout does not name still reaches the reader, rather than
    # disappearing from a form that must stay input-identical to the command.
    record += [leaf for leaf in leaves if leaf['path'] not in placed]
    line_fields = list(lines['collection']['item']['fields']) if lines is not None else []
    named = {name for name, _ in grid if not name.startswith('@')}
    columns = [{'name': name, 'label': label, 'hint': COLUMN_HINTS.get(name),
                'field': next((child for child in line_fields if child['name'] == name), None)}
               for name, label in grid]
    extras = ([child for child in line_fields
               if child['name'] in LINE_EXTRAS and child['name'] not in named]
              + [child for child in line_fields
                 if child['name'] not in named and child['name'] not in LINE_EXTRAS
                 and child['name'] != 'line_id'])
    tracks = [WIDTHS.get(column['name'], ('9rem', 9)) for column in columns] + [ACTIONS_WIDTH]
    return {'primary': primary, 'addresses': addresses, 'terms': terms, 'scope': scope,
            'lines': lines, 'columns': columns, 'line_extras': extras,
            'line_pricing': LINE_PRICING,
            'footer': footer, 'pricing': pricing, 'record': record,
            'grid_template': ' '.join(track for track, _ in tracks),
            'grid_width': format(sum(width for _, width in tracks), 'g') + 'rem'}


def _amount(value):
    return value['amount'] if isinstance(value, dict) and 'amount' in value else None


def computed(record):
    """Copy the server's computed money for display. Nothing here is recalculated."""
    if not isinstance(record, dict) or not isinstance(record.get('revision'), dict):
        return None
    revision = record['revision']
    lines = [{'line_id': line.get('line_id'), 'amount': _amount(line.get('net')),
              'tax': _amount(line.get('tax'))} for line in revision.get('lines', [])]
    subtotal = _amount(revision.get('subtotal')) or _amount(revision.get('net'))
    out = {'currency': revision.get('currency'), 'subtotal': subtotal,
           'tax': _amount(revision.get('tax')), 'total': _amount(revision.get('total')),
           'lines': lines, 'by_line': {row['line_id']: row for row in lines if row['line_id']}}
    settlement = record.get('settlement_current')
    if isinstance(settlement, dict):
        currency = settlement['currency']
        out['applied'] = Money(settlement['applied_minor_units'], currency).to_dict()['amount']
        out['due'] = Money(settlement['due_minor_units'], currency).to_dict()['amount']
        out['settlement_status'] = settlement['status']
    return out


def context(noun, verb, leaves, originals, *, shown=None, result=None, preview=False,
            record_id=None, base=''):
    """Everything the document template needs, with money taken from the server alone."""
    figures = computed(result) or computed(shown)
    fresh = result is not None

    def line_amount(line_id, index):
        if figures is None:
            return None
        row = figures['by_line'].get(line_id) if line_id else None
        if row is None and fresh and index < len(figures['lines']):
            row = figures['lines'][index]
        return row

    return dict(layout(noun, leaves),
                noun=noun, verb=verb, title=TITLES[noun],
                heading=heading(noun, verb, originals),
                help=HELP[noun], computed=figures, line_amount=line_amount,
                preview=preview, creating=verb in ('post', 'create'),
                settled=noun == 'invoice', base=base, record_id=record_id,
                context_labels=CONTEXT_LABELS,
                origin='the last preview' if fresh else 'the saved document')
