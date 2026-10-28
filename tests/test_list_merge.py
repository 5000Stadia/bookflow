"""R117 accounts and items: what a merge refuses, idempotence, people only, and undo.

The twin-company check (test_list_merge_twins.py) proves the reads; this file proves the guards,
on one small company of its own.
"""
import pytest

from bookflow.core.errors import BookflowError
from tests.conftest import as_user, make_actor
from tests.test_list_merge_twins import ORG, client  # noqa: F401  (the empty-root fixture)

COMPANY = "Merge Rules LLC"
REASON = "duplicate entry"


def _run(client, name, body, **kw):
    from bookflow.core import registry
    command = registry.get(name)
    if command.is_write:
        kw.setdefault("reason", REASON)
    return client.run(name, body, company=COMPANY, **kw)


@pytest.fixture
def books(client):  # noqa: F811
    client.run("company new", dict(legal_name=COMPANY, home_currency="USD", organization=ORG, timezone="UTC",
                                   chart="general"))
    for name, kind in (("Bank One", "bank"), ("Bank Two", "bank"), ("Truck Fuel", "expense"), ("Truck Fuel Costs", "expense"),
                       ("Gas Spend", "expense"), ("Side Income", "income")):
        _run(client, "account create", dict(name=name, type=kind))
    parent = _run(client, "account show", dict(account="Gas Spend"))["id"]
    _run(client, "account create", dict(name="Diesel Spend", type="expense", parent_id=parent))
    _run(client, "journal post", dict(date="2026-02-01", lines=[dict(account="Truck Fuel", side="debit", amount="10.00"),
                                                                 dict(account="Bank One", side="credit", amount="10.00")]))
    return client


def _refused(client, merged, into, problem, kind="account"):
    with pytest.raises(BookflowError) as error:
        _run(client, f"{kind} merge", dict(merged=merged, into=into), dry_run=True)
    assert error.value.code == "E_MERGE_REFUSED", error.value
    assert problem in error.value.details["problem"], error.value.details["problem"]


def test_account_refusals(books):
    _refused(books, "Truck Fuel", "Truck Fuel", "into itself")
    _refused(books, "Truck Fuel", "Side Income", "different types")
    _refused(books, "Gas Spend", "Truck Fuel", "subaccounts")
    _refused(books, "Bank One", "Bank Two", "reconciliation clears each line")
    _refused(books, "Accounts Receivable", "Bank Two", "different types")
    _refused(books, "Undeposited Funds", "Truck Fuel", "different types")
    _refused(books, "Uncategorized Expense (Ask My Accountant)", "Truck Fuel", "Uncategorized")
    # A system account on either side, of the same type.
    _run(books, "account create", dict(name="Opening Equity Two", type="equity"))
    _refused(books, "Opening Equity Two", "Opening Balance Equity", "system account")
    # An empty bank account may merge: nothing posted names it.
    assert _run(books, "account merge", dict(merged="Bank Two", into="Bank One"), dry_run=True)["changed"]


def test_account_merge_is_idempotent_chains_are_refused_and_undo_restores(books):
    done = _run(books, "account merge", dict(merged="Truck Fuel", into="Truck Fuel Costs"))
    assert done["changed"] and done["document_count"] == 1
    assert done["survivor_balance_after"]["minor_units"] == 1000
    assert any("Reports of every period" in note for note in done["notes"])
    assert not _run(books, "account merge", dict(merged="Truck Fuel", into="Truck Fuel Costs"))["changed"]
    _refused(books, "Truck Fuel", "Gas Spend", "already merged")
    _refused(books, "Truck Fuel Costs", "Gas Spend:Diesel Spend", "other accounts are merged into this one")
    _refused(books, "Gas Spend:Diesel Spend", "Truck Fuel", "itself merged away")
    # The merged account is hidden and cannot be made active while merged.
    listed = {a["full_name"] for a in _run(books, "account list", {})["items"]}
    assert "Truck Fuel" not in listed and "Truck Fuel Costs" in listed
    assert _run(books, "account show", dict(account="Truck Fuel Costs"))["balance"]["minor_units"] == 1000
    # A new document naming the merged account saves to the survivor.
    _run(books, "journal post", dict(date="2026-02-02", lines=[dict(account="Truck Fuel", side="debit", amount="5.00"),
                                                                dict(account="Bank One", side="credit", amount="5.00")]))
    assert _run(books, "account show", dict(account="Truck Fuel Costs"))["balance"]["minor_units"] == 1500
    undone = _run(books, "account unmerge", dict(merged="Truck Fuel"))
    assert undone["changed"] and undone["reactivated"]
    assert not _run(books, "account unmerge", dict(merged="Truck Fuel"))["changed"]
    assert _run(books, "account show", dict(account="Truck Fuel"))["balance"]["minor_units"] == 1000
    assert _run(books, "account show", dict(account="Truck Fuel Costs"))["balance"]["minor_units"] == 500
    with pytest.raises(BookflowError) as error:
        _run(books, "account unmerge", dict(merged="Gas Spend"))
    assert error.value.code == "E_MERGE_REFUSED"


