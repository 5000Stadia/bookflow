"""Presentation of the four period summaries; all amounts come from core commands.

Nothing here computes money. The one derivation is the percentage a person reads: the
command returns the exact coefficient in millionths of a percentage point, and a column
of eight decimal places is unreadable, so it is shown to two -- by integer arithmetic,
never by turning a share into a float. The exact coefficient stays on the row for anyone
who wants it, and the structured report data below the table carries it verbatim.
"""
from urllib.parse import urlencode

COMMANDS = {"report sales-by-customer", "report sales-by-item", "report sales-by-rep",
            "report expenses-by-vendor"}

# What each report calls its rows and its money, in the words a bookkeeper expects.
SHAPE = {
    "sales-by-customer": {"row": "Customer", "amount": "income", "amount_heading": "Income",
                          "label": "display_customer_label", "id": "customer_id",
                          "rows": "customers"},
    "sales-by-item": {"row": "Item", "amount": "income", "amount_heading": "Income",
                      "label": "display_item_label", "id": "item_id", "rows": "items"},
    "sales-by-rep": {"row": "Rep", "amount": "income", "amount_heading": "Income",
                     "label": "display_sales_rep_label", "id": "sales_rep_id", "rows": "representatives"},
    "expenses-by-vendor": {"row": "Vendor", "amount": "expense", "amount_heading": "Expense",
                           "label": "display_vendor_label", "id": "vendor_id", "rows": "vendors"},
}


def percent(millionths):
    """A percentage coefficient shown to two places, by integer arithmetic only."""
    if millionths is None:
        return None
    sign, value = ("-" if millionths < 0 else ""), abs(millionths)
    whole, fraction = divmod(value, 10 ** 6)
    hundredths, remainder = divmod(fraction, 10 ** 4)
    if remainder * 2 >= 10 ** 4:
        hundredths += 1
        if hundredths == 100:
            whole, hundredths = whole + 1, 0
    return f"{sign}{whole}.{hundredths:02d}%"


def view(result, inputs, company_id, verb):
    period = result["metadata"]["period"]
    shape = SHAPE[verb]
    watermark = result["metadata"]["audit_watermark"]
    rows = []
    for row in result["rows"]:
        # A drill-down exists only where one report really answers the next question a
        # reader has. An item and a representative have no such report yet, so their
        # rows are plain rather than carrying a link that goes somewhere unrelated.
        detail = None
        if verb == "sales-by-customer" and row["customer_id"]:
            detail = f"/c/{company_id}/report/statement?" + urlencode(
                {"f:date_from": period["date_from"], "f:date_to": period["date_to"],
                 "f:customer": row["customer_id"], "source_report_watermark": watermark})
        elif verb == "expenses-by-vendor" and row["vendor_id"]:
            detail = f"/c/{company_id}/report/unpaid-bills?" + urlencode(
                {"f:as_of": period["date_to"], "f:vendor": row["vendor_id"],
                 "source_report_watermark": watermark})
        rows.append({**row, "detail_url": detail,
                     "display_label": row[shape["label"]],
                     "display_amount": row[shape["amount"]]["amount"],
                     "display_percent": percent(row["percent_of_total_millionths"])})
    next_fields = {f"f:{key}": (str(value).lower() if isinstance(value, bool) else str(value))
                   for key, value in inputs.items() if key != "cursor" and value is not None}
    next_fields["f:cursor"] = result["next_cursor"]
    return {**result, "rows": rows, "next_fields": next_fields, "verb": verb, "shape": shape,
            "date_from": period["date_from"], "date_to": period["date_to"],
            "total_amount": result["totals"][shape["amount"]]["amount"]}
