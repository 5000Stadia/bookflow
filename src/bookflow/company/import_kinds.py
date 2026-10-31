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



# ------------------------------------------------------------------ document kinds (increment 4)

@dataclass(frozen=True)
class DocKind:
    """A document a file's rows become: rows sharing the group key (an invoice's number) are its lines, in file
    order; its header fields come from the first row that fills them, and a later row that disagrees refuses the
    whole document (``header_conflict``). Documents are made, never matched or changed: create mode only."""
    name: str
    command: str
    family: str  # the record type its batch effects and keys name (transactions.type, or work_documents.kind)
    noun: str  # its show command's noun (`bill payment show`)
    selector: str  # the input field its show and inverse commands select it by
    inverse: str  # what reversal runs: the family's delete (history preserved), else its void
    lines: tuple[str, ...] = ('lines',)
    key_default: tuple[str, ...] = ('number',)
    key_core: tuple[str, ...] = ('number',)  # a key needs these mapped; otherwise rows are keyed by content
    synonyms: dict[str, str] = field(default_factory=dict)
    summary: str = ''
    table: str = 'transactions'
    type_column: str | None = 'type'  # None: the table holds this kind alone (purchase_orders)
    money: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()
    decimals: tuple[str, ...] = ()
    document = True

    @property
    def create(self) -> str:
        return self.command

    @property
    def update(self) -> str:
        return self.command  # documents are never updated by an import: create mode only

    @property
    def definition(self):
        from types import SimpleNamespace
        return SimpleNamespace(record_type=self.family, references=(), hierarchy=None, noun=self.name)

    @property
    def hierarchical(self) -> bool:
        return False


def is_document(k) -> bool:
    return getattr(k, 'document', False)


_SALE = {'invoice': 'number', 'invoice no': 'number', 'invoice number': 'number', 'invoice #': 'number',
         'num': 'number', 'no': 'number', 'number': 'number', 'ref no': 'number', 'customer': 'customer',
         'customer name': 'customer', 'client': 'customer', 'customer job': 'customer', 'customer/job': 'customer',
         'name': 'customer', 'date': 'date', 'invoice date': 'date', 'txn date': 'date', 'due date': 'due_date',
         'terms': 'terms', 'memo': 'memo', 'po no': 'customer_purchase_order', 'po number': 'customer_purchase_order',
         'item': 'lines[].item', 'product': 'lines[].item', 'product/service': 'lines[].item', 'sku': 'lines[].item',
         'description': 'lines[].description', 'qty': 'lines[].quantity', 'quantity': 'lines[].quantity',
         'rate': 'lines[].unit_price', 'price': 'lines[].unit_price', 'unit price': 'lines[].unit_price',
         'amount': 'lines[].net_amount', 'line amount': 'lines[].net_amount', 'class': 'class_id',
         'rep': 'sales_rep', 'sales rep': 'sales_rep', 'ship via': 'ship_method', 'ar account': 'ar_account',
         'tax code': 'lines[].tax_code'}
_PURCHASE = {'vendor': 'vendor', 'vendor name': 'vendor', 'supplier': 'vendor', 'name': 'vendor', 'date': 'date',
             'bill date': 'date', 'due date': 'due_date', 'terms': 'terms', 'memo': 'memo', 'ref no': 'supplier_reference',
             'reference': 'supplier_reference', 'vendor invoice': 'supplier_reference', 'bill no': 'number',
             'num': 'number', 'number': 'number', 'account': 'expenses[].account', 'expense account': 'expenses[].account',
             'amount': 'expenses[].amount', 'line memo': 'expenses[].memo', 'item': 'items[].item',
             'qty': 'items[].quantity', 'quantity': 'items[].quantity', 'cost': 'items[].unit_cost',
             'unit cost': 'items[].unit_cost', 'ap account': 'ap_account', 'class': 'class_id'}
_SPEND = {'account': 'account', 'bank account': 'account', 'card': 'account', 'payee': 'pay_to.name_id',
          'pay to': 'pay_to.name_id', 'date': 'date', 'amount': 'amount', 'total': 'amount', 'memo': 'memo',
          'check no': 'number', 'num': 'number', 'number': 'number', 'expense account': 'expenses[].account',
          'line amount': 'expenses[].amount', 'line memo': 'expenses[].memo', 'item': 'items[].item',
          'qty': 'items[].quantity', 'cost': 'items[].unit_cost'}
