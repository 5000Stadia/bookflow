"""The hub's mirror of each company's external-accountant designations (R121, hub0016).

A designation lives in the company's books (``accountant_designations``), where the company's own rules read
it. The hub membership commands must hold a designated accountant to standard access without opening every
company of an organization, so the hub keeps ``company_accountants`` and ``company_accountant_sync``:

* ``accountant add`` writes the hub row in the same operation, the hub committing first; ``accountant remove``
  commits the company first. Either way a failure part-way leaves the hub saying "designated" -- the safe side.
* Every real company write converges the mirror (``converge``, called from dispatch's projection repair, the
  same place the hub's copy of company_info is repaired), so drift lasts at most until the next write there.
* Registering a company (new, attach, restore) reads its books; restoring books that designate someone mirrors
  them at once.
* A company whose books could not be read is ``unverified``. ``scope_check`` then reads it again, read-only and
  without waiting; if that fails too, membership changes there are refused. It never takes "unreadable" for
  "no accountant".
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import sqlalchemy as sa

from bookflow.core.errors import BookflowError
from bookflow.hub import schema as h


def _now():
    from bookflow.core.clock import now_iso
    return now_iso()


def read_books(path: Path) -> list[tuple[str, str, str]]:
    """[(user_id, through_date, updated_at)] of a company's active designations, read-only; raises if unreadable."""
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=0.05)) as db:
        if not db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='accountant_designations'").fetchone():
            if not db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='company_info'").fetchone():
                raise sqlite3.DatabaseError('not a company database')
            return []
        return [tuple(r) for r in db.execute('SELECT user_id, through_date, updated_at FROM accountant_designations WHERE active=1')]


def _mirrored(conn, company_id):
    rows = conn.execute(sa.select(h.company_accountants.c.user_id, h.company_accountants.c.through_date).where(
        h.company_accountants.c.company_id == company_id)).all()
    state = conn.execute(sa.select(h.company_accountant_sync.c.state).where(
        h.company_accountant_sync.c.company_id == company_id)).scalar()
    return {(r[0], r[1]) for r in rows}, state


def record(conn, company_id, rows, problem=None) -> bool:
    """Make the hub's rows for this company these designations (or mark it unverified). True when anything changed."""
    t, sync = h.company_accountants, h.company_accountant_sync
    have, state = _mirrored(conn, company_id)
    want = None if problem else {(u, d) for u, d, _ in rows}
    if problem is None and have == want and state == 'verified':
        return False
    if problem is not None and state == 'unverified':
        return False
    at = _now()
    if problem is None:
        conn.execute(t.delete().where(t.c.company_id == company_id))
        for user_id, through, when in rows:
            conn.execute(t.insert().values(company_id=company_id, user_id=user_id, through_date=through, designated_at=when or at))
    conn.execute(sync.delete().where(sync.c.company_id == company_id))
    conn.execute(sync.insert().values(company_id=company_id, state='unverified' if problem else 'verified', checked_at=at,
                                      problem=problem[:200] if problem else None))
    return True


def _touched(company_id, rows, problem=None):
    from bookflow.core.registry import Touched
    snapshot = {'company_id': company_id, 'accountants': sorted(u for u, _, _ in rows) if rows else [], 'problem': problem}
    return Touched('company_accountants', company_id, 'update', None, None, snapshot, db='hub')


def converge(s) -> list:
    """Inside a real company write: bring the hub mirror level with the open company's books."""
    if s.company is None or s.company_row is None or s.hub is None or not s.hub.writable:
        return []
    try:
        rows = [tuple(r) for r in s.company.conn.execute(sa.text(
            'SELECT user_id, through_date, updated_at FROM accountant_designations WHERE active=1')).all()]
    except sa.exc.OperationalError:
        return []  # books from before co0088
    company_id = s.company_row['id']
    have, state = _mirrored(s.hub.conn, company_id)
    if have == {(u, d) for u, d, _ in rows} and state == 'verified':
        return []
    if not s.hub.write_transaction:
        s.hub.raw.execute('BEGIN IMMEDIATE')
    record(s.hub.conn, company_id, rows)
    return [_touched(company_id, rows)]


def register(s, company_id: str, folder: Path) -> None:
    """A company just registered (new, attached, restored): mirror what its books say, inside the registering write."""
    try:
        rows, problem = read_books(Path(folder) / 'company.db'), None
    except (sqlite3.Error, OSError) as e:
        rows, problem = [], f'{type(e).__name__}: {e}'
    record(s.hub.conn, company_id, rows, problem)


def scope_check(s, user_ids, scope_type, scope_id):
    """(names of companies in the scope where any of user_ids is designated, names that cannot be verified)."""
    c = h.companies
    where = c.c.id == scope_id if scope_type == 'company' else c.c.organization_id == scope_id
    companies = {r[0]: (r[1], r[2]) for r in s.hub.conn.execute(sa.select(c.c.id, c.c.display_name, c.c.path).where(where))}
    if not companies:
        return [], []
    ids = sorted(companies)
    ids_json = json.dumps(ids)
    designated = set()
    users = [u for u in user_ids if u]
    if users:
        designated = {r[0] for r in s.hub.conn.execute(sa.text(
            'SELECT DISTINCT company_id FROM company_accountants WHERE company_id IN (SELECT value FROM json_each(:c))'
            ' AND user_id IN (SELECT value FROM json_each(:u))'), dict(c=ids_json, u=json.dumps(users)))}
    verified = {r[0] for r in s.hub.conn.execute(sa.text(
        "SELECT company_id FROM company_accountant_sync WHERE state='verified' AND company_id IN (SELECT value FROM json_each(:c))"),
        dict(c=ids_json))}
    unverified = []
    for company_id in ids:
        if company_id in verified:
            continue
        # Not vouched for: read the books now, without waiting on a lock; failing that, refuse.
        try:
            rows = read_books(s.abs_path(companies[company_id][1]) / 'company.db')
        except (sqlite3.Error, OSError):
            unverified.append(companies[company_id][0])
            continue
        if any(u in users for u, _, _ in rows):
            designated.add(company_id)
    return sorted(companies[i][0] for i in designated), unverified


def require_verified(s, scope_type, scope_id, command):
    _, unverified = scope_check(s, [], scope_type, scope_id)
    if unverified:
        raise BookflowError('E_PERMISSION', message=(
            f"Bookflow cannot confirm who is the external accountant of {', '.join(unverified)}: its books could not be read. "
            'Access there cannot change until they can be; open the company (any change to it compares them again).'),
            details={'reason': 'accountants_unverified', 'command': command, 'companies': unverified})
