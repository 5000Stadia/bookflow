"""Document-window layout: header band, line grid where there is one, footer band.

Four families share this window. The sales documents have a priced line grid; the money-out
documents and the bill have an expenses grid, the bill's carrying the two columns a payable
adds; a transfer has no grid at all, because it is two legs of one amount and there is nothing
to itemise.

Presentation only. Every control keeps the form name the generated form gives it, so
the browser submits exactly the command input an agent sends over MCP. Nothing here
resolves defaults, computes money or decides accounting: displayed amounts are copied
from the server's own computed result and are never recalculated.

Integration: call ``is_document(noun, verb)`` to select the layout, ``describe`` to put
human labels on the typed leaves, and ``context`` for the template. Leaves this module
does not place reach the reader in the advanced sections rather than disappearing.
"""
from bookflow.core.money import Money

NOUNS = ('invoice', 'sales-receipt', 'estimate', 'bill')

# The money-out documents: a check written on a bank account, a charge made on a credit card.
# They open in this same window and use this same grid; what differs is the account that funds
# them, whether a check number applies, and the words on the form. They are deliberately not in
# NOUNS -- those three have list pages and document arrows, and these do not yet.
MONEY_OUT = ('check', 'card-charge')

# The transfer: money moved between two of the company's own accounts. It opens in this same
# window and shares the header band, and it is the one document here with no line grid at
# all -- a transfer has exactly two legs of one amount, so a grid would ask for the amount
# twice and leave room for a row that could never balance.
TRANSFER = 'transfer'

# The bill: what a vendor has charged the company, standing open at that total until it is
# paid. It is the payables mirror of the invoice and it opens in this same window -- the same
# header band, the same Expenses grid the check writes, the same footer -- in its own words: a
# vendor rather than a customer, a due date the vendor's terms derive, and a reference number
# that is the vendor's own spelling rather than ours. It has a list page and document arrows,
# which is why it is in NOUNS and the money-out pair is not.
BILL = 'bill'

# Documents with no list page and no arrows of their own; Cancel returns them to the board
# they were opened from.
NO_LIST = (*MONEY_OUT, TRANSFER)

TITLES = {'invoice': 'Invoice', 'sales-receipt': 'Sales receipt', 'estimate': 'Estimate',
          'check': 'Check', 'card-charge': 'Credit card charge', 'transfer': 'Transfer',
          'bill': 'Bill'}

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

NUMBER_LABELS = {'invoice': 'Invoice #', 'sales-receipt': 'Sale #', 'estimate': 'Estimate #',
                 'check': 'Check No.', 'bill': 'Bill #'}

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

# The header of a money-out document, in the order it reads: the account it is drawn on, who
# the money went to, when, its number, and the figure on its face.
MONEY_OUT_PRIMARY = ('account', 'pay_to.name_type', 'pay_to.name_id', 'date', 'number',
                     'amount', 'class_id')
MONEY_OUT_FOOTER = ('memo',)

# The whole of a transfer: where it comes from, where it goes, when, and how much. The memo
# sits in the footer with every other document's memo.
TRANSFER_PRIMARY = ('from_account', 'to_account', 'date', 'amount')
TRANSFER_FOOTER = ('memo',)

# The Expenses grid. ``amount`` here is a number a person types rather than a server
# computation, so there is no ``@amount`` column: what the server computes is the total of
# these lines, and the footer shows it against the amount on the face of the document.
#
# The collection behind this grid is named ``expenses`` rather than ``lines`` precisely so
# that the Items tab this document is going to grow arrives as a second named collection
# beside it instead of as a rewrite of this one.
EXPENSE_GRID = (('account', 'Account'), ('amount', 'Amount'), ('memo', 'Memo'),
                ('class_id', 'Class'))

# The header of a bill, in the order the payables window reads it: who it is owed to, when it
# was written, our own number for it, and the number the vendor printed on their own document.
BILL_PRIMARY = ('vendor', 'date', 'number', 'supplier_reference')

