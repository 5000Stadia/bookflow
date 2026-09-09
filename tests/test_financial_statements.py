"""Statement oracles from business facts, public fixtures and exact signed effects."""
import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

import bookflow
from bookflow.company import schema, financial_statements as fs
from bookflow.core.errors import BookflowError
from tests.test_row8_reports import ledger, insert, all_pages, numbered_ledger  # noqa: F401
from tests.test_reference_year import reference_template, reference_client, EXPECTED, REFERENCE, cli_run  # noqa: F401


@pytest.fixture
def financial_ledger(ledger):
    s, batch, oracle = ledger
    for account, kind in (("b", "income"), ("c", "expense"), ("z", "non_posting")):
        s.company.raw.execute("UPDATE accounts SET type=? WHERE id=?", (kind, account))
    for account, kind in (("liability", "credit_card"), ("equity", "equity"), ("contra", "fixed_asset"),
                          ("cost", "cost_of_goods_sold"), ("other-income", "other_income"), ("other-expense", "other_expense")):
        insert(s.company, schema.accounts, id=account, name=account, name_key=account, full_name=account,
               full_name_key=account, path=account, depth=1, type=kind, currency="USD", active=True)
    return s, batch, oracle


def pl(s, **kwargs):
    return fs.profit_and_loss(fs.ProfitAndLossInput(**{"date_from": "2026-01-01", "date_to": "2026-12-31", **kwargs}), s)


def bs(s, **kwargs):
    return fs.balance_sheet(fs.BalanceSheetInput(**{"date_to": "2026-12-31", **kwargs}), s)


def units(totals):
    return {key: value.minor_units for key, value in totals}


def test_statement_signs_contra_accounts_hierarchy_and_all_effects(financial_ledger):
    s, batch, _ = financial_ledger
    batch("2026-01-01", 1000, credit="equity")
    batch("2026-01-02", 500)
    original, revision = batch("2026-02-01", 200)
    batch("2026-02-01", 200, debit="b", credit="a", kind="reversal", reverses=original, revision=revision)
    batch("2026-02-01", 150, kind="replacement", replaces=original)
    batch("2026-03-01", 50, debit="cost", credit="a")
    batch("2026-03-02", 80, debit="c", credit="liability")
    batch("2026-03-03", 20, debit="a", credit="c")  # expense credit
    batch("2026-03-04", 30, credit="other-income")
    batch("2026-03-05", 15, debit="other-expense", credit="contra")
    batch("2027-01-01", 9999)  # excluded future
    s.company.raw.execute("UPDATE accounts SET parent_id='cost',full_name='cost:c',full_name_key='cost:c',number='501' WHERE id='c'")
    report, rows = all_pages(lambda **kw: pl(s, **kw), limit=1)
    assert units(report.totals) == dict(income=650, cost_of_goods_sold=50, gross_profit=600,
        expense=60, net_operating_income=540, other_income=30, other_expense=15, net_income=555)
    assert {r.account_id: r.amount.minor_units for r in rows} == {"b":650,"cost":50,"c":60,"other-income":30,"other-expense":15}
    expense = next(r for r in rows if r.account_id == "c")
    assert not expense.active and expense.parent_id == "cost"
    assert expense.display_account_label == "501 · cost:c"
    s.company.raw.execute("UPDATE company_info SET show_lowest_subaccount_only=1")
    assert next(r for r in pl(s).rows if r.account_id == "c").display_account_label == "501 · c"
    s.company.raw.execute("UPDATE company_info SET use_account_numbers=0")
    assert next(r for r in pl(s).rows if r.account_id == "c").display_account_label == "c"
    balance, detail = all_pages(lambda **kw: bs(s, **kw), limit=1)
    assert units(balance.totals) == dict(assets=1635,liabilities=80,posted_equity=1000,prior_earnings=0,
        current_year_income=555,total_equity=1555,liabilities_and_equity=1635,difference=0)
    assert next(r for r in detail if r.account_id == "contra").amount.minor_units == -15


def test_losses_fiscal_rollover_and_prior_manual_transfer(financial_ledger):
    s, batch, _ = financial_ledger
    batch("2025-12-30", 100)
    batch("2025-12-31", 60, debit="b", credit="equity")
    batch("2026-01-01", 30, debit="c", credit="a")
    report = bs(s)
    assert (report.totals.prior_earnings.minor_units,report.totals.current_year_income.minor_units,
            report.totals.posted_equity.minor_units,report.totals.total_equity.minor_units) == (40,-30,60,70)
    assert pl(s).totals.net_income.minor_units == -30
    assert report.totals.difference.minor_units == 0
    s.company.raw.execute("UPDATE company_info SET fiscal_year_start_month=7")
    before = bs(s,date_to="2026-06-30")
    after = bs(s,date_to="2026-07-01")
    assert before.fiscal_year_start == "2025-07-01" and after.fiscal_year_start == "2026-07-01"
    assert before.totals.current_year_income.minor_units == 10
    assert after.totals.prior_earnings.minor_units == 10 and after.totals.current_year_income.minor_units == 0
    assert bs(s,date_to="0001-01-01").fiscal_year_start == "0001-01-01"


