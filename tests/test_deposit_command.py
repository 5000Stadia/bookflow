"""Money received can be banked, and the bank line names the receipts inside it.

The walkthrough a bookkeeper actually performs: create a company, bill a
customer, take their payment, ring up a counter sale, then deposit both
receipts together into one bank account. What is asserted afterwards is what a
month-end depends on -- the trial balance still balances, Undeposited Funds is
empty, the bank holds exactly what was banked, and the deposit can still say
which receipts it contains.
"""

import pytest

import bookflow

ORGANIZATION = "Deposit Walkthrough Org"
COMPANY = "Riverbend Plumbing"


@pytest.fixture
def books(tmp_path, monkeypatch):
    """A company of its own, so Undeposited Funds starts and ends empty on its own account."""
    root = tmp_path / "root"
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(root))
    monkeypatch.delenv("BOOKFLOW_COMPANY", raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.run("organization new", dict(name=ORGANIZATION), reason="first organization")
    client.run("company new", dict(legal_name=COMPANY, home_currency="USD", organization=ORGANIZATION),
               reason="first company")
    accounts = {row["name"]: row for row in client.run("account list", {}, company=COMPANY)["items"]}
    income = client.account.create(name="Drain service income", type="income", company=COMPANY)["id"]
    customer = client.customer.create(name="Ada Waterworks", company=COMPANY)["id"]
    code = next(row["id"] for row in client.run("sales-tax-code list", {}, company=COMPANY)["items"]
                if not row["taxable"])
    item = client.run("item create", dict(name="Drain service", type="service", sales_enabled=True,
        description="Service labor", income_account_id=income, price="100.00", sales_tax_code_id=code),
        company=COMPANY)["id"]
    method = next(row["id"] for row in client.run("payment-method list", {}, company=COMPANY)["items"]
                  if row["kind"] == "cash")
    return dict(client=client, customer=customer, item=item, income=income, method=method,
                bank=accounts["Checking"]["id"], uf=accounts["Undeposited Funds"]["id"],
                ar=accounts["Accounts Receivable"]["id"])


def balances(client, date_to="2026-12-31"):
    """Every account's signed ending net, zero balances included."""
    report = client.run("report trial-balance", dict(date_to=date_to, include_zero=True, limit=200),
                        company=COMPANY)
    assert not report["next_cursor"], "the walkthrough's chart fits one page"
    return report["totals"], {row["account_id"]: row["signed_net"]["minor_units"] for row in report["rows"]}


def receipts_in_undeposited_funds(books):
    """Invoice a customer and take their payment, then ring up a counter sale."""
    client = books["client"]
    invoice = client.run("invoice post", dict(customer=books["customer"], date="2026-06-01",
        lines=[dict(item=books["item"], quantity="1", net_amount="100")]),
        company=COMPANY, reason="bill the June service call")
    payment = client.run("payment receive", dict(customer=books["customer"], date="2026-06-02", amount="100",
        payment_method=books["method"], operation_key="june-cash", applications=dict(mode="inline",
            items=[dict(invoice=invoice["id"], expected_version=1, amount="100")])),
        company=COMPANY, reason="customer paid in cash")
    sale = client.run("sales-receipt post", dict(customer=books["customer"], deposit_to=books["uf"],
        payment_method=books["method"], date="2026-06-02",
        lines=[dict(item=books["item"], quantity="1", unit_price="60")]),
        company=COMPANY, reason="counter sale")
    return invoice, payment, sale


def test_two_receipts_are_banked_together_and_the_books_still_balance(books):
    client = books["client"]
    invoice, payment, sale = receipts_in_undeposited_funds(books)

    # Before the deposit: the money is real, and it is stuck.
    totals, before = balances(client)
    assert before[books["uf"]] == 16000, "both receipts are sitting in Undeposited Funds"
    assert before[books["bank"]] == 0, "the bank has seen none of it"
    assert before[books["ar"]] == 0 and totals["signed_net"]["minor_units"] == 0

    # See what is available to deposit.
    available = client.run("deposit sources", dict(date="2026-06-03"), company=COMPANY)
    assert available["total_count"] == 2 and available["subtotal"] == {"minor_units": 16000, "currency": "USD"}
    assert {(row["source_type"], row["source"], row["amount"]["minor_units"]) for row in available["items"]} == {
        ("payment", payment["id"], 10000), ("sales_receipt", sale["id"], 6000)}
    assert all(row["eligible"] and not row["deposited"] and row["received_from"] == "Ada Waterworks"
               for row in available["items"])

    document = dict(mode="inline", deposit_to=books["bank"], date="2026-06-03", memo="Saturday receipts",
                    sources=[dict(source_type=row["source_type"], source=row["source"],
                                  expected_version=row["expected_version"]) for row in available["items"]])
    request = dict(operation_key="june-deposit", document=document)

    # A preview writes nothing and names no identity it has not allocated.
    preview = client.run("deposit post", request, company=COMPANY, reason="bank Saturday receipts", dry_run=True)
    assert preview["dry_run"] and preview["deposit"]["id"] == "new-deposit" and preview["operation_id"] is None
    assert preview["deposit"]["bank_total"] == {"minor_units": 16000, "currency": "USD"}
    assert balances(client)[1] == before, "the preview moved nothing"

    posted = client.run("deposit post", dict(request, expected_facts_fingerprint=preview["facts_fingerprint"]),
                        company=COMPANY, reason="bank Saturday receipts")

    # One document, one bank account, one date, both receipts.
    assert posted["changed"] and posted["deposit"]["status"] == "posted"
    assert posted["deposit"]["date"] == "2026-06-03"
    assert posted["deposit_to"]["id"] == books["bank"]
    assert posted["deposit"]["subtotal"] == {"minor_units": 16000, "currency": "USD"}
    assert posted["deposit"]["bank_total"] == {"minor_units": 16000, "currency": "USD"}
    assert posted["deposit"]["cash_back"]["minor_units"] == 0
    assert [(row["source"], row["amount"]["minor_units"]) for row in posted["receipts"]] == [
        (payment["id"], 10000), (sale["id"], 6000)]
    assert sorted(posted["deposit"]["banked_receipt_ids"]) == sorted([payment["id"], sale["id"]])
    assert [(row["source"], row["kind"], row["amount"]["minor_units"]) for row in posted["memberships"]] == [
        (payment["id"], "claim", 10000), (sale["id"], "claim", 6000)]
    assert [(row["role"], row["account_id"], row["statement_amount"]["minor_units"]) for row in posted["bank_movements"]] == [
        ("main_bank", books["bank"], 16000)]

    # The accounting bar.
    totals, after = balances(client)
    assert totals["debit"] == totals["credit"], "the trial balance still balances"
    assert totals["signed_net"]["minor_units"] == 0
    assert after[books["uf"]] == 0, "Undeposited Funds is empty once everything in it is deposited"
    assert after[books["bank"]] == 16000, "the bank holds exactly the deposited total"
    assert after[books["income"]] == -16000, "banking recognizes no second income"
    assert after[books["ar"]] == 0, "banking does not touch the customer's balance"

    # Nothing is left to deposit, and neither receipt can be banked twice.
    assert client.run("deposit sources", dict(date="2026-06-03"), company=COMPANY)["total_count"] == 0
    again = dict(operation_key="june-deposit-again", document=document)
    with pytest.raises(bookflow.BookflowError) as claimed:
        client.run("deposit post", again, company=COMPANY, reason="bank them twice")
    assert claimed.value.code == "E_DEPOSIT_SOURCE_CLAIMED"

    # After the fact, the deposit still says which receipts made up the bank line: the
    # permanent operation key replays the original effect, on any surface, and writes nothing.
    replay = client.run("deposit post", dict(request, expected_facts_fingerprint=preview["facts_fingerprint"]),
                        company=COMPANY, reason="bank Saturday receipts")
    assert replay["idempotent_replay"] and not replay["changed"]
    assert [row["source"] for row in replay["receipts"]] == [payment["id"], sale["id"]]
    assert replay["deposit"]["id"] == posted["deposit"]["id"]
    assert balances(client)[1] == after, "a replay writes nothing"

    # Every write is attributed, on the surface the owner reads.
    events = client.run("audit list", {}, company=COMPANY)["items"]
    banked = next(event for event in events if event["command"] == "deposit post")
    assert banked["reason"] == "bank Saturday receipts"
    assert client.run("audit show", dict(event=banked["id"]), company=COMPANY)["entries"]


def test_a_deposit_with_a_fee_and_cash_back_posts_the_exact_net(books):
    client = books["client"]
    _, payment, sale = receipts_in_undeposited_funds(books)
    fee = client.account.create(name="Merchant fees", type="expense", company=COMPANY)["id"]
    other = client.account.create(name="Interest earned", type="other_income", company=COMPANY)["id"]
    till = client.account.create(name="Cash in till", type="other_current_asset", company=COMPANY)["id"]
    available = client.run("deposit sources", dict(date="2026-06-03"), company=COMPANY)
    document = dict(mode="inline", deposit_to=books["bank"], date="2026-06-03",
        sources=[dict(source_type=row["source_type"], source=row["source"],
                      expected_version=row["expected_version"]) for row in available["items"]],
        additional=[dict(received_from=dict(kind="customer", id=books["customer"]), from_account=other, amount="20.00"),
                    dict(received_from=dict(kind="customer", id=books["customer"]), from_account=fee, amount="-3.00")],
        cash_back=dict(account=till, amount="5.00", memo="Float for the van"))
    posted = client.run("deposit post", dict(operation_key="june-net-deposit", document=document),
                        company=COMPANY, reason="bank Saturday receipts net of the fee")

    # 16000 banked + 2000 other income - 300 fee = 17700 subtotal; 500 kept as cash; 17200 to the bank.
    assert posted["deposit"]["subtotal"]["minor_units"] == 17700
    assert posted["deposit"]["cash_back"]["minor_units"] == 500
    assert posted["deposit"]["bank_total"]["minor_units"] == 17200
    assert posted["cash_back"]["account"]["id"] == till
    assert [row["amount"]["minor_units"] for row in posted["other_money"]] == [2000, -300]

    totals, after = balances(client)
    assert totals["debit"] == totals["credit"] and totals["signed_net"]["minor_units"] == 0
    assert after[books["uf"]] == 0 and after[books["bank"]] == 17200 and after[till] == 500
    assert after[fee] == 300 and after[other] == -2000
    assert after[books["income"]] == -16000, "the receipts recognize their income once, when they were taken"
    assert sorted(posted["deposit"]["banked_receipt_ids"]) == sorted([payment["id"], sale["id"]])


def test_voiding_a_deposit_frees_its_receipts_and_keeps_both_effects(books):
    client = books["client"]
    _, payment, sale = receipts_in_undeposited_funds(books)
    available = client.run("deposit sources", dict(date="2026-06-03"), company=COMPANY)
    document = dict(mode="inline", deposit_to=books["bank"], date="2026-06-03",
        sources=[dict(source_type=row["source_type"], source=row["source"],
                      expected_version=row["expected_version"]) for row in available["items"]])
    posted = client.run("deposit post", dict(operation_key="june-deposit", document=document),
                        company=COMPANY, reason="bank Saturday receipts")

    # Void needs the guard its own preview issues; it cannot be committed blind.
    void = dict(deposit=posted["deposit"]["id"], expected_version=posted["deposit"]["version"],
                operation_key="june-deposit-void")
    preview = client.run("deposit void", void, company=COMPANY, reason="never reached the bank", dry_run=True)
    assert preview["dry_run"] and preview["deposit"]["status"] == "voided"
    voided = client.run("deposit void", dict(void, dependency_guard=preview["dependency_guard"]),
                        company=COMPANY, reason="never reached the bank")
    assert voided["deposit"]["status"] == "voided" and voided["deposit"]["version"] == 2
    assert [row["kind"] for row in voided["memberships"]] == ["release", "release"]
    assert all(not row["active"] for row in voided["bank_movements"])

    totals, after = balances(client)
    assert totals["debit"] == totals["credit"] and totals["signed_net"]["minor_units"] == 0
    assert after[books["bank"]] == 0, "the bank never had it"
    assert after[books["uf"]] == 16000, "the receipts are undeposited again, still posted"

    # History is preserved, not erased: the original deposit and its exact reversal both stand.
    freed = client.run("deposit sources", dict(date="2026-06-03"), company=COMPANY)
    assert {row["source"] for row in freed["items"]} == {payment["id"], sale["id"]}
    assert client.run("deposit post", dict(operation_key="june-deposit", document=document),
                      company=COMPANY, reason="bank Saturday receipts")["receipts"], \
        "the voided deposit can still name the receipts it contained"
    commands = [event["command"] for event in client.run("audit list", {}, company=COMPANY)["items"]]
    assert commands.count("deposit post") == 1 and commands.count("deposit void") == 1, \
        "the replay read the permanent operation; it wrote nothing"

    # And they can be banked again, into a deposit of their own.
    again = client.run("deposit post", dict(operation_key="june-deposit-second", document=dict(document,
        sources=[dict(source_type=row["source_type"], source=row["source"],
                      expected_version=row["expected_version"]) for row in freed["items"]])),
        company=COMPANY, reason="bank them for real this time")
    assert again["deposit"]["id"] != posted["deposit"]["id"]
    assert balances(client)[1][books["bank"]] == 16000