# What was agreed about paying it. ``terms`` derives ``due_date`` on the server, so both are
# shown: the rule, and the date it produced, which a person may overrule outright.
BILL_TERMS = ('terms', 'due_date', 'ap_account', 'class_id')
BILL_FOOTER = ('memo',)

# The bill's Expenses grid. It is the check's grid with the two columns a payable adds: the
# customer or job the cost belongs to, and whether it is to be passed on to them. ``amount``
# is typed here too, and the bill's total is what these rows add up to -- there is no figure
# on the face of the document to reconcile against, which is the one way this differs from a
# check written for a fixed sum.
BILL_GRID = (('account', 'Account'), ('amount', 'Amount'), ('memo', 'Memo'),
             ('customer', 'Customer:Job'), ('billable', 'Billable'), ('class_id', 'Class'))

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
    'account': 'What the money was spent on',
    'amount': 'How much of the total this line is',
    'memo': 'What this line was for',
    'customer': 'Customer or job this cost belongs to',
    'billable': 'Pass this cost on to that customer later',
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
          '@amount': ('5.5rem', 5.5), 'tax_code': ('6.5rem', 6.5),
          'account': ('minmax(9rem, 1.4fr)', 9), 'amount': ('6.5rem', 6.5),
          'memo': ('minmax(9rem, 1.6fr)', 9), 'customer': ('minmax(8rem, 1.1fr)', 8),
          'billable': ('4.5rem', 4.5)}
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
    'account': 'Account', 'amount': 'Amount', 'memo': 'Memo',
    'customer': 'Customer:Job',
    'class_mode': 'How this line is classed',
    'party': 'Customer, job or other name this line is for',
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
    'check': 'A check records money leaving a bank account. Nothing is printed or sent: the number is the '
             'one written on the check itself. The expense lines have to add up to the amount.',
    'card-charge': 'A credit card charge records a purchase put on a company card. What is owed on the card '
                   'goes up until the card is paid. The expense lines have to add up to the amount.',
    'transfer': 'A transfer moves money between two accounts the company already owns. It is neither income '
                'nor expense, so it changes no profit. Both accounts have to be balance-sheet accounts: a '
                'bank, a credit card, another asset, a loan, or equity.',
    'bill': 'A bill records what a vendor has charged you and what it was for. Each expense line debits '
            'its own account and Accounts Payable is credited the total, so the bill stands open at that '
            'total until it is paid. Nothing is paid here, and nothing is sent to the vendor.',
}

# The same window, in each document's own words. A label or an explanation here overrides the
# shared ones below it, which is what lets one layout serve a sale and a check without either
# of them reading as the other.
NOUN_LABELS = {
    'check': {'account': 'Bank Account', 'pay_to.name_type': 'Kind of name',
              'pay_to.name_id': 'Pay to the Order of', 'amount': 'Amount of this check'},
    'card-charge': {'account': 'Credit Card', 'pay_to.name_type': 'Kind of name',
                    'pay_to.name_id': 'Purchased From', 'amount': 'Amount of this charge'},
    'transfer': {'from_account': 'Transfer Funds From', 'to_account': 'Transfer Funds To',
                 'amount': 'Transfer Amount'},
    'bill': {'vendor': 'Vendor', 'supplier_reference': 'Ref. No.', 'terms': 'Terms',
             'due_date': 'Bill Due', 'ap_account': 'A/P Account'},
}

