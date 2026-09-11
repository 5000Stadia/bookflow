"""Presentation of typed statement results; all amounts come from core commands."""
from urllib.parse import urlencode


COMMANDS = {"report profit-and-loss", "report balance-sheet", "report trial-balance",
            "report general-ledger", "report profit-and-loss-by-job", "report profit-and-loss-by-class"}
# The two statements that are read across a dimension rather than down one column.
DIMENSIONAL = {"report profit-and-loss-by-job", "report profit-and-loss-by-class"}
# What the reader is being shown a column of, in that reader's words.
DIMENSION_LABEL = {"job": "Customer or job", "class": "Class"}
# The section a row sits in, named the way a profit-and-loss names it.
SECTIONS = {"income": "Income", "cost_of_goods_sold": "Cost of goods sold", "expense": "Expense",
            "other_income": "Other income", "other_expense": "Other expense"}


def view(result, inputs, company_id, command=None):
    period = result["metadata"]["period"]
    dimensional = command in DIMENSIONAL
    rows = []
    for row in result["rows"]:
        query = {"f:account": row["account_id"], "f:date_from": period["date_from"] or "0001-01-01",
                 "f:date_to": period["date_to"], "source_report_watermark": result["metadata"]["audit_watermark"]}
        extra = {}
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
             "dimensional": dimensional}
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
