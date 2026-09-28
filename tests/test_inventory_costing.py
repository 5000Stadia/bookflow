"""Weighted-average costing: the replay itself, then the same arithmetic through the command.

The unit tests below drive ``inventory_costing.replay`` directly, because the cases that
matter -- a backdated purchase landing between two issues, a void taking one back out, a
prefix that would go negative -- are arithmetic, and arithmetic tested through four layers is
arithmetic tested once and debugged four times.

The acceptance test after them is the one the costing decision asks for, run through the real
command: two issues on different dates plus a backdated purchase, balances checked at cutoffs
before, between and after, a retry, a void, the zero-quantity residual, and the closed-period
refusal. This increment does not wire purchases or sales to documents yet, so the two "sales"
are issuing adjustments; the arithmetic under test is the one the next increment will re-run
through real invoices.
"""
import pytest

from bookflow import BookflowError
from bookflow.company.inventory_costing import StockRefusal, replay, totals

COMPANY = "Demo Plumbing Co"
ITEM = "Costing Test Valve"


# ---------------------------------------------------------------- the replay itself


def row(identity, kind, quantity=0, value=0, date="2026-01-01", sequence=1, corrects=None, reverses=None):
    return dict(id=identity, kind=kind, quantity_microunits=quantity, value_minor_units=value,
                effective_date=date, sequence=sequence,
                corrects_movement_id=corrects, reverses_movement_id=reverses)


MICRO = 10 ** 6


def test_a_receipt_and_an_issue_take_value_at_the_running_average():
    state = replay([
        row("A", "receipt", 10 * MICRO, 10000, "2026-01-01", 1),
        row("B", "issue", -4 * MICRO, -4000, "2026-02-10", 2),
    ])
    assert (state.quantity_microunits, state.value_minor_units) == (6 * MICRO, 6000)
    assert state.average_cost_minor_units == 1000
    assert state.targets["B"] == -4000
    assert state.corrections == ()


def test_the_last_quantity_out_consumes_every_remaining_minor_unit():
    """Zero stock leaves zero residual value, even where an average would not divide."""
    state = replay([
        row("A", "receipt", 3 * MICRO, 1000, "2026-01-01", 1),      # 3 units, 10.00: 3.3333 each
        row("B", "issue", -1 * MICRO, -333, "2026-01-02", 2),
        row("C", "issue", -2 * MICRO, -667, "2026-01-03", 3),
    ])
    assert state.targets["B"] == -333 and state.targets["C"] == -667
    assert (state.quantity_microunits, state.value_minor_units) == (0, 0)
    assert state.corrections == ()


def test_a_backdated_receipt_corrects_each_later_issue_at_that_issue_own_date():
    posted = [
        row("A", "receipt", 10 * MICRO, 10000, "2026-01-01", 1),
        row("B", "issue", -4 * MICRO, -4000, "2026-02-10", 2),
        row("C", "issue", -6 * MICRO, -6000, "2026-03-10", 3),
    ]
    state = replay(posted + [row("D", "receipt", 10 * MICRO, 30000, "2026-01-20", 4)])
    # 20 units worth 400.00 on 20 January, so February takes 80.00 rather than 40.00 and
    # March takes 6 of the remaining 16 at 320.00, which is 120.00 rather than 60.00.
    assert [(x.effective_date, x.delta_minor_units) for x in state.corrections] == [
        ("2026-02-10", -4000), ("2026-03-10", -6000)]
    assert (state.quantity_microunits, state.value_minor_units) == (10 * MICRO, 20000)