_ORDER = {**{k: v for k, v in _SALE.items() if k != 'invoice'}, 'estimate': 'number', 'order': 'number',
          'order no': 'number', 'title': 'title', 'expires': 'expires_on'}

DOC_KINDS: dict[str, DocKind] = {k.name: k for k in (
    DocKind('invoice', 'invoice post', 'invoice', 'invoice', 'invoice', 'invoice delete', synonyms=_SALE,
            summary='Invoices: rows sharing a number are one invoice, its lines in file order.'),
    DocKind('credit-memo', 'credit-memo post', 'credit_memo', 'credit-memo', 'credit_memo', 'credit-memo delete',
            synonyms={**_SALE, 'credit no': 'number'}, summary='Credit memos, grouped by number.'),
    DocKind('sales-receipt', 'sales-receipt post', 'sales_receipt', 'sales-receipt', 'sales_receipt',
            'sales-receipt delete', synonyms={**_SALE, 'deposit to': 'deposit_to', 'payment method': 'payment_method'},
            summary='Sales receipts, grouped by number.'),
    DocKind('estimate', 'estimate create', 'estimate', 'estimate', 'estimate', 'estimate void', synonyms=_ORDER,
            table='work_documents', type_column='kind', summary='Estimates, grouped by number.'),
    DocKind('sales-order', 'sales-order create', 'sales_order', 'sales-order', 'sales_order', 'sales-order void',
            synonyms=_ORDER, table='work_documents', type_column='kind', summary='Sales orders, grouped by number.'),
    DocKind('bill', 'bill post', 'bill', 'bill', 'bill', 'bill delete', lines=('expenses', 'items'),
            key_default=('vendor', 'supplier_reference'), key_core=('vendor', 'supplier_reference'),
            synonyms=_PURCHASE, summary="Bills, grouped by vendor and the vendor's reference; a row's account and "
                                        'amount make an expense line, its item an item line.'),
    DocKind('vendor-credit', 'vendor-credit post', 'vendor_credit', 'vendor-credit', 'credit', 'vendor-credit void',
            lines=('expenses', 'items'), key_default=('vendor', 'supplier_reference'),
            key_core=('vendor', 'supplier_reference'), synonyms=_PURCHASE,
            summary="Vendor credits, grouped by vendor and the vendor's reference."),
    DocKind('purchase-order', 'purchase-order post', 'purchase_order', 'purchase-order', 'purchase_order',
            'purchase-order void', synonyms={**_PURCHASE, 'po no': 'number', 'item': 'lines[].item',
                                             'qty': 'lines[].quantity', 'quantity': 'lines[].quantity',
                                             'cost': 'lines[].rate', 'rate': 'lines[].rate'},
            table='purchase_orders', type_column=None, summary='Purchase orders, grouped by number.'),
    DocKind('journal', 'journal post', 'journal_entry', 'journal', 'journal', 'journal delete',
            synonyms={'entry no': 'number', 'num': 'number', 'number': 'number', 'date': 'date', 'memo': 'memo',
                      'account': 'lines[].account', 'debit/credit': 'lines[].side', 'side': 'lines[].side',
                      'amount': 'lines[].amount', 'line memo': 'lines[].description',
                      'description': 'lines[].description', 'class': 'lines[].class_id',
                      'debit': 'lines[].debit', 'credit': 'lines[].credit'},
            summary='Journal entries, grouped by number; each row a debit or credit line.'),
    DocKind('check', 'check post', 'check', 'check', 'check', 'check delete', lines=('expenses', 'items'),
            key_default=('account', 'number'), key_core=('account', 'number'), synonyms=_SPEND,
            summary='Checks, grouped by bank account and check number.'),
    DocKind('card-charge', 'card-charge post', 'card_charge', 'card-charge', 'card_charge', 'card-charge delete',
            lines=('expenses', 'items'), key_default=(), key_core=(), synonyms=_SPEND,
            summary='Card charges; a file has no number for them, so each row is one charge keyed by its content.'),
    DocKind('payment', 'payment receive', 'payment', 'payment', 'payment', 'payment delete', lines=('applications',),
            key_default=('customer', 'date', 'amount', 'reference', 'number'), key_core=('customer', 'date', 'amount'),
            synonyms={'customer': 'customer', 'customer name': 'customer', 'client': 'customer', 'name': 'customer',
                      'date': 'date', 'payment date': 'date', 'amount': 'amount', 'payment amount': 'amount',
                      'total': 'amount', 'reference': 'reference', 'ref no': 'reference', 'check no': 'reference',
                      'method': 'payment_method', 'payment method': 'payment_method', 'deposit to': 'deposit_to',
                      'memo': 'memo', 'invoice': 'applications[].invoice', 'invoice no': 'applications[].invoice',
                      'invoice number': 'applications[].invoice', 'applied': 'applications[].amount',
                      'amount applied': 'applications[].amount'},
            summary='Customer payments, applied to invoices by number; rows sharing a reference (or payment number) '
                    'are one payment, as are later rows leaving customer, date and amount blank.'),
    DocKind('bill-payment', 'bill pay', 'bill_payment', 'bill payment', 'payment', 'bill payment void',
            lines=('bills',), key_default=('date', 'funding_account', 'check_number', 'reference', 'number'),
            key_core=('date', 'funding_account'),
            synonyms={'date': 'date', 'account': 'funding_account', 'bank account': 'funding_account',
                      'paid from': 'funding_account', 'method': 'method', 'check no': 'check_number',
                      'reference': 'reference', 'memo': 'memo', 'bill': 'bills[].bill', 'bill no': 'bills[].bill',
                      'amount': 'bills[].amount', 'discount': 'bills[].discount'},
            summary='Bill payments, applied to bills by number.'),
    DocKind('inventory-adjustment', 'inventory adjust', 'inventory_adjustment', 'inventory', 'adjustment',
            'inventory void', synonyms={'date': 'date', 'number': 'number', 'ref no': 'number',
                                        'adjustment account': 'adjustment_account', 'account': 'adjustment_account',
                                        'item': 'lines[].item', 'qty change': 'lines[].quantity_change',
                                        'quantity': 'lines[].new_quantity', 'new qty': 'lines[].new_quantity',
                                        'value change': 'lines[].value_change', 'memo': 'memo'},
            summary='Inventory adjustments, grouped by number; each row an item line.'),
)}
LIST_KINDS = KINDS
KINDS = {**LIST_KINDS, **DOC_KINDS}

