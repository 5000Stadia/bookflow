"""Cutover blind trial (notes/blind-trials-20260928.md): a fresh agent never found `cutover`.

The command list overflowed the MCP budget, migration words did not lead to `cutover`, a cut
`account list` read as complete, and two refusals named the wrong fix.
"""
import pytest

import bookflow
from bookflow.core.errors import BookflowError


def test_every_command_list_page_fits_the_budget_and_paging_reaches_every_command():
    from bookflow.adapters.mcp.budget import BUDGET, size
    from bookflow.adapters.mcp.catalog import _commands, list_commands
    names, cursor, pages = [], None, 0
    while True:
        page = list_commands(limit=200, cursor=cursor)
        assert size(page) <= BUDGET
        names += [row["name"] for row in page["commands"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages > 1 and names == sorted({cmd.name for cmd in _commands()})
    first = list_commands(limit=200)
    assert first["total"] == len(names) and "page_note" in first and "next_cursor" in first["page_note"]
    assert len(first["commands"]) < 200
    # A listed row is compact: the opening of its description, flags only when unusual.
    row = next(r for r in first["commands"] if r["name"] == "account list")
    assert set(row) == {"name", "description", "scope", "kind", "authorization"}


@pytest.mark.parametrize("words", ["import", "migrate", "migrat", "migration", "move in", "switch from", "convert",
                                   "conversion", "IIF", "QuickBooks", "opening balance", "opening balances",
                                   "opening", "old books", "set up from", "import from QuickBooks"])
def test_migration_words_lead_to_cutover_first(words):
    from bookflow.adapters.mcp.catalog import list_commands
    page = list_commands(prefix=words)
    assert page["match"] == "keywords"
    assert [row["name"] for row in page["commands"]][:3] == ["cutover plan", "cutover apply", "cutover tie-out"], words


def test_cutover_plan_says_what_it_is_for_and_the_guide_points_there():
    from importlib.resources import files
    from bookflow.adapters.mcp.catalog import list_commands
    from bookflow.adapters.mcp.envelopes import TOOLS
    row = list_commands(prefix="cutover plan")["commands"][0]
    assert row["description"].startswith("Move a company in from its old books (QuickBooks Desktop")
    assert "prefix cutover" in TOOLS["bookflow_list_commands"][1]
    guide = files("bookflow.documentation.resources").joinpath("agent-guide.md").read_text()
    section = guide.split("## Moving a company in", 1)[1].split("\n## ", 1)[0]
    assert "`cutover plan`" in section and "`cutover apply`" in section and "`cutover tie-out`" in section


def test_a_cut_list_sheds_empty_fields_first_and_says_plainly_when_rows_are_missing():
    from bookflow.adapters.mcp.budget import BUDGET, fit, size
    paging = {"command": "account list", "paged": False, "narrow": ["query", "filter"], "alternative": "account query"}

    def account(i):
        return {"id": f"01ACCOUNT{i:017d}", "created_at": "2026-10-08T16:54:23.442-05:00", "created_by": "01M4ER507QVZZ0V",
                "updated_at": "2026-10-08T16:54:23.442-05:00", "updated_by": "01M4ER507QVZZ0V", "name": f"Account {i}",
                "active": True, "has_children": False, "balance": {"amount": "0.00", "currency": "USD"},
                **{f"empty_{k}": None for k in range(40)}, "tags": []}

    whole = fit({"count": 23, "items": [account(i) for i in range(23)]}, paging=paging)
    assert size(whole) <= BUDGET and len(whole["items"]) == 23 and list(whole)[0] == "result_compacted"
    assert whole["items"][0] == {"id": "01ACCOUNT00000000000000000", "name": "Account 0", "active": True,
                                 "has_children": False, "balance": {"amount": "0.00", "currency": "USD"}}
    cut = fit({"count": 200, "items": [account(i) for i in range(200)]}, paging=paging)
    kept = len(cut["items"])
    note = cut["result_compacted"]
    assert size(cut) <= BUDGET and list(cut)[0] == "result_compacted"
    assert note["showing"] == f"showing {kept} of 200 items"
    assert note["full_result"].startswith(f"Not every row is here: showing {kept} of 200 items.")
    assert "account query" in note["full_result"]


def _company(root):
    c = bookflow.connect(data_root=str(root))
    return c, c.company.list()["items"][0]["company_id"]


def test_payment_receive_names_the_payment_methods(root):
    c, company = _company(root)
    customer = c.run("customer create", {"name": "Method Test Co"}, company=company, reason="fixture")["id"]
    receipt = {"customer": customer, "date": "2026-09-27", "amount": "10.00", "operation_key": "method-test"}
    with pytest.raises(BookflowError) as missing:
        c.run("payment receive", receipt, company=company, reason="test", dry_run=True)
    allowed = missing.value.details["allowed"]
    assert "Check" in allowed and "payment-method list" in missing.value.details["fields"][0]["problem"]
    with pytest.raises(BookflowError) as wrong:
        c.run("payment receive", {**receipt, "payment_method": "Carrier Pigeon"}, company=company, reason="test",
              dry_run=True)
    assert wrong.value.details["allowed"] == allowed and "payment-method list" in wrong.value.details["hint"]


def test_item_create_with_a_vendor_names_purchase_enabled(root):
    c, company = _company(root)
    vendor = c.run("vendor create", {"name": "Item Test Supply"}, company=company, reason="fixture")["id"]
    income = next(a["id"] for a in c.run("account list", {}, company=company)["items"] if a["type"] == "income")
    service = {"type": "service", "name": "Drain snaking", "description": "Drain snaking", "price": "95.00",
               "income_account_id": income, "sales_enabled": True}
    try:
        c.run("item create", service, company=company, reason="test", dry_run=True)
    except BookflowError as exc:  # a company that taxes services needs a code too
        if any(f["field"] == "sales_tax_code_id" for f in exc.details.get("fields", [])):
            code = c.run("sales-tax-code query", {}, company=company)["items"][0]["id"]
            service["sales_tax_code_id"] = code
        else:
            raise
    c.run("item create", service, company=company, reason="test", dry_run=True)
    with pytest.raises(BookflowError) as caught:
        c.run("item create", {**service, "preferred_vendor_id": vendor}, company=company, reason="test", dry_run=True)
    field = caught.value.details["fields"][0]
    assert field["field"] == "purchase_enabled"
    assert "set purchase_enabled to true" in field["problem"] and "expense_account_id" in field["problem"]