def test_a_correction_is_never_applied_twice():
    """The second run compares against what is posted and finds nothing owed."""
    posted = [
        row("A", "receipt", 10 * MICRO, 10000, "2026-01-01", 1),
        row("B", "issue", -4 * MICRO, -4000, "2026-02-10", 2),
        row("C", "issue", -6 * MICRO, -6000, "2026-03-10", 3),
        row("D", "receipt", 10 * MICRO, 30000, "2026-01-20", 4),
        row("E", "recost", 0, -4000, "2026-02-10", 5, corrects="B"),
        row("F", "recost", 0, -6000, "2026-03-10", 6, corrects="C"),
    ]
    state = replay(posted)
    assert state.corrections == ()
    # Posted rows and the replay now agree, which is the report-to-balance-sheet tie.
    assert totals(posted) == (state.quantity_microunits, state.value_minor_units)


def test_a_correction_row_is_never_replayed_as_a_purchase_or_a_sale():
    """A recost carries value and no quantity; feeding it back in would move the average."""
    with_correction = [
        row("A", "receipt", 10 * MICRO, 10000, "2026-01-01", 1),
        row("B", "recost", 0, 5000, "2026-01-01", 2, corrects="X"),
    ]
    state = replay(with_correction)
    assert (state.quantity_microunits, state.value_minor_units) == (10 * MICRO, 10000)


def test_a_void_retires_the_movement_and_backs_out_its_corrections():
    posted = [
        row("A", "receipt", 10 * MICRO, 10000, "2026-01-01", 1),
        row("B", "issue", -4 * MICRO, -4000, "2026-02-10", 2),
        row("D", "receipt", 10 * MICRO, 30000, "2026-01-20", 3),
        row("E", "recost", 0, -4000, "2026-02-10", 4, corrects="B"),
    ]
    state = replay(posted + [row("R", "reversal", -10 * MICRO, -30000, "2026-01-20", 5, reverses="D")])
    assert [(x.effective_date, x.delta_minor_units) for x in state.corrections] == [("2026-02-10", 4000)]
    # A retired issue's target is zero and the same subtraction backs out its corrections.
    retired = replay(posted + [row("S", "reversal", 4 * MICRO, 4000, "2026-02-10", 6, reverses="B")])
    assert [(x.effective_date, x.delta_minor_units) for x in retired.corrections] == [("2026-02-10", 4000)]


def test_a_sale_ahead_of_its_stock_is_provisional_and_trued_up_at_the_receipt():
    """Every prefix is still walked; a prefix below zero is costed provisionally, not refused.

    One unit out on 1 January with no average and no purchase cost: provisional zero. Ten in
    at 100.00 on 1 February fill it first: the filled unit really cost 10000 * 1 / 10 = 1000,
    so the true-up is 0 - 1000 = -1000 (more cost of goods sold), dated 1 February.
    """
    posted = [
        row("A", "issue", -1 * MICRO, 0, "2026-01-01", 1),
        row("B", "receipt", 10 * MICRO, 10000, "2026-02-01", 2),
    ]
    state = replay(posted)
    assert [(x.movement["id"], x.basis, x.provisional_minor_units, x.quantity_after_microunits)
            for x in state.shortfalls] == [("A", "none", 0, -1 * MICRO)]
    assert [(x.target_movement["id"], x.filled_by["id"], x.effective_date, x.delta_minor_units)
            for x in state.corrections] == [("A", "B", "2026-02-01", -1000)]
    assert (state.quantity_microunits, state.value_minor_units) == (9 * MICRO, 9000)


def test_stock_on_hand_may_not_be_written_down_to_nothing():
    with pytest.raises(StockRefusal) as caught:
        replay([
            row("A", "receipt", 10 * MICRO, 10000, "2026-01-01", 1),
            row("B", "value", 0, -10000, "2026-01-02", 2),
        ])
    assert caught.value.reason == "unvalued_stock"


def test_value_cannot_be_carried_with_nothing_on_hand():
    with pytest.raises(StockRefusal) as caught:
        replay([row("A", "value", 0, 500, "2026-01-01", 1)])
    assert caught.value.reason == "unvalued_stock"


