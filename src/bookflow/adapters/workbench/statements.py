"""Presentation of typed statement results; all amounts come from core commands."""
from urllib.parse import urlencode

from bookflow.adapters.workbench.transaction_detail import document_link


COMMANDS = {"report profit-and-loss", "report balance-sheet", "report trial-balance",
            "report general-ledger", "report cash-flows", "report income-tax-summary",
            "report profit-and-loss-by-job", "report profit-and-loss-by-class"}
# The two statements that are read across a dimension rather than down one column.
DIMENSIONAL = {"report profit-and-loss-by-job", "report profit-and-loss-by-class"}
# What the reader is being shown a column of, in that reader's words.
DIMENSION_LABEL = {"job": "Customer or job", "class": "Class"}
# The section a row sits in, named the way a profit-and-loss names it.
SECTIONS = {"income": "Income", "cost_of_goods_sold": "Cost of goods sold", "expense": "Expense",
            "other_income": "Other income", "other_expense": "Other expense"}

# How a statement reads from top to bottom. A section lists its accounts under a heading and
# closes with its own total; the other steps are single lines taken from the command's
# whole-statement totals. `subtotal` lines are set apart with a rule and `final` is the bold
# last line. Nothing is added up here: every figure on a line is one the command returned.
LAYOUTS = {
    "report profit-and-loss": (
        ("section", "income", "Income", "income", "Total income", "total"),
        ("section", "cost_of_goods_sold", "Cost of goods sold", "cost_of_goods_sold", "Total cost of goods sold", "total"),
        ("subtotal", "gross_profit", "Gross profit"),
        ("section", "expense", "Expenses", "expense", "Total expenses", "total"),
        ("subtotal", "net_operating_income", "Net operating income"),
        ("section", "other_income", "Other income", "other_income", "Total other income", "total"),
        ("section", "other_expense", "Other expense", "other_expense", "Total other expense", "total"),
        ("final", "net_income", "Net income"),
    ),
    "report balance-sheet": (
        ("section", "assets", "Assets", "assets", "Total assets", "subtotal"),
        ("section", "liabilities", "Liabilities", "liabilities", "Total liabilities", "total"),
        ("section", "equity", "Equity", "posted_equity", "Total posted equity", "total"),
        ("line", "prior_earnings", "Retained earnings (prior years)"),
        ("line", "current_year_income", "Net income (this fiscal year)"),
        ("subtotal", "total_equity", "Total equity"),
        ("final", "liabilities_and_equity", "Total liabilities and equity"),
        ("check", "difference", "Out of balance by"),
    ),
    "report cash-flows": (
        ("heading", "operating", "Operating activities"),
        ("line", "net_income", "Net income"),
        ("section", "operating", None, "operating_adjustments", "Total adjustments to net income", "total"),
        ("subtotal", "operating", "Net cash from operating activities"),
        ("section", "investing", "Investing activities", "investing", "Net cash from investing activities", "subtotal"),
        ("section", "financing", "Financing activities", "financing", "Net cash from financing activities", "subtotal"),
        ("subtotal", "net_change_in_cash", "Net change in cash"),
        ("line", "opening_cash", "Cash at beginning of period"),
        ("final", "closing_cash", "Cash at end of period"),
        ("check", "difference", "Difference from the bank balances"),
    ),
}


def _zero(money):
    return not money or money.get("minor_units") == 0


def lines(command, rows, totals):
    """The statement as the lines a person reads, in the order `LAYOUTS` gives them.

    A section with no account on this page and a zero total is left out rather than shown
    as an empty heading; a check line appears only when it is not zero. An account whose
    section the layout does not name is still listed, under its own heading at the end.
    """
    shown, placed = [], set()
    for step in LAYOUTS[command]:
        kind, key = step[0], step[1]
        if kind == "section":
            _, section, heading, total, total_label, style = step
            members = [row for row in rows if row.get("section") == section]
            placed.add(section)
            if not members and _zero(totals.get(total)):
                continue
            if heading:
                shown.append({"kind": "section", "label": heading, "section": section})
            shown.extend({"kind": "account", "row": row, "amount": row["amount"], "section": section}
                         for row in members)
            shown.append({"kind": style, "label": total_label, "amount": totals.get(total), "total": total})
        elif kind == "heading":
            shown.append({"kind": "section", "label": step[2], "section": key})
        elif kind == "check" and _zero(totals.get(key)):
            continue
        else:
            shown.append({"kind": kind, "label": step[2], "amount": totals.get(key), "total": key})
    stray = [row for row in rows if row.get("section") not in placed]
    for row in stray:
        if not shown or shown[-1].get("section") != row.get("section"):
            shown.append({"kind": "section", "label": SECTIONS.get(row.get("section"), str(row.get("section") or "").replace("_", " ").capitalize()),
                          "section": row.get("section")})
        shown.append({"kind": "account", "row": row, "amount": row["amount"], "section": row.get("section")})
    return shown


def view(result, inputs, company_id, command=None, source_watermark=None):
    period = result["metadata"]["period"]
    dimensional = command in DIMENSIONAL
    general_ledger = command == "report general-ledger"
    # A figure on a statement opens the ledger; a ledger line opens the document that
    # posted it. The position the reader started from is the one they are asking about,
    # so a ledger opened from a statement hands that statement's watermark on rather
    # than re-anchoring the question to its own.
    source = result["metadata"]["audit_watermark"] if source_watermark is None else source_watermark
    rows = []
    for row in result["rows"]:
        # A grouping row -- a tax line, which is not an account -- has no ledger
        # of its own, so it carries no drill-down rather than a broken one.
        if not row.get("account_id"):
            rows.append({**row, "ledger_url": None})
            continue
        query = {"f:account": row["account_id"], "f:date_from": period["date_from"] or "0001-01-01",
                 "f:date_to": period["date_to"], "source_report_watermark": result["metadata"]["audit_watermark"]}
        extra = {}
        if general_ledger:
            extra["document_url"] = document_link(company_id, row, source)
        if dimensional:
            # The row's own cells, already in column order, paired with the column
            # they belong to, so the template never walks two lists in step.
            extra["cells"] = list(zip(result["columns"], row["amounts"]))
            extra["section_label"] = SECTIONS.get(row["section"], row["section"])
        rows.append({**row, **extra, "ledger_url": f"/c/{company_id}/report/general-ledger?" + urlencode(query)})
    next_fields = {f"f:{key}": (str(value).lower() if isinstance(value, bool) else str(value))
                   for key, value in inputs.items() if key != "cursor" and value is not None}
    next_fields["f:cursor"] = result["next_cursor"]
    shown = {**result, "rows": rows, "next_fields": next_fields,
             "basic_report": command in {"report trial-balance", "report general-ledger"},
             "general_ledger": command == "report general-ledger",
             "cash_flows": command == "report cash-flows",
             "tax_summary": command == "report income-tax-summary",
             # Which page reads this result, named here rather than worked out in
             # the template, exactly as the four above are.
             "dimensional": dimensional,
             "lines": lines(command, rows, result["totals"]) if command in LAYOUTS else None}
    if dimensional:
        shown["dimension_label"] = DIMENSION_LABEL[result["dimension"]]
        shown["folded"] = sum(column["folded_count"] or 0 for column in result["columns"])
        # One line per column, so the figure a reader came for -- what this job or
        # this class made -- is readable without scrolling the wide table sideways.
        shown["column_summary"] = [
            {"label": column["label"], "kind": column["kind"],
             "net_income": column["totals"]["net_income"]["amount"]}
            for column in result["columns"]]
    return shown
