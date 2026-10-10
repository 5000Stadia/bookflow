"""R170: Harbor Electric, a fake company kept in Bookflow and checked against its answer key.

tests/fixtures/fakeco/ is written by tests/fakeco.py (see its README). The first test holds the
committed files to exactly what the generator writes, so the key and the files never drift apart.
The second keeps July the way a person would through the Python client (tests/fakeco_replay.py):
the move-in from the old books' exports (with the June reconciliations, uncleared items, waiting
receipts and the 1099 Summary), every July event, a reconciliation of checking (OFX),
savings and the Visa card (CSV) to their statements, and the closing date. The books must then
agree with the key to the cent.
"""
import pytest

import bookflow
from bookflow import BookflowError

from tests import fakeco, fakeco_render, fakeco_replay


def test_the_fixture_is_what_the_generator_writes():
    files = fakeco_render.render()
    committed = {str(p.relative_to(fakeco.OUT)): p.read_bytes() for p in fakeco.OUT.rglob("*")
                 if p.is_file() and p.name != "README.md"}
    assert sorted(files) == sorted(committed)
    stale = [path for path, data in files.items() if committed[path] != data]
    assert not stale, f"regenerate with `python -m tests.fakeco`: {stale}"


@pytest.mark.timeout(900)
def test_july_kept_in_bookflow_matches_the_answer_key(tmp_path, monkeypatch):
    root = tmp_path / "root"
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(root))
    monkeypatch.delenv("BOOKFLOW_COMPANY", raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    replay = fakeco_replay.Replay(root, client)
    month = replay.keep_july()

    tie = replay.cutover["tie"]
    assert tie["tied"], tie["summary"]
    # Interest Expense was mapped to `create` on a number Bookflow's chart holds: it came in unnumbered, with the
    # collision stated, and the tie-out still found it (R166).
    dropped = [e for e in replay.cutover["plan"]["exceptions"] if e["code"] == "account_number_dropped"]
    assert len(dropped) == 1 and "Other Expense" in dropped[0]["problem"] and "renumber" in dropped[0]["fix"]
    assert fakeco_replay.differences(replay.books("2026-07-31"), fakeco_replay.expected("2026-07-31")) == []

    # Every cleared check pairs on its own number at the first import: nothing is ticked by hand and no check is
    # told to be entered again (R167).
    checking = month["reconciliations"]["Checking"]
    assert checking["by_hand"] == []
    assert not [line for line in checking["first"]["lines"] if line["status"] == "unmatched" and line["number"]]

    key = fakeco_replay.key("2026-07-31")["reconciliations"]
    for account, done in month["reconciliations"].items():
        want, totals = key[account], done["finish"]["totals"]
        card = account == "Visa Business Card"
        assert totals["difference"] == 0, account
        assert (totals["beginning_balance"], totals["ending_balance"]) == (
            fakeco.cents(want["beginning_balance"]), fakeco.cents(want["ending_balance"])), account
        assert totals["positive_count"] + totals["negative_count"] == len(want["cleared"]), account
        outstanding = sorted(abs(row["amount"]) for row in done["outstanding"])
        assert outstanding == sorted(abs(fakeco.cents(row["amount"])) for row in want["outstanding"]), (account, card)

    # The year's 1099 summary is the whole year's: January to June from the old books, July from Bookflow.
    year = replay.run("report vendor-1099-summary", dict(date_from="2026-01-01", date_to="2026-12-31"))
    assert [(row["display_vendor_label"], row["payments"]["minor_units"], row["opening_payments"]["minor_units"])
            for row in year["rows"]] == [("Delgado, Ray", 845000 + 255000, 755000)]

    # Once July is reconciled and closed, the move-in run again makes nothing, and what it brought still ties as of
    # the cutover: each opening is now certified, the receipts were deposited, the 1099 figure stands (R179).
    given = [{"attachment": f["attachment"]} for f in replay.cutover["plan"]["files"]]
    again = replay.run("cutover apply", dict(as_of=fakeco_replay.AS_OF, files=given, mappings=fakeco_replay.MAPPINGS),
                       reason="Run the move-in again")
    assert again["created"] == 0 and "reconciliation_exists" not in {e["code"] for e in again["exceptions"]}
    tie = replay.run("cutover tie-out", dict(as_of=fakeco_replay.AS_OF, files=given, mappings=fakeco_replay.MAPPINGS))
    assert [(row["name"], row["opening"], row["tied"]) for row in tie["bank"]["rows"]] == [
        ("Checking", "certified", True), ("Savings", "certified", True), ("Visa Business Card", "certified", True)]
    assert (tie["undeposited"]["differences"], tie["vendor_1099"]["differences"],
            tie["trial_balance"]["differences"]) == (0, 0, 0)

    # No workaround is left in July that a v1.6 command now covers (R176-R179).
    workarounds = {row.kind for row in replay.fit.values() if row.status == "workaround"}
    assert workarounds == {"Other names list (owner)", "Group item (Smoke Alarm Package)"}, workarounds

    # The person closed July: nothing more can be posted into it.
    assert replay.run("company show")["info"]["closing_date"] == "2026-07-31"
    with pytest.raises(BookflowError) as refused:
        replay.run("journal post", dict(date="2026-07-31", memo="Late entry", lines=[
            dict(account="Office Supplies", side="debit", amount="10.00"),
            dict(account="Checking", side="credit", amount="10.00")]))
    assert refused.value.code == "E_PERIOD_CLOSED"
