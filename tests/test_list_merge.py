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
        assert error.value.code == "E_PERMISSION" and error.value.details.get("required_role") == "human", name
    assert as_user(root, "merge-person").run("account merge", {"merged": "Truck Fuel", "into": "Truck Fuel Costs"},
                                             company=COMPANY, reason=REASON)["changed"]
