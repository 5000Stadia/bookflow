"""Presentation of a typed transaction detail report; all amounts come from the core command.

Transaction detail is the general ledger read line by line, so the page reuses the ledger
page's blocks and adds the two columns that make it a drill-down: who the entry names, and
what the other side of it was. Every row opens the document it came from, and every
account opens its own ledger, because the reason to read this report is to go and look at
something.
"""
from urllib.parse import urlencode

from bookflow.core import registry


COMMANDS = {"report transaction-detail"}
# What the two summary rows of a section are called, in a bookkeeper's words.
KINDS = {"opening": "Opening balance", "closing": "Total and closing balance"}
# The one document whose record page is not named after its own stored type. Every other
# type is its own noun with hyphens, which is checked against the registry rather than
# assumed, so a document family without a page yet simply does not link.
IRREGULAR = {"journal_entry": "journal"}


def document_noun(document_type):
    """The record page a posting row opens, or none where that document has no page."""
    if not document_type:
        return None
    noun = IRREGULAR.get(document_type, document_type.replace("_", "-"))
    return noun if registry.get(f"{noun} show") is not None else None


def collection_fields(name, values):
    """The repeated control's own form keys, which is how a list survives the next page."""
    fields = {f"collection:{name}": "1"}
    fields.update({f"c:{name}:{index}:value": str(value) for index, value in enumerate(values)})
    return fields


def view(result, inputs, company_id):
    period = result["metadata"]["period"]
    watermark = result["metadata"]["audit_watermark"]
    ledger = {"f:date_from": period["date_from"], "f:date_to": period["date_to"],
              "source_report_watermark": watermark}
    rows = []
    for row in result["rows"]:
        noun = document_noun(row["transaction_type"]) if row["transaction_id"] else None
        rows.append({**row,
            "kind_label": KINDS.get(row["kind"]),
            "document_url": f"/c/{company_id}/{noun}/{row['transaction_id']}" if noun else None,
            "ledger_url": f"/c/{company_id}/report/general-ledger?" + urlencode(
                {**ledger, "f:account": row["account_id"]}),
            "split_url": (f"/c/{company_id}/report/general-ledger?" + urlencode(
                {**ledger, "f:account": row["split_account_id"]})) if row["split_account_id"] else None})
    next_fields = {}
    for key, value in inputs.items():
        if key == "cursor" or value is None:
            continue
        if isinstance(value, list):
            next_fields.update(collection_fields(key, value))
        else:
            next_fields[f"f:{key}"] = str(value).lower() if isinstance(value, bool) else str(value)
    next_fields["f:cursor"] = result["next_cursor"]
    return {**result, "rows": rows, "next_fields": next_fields,
            "date_from": period["date_from"], "as_of": period["date_to"]}