NOUN_DESCRIPTIONS = {
    'check': {'account': 'The bank account this check is written on.',
              'pay_to.name_type': 'Which list the name comes from. Choose this before searching.',
              'pay_to.name_id': 'Search by name, then choose the match.',
              'amount': 'The figure on the face of the check. The expense lines below have to add up to it.',
              'number': 'The number written on the check. Leave it empty to take the next one.'},
    'card-charge': {'account': 'The credit card account this purchase was charged to.',
                    'pay_to.name_type': 'Which list the name comes from. Choose this before searching.',
                    'pay_to.name_id': 'Search by name, then choose the match.',
                    'amount': 'What was charged. The expense lines below have to add up to it.'},
    'transfer': {'from_account': 'The account the money comes out of. It is credited, so a bank '
                                 'balance falls and what is owed on a card rises.',
                 'to_account': 'The account the money goes into. It is debited, so a bank balance '
                               'rises and what is owed on a card falls.',
                 'amount': 'How much moves. Both accounts move by exactly this.'},
    'bill': {'vendor': 'Search by vendor name, then choose the match.',
             'supplier_reference': "The vendor's own number on the document they sent, kept as typed. "
                                   'Another bill from this vendor carrying the same one is reported '
                                   'back to you, never refused.',
             'terms': 'How long you have to pay. Left empty it comes from the vendor record, and '
                      'whatever it is it fixes the due date.',
             'due_date': 'When this bill has to be paid. Left empty the terms derive it; a date '
                         'entered here overrides them outright.',
             'ap_account': 'The Accounts Payable account this bill is owed from.',
             'number': "Bookflow's own number for this bill, which is not the vendor's. Leave it "
                       'empty to take the next one.',
             'class_id': 'The class the whole bill is tracked under. A line with a class of its '
                         'own keeps it.'},
}

# What the money-out footer calls the figure on the face of the document.
FACE_LABELS = {'check': 'Amount of this check', 'card-charge': 'Amount of this charge'}

# How a transfer's own footer names each end's figure. On a card or a loan the number that
# moves is what you owe on it, and saying the account "goes down" when the debt does would
# read as the money going the other way. Equity is credit-normal too and is not a debt, so it
# keeps its own name.
OWED_TYPES = ('credit_card', 'other_current_liability', 'long_term_liability')
EFFECT_WORDS = {'increase': 'goes up', 'decrease': 'goes down'}


def is_document(noun, verb):
    """Whether this command opens as a document window rather than a generated form."""
    return ((noun in ('invoice', 'sales-receipt') and verb in ('post', 'update'))
            or (noun == 'estimate' and verb in ('create', 'update'))
            or (noun in MONEY_OUT and verb == 'post')
            or (noun == BILL and verb in ('post', 'update'))
            or (noun == TRANSFER and verb == 'post'))


def heading(noun, verb, originals):
    number = (originals or {}).get('number')
    title = TITLES[noun]
    if verb in ('post', 'create'):
        return 'New ' + title.lower()
    return title + (' ' + str(number) if number else '') + ' — correction'


