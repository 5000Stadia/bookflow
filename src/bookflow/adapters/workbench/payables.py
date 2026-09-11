"""Presentation of typed payables results; all amounts come from core commands."""
from urllib.parse import urlencode


COMMANDS = {"report ap-aging", "report unpaid-bills"}
# Column keys in reading order, with the heading a bookkeeper expects above each.
COLUMNS = ("current", "days_1_30", "days_31_60", "days_61_90", "over_90")
HEADINGS = {"current": "Current", "days_1_30": "1-30", "days_31_60": "31-60",
            "days_61_90": "61-90", "over_90": "Over 90", "total": "Total",
            "amount": "Amount", "applied": "Applied", "balance": "Open balance"}


def view(result, inputs, company_id, verb):
    as_of = result["metadata"]["period"]["date_to"]
    rows = []
    for row in result["rows"]:
        link = {}
        if verb == "ap-aging" and row["vendor_id"]:
            link["detail_url"] = f"/c/{company_id}/report/unpaid-bills?" + urlencode(
                {"f:as_of": as_of, "f:vendor": row["vendor_id"],
                 "source_report_watermark": result["metadata"]["audit_watermark"]})
        else:
            link["detail_url"] = f"/c/{company_id}/bill/{row['transaction_id']}" if verb == "unpaid-bills" else None
        rows.append({**row, **link})
    next_fields = {f"f:{key}": (str(value).lower() if isinstance(value, bool) else str(value))
                   for key, value in inputs.items() if key != "cursor" and value is not None}
    next_fields["f:cursor"] = result["next_cursor"]
    return {**result, "rows": rows, "next_fields": next_fields,
            "columns": COLUMNS, "headings": HEADINGS, "as_of": as_of}
