"""Human presentation over command results; no domain or write decisions."""

from __future__ import annotations

from typing import Any


CUSTOMER_GROUPS = (
    ("Customer or job", ("name", "parent_id", "company_name", "active", "account_number")),
    ("Contact details", ("salutation", "first_name", "middle_name", "last_name", "job_title", "contact", "alt_contact", "phone", "alt_phone", "fax", "email", "cc_email", "website", "contact_mode", "contacts")),
    ("Addresses", ("billing_address", "address_mode", "shipping_addresses")),
    ("Job details", ("job_status", "job_description", "job_type_id", "job_start", "job_projected_end", "job_end", "job_sales_rep_id")),
    ("Commercial defaults", ("terms_id", "credit_limit", "customer_type_id", "sales_rep_id", "price_level_id", "sales_tax_code_id", "sales_tax_item_id", "resale_number", "preferred_payment_method_id", "preferred_delivery_method", "preferred_ship_method_id", "default_class_id")),
    ("Payment details and notes", ("payment_brand", "payment_last4", "payment_expiry_month", "payment_expiry_year", "payment_billing_address", "notes")),
)

LABELS = {
    "name": "Name", "parent_id": "Parent customer or job", "company_name": "Company name",
    "terms_id": "Payment terms", "sales_tax_code_id": "Sales tax code",
    "sales_tax_item_id": "Sales tax item", "sales_rep_id": "Sales representative",
    "job_sales_rep_id": "Job sales representative", "default_class_id": "Default class",
    "preferred_payment_method_id": "Preferred payment method",
    "preferred_ship_method_id": "Preferred shipping method", "price_level_id": "Price level",
    "customer_type_id": "Customer type", "job_type_id": "Job type",
    "contact_mode": "Contact source", "address_mode": "Shipping address source",
}


def label(field: str) -> str:
    return LABELS.get(field, field.removesuffix("_id").replace("_", " ").replace(".", " · ").capitalize())


def company_form_groups(leaves):
    preferences = {'estimates_enabled', 'progress_billing_enabled', 'close_estimates_after_billing'}
    labels = dict(estimates_enabled='Create estimates', progress_billing_enabled='Enable progress billing',
        close_estimates_after_billing='Make estimates inactive after final billing (only with progress billing off)')
    for leaf in leaves:
        if leaf['path'] in labels:
            leaf['label'] = labels[leaf['path']]
    return [dict(title=title, open=True, leaves=[leaf for leaf in leaves if (leaf['path'] in preferences) == selected])
        for title, selected in [('Customer work preferences', True), ('Company information', False)]]


def customer_form_groups(leaves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    placed: set[str] = set()
    for title, fields in CUSTOMER_GROUPS:
        selected = [leaf for leaf in leaves if leaf["path"].split(".")[0] in fields]
        if selected:
            groups.append({"title": title, "leaves": selected, "open": title in ("Customer or job", "Contact details")})
            placed.update(leaf["path"] for leaf in selected)
    remaining = [leaf for leaf in leaves if leaf["path"] not in placed]
    if remaining:
        groups.append({"title": "Additional fields and concurrency", "leaves": remaining, "open": False})
    return groups


def customer_sections(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Choose readable values already computed by customer show."""
    groups = (
        ("Identity", ("customer_or_job", "company_name", "account_number", "active", "job_status")),
        ("Contact details", ("contact", "phone", "alt_phone", "email", "website", "contacts")),
        ("Addresses", ("effective_billing_address", "shipping_addresses")),
        ("Commercial defaults", ("terms", "customer_type", "sales_rep", "payment_method", "credit_limit", "effective_preferred_delivery_method")),
        ("Job details and notes", ("job_description", "job_start", "job_projected_end", "job_end", "notes")),
    )
    return [{"title": title, "fields": [(label(key.removeprefix("effective_")), record[key]) for key in fields if record.get(key) not in (None, "", [])]} for title, fields in groups]
