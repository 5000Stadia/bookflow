"""The anchor's "Ask My Accountant" pattern: two Uncategorized accounts every company has, a read of
what is still waiting in them, the Overview's panel for it, and the help that tells an agent to
use them rather than guess."""
import re
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from bookflow.core import registry

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

EXPENSE = "Uncategorized Expense (Ask My Accountant)"
INCOME = "Uncategorized Income"
DEMO = "Demo Plumbing Co"


def _new(client, name, chart="general"):
    return client.company.new(legal_name=name, home_currency="USD", organization="Demo Holdings LLC",
                              timezone="UTC", chart=chart)


def test_new_companies_and_the_demo_have_both_accounts(client):
    for company in (DEMO, _new(client, "Fresh Books LLC")["company_id"],
                    _new(client, "No Chart LLC", chart="none")["company_id"]):
        expense = client.account.show(account=EXPENSE, company=company)
        income = client.account.show(account=INCOME, company=company)
        assert (expense["type"], expense["number"], expense["seed_key"]) == ("expense", "6990", "account.uncategorized-expense")
        assert (income["type"], income["number"], income["seed_key"]) == ("income", "4990", "account.uncategorized-income")
        # Ordinary accounts: posting to them is allowed everywhere an expense or income account is.
        assert expense["system_role"] is None and income["system_role"] is None


def test_profile_apply_adds_them_to_an_older_company_and_keeps_its_own(client):
    made = _new(client, "Older Books LLC")
    company, path = made["company_id"], Path(made["path"]) / "company.db"
    with sqlite3.connect(path) as db:
        # As a company created before the profile carried them: neither account, and its own
        # account on 6990 and its own "Uncategorized Income".
        db.execute("DELETE FROM accounts WHERE seed_key LIKE 'account.uncategorized-%'")
    db.close()
    client.account.create(name="Own Clearing", number="6990", type="expense", company=company)
    client.account.create(name="Uncategorized Income", type="income", company=company)

    applied = client.profile.apply(profile_id="standard", company=company)
    assert applied["version"] == 3
    assert (applied["inserted_by_list"]["account"], applied["preserved_by_list"]["account"]) == (1, 1)
    expense = client.account.show(account=EXPENSE, company=company)
    assert expense["number"] is None  # 6990 was taken, so it arrives unnumbered rather than refused
    assert client.account.show(account=INCOME, company=company)["seed_key"] is None  # theirs, kept
    again = client.profile.apply(profile_id="standard", company=company)
    assert sum(again["inserted_by_list"].values()) == 0


def test_the_read_counts_only_entries_still_waiting(client):
    company = _new(client, "Waiting Books LLC")["company_id"]
    empty = client.account.uncategorized(company=company)
    assert empty["count"] == 0 and empty["entries"] == [] and [a["count"] for a in empty["accounts"]] == [0, 0]

    def check(amount, memo):
        return client.check.post(account="Checking", date="2026-03-02", amount=amount, memo=memo,
                                 expenses=[{"account": EXPENSE, "amount": amount}], company=company)
    kept = check("45.00", "Hardware store, no receipt: tools or job supplies?")
    moved = check("20.00", "Parking or client meal?")
    voided = check("7.50", "Unknown card fee")
    client.check.update(check=moved["id"], expected_version=moved["version"], company=company,
                        expenses=[{"account": "Office Supplies", "amount": "20.00"}])
    client.check.void(check=voided["id"], reason="Entered twice", company=company)
    client.journal.post(date="2026-03-05", memo="Transfer from someone: refund or a sale?", company=company, lines=[
        {"account": "Checking", "side": "debit", "amount": "100.00"},
        {"account": INCOME, "side": "credit", "amount": "100.00"}])

    waiting = client.account.uncategorized(company=company)
    assert waiting["count"] == 2 and waiting["more"] is False
    by_name = {a["name"]: a for a in waiting["accounts"]}
    assert (by_name[EXPENSE]["count"], by_name[EXPENSE]["amount"]["amount"]) == (1, "45.00")
    assert (by_name[INCOME]["count"], by_name[INCOME]["amount"]["amount"]) == (1, "100.00")
    assert [(e["transaction_id"], e["memo"]) for e in waiting["entries"]][0] == (
        kept["id"], "Hardware store, no receipt: tools or job supplies?")
    # The amount is the account's balance: what reversals took back is not waiting.
    ledger = client.report.general_ledger(date_from="2026-01-01", date_to="2026-12-31",
                                          account=by_name[EXPENSE]["account_id"], company=company)
    assert ledger["totals"]["closing"]["amount"] == "45.00"
    assert len(client.account.uncategorized(limit=1, company=company)["entries"]) == 1
    assert client.account.uncategorized(limit=1, company=company)["more"] is True


def test_posting_help_and_the_agent_guide_point_to_uncategorized():
    registry.load_all()
    for name in ("check post", "card-charge post", "bill post", "journal post", "deposit post", "register post"):
        assert "Uncategorized Expense (Ask My Accountant)" in registry.get(name).description, name
        assert "memo saying what is" in registry.get(name).description, name
    from importlib.resources import files
    guide = files("bookflow.documentation").joinpath("resources/agent-guide.md").read_text(encoding="utf-8")
    assert "## When the account is unclear: Ask My Accountant" in guide
    assert "do not guess" in guide and "`account uncategorized`" in guide


def test_the_overview_counts_what_is_waiting_and_links_to_the_register(hosted):
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200

    def run(name, raw):
        answered = browser.post(f"/companies/{hosted.company_id}/commands/{name.replace(' ', '.')}", json=raw,
                                headers={"X-Bookflow-Workbench": "1", "X-Bookflow-Client-Name": "bookflow-workbench"})
        assert answered.status_code == 200, (name, answered.text[:400])
        return answered.json()

    before = run("account uncategorized", {})
    page = browser.get(f"/c/{hosted.company_id}/_overview").text
    if before["count"] == 0:
        assert "Ask My Accountant" not in page
    saved = run("check post", {"account": "Checking", "date": "2026-12-01", "amount": "12.34",
                               "memo": "Unlabelled receipt: what was this for?",
                               "expenses": [{"account": EXPENSE, "amount": "12.34"}]})
    after = run("account uncategorized", {})
    assert after["count"] == before["count"] + 1
    page = browser.get(f"/c/{hosted.company_id}/_overview").text
    assert 'id="uncategorized-title">Ask My Accountant<' in page
    assert f'data-uncategorized-count="{after["count"]}"' in page
    expense = next(a for a in after["accounts"] if a["name"] == EXPENSE)
    link = re.search(rf'<a href="([^"]+)" data-uncategorized-account="{expense["account_id"]}">', page).group(1)
    link = link.replace("&amp;", "&")
    assert link.startswith(f"/c/{hosted.company_id}/report/general-ledger?") and f"f:account={expense['account_id']}" in link
    assert browser.get(link).status_code == 200
    assert f'/c/{hosted.company_id}/check/{saved["id"]}' in page or after["more"]