def test_a_reason_is_required(books):
    with pytest.raises(BookflowError) as error:
        books.run("account merge", dict(merged="Truck Fuel", into="Truck Fuel Costs"), company=COMPANY)
    assert error.value.code == "E_REASON_REQUIRED"


def test_an_agent_is_refused(books, tmp_path):
    from bookflow.core import registry
    from bookflow.core.config import Config
    from bookflow.core.context import Context, Interface
    from bookflow.core.dispatch import run as dispatch_run
    from tests.conftest import make_agent
    import os
    from pathlib import Path
    root = Path(os.environ["BOOKFLOW_DATA_ROOT"])
    company = books.company.show(company=COMPANY)["company_id"]
    person = make_actor(root, "merge-person", company_role=(company, "standard"))
    agent = make_agent(lambda name, body: books.run(name, body), "merge-agent", principals=person, company=company)
    config = Config.load(root / "config.toml")
    config.set_user("merge-agent", agent)
    config.save()
    for name, body in (("account merge", {"merged": "Truck Fuel", "into": "Truck Fuel Costs"}), ("account unmerge", {"merged": "Truck Fuel"}),
                       ("item merge", {"merged": "A", "into": "B"}), ("item unmerge", {"merged": "A"})):
        ctx = Context.new(Interface.python, "merge agent witness", on_behalf_of=person, reason="an agent tidying up")
        with pytest.raises(BookflowError) as error:
            dispatch_run(registry.get(name), body, ctx, data_root=str(root), company_selector=COMPANY,
                         company_source="option", _login="merge-agent")
        assert error.value.code == "E_PERMISSION" and error.value.details.get("capability") == "list.merge", name
        assert error.value.details["reason"] == "capability_not_granted", name
    assert as_user(root, "merge-person").run("account merge", {"merged": "Truck Fuel", "into": "Truck Fuel Costs"},
                                             company=COMPANY, reason=REASON)["changed"]


def _item(client, name, kind="service", **extra):
    tax = next(c["id"] for c in _run(client, "sales-tax-code list", {})["items"] if not c.get("taxable"))
    income = _run(client, "account show", dict(account="Side Income"))["id"]
    body = dict(name=name, type=kind, description=name, sales_tax_code_id=tax, price="10.00", income_account_id=income)
    if kind in ("inventory_part", "inventory_assembly"):
        body.update(purchase_description=name, cost="4.00",
                    cogs_account_id=_run(client, "account show", dict(account="Cost of Goods Sold"))["id"],
                    asset_account_id=_run(client, "account show", dict(account="Inventory Asset"))["id"])
    body.update(extra)
    return _run(client, "item create", body)["id"]


def test_item_refusals(books):
    a, b = _item(books, "Snake Rental"), _item(books, "Snake Hire")
    _item(books, "Snake Part", kind="non_inventory_part")
    _refused(books, "Snake Rental", "Snake Part", "different types", kind="item")
    _item(books, "Sub Snake", parent_id=a)
    _refused(books, "Snake Rental", "Snake Hire", "sub-items", kind="item")
    _run(books, "item create", dict(name="Snake Kit", type="group", description="Kit", print_members=True,
                                    members=[dict(component_item_id=b, quantity="1")]))
    _run(books, "item create", dict(name="Snake Kit Two", type="group", description="Kit", print_members=True,
                                    members=[dict(component_item_id=b, quantity="1")]))
    _refused(books, "Snake Kit", "Snake Kit Two", "groups are never merged", kind="item")
    # Two stocked parts: one bought, so its average cost has a history.
    _item(books, "Valve", kind="inventory_part")
    _item(books, "Valve B", kind="inventory_part")
    _run(books, "vendor create", dict(name="Valve Vendor"))
    _run(books, "bill post", dict(vendor="Valve Vendor", date="2026-02-03", items=[dict(item="Valve B", quantity="2", unit_cost="4.00")]))
    _refused(books, "Valve", "Valve B", "stock movements", kind="item")
    # A group listing both with different quantities cannot keep one row for both.
    c = _item(books, "Snake Cable")
    _run(books, "item create", dict(name="Cable Kit", type="group", description="Kit", print_members=True,
                                    members=[dict(component_item_id=b, quantity="1"), dict(component_item_id=c, quantity="2")]))
    preview = _run(books, "item merge", dict(merged="Snake Cable", into="Snake Hire"), dry_run=True)
    assert any(r["label"] == "Cable Kit: Snake Hire ×1 and Snake Cable ×2 become Snake Hire ×3" for r in preview["references"])


