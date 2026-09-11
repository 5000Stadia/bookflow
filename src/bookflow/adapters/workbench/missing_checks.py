"""Presentation of a typed missing-checks report; every figure comes from the core command.

The report is read with a checkbook open, so each row names the checks on either side of
the hole and opens them, and each bank account narrows the report to itself.
"""
from urllib.parse import urlencode


COMMANDS = {"report missing-checks"}
# The counts printed above the rows, in the order a person reads them.
TOTALS = (("gaps", "Holes in the sequence"), ("missing_numbers", "Numbers missing"),
          ("legacy_uncertain_gaps", "Holes that cannot be confirmed"),
          ("retired_numbers", "Numbers a correction gave up"),
          ("duplicate_numbers", "Numbers used twice"), ("duplicate_checks", "Checks sharing a number"),
          ("checks_examined", "Checks examined"), ("numbered_checks", "In a numbered sequence"),
          ("unnumbered_checks", "Numbered some other way"),
          ("checks_off_a_bank_account", "No longer drawn on the account they were written from"),
          ("checks_numbered_before_the_upgrade", "Numbered before the upgrade"))

# What each kind of row is called where a person reads it, rather than by its stored spelling.
KINDS = {"gap": "Missing", "duplicate": "Used twice", "retired": "Given up by a correction"}


def _range(row):
    """The number or the run of numbers this row is about, in the words of its own kind."""
    if row["kind"] == "duplicate":
        return str(row["duplicate_number"])
    if row["kind"] == "retired":
        return str(row["retired_number"])
    return (str(row["first_missing"]) if row["missing_count"] == 1
            else f"{row['first_missing']}–{row['last_missing']}")


def _use(use, company_id):
    """One occupied number, with the check that occupies it opened where it was written."""
    if use is None:
        return None
    return {**use, "document_url": f"/c/{company_id}/check/{use['transaction_id']}"}


def view(result, inputs, company_id):
    as_of = result["metadata"]["period"]["date_to"]
    watermark = result["metadata"]["audit_watermark"]
    rows = []
    for row in result["rows"]:
        rows.append({**row,
            "before": _use(row["before"], company_id),
            "after": _use(row["after"], company_id),
            "checks": [_use(use, company_id) for use in row["checks"]],
            "range_label": _range(row),
            "kind_label": KINDS[row["kind"]],
            "account_url": f"/c/{company_id}/report/missing-checks?" + urlencode(
                {"f:as_of": as_of, "f:account": row["account_id"],
                 "source_report_watermark": watermark})})
    next_fields = {f"f:{key}": (str(value).lower() if isinstance(value, bool) else str(value))
                   for key, value in inputs.items() if key != "cursor" and value is not None}
    next_fields["f:cursor"] = result["next_cursor"]
    return {**result, "rows": rows, "next_fields": next_fields,
            "total_columns": TOTALS, "as_of": as_of}
