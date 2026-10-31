"""R107 increment 8: the agent pass (design/bulk-import.md §11, increment 8, and the closing trial).

The demo company holds a messy customer file and a pasted supplier list an agent finds with `attachment list`; owner
words find the import, opening and grid-edit commands; the agent guide teaches the list import from preview to undo.
"""
from importlib.resources import files

import pytest

from bookflow.adapters.mcp.catalog import list_commands
from tests.test_imports import books, run_all  # noqa: F401 - fixture

DEMO = "Demo Plumbing Co"


def test_the_demo_holds_a_messy_customer_file_and_a_paste_to_import(client):
    company = client.company.show(company=DEMO)["company_id"]
    run = lambda name, data, **kw: client.run(name, data, company=DEMO, **kw)
    attached = {a["caption"]: a["attachment_id"] for a in
                run("attachment list", {"record_type": "company_info", "record_id": company})["items"]}
    customers = attached["Customer list exported from the old system, to import"]
    suppliers = attached["New suppliers, pasted from a spreadsheet, to import"]
    preview = run("import preview", {"kind": "customer", "attachment": customers, "source": "Old system"},
                  reason="Try the demo import")
    assert preview["counts"] == {"create": 3, "waiting": 4}
    assert {(d["value"], d["row_count"]) for d in preview["decisions"]} == {("Net 30 days", 3), ("COD", 1)}
    assert preview["mapping"]["formats"] == {"date_order": "DMY", "decimal": ",", "thousands": "."}
    assert preview["mapping"]["columns"]["Job of"] == "parent_id" and preview["mapping"]["columns"]["St"] == \
        "billing_address.state" and preview["mapping"]["unmapped"] == []
    assert any("cp1252" in note for note in preview["notes"])
    pasted = run("import preview", {"kind": "vendor", "attachment": suppliers}, reason="Try the demo paste")
    assert pasted["counts"] == {"create": 3, "waiting": 1}
    assert [d["value"] for d in pasted["decisions"]] == ["Net 30 days"]
    # Nothing was imported by the seed or the previews.
    found = run("customer query", {"query": "Lumière"})["items"]
    assert not found


@pytest.mark.parametrize("words,first", [
    ("csv", "import preview"), ("spreadsheet", "import preview"), ("paste", "import preview"),
    ("load these open invoices from the spreadsheet", "import preview"), ("customer list from my old system", "import preview"),
    ("undo import", "import reverse"), ("that import was wrong", "import reverse"),
    ("reprice", "bulk-edit preview"), ("raise every price in the Fixtures category", "bulk-edit preview"),
    ("mass update", "bulk-edit preview"), ("undo that price change", "bulk-edit reverse"),
    ("starting from nothing", "opening-draft create"), ("from scratch", "opening-draft create"),
])
def test_owner_words_find_the_import_opening_and_grid_edit_commands(words, first):
    page = list_commands(prefix=words, limit=5)
    assert page["match"] == "keywords" and page["commands"][0]["name"] == first, [r["name"] for r in page["commands"]]


def test_opening_balance_still_leads_to_the_move_in_then_the_guided_opening():
    names = [row["name"] for row in list_commands(prefix="opening balance", limit=8)["commands"]]
    assert names[:3] == ["cutover plan", "cutover apply", "cutover tie-out"] and names[3] == "opening-draft create"


def test_the_agent_guide_teaches_a_list_import_from_preview_to_undo():
    guide = files("bookflow.documentation.resources").joinpath("agent-guide.md").read_text()
    section = " ".join(guide.split("## Importing a list from a file", 1)[1].split("\n## ", 1)[0].split())
    for term in ("attachment list company_info", "import preview", "import decide", "import run", "import errors",
                 "import-mapping save", "Same export as last month", "already_in", "import reverse", "--dry-run",
                 "batch query", "one check of its exact details", "just runs", "ask the"):
        assert term in section, term


def test_a_column_no_field_takes_is_named_unless_left_out_on_purpose(client):
    """Closing trial: a Balance column was dropped with no word; the preview now says so."""
    run = lambda name, data: client.run(name, data, company=DEMO, reason="Closing trial fix")
    content = "Name;Balance;Fax\nNote One;(120,50);\nNote Two;310,00;\n"
    noted = run("import preview", {"kind": "customer", "content": content})["notes"]
    assert any(n.startswith("not imported: Balance (") for n in noted), noted  # Fax is empty: nothing to lose
    quiet = run("import preview", {"kind": "customer", "content": content, "columns": {"Balance": None}})["notes"]
    assert not any(n.startswith("not imported") for n in quiet), quiet


def test_template_fields_name_their_input_path_for_every_caller(client):
    """Closing trial: over MCP every `path` key is nulled for anyone but a hub admin, so each field also says `field`."""
    fields = client.run("import template", {"kind": "customer"}, company=DEMO)["fields"]
    assert fields[0]["field"] == "name" and all(f["field"] == f["path"] for f in fields)


def test_next_months_file_compares_rows_an_earlier_run_made(books):
    """Closing trial: next month's export changed Bayside's phone; the re-run read it as already in without a word,
    and update mode never reached it. Now the row is still `already_in` (a re-run never refuses its own rows) but the
    change is named, and update mode changes it."""
    first = "Customer,Phone\nBayside Grill,555-0122\nOceanview Motel,555-0144\n"
    made = books.run("import preview", {"kind": "customer", "content": first, "source": "Old export"})
    run_all(books, made["run"]["id"])
    later = "Customer,Phone\nBayside Grill,555-0999\nOceanview Motel,555-0144\nLighthouse Bakery,555-0211\n"
    again = books.run("import preview", {"kind": "customer", "content": later, "source": "Old export"})
    assert again["counts"] == {"already_in": 2, "create": 1}, again["counts"]
    assert any(n.startswith("1 row(s) an earlier run of this source made now differ from the file (rows 1)")
               for n in again["notes"]), again["notes"]
    rows = {r["row"]: r for r in books.run("import rows", {"run": again["run"]["id"]})["items"]}
    assert rows[1]["code"] == "changed_since" and "differs in phone" in rows[1]["message"]
    run_all(books, again["run"]["id"])
    assert books.run("customer show", {"customer": "Bayside Grill"})["phone"] == "555-0122"
    update = books.run("import preview", {"kind": "customer", "content": later, "source": "Old export", "mode": "update"})
    assert update["counts"] == {"update": 1, "already_in": 2}, update["counts"]
    run_all(books, update["run"]["id"])
    assert books.run("customer show", {"customer": "Bayside Grill"})["phone"] == "555-0999"