def test_zero_activity_accounts_do_not_duplicate_or_leak_nonposting(financial_ledger):
    s, batch, _ = financial_ledger
    batch("2026-01-01",50)
    batch("2026-01-02",50,debit="b",credit="a")
    assert pl(s).rows == [] and bs(s).rows == []
    assert len(pl(s,include_zero=True).rows) == 5
    assert len(bs(s,include_zero=True).rows) == 4
    assert "z" not in {r.account_id for r in pl(s,include_zero=True).rows + bs(s,include_zero=True).rows}


def test_profit_and_loss_zero_selection_uses_period_not_ending_net(financial_ledger):
    s,batch,_=financial_ledger
    batch("2025-12-31",100)
    assert pl(s).rows==[]
    assert next(r for r in pl(s,include_zero=True).rows if r.account_id=="b").amount.minor_units==0
    assert bs(s).totals.prior_earnings.minor_units==100
    batch("2026-01-01",100,debit="b",credit="a")
    assert [(r.account_id,r.amount.minor_units) for r in pl(s).rows]==[("b",-100)]
    assert bs(s).totals.current_year_income.minor_units==-100
    assert bs(s).totals.assets.minor_units==0


@pytest.mark.parametrize("sql", [
    "UPDATE company_info SET fiscal_year_start_month=7",
    "UPDATE company_info SET use_account_numbers=0",
    "UPDATE accounts SET active=0 WHERE id='a'",
    "UPDATE accounts SET full_name='renamed' WHERE id='a'",
    "UPDATE accounts SET number='123' WHERE id='a'",
    "UPDATE accounts SET parent_id='other-income' WHERE id='b'",
])
@pytest.mark.parametrize("call",[pl,bs])
def test_changed_display_or_fiscal_state_stales_both_statements(financial_ledger,sql,call):
    s, batch, _ = financial_ledger
    batch("2026-01-01",50)
    first=call(s,include_zero=True,limit=1)
    s.company.raw.execute(sql)
    with pytest.raises(BookflowError,match="E_QUERY_STALE"):
        call(s,include_zero=True,limit=1,cursor=first.next_cursor)


@pytest.mark.parametrize("call",[pl,bs])
def test_cursor_binding_tamper_and_unrelated_audit_change(financial_ledger,call):
    s,batch,_=financial_ledger
    batch("2026-01-01",50)
    first=call(s,include_zero=True,limit=1)
    for change in ({"limit":2}, {"date_to":"2026-11-30"}, {"include_zero":False}, {"cursor":first.next_cursor+"bad"}):
        with pytest.raises(BookflowError,match="E_VALIDATION"):
            call(s,**{"include_zero":True,"limit":1,"cursor":first.next_cursor,**change})
    s.company_row["id"]="other-company"
    with pytest.raises(BookflowError,match="E_VALIDATION"):
        call(s,include_zero=True,limit=1,cursor=first.next_cursor)
    s.company_row["id"]="company"
    s.actor.id="other-reader"
    with pytest.raises(BookflowError,match="E_VALIDATION"):
        call(s,include_zero=True,limit=1,cursor=first.next_cursor)
    s.actor.id="actor"
    insert(s.company,schema.audit_events,id="unrelated-note",seq=100,actor_kind="human")
    with pytest.raises(BookflowError,match="E_QUERY_STALE"):
        call(s,include_zero=True,limit=1,cursor=first.next_cursor)


@pytest.mark.parametrize("call,account",[(pl,"other-expense"),(bs,"contra")])
def test_overflow_on_later_page_is_not_hidden(financial_ledger,call,account):
    s,batch,_=financial_ledger
    for _ in range(2): batch("2026-01-01",2**62,debit=account,credit="equity")
    with pytest.raises(BookflowError,match="E_VALUE_RANGE"):
        call(s,limit=1,include_zero=True)


