"""Explicit company-local identities that support annotations."""

from __future__ import annotations

import sqlalchemy as sa

from bookflow.company import schema
from bookflow.company.lists import all_list_definitions
from bookflow.core.errors import BookflowError
from bookflow.core.ids import is_ulid, normalize_ulid


# Values are declared here, never derived from caller-supplied table names.
_TARGETS = {
    definition.record_type: (definition.table, "id")
    for definition in all_list_definitions()
}
_TARGETS.update({
    "company_info": ("company_info", "id"),
    "directive": ("directives", "id"),
    "principal": ("principals", "user_id"),
    "audit_event": ("audit_events", "id"),
    "audit_entry": ("audit_entries", "id"),
    "customer_vendor_link": ("customer_vendor_links", "id"),
    "note": ("notes", "id"),
    "work_document": ("work_documents", "id"),
    "work_revision": ("work_revisions", "id"),
    "work_line": ("work_lines", "id"),
    "transaction": ("transactions", "id"),
    "transaction_revision": ("transaction_revisions", "id"),
    "document_line_identity": ("document_line_identities", "id"),
    "document_line": ("document_lines", "id"),
    "posting_batch": ("posting_batches", "id"),
    "posting_line": ("posting_lines", "id"),
    "posting_line_source": ("posting_line_sources", "id"),
    "attachment": ("attachments", "id"),
    "attachment_link": ("attachment_links", "id"),
    "customer_address": ("customer_addresses", "id"),
    "customer_contact": ("customer_contacts", "id"),
    "customer_contact_point": ("customer_contact_points", "id"),
    "vendor_contact": ("vendor_contacts", "id"),
    "vendor_contact_point": ("vendor_contact_points", "id"),
    "vendor_expense_account": ("vendor_expense_accounts", "id"),
    "item_member": ("item_members", "id"),
    "item_vendor_profile": ("item_vendor_profiles", "id"),
    "price_level_item": ("price_level_items", "id"),
    "unit_conversion": ("unit_conversions", "id"),
    "custom_field_scope": ("custom_field_scopes", "id"),
    "custom_field_choice": ("custom_field_choices", "id"),
    "custom_field_value": ("custom_field_values", "id"),
})


def target_types() -> tuple[str, ...]:
    return tuple(sorted(_TARGETS))


def resolve(session, record_type: str, record_id: str) -> str:
    """Check existence inside an already-authorized company, including inactive rows."""
    target = _TARGETS.get(record_type)
    if target is None:
        raise BookflowError("E_VALIDATION", details={"fields": [{
            "field": "record_type", "problem": "unsupported annotation target type",
        }]})
    found = None
    if is_ulid(record_id):
        record_id = normalize_ulid(record_id)
        table_name, key = target
        table = schema.metadata.tables[table_name]
        found = session.company.conn.execute(
            sa.select(table.c[key]).where(table.c[key] == record_id).limit(1)
        ).scalar_one_or_none()
    if found is None:
        raise BookflowError("E_RECORD_NOT_FOUND", details={
            "record_type": record_type, "record_id": record_id, "suggestions": [],
        })
    return str(found)
