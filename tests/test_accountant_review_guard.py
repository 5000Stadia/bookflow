"""R121: the membership guard reads the hub's mirror of external-accountant designations (hub0016).

It never takes an unreadable company for one without an accountant; it stays fast over an organization of
200 companies; `accountant add` and `remove` keep hub and company in step; and a designated accountant holds
standard access with no extra capabilities.
"""
import os
import sqlite3
import time

import pytest

from bookflow import BookflowError
from bookflow.core.ids import new_id
from tests.conftest import add_membership, make_actor
from tests.test_service_sales_lifecycle import COMPANY

pytestmark = pytest.mark.timeout(900)
THROUGH = '2026-09-30'


def refused(fn, code):
    with pytest.raises(BookflowError) as e:
        fn()
    assert e.value.code == code, (e.value.code, e.value.message)
    return e.value


def grant(client, user, role, **scope):
    return client.run('membership grant', {'user': user, 'role': role, **scope}, reason='access')


def hub_rows(root, company):
    with sqlite3.connect(root / 'hub.db') as db:
        rows = db.execute('SELECT user_id, through_date FROM company_accountants WHERE company_id=?', (company,)).fetchall()
        state = db.execute('SELECT state FROM company_accountant_sync WHERE company_id=?', (company,)).fetchone()
    return set(rows), state and state[0]


def company_db(root):
    return next(p for p in root.rglob('company.db') if p.parent.name == COMPANY)


@pytest.fixture
def designated(root, client):
    company = client.run('company show', {}, company=COMPANY)['company_id']
    cpa = make_actor(root, 'outside-cpa', company_role=(company, 'standard'))
    client.run('accountant add', {'user': 'outside-cpa', 'through': THROUGH}, company=COMPANY, reason='Q3 review')
    return company, cpa


def test_add_and_remove_keep_the_hub_in_step(root, client, designated):
    company, cpa = designated
    assert hub_rows(root, company) == ({(cpa, THROUGH)}, 'verified')
    client.run('accountant add', {'user': 'outside-cpa', 'through': '2026-10-31'}, company=COMPANY, reason='longer')
    assert hub_rows(root, company) == ({(cpa, '2026-10-31')}, 'verified')
    client.run('accountant remove', {'user': 'outside-cpa'}, company=COMPANY, reason='done')
    assert hub_rows(root, company) == (set(), 'verified')
    # A drifted mirror converges on the company's next write, from the company's books.
    with sqlite3.connect(root / 'hub.db') as db:
        db.execute("INSERT INTO company_accountants VALUES (?, 'U-STALE', '2026-01-01', 'now')", (company,))
    client.run('customer create', {'name': 'Any write'}, company=COMPANY, reason='write')
    assert hub_rows(root, company) == (set(), 'verified')


def test_an_unreadable_or_locked_company_refuses_access_changes(root, client, designated):
    company, cpa = designated
    db = company_db(root)
    # The mirror says designated, whatever state the books are in: the raise is refused from the hub alone.
    os.chmod(db, 0)
    try:
        assert refused(lambda: grant(client, 'outside-cpa', 'admin', company=company), 'E_VALIDATION').details['reason'] == 'external_accountant'
    finally:
        os.chmod(db, 0o644)
    hold = sqlite3.connect(str(db), timeout=0)
    hold.execute('PRAGMA locking_mode=EXCLUSIVE')
    hold.execute('BEGIN EXCLUSIVE')
    try:
        started = time.perf_counter()
        refused(lambda: grant(client, 'outside-cpa', 'admin', company=company), 'E_VALIDATION')
        assert time.perf_counter() - started < 2
    finally:
        hold.rollback()
        hold.close()
    # Books the hub cannot vouch for (never compared, now unreadable): refused, never read as "no accountant".
    make_actor(root, 'friend')
    with sqlite3.connect(root / 'hub.db') as hub:
        hub.execute('DELETE FROM company_accountant_sync WHERE company_id=?', (company,))
        hub.execute('DELETE FROM company_accountants WHERE company_id=?', (company,))
    os.chmod(db, 0)
    try:
        assert refused(lambda: grant(client, 'friend', 'standard', company=company),
                       'E_PERMISSION').details['reason'] == 'accountants_unverified'
    finally:
        os.chmod(db, 0o644)
    # Readable again, the books are read directly and the designation found.
    refused(lambda: grant(client, 'outside-cpa', 'admin', company=company), 'E_VALIDATION')
    client.run('customer create', {'name': 'Any write'}, company=COMPANY, reason='write')
    assert hub_rows(root, company) == ({(cpa, THROUGH)}, 'verified')


def test_an_organization_of_200_companies_is_checked_in_well_under_a_second(root, client, designated):
    company, cpa = designated
    organization = client.run('company show', {}, company=COMPANY)['organization_id']
    make_actor(root, 'friend')
    with sqlite3.connect(root / 'hub.db') as hub:
        columns = [r[1] for r in hub.execute('PRAGMA table_info(companies)')]
        template = dict(zip(columns, hub.execute('SELECT * FROM companies WHERE id=?', (company,)).fetchone()))
        owner = hub.execute("SELECT user_id FROM memberships WHERE scope_id=? AND role='owner'", (company,)).fetchone()[0]
        for i in range(200):
            row = dict(template, id=new_id(), display_name=f'Branch {i}', name_key=f'branch {i}', path=f'nowhere/branch-{i}')
            hub.execute(f"INSERT INTO companies ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
            hub.execute("INSERT INTO company_accountant_sync VALUES (?, 'verified', 'now', NULL)", (row['id'],))
    add_membership(root, owner, 'organization', organization, 'owner')
    # The guard runs before the grant is planned, so a refusal times the guard alone. (An allowed organization grant
    # over 200 companies spends tens of seconds in the existing permission reconciliation, not here.)
    started = time.perf_counter()
    refused(lambda: grant(client, 'outside-cpa', 'admin', organization=organization), 'E_VALIDATION')
    assert time.perf_counter() - started < 1.0
    # One branch the hub cannot vouch for, among the 200: refused, and still quick (that one is tried, without waiting).
    with sqlite3.connect(root / 'hub.db') as hub:
        hub.execute("UPDATE company_accountant_sync SET state='unverified', problem='unreadable' WHERE company_id IN "
                    "(SELECT id FROM companies WHERE display_name='Branch 7')")
    started = time.perf_counter()
    e = refused(lambda: grant(client, 'friend', 'standard', organization=organization), 'E_PERMISSION')
    assert e.details['reason'] == 'accountants_unverified' and e.details['companies'] == ['Branch 7']
    assert time.perf_counter() - started < 1.0


def test_the_accountant_holds_standard_with_no_extra_capabilities(root, client):
    company = client.run('company show', {}, company=COMPANY)['company_id']
    extra = make_actor(root, 'extra-cpa')
    membership = add_membership(root, extra, 'company', company, 'standard')
    with sqlite3.connect(root / 'hub.db') as hub:
        hub.execute("UPDATE memberships SET grants='[\"undo\"]' WHERE id=?", (membership,))
    e = refused(lambda: client.run('accountant add', {'user': 'extra-cpa', 'through': THROUGH}, company=COMPANY, reason='x'),
                'E_VALIDATION')
    assert e.details['reason'] == 'extra_grants' and 'undo' in e.message
