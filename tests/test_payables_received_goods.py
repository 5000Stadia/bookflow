"""A bill for goods already received carries the payable it took over from the receipt.

Receiving items credits Accounts Payable on the receipt's own journal; billing them later posts
a transfer on the bill -- Accounts Payable debited for what the receipt held and credited for
the bill -- so the ledger's balance never moves. Reports that list documents follow the transfer:
the bill is open for what it billed, the receipt keeps only what is still unbilled, and every
total stays the ledger's Accounts Payable (as the anchor's A/P aging and unpaid bills do).
"""
from tests.demo_oracle import DEMO_AS_OF, DEMO_POSITION

COMPANY = "Demo Plumbing Co"


def _run(client, command, body):
    return client.run(command, {**body, "limit": 200}, company=COMPANY)


def test_billed_receipts_are_unpaid_bills_and_every_total_is_accounts_payable(client):
    unpaid = _run(client, "report unpaid-bills", {"as_of": DEMO_AS_OF})
    listed = {row["number"]: row["balance"]["minor_units"] for row in unpaid["rows"]}
    # DEMO-KIT-BILL bills 4 received kits at 11.00; bill 1 bills 2 kits at 8.00 and 8.00 shipping.
    assert listed == {"DEMO-KIT-BILL": 4400, "1": 2400}
    for number in listed:
        bill = next(row for row in client.run("bill query", {"number": number, "limit": 5}, company=COMPANY)["items"]
                    if row["number"] == number)
        assert bill["settlement_current"]["open"]["minor_units"] == listed[number]
    assert unpaid["totals"]["balance"]["minor_units"] == 6800

    payable = DEMO_POSITION["balances"]["Accounts Payable"]
    aging = _run(client, "report ap-aging", {"as_of": DEMO_AS_OF})
    summary = _run(client, "report vendor-balance-summary", {"as_of": DEMO_AS_OF})
    assert -aging["totals"]["total"]["minor_units"] == payable == -summary["totals"]["balance"]["minor_units"]
    # Owed: the two open bills 68.00, two kits and 12.00 of shipping received but not billed
    # (20.00 + 12.00), less the 21.90 vendor credit not yet applied.
    assert aging["totals"]["total"]["minor_units"] == 6800 + 3200 - 2190