def test_item_merge_repoints_and_undo_puts_back(books):
    keep, dup = _item(books, "Jetting"), _item(books, "Jetting Dup")
    _run(books, "item create", dict(name="Jet Kit", type="group", description="Kit", print_members=True,
                                    members=[dict(component_item_id=dup, quantity="3")]))
    preview = _run(books, "item merge", dict(merged="Jetting Dup", into="Jetting"), dry_run=True)
    assert [(r["table"], r["action"]) for r in preview["references"]] == [("item_members", "repoint")]
    assert preview["merged_balance"] is None
    _run(books, "item merge", dict(merged="Jetting Dup", into="Jetting"))
    kit = _run(books, "item show", dict(item="Jet Kit"))
    assert [m["component_item_id"] for m in kit["members"]] == [keep]
    assert "Jetting Dup" not in {i["full_name"] for i in _run(books, "item list", {})["items"]}
    # A new sale naming the merged item sells the survivor.
    _run(books, "customer create", dict(name="Jet Customer"))
    sale = _run(books, "invoice post", dict(customer="Jet Customer", date="2026-02-04",
                                            lines=[dict(item="Jetting Dup", quantity="1")]))
    assert _run(books, "invoice show", dict(invoice=sale["id"]))["revision"]["lines"][0]["item_id"] == keep
    assert _run(books, "item unmerge", dict(merged="Jetting Dup"))["restored"] == 1
    kit = _run(books, "item show", dict(item="Jet Kit"))
    assert [m["component_item_id"] for m in kit["members"]] == [dup]


def test_each_money_out_family_saves_again_after_an_account_merge(books):
    """A memo-only correction of a check, card charge, card credit and vendor credit whose lines
    (or captured item expense account) name the merged account saves with the survivor."""
    _run(books, "account create", dict(name="Visa Card", type="credit_card"))
    _run(books, "vendor create", dict(name="Fuel Vendor"))
    expense = _run(books, "account show", dict(account="Truck Fuel"))["id"]
    _item(books, "Fuel Delivery", purchase_enabled=True, purchase_description="Delivery", cost="5.00",
          expense_account_id=expense)
    payee = dict(name_type="vendor", name_id="Fuel Vendor")
    line = [dict(account="Truck Fuel", amount="12.00")]
    made = {
        "check": _run(books, "check post", dict(account="Bank One", pay_to=payee, date="2026-02-10", number="7",
                                                 amount="12.00", expenses=line)),
        "card-charge": _run(books, "card-charge post", dict(account="Visa Card", pay_to=payee, date="2026-02-10",
                                                             amount="12.00", expenses=line)),
        "card-credit": _run(books, "card-credit post", dict(account="Visa Card", pay_to=payee, date="2026-02-11",
                                                             amount="12.00", expenses=line)),
        "vendor-credit": _run(books, "vendor-credit post", dict(vendor="Fuel Vendor", date="2026-02-12", expenses=line,
                                                                 items=[dict(item="Fuel Delivery", quantity="1", unit_cost="5.00")])),
    }
    _run(books, "account merge", dict(merged="Truck Fuel", into="Truck Fuel Costs"))
    survivor = _run(books, "account show", dict(account="Truck Fuel Costs"))
    for family, doc in made.items():
        selector = {"check": "check", "card-charge": "card_charge", "card-credit": "card_credit",
                    "vendor-credit": "credit"}[family]
        shown = _run(books, f"{family} show", {selector: doc["id"]})
        _run(books, f"{family} update", {selector: doc["id"], "expected_version": shown["version"],
                                         "memo": "corrected after the merge"})
    # The survivor holds everything, the merged account reads as nothing.
    after = _run(books, "account show", dict(account="Truck Fuel Costs"))
    assert after["balance"] == survivor["balance"]
    assert _run(books, "account list", dict(include_inactive=True))["count"] >= 1


