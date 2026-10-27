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
    "report open-sales-orders", "report inventory-layers",
    "report inventory-valuation-detail", "report physical-inventory-worksheet", "report quantity-by-site",
    "report mileage-by-vehicle", "report mileage-by-job", "report customer-prepayments",
    "report accountant-changes", "report accountant-review",
    "report items-by-bin", "report serial-lot-stock", "report serial-lot-history", "report lot-trace",
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
    "open-sales-orders": (("Sales order", "number", "label"), ("Date", "date", D), ("Customer", "customer_name", T),
                          ("P.O.", "customer_purchase_order", T), ("Ship", "ship_date", D), ("Item", "item_name", T),
                          ("Ordered", "ordered", "num"), ("Invoiced", "invoiced", "num"), ("Backordered", "backordered", "num"),
                          ("On hand", "quantity_on_hand", "num"), ("Available", "quantity_available", "num"),
                          ("Open amount", "open_amount", M)),
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
                            ("Of which before the move-in", "opening_payments", M),
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
LAYOUTS["inventory-layers"] = (("Item", "item_name", "label"), ("Came in on", "source", T), ("Date", "date", D),
                               ("Quantity", "quantity", "num"), ("Value", "value", M), ("Unit cost", "unit_cost", "num"))
# R104's inventory reports: each row an item's movement, an item to count, or an item at a site.
LAYOUTS["inventory-valuation-detail"] = (("Item", "item_name", "label"), ("Date", "date", D),
                                         ("Transaction", "number", "document"), ("Quantity change", "quantity_change", "num"),
                                         ("Value change", "value_change", M), ("On hand", "quantity_on_hand", "num"),
                                         ("Average cost", "average_cost", M), ("Asset value", "asset_value", M))
LAYOUTS["physical-inventory-worksheet"] = (("Item", "item_name", "label"), ("Description", "description", T),
                                           ("Site", "site_name", T), ("On hand", "quantity_on_hand", "num"),
                                           ("Physical count", "physical_count", T))
LAYOUTS["quantity-by-site"] = (("Item", "item_name", "label"), ("Site", "site_name", T),
                               ("On hand", "quantity_on_hand", "num"), ("Committed", "quantity_committed", "num"),
                               ("For assemblies", "quantity_for_assemblies", "num"),
                               ("Available", "quantity_available", "num"), ("On order", "quantity_on_order", "num"),
                               ("Asset value", "asset_value", M))
# Bins and serial/lot numbers (R104).
LAYOUTS["items-by-bin"] = (("Site", "site_name", "label"), ("Bin", "bin", T), ("Item", "item_name", T), ("Lot", "lot", T),
                           ("Serial number", "serial_number", T), ("On hand", "quantity_on_hand", "num"))
LAYOUTS["serial-lot-stock"] = (("Item", "item_name", "label"), ("Number", "number", T), ("Site", "site_name", T),
                               ("Quantity", "quantity", "num"), ("Expires", "expiry_date", D), ("Expired", "expired_label", T))
LAYOUTS["serial-lot-history"] = (("Date", "date", "label"), ("Document", "document", T), ("Name", "name", T),
                                 ("Site", "site_name", T), ("In", "quantity_in", "num"), ("Out", "quantity_out", "num"),
                                 ("Balance", "balance", "num"))
LAYOUTS["lot-trace"] = LAYOUTS["serial-lot-history"]
# Mileage (R118), customer prepayments (R124) and the accountant's review (R121).
LAYOUTS["mileage-by-vehicle"] = (("Vehicle", "name", "label"), ("Trips", "trips", "num"), ("Miles", "miles", "num"),
                                 ("Amount", "amount", M), ("Miles with no rate", "unrated_miles", "num"))
LAYOUTS["mileage-by-job"] = (("Customer:job", "name", "label"), ("Trips", "trips", "num"), ("Miles", "miles", "num"),
                             ("Billable miles", "billable_miles", "num"), ("Billable amount", "amount", M))
LAYOUTS["customer-prepayments"] = (("Customer", "customer", "label"), ("Date", "date", D), ("Kind", "kind_label", T),
                                   ("Number", "number", T), ("Applied to", "applied_to", T), ("Amount", "amount", M),
                                   ("Held", "held", M))
