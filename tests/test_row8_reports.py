"""Independent immutable-effect oracle; no journal writer required for SQL tests.

The fixture compiles the actual company tables, disables owner FKs only because
it deliberately seeds effects without the command-owned commercial graph, and
forbids mutation of seeded history. Command integration below uses real rollout.
"""
from __future__ import annotations

from collections import defaultdict
import json
import sqlite3
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
import sqlalchemy as sa

from bookflow.company import schema
from bookflow.company import ledger_reports as reports
from bookflow.storage.engine import open_database
from bookflow.core.errors import BookflowError

STAMP = "2026-09-05T00:00:00+00:00"


def insert(db, table, **values):
    """Supply provenance for real table DDL; explicit facts override placeholders."""
    row = {}
    for column in table.c:
        if column.name in values or column.nullable or column.default is not None or column.server_default is not None:
            continue
        row[column.name] = 1 if isinstance(column.type, sa.Integer) else "fixture"
    row.update(values)
    db.conn.execute(table.insert().values(**row))


@pytest.fixture
def ledger(tmp_path):
    with open_database(tmp_path / "ledger.db", writable=True, create=True) as db:
        db.raw.execute("PRAGMA foreign_keys=OFF")
        for name in ("report_cursor_keys", "accounts", "company_info", "audit_events", "transactions", "transaction_revisions", "posting_batches", "posting_lines", "posting_line_sources"):
            db.conn.execute(sa.schema.CreateTable(getattr(schema, name)))
        db.raw.execute("INSERT INTO report_cursor_keys VALUES (1, ?)", (b"r" * 32,))
        db.raw.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        db.raw.execute("INSERT INTO alembic_version VALUES ('co0007')")
        insert(db, schema.company_info, id="company", legal_name="Books", display_name="Books", home_currency="USD", timezone="UTC")
        insert(db, schema.transactions, id="document", type="journal_entry", number="J", status="posted", current_revision_id="revision0001")
        for account, active in (("a", True), ("b", True), ("c", False), ("z", False)):
            insert(db, schema.accounts, id=account, name=account, name_key=account, full_name=account,
                full_name_key=account, path=account, depth=1, type="bank", currency="USD", active=active)
        for table in ("posting_lines", "posting_batches", "transaction_revisions", "posting_line_sources"):
            for operation in ("UPDATE", "DELETE"):
                db.raw.execute(f"CREATE TRIGGER immutable_{table}_{operation} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable'); END")
        session = SimpleNamespace(company=db, company_row={"id": "company", "organization_id": "org"},
            actor=SimpleNamespace(id="actor"), is_hub_admin=True, memberships=[])
        oracle = []
        serial = 0

        def batch(day, units, *, debit="a", credit="b", kind="original", reverses=None, replaces=None, revision=None):
            nonlocal serial
            serial += 1
            bid, rid, event = f"batch{serial:04}", revision or f"revision{serial:04}", f"event{serial:04}"
            insert(db, schema.audit_events, id=event, seq=serial, actor_kind="human")
            if revision is None:
                insert(db, schema.transaction_revisions, id=rid, transaction_id="document", revision_number=serial,
                    date=day, number=f"J{serial}", total_minor_units=units, currency="USD",
                    issuer_snapshot="{}", custom_fields_snapshot="{}", audit_event_id=event)
            insert(db, schema.posting_batches, id=bid, transaction_id="document", revision_id=rid,
                kind=kind, effective_date=day, reverses_batch_id=reverses, replaces_batch_id=replaces,
                audit_event_id=event, created_at=STAMP)
            for no, account, d, c in ((1, debit, units, 0), (2, credit, 0, units)):
                lid = f"{bid}-line{no}"
                insert(db, schema.posting_lines, id=lid, transaction_id="document", batch_id=bid, line_no=no,
                    account_id=account, debit_minor_units=d, credit_minor_units=c, currency="USD",
                    account_snapshot=json.dumps({"name": f"historical-{account}", "number": "100"}),
                    party_name="Historical party", class_name="Historical class", description="entered explanation")
                oracle.append((account, day, bid, no, lid, d, c))
            return bid, rid

        yield session, batch, oracle