# Document kinds of design/bulk-import.md §2 not built (none since increment 4).
PLANNED: frozenset[str] = frozenset()
# What the importer gives a document command itself, never a column: a payment's operation key (unique per run and
# row, so a reversed import imports again) and each application's version (read as the row runs).
SUPPLIED = frozenset(('operation_key',))

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
        'delivery-schedule pause', 'automation pause', 'item picture clear',
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
        # An item's picture is a file on an item that exists, never a cell of a row (R114).
        'item picture set', 'item picture use',
        'import preview', 'import run', 'import cancel', 'import reverse', 'import decide', 'import-mapping save',
        'import-mapping deactivate', 'bank-feed import', 'bank-feed reverse', 'bank-feed move',
        'bank-line match', 'bank-line unmatch', 'bank-line ignore', 'bank-line add', 'bank-line transfer',
        'bank-line undo', 'bank-line deposit', 'bank-line pay-bills',
        'bulk-edit preview', 'bulk-edit apply', 'bulk-edit reverse',
        'opening-draft create', 'opening-draft update', 'opening-draft discard', 'opening-draft plan',
        'opening-draft apply', 'opening-draft reverse',
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
    if is_document(k):
        return _doc_fields(k)
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
    return tuple(name for name, info in model.model_fields.items() if info.is_required() and name not in SUPPLIED)


# A document's fields, by their leaf: money, quantities, dates, and the lists a reference names (the command takes
# the text and resolves it itself; the check looks it up first). `invoice` and `bill` are documents found by number.
DOC_MONEY = frozenset(('amount', 'net_amount', 'unit_price', 'unit_cost', 'discount', 'price_basis_amount',
                       'value_change', 'new_value', 'estimated_unit_cost', 'debit', 'credit'))
DOC_DECIMALS = frozenset(('quantity', 'quantity_change', 'new_quantity', 'percent', 'rate', 'markup_percent',
                          'completed_quantity', 'cancelled_quantity'))
DOC_DATES = frozenset(('date', 'due_date', 'ship_date', 'expires_on', 'expected_date', 'lot_expiry'))
DOC_REFERENCES = {'customer': 'customer', 'vendor': 'vendor', 'terms': 'term', 'ar_account': 'account',
                  'ap_account': 'account', 'deposit_to': 'account', 'account': 'account', 'funding_account': 'account',
                  'adjustment_account': 'account', 'discount_account': 'account', 'write_off_account': 'account',
                  'class_id': 'class', 'sales_rep': 'sales-rep', 'ship_method': 'ship-method',
                  'payment_method': 'payment-method', 'price_level': 'price-level', 'customer_message': 'customer-message',
                  'item': 'item', 'invoice': 'invoice', 'bill': 'bill', 'tax_code': 'sales-tax-code',
                  'customer_tax_code': 'sales-tax-code'}
