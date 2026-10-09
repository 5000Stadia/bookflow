"""R170: Harbor Electric, a fake company kept in Bookflow and checked against its answer key.

tests/fixtures/fakeco/ is written by tests/fakeco.py (see its README). The first test holds the
committed files to exactly what the generator writes, so the key and the files never drift apart.
The second keeps July the way a person would through the Python client (tests/fakeco_replay.py):
the move-in from the old books' exports, every July event, a reconciliation of checking (OFX),
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
    assert fakeco_replay.differences(replay.books("2026-07-31"), fakeco_replay.expected("2026-07-31")) == []

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

    # The person closed July: nothing more can be posted into it.
    assert replay.run("company show")["info"]["closing_date"] == "2026-07-31"
    with pytest.raises(BookflowError) as refused:
        replay.run("journal post", dict(date="2026-07-31", memo="Late entry", lines=[
            dict(account="Office Supplies", side="debit", amount="10.00"),
            dict(account="Checking", side="credit", amount="10.00")]))
    assert refused.value.code == "E_PERIOD_CLOSED"
