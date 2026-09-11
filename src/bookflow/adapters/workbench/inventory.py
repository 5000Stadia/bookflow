"""Presentation of the two stock reports; every amount comes from the core command.

Nothing is computed here. The view adds a link from each row to the item it names and the
hidden fields the continuation form submits, and that is all: the quantities, the average
cost and the asset value are what ``report inventory-valuation`` and ``report stock-status``
returned, and the total under them is the total those commands computed over the whole
filter, not over the page.
"""

COMMANDS = {"report inventory-valuation", "report stock-status"}

HEADINGS = {"item_name": "Item", "description": "Description", "quantity_on_hand": "On hand",
            "quantity_available": "Available", "quantity_on_order": "On order",
            "average_cost": "Avg cost", "asset_value": "Asset value",
            "reorder_point_min": "Reorder point", "reorder_point_max": "Max",
            "below_reorder_point": "Order"}


def view(result, inputs, company_id, verb):
    as_of = result["metadata"]["period"]["date_to"]
    rows = [{**row, "detail_url": f"/c/{company_id}/item/{row['item_id']}"} for row in result["rows"]]
    next_fields = {f"f:{key}": (str(value).lower() if isinstance(value, bool) else str(value))
                   for key, value in inputs.items() if key != "cursor" and value is not None}
    next_fields["f:cursor"] = result["next_cursor"]
    return {**result, "rows": rows, "next_fields": next_fields, "headings": HEADINGS,
            "as_of": as_of, "verb": verb}