LAYOUTS["accountant-changes"] = (("When", "at", "label"), ("Change", "summary", T), ("Accountant", "accountant_name", T),
                                 ("By agent", "agent_name", T), ("Reason", "reason", T), ("From a copy", "from_copy_label", T))
LAYOUTS["accountant-review"] = (("To look at", "title", "label"), ("State", "done_label", T), ("Detail", "detail", T))
# The whole-report figures above the table, in reading order, with their headings.
TOTALS = {
    "inventory-layers": (("asset_value", "Inventory value"),),
    "inventory-valuation-detail": (("asset_value", "Inventory value"),),
    "physical-inventory-worksheet": (),
    "mileage-by-vehicle": (), "mileage-by-job": (), "customer-prepayments": (),
    "accountant-changes": (), "accountant-review": (),
    "items-by-bin": (), "serial-lot-stock": (), "serial-lot-history": (), "lot-trace": (),
    "quantity-by-site": (("asset_value", "Inventory value"),),
    "customer-balance-summary": (("balance", "Total balance"),),
    "vendor-balance-summary": (("balance", "Total balance"),),
    "customer-balance-detail": (("balance", "Total balance"),),
    "vendor-balance-detail": (("balance", "Total balance"),),
    "open-purchase-orders": (("amount", "Ordered"), ("received", "Received"), ("open_balance", "Open balance")),
    "open-sales-orders": (("open_amount", "Open amount"), ("lines", "Open lines")),
    "purchases-by-vendor": (("amount", "Total purchases"),),
    "purchases-by-item": (("amount", "Total purchases"),),
    "deposit-detail": (("deposited", "Total deposited"), ("deposits", "Deposits")),
    "transaction-list-by-date": (("transactions", "Transactions"),),
    "vendor-1099-summary": (("reportable", "Reportable payments"), ("threshold", "Threshold"),
                            ("vendors_meeting_threshold", "Vendors at or over threshold"),
                            ("payments", "All payments to 1099 vendors"),
                            ("opening_payments", "Of which before the move-in"),
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
    "items-by-bin": "Each item in each bin of a site, with its lot or serial number; units in no bin are listed under no bin.",
    "serial-lot-stock": "Each tracked item's lots and serial numbers on hand, by site, with each lot's expiry date.",
    "serial-lot-history": "Every movement of one serial or lot number, oldest first, with the balance after each.",
    "lot-trace": "One lot's or serial number's movements, oldest first; the customers, vendors and builds it reached are in the full result.",
    "mileage-by-vehicle": "Miles each vehicle drove in the period, at the mileage rate in force on each trip's end date.",
    "mileage-by-job": "Miles driven for each customer or job, the billable miles, and their amount at the mileage item's price.",
    "customer-prepayments": ("Every prepayment receipt and each draw against it, with what is still held for the customer; "
                             "the total held equals the Customer Prepayments account."),
    "accountant-changes": "Every change an external accountant made in these books or sent back from a copy, newest first.",
    "accountant-review": "What to look at before closing the period under review; each row says whether anything is left.",
    "inventory-valuation-detail": ("Each item's opening position, then every movement in the period with the quantity "
                                   "and value it changed, and the running quantity, average cost and value."),
    "physical-inventory-worksheet": ("Count each item where it is kept and write the count in the last column; "
                                     "enter it with an inventory count."),
    "quantity-by-site": ("Each item at each site: on hand, what open orders commit, what is available, what is on "
                         "order, and the site's share of the item's value."),
    "inventory-layers": ("Each item's cost layers, oldest first. Under FIFO a sale takes the oldest layer first; "
                         "under average cost each item is one row."),
    "customer-balance-summary": "Each customer's or job's balance is its Total on the A/R aging summary for the same date, so the total is Accounts Receivable on the balance sheet. Select a customer to see every transaction behind its balance.",
    "vendor-balance-summary": "Each vendor's balance is its Total on the A/P aging summary for the same date, so the total is Accounts Payable on the balance sheet. Select a vendor to see every transaction behind its balance.",
    "customer-balance-detail": "Every transaction that makes up each customer's balance, oldest first, with the balance after each. A total row closes each customer; it is the customer's line on the customer balance summary. Select a transaction to open it.",
    "vendor-balance-detail": "Every transaction that makes up each vendor's balance, oldest first, with the balance after each. A total row closes each vendor; it is the vendor's line on the vendor balance summary. Select a transaction to open it.",
    "open-purchase-orders": "Purchase orders with something still to receive. Received is the ordered amount of what has arrived; Open balance is the ordered amount of what has not. Select an order to open it.",
    "open-sales-orders": "What customers have ordered and not yet received, one row per open line. Backordered is ordered less invoiced less cancelled. By item, On hand and Available (on hand less what open sales orders and work orders commit) are shown for stocked items. Select an order to open it.",
    "purchases-by-vendor": "What was bought as items from each vendor in the period: bills, checks, credit card charges and item receipts, at the cost each purchase posted. Purchases entered on an expense account rather than an item are on Expenses by vendor instead.",
    "purchases-by-item": "What was bought of each item in the period, with the quantity bought and its average cost. Stock items count what arrived from vendors; a customer return, an inventory adjustment and a sale are not purchases.",
    "deposit-detail": "Each deposit, then the payments, sales receipts and other lines it gathered, at the amount each took out of the account it came from. Select a transaction to open it.",
    "transaction-list-by-date": "Every transaction posted in the period, in date order: its type and number, who it names, the account it posts to and the other side of the entry. A correction or a void appears as the reversal and replacement it posted. Select a transaction to open it.",
    "reconciliation-discrepancy": "Each finished reconciliation of the account, with the statement's ending balance, its cleared balance as the transactions it cleared stand now, and the difference; under it, each of those transactions that was changed or voided after it was reconciled, at what it was reconciled and what it counts for now. A later reconciliation carries an earlier one's difference in its beginning balance. Change the transaction back, or re-do the reconciliation, to make it tie again.",
    "entries-to-review": "Entries anyone posted that an owner should look at: dated inside a statement already reconciled or a closed period but entered later, cleared by an agent on the very reconciliation it was entered during, touching Opening Balance Equity outside the move-in, or an agent's round, unexplained month-end amount into a bank account. Nothing was refused or changed. Open one to check it; mark it reviewed with review mark, which changes nothing in the entry.",
    "prior-balances": "Each finished reconciliation and the closing date reviewed an account balance as of a date. Where that balance has changed since, the first row shows it as it was when reviewed and as it is now; the rows under it are the entries that moved it, entered after the review but dated on or before it. Open one to see who entered it.",
    "vendor-1099-summary": "Payments made in the calendar year to vendors marked eligible for a 1099, from bank accounts only: payments by credit card are reported by the card company, so they are shown but not counted. What a vendor was paid before the books began here, brought from the old books' 1099 Summary, counts as paid on the move-in date. This is a report, not a filing.",
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
    if verb == "open-sales-orders":
        return f"/c/{company_id}/sales-order/{row['sales_order_id']}"
    if verb in ("purchases-by-vendor", "vendor-1099-summary"):
        return f"/c/{company_id}/vendor/{row['vendor_id']}" if row.get("vendor_id") else None
    if verb == "customer-prepayments":
        return f"/c/{company_id}/customer/{row['customer_id']}"
    if verb in ("items-by-bin", "serial-lot-stock") and row.get("item_id"):
        return f"/c/{company_id}/item/{row['item_id']}"
    if verb in ("purchases-by-item", "inventory-layers", "inventory-valuation-detail",
                "physical-inventory-worksheet", "quantity-by-site"):
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
        if verb == "customer-prepayments":
            shown["kind_label"] = "Receipt" if row["kind"] == "receipt" else "Draw"
            shown["is_total"] = False
        if verb == "serial-lot-stock":
            shown["expired_label"] = "Expired" if row["expired"] else ""
        if verb == "accountant-changes":
            shown["from_copy_label"] = "Yes" if row["from_copy"] else "No"
        if verb == "accountant-review":
            shown["done_label"] = "Done" if row["done"] else "To look at"
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
