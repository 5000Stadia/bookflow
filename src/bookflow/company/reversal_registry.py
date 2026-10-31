"""Inverse registry for record families, independent of file import support.

Document families declare their native delete or void here. Applied payments first
unapply; list families restore before-images or release names and deactivate through
the shared planner. Aggregate inverses are supplied by batch-kind adapters.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from bookflow import BookflowError
import sqlalchemy as sa

@dataclass(frozen=True)
class DocumentRecord:
    """A document a batch made: retired by its family's delete, history preserved (else its void), one step."""
    noun: str
    table: sa.Table
    name_field: str
    selector: str
    inverse: str
    type_value: str
    type_column: str | None
    document = True
    number_field = None



@dataclass(frozen=True)
class ListRecord:
    noun: str
    table: sa.Table
    name_field: str  # the input field holding the record's own (leaf) name
    selector_field: str
    number_field: str | None = None  # a second unique field a reversed record releases (an account's number)

    @property
    def selector(self) -> str:
        """The input field its update and deactivate commands select the record by (`customer_type`)."""
        return self.noun.replace('-', '_')


@lru_cache(maxsize=None)
def list_record(record_type: str) -> ListRecord | None:
    from bookflow.company import lists
    for definition in lists.all_list_definitions():
        if definition.record_type == record_type:
            table = c.metadata.tables[definition.table]
            return ListRecord(definition.noun, table, 'name', definition.display_field,
                              'number' if definition.noun == 'account' else None)
    return None


from bookflow.company import schema as c

DOCUMENTS: dict[str, DocumentRecord] = {}

def register(family: str, noun: str, selector: str, inverse: str, *,
             table: str = 'transactions', type_column: str | None = 'type') -> DocumentRecord:
    record = DocumentRecord(noun, c.metadata.tables[table], 'number', selector, inverse, family, type_column)
    DOCUMENTS[family] = record
    return record

register('invoice', 'invoice', 'invoice', 'invoice delete', table='transactions', type_column='type')
register('credit_memo', 'credit-memo', 'credit_memo', 'credit-memo delete', table='transactions', type_column='type')
register('sales_receipt', 'sales-receipt', 'sales_receipt', 'sales-receipt delete', table='transactions', type_column='type')
register('estimate', 'estimate', 'estimate', 'estimate void', table='work_documents', type_column='kind')
register('sales_order', 'sales-order', 'sales_order', 'sales-order void', table='work_documents', type_column='kind')
register('bill', 'bill', 'bill', 'bill delete', table='transactions', type_column='type')
register('vendor_credit', 'vendor-credit', 'credit', 'vendor-credit void', table='transactions', type_column='type')
register('purchase_order', 'purchase-order', 'purchase_order', 'purchase-order void', table='purchase_orders', type_column=None)
register('journal_entry', 'journal', 'journal', 'journal delete', table='transactions', type_column='type')
register('check', 'check', 'check', 'check delete', table='transactions', type_column='type')
register('card_charge', 'card-charge', 'card_charge', 'card-charge delete', table='transactions', type_column='type')
register('payment', 'payment', 'payment', 'payment delete', table='transactions', type_column='type')
register('bill_payment', 'bill payment', 'payment', 'bill payment void', table='transactions', type_column='type')
register('inventory_adjustment', 'inventory', 'adjustment', 'inventory void', table='transactions', type_column='type')
register('statement_charge', 'statement-charge', 'statement_charge', 'statement-charge void')


def steps_for(group: dict, rec: ListRecord, version: int, s=None) -> list[tuple[str, dict]]:
    """The inverse commands for one record, in order, given its version when the first one runs."""
    if group['action'] == 'created' and getattr(rec, 'document', False):
        from bookflow.company.batches import _document_steps
        return _document_steps(s, group, rec)
    if group['action'] == 'updated':
        return [(f'{rec.noun} update', {rec.selector: group['record_id'], 'expected_version': version,
                                        **group['before']})]
    if group['action'] == 'created':
        return [('rename', {}), (f'{rec.noun} deactivate', {rec.selector: group['record_id']})]
    raise BookflowError('E_VALIDATION', message=f"No inverse for a {group['action']} effect.",
                        details={'action': group['action']})



# These native aggregate inverses also own run state, inbox effects and their grants.
NATIVE_INVERSES = {
    'bank-import': ('bank-feed reverse', 'import_id'),
    'bank-sort': ('bank-line undo', 'batch'),
    'opening': ('opening-draft reverse', 'draft'),
    'bulk-edit': ('bulk-edit reverse', 'run'),
    'import': ('import reverse', 'run'),
}

