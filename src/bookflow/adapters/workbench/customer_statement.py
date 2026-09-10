"""Presentation of a typed customer statement; all amounts come from the core command.

A statement is the one receivables page a customer would read over the owner's
shoulder, so every row names itself in words rather than in field values, and
every document on it opens where it was written.
"""
from urllib.parse import urlencode


COMMANDS = {"report statement"}
# What each row is, in the words a customer reads rather than the wire value.
ENTRIES = {"balance_forward": "Balance forward", "balance_due": "Balance due",
           "invoice": "Invoice", "sales_receipt": "Sales receipt", "payment": "Payment",
           "deposit": "Deposit", "journal_entry": "Adjustment",
           "applied_credit": "Credit applied"}
# The record page a document row opens, by the noun that owns the document.
NOUNS = {"invoice": "invoice", "sales_receipt": "sales-receipt", "payment": "payment",
         "deposit": "deposit", "journal_entry": "journal"}
AGING = (("current", "Current"), ("days_1_30", "1-30"), ("days_31_60", "31-60"),
         ("days_61_90", "61-90"), ("over_90", "Over 90"), ("total", "Total"))
TOTALS = (("opening", "Balance forward"), ("charges", "Charges"),
          ("credits", "Payments and credits"), ("closing", "Balance due"))


def view(result, inputs, company_id):
    period = result["metadata"]["period"]
    watermark = result["metadata"]["audit_watermark"]
    rows = []
    for row in result["rows"]:
        customer_url = None
        if row["customer_id"]:
            customer_url = f"/c/{company_id}/report/statement?" + urlencode(
                {"f:date_from": period["date_from"], "f:date_to": period["date_to"],
                 "f:customer": row["customer_id"], "source_report_watermark": watermark})
        noun = NOUNS.get(row["entry"]) if row["transaction_id"] else None
        rows.append({**row, "customer_url": customer_url,
                     "document_url": f"/c/{company_id}/{noun}/{row['transaction_id']}" if noun else None})
    next_fields = {f"f:{key}": (str(value).lower() if isinstance(value, bool) else str(value))
                   for key, value in inputs.items() if key != "cursor" and value is not None}
    next_fields["f:cursor"] = result["next_cursor"]
    return {**result, "rows": rows, "next_fields": next_fields, "entries": ENTRIES,
            "aging_columns": AGING, "total_columns": TOTALS,
            "date_from": period["date_from"], "as_of": period["date_to"],
            "one_customer": inputs.get("customer") is not None}
