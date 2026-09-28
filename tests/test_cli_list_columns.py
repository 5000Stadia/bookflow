"""R143 (decision D13): a CLI `<noun> list` prints the columns a bookkeeper reads first.

The declaration is shared with the workbench's list pages; `--columns` shows more or fewer, and
`--json` is the whole payload, unchanged.
"""
import json

import bookflow
from bookflow.adapters.cli.render import render_output
from bookflow.adapters.list_columns import DEFAULT_COLUMNS
from bookflow.adapters.workbench.list_layout import LAYOUTS

COMPANY = ("--company", "Demo Plumbing Co")


def heading(text):
    return text.splitlines()[0].split()


def test_the_workbench_and_the_cli_read_one_declaration():
    for noun, columns in DEFAULT_COLUMNS.items():
        assert LAYOUTS[noun]["columns"] is columns
    assert {"customer", "vendor", "item", "employee", "account"} <= set(DEFAULT_COLUMNS)


def test_a_chosen_column_set_prints_exactly_those_columns_in_order():
    items = [{"name": "A", "phone": "1", "open_balance": {"minor_units": 125, "currency": "USD"}, "memo": "x"}]
    text = render_output({"items": items, "count": 1}, False, columns=["phone", "name"])
    assert heading(text) == ["phone", "name"] and "1.25 USD" not in text
    assert json.loads(render_output({"items": items, "count": 1}, True)) == {"items": items, "count": 1}


def test_cli_lists_print_curated_defaults_and_columns_shows_more(cli, root):
    for noun, columns in DEFAULT_COLUMNS.items():
        text = cli.run(noun, "list", *COMPANY).stdout
        assert heading(text) == list(columns), (noun, heading(text))
    wide = heading(cli.run("customer", "list", *COMPANY, "--columns", "all").stdout)
    assert len(wide) > 20 and {"full_name", "phone", "open_balance", "email", "terms"} <= set(wide)
    chosen = cli.run("customer", "list", *COMPANY, "--columns", "full_name, terms,email").stdout
    assert heading(chosen) == ["full_name", "terms", "email"]
    refused = cli.run("customer", "list", *COMPANY, "--columns", "full_name,no_such_column", expect=None)
    error = json.loads(refused.stderr.strip().splitlines()[-1])
    assert refused.returncode != 0 and error["code"] == "E_USAGE" and error["details"]["unknown_columns"] == ["no_such_column"]
    # A noun with no declaration keeps every column.
    assert len(heading(cli.run("term", "list", *COMPANY).stdout)) > 3


def test_json_is_the_whole_payload_whatever_the_table_shows(cli, root):
    library = bookflow.connect(data_root=str(root)).run("customer list", {}, company="Demo Plumbing Co")
    listed = cli.json("customer", "list", *COMPANY)
    assert len(listed["items"]) == len(library["items"])
    assert [sorted(item) for item in listed["items"]] == [sorted(item) for item in library["items"]]
    assert len(listed["items"][0]) > 50
    narrowed = cli.json("customer", "list", *COMPANY, "--columns", "full_name")
    assert [sorted(item) for item in narrowed["items"]] == [sorted(item) for item in listed["items"]]
