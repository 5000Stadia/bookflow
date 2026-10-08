"""R166: reading the old books' exports, without a database.

Each expected value is read off the export text by hand: what a cell says is what it must parse to.
"""
from pathlib import Path

import pytest

from bookflow.company import cutover_sources as src

FIXTURES = Path(__file__).parent / "fixtures" / "cutover"


def read(*named):
    files = [src.SourceFile(name, kind, src.digest(text), text) for name, kind, text in named]
    return src.read(files, 2)


def fixture(name):
    return src.decode((FIXTURES / name).read_bytes())


@pytest.mark.parametrize("cell, minor", [
    ("1,234.56", 123456), ("-1,234.56", -123456), ("(1,234.56)", -123456), ("$12", 1200), ("0.00", 0),
    (".5", 50), ("1234.5", 123450), ("1,234.56-", -123456), ("", None), ("  ", None)])
def test_report_amounts_are_exact_minor_units(cell, minor):
    assert src.parse_amount(cell) == minor


@pytest.mark.parametrize("cell", ["12.345", "1.2.3", "abc", "1,23x"])
def test_an_amount_that_is_not_one_is_refused(cell):
    with pytest.raises(ValueError):
        src.parse_amount(cell)


@pytest.mark.parametrize("cell, day", [
    ("09/30/2026", "2026-09-30"), ("9/3/26", "2026-09-03"), ("2026-09-30", "2026-09-30"),
    ("September 30, 2026", "2026-09-30"), ("Sep 30, 26", "2026-09-30"), ("13/40/2026", None), ("soon", None)])
def test_report_dates(cell, day):
    assert src.parse_date(cell) == day


def test_account_cells_split_into_number_and_path():
    assert src.split_account_label("6700 · Utilities:6710 · Telephone") == ("6710", "Utilities:Telephone")
    assert src.split_account_label("1000 · Checking") == ("1000", "Checking")
    assert src.split_account_label("Utilities:Telephone") == (None, "Utilities:Telephone")
    assert src.key(" Harbor View Apartments : Unit 4B ") == "harbor view apartments:unit 4b"


def test_every_sample_export_is_known_by_its_own_headings():
    kinds = {path.name: src.detect(fixture(path.name)) for path in FIXTURES.iterdir()}
    assert kinds == {
        "accounts.iif": "iif", "customers.iif": "iif", "vendors.iif": "iif", "items.iif": "iif",
        "trial_balance.csv": "trial_balance", "open_invoices.csv": "open_invoices", "unpaid_bills.csv": "unpaid_bills",
        "ar_aging.csv": "ar_aging", "ap_aging.csv": "ap_aging", "inventory_valuation.csv": "inventory_valuation"}
    # Without its title rows a report is still known by its column headings.
    body = fixture("open_invoices.csv").split("\r\n", 3)[3]
    assert src.detect(body) == "open_invoices"
    trial = fixture("trial_balance.csv").split("\r\n", 4)[4]
    assert src.detect(trial) == "trial_balance"


def test_the_windows_encoded_trial_balance_reads_its_middle_dots():
    raw = (FIXTURES / "trial_balance.csv").read_bytes()
    assert b"\xb7" in raw and "·" in src.decode(raw)
    sources = read(("trial_balance.csv", None, src.decode(raw)))
    report = sources.trial_balances[0]
    assert (report.basis, report.as_of) == ("accrual", "2026-09-30")
    assert report.total_debit == report.total_credit == 29888793
    truck = [row for row in report.rows if row.path.startswith("Truck:")]
    assert [(row.number, row.path, row.net) for row in truck] == [
        ("1510", "Truck:Original Cost", 4850000), ("1520", "Truck:Accumulated Depreciation", -1455000)]


def test_jobs_take_their_customers_path_and_credits_stay_negative():
    sources = read(("open_invoices.csv", None, fixture("open_invoices.csv")))
    unit = [(d.type, d.number, d.open_balance) for d in sources.documents
            if d.party == "Harbor View Apartments:Unit 4B Remodel"]
    assert unit == [("Invoice", "1176", 238025), ("Credit Memo", "1180", -15000)]
    assert sum(d.open_balance for d in sources.documents) == 2509905
    assert not [p for p in sources.problems if p.severity == "blocking"]


def test_an_export_cut_short_is_caught_by_its_total_rows():
    text = fixture("open_invoices.csv")
    cut = text.replace(',,"Invoice","09/29/2026","1205","SPM-118","1% 10 Net 30","10/29/2026",,,"2,207.30"\r\n', "")
    assert cut != text
    codes = {p.code for p in read(("open_invoices.csv", None, cut)).problems if p.severity == "blocking"}
    assert codes == {"subtotal_mismatch", "total_mismatch"}


def test_iif_lists_read_every_section_and_name_what_they_skip():
    text = fixture("accounts.iif") + "!CTYPE\tNAME\tREFNUM\r\nCTYPE\tResidential\t1\r\n"
    sources = read(("lists.iif", None, text))
    assert len(sources.lists["account"]) == 43
    assert sources.ignored_lists == {"CTYPE": 1}
    assert sources.product == "QuickBooks Pro Version 33.0D"
    utilities = [row for row in sources.lists["account"] if row.path.startswith("Utilities")]
    assert [(row.path, row.get("ACCNUM"), row.get("ACCNTTYPE")) for row in utilities] == [
        ("Utilities", "6700", "EXP"), ("Utilities:Telephone", "6710", "EXP"), ("Utilities:Gas and Electric", "6720", "EXP")]
