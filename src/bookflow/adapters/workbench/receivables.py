"""Presentation of typed receivables results; all amounts come from core commands."""
from urllib.parse import urlencode


COMMANDS = {"report ar-aging", "report open-invoices", "report collections", "report unbilled-costs"}
# Column keys in reading order, with the heading a bookkeeper expects above each.
COLUMNS = ("current", "days_1_30", "days_31_60", "days_61_90", "over_90")
HEADINGS = {"current": "Current", "days_1_30": "1-30", "days_31_60": "31-60",
            "days_61_90": "61-90", "over_90": "Over 90", "total": "Total",
            "amount": "Amount", "applied": "Applied", "balance": "Balance",
            "overdue": "Overdue", "billed": "Already billed", "remaining": "To bill"}
# What one word of billing state says to a person who is about to raise an invoice.
STATES = {"unbilled": "Not billed", "partially_billed": "Partly billed", "no_charge": "No charge"}
# The link a contact point should be reachable through, by the kind the list records.
CONTACT_LINKS = {"main_email": "mailto:", "additional_email": "mailto:", "cc_email": "mailto:",
                 "main_phone": "tel:", "work_phone": "tel:", "home_phone": "tel:",
                 "mobile_phone": "tel:", "other_phone": "tel:", "pager": "tel:"}


def _contact_lines(contact):
    """One contact's telephone numbers and addresses, in the order a caller tries them."""
    lines = []
    for field, label in (("work_phone", "Work"), ("mobile_phone", "Mobile"), ("home_phone", "Home"),
                         ("other_phone", "Other"), ("primary_email", "Email"),
                         ("secondary_email", "Other email")):
        if contact.get(field):
            prefix = "mailto:" if "email" in field else "tel:"
            lines.append({"label": label, "value": contact[field], "href": prefix + contact[field]})
    for point in contact.get("points") or []:
        prefix = CONTACT_LINKS.get(point["kind"])
        lines.append({"label": point["custom_label"] or point["kind"].replace("_", " ").capitalize(),
                      "value": point["value"], "href": (prefix + point["value"]) if prefix else None})
    return lines


def _person(contact):
    name = contact.get("display_name") or " ".join(
        part for part in (contact.get("first_name"), contact.get("last_name")) if part)
    return name or contact["role"].capitalize()


def view(result, inputs, company_id, verb):
    as_of = result["metadata"]["period"]["date_to"]
    rows = []
    for row in result["rows"]:
        link = {}
        if verb == "ar-aging" and row["customer_id"]:
            link["detail_url"] = f"/c/{company_id}/report/open-invoices?" + urlencode(
                {"f:as_of": as_of, "f:customer": row["customer_id"],
                 "source_report_watermark": result["metadata"]["audit_watermark"]})
        elif verb == "collections":
            link["detail_url"] = (f"/c/{company_id}/invoice/{row['transaction_id']}"
                                  if row["kind"] == "invoice" else
                                  f"/c/{company_id}/customer/{row['customer_id']}" if row["customer_id"] else None)
            link["people"] = [{"name": _person(contact), "role": contact["role"].capitalize(),
                               "job_title": contact["job_title"], "inherited": contact["inherited"],
                               "lines": _contact_lines(contact)}
                              for contact in row["contacts"]]
        elif verb == "unbilled-costs":
            # The page a reader goes to next is the one that turns this into money:
            # the source document's own billing window.
            link["detail_url"] = (f"/c/{company_id}/{row['source_kind'].replace('_', '-')}/{row['source_id']}/billing"
                                  if row["kind"] == "line" and row["source_id"] else None)
            link["state_label"] = STATES.get(row["state"], row["state"])
        else:
            link["detail_url"] = f"/c/{company_id}/invoice/{row['transaction_id']}" if verb == "open-invoices" else None
        rows.append({**row, **link})
    next_fields = {f"f:{key}": (str(value).lower() if isinstance(value, bool) else str(value))
                   for key, value in inputs.items() if key != "cursor" and value is not None}
    next_fields["f:cursor"] = result["next_cursor"]
    return {**result, "rows": rows, "next_fields": next_fields,
            "columns": COLUMNS, "headings": HEADINGS, "as_of": as_of}
