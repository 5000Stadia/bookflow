"""Import kinds (R107, design/bulk-import.md §2): what a file can become, as declarations over registry commands.

An **import kind** names the registry command that creates one record (and the one that updates it), its natural key,
which fields are money or dates, and header synonyms. Mappable fields are the command's own input paths
(``billing_address.city``), read from its input model, so a field the importer never heard of still maps. A kind adds no
write path: every row runs through its command, validated, permission-checked and audited as a typed one.

**Completeness is a test** (``tests/test_imports.py``): every registry write command that writes only the company
database is a kind's create or update command, a document kind planned for a later increment (``PLANNED``), or in
``NOT_IMPORTABLE`` with a reason. A new command that is none of these fails the build.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from bookflow import BookflowError


@dataclass(frozen=True)
class Kind:
    name: str
    noun: str
    money: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()
    decimals: tuple[str, ...] = ()
    synonyms: dict[str, str] = field(default_factory=dict)
    key_default: tuple[str, ...] = ('name',)
    summary: str = ''

    @property
    def create(self) -> str:
        return f'{self.noun} create'

    @property
    def update(self) -> str:
        return f'{self.noun} update'

    @property
    def selector(self) -> str:
        """The input field its show, update and deactivate commands select a record by (`customer_type`)."""
        return self.noun.replace('-', '_')

    @property
    def definition(self):
        from bookflow.company import lists
        return lists.get_list_definition(self.noun)

    @property
    def hierarchical(self) -> bool:
        return self.definition.hierarchy is not None


_PARTY = {'customer': 'name', 'customer name': 'name', 'vendor': 'name', 'vendor name': 'name', 'supplier': 'name',
          'supplier name': 'name', 'company': 'company_name', 'e-mail': 'email', 'email address': 'email',
          'e-mail address': 'email', 'mail': 'email',
          'phone number': 'phone', 'telephone': 'phone', 'tel': 'phone', 'mobile': 'alt_phone', 'terms': 'terms_id',
          'payment terms': 'terms_id', 'pay terms': 'terms_id', 'note': 'notes', 'acct no': 'account_number',
          'account no': 'account_number', 'tax id': 'resale_number', 'web': 'website', 'parent': 'parent_id',
          'job of': 'parent_id', 'customer type': 'customer_type_id', 'vendor type': 'vendor_type_id',
          'rep': 'sales_rep_id', 'sales rep': 'sales_rep_id', 'class': 'default_class_id',
          'price level': 'price_level_id', 'tax code': 'sales_tax_code_id', 'contact name': 'contact'}
# A customer's own words: what a client list or a job sheet calls its columns.
_CUSTOMER = {'client': 'name', 'client name': 'name', 'customer/job': 'name', 'job started': 'job_start',
             'job start': 'job_start', 'job start date': 'job_start', 'start date': 'job_start',
             'job ended': 'job_end', 'job end': 'job_end', 'job end date': 'job_end',
             'projected end': 'job_projected_end', 'job status': 'job_status', 'job type': 'job_type_id',
             'job description': 'job_description'}
_ADDRESS = {'address': 'line1', 'address 1': 'line1', 'address1': 'line1', 'street': 'line1', 'address line 1': 'line1',
            'address 2': 'line2', 'address2': 'line2', 'address line 2': 'line2', 'city': 'city', 'town': 'city',
            'state': 'state', 'province': 'state', 'region': 'state', 'zip': 'postal_code', 'zip code': 'postal_code',
            'postcode': 'postal_code', 'postal code': 'postal_code', 'country': 'country'}

KINDS: dict[str, Kind] = {k.name: k for k in (
    Kind('customer', 'customer', money=('credit_limit',), dates=('job_start', 'job_projected_end', 'job_end'),
         synonyms={**_PARTY, **_CUSTOMER, **{k: 'billing_address.' + v for k, v in _ADDRESS.items()}},
         summary='Customers and jobs (a job names its customer in `parent_id`).'),
    Kind('vendor', 'vendor', money=('credit_limit',),
         synonyms={**_PARTY, **{k: 'address.' + v for k, v in _ADDRESS.items()}}, summary='Vendors.'),
    Kind('item', 'item', money=('price', 'cost', 'original_cost', 'disposal_proceeds', 'disposal_costs',
                                'discount_amount', 'book_basis', 'tax_basis'),
         dates=('purchase_date', 'warranty_expiration', 'disposal_date'),
         decimals=('charge_percent', 'discount_percent', 'tax_percent', 'reorder_point_min', 'reorder_point_max',
                   'unit_weight', 'unit_volume', 'assembly_build_point'),
         synonyms={'item': 'name', 'item name': 'name', 'product': 'name', 'sku': 'name', 'item type': 'type',
                   'sales price': 'price', 'rate': 'price', 'unit price': 'price', 'purchase cost': 'cost',
                   'income account': 'income_account_id', 'expense account': 'expense_account_id',
                   'cogs account': 'cogs_account_id', 'asset account': 'asset_account_id', 'upc': 'barcode',
                   'mpn': 'manufacturer_part_number', 'preferred vendor': 'preferred_vendor_id',
                   'sales description': 'description', 'purchase description': 'purchase_description',
                   'parent': 'parent_id', 'subitem of': 'parent_id', 'category': 'category_id'},
         summary='Items of every type a create command takes (service, inventory part, non-inventory part...).'),
    Kind('account', 'account', synonyms={'account': 'name', 'account name': 'name', 'account number': 'number',
                                         'acct num': 'number', 'no': 'number', 'account type': 'type',
                                         'subaccount of': 'parent_id', 'parent': 'parent_id'},
         key_default=('number',), summary='The chart of accounts; keyed by number, else by name.'),
    Kind('term', 'term', decimals=('discount_percent',),
         synonyms={'terms': 'name', 'net due in days': 'due_days', 'net due': 'due_days', 'days': 'due_days',
                   'discount %': 'discount_percent', 'discount if paid within': 'discount_days'},
         summary='Payment terms.'),
    Kind('class', 'class', synonyms={'class': 'name', 'subclass of': 'parent_id', 'parent': 'parent_id'},
         summary='Classes.'),
    Kind('customer-type', 'customer-type', synonyms={'customer type': 'name', 'type': 'name', 'parent': 'parent_id'},
         summary='Customer types.'),
    Kind('vendor-type', 'vendor-type', synonyms={'vendor type': 'name', 'type': 'name', 'parent': 'parent_id'},
         summary='Vendor types.'),
    Kind('job-type', 'job-type', synonyms={'job type': 'name', 'type': 'name', 'parent': 'parent_id'},
         summary='Job types.'),
    Kind('payment-method', 'payment-method', synonyms={'payment method': 'name', 'method': 'name', 'type': 'kind'},
         summary='Payment methods.'),
    Kind('ship-method', 'ship-method', synonyms={'ship method': 'name', 'shipping method': 'name', 'via': 'name'},
         summary='Ship methods.'),
    Kind('sales-rep', 'sales-rep', synonyms={'sales rep': 'name', 'rep': 'name', 'rep name': 'name_id',
                                             'name type': 'name_type'},
         summary='Sales reps: initials and the employee, vendor or other name they are (`name_type`, `name_id`).'),
    Kind('price-level', 'price-level', decimals=('percent', 'rounding_increment', 'rounding_offset'),
         synonyms={'price level': 'name', 'type': 'kind', '%': 'percent'},
         summary='Price levels (fixed percent; per-item prices are added afterwards).'),
    Kind('item-category', 'item-category', synonyms={'category': 'name', 'parent': 'parent_id'},
         summary='Item categories.'),
    Kind('customer-message', 'customer-message', synonyms={'message': 'name'}, summary='Customer messages.'),
    Kind('employee', 'employee', synonyms={**_PARTY, 'employee': 'name', 'employee name': 'name'},
         summary='Employees as names (no payroll).'),
    Kind('other-name', 'other-name', synonyms={**_PARTY, 'other name': 'name'}, summary='Other names.'),
)}

# Document kinds of design/bulk-import.md §2, built in increment 4.
PLANNED = frozenset(('invoice post', 'bill post', 'credit-memo post', 'vendor-credit post', 'sales-receipt post',
                     'journal post', 'payment receive', 'bill pay', 'check post', 'card-charge post', 'estimate create',
                     'purchase-order post', 'sales-order create', 'inventory adjust'))

REASONS = {
    'delete': 'deletes records; no import or bulk edit reaches a delete',
    'void': 'voids or cancels a document',
    'merge': 'merges or unmerges records',
    'restore': 'restores deleted or replaced records',
    'destructive': 'deactivates, removes, closes or discards; nothing deactivates through a mapping',
    'needs-a-person': 'an act a person takes at the time: approving, printing, recovering, exporting',
    'not-yet': 'makes a record no import kind covers yet',
    'not-a-record': 'an operation on records that exist, not a record a file row describes',
}

# Generated once from the registry and kept by hand since: see the module docstring.
NOT_IMPORTABLE: dict[str, frozenset[str]] = {
    'delete': frozenset((
        'assembly delete', 'bill delete', 'billing-group delete', 'budget delete', 'card-charge delete',
        'check delete', 'credit-memo delete', 'deposit delete', 'invoice delete', 'journal delete',
        'memorized delete', 'memorized-group delete', 'memorized-report delete', 'mileage-rate delete',
        'payment delete', 'sales-receipt delete',
    )),
    'void': frozenset((
        'assembly void', 'bill payment void', 'bill void', 'card-charge void', 'card-credit void',
        'check print-cancel', 'check void', 'credit-memo void', 'customer-credit transfer-void',
        'customer-refund void', 'deposit void', 'estimate void', 'inventory void', 'inventory-count void',
        'inventory-transfer void', 'invoice void', 'item-receipt void', 'journal void', 'landed-cost void',
        'payment void', 'purchase-order void', 'sales-order void', 'sales-receipt void', 'sales-tax adjustment void',
        'sales-tax payment void', 'shipment void', 'shipment-return void', 'statement-charge void',
        'time-activity void', 'transfer void', 'trip void', 'vendor-credit void',
        'delivery cancel', 'shipment cancel', 'delivery batch-cancel',
    )),
    'merge': frozenset((
        'account merge', 'account unmerge', 'customer merge', 'customer unmerge', 'item merge', 'item unmerge',
        'vendor merge', 'vendor unmerge',
    )),
    'restore': frozenset((
        'invoice restore', 'journal restore',
    )),
    'destructive': frozenset((
        'account deactivate', 'accountant-copy cancel', 'attachment unlink', 'bill payment unapply',
        'billing-group remove', 'bin deactivate', 'budget clear', 'class deactivate', 'clock cancel',
        'company compact', 'costing-method withdraw', 'custom-field deactivate', 'customer deactivate',
        'customer unlink-vendor', 'customer-credit unapply', 'customer-message deactivate',
        'customer-type deactivate', 'cycle-class deactivate', 'directive deactivate', 'employee deactivate',
        'inventory-count cancel', 'inventory-site deactivate', 'item deactivate', 'item-category deactivate',
        'job-type deactivate', 'memorized skip', 'notice dismiss', 'other-name deactivate', 'payment recovery abort',
        'payment selection clear', 'payment unapply', 'payment-method deactivate', 'pick-list cancel',
        'pick-list close', 'prepayment unapply', 'price-level deactivate', 'price-rule deactivate',
        'purchase-order close-lines', 'reconcile discard', 'sales-rep deactivate', 'sales-tax-code deactivate',
        'schedule cancel', 'ship-method deactivate', 'term deactivate', 'unit-of-measure deactivate',
        'vendor deactivate', 'vendor-credit unapply', 'vendor-type deactivate',
        'delivery-template deactivate', 'delivery-connection disable', 'delivery-connection remove',
        'delivery-schedule pause',
    )),
    'needs-a-person': frozenset((
        'accountant mark-entry', 'accountant reviewed-through', 'accountant-copy export', 'assembly finalize',
        'check print', 'check print-confirm', 'check print-setup', 'check reprint', 'company backup',
        'payment recovery apply', 'payment recovery begin', 'payment recovery replace', 'payment recovery seal',
        'payment recovery upload', 'price-rule set-preferences', 'timesheet set-preferences',
        'delivery approve', 'delivery send', 'delivery mark-sent', 'delivery export', 'delivery retry', 'delivery run',
        'delivery-connection add', 'delivery-connection update', 'delivery-connection enable',
        'delivery-connection check', 'delivery-schedule live',
    )),
    'not-yet': frozenset((
        'attachment add', 'batch-invoice post', 'billing-group add', 'billing-group create', 'bin create',
        'budget create', 'budget set', 'card-credit post', 'costing-method set', 'custom-field create',
        'customer-refund post', 'cycle-class create', 'deposit post', 'directive add', 'fixed-asset setup',
        'inventory-count create', 'inventory-count enter', 'inventory-count post', 'inventory-site create',
        'inventory-transfer post', 'item-receipt post', 'landed-cost post', 'landed-cost setup', 'loan create',
        'loan pay', 'memorized create', 'memorized enter', 'memorized-group create', 'memorized-group enter',
        'memorized-report create', 'mileage-rate set', 'note add', 'payment selection create', 'pick-list create',
        'pick-list enter', 'prepayment receive', 'price-rule create', 'proposal create', 'rate set', 'register post',
        'sales-tax pay', 'sales-tax-code create', 'schedule create', 'shipment post', 'statement-charge post',
        'time-activity create', 'todo create', 'transfer post', 'trip create', 'unit-of-measure create',
        'vehicle create', 'work-order create',
        'delivery draft', 'delivery-template create', 'shipment create', 'delivery-schedule create',
    )),
    'not-a-record': frozenset((
        'import preview', 'import run', 'import cancel', 'import reverse', 'import decide', 'import-mapping save',
        'import-mapping deactivate', 'bank-feed import', 'bank-feed reverse', 'bank-feed move',
        'bank-line match', 'bank-line unmatch', 'bank-line ignore', 'bank-line add', 'bank-line transfer',
        'bank-line undo',
        'account activate', 'accountant reclassify', 'accountant-copy changes', 'accountant-copy import',
        'assembly build', 'assembly update', 'attachment link', 'barcode generate', 'batch-invoice retry',
        'bill payment apply', 'bill update', 'billing-group rename', 'bin activate', 'bin update', 'budget fill',
        'budget rename', 'card-charge update', 'card-credit update', 'chart apply', 'check update', 'class activate',
        'clock in', 'clock link', 'clock out', 'credit-memo update', 'custom-field activate', 'custom-field update',
        'customer activate', 'customer link-vendor', 'customer-credit apply', 'customer-credit transfer',
        'customer-message activate', 'customer-refund update', 'customer-type activate', 'cutover apply',
        'cycle-class activate', 'cycle-class update', 'cycle-count classify', 'deposit update', 'employee activate',
        'estimate copy', 'estimate invoice', 'estimate sales-order', 'estimate sales-receipt', 'estimate update',
        'estimate work-order', 'finance-charge assess', 'fixed-asset depreciate', 'fixed-asset dispose',
        'inventory assign', 'inventory-site activate', 'inventory-site update', 'inventory-transfer update',
        'invoice update', 'item activate', 'item convert', 'item-category activate', 'item-receipt update',
        'job-type activate', 'journal update', 'loan update', 'lot update', 'memorized process', 'memorized retry',
        'memorized update', 'memorized-group update', 'memorized-report update', 'note edit', 'notice generate',
        'notice mark-sent', 'other-name activate', 'other-name convert', 'payment apply', 'payment bounce',
        'payment selection update', 'payment update', 'payment-method activate', 'prepayment apply',
        'prepayment attach', 'prepayment refund', 'price-level activate', 'price-rule activate', 'price-rule update',
        'profile apply', 'proposal copy', 'proposal estimate', 'proposal update', 'purchase-order update',
        'reconcile finish', 'reconcile import', 'reconcile mark', 'reconcile opening start', 'reconcile start',
        'register update', 'review mark', 'sales-order copy', 'sales-order invoice', 'sales-order purchase-order',
        'sales-order update', 'sales-receipt update', 'sales-rep activate', 'sales-tax adjust',
        'sales-tax-code activate', 'sales-tax-code update', 'schedule update', 'serial-number update',
        'ship-method activate', 'shipment invoice', 'shipment return', 'shipment update', 'term activate',
        'time-activity invoice', 'time-activity sales-receipt', 'time-activity update', 'timesheet save',
        'todo complete', 'todo update', 'transfer update', 'trip invoice', 'trip reimburse', 'trip sales-receipt',
        'trip update', 'undo', 'unit-of-measure activate', 'unit-of-measure update', 'vehicle update',
        'vendor 1099-opening', 'vendor activate', 'vendor-credit apply', 'vendor-credit refund',
        'vendor-credit update', 'vendor-type activate', 'work-order complete', 'work-order copy',
        'work-order invoice', 'work-order purchase-order', 'work-order sales-receipt', 'work-order update',
        'delivery update', 'delivery-template update', 'shipment pack',
        'delivery flag', 'delivery unflag', 'delivery batch',
        'delivery-schedule update', 'delivery-schedule resume', 'delivery-schedule run',
    )),
}


def kind(name: str) -> Kind:
    try:
        return KINDS[name]
    except KeyError:
        raise BookflowError('E_VALIDATION', message=(f'No import kind {name!r}; `import kinds` lists them: '
                                                     + ', '.join(sorted(KINDS)) + '.'),
                            details={'field': 'kind', 'value': name, 'kinds': sorted(KINDS)}) from None


# ------------------------------------------------------------------ fields, from the command's own input model

@dataclass(frozen=True)
class FieldSpec:
    path: str
    type: str  # string, boolean, integer, enum, money, date, decimal, reference, any
    required: bool
    enum: tuple[str, ...] = ()
    target: str | None = None  # the list a reference resolves in
    description: str = ''
    max_length: int | None = None
    min_length: int | None = None
    pattern: str | None = None
    minimum: float | None = None
    maximum: float | None = None

    @property
    def multiline(self) -> bool:
        """Long text (notes, descriptions: no length limit, or 1,000 characters or more) keeps tabs and line breaks."""
        return self.type in ('string', 'any') and (self.max_length is None or self.max_length >= 1000)


def _resolve_ref(schema: dict, node: dict) -> dict:
    if '$ref' in node:
        return schema['$defs'][node['$ref'].split('/')[-1]]
    return node


def _branches(schema: dict, node: dict) -> list[dict]:
    if 'anyOf' in node:
        return [_resolve_ref(schema, b) for b in node['anyOf'] if b.get('type') != 'null']
    return [_resolve_ref(schema, node)]


@lru_cache(maxsize=None)
def fields(kind_name: str) -> dict[str, FieldSpec]:
    """Every scalar input path of the kind's create command, typed for the reader."""
    from bookflow.core import registry
    k = kind(kind_name)
    model = registry.get(k.create).input_model
    schema = model.model_json_schema()
    required = set(schema.get('required', ()))
    refs = {r.field: r.target for r in k.definition.references if '|' not in r.target}
    for name in schema['properties']:
        if name.endswith('_account_id') and name not in refs:
            refs[name] = 'account'
        elif name == 'vendor_id' and name not in refs:
            refs[name] = 'vendor'
    out: dict[str, FieldSpec] = {}

    def add(path, node, is_required):
        branches = _branches(schema, node)
        branch = branches[0] if branches else {}
        leaf = path.rsplit('.', 1)[-1]
        if branch.get('type') == 'array':
            return
        if branch.get('type') == 'object' and 'properties' in branch:
            inner = set(branch.get('required', ()))
            for name, sub in branch['properties'].items():
                add(f'{path}.{name}', sub, is_required and name in inner)
            return
        if branch.get('type') == 'object':
            return  # free maps (custom fields) map as custom_fields.<name>, below
        if path in k.money:
            kind_ = 'money'
        elif path in k.dates:
            kind_ = 'date'
        elif path in k.decimals:
            kind_ = 'decimal'
        elif path in refs and '.' not in path:
            kind_ = 'reference'
        elif 'enum' in branch:
            kind_ = 'enum'
        elif branch.get('type') in ('boolean', 'integer', 'string'):
            kind_ = branch['type']
        else:
            kind_ = 'any'
        target = refs.get(path) if kind_ == 'reference' else None
        out[path] = FieldSpec(path, kind_, is_required, tuple(branch.get('enum', ())), target,
                              (node.get('description') or '')[:200], branch.get('maxLength'), branch.get('minLength'),
                              branch.get('pattern'), branch.get('minimum'), branch.get('maximum'))
        del leaf

    for name, node in schema['properties'].items():
        add(name, node, name in required)
    return out


