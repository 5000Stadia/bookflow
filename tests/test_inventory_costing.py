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
ITEM = "Brass Shutoff Valve"


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


def test_every_chronological_prefix_is_checked_not_only_the_end_state():
    with pytest.raises(StockRefusal) as caught:
        replay([
            row("A", "issue", -1 * MICRO, -100, "2026-01-01", 1),
            row("B", "receipt", 10 * MICRO, 10000, "2026-02-01", 2),
        ])
    assert caught.value.reason == "negative_stock"
    assert caught.value.movement["id"] == "A"


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
    with pytest.raises(StockRefusal):
        # The same two rows recorded the other way round is a sale before the stock arrived.
        replay([
            row("A", "receipt", 10 * MICRO, 10000, "2026-01-01", 2),
            row("B", "issue", -4 * MICRO, -4000, "2026-01-01", 1),
        ])


# ---------------------------------------------------------------- through the command


@pytest.fixture
def books(client):
    item = client.run("item show", {"item": ITEM}, company=COMPANY)["id"]

    def adjust(**body):
        return client.run("inventory adjust", dict(item=item, **body), company=COMPANY, reason="stock")

    def valuation(as_of):
        return client.run("report inventory-valuation", {"as_of": as_of, "limit": 200},
                          company=COMPANY)["totals"]["asset_value"]["amount"]

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


def test_negative_stock_is_refused_including_after_a_void(client, books):
    item, adjust, valuation = books
    purchase = adjust(date="2026-01-01", adjustment_account="Opening Balance Equity",
                      quantity_change="10", value_change="100.00")
    adjust(date="2026-02-01", adjustment_account="Cost of Goods Sold", quantity_change="-7")
    with pytest.raises(BookflowError) as caught:
        adjust(date="2026-03-01", adjustment_account="Cost of Goods Sold", quantity_change="-5")
    assert caught.value.code == "E_VALIDATION"
    assert "below zero" in caught.value.message
    before = valuation("2026-12-31")
    with pytest.raises(BookflowError) as voided:
        client.run("inventory void", {"adjustment": purchase["id"]}, company=COMPANY, reason="mistake")
    assert voided.value.code == "E_VALIDATION"
    assert voided.value.details["effective_date"] == "2026-02-01"
    assert valuation("2026-12-31") == before


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
