"""Presentation of the everyday reports; every figure comes from the core command.

Balance summaries and details, open purchase orders, purchases by vendor and by item,
deposit detail, the transaction list by date and the 1099 vendor summary are all one shape
on the page: whole-report totals, then a table whose columns a report declares here, then
the Next page form. Nothing here adds, subtracts or rounds money. The one derivation is the
share-of-total column, shown to two places by the same integer rule the period summaries use.
"""
from urllib.parse import urlencode

from bookflow.adapters.workbench.summaries import percent
from bookflow.adapters.workbench.transaction_detail import document_link

COMMANDS = {
    "report customer-balance-summary", "report customer-balance-detail",
    "report vendor-balance-summary", "report vendor-balance-detail",
    "report open-purchase-orders", "report purchases-by-vendor", "report purchases-by-item",
    "report deposit-detail", "report transaction-list-by-date", "report vendor-1099-summary",
    "report reconciliation-discrepancy", "report entries-to-review", "report prior-balances",
}

# One column: (heading, row field, how the cell is written). `label` is the row's first
# column and carries the row's link; `money` is right-aligned; `document` is a type and
# number opening the document; `date` a day; `text` as returned.
M, T, D = "money", "text", "date"
LAYOUTS = {
    "customer-balance-summary": (("Customer", "display_customer_label", "label"), ("Balance", "balance", M)),
    "vendor-balance-summary": (("Vendor", "display_vendor_label", "label"), ("Balance", "balance", M)),
    "customer-balance-detail": (("Customer", "display_customer_label", "label"), ("Date", "date", D),
                                ("Transaction", "number", "document"), ("Due", "due_date", D),
                                ("Memo", "memo", T), ("Amount", "amount", M), ("Balance", "balance", M)),
    "vendor-balance-detail": (("Vendor", "display_vendor_label", "label"), ("Date", "date", D),
                              ("Transaction", "number", "document"), ("Due", "due_date", D),
                              ("Memo", "memo", T), ("Amount", "amount", M), ("Balance", "balance", M)),
    "open-purchase-orders": (("Purchase order", "number", "label"), ("Date", "date", D),
                             ("Vendor", "display_vendor_label", T), ("Expected", "expected_date", D),
                             ("Status", "status_label", T), ("Amount", "amount", M),
                             ("Received", "received", M), ("Open balance", "open_balance", M)),
    "purchases-by-vendor": (("Vendor", "display_vendor_label", "label"), ("Amount", "amount", M),
                            ("% of total", "display_percent", "num")),
    "purchases-by-item": (("Item", "display_item_label", "label"), ("Quantity", "quantity", "num"),
                          ("Amount", "amount", M), ("Average cost", "average_cost", M),
                          ("% of total", "display_percent", "num")),
    "deposit-detail": (("Deposit", "deposit_number", "label"), ("Date", "date", D),
                       ("Transaction", "number", "document"), ("Name", "party_name", T),
                       ("Account", "display_account_label", T), ("Memo", "memo", T),
                       ("Amount", "amount", M)),
    "transaction-list-by-date": (("Date", "date", "label"), ("Transaction", "number", "document"),
                                 ("Effect", "batch_label", T), ("Name", "party_name", T),
                                 ("Memo", "memo", T), ("Account", "display_account_label", T),
                                 ("Split", "split_account_label", T), ("Amount", "amount", M)),
    "vendor-1099-summary": (("Vendor", "display_vendor_label", "label"),
                            ("Box", "box_label", T),
                            ("Payments", "payments", M),
                            ("Card payments not counted", "card_payments_excluded", M),
                            ("Meets threshold", "threshold_label", T)),
    "reconciliation-discrepancy": (("Reconciliation", "reconciliation_label", "label"),
                                   ("Transaction", "number", "document"), ("Date", "date", D),
                                   ("Memo", "memo", T), ("Change", "change_label", T),
                                   ("Reconciled", "reconciled", M), ("Now", "current", M),
                                   ("Difference", "difference", M)),
    "entries-to-review": (("Why", "why", "label"), ("Transaction", "number", "document"),
                          ("Date", "date", D), ("Posted by", "posted_by_label", T),
                          ("Reason", "reason", T), ("Entered", "entered_at", T),
                          ("Reviewed", "reviewed_label", T), ("Amount", "amount", M)),
    "prior-balances": (("Reviewed", "review_label", "label"), ("Account", "display_account_label", T),
                       ("Transaction", "number", "document"), ("Date", "date", D),
                       ("Entered", "entered_at", T), ("Posted by", "posted_by", T),
                       ("When reviewed", "reviewed_balance", M), ("Now", "current_balance", M),
                       ("Change", "change", M)),
}
# The whole-report figures above the table, in reading order, with their headings.
TOTALS = {
    "customer-balance-summary": (("balance", "Total balance"),),
    "vendor-balance-summary": (("balance", "Total balance"),),
    "customer-balance-detail": (("balance", "Total balance"),),
    "vendor-balance-detail": (("balance", "Total balance"),),
    "open-purchase-orders": (("amount", "Ordered"), ("received", "Received"), ("open_balance", "Open balance")),
    "purchases-by-vendor": (("amount", "Total purchases"),),
    "purchases-by-item": (("amount", "Total purchases"),),
    "deposit-detail": (("deposited", "Total deposited"), ("deposits", "Deposits")),
    "transaction-list-by-date": (("transactions", "Transactions"),),
    "vendor-1099-summary": (("reportable", "Reportable payments"), ("threshold", "Threshold"),
                            ("vendors_meeting_threshold", "Vendors at or over threshold"),
                            ("payments", "All payments to 1099 vendors"),
                            ("card_payments_excluded", "Card payments not counted")),
    "reconciliation-discrepancy": (("reconciliations", "Reconciliations"),
                                   ("out_of_balance", "No longer tie"),
                                   ("changes", "Transactions changed since reconciled")),
    "entries-to-review": (("entries", "Entries to review"),
                          ("open_reconciliations", "Reconciliations left open with a difference")),
    "prior-balances": (("reviews", "Reviewed balance dates"), ("changed_balances", "Balances that moved"),
                       ("entries", "Entries responsible")),
}
# What each report says under its table, in a bookkeeper's words.
NOTES = {
    "customer-balance-summary": "Each customer's or job's balance is its Total on the A/R aging summary for the same date, so the total is Accounts Receivable on the balance sheet. Select a customer to see every transaction behind its balance.",
    "vendor-balance-summary": "Each vendor's balance is its Total on the A/P aging summary for the same date, so the total is Accounts Payable on the balance sheet. Select a vendor to see every transaction behind its balance.",
    "customer-balance-detail": "Every transaction that makes up each customer's balance, oldest first, with the balance after each. A total row closes each customer; it is the customer's line on the customer balance summary. Select a transaction to open it.",
    "vendor-balance-detail": "Every transaction that makes up each vendor's balance, oldest first, with the balance after each. A total row closes each vendor; it is the vendor's line on the vendor balance summary. Select a transaction to open it.",
    "open-purchase-orders": "Purchase orders with something still to receive. Received is the ordered amount of what has arrived; Open balance is the ordered amount of what has not. Select an order to open it.",
    "purchases-by-vendor": "What was bought as items from each vendor in the period: bills, checks, credit card charges and item receipts, at the cost each purchase posted. Purchases entered on an expense account rather than an item are on Expenses by vendor instead.",
    "purchases-by-item": "What was bought of each item in the period, with the quantity bought and its average cost. Stock items count what arrived from vendors; a customer return, an inventory adjustment and a sale are not purchases.",
    "deposit-detail": "Each deposit, then the payments, sales receipts and other lines it gathered, at the amount each took out of the account it came from. Select a transaction to open it.",
    "transaction-list-by-date": "Every transaction posted in the period, in date order: its type and number, who it names, the account it posts to and the other side of the entry. A correction or a void appears as the reversal and replacement it posted. Select a transaction to open it.",
    "reconciliation-discrepancy": "Each finished reconciliation of the account, with the statement's ending balance, its cleared balance as the transactions it cleared stand now, and the difference; under it, each of those transactions that was changed or voided after it was reconciled, at what it was reconciled and what it counts for now. A later reconciliation carries an earlier one's difference in its beginning balance. Change the transaction back, or re-do the reconciliation, to make it tie again.",
    "entries-to-review": "Entries anyone posted that an owner should look at: dated inside a statement already reconciled or a closed period but entered later, cleared by an agent on the very reconciliation it was entered during, touching Opening Balance Equity outside the move-in, or an agent's round, unexplained month-end amount into a bank account. Nothing was refused or changed. Open one to check it; mark it reviewed with review mark, which changes nothing in the entry.",
    "prior-balances": "Each finished reconciliation and the closing date reviewed an account balance as of a date. Where that balance has changed since, the first row shows it as it was when reviewed and as it is now; the rows under it are the entries that moved it, entered after the review but dated on or before it. Open one to see who entered it.",
    "vendor-1099-summary": "Payments made in the calendar year to vendors marked eligible for a 1099, from bank accounts only: payments by credit card are reported by the card company, so they are shown but not counted. This is a report, not a filing.",
}
PO_STATUS = {"open": "Open", "partly_received": "Partly received"}
BATCH = {"original": "", "reversal": "Reversal", "replacement": "Correction"}
BOXES = {"nonemployee_compensation": "1099-NEC box 1"}
CHANGES = {"amount": "Amount changed", "date": "Dated after the statement", "account": "Moved to another account",
           "voided": "Voided"}


