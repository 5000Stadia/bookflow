"""Presentation of a typed customer statement; all amounts come from the core command.

A statement is the one receivables page a customer would read over the owner's
shoulder, so every row names itself in words rather than in field values, and
every document on it opens where it was written. A statement is one customer's
account, so the printable copy is per customer and its link sits on the row that
opens that customer's balance.
"""
from urllib.parse import urlencode

from bookflow.adapters.workbench.document_print import statement_url
from bookflow.adapters.workbench.transaction_detail import document_link


COMMANDS = {"report statement"}
# What each row is, in the words a customer reads rather than the wire value.
ENTRIES = {"balance_forward": "Balance forward", "balance_due": "Balance due",
           "invoice": "Invoice", "sales_receipt": "Sales receipt", "payment": "Payment",
           "deposit": "Deposit", "journal_entry": "Adjustment",
           "credit_memo": "Credit memo", "customer_refund": "Refund",
           "statement_charge": "Statement charge", "applied_credit": "Credit applied"}
# The link a document row opens is built by `document_link`, the way every other accounting
# report builds it, rather than from a map or a path spelled out here. The map that used to
# be here omitted the statement charge, the credit memo and the refund, so three kinds of row
# a customer reads opened nothing at all; spelling the path out instead still lost the two
# things the shared link carries. A statement sums immutable effects, so a document deleted
# out of ordinary lists is still named by the rows it posted and must still open behind them.
# And the audit position the statement was read at travels with the link, so the document
# answers the question the statement asked rather than a fresh one.
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
        print_url = None
        if row["kind"] == "opening" and row["customer_id"]:
            print_url = statement_url(company_id, row["customer_id"],
                                      period["date_from"], period["date_to"])
        rows.append({**row, "customer_url": customer_url, "print_url": print_url,
                     "document_url": document_link(company_id, row, watermark)})
    next_fields = {f"f:{key}": (str(value).lower() if isinstance(value, bool) else str(value))
                   for key, value in inputs.items() if key != "cursor" and value is not None}
    next_fields["f:cursor"] = result["next_cursor"]
    return {**result, "rows": rows, "next_fields": next_fields, "entries": ENTRIES,
            "aging_columns": AGING, "total_columns": TOTALS,
            "date_from": period["date_from"], "as_of": period["date_to"],
            "one_customer": inputs.get("customer") is not None}