def describe(leaves, noun):
    """Human labels over the typed leaves; the wire names and controls stay untouched."""
    own_labels = NOUN_LABELS.get(noun, {})
    own_descriptions = NOUN_DESCRIPTIONS.get(noun, {})
    for leaf in leaves:
        path = leaf['path']
        tail = path.rsplit('.', 1)[-1]
        if path in own_labels:
            leaf['label'] = own_labels[path]
        elif path.startswith(('billing_address.', 'shipping_address.')):
            leaf['label'] = LABELS.get(tail, tail.replace('_', ' ').capitalize())
        elif path == 'number':
            leaf['label'] = NUMBER_LABELS[noun]
        elif path in LABELS:
            leaf['label'] = LABELS[path]
        elif not leaf.get('label'):
            leaf['label'] = tail.replace('_id', '').replace('_', ' ').capitalize()
        if leaf['kind'] == 'collection' and path == 'use_defaults':
            leaf['collection']['item']['choice_labels'] = DEFAULT_CHOICE_LABELS
        if path in ('lines', 'expenses'):
            for child in leaf['collection']['item']['fields']:
                child['label'] = LINE_LABELS.get(child['name'],
                                                 child['name'].replace('_id', '').replace('_', ' ').capitalize())
                if child['name'] in COLUMN_HINTS and not child.get('description'):
                    child['description'] = COLUMN_HINTS[child['name']]
                if child['kind'] == 'collection' and child['name'] == 'use_defaults':
                    child['collection']['item']['choice_labels'] = DEFAULT_CHOICE_LABELS
        if path in own_descriptions:
            leaf['description'] = own_descriptions[path]
        elif path in DESCRIPTIONS:
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
    transfer = noun == TRANSFER
    money_out = noun in MONEY_OUT
    bill = noun == BILL
    header_only = transfer or money_out
    # A bill has a terms band of its own but none of the sales bands: no addresses to print
    # on, no quoted scope, and no price rules, because nothing here is being sold.
    plain = header_only or bill
    grid = () if transfer else BILL_GRID if bill else EXPENSE_GRID if money_out else \
        WORK_GRID if noun == 'estimate' else SALE_GRID

    def take(paths):
        found = []
        for path in paths:
            leaf = by_path.get(path)
            if leaf is not None and path not in placed:
                placed.add(path)
                found.append(leaf)
        return found

    primary = take(BILL_PRIMARY if bill else TRANSFER_PRIMARY if transfer else
                   MONEY_OUT_PRIMARY if money_out else PRIMARY)
    terms = take(BILL_TERMS) if bill else [] if header_only else take(TERMS)
    addresses = [] if plain else [group for group in
                 (_address_group(title, prefix, leaves, placed)
                  for title, prefix in ADDRESSES.get(noun, DEFAULT_ADDRESSES))
                 if group is not None]
    scope = [] if plain else take(SCOPE)
    # A transfer has no line collection at all, which is what leaves the grid band out of
    # the page rather than rendering an empty one.
    lines = None if transfer else by_path.get('expenses' if money_out or bill else 'lines')
    if lines is not None:
        placed.add(lines['path'])
    footer = take(BILL_FOOTER if bill else TRANSFER_FOOTER if transfer else
                  MONEY_OUT_FOOTER if money_out else FOOTER)
    pricing = [] if plain else take(PRICING)
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
            'line_pricing': None if plain else LINE_PRICING,
            'lines_title': 'Expenses' if money_out or bill else 'Lines',
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


def _row(label, value, strong=False):
    return {'label': label, 'value': value, 'strong': strong}


def sale_totals(figures, settled):
    """The sales footer, unchanged: every figure copied from the server's own result."""
    if figures is None:
        return []
    rows = [_row('Subtotal', figures['subtotal']), _row('Tax', figures['tax']),
            _row('Total', f"{figures['total']} {figures['currency']}", True)]
    if settled and 'applied' in figures:
        rows += [_row('Payments Applied', figures['applied']),
                 _row('Balance Due', f"{figures['due']} {figures['currency']}", True)]
    return rows


def money_out_totals(noun, result, error):
    """The money-out footer: what the lines add up to, and whether it agrees with the face.

    Both branches are the server's arithmetic. The success branch copies the document summary
    the command returned; the refusal branch copies the three figures the refusal carried,
    which is how the footer can still show the reconciliation on the page that refused.
    """
    face = FACE_LABELS[noun]
    document = result.get('document') if isinstance(result, dict) else None
    if isinstance(document, dict):
        currency = document['currency']
        return ([_row('Expenses', f"{document['expense_total']['amount']} {currency}"),
                 _row(face, f"{document['amount']['amount']} {currency}", True)],
                'The expense lines add up to what this document is written for.', True)
    details = (error or {}).get('details') if isinstance(error, dict) else None
    if isinstance(details, dict) and isinstance(details.get('difference'), dict):
        currency = details['currency']
        over = details['difference_minor_units'] > 0
        return ([_row('Expenses', f"{details['expense_total']['amount']} {currency}"),
                 _row(face, f"{details['amount']['amount']} {currency}"),
                 _row('Over' if over else 'Short by',
                      f"{details['difference']['amount']} {currency}", True)],
                'The expense lines do not add up to what this document is written for. '
                'Nothing was written.', False)
    return [], None, None