def test_same_day_movements_replay_in_recorded_order():
    """Effective date first, then the company-wide sequence: a defined, stable order."""
    state = replay([
        row("B", "issue", -4 * MICRO, -4000, "2026-01-01", 2),
        row("A", "receipt", 10 * MICRO, 10000, "2026-01-01", 1),
    ])
    assert (state.quantity_microunits, state.value_minor_units) == (6 * MICRO, 6000)
    # The same two rows recorded the other way round is a sale before the stock arrived: the
    # sale is short on its own date (nothing to average, no purchase cost: provisional zero, so
    # its posted -4000 owes +4000 at its date) and the receipt, recorded after it, trues it up
    # by 0 - 4000 = -4000 on the same day. The day ends where the recorded order said it would.
    reversed_order = replay([
        row("A", "receipt", 10 * MICRO, 10000, "2026-01-01", 2),
        row("B", "issue", -4 * MICRO, -4000, "2026-01-01", 1),
    ])
    assert [x.movement["id"] for x in reversed_order.shortfalls] == ["B"]
    assert sorted((x.filled_by is not None, x.delta_minor_units)
                  for x in reversed_order.corrections) == [(False, 4000), (True, -4000)]
    assert (reversed_order.quantity_microunits, reversed_order.value_minor_units) == (6 * MICRO, 6000)


# ---------------------------------------------------------------- through the command


@pytest.fixture
def books(client):
    # An item of the test's own: the demo's stocked items carry seeded history, and every
    # figure below is worked out by hand from an empty stock ledger.
    accounts = {row["full_name"]: row["id"] for row in client.run(
        "account query", {"limit": 200}, company=COMPANY)["items"]}
    item = client.run("item create", dict(
        name=ITEM, type="inventory_part", description="Costing test valve", price="20.00",
        purchase_description="Costing test valve", cost="0.00",
        income_account_id=accounts["Construction Income"],
        cogs_account_id=accounts["Cost of Goods Sold"]), company=COMPANY, reason="stock")["id"]

    def adjust(**body):
        return client.run("inventory adjust", dict(item=item, **body), company=COMPANY, reason="stock")

    def valuation(as_of):
        rows = client.run("report inventory-valuation", {"as_of": as_of, "limit": 200},
                          company=COMPANY)["rows"]
        return next((row["asset_value"]["amount"] for row in rows if row["item_id"] == item), "0.00")

    return item, adjust, valuation