def _link(company_id, verb, row, result):
    """Where a row goes when a reader selects it, or nowhere."""
    metadata = result["metadata"]
    watermark = metadata["audit_watermark"]
    as_of = metadata["period"]["date_to"]
    if verb in ("customer-balance-summary", "vendor-balance-summary"):
        party = "customer" if verb.startswith("customer") else "vendor"
        if not row.get(f"{party}_id"):
            return None
        return f"/c/{company_id}/report/{party}-balance-detail?" + urlencode(
            {"f:as_of": as_of, f"f:{party}": row[f"{party}_id"], "source_report_watermark": watermark})
    if verb == "open-purchase-orders":
        return f"/c/{company_id}/purchase-order/{row['purchase_order_id']}"
    if verb in ("purchases-by-vendor", "vendor-1099-summary"):
        return f"/c/{company_id}/vendor/{row['vendor_id']}" if row.get("vendor_id") else None
    if verb == "purchases-by-item":
        return f"/c/{company_id}/item/{row['item_id']}" if row.get("item_id") else None
    if verb == "deposit-detail" and row["kind"] == "deposit":
        return f"/c/{company_id}/deposit/{row['deposit_transaction_id']}"
    return None


def view(result, inputs, company_id, verb):
    rows = []
    for row in result["rows"]:
        shown = {**row, "row_url": _link(company_id, verb, row, result),
                 "document_url": document_link(company_id, row, result["metadata"]["audit_watermark"])
                 if row.get("transaction_id") else None,
                 "is_total": row.get("kind") in ("total", "deposit")}
        if "percent_of_total_millionths" in row:
            shown["display_percent"] = percent(row["percent_of_total_millionths"])
        if verb == "open-purchase-orders":
            shown["status_label"] = PO_STATUS.get(row["status"], row["status"])
        if verb == "transaction-list-by-date":
            shown["batch_label"] = BATCH.get(row["batch_kind"], row["batch_kind"])
        if verb == "vendor-1099-summary":
            shown["box_label"] = BOXES.get(row["box"], row["box"])
            shown["threshold_label"] = "Yes" if row["meets_threshold"] else "No"
        if verb == "deposit-detail" and row["kind"] == "deposit":
            shown["document_url"] = None
        if verb == "reconciliation-discrepancy":
            shown["is_total"] = row["kind"] == "reconciliation"
            shown["change_label"] = CHANGES.get(row["type_of_change"], "")
            shown["reconciliation_label"] = (
                ("Opening balance " if row["reconciliation"] == "opening" else "Statement ")
                + row["statement_date"]) if row["kind"] == "reconciliation" else ""
        if verb == "entries-to-review":
            who = row.get("posted_by") or row.get("posted_by_id") or ""
            if row.get("actor_kind") == "agent":
                who += " (agent" + (" for " + row["on_behalf_of"] if row.get("on_behalf_of") else "") + ")"
            shown["posted_by_label"] = who
            shown["reviewed_label"] = ("Yes, by " + (row["reviewed_by"] or "") if row["reviewed"] else "")
        if verb == "prior-balances":
            shown["is_total"] = row["kind"] == "balance"
            shown["review_label"] = (("Statement " if row["review"] == "statement" else "Closing date ")
                                     + row["review_date"]) if row["kind"] == "balance" else ""
        if verb in ("customer-balance-detail", "vendor-balance-detail") and row["kind"] == "total":
            shown["number"] = "Total"
        rows.append(shown)
    next_fields = {f"f:{key}": (str(value).lower() if isinstance(value, bool) else str(value))
                   for key, value in inputs.items() if key != "cursor" and value is not None}
    next_fields["f:cursor"] = result["next_cursor"]
    period = result["metadata"]["period"]
    return {**result, "rows": rows, "next_fields": next_fields, "verb": verb,
            "layout": LAYOUTS[verb], "total_columns": TOTALS[verb], "note": NOTES[verb],
            "date_from": period["date_from"], "date_to": period["date_to"]}