def bill_totals(result):
    """The bill footer: what the lines add up to, what is owed, and when it falls due.

    Every figure is the server's own. A bill has no figure written on its face, so there is
    nothing to reconcile against the way a check has: the total *is* what the expense lines
    add up to, and the sentence under it says the two things a person opening a payable wants
    to know — when it is due, and that nothing here has been paid.
    """
    if not isinstance(result, dict) or not isinstance(result.get('total'), dict):
        return [], None, None
    currency = result['currency']
    rows = [_row('Expenses', f"{result['expense_total']['amount']} {currency}"),
            _row('Amount due', f"{result['total']['amount']} {currency}", True)]
    settlement = result.get('settlement_current')
    if isinstance(settlement, dict) and settlement.get('applied_minor_units'):
        rows += [_row('Paid so far', settlement['applied']['amount']),
                 _row('Still open', f"{settlement['open']['amount']} {currency}", True)]
    terms = ((result.get('revision') or {}).get('profile') or {}).get('terms')
    said = 'Due ' + str(result['due_date']) + (' on ' + terms['label'] if terms else '') + '.'
    return rows, said + ' Accounts Payable carries it until it is paid.', True


def _leg_figure(leg):
    """What this end's own figure is called: on a card or a loan it is what you owe."""
    return (f'what you owe on {leg["name"]}' if leg['type'] in OWED_TYPES else leg['name'])


def transfer_totals(result):
    """The transfer footer: where the money went, and what each end did.

    Every figure is copied from the document summary the command returned. A refusal
    computes nothing, so it shows nothing rather than a figure the server never produced.
    """
    document = result.get('document') if isinstance(result, dict) else None
    if not isinstance(document, dict):
        return [], None, None
    currency, source, target = document['currency'], document['from_account'], document['to_account']
    figure = f"{document['amount']['amount']} {currency}"
    said = (f'{_leg_figure(source)} {EFFECT_WORDS[source["effect"]]} {figure} and '
            f'{_leg_figure(target)} {EFFECT_WORDS[target["effect"]]} {figure}.')
    return ([_row('Out of ' + source['name'], figure),
             _row('Into ' + target['name'], figure, True)],
            said[0].upper() + said[1:] + ' Neither end is income or expense, so this changes '
            'no profit.', True)


def context(noun, verb, leaves, originals, *, shown=None, result=None, preview=False,
            record_id=None, base='', error=None):
    """Everything the document template needs, with money taken from the server alone."""
    transfer = noun == TRANSFER
    money_out = noun in MONEY_OUT
    bill = noun == BILL
    # A bill's line amounts are typed, not computed, so there is no per-line server figure to
    # copy back and no ``@amount`` column asking for one.
    figures = None if transfer or money_out or bill else (computed(result) or computed(shown))
    fresh = result is not None

    def line_amount(line_id, index):
        if figures is None:
            return None
        row = figures['by_line'].get(line_id) if line_id else None
        if row is None and fresh and index < len(figures['lines']):
            row = figures['lines'][index]
        return row

    if transfer:
        totals, reconciliation, reconciled = transfer_totals(result)
        empty = 'Preview to see what each of the two accounts does.'
    elif bill:
        totals, reconciliation, reconciled = bill_totals(result)
        empty = ('Preview to see what the expense lines add up to and when the terms make '
                 'this bill due.')
    elif money_out:
        totals, reconciliation, reconciled = money_out_totals(noun, result, error)
        empty = ('Preview to see what the expense lines add up to and whether it agrees '
                 'with the amount above.')
    else:
        totals = sale_totals(figures, noun == 'invoice')
        reconciliation, reconciled = None, None
        empty = 'Preview to see the subtotal, tax and total the server computes.'

    return dict(layout(noun, leaves),
                noun=noun, verb=verb, title=TITLES[noun],
                heading=heading(noun, verb, originals),
                help=HELP[noun], computed=figures, line_amount=line_amount,
                totals=totals, totals_empty=empty,
                reconciliation=reconciliation, reconciled=reconciled,
                preview=preview, creating=verb in ('post', 'create'),
                settled=noun == 'invoice', base=base, record_id=record_id,
                context_labels=CONTEXT_LABELS,
                origin=('the values you entered' if (transfer or money_out or bill) and not fresh
                        else 'the last preview' if fresh else 'the saved document'))
