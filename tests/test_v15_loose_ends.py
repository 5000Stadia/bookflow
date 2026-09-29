"""V1.5 blind trials (notes/blind-trials-20260928.md): the loose ends the trials turned up.

A card charge said it was a journal entry, a check number could not be searched for, two warnings
read as noise, a receipt did not say where its money went, and `invoice history` hid the quantities
an edit changed and which tax applied.
"""
import bookflow


def _books(root):
    c = bookflow.connect(data_root=str(root))
    company = c.company.list()["items"][0]["company_id"]
    return lambda cmd, inp, **kw: c.run(cmd, inp, company=company, **kw)


def _valves(run, quantity=2):
    return run("invoice post", {"date": "2026-09-21", "customer": "Riverside Apartments", "lines": [
        {"item": "Brass Shutoff Valve", "quantity": str(quantity)}, {"item": "Mainline Clearing", "quantity": "1"}]},
        reason="fixture")


def test_a_card_charge_reads_back_as_a_card_charge_not_a_journal_entry(root):
    run = _books(root)
    expense = next(a["name"] for a in run("account list", {})["items"] if a["type"] == "expense")
    charge = {"account": "Business Credit Card", "date": "2026-09-21", "amount": "86.40", "memo": "Fittings",
              "expenses": [{"account": expense, "amount": "86.40"}]}
    assert run("card-charge post", charge, dry_run=True, reason="x")["type"] == "card_charge"
    posted = run("card-charge post", charge, reason="x")
    assert posted["type"] == "card_charge" and posted["document"]["kind"] == "card_charge"
    assert run("card-charge show", {"card_charge": posted["id"]})["type"] == "card_charge"
    assert {row["type"] for row in run("card-charge query", {})["items"]} == {"card_charge"}
    credit = run("card-credit post", dict(charge, memo="Returned fittings", amount="10.00",
                                          expenses=[{"account": expense, "amount": "10.00"}]), reason="x")
    assert credit["type"] == "card_credit"
    check = run("check query", {"limit": 1})["items"][0]
    assert check["type"] == "check"
    # The register and the journal list name each by the document entered, not the journal
    # entry it is stored as; storage and the ledger are untouched.
    rows = {r["transaction_id"]: r for r in run("register query", {"account": "Business Credit Card"})["rows"]
            if r.get("transaction_id")}
    assert (rows[posted["id"]]["transaction_type"], rows[posted["id"]]["money_out_kind"]) == ("card_charge", "card_charge")
    assert rows[credit["id"]]["transaction_type"] == "card_credit"
    assert "journal_entry" in {r["transaction_type"] for r in rows.values()}  # a plain register entry stays one
    journals = {j["id"]: j["type"] for j in run("journal query", {"limit": 200})["items"]}
    assert (journals[posted["id"]], journals[credit["id"]], journals[check["id"]]) == ("card_charge", "card_credit", "check")
    assert "journal_entry" in journals.values()


def test_payment_query_finds_a_receipt_by_its_check_number(root):
    run = _books(root)
    for key, reference in (("a", "4411"), ("b", "99")):
        run("payment receive", {"customer": "Riverside Apartments", "date": "2026-09-22", "amount": "10.00",
            "reference": reference, "payment_method": "Check", "operation_key": key}, reason="x")
    found = run("payment query", {"reference": " 4411 "})["items"]
    assert len(found) == 1
    assert [r["id"] for r in run("payment query", {"q": "4411"})["items"]] == [found[0]["id"]]
    assert not run("payment query", {"reference": "441"})["items"]
    from bookflow.core import registry
    command = registry.get("payment query")
    assert "check number" in command.description and "`q` searches" in command.description


def test_a_receipt_says_where_the_money_went(root):
    run = _books(root)
    held = run("payment receive", {"customer": "Riverside Apartments", "date": "2026-09-22", "amount": "10.00",
               "payment_method": "Check", "operation_key": "u"}, reason="x")
    assert "held in Undeposited Funds until you record the bank deposit with `deposit post`" in held["summary"]["text"]
    banked = run("payment receive", {"customer": "Riverside Apartments", "date": "2026-09-22", "amount": "5.00",
                 "payment_method": "Check", "operation_key": "c", "deposit_to": "Checking"}, reason="x")
    assert "It was deposited to Checking." in banked["summary"]["text"]


def test_warnings_read_once_and_in_plain_words(root):
    run = _books(root)
    posted = _valves(run)
    assert posted["warnings"].count("price_level: price levels disabled; standard/manual price is in use") == 1
    edited = run("invoice update", {"invoice": posted["id"], "lines": [
        {"item": "Brass Shutoff Valve", "quantity": "1"}, {"item": "Mainline Clearing", "quantity": "1"}]}, reason="x")
    blind = [w for w in edited["warnings"] if w.startswith("Blind write")]
    assert len(blind) == 1 and "fields journal" not in blind[0]
    assert "without a version check" in blind[0] and "pass the version you read as expected_version" in blind[0]
    checked = run("invoice update", {"invoice": posted["id"], "expected_version": edited["version"], "memo": "m"},
                  reason="x")
    assert not [w for w in checked["warnings"] if w.startswith("Blind write")]


def test_invoice_history_shows_each_revisions_quantities_and_the_tax_applied(root):
    run = _books(root)
    posted = _valves(run, 2)
    tax = posted["sales_tax"]
    assert tax["item"] and tax["rate_percent"] and tax["chosen_by"] in ("customer", "company default")
    assert tax["amount"] == posted["tax"] and f"{tax['item']} at {tax['rate_percent']}%" in tax["summary"]
    run("invoice update", {"invoice": posted["id"], "lines": [
        {"item": "Brass Shutoff Valve", "quantity": "1"}, {"item": "Mainline Clearing", "quantity": "1"}]}, reason="x")
    revisions = run("invoice history", {"invoice": posted["id"]})["items"]
    valves = [next(line for line in r["lines"] if line["item"] == "Brass Shutoff Valve") for r in revisions]
    assert [v["quantity"] for v in valves] == ["2", "1"]
    assert valves[0]["unit_price"] == valves[1]["unit_price"] and valves[0]["amount"] != valves[1]["amount"]
    assert all(r["sales_tax"]["item"] == tax["item"] for r in revisions)
