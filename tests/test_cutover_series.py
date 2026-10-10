"""The move-in with an idempotency key, and the document series it carries forward.

Harbor Electric (tests/fixtures/fakeco/) moves in at 2026-06-30. Its open invoices run to 2386 (Lakeview
Property Management, Cass Street Apartments, 6/30) and its credit memo is 2350; its uncleared checks on
Checking are 4471 to 4473. In the anchor product the next invoice number follows the last one used and the
next check follows the last check written, so July's first new invoice is 2387 (as the paperwork numbers it)
and the first new check is 4474.

The blind July trial (R170) found two things here: `cutover apply` with an idempotency key stopped part-way,
refusing its own read of the reconciliation candidates for carrying the key; and after the move-in the first
new invoice was numbered 1.
"""
from tests import fakeco_replay as replay_module
from tests.test_cutover_rest import _harbor

AS_OF = replay_module.AS_OF
MAPPINGS = replay_module.MAPPINGS


def test_a_keyed_move_in_finishes_first_time_reruns_to_nothing_and_carries_the_series(tmp_path, monkeypatch):
    harbor = _harbor(tmp_path, monkeypatch)
    run = harbor.run
    files = harbor.attach(replay_module.CORE_FILES + replay_module.REST)
    move_in = dict(as_of=AS_OF, files=files, mappings=MAPPINGS)

    first = run("cutover apply", move_in, reason="Move in from the old books", idempotency_key="harbor-cutover-2026-06-30")
    assert first["created"] > 0 and first["already_in"] == 0
    assert "the next invoice number is 2387" in first["summary"], first["summary"]
    # The same key replays the stored answer; a fresh key finds everything already in.
    same = run("cutover apply", move_in, reason="Move in from the old books", idempotency_key="harbor-cutover-2026-06-30")
    assert (same["created"], same["already_in"]) == (first["created"], first["already_in"])
    again = run("cutover apply", move_in, reason="Move in again", idempotency_key="harbor-cutover-again")
    assert again["created"] == 0 and again["already_in"] == first["created"]
    assert "next invoice number" not in again["summary"]

    # The three June reconciliations are open drafts with their uncleared items placed: the keyed run got there.
    tie = run("cutover tie-out", dict(as_of=AS_OF, files=files + harbor.attach(replay_module.AGING_FILES), mappings=MAPPINGS))
    assert tie["tied"], tie["summary"]

    invoice = run("invoice post", dict(customer="Abernathy, Ruth", date="2026-07-01",
                                       lines=[dict(item="Labor", quantity="1", unit_price="105.00")]))
    assert invoice["number"] == "2387"
    credit = run("credit-memo post", dict(customer="Abernathy, Ruth", date="2026-07-01",
                                          lines=[dict(item="Labor", quantity="1", unit_price="10.00")]))
    assert credit["number"] == "2388"

    expense = next(row["id"] for row in run("account list")["items"] if row["type"] == "expense")
    check = run("check post", dict(account="Checking", date="2026-07-01", amount="12.00",
                                   expenses=[dict(account=expense, amount="12.00")]))
    assert check["document"]["check_number"] == "4474", check["document"]