def test_the_acceptance_scenario_the_costing_decision_asks_for(client, books):
    item, adjust, valuation = books
    opening = adjust(date="2026-01-01", adjustment_account="Opening Balance Equity",
                     quantity_change="10", value_change="100.00", memo="Opening stock")
    assert opening["adjustment"]["average_cost"]["amount"] == "10.00"
    first = adjust(date="2026-02-10", adjustment_account="Cost of Goods Sold", quantity_change="-4")
    second = adjust(date="2026-03-10", adjustment_account="Cost of Goods Sold", quantity_change="-6")
    assert first["adjustment"]["value_change"]["amount"] == "-40.00"
    assert second["adjustment"]["value_change"]["amount"] == "-60.00"
    # Issuing the last unit leaves nothing behind, in quantity and in value.
    assert second["adjustment"]["quantity_on_hand"] == "0"
    assert second["adjustment"]["inventory_value"]["amount"] == "0.00"
    assert [valuation(date) for date in ("2026-01-31", "2026-02-28", "2026-03-31")] == \
        ["100.00", "60.00", "0.00"]

    backdated = adjust(date="2026-01-20", adjustment_account="Opening Balance Equity",
                       quantity_change="10", value_change="300.00", memo="Backdated purchase")
    assert [(x["effective_date"], x["delta"]["amount"]) for x in backdated["adjustment"]["corrections"]] == \
        [("2026-02-10", "-40.00"), ("2026-03-10", "-60.00")]
    # Each delta sits on its own sale's date, so no intervening report moves for a reason it
    # cannot show; putting both on 20 January would make all three of these wrong.
    assert [valuation(date) for date in ("2026-01-31", "2026-02-28", "2026-03-31")] == \
        ["400.00", "320.00", "200.00"]

    # A retry of the same write is the same write.
    body = dict(item=item, date="2026-01-25", adjustment_account="Opening Balance Equity",
                quantity_change="5", value_change="50.00")
    once = client.run("inventory adjust", body, company=COMPANY, reason="second purchase",
                      idempotency_key="inventory-retry")
    after = valuation("2026-12-31")
    twice = client.run("inventory adjust", body, company=COMPANY, reason="second purchase",
                       idempotency_key="inventory-retry")
    assert twice["id"] == once["id"] and twice["idempotent_replay"] is True
    assert valuation("2026-12-31") == after
    assert [(x["effective_date"], x["delta"]["amount"]) for x in once["adjustment"]["corrections"]] == \
        [("2026-02-10", "8.00"), ("2026-03-10", "12.00")]
    undone = client.run("inventory void", {"adjustment": once["id"]}, company=COMPANY, reason="wrong bin")
    assert [(x["effective_date"], x["delta"]["amount"]) for x in undone["adjustment"]["corrections"]] == \
        [("2026-02-10", "-8.00"), ("2026-03-10", "-12.00")]

    voided = client.run("inventory void", {"adjustment": backdated["id"]}, company=COMPANY,
                        reason="the purchase never happened")
    assert [(x["effective_date"], x["delta"]["amount"]) for x in voided["adjustment"]["corrections"]] == \
        [("2026-02-10", "40.00"), ("2026-03-10", "60.00")]
    assert voided["adjustment"]["quantity_on_hand"] == "0"
    assert voided["adjustment"]["inventory_value"]["amount"] == "0.00"
    assert valuation("2026-12-31") == "0.00"


def test_a_closed_period_refuses_the_whole_change_and_names_the_period(client, books):
    item, adjust, valuation = books
    adjust(date="2026-01-01", adjustment_account="Opening Balance Equity",
           quantity_change="10", value_change="100.00")
    client.run("company update", {"closing_date": "2026-05-31"}, company=COMPANY)
    before = valuation("2026-12-31")
    movements = len(client.run("report inventory-valuation", {"as_of": "2026-12-31"},
                               company=COMPANY)["rows"])
    with pytest.raises(BookflowError) as caught:
        adjust(date="2026-05-01", adjustment_account="Cost of Goods Sold", quantity_change="-2")
    assert caught.value.code == "E_PERIOD_CLOSED"
    assert caught.value.details["closing_date"] == "2026-05-31"
    # Atomic: nothing about the books moved, and the open period still accepts work.
    assert valuation("2026-12-31") == before
    assert len(client.run("report inventory-valuation", {"as_of": "2026-12-31"},
                          company=COMPANY)["rows"]) == movements
    after = adjust(date="2026-06-05", adjustment_account="Cost of Goods Sold", quantity_change="-2")
    assert after["adjustment"]["quantity_on_hand"] == "8"


