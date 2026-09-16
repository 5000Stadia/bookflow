"""Presentation of a typed transaction detail report; all amounts come from the core command.

Transaction detail is the general ledger read line by line, so the page reuses the ledger
page's blocks and adds the two columns that make it a drill-down: who the entry names, and
what the other side of it was. Every row opens the document it came from, and every
account opens its own ledger, because the reason to read this report is to go and look at
something.
"""
from urllib.parse import urlencode

from bookflow.adapters.workbench import routing as Routing
from bookflow.core import registry


COMMANDS = {"report transaction-detail"}
# What the two summary rows of a section are called, in a bookkeeper's words.
KINDS = {"opening": "Opening balance", "closing": "Total and closing balance"}
# The one document whose record page is not named after its own stored type. Every other
# type is its own noun with hyphens, which is checked against the registry rather than
# assumed, so a document family without a page yet simply does not link.
IRREGULAR = {"journal_entry": "journal"}


def document_noun(document_type, money_out_kind=None):
    """The path segment a report row's document opens at, or none where it has no page.

    A document names itself twice over. Its stored transaction type is usually the whole
    answer. The three documents that post *as* a journal entry -- a check, a credit card
    charge and a transfer -- are the exception: their type says `journal_entry`, and only
    ``money_out_documents.kind`` says which of the three a person entered, so a row that
    carries that kind is opened as the document it was entered as rather than as the journal
    it posts through.

    What comes back is the segment, already legal in an ``href``, because every caller puts
    it straight into one. A multi-word noun is spelled with a hyphen there and resolved back
    through :mod:`routing` to ask the registry, which is how ``bill payment`` and ``sales-tax
    payment`` are found at all: looking either up under the hyphenated spelling finds nothing
    registered, and the row silently loses its link.
    """
    named = money_out_kind or document_type
    if not named:
        return None
    segment = IRREGULAR.get(named) or named.replace("_", "-")
    return segment if registry.get(f"{Routing.noun(segment)} show") is not None else None


def document_link(company_id, row, watermark=None):
    """The record page a report row's own document opens at, or none where it has no page.

    One spelling of this link for every accounting report, because a second one drifts from
    the first. Two things ride along with it. A report sums immutable effects, so a document
    deleted out of ordinary lists is still named by the rows it posted and must still open
    behind them: the link asks for retained history wherever the document's own read command
    offers it, which is asked of the registry rather than kept here as a list of the families
    that can be deleted. And the audit position the report was read at travels too, so the
    document answers the question the statement asked rather than a fresh one.
    """
    noun = (document_noun(row.get("transaction_type"), row.get("money_out_kind"))
            if row.get("transaction_id") else None)
    if noun is None:
        return None
    query = {}
    if "include_deleted" in registry.get(f"{Routing.noun(noun)} show").input_model.model_fields:
        query["include_deleted"] = "1"
    if watermark is not None:
        query["source_report_watermark"] = str(watermark)
    return (f"/c/{company_id}/{noun}/{row['transaction_id']}"
            + ("?" + urlencode(query) if query else ""))


def collection_fields(name, values):
    """The repeated control's own form keys, which is how a list survives the next page."""
    fields = {f"collection:{name}": "1"}
    fields.update({f"c:{name}:{index}:value": str(value) for index, value in enumerate(values)})
    return fields


def view(result, inputs, company_id, source_watermark=None):
    period = result["metadata"]["period"]
    watermark = result["metadata"]["audit_watermark"]
    ledger = {"f:date_from": period["date_from"], "f:date_to": period["date_to"],
              "source_report_watermark": watermark}
    # Where this page was itself opened from a statement, the position that statement was
    # read at is the one the reader is asking about; a page opened directly asks about its own.
    source = watermark if source_watermark is None else source_watermark
    rows = []
    for row in result["rows"]:
        rows.append({**row,
            "kind_label": KINDS.get(row["kind"]),
            "document_url": document_link(company_id, row, source),
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