def tb(session, **kwargs):
    return reports.trial_balance(reports.TrialBalanceInput(date_to="2026-01-31", **kwargs), session)


def gl(session, **kwargs):
    return reports.general_ledger(reports.GeneralLedgerInput(date_from="2026-01-01", date_to="2026-01-31", **kwargs), session)


def all_pages(call, **kwargs):
    result, rows, cursor = None, [], None
    while True:
        page = call(cursor=cursor, **kwargs)
        assert page.count == len(page.rows) <= kwargs.get("limit", 50)
        if result is not None:
            assert page.metadata == result.metadata
            assert page.totals == result.totals
        else:
            result = page
        rows.extend(page.rows)
        cursor = page.next_cursor
        if cursor is None:
            return result, rows
        assert len(cursor) <= 4096


def test_trial_balance_nets_all_correction_and_void_effects(ledger):
    s, batch, oracle = ledger
    original, revision = batch("2026-01-02", 100)
    batch("2026-01-02", 100, debit="b", credit="a", kind="reversal", reverses=original, revision=revision)
    replacement, revision2 = batch("2026-01-10", 70, kind="replacement", replaces=original)
    batch("2026-01-10", 70, debit="b", credit="a", kind="reversal", reverses=replacement, revision=revision2)
    batch("2026-01-20", 35)
    batch("2026-01-20", 12, debit="b", credit="a")
    batch("2026-02-01", 500)  # Future effect is outside the as-of contract.
    expected = defaultdict(int)
    for account, day, _, _, _, debit, credit in oracle:
        if day <= "2026-01-31":
            expected[account] += debit-credit
    page, rows = all_pages(lambda **kw: tb(s, **kw), limit=1)
    assert {r.account_id: r.signed_net.minor_units for r in rows} == dict(expected) == {"a": 23, "b": -23}
    assert page.totals.debit.minor_units == page.totals.credit.minor_units == 23
    assert reports.net_balances(s.company, as_of="2026-01-31") == dict(expected)
    assert reports.net_balances(s.company) == {"a": 523, "b": -523}


def test_inactive_zero_and_opening_only_accounts_are_bounded(ledger):
    s, batch, _ = ledger
    batch("2025-12-31", 90, debit="c")
    page = tb(s)
    assert {r.account_id for r in page.rows} == {"b", "c"}
    assert next(r for r in page.rows if r.account_id == "c").active is False
    _, zeros = all_pages(lambda **kw: tb(s, **kw), include_zero=True, limit=1)
    assert [r.account_id for r in zeros] == ["a", "b", "c", "z"]
    page, rows = all_pages(lambda **kw: gl(s, **kw), limit=1)
    assert [(r.account_id, r.kind, r.signed_balance.minor_units) for r in rows] == [
        ("b", "opening", -90), ("b", "closing", -90), ("c", "opening", 90), ("c", "closing", 90)]
    assert page.totals.period_debits.minor_units == 0
    assert set(page.model_dump()) == {"metadata", "count", "next_cursor", "totals", "rows"}


def test_running_window_order_and_snapshots_before_page_slice(ledger):
    s, batch, oracle = ledger
    batch("2025-12-31", 30)
    batch("2026-01-20", 9)
    batch("2026-01-05", 7)
    batch("2026-01-05", 3, debit="b", credit="a")
    s.company.raw.execute("UPDATE accounts SET full_name='Renamed', version=2 WHERE id='a'")
    page, rows = all_pages(lambda **kw: gl(s, **kw), limit=2)
    expected = defaultdict(int)
    expected_postings = []
    for account, day, bid, no, lid, d, c in sorted(oracle):
        expected[account] += d-c
        if day >= "2026-01-01":
            expected_postings.append((account, day, bid, no, lid, expected[account]))
    actual = [(r.account_id, r.effective_date, r.batch_id, r.line_no, r.posting_line_id, r.signed_balance.minor_units)
        for r in rows if r.kind == "posting"]
    assert actual == expected_postings
    for row in rows:
        if row.kind == "posting":
            assert row.account_snapshot["name"] == f"historical-{row.account_id}"
            assert row.party_name == "Historical party"
            assert row.class_name == "Historical class"
            assert row.transaction_number is not None
            assert row.recorded_at == STAMP
    assert next(r for r in rows if r.account_id == "a").current_account_label == "Renamed"
    assert page.totals.period_debits.minor_units == page.totals.period_credits.minor_units == 19
    assert [r.signed_balance.minor_units for r in rows if r.kind == "closing"] == [43, -43]