def test_survivor_guards_count_what_reads_as_the_survivor(books):
    _run(books, "account merge", dict(merged="Truck Fuel", into="Truck Fuel Costs"))
    for change in (dict(type="income"), dict(currency="CAD")):
        current = _run(books, "account show", dict(account="Truck Fuel Costs"))
        with pytest.raises(BookflowError) as error:
            _run(books, "account update", dict(account="Truck Fuel Costs", expected_version=current["version"], **change))
        assert error.value.code in ("E_TYPE_CHANGE", "E_RECORD_IN_USE"), change
    with pytest.raises(BookflowError) as error:
        _run(books, "account deactivate", dict(account="Truck Fuel Costs"))
    assert error.value.code == "E_RECORD_IN_USE"
    assert _run(books, "account show", dict(account="Truck Fuel Costs"))["has_transactions"]
    # The merged account itself changes only by undoing its merge.
    merged = _run(books, "account show", dict(account="Truck Fuel"))
    for name, body in (("account activate", dict(account="Truck Fuel")),
                       ("account update", dict(account="Truck Fuel", expected_version=merged["version"], name="Fuel Two"))):
        with pytest.raises(BookflowError) as error:
            _run(books, name, body)
        assert error.value.code == "E_RECORD_IN_USE", name


def test_item_merge_sums_a_group_listing_both_refuses_a_cycle_and_undo_respects_a_rewritten_kit(books):
    widget, dup = _item(books, "Widget"), _item(books, "Widget Dup")
    kit = _run(books, "item create", dict(name="Widget Kit", type="group", description="Kit", print_members=True,
                                          members=[dict(component_item_id=widget, quantity="1"),
                                                   dict(component_item_id=dup, quantity="1")]))["id"]
    preview = _run(books, "item merge", dict(merged="Widget Dup", into="Widget"), dry_run=True)
    assert any(r["label"] == "Widget Kit: Widget ×1 and Widget Dup ×1 become Widget ×2" for r in preview["references"])
    _run(books, "item merge", dict(merged="Widget Dup", into="Widget"))
    members = _run(books, "item show", dict(item=kit))["members"]
    members = [m for m in members if m["active"]]
    assert [(m["component_item_id"], m["quantity"]) for m in members] == [(widget, "2")]
    # Undo with the kit unchanged restores both rows.
    _run(books, "item unmerge", dict(merged="Widget Dup"))
    members = _run(books, "item show", dict(item=kit))["members"]
    members = [m for m in members if m["active"]]
    assert sorted((m["component_item_id"], m["quantity"]) for m in members) == sorted([(widget, "1"), (dup, "1")])
    # Merge again, rewrite the kit, and undo: the kit stays as the person saved it.
    _run(books, "item merge", dict(merged="Widget Dup", into="Widget"))
    shown = _run(books, "item show", dict(item=kit))
    _run(books, "item update", dict(item=kit, expected_version=shown["version"],
                                    members=[dict(component_item_id=widget, quantity="5")]))
    undone = _run(books, "item unmerge", dict(merged="Widget Dup"))
    assert undone["kept"] >= 1
    members = _run(books, "item show", dict(item=kit))["members"]
    members = [m for m in members if m["active"]]
    assert [(m["component_item_id"], m["quantity"]) for m in members] == [(widget, "5")]
    # A merge that would make an assembly contain itself is refused (assemblies with no stock movement).
    part = _item(books, "Part P", kind="inventory_part")
    mx = _item(books, "Assembly Mx", kind="inventory_assembly", members=[dict(component_item_id=part, quantity="1")])
    a2 = _item(books, "Assembly A2", kind="inventory_assembly", members=[dict(component_item_id=mx, quantity="1")])
    _item(books, "Assembly Sx", kind="inventory_assembly", members=[dict(component_item_id=a2, quantity="1")])
    _refused(books, "Assembly Mx", "Assembly Sx", "contain itself", kind="item")
    s1, m1 = _item(books, "Svc S"), _item(books, "Svc M")
    assert s1 and m1
    # The merged item itself changes only by undoing its merge.
    _run(books, "item merge", dict(merged="Svc M", into="Svc S"))
    m_row = _run(books, "item show", dict(item="Svc M"))
    with pytest.raises(BookflowError) as error:
        _run(books, "item update", dict(item="Svc M", expected_version=m_row["version"], name="Svc M2"))
    assert error.value.code == "E_RECORD_IN_USE"
    with pytest.raises(BookflowError) as error:
        _run(books, "item activate", dict(item="Svc M"))
    assert error.value.code == "E_RECORD_IN_USE"
    assert s1