@pytest.mark.parametrize("model",[fs.ProfitAndLossInput,fs.BalanceSheetInput])
@pytest.mark.parametrize("change",[{"basis":"cash"},{"date_to":"2026-02-30"},{"limit":1.0},{"include_zero":1},{"unexpected":True}])
def test_strict_report_inputs(model,change):
    fields={"date_to":"2026-12-31"}
    if model is fs.ProfitAndLossInput: fields["date_from"]="2026-01-01"
    with pytest.raises(ValidationError): model(**{**fields,**change})


def test_reference_monthly_annual_half_year_cli_and_read_only(reference_client):
    c,root=reference_client
    before=c.audit.list(company=REFERENCE,limit=1)
    for checkpoint in EXPECTED["monthly"]:
        profit=c.report.profit_and_loss(company=REFERENCE,date_from="2026-01-01",date_to=checkpoint["date_to"],limit=2)
        balance=c.report.balance_sheet(company=REFERENCE,date_to=checkpoint["date_to"],limit=2)
        assert profit["totals"]["net_income"]["minor_units"] == checkpoint["income"]
        assert balance["totals"]["total_equity"]["minor_units"] == checkpoint["net_assets"]
        assert balance["totals"]["current_year_income"] == profit["totals"]["net_income"]
        assert balance["totals"]["difference"]["minor_units"] == 0
    half=c.report.profit_and_loss(company=REFERENCE,date_from="2026-07-01",date_to="2026-12-31")
    assert half["totals"]["net_income"]["minor_units"] == 6439035-2305000
    rollover=c.report.balance_sheet(company=REFERENCE,date_to="2027-01-01")
    assert rollover["totals"]["prior_earnings"]["minor_units"] == 6439035
    assert rollover["totals"]["current_year_income"]["minor_units"] == 0
    for name,args in (("profit-and-loss",["--date-from","2026-01-01"]),("balance-sheet",[])):
        output=cli_run(root,"report",name,"--company",REFERENCE,"--date-to","2026-12-31",*args)
        method=c.report.profit_and_loss if name=="profit-and-loss" else c.report.balance_sheet
        options={"date_from":"2026-01-01"} if name=="profit-and-loss" else {}
        direct=method(company=REFERENCE,date_to="2026-12-31",**options)
        assert output["totals"] == direct["totals"] and output["rows"] == direct["rows"]
    assert c.audit.list(company=REFERENCE,limit=1) == before


def test_company_copy_attach_reproduces_statements_without_original_hub(reference_client,tmp_path):
    c,_=reference_client
    info=c.company.show(company=REFERENCE)
    c.company.update(company=REFERENCE,expected_version=info["info_version"],fiscal_year_start_month=7,show_lowest_subaccount_only=True)
    options={"date_to":"2026-12-31","include_zero":True,"limit":200}
    profit=c.report.profit_and_loss(company=REFERENCE,date_from="2026-01-01",**options)
    balance=c.report.balance_sheet(company=REFERENCE,**options)
    other=tmp_path/"separate-root"
    target=bookflow.connect(data_root=str(other));target.init();target.organization.new(name="Portable")
    destination=other/"organizations"/"Portable"/"Imported"
    shutil.copytree(Path(info["path"]),destination)
    attached=target.company.attach(path=str(destination))["company_id"]
    copied_pl=target.report.profit_and_loss(company=attached,date_from="2026-01-01",**options)
    copied_bs=target.report.balance_sheet(company=attached,**options)
    for original,copied in ((profit,copied_pl),(balance,copied_bs)):
        assert copied["rows"] == original["rows"] and copied["totals"] == original["totals"]
    assert copied_bs["fiscal_year_start"] == "2026-07-01"


def test_statements_order_rows_by_account_number_then_name(numbered_ledger):
    s, _, _ = numbered_ledger
    balance, asset_rows = all_pages(lambda **kw: bs(s, date_to="2026-01-31", **kw), limit=1)
    assert [r.account_id for r in asset_rows] == ["k2", "k4"]           # 1010 then 1100
    assert [r.display_account_label for r in asset_rows] == ["1010 · Zulu Bank Checking", "1100 · Accounts Receivable"]
    assert {r.section for r in asset_rows} == {"assets"}
    assert balance.totals.assets.minor_units == 0
    assert balance.totals.difference.minor_units == 0

    report, income_rows = all_pages(lambda **kw: pl(s, date_from="2026-01-01", date_to="2026-01-31", **kw), limit=1)
    assert [r.account_id for r in income_rows] == ["k3", "k1"]          # 4000 then 4200
    assert [r.display_account_label for r in income_rows] == ["4000 · Service Income", "4200 · Product Sales"]
    # Presentation only: same rows, same signed amounts, same statement totals.
    assert {r.account_id: r.amount.minor_units for r in income_rows} == {"k1": -50, "k3": 50}
    assert report.totals.income.minor_units == 0