@pytest.mark.parametrize("numbers,lowest,label", [
    (True, False, "1010 · Assets:Checking"),
    (False, False, "Assets:Checking"),
    (True, True, "1010 · Checking"),
    (False, True, "Checking"),
])
def test_general_ledger_current_account_display_preserves_captured_account(ledger, numbers, lowest, label):
    s, batch, _ = ledger
    batch("2026-01-05", 7)
    s.company.raw.execute("UPDATE accounts SET name='Checking',full_name='Assets:Checking',number='1010' WHERE id='a'")
    s.company.raw.execute("UPDATE company_info SET use_account_numbers=?,show_lowest_subaccount_only=?", (numbers, lowest))
    page, rows = all_pages(lambda **kw: gl(s, **kw), limit=2)
    account_rows = [r for r in rows if r.account_id == "a"]
    assert {r.kind for r in account_rows} == {"opening", "posting", "closing"}
    for row in account_rows:
        assert row.display_account_label == label
        assert row.current_account_number == "1010"
        assert row.current_account_label == "Assets:Checking"
        if row.kind == "posting":
            assert row.account_snapshot["name"] == "historical-a"
    assert page.totals.period_debits.minor_units == page.totals.period_credits.minor_units == 7


def test_backdated_between_pages_stales_before_rows_and_restart_reconciles(ledger):
    s, batch, _ = ledger
    batch("2026-01-20", 20)
    first = gl(s, limit=1)
    batch("2025-12-31", 11)
    statements = []
    s.company.raw.set_trace_callback(statements.append)
    with pytest.raises(BookflowError) as caught:
        gl(s, limit=1, cursor=first.next_cursor)
    s.company.raw.set_trace_callback(None)
    assert caught.value.code == "E_QUERY_STALE"
    assert not any("running AS" in sql or "SELECT * FROM selected ORDER BY" in sql for sql in statements)
    _, rows = all_pages(lambda **kw: gl(s, **kw), limit=1)
    assert [r.signed_balance.minor_units for r in rows if r.kind == "posting"] == [31, -31]
    assert [r.signed_balance.minor_units for r in rows if r.kind == "opening"] == [11, -11]
    assert not s.company.raw.in_transaction


@pytest.mark.parametrize("change", ["label", "posting", "schema"])
def test_trial_balance_continuation_relevant_changes_stale(ledger, change):
    s, batch, _ = ledger
    batch("2026-01-10", 1)
    first = tb(s, limit=1)
    if change == "label":
        s.company.raw.execute("UPDATE accounts SET full_name='new',version=2 WHERE id='a'")
    elif change == "posting":
        batch("2026-01-01", 2)
    else:
        s.company.raw.execute("UPDATE alembic_version SET version_num='co0008'")
    with pytest.raises(BookflowError) as caught:
        tb(s, limit=1, cursor=first.next_cursor)
    assert caught.value.code == "E_QUERY_STALE"


def test_continuation_metadata_survives_unrelated_audit_and_future_posting(ledger):
    s, batch, _ = ledger
    batch("2026-01-10", 1)
    first = tb(s, limit=1)
    batch("2026-02-01", 100)
    second = tb(s, limit=1, cursor=first.next_cursor)
    assert second.metadata == first.metadata
    assert first.metadata.model_dump() == {
        "company_id": "company", "period": {"date_from": None, "date_to": "2026-01-31"},
        "basis": "accrual", "report_version": reports.REPORT_VERSION, "schema_revision": "co0007",
        "generation_time": first.metadata.generation_time, "audit_watermark": 1, "currency": "USD"}


