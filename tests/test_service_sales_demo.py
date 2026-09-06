"""Disposable commercial seeds and independently calculated reference effects."""
from collections import Counter, defaultdict
from importlib.resources import files
import json
from pathlib import Path
import sqlite3
import tomllib

import pytest

from tests.test_reference_year import (
    page_rows, reference_client, reference_template, source_effects,
)  # noqa: F401


COMPANIES = [("Demo Plumbing Co", "DEMO"), ("Reference Plumbing Co", "REF")]
EXPECTED = json.loads(files("bookflow.demo").joinpath("reference-expected.json").read_text())


def commercial_effects(prefix="REF"):
    """USD cents, independent of the sales calculator and seed command inputs.

    Each original is service10000 + tax800 = 10800. Each correction adds
    exempt service2000: subtotal12000 + tax800 = 12800. For each noun there
    are two originals, two original reversals, two replacements and one void.
    Control gross = [2*10800 + 2*12800, 2*10800 + 12800] = [47200,34400].
    Across both nouns income gross = [2*(2*10000+12000),
    2*(2*10000+2*12000)] = [64000,88000]; tax gross = [4800,6400].
    Thus AR+12800, Checking+12800, income+24000, tax liability+1600.
    Trial balance increases25600; net assets/equity and profit increase24000.
    All effects are in September, so January-August and H2 opening stay put.
    """
    rows = []
    for tag, control in [("INV", "Accounts Receivable"), ("SR", "Checking")]:
        for state, day in [("ACTIVE", "05"), ("VOID", "06")]:
            batches = [(10000, "original"), (-10000, "reversal"), (12000, "replacement")]
            if state == "VOID":
                batches.append((-12000, "reversal"))
            for net, kind in batches:
                tax = 800 if net > 0 else -800
                for account, signed in [(control, net + tax), ("Service Income", -net),
                                        ("Sales Tax Payable", -tax)]:
                    rows.append((account, "2026-09-" + day, prefix + "-SALE-" + tag + "-" + state,
                                 kind, max(signed, 0), max(-signed, 0)))
    return rows


def test_reference_expectations_include_exact_commercial_arithmetic():
    # The existing independent journal oracle supplies only historical journals;
    # exclude our namespace if the parent extends that shared helper as well.
    historical = [r for r in source_effects() if not r[2].startswith("REF-SALE-")]
    effects = historical + commercial_effects()
    for checkpoint in [*EXPECTED["monthly"], EXPECTED["annual"]]:
        for account, balance in checkpoint["balances"].items():
            facts = [r for r in effects if r[0] == account and r[1] <= checkpoint["date_to"]]
            gross = [sum(r[i] for r in facts) for i in (4, 5)]
            assert checkpoint["gross_debits_credits"][account] == gross
            assert balance == gross[0] - gross[1]
        b = checkpoint["balances"]
        assert sum(b.values()) == 0
        assert checkpoint["trial_balance"] == sum(max(v, 0) for v in b.values())
        assert checkpoint["income"] == -sum(b[a] for a in (
            "Service Income", "Professional Fees", "Insurance Expense", "Depreciation Expense"))
        assert checkpoint["net_assets"] == sum(b[a] for a in (
            "Checking", "Accounts Receivable", "Equipment", "Accumulated Depreciation",
            "Business Credit Card", "Sales Tax Payable"))
        assert checkpoint["net_assets"] == 1000000 + checkpoint["income"]
        assert checkpoint["accounts_receivable"] == b["Accounts Receivable"]
        assert checkpoint["accounts_payable"] == 0
    assert EXPECTED["annual"] == EXPECTED["monthly"][-1]
    assert EXPECTED["second_half"]["opening"] == EXPECTED["monthly"][5]["balances"]
    for account, gross in EXPECTED["second_half"]["gross_debits_credits"].items():
        facts = [r for r in effects if r[0] == account and r[1] >= "2026-07-01"]
        assert gross == [sum(r[i] for r in facts) for i in (4, 5)]
    annual = EXPECTED["annual"]
    assert (annual["trial_balance"], annual["income"], annual["net_assets"]) == (8030633, 6439030, 7439030)


@pytest.mark.parametrize("resource", ["seed.toml", "reference.toml"])
def test_both_seed_manifests_exercise_all_twelve_sales_commands(resource):
    commands = tomllib.loads(files("bookflow.demo").joinpath(resource).read_text())["commands"]
    sales = [e for e in commands if e["command"].split()[0] in ("invoice", "sales-receipt")]
    assert {e["command"] for e in sales} == {
        f"{noun} {verb}" for noun in ("invoice", "sales-receipt")
        for verb in ("post", "update", "void", "show", "query", "history")}
    for entry in sales:
        if entry["command"].split()[1] in ("post", "update", "void"):
            assert entry["reason"].strip()