@lru_cache(maxsize=None)
def required(kind_name: str, mode: str = 'create') -> tuple[str, ...]:
    """What a row cannot go without: exactly the fields the single command requires, read from its input model (never a
    separate list), so the import is never stricter than typing. An update needs only what selects its record."""
    from bookflow.core import registry
    k = kind(kind_name)
    if mode == 'update':
        return ()
    model = registry.get(k.create).input_model
    return tuple(name for name, info in model.model_fields.items() if info.is_required())


def path_ok(kind_name: str, path: str) -> bool:
    return path in fields(kind_name) or (path.startswith('custom_fields.') and len(path) > 14)


def _norm(text: str) -> str:
    return re.sub(r'[^0-9a-z%]+', ' ', (text or '').casefold()).strip()


def guess(kind_name: str, headers: list[str]) -> list[dict]:
    """Each header against the kind's paths, labels and synonyms: the guessed path and how sure."""
    k = kind(kind_name)
    specs = fields(kind_name)
    labels: dict[str, str] = {}
    for path in specs:
        for label in (path, path.replace('_id', ''), path.rsplit('.', 1)[-1],
                      path.rsplit('.', 1)[-1].replace('_id', '')):
            labels.setdefault(_norm(label.replace('_', ' ').replace('.', ' ')), path)
    synonyms = {_norm(key): value for key, value in k.synonyms.items() if path_ok(kind_name, value)}
    taken: set[str] = set()
    out = []
    for header in headers:
        n = _norm(header)
        path, confidence = None, None
        if n in synonyms and synonyms[n] not in taken:
            path, confidence = synonyms[n], 'synonym'
        elif n in labels and labels[n] not in taken:
            path, confidence = labels[n], 'exact'
        if path:
            taken.add(path)
        out.append({'header': header, 'path': path, 'confidence': confidence})
    return out