@pytest.mark.parametrize("change", ["actor", "principal", "company", "permission", "filter"])
def test_cursor_binds_identity_permissions_and_query(ledger, change):
    s, batch, _ = ledger
    batch("2026-01-10", 1)
    first = tb(s, limit=1)
    kwargs = {}
    if change == "actor":
        s.actor.id = "other"
    elif change == "company":
        s.company_row["id"] = "other"
    elif change == "permission":
        s.memberships = [{"scope_type": "company", "scope_id": "company", "role": "readonly"}]
    elif change == "filter":
        kwargs["include_zero"] = True
    with pytest.raises(BookflowError) as caught:
        reports.trial_balance(reports.TrialBalanceInput(date_to="2026-01-31", limit=1, cursor=first.next_cursor, **kwargs),
            s, principal_id="other" if change == "principal" else None)
    assert caught.value.code == "E_VALIDATION"


def test_exact_cancellation_overflow_and_cleanup(ledger):
    s, batch, _ = ledger
    maximum = reports.I64_MAX
    batch("2026-01-01", maximum)
    batch("2026-01-02", maximum)
    assert reports.net_balances(s.company)["a"] == maximum*2
    with pytest.raises(BookflowError) as caught:
        tb(s)
    assert caught.value.code == "E_VALUE_RANGE"
    assert not s.company.raw.in_transaction
    batch("2026-01-03", maximum, debit="b", credit="a")
    batch("2026-01-04", maximum, debit="b", credit="a")
    assert reports.net_balances(s.company) == {"a": 0, "b": 0}
    assert tb(s).totals.debit.minor_units == 0
    # Gross period totals cannot be represented by the public Money contract.
    with pytest.raises(BookflowError) as caught:
        gl(s)
    assert caught.value.code == "E_VALUE_RANGE"
    assert not s.company.raw.in_transaction
    assert tb(s).rows == []


def test_integer_window_inverse_and_reject_float(ledger):
    s, _, _ = ledger
    raw = s.company.raw
    reports.register_ledger_functions(s.company)
    raw.execute("CREATE TABLE numbers (id INTEGER, n)")
    raw.executemany("INSERT INTO numbers VALUES (?,?)", [(1, reports.I64_MAX), (2, reports.I64_MAX), (3, -reports.I64_MAX)])
    assert raw.execute("SELECT bookflow_sum_int(n) FROM numbers").fetchone()[0] == str(reports.I64_MAX)
    assert [r[0] for r in raw.execute("SELECT bookflow_sum_int(n) OVER (ORDER BY id ROWS 1 PRECEDING) FROM numbers")] == [
        str(reports.I64_MAX), str(2*reports.I64_MAX), "0"]
    with pytest.raises(sqlite3.OperationalError):
        raw.execute("SELECT bookflow_sum_int(1.25)").fetchone()
    assert raw.execute("SELECT bookflow_sum_int(n) FROM numbers").fetchone()[0] == str(reports.I64_MAX)
    with pytest.raises(BookflowError):
        reports.money(True, "USD")