def test_stock_taken_below_zero_warns_and_so_does_a_void_that_leaves_a_sale_short(client, books):
    """10 in at 100.00, 7 out (70.00), then 5 out: 3 on hand worth 30.00 go at 30.00 and the 2
    below zero at the 10.00 average, 20.00 -- 50.00 in all, leaving -2 worth -20.00.

    Voiding the purchase leaves the 1 February issue short with no average and no purchase
    cost, so it is re-costed at zero on its own date (+70.00) and the 1 March issue, already
    short, at zero too (+50.00): -12 on hand worth nothing, and the void says which sale it
    newly left short.
    """
    item, adjust, valuation = books
    purchase = adjust(date="2026-01-01", adjustment_account="Opening Balance Equity",
                      quantity_change="10", value_change="100.00")
    adjust(date="2026-02-01", adjustment_account="Cost of Goods Sold", quantity_change="-7")
    short = adjust(date="2026-03-01", adjustment_account="Cost of Goods Sold", quantity_change="-5")
    assert short["adjustment"]["value_change"]["amount"] == "-50.00"
    assert short["adjustment"]["quantity_on_hand"] == "-2"
    assert short["adjustment"]["inventory_value"]["amount"] == "-20.00"
    assert short["warnings"] == [
        "Takes Costing Test Valve to -2 on 2026-03-01; its cost is provisional, at the average "
        "cost, until a receipt brings the item back up and trues it up."]
    assert valuation("2026-03-31") == "-20.00"
    voided = client.run("inventory void", {"adjustment": purchase["id"]}, company=COMPANY, reason="mistake")
    assert [(x["effective_date"], x["delta"]["amount"]) for x in voided["adjustment"]["corrections"]] == \
        [("2026-02-01", "70.00"), ("2026-03-01", "50.00")]
    assert voided["adjustment"]["quantity_on_hand"] == "-12"
    assert voided["adjustment"]["inventory_value"]["amount"] == "0.00"
    stock = [line for line in voided["warnings"] if line.startswith("Takes ")]
    assert [line.split(";")[0] for line in stock] == ["Takes Costing Test Valve to -7 on 2026-02-01"]
    assert "at zero" in stock[0]
    assert valuation("2026-12-31") == "0.00"


def test_an_adjustment_says_what_it_will_not_accept(client, books):
    item, adjust, _ = books
    with pytest.raises(BookflowError) as caught:
        adjust(date="2026-01-01", adjustment_account="Opening Balance Equity", quantity_change="5")
    assert caught.value.code == "E_VALIDATION" and "worth" in str(caught.value.details)
    with pytest.raises(BookflowError):
        adjust(date="2026-01-01", adjustment_account="Opening Balance Equity",
               quantity_change="-5", value_change="10.00")
    with pytest.raises(BookflowError):
        adjust(date="2026-01-01", adjustment_account="Opening Balance Equity")
    with pytest.raises(BookflowError) as service:
        client.run("inventory adjust", dict(item="Copper Coupling", date="2026-01-01",
                                            adjustment_account="Opening Balance Equity",
                                            quantity_change="1", value_change="1.00"),
                   company=COMPANY, reason="stock")
    assert service.value.code == "E_VALIDATION" and "carries no stock" in str(service.value.details)
    with pytest.raises(BookflowError) as control:
        client.run("inventory adjust", dict(item=item, date="2026-01-01",
                                            adjustment_account="Inventory Asset",
                                            quantity_change="1", value_change="1.00"),
                   company=COMPANY, reason="stock")
    assert control.value.code == "E_VALIDATION"


def test_an_entry_may_not_post_straight_to_the_inventory_asset(client):
    """The control account is the sum of the item ledger; an unattributed amount would break it."""
    with pytest.raises(BookflowError) as caught:
        client.run("journal post", {"date": "2026-01-01", "lines": [
            {"account": "Inventory Asset", "side": "debit", "amount": "50.00"},
            {"account": "Opening Balance Equity", "side": "credit", "amount": "50.00"}]},
            company=COMPANY, reason="try it")
    assert caught.value.code == "E_VALIDATION"
    assert "inventory ledger" in caught.value.details["fields"][0]["problem"]


def test_an_inventory_adjustment_is_not_edited_in_the_journal_editor(client, books):
    item, adjust, _ = books
    posted = adjust(date="2026-01-01", adjustment_account="Opening Balance Equity",
                    quantity_change="10", value_change="100.00")
    for command, body in (("journal void", {"journal": posted["id"]}),
                          ("journal update", {"journal": posted["id"], "date": "2026-02-02"})):
        with pytest.raises(BookflowError) as caught:
            client.run(command, body, company=COMPANY, reason="try it")
        assert caught.value.code == "E_VALIDATION"
        assert caught.value.details["inventory_document"] == "adjustment"