# Inputs a file never gives: replays, fingerprints, defaults switches, a line's id or serials.
DOC_SKIPPED = frozenset(('operation_key', 'expected_facts_fingerprint', 'custom_field_kinds',
                         'expected_custom_field_kinds', 'use_defaults', 'refresh_defaults', 'count_position',
                         'line_id', 'serial_numbers', 'expected_version', 'shipping_address_id', 'discounts',
                         'order_lines', 'receipts', 'purchase_order'))
# A payment's applications are a choice of shapes; a file gives the inline one, by invoice number.
_APPLICATION = {'invoice': ('reference', True), 'amount': ('money', False)}


def _doc_fields(k: DocKind) -> dict[str, FieldSpec]:
    from bookflow.core import registry
    model = registry.get(k.command).input_model
    schema = model.model_json_schema()
    out: dict[str, FieldSpec] = {}

    def typed(path: str, leaf: str, branch: dict, node: dict, required: bool) -> FieldSpec:
        if leaf in DOC_MONEY or (leaf == 'rate' and '[].' in path):  # a line's rate is its unit price
            kind_ = 'money'
        elif leaf in DOC_DECIMALS:
            kind_ = 'decimal'
        elif leaf in DOC_DATES:
            kind_ = 'date'
        elif leaf in DOC_REFERENCES and not path.startswith(('pay_to.', 'billing_address.', 'shipping_address.')):
            kind_ = 'reference'
        elif 'enum' in branch:
            kind_ = 'enum'
        elif branch.get('type') in ('boolean', 'integer', 'string'):
            kind_ = branch['type']
        else:
            kind_ = 'any'
        return FieldSpec(path, kind_, required, tuple(branch.get('enum', ())),
                         DOC_REFERENCES.get(leaf) if kind_ == 'reference' else None,
                         (node.get('description') or '')[:200], branch.get('maxLength'), branch.get('minLength'),
                         branch.get('pattern'), branch.get('minimum'), branch.get('maximum'))

    def add(prefix: str, props: dict, required: set, depth: int = 0):
        for name, node in props.items():
            if name in DOC_SKIPPED or name == 'custom_fields':
                continue
            path = prefix + name
            branches = _branches(schema, node)
            if name in k.lines and not prefix:
                if (k.name, name) == ('payment', 'applications'):
                    for leaf, (kind_, req) in _APPLICATION.items():
                        out[f'{name}[].{leaf}'] = FieldSpec(f'{name}[].{leaf}', kind_, req, (),
                                                            DOC_REFERENCES.get(leaf) if kind_ == 'reference' else None)
                    continue
                array = next((b for b in branches if b.get('type') == 'array'), None)
                inner = _resolve_ref(schema, array['items']) if array else {}
                add(f'{name}[].', inner.get('properties', {}), set(inner.get('required', ())), depth + 1)
                continue
            branch = next((b for b in branches if b.get('type') == 'string'), branches[0] if branches else {})
            if branch.get('type') == 'array':
                continue
            if branch.get('type') == 'object' and 'properties' in branch:
                if depth < 2:
                    add(path + '.', branch['properties'], set(branch.get('required', ())) if name in required
                        else set(), depth + 1)
                continue
            if branch.get('type') == 'object':
                continue
            out[path] = typed(path, name, branch, node, name in required)

    add('', schema['properties'], set(schema.get('required', ())))
    for path in DOC_EXTRA.get(k.name, ()):
        out[path] = FieldSpec(path, 'money', False, (), None, JOURNAL_SIDES[path])
    return out


# A journal file with a Debit and a Credit column: each row's filled one becomes the line's side and amount.
JOURNAL_SIDES = {'lines[].debit': 'The line amount when it is a debit (a Debit column); becomes side and amount.',
                 'lines[].credit': 'The line amount when it is a credit (a Credit column); becomes side and amount.'}
DOC_EXTRA = {'journal': tuple(JOURNAL_SIDES)}


def line_array(path: str) -> str | None:
    """The line array a document field belongs to (`lines` for `lines[].item`), or None for a header field."""
    return path.split('[]', 1)[0] if '[].' in path else None


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
