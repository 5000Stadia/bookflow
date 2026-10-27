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
    "again": "Enter it again",
    "name": "Name", "parent_id": "Parent customer or job", "company_name": "Company name",
    "terms_id": "Payment terms", "sales_tax_code_id": "Sales tax code",
    "sales_tax_item_id": "Sales tax item", "sales_rep_id": "Sales representative",
    "job_sales_rep_id": "Job sales representative", "default_class_id": "Default class",
    "preferred_payment_method_id": "Preferred payment method",
    "preferred_ship_method_id": "Preferred shipping method", "price_level_id": "Price level",
    "customer_type_id": "Customer type", "job_type_id": "Job type",
    "contact_mode": "Contact source", "address_mode": "Shipping address source",
    "to_be_printed": "Print later",
}


def label(field: str) -> str:
    return LABELS.get(field, field.removesuffix("_id").replace("_", " ").replace(".", " · ").capitalize())


def company_form_groups(leaves):
    preferences = {'estimates_enabled', 'progress_billing_enabled', 'close_estimates_after_billing',
                   'sales_orders_enabled', 'available_deducts_sales_orders', 'available_deducts_pending_builds'}
    labels = dict(estimates_enabled='Create estimates', progress_billing_enabled='Enable progress billing',
        sales_orders_enabled='Use sales orders',
        available_deducts_sales_orders='Quantity available deducts quantity on sales orders and work orders',
        available_deducts_pending_builds='Quantity available deducts quantity reserved for pending builds',
        close_estimates_after_billing='Make estimates inactive after final billing (only with progress billing off)',
        automatically_apply_payments='Suggest matching invoice, then oldest invoices for new payments',
        automatically_calculate_payments='Calculate selected invoice payments when no amount was entered',
        use_undeposited_funds_for_payments='Default new payments to Undeposited Funds',
        negative_number_style='Show negative amounts as',
        finance_charge_annual_rate='Annual interest rate (%)', finance_charge_minimum='Minimum finance charge',
        finance_charge_grace_days='Grace period (days)', finance_charge_account_id='Finance charge account',
        finance_charge_on_overdue_charges='Assess finance charges on overdue finance charges',
        finance_charge_from='Calculate charges from', finance_charge_mark_to_print='Mark finance charge invoices to be printed',
        scheduler_catch_up_days='Enter missed automatic transactions up to (days back; blank for all)',
        overdue_notice_days='Prepare a notice when an invoice is this many days overdue (blank: off)',
        overdue_notice_frequency='Prepare notices', overdue_notice_minimum='Only for an overdue balance of at least',
        overdue_notice_template='Each notice is',
        invoice_when_shipping='After a shipment, offer an invoice for it',
        pick_and_pack_enabled='Use pick lists and packing (pick and box before shipping; off ships straight from the order)',
        weight_unit='Weight unit (lb, kg, oz)', shipping_item_id='Freight to bill is billed as')
    payment_preferences = {'automatically_apply_payments', 'automatically_calculate_payments', 'use_undeposited_funds_for_payments'}
    display_preferences = {'negative_number_style'}
    shipping_preferences = {'invoice_when_shipping', 'pick_and_pack_enabled', 'weight_unit', 'shipping_item_id'}
    finance_preferences = {'finance_charge_annual_rate', 'finance_charge_minimum', 'finance_charge_grace_days',
        'finance_charge_account_id', 'finance_charge_on_overdue_charges', 'finance_charge_from', 'finance_charge_mark_to_print'}
    reminder_preferences = {'scheduler_catch_up_days', 'overdue_notice_days', 'overdue_notice_frequency',
        'overdue_notice_minimum', 'overdue_notice_template'}
    for leaf in leaves:
        if leaf['path'] in labels:
            leaf['label'] = labels[leaf['path']]
    groups = [dict(title=title, open=True, leaves=[leaf for leaf in leaves if leaf['path'] in fields])
        for title, fields in [('Customer work preferences', preferences), ('Customer payment preferences', payment_preferences),
            ('Finance charges', finance_preferences), ('Reminders and overdue notices', reminder_preferences),
            ('Shipping and fulfillment', shipping_preferences), ('Reports and lists', display_preferences),
            ('Company information', {leaf['path'] for leaf in leaves} - preferences - payment_preferences - display_preferences
             - finance_preferences - reminder_preferences - shipping_preferences)]]
    return [group for group in groups if group['leaves'] or group['title'] not in ('Reports and lists', 'Finance charges',
                                                                                   'Reminders and overdue notices',
                                                                                   'Shipping and fulfillment')]


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
