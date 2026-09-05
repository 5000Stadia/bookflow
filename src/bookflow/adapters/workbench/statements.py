"""Presentation of typed statement results; all amounts come from core commands."""
from urllib.parse import urlencode


COMMANDS = {"report profit-and-loss", "report balance-sheet"}


def view(result, inputs, company_id):
    period = result["metadata"]["period"]
    rows = []
    for row in result["rows"]:
        query = {"f:account": row["account_id"], "f:date_from": period["date_from"] or "0001-01-01",
                 "f:date_to": period["date_to"], "source_report_watermark": result["metadata"]["audit_watermark"]}
        rows.append({**row, "ledger_url": f"/c/{company_id}/report/general-ledger?" + urlencode(query)})
    next_fields = {f"f:{key}": (str(value).lower() if isinstance(value, bool) else str(value))
                   for key, value in inputs.items() if key != "cursor" and value is not None}
    next_fields["f:cursor"] = result["next_cursor"]
    return {**result, "rows": rows, "next_fields": next_fields}