def test_cancellation_releases_owned_snapshot(ledger, monkeypatch):
    s, batch, _ = ledger
    batch("2026-01-01", 1)
    real = reports._state
    def cancel(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(reports, "_state", cancel)
    with pytest.raises(KeyboardInterrupt):
        gl(s)
    assert not s.company.raw.in_transaction
    monkeypatch.setattr(reports, "_state", real)
    assert gl(s).totals.period_debits.minor_units == 1
    s.company.raw.execute("BEGIN")
    gl(s)
    assert s.company.raw.in_transaction  # Caller-owned transactions are retained.
    s.company.raw.rollback()


@pytest.mark.parametrize("values", [
    {"date_to": "20260131"}, {"date_to": "2026-1-31"}, {"date_to": "2026-02-30"},
    {"date_to": " 2026-01-31"}, {"date_to": True}, {"date_to": 1.0},
    {"basis": "cash"}, {"limit": 0}, {"limit": 201}, {"limit": True}, {"limit": 1.0},
    {"cursor": "x"*4097}, {"include_zero": 1}, {"unrecognized": 1},
])
def test_trial_balance_input_rejects_invalid(values):
    with pytest.raises(ValidationError):
        reports.TrialBalanceInput.model_validate({"date_to": "2026-01-31", **values})


@pytest.mark.parametrize("values", [
    {"date_from": "2026-02-01"}, {"date_from": "2026-01-01T00:00:00Z"},
    {"basis": "cash"}, {"account": ""}, {"account": 12}, {"limit": False},
])
def test_gl_input_rejects_invalid(values):
    with pytest.raises(ValidationError):
        reports.GeneralLedgerInput.model_validate({"date_from": "2026-01-01", "date_to": "2026-01-31", **values})


@pytest.mark.parametrize("cursor", ["!", "e30", "", "🙃"])
def test_bad_cursor_error_and_cleanup(ledger, cursor):
    s, _, _ = ledger
    with pytest.raises(BookflowError) as caught:
        tb(s, cursor=cursor)
    assert caught.value.code == "E_VALIDATION"
    assert not s.company.raw.in_transaction


def test_sources_do_not_multiply_amounts(ledger):
    s, batch, _ = ledger
    bid, rid = batch("2026-01-10", 10)
    for n in range(2):
        insert(s.company, schema.posting_line_sources, id=f"source{n}", transaction_id="document", posting_line_id=f"{bid}-line1",
            revision_id=rid, document_line_id=f"document-line{n}", amount_minor_units=5, currency="USD")
    assert tb(s).totals.debit.minor_units == 10
    assert gl(s).totals.period_debits.minor_units == 10


def test_account_selector_and_renamed_selector_continuation(ledger):
    s, batch, _ = ledger
    batch("2025-12-31", 4, debit="c")
    batch("2026-01-15", 6, debit="c")
    first = gl(s, account="c", limit=1)
    assert first.rows[0].account_id == "c"
    assert first.totals.opening.minor_units == 4
    assert first.totals.period_debits.minor_units == 6
    assert first.totals.period_credits.minor_units == 0
    assert first.totals.closing.minor_units == 10
    s.company.raw.execute("UPDATE accounts SET name='changed',name_key='changed',full_name='changed',full_name_key='changed',version=2 WHERE id='c'")
    with pytest.raises(BookflowError) as caught:
        gl(s, account="c", limit=1, cursor=first.next_cursor)
    assert caught.value.code == "E_QUERY_STALE"
    with pytest.raises(BookflowError) as caught:
        gl(s, account="missing")
    assert caught.value.code == "E_RECORD_NOT_FOUND"


def test_single_page_snapshot_keeps_labels_and_metadata_consistent(ledger, monkeypatch):
    s, batch, _ = ledger
    batch("2026-01-01", 3)
    original_state = reports._state
    def changed_after_state(*args, **kwargs):
        state = original_state(*args, **kwargs)
        with sqlite3.connect(s.company.path, isolation_level=None) as writer:
            writer.execute("UPDATE accounts SET full_name='Concurrent label',version=2 WHERE id='a'")
        return state
    monkeypatch.setattr(reports, "_state", changed_after_state)
    first = tb(s, limit=1)
    assert first.rows[0].current_account_label == "a"
    monkeypatch.setattr(reports, "_state", original_state)
    with pytest.raises(BookflowError) as caught:
        tb(s, limit=1, cursor=first.next_cursor)
    assert caught.value.code == "E_QUERY_STALE"
    assert tb(s).rows[0].current_account_label == "Concurrent label"


def test_moved_date_correction_reconciles_each_asof(ledger):
    s, batch, _ = ledger
    original, revision = batch("2026-01-10", 100)
    batch("2026-01-10", 100, debit="b", credit="a", kind="reversal", reverses=original, revision=revision)
    batch("2026-02-10", 60, kind="replacement", replaces=original)
    assert tb(s).totals.debit.minor_units == 0
    feb = reports.trial_balance(reports.TrialBalanceInput(date_to="2026-02-28"), s)
    assert feb.totals.debit.minor_units == feb.totals.credit.minor_units == 60
    jan = gl(s)
    assert jan.totals.period_debits.minor_units == jan.totals.period_credits.minor_units == 200
    assert all(r.signed_balance.minor_units == 0 for r in jan.rows if r.kind == "closing")


def test_registered_contract():
    from bookflow.commands import report_cmds  # noqa: F401
    from bookflow.core.registry import REGISTRY
    for name, input_model, output_model in (
        ("trial-balance", reports.TrialBalanceInput, reports.TrialBalanceOutput),
        ("general-ledger", reports.GeneralLedgerInput, reports.GeneralLedgerOutput),
    ):
        cmd = REGISTRY[f"report {name}"]
        assert cmd.scope == "company" and cmd.kind == "read"
        assert cmd.required_role == "member" and cmd.capability == "reports"
        assert cmd.input_model is input_model and cmd.output_model is output_model


def test_reports_through_journal_commands_and_real_migrated_company(tmp_path, monkeypatch):
    """Real migrations and command dispatch, independent of demo financial history."""
    import bookflow
    root = tmp_path / "report-root"
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(root))
    monkeypatch.delenv("BOOKFLOW_COMPANY", raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.organization.new(name="Report organization")
    company = client.company.new(legal_name="Report integration", home_currency="USD",
        organization="Report organization", timezone="UTC", chart="general")["company_id"]
    accounts = client.account.query(company=company, limit=200)["items"]
    a = next(row["id"] for row in accounts if row["type"] == "bank")
    b = next(row["id"] for row in accounts if row["type"] == "equity")
    def lines(amount):
        return [{"account": a, "side": "debit", "amount": amount}, {"account": b, "side": "credit", "amount": amount}]
    posted = client.journal.post(date="2026-01-10", lines=lines("1.00"), company=company)
    corrected = client.journal.update(journal=posted["id"], expected_version=posted["version"],
        date="2026-02-10", lines=lines("0.60"), company=company)
    assert client.report.trial_balance(date_to="2026-01-31", company=company)["totals"]["debit"]["minor_units"] == 0
    assert client.report.trial_balance(date_to="2026-02-28", company=company)["totals"]["debit"]["minor_units"] == 60
    before = client.report.general_ledger(date_from="2026-01-01", date_to="2026-02-28", company=company)
    kinds = {row["batch_kind"] for row in before["rows"] if row["kind"] == "posting"}
    assert kinds == {"original", "reversal", "replacement"}
    client.journal.void(journal=posted["id"], expected_version=corrected["version"], reason="Cancel entry", company=company)
    assert client.report.trial_balance(date_to="2026-02-28", company=company)["totals"]["debit"]["minor_units"] == 0
    after = client.report.general_ledger(date_from="2026-01-01", date_to="2026-02-28", company=company)
    assert len([r for r in after["rows"] if r["kind"] == "posting"]) == 8
    assert all(r["signed_balance"]["minor_units"] == 0 for r in after["rows"] if r["kind"] == "closing")


def test_undisplayed_account_changes_do_not_invalidate_gl(ledger):
    s, batch, _ = ledger
    batch("2026-01-10", 8)
    first = gl(s, limit=1)
    s.company.raw.execute("UPDATE accounts SET full_name='unused renamed',version=2 WHERE id='z'")
    second = gl(s, limit=1, cursor=first.next_cursor)
    assert second.metadata == first.metadata
    # Notes and other non-displayed master facts do not alter report content.
    s.company.raw.execute("UPDATE accounts SET note='internal note',version=2 WHERE id='a'")
    assert gl(s, limit=1, cursor=second.next_cursor).metadata == first.metadata


# A chart whose creation order, account-number order and name order all
# disagree, which is the only kind that can tell the three apart.  Ids are
# inserted k1..k4 in that sequence, so ordering by the stored record id is
# ordering by creation.
NUMBERED_ACCOUNTS = (
    ("k1", "Product Sales", "4200", "income"),
    ("k2", "Zulu Bank Checking", "1010", "bank"),
    ("k3", "Service Income", "4000", "income"),
    ("k4", "Accounts Receivable", "1100", "accounts_receivable"),
)
BY_NUMBER = ["k2", "k4", "k3", "k1"]    # 1010, 1100, 4000, 4200
BY_CREATION = ["k1", "k2", "k3", "k4"]
BY_NAME = ["k4", "k1", "k3", "k2"]


@pytest.fixture
def numbered_ledger(ledger):
    s, batch, oracle = ledger
    for ident, name, number, kind in NUMBERED_ACCOUNTS:
        insert(s.company, schema.accounts, id=ident, name=name, name_key=name.lower(),
               full_name=name, full_name_key=name.lower(), path=ident, depth=1, type=kind,
               number=number, number_key=number, currency="USD", active=True)
    batch("2026-01-10", 100, debit="k2", credit="k4")
    batch("2026-01-11", 50, debit="k1", credit="k3")
    return s, batch, oracle


def test_trial_balance_and_ledger_order_by_account_number_then_name(numbered_ledger):
    s, _, _ = numbered_ledger
    assert BY_NUMBER != BY_CREATION != BY_NAME != BY_NUMBER
    balance, rows = all_pages(lambda **kw: tb(s, **kw), limit=1)
    assert [r.account_id for r in rows] == BY_NUMBER
    assert [r.current_account_number for r in rows] == ["1010", "1100", "4000", "4200"]
    # Presentation only: the same accounts, the same nets, the same totals.
    assert {r.account_id: r.signed_net.minor_units for r in rows} == {"k1": 50, "k2": 100, "k3": -50, "k4": -100}
    assert (balance.totals.debit.minor_units, balance.totals.credit.minor_units) == (150, 150)
    assert tb(s).rows == rows

    _, ledger_rows = all_pages(lambda **kw: gl(s, **kw), limit=1)
    seen = [row.account_id for row in ledger_rows]
    assert [account for index, account in enumerate(seen) if index == 0 or seen[index-1] != account] == BY_NUMBER
    assert [r.kind for r in ledger_rows if r.account_id == "k2"] == ["opening", "posting", "closing"]
    assert gl(s).rows == ledger_rows[:len(gl(s).rows)]


def test_trial_balance_rows_carry_the_account_number(numbered_ledger):
    s, _, _ = numbered_ledger
    row = next(r for r in tb(s).rows if r.account_id == "k2")
    assert (row.current_account_label, row.current_account_name, row.current_account_number) == (
        "Zulu Bank Checking", "Zulu Bank Checking", "1010")
    assert row.display_account_label == "1010 · Zulu Bank Checking"
    s.company.raw.execute("UPDATE company_info SET use_account_numbers=0")
    plain = next(r for r in tb(s).rows if r.account_id == "k2")
    assert plain.display_account_label == "Zulu Bank Checking" and plain.current_account_number == "1010"


@pytest.mark.parametrize("change", ["number", "preference"])
def test_trial_balance_continuation_stales_on_number_and_label_preferences(ledger, change):
    s, batch, _ = ledger
    batch("2026-01-10", 1)
    first = tb(s, limit=1)
    if change == "number":
        s.company.raw.execute("UPDATE accounts SET number='7000',number_key='7000',version=2 WHERE id='a'")
    else:
        s.company.raw.execute("UPDATE company_info SET use_account_numbers=0")
    with pytest.raises(BookflowError) as caught:
        tb(s, limit=1, cursor=first.next_cursor)
    assert caught.value.code == "E_QUERY_STALE"