@pytest.mark.parametrize("company,prefix", COMPANIES)
def test_seed_sales_history_custom_facts_and_exact_effects(reference_client, company, prefix):
    client, _ = reference_client
    customer = client.customer.show(customer="Commercial Example Customer", company=company)
    assert customer["current_balance"]["minor_units"] == 12800
    for noun, selector, tag in [("invoice", "invoice", "INV"), ("sales-receipt", "sales_receipt", "SR")]:
        page = client.run(noun + " query", {"customer": customer["id"]}, company=company)
        assert page["count"] == 2 and not page["has_more"]
        assert Counter(r["status"] for r in page["items"]) == {"posted": 1, "voided": 1}
        for state in ("ACTIVE", "VOID"):
            number = prefix + "-SALE-" + tag + "-" + state
            row = next(r for r in page["items"] if r["number"] == number)
            args = {selector: row["id"]}
            current = client.run(noun + " show", args, company=company)
            original = client.run(noun + " show", dict(args, revision_number=1), company=company)
            history = client.run(noun + " history", args, company=company)
            assert current["version"] == (2 if state == "ACTIVE" else 3)
            assert current["total_minor_units"] == 12800
            assert original["revision"]["total_minor_units"] == 10800
            assert [r["revision_number"] for r in history["items"]] == [1, 2]
            assert [len(r["batches"]) for r in history["items"]] == [2, 1 if state == "ACTIVE" else 2]
            assert current["revision"]["lines"][0]["line_id"] == original["revision"]["lines"][0]["line_id"]
            assert [l["tax_minor_units"] for l in current["revision"]["lines"]] == [800, 0]
            for document in (original, current):
                field, = document["revision"]["custom_fields"]
                assert (field["name"], field["kind"], field["value"]) == ("Site verified", "bool", False)
    definition = client.run("custom-field show", {"custom_field": "Site verification complete"}, company=company)
    assert definition["kind"] == "bool"
    rows, _ = page_rows(client.report.general_ledger, company=company,
                        date_from="2026-01-01", date_to="2026-12-31", limit=200)
    actual = defaultdict(lambda: [0, 0])
    for row in rows:
        if row["kind"] == "posting" and row["transaction_number"].startswith(prefix + "-SALE-"):
            key = (row["current_account_label"], row["effective_date"], row["transaction_number"], row["batch_kind"])
            actual[key][0] += row["debit"]["minor_units"]
            actual[key][1] += row["credit"]["minor_units"]
    expected = defaultdict(lambda: [0, 0])
    for account, day, number, kind, debit, credit in commercial_effects(prefix):
        expected[account, day, number, kind][0] += debit
        expected[account, day, number, kind][1] += credit
    assert dict(actual) == dict(expected)


@pytest.mark.parametrize("company,prefix", COMPANIES)
def test_seed_known_balances_counts_and_readonly_preview(reference_client, company, prefix):
    client, _ = reference_client
    checking, trial, journals = (624895, 690228, 10) if prefix == "DEMO" else (7267800, 8030633, 36)
    assert client.account.show(account="Checking", company=company)["balance"]["minor_units"] == checking
    assert client.account.show(account="Accounts Receivable", company=company)["balance"]["minor_units"] == 12833
    assert client.account.show(account="Sales Tax Payable", company=company)["balance"]["minor_units"] == 1603
    balance = client.report.trial_balance(company=company, date_to="2026-12-31", limit=200)
    totals = balance["totals"]
    assert totals["debit"]["minor_units"] == totals["credit"]["minor_units"] == trial
    expected_balances = ({"Checking": 624895, "Accounts Receivable": 12833,
                          "Professional Fees": 52500, "Service Income": -185625,
                          "Opening Balance Equity": -500000, "Business Credit Card": -3000,
                          "Sales Tax Payable": -1603} if prefix == "DEMO"
                         else EXPECTED["annual"]["balances"])
    assert {r["current_account_label"]: r["signed_net"]["minor_units"] for r in balance["rows"]} == expected_balances
    profit = client.report.profit_and_loss(company=company, date_from="2026-01-01", date_to="2026-12-31")
    sheet = client.report.balance_sheet(company=company, date_to="2026-12-31")
    assert profit["totals"]["net_income"]["minor_units"] == (133125 if prefix == "DEMO" else 6439030)
    assert sheet["totals"]["total_equity"]["minor_units"] == (633125 if prefix == "DEMO" else 7439030)
    assert sheet["totals"]["difference"]["minor_units"] == 0
    assert client.journal.query(company=company, limit=200)["count"] == journals
    database = Path(client.company.show(company=company)["path"]) / "company.db"
    def counts():
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
            return {name: db.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                    for name in ("transactions", "transaction_revisions", "posting_batches", "posting_lines",
                                 "sales_profiles", "sales_line_profiles", "sales_tax_components", "audit_events")}
    before = counts()
    assert before["transactions"] == journals + 22  # 4 sales + 6 whole-work + 8 progress + 1 preference + 3 active tax examples
    assert before["sales_profiles"] == 28
    assert before["sales_line_profiles"] == 49
    assert before["sales_tax_components"] == 44
    assert (before["transaction_revisions"], before["posting_batches"], before["posting_lines"]) == (
        (42, 70, 280) if prefix == "DEMO" else (65, 90, 313))
    for noun in ("invoice", "sales-receipt"):
        args = dict(date="2026-09-07", customer="Commercial Example Customer",
                    sales_tax_item="Commercial Example Tax 8%", customer_tax_code="Tax",
                    lines=[dict(item="Commercial Example Service")])
        if noun == "sales-receipt":
            args.update(deposit_to="Checking", payment_method="Check")
        preview = client.run(noun + " post", args, company=company, dry_run=True)
        assert preview["total_minor_units"] == 10800
        assert len(preview["facts_fingerprint"]) == 64
        assert preview["revision"]["custom_fields"][0]["value"] is True
    assert counts() == before
    print(company, before)
