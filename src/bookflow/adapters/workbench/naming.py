"""What a person reads at the top of a page and across the head of a table.

Presentation only. Nothing here reads, writes, renames a command or changes a form
control: the wire name of every command, field and column is untouched. This module
decides one thing — the words shown to a human where the raw name of the machinery
would otherwise be shown.

**Pages are titled with the work, not with the command that does it.** That mechanism
already exists: ``document_form.heading`` gives the three sales documents "New invoice"
instead of ``invoice post``, and it stays there because those titles carry the
document's own number. ``heading`` below is the same function for every other page, so
no page anywhere is titled ``report ar-aging`` or ``customer create``.

**Columns and fields are humanised by rule, not by list.** The general fix comes
first — the storage suffixes and the underscores come off and what is left is
capitalised, which corrects a whole class of names at once rather than one at a time.
Only a name a person would not recognise from its own field gets an entry, and those
entries are read from the maps already written for the sales document window and for
the master lists rather than started again here.
"""
from bookflow.adapters.workbench import document_form as Document
from bookflow.company.query_catalog import LABELS as CATALOG_LABELS

# Reports are named the way a bookkeeper asks for them, which is not the command's verb.
REPORTS = {
    'ar-aging': 'A/R aging summary',
    'open-invoices': 'Open invoices',
    'ap-aging': 'A/P aging summary',
    'unpaid-bills': 'Unpaid bills',
    'statement': 'Customer statement',
    'sales-by-customer': 'Sales by customer',
    'sales-by-item': 'Sales by item',
    'sales-by-rep': 'Sales by rep',
    'expenses-by-vendor': 'Expenses by vendor',
    'trial-balance': 'Trial balance',
    'general-ledger': 'General ledger',
    'transaction-detail': 'Transaction detail by account',
    'missing-checks': 'Missing checks',
    'profit-and-loss': 'Profit and loss',
    'balance-sheet': 'Balance sheet',
    'cash-flows': 'Statement of cash flows',
    'income-tax-summary': 'Income tax summary',
    'inventory-valuation': 'Inventory valuation summary',
    'stock-status': 'Inventory stock status by item',
}

# Pages whose title is neither the noun's own name nor a phrase the verb map knows. The
# inventory nouns are the case the general rule cannot reach: "inventory" is already plural
# in a bookkeeper's mouth, and "void this inventory" is not what the page does.
PAGES = {
    'inventory adjust': 'Adjust inventory',
    'inventory void': 'Void an inventory adjustment',
    'inventory show': 'Inventory adjustment',
}

# The verbs a page is opened by, phrased as the work rather than as the instruction.
# {subject} is this noun's own name; {plural} is the same name for many of them.
PHRASES = {
    'create': 'New {subject}', 'new': 'New {subject}', 'post': 'New {subject}',
    'add': 'New {subject}', 'issue': 'New {subject}', 'set': 'New {subject}',
    'receive': 'Receive a {subject}',
    'update': 'Edit this {subject}', 'edit': 'Edit this {subject}',
    'rename': 'Rename this {subject}',
    'show': '{Subject}', 'list': '{Plural}', 'query': 'Find {plural}',
    'options': 'What {plural} can be searched by',
    'history': 'History of this {subject}',
    'void': 'Void this {subject}', 'copy': 'Copy this {subject}',
    'delete': 'Delete this {subject}',
    'delete': 'Take this {subject} off the list',
    'enter': 'Enter this {subject} now', 'process': 'Enter every {subject} that is due',
    'retry': 'Try this {subject} again', 'skip': 'Set this {subject} aside',
    'activate': 'Make this {subject} active again',
    'deactivate': 'Make this {subject} inactive',
    'convert': 'Convert this {subject}',
    'complete': 'Complete this {subject}',
    'billing': 'Bill this {subject}',
    'settlement': 'Settlement of this {subject}',
    'children': 'Rows within this {subject}',
}

# A handful of verbs mean something different under one noun than they mean everywhere else.
# `add` is "new" on almost every noun and "put customers in" on a billing group, so a title
# taken from the verb alone would call the membership form "New billing group". Keyed by the
# pair rather than by the verb, and consulted before the general map.
NOUN_PHRASES = {
    ('billing-group', 'add'): 'Add customers to this {subject}',
    ('billing-group', 'remove'): 'Take customers out of this {subject}',
    ('batch-invoice', 'retry'): 'Invoice the customers this batch missed',
}


# Column names a person would not recognise from the field's own words.
COLUMNS = {'customer_name': 'Customer', 'display_customer_label': 'Customer',
           'vendor_name': 'Vendor',
           'total': 'Total', 'net': 'Net', 'tax': 'Tax', 'number': 'Number',
           'memo': 'Memo', 'status': 'Status', 'rate': 'Rate', 'source': 'Source',
           'from_currency': 'From currency', 'to_currency': 'To currency'}

# Storage suffixes that name how a value is stored, never what it is.
SUFFIXES = ('_minor_units', '_micro_units', '_millionths', '_id', '_label')


def words(value):
    """The general fix: separators become spaces and the first letter is a capital."""
    return str(value).replace('_', ' ').replace('-', ' ').strip().capitalize()


def subject(noun, meta=None, plural=False):
    """This noun's own name for a person, singular or plural."""
    label = (meta or {}).get('plural_label' if plural else 'singular_label')
    if label:
        return label
    name = words(noun)
    return (name + 's') if plural else name


def heading(noun, verb, meta=None):
    """The title of a page, in the words of the work it does.

    Never a command name: a verb this does not know is still shown as the noun and
    what is being done to it, in words, rather than as ``noun verb``.
    """
    if noun == 'report':
        return REPORTS.get(verb) or words(verb)
    if (noun + ' ' + verb) in PAGES:
        return PAGES[noun + ' ' + verb]
    one, many = subject(noun, meta), subject(noun, meta, plural=True)
    if not verb:
        return one
    phrase = NOUN_PHRASES.get((noun, verb)) or PHRASES.get(verb)
    if phrase is None:
        return one + ' — ' + words(verb).lower()
    return phrase.format(subject=one.lower(), Subject=one, plural=many.lower(), Plural=many)


def list_heading(noun, meta=None):
    """The title of a list page: the records themselves, named for a person."""
    return subject(noun, meta, plural=True)


def _trim(key):
    for suffix in SUFFIXES:
        key = key.removesuffix(suffix)
    return key


def column_label(key):
    """The head of one list column.

    The rule carries it, and the maps only correct it, so a column nobody has ever
    named still arrives readable instead of arriving as its field name.
    """
    for name in dict.fromkeys((key, _trim(key))):
        for source in (COLUMNS, CATALOG_LABELS, Document.LABELS, Document.LINE_LABELS):
            if name in source:
                return source[name]
    return words(_trim(key))