def test_reading_an_adjustment_back_shows_what_it_did(client, books):
    item, adjust, _ = books
    posted = adjust(date="2026-01-01", adjustment_account="Opening Balance Equity",
                    quantity_change="8", value_change="96.00", memo="Counted")
    shown = client.run("inventory show", {"adjustment": posted["number"]}, company=COMPANY)
    assert shown["id"] == posted["id"]
    assert shown["adjustment"]["movement_kind"] == "receipt"
    assert shown["adjustment"]["quantity_change"] == "8"
    assert shown["adjustment"]["average_cost"]["amount"] == "12.00"
    assert shown["revision"]["lines"][0]["account_id"] == shown["adjustment"]["asset_account_id"]
    listed = client.run("item show", {"item": ITEM}, company=COMPANY)
    assert listed["inventory_values_available"] is True
    assert listed["quantity_on_hand"] == "8" and listed["inventory_value"]["amount"] == "96.00"
    assert listed["average_cost"]["amount"] == "12.00"
    other = client.run("item show", {"item": "Copper Coupling"}, company=COMPANY)
    assert other["inventory_values_available"] is False


def test_a_read_of_a_recosted_item_names_the_correcting_documents(client, books):
    item, adjust, _ = books
    adjust(date="2026-01-01", adjustment_account="Opening Balance Equity",
           quantity_change="10", value_change="100.00")
    sale = adjust(date="2026-02-10", adjustment_account="Cost of Goods Sold", quantity_change="-4")
    backdated = adjust(date="2026-01-20", adjustment_account="Opening Balance Equity",
                       quantity_change="10", value_change="300.00")
    shown = client.run("inventory show", {"adjustment": sale["id"]}, company=COMPANY)
    correction = shown["adjustment"]["corrections"][0]
    assert correction["effective_date"] == "2026-02-10"
    assert correction["delta"]["amount"] == "-40.00"
    assert correction["number"] == backdated["adjustment"]["corrections"][0]["number"]
    # A correction document is not an adjustment, so the adjustment reader does not answer
    # for it; its number is there to be looked up in the journal.
    with pytest.raises(BookflowError) as caught:
        client.run("inventory show", {"adjustment": correction["number"]}, company=COMPANY)
    assert caught.value.code == "E_RECORD_NOT_FOUND"
    entry = client.run("journal show", {"journal": correction["number"]}, company=COMPANY)
    assert entry["date"] == "2026-02-10" and entry["total"]["amount"] == "40.00"


def test_a_correction_that_cannot_be_posted_says_so_and_writes_nothing(client, books):
    """A generated document has no form to fill in, so its refusal has to explain itself.

    A required journal-entry custom field with no default is the case that reaches this: the
    adjustment can carry the value a person typed, and the corrections it owes cannot.
    """
    item, adjust, valuation = books
    field = client.run("custom-field create", {"name": "Approver", "kind": "text",
                                               "required": True, "scopes": ["journal_entry"]},
                       company=COMPANY, reason="policy")["id"]
    opening = dict(date="2026-01-01", adjustment_account="Opening Balance Equity",
                   quantity_change="10", value_change="100.00", custom_fields={field: "Kabe"})
    adjust(**opening)
    adjust(date="2026-02-01", adjustment_account="Cost of Goods Sold", quantity_change="-4",
           custom_fields={field: "Kabe"})
    before = valuation("2026-12-31")
    with pytest.raises(BookflowError) as caught:
        adjust(date="2026-01-15", adjustment_account="Opening Balance Equity",
               quantity_change="10", value_change="300.00", custom_fields={field: "Kabe"})
    assert caught.value.details["blocked_correction_date"] == "2026-02-01"
    assert "Nothing was written" in caught.value.message
    assert valuation("2026-12-31") == before
