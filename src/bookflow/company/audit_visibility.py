"""Which company audit events a reader may see, from the record types each event touched.

Every audit entry names the record type it wrote. Each record type is read under one or more
catalog capabilities at `member`: an estimate under `customer-work`, a purchase order or any
other transaction under `ledger.read`, a note under `note`, a customer under `customer`. An event
is shown only when the reader holds every capability its entries need, and a note or file link
also needs what the record it is attached to needs. A record type this map does not know needs
every capability here, so a new kind of record stays hidden from a restricted reader until it is
named. The audit and activity commands apply this in SQL, before paging, so a hidden event is
neither returned nor counted.
"""
from __future__ import annotations

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.core.errors import BookflowError
from bookflow.hub.access import require_resource

READ = 'member'
LEDGER = 'ledger.read'
WORK = 'customer-work'


def _lists():
    from bookflow.company.lists import all_list_definitions
    return {d.record_type: frozenset((d.noun,)) for d in all_list_definitions()}


# Record types named exactly; checked before the prefixes below.
EXACT = {
    **_lists(),
    'company': frozenset(('company',)),
    'company_info': frozenset(('company',)),
    'company_backup': frozenset(('company',)),
    'principal': frozenset(('company',)),
    'directive': frozenset(('directive',)),
    'note': frozenset(('note',)),
    'attachment': frozenset(('attachment',)),
    'attachment_link': frozenset(('attachment',)),
    'attachment_collection': frozenset(('attachment',)),
    'customer_vendor_link': frozenset(('customer', 'vendor')),
    'customer_address': frozenset(('customer',)),
    'customer_contact': frozenset(('customer',)),
    'customer_contact_point': frozenset(('customer',)),
    'billing_group': frozenset(('customer',)),
    'billing_group_member': frozenset(('customer',)),
    'vendor_contact': frozenset(('vendor',)),
    'vendor_contact_point': frozenset(('vendor',)),
    'vendor_expense_account': frozenset(('vendor',)),
    'item_member': frozenset(('item',)),
    'item_vendor_profile': frozenset(('item',)),
    'price_level_item': frozenset(('price-level',)),
    'unit_conversion': frozenset(('unit-of-measure',)),
    'custom_field_def': frozenset(('custom-field',)),
    'custom_field_scope': frozenset(('custom-field',)),
    'custom_field_choice': frozenset(('custom-field',)),
    'custom_field_value': frozenset(('custom-field',)),
    'exchange_rate': frozenset((LEDGER,)),
    # Billing an estimate or a work order writes both sides.
    'work_billing_allocation': frozenset((WORK, LEDGER)),
    'work_billing_conversion': frozenset((WORK, LEDGER)),
}

# Record-type families: estimates, work orders and recorded time under customer-work; every
# transaction, its lines, postings, settlement, deposit, reconciliation and deletion rows under
# ledger.read. No two prefixes here match the same record type.
PREFIXES = (
    ('work_', frozenset((WORK,))),
    ('time_activit', frozenset((WORK,))),
    *((prefix, frozenset((LEDGER,))) for prefix in (
        'transaction', 'document_line', 'posting_', 'check_instrument', 'money_out_', 'purchase_',
        'item_receipt', 'receipt_bill_', 'ap_', 'bill_', 'payment_', 'application', 'settlement_',
        'deposit_', 'bank_effect_', 'credit_', 'vendor_credit_', 'customer_refund_', 'sales_',
        'statement_', 'inventory_', 'invoice_batch', 'memorized_', 'reconciliation_', 'journal_')),
)

# The strictest requirement: what a record type this module does not know needs.
CAPABILITIES = frozenset().union(*EXACT.values(), *(caps for _, caps in PREFIXES))


def record_capabilities(record_type: str) -> frozenset[str]:
    """The read capabilities one record type needs; every one of them when it is unknown."""
    if record_type in EXACT:
        return EXACT[record_type]
    for prefix, caps in PREFIXES:
        if record_type.startswith(prefix):
            return caps
    return CAPABILITIES


def admitted(s) -> frozenset[str]:
    """The read capabilities this reader holds, each asked once of the policy in force."""
    held = set()
    for capability in sorted(CAPABILITIES):
        try:
            require_resource(s, capability, READ)
        except BookflowError as exc:
            if exc.code != 'E_PERMISSION':
                raise
        else:
            held.add(capability)
    return frozenset(held)


def require_record_type(s, record_type: str, held: frozenset[str] | None = None) -> None:
    """Refuse a record type the reader may not read, before anything about a record is looked up."""
    held = admitted(s) if held is None else held
    missing = record_capabilities(record_type) - held
    if missing:
        raise BookflowError('E_PERMISSION', details={'capability': sorted(missing)[0], 'required_role': READ})


def _hidden(column, held: frozenset[str]):
    """SQL: this record-type column names a type the reader may not read."""
    missing = CAPABILITIES - held
    exact = sorted(EXACT)
    denied = [name for name, caps in EXACT.items() if caps & missing]
    family = [column.startswith(prefix, autoescape=True) for prefix, caps in PREFIXES if caps & missing]
    known = sa.or_(*(column.startswith(prefix, autoescape=True) for prefix, _ in PREFIXES))
    clauses = [column.in_(denied)] if denied else []
    if family:
        clauses.append(sa.and_(column.not_in(exact), sa.or_(*family)))
    clauses.append(sa.and_(column.not_in(exact), ~known))
    return sa.or_(*clauses)


def visible_events(event_id, held: frozenset[str]):
    """SQL predicate over an audit event id column, or None when this reader sees every event."""
    if held >= CAPABILITIES:
        return None
    entries = c.audit_entries.alias('visibility_entries')
    clauses = [sa.exists(sa.select(sa.literal(1)).select_from(entries).where(
        entries.c.event_id == event_id, _hidden(entries.c.record_type, held)))]
    # A note or file link shows the record it is attached to.
    for kind, table in (('note', c.notes), ('attachment_link', c.attachment_links)):
        if record_capabilities(kind) <= held:
            target = table.alias('visibility_' + table.name)
            clauses.append(sa.exists(sa.select(sa.literal(1)).select_from(entries.join(
                target, sa.and_(entries.c.record_type == kind, entries.c.record_id == target.c.id))).where(
                    entries.c.event_id == event_id, _hidden(target.c.record_type, held))))
    return ~sa.or_(*clauses)
