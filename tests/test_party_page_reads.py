"""R180: a list projects its parties through one PartyPage, and every record reads exactly as alone.

The page reads each kind of fact (custom values, contacts and addresses, links, balances, item
profiles, labels, ancestors) once for the whole list. Projected with or without it, every
customer, vendor, employee and other name must come out identical, including after a merge and
for a page that leaves records out (ancestors then fall back to one-record reads).
"""
import sqlalchemy as sa

from bookflow.company import parties, schema
from bookflow.storage.engine import open_database

COMPANY = "Demo Plumbing Co"


def _db_path(root):
    return root / "organizations" / "Demo Holdings LLC" / COMPANY / "company.db"


def _same_with_and_without_page(root, nouns=("customer", "vendor", "employee", "other-name")):
    with open_database(_db_path(root), False) as db:
        checked = 0
        for noun in nouns:
            rows = [dict(r) for r in db.conn.execute(sa.select(parties.TABLES[noun])).mappings()]
            for subset in (rows, rows[1::2]):  # the whole list, and a page that leaves records out
                page = parties.PartyPage(db, noun, subset)
                for row in subset:
                    alone = parties.project_party_record(db, noun, row, reveal_tax_suffix=True)
                    paged = parties.project_party_record(db, noun, row, reveal_tax_suffix=True, page=page)
                    assert paged == alone, (noun, row["id"])
                    checked += 1
        return checked


def test_every_party_projects_the_same_through_a_page(root):
    assert _same_with_and_without_page(root) > 20


def test_after_a_merge_and_with_jobs_balances_and_links_still_agree(client, root):
    client.run("customer merge", {"merged": "Line Kinds Example Customer", "into": "Commercial Example Customer"},
               company=COMPANY, reason="same customer entered twice")
    with open_database(_db_path(root), False) as db:
        assert db.conn.execute(sa.select(sa.func.count()).select_from(schema.customers)
                               .where(schema.customers.c.parent_id.is_not(None))).scalar() > 0
    assert _same_with_and_without_page(root, ("customer", "vendor")) > 10


def test_the_list_reads_a_bounded_number_of_statements(client, root):
    from bookflow.core import timings
    from bookflow.perf import store
    import sqlite3
    client.run("customer list", {"include_inactive": True}, company=COMPANY)
    timings.flush()
    db = sqlite3.connect(store.path(root))
    try:
        (count,) = db.execute("SELECT sql_count FROM calls WHERE command='customer list' ORDER BY rowid DESC LIMIT 1").fetchone()
    finally:
        db.close()
    assert count < 80  # was about 14 a customer; now a fixed handful per list
