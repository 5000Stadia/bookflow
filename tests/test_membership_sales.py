"""R188 increment 2: one member price on invoices, sales receipts and POS sales, for jobs too, and a reversible
bulk enrollment.

The money, by hand. "Member widget" sells at 19.99 and is not taxable. The member level "Member 12.5" takes
12.5% off: 19.99 x 0.875 = 17.49125, to the cent 17.49 (not a half, so every nearest rule agrees).
Three widgets: 3 x 17.49 = 52.47 for a member (5247 cents), 3 x 19.99 = 59.97 otherwise (5997 cents).
The difference, 59.97 - 52.47 = 7.50, is exactly 3 x 2.50 (19.99 - 17.49).

Enrollments start on 2020-01-01 and stay open, so they cover whatever day a POS sale is rung up on.
"""
from pathlib import Path
import sqlite3

import anyio
import pytest

from tests.test_pos_sale import Company

SURFACES = ('python', 'cli', 'http', 'mcp')
START = '2020-01-01'
MEMBER, ORDINARY = 5247, 5997
DAY = '2026-11-15'  # inside the demo's own fourth-quarter enrollments


def setup(root):
    co = Company(root)
    info = co.run('company show')
    co.run('company update', {'enable_price_levels': True, 'membership_pricing_enabled': True, 'pos_enabled': True,
                              'expected_version': info['info_version']})
    income = next(a['id'] for a in co.run('account list')['items'] if a['type'] == 'income')
    code = next(x['id'] for x in co.run('sales-tax-code list')['items'] if not x['taxable'])
    co.widget = co.run('item create', {'name': 'Member widget', 'type': 'service', 'sales_enabled': True,
                                       'description': 'Member widget', 'income_account_id': income, 'price': '19.99',
                                       'sales_tax_code_id': code})['id']
    co.run('price-level create', {'name': 'Member 12.5', 'kind': 'fixed_percent', 'percent': '-12.5'})
    co.plan = co.run('membership-plan create', {'name': 'Trade 12.5', 'price_level': 'Member 12.5'})['id']
    co.parent = co.run('customer create', {'name': 'Sales Member Co'})['id']
    co.job = co.run('customer create', {'name': 'Fit-out', 'parent_id': co.parent})['id']
    co.subjob = co.run('customer create', {'name': 'Fit-out phase two', 'parent_id': co.job})['id']
    co.own = co.run('customer create', {'name': 'Own prices only', 'parent_id': co.parent})['id']
    return co


def enroll(co, customer, start=START, end=None):
    body = {'customer': customer, 'plan': co.plan, 'start_date': start}
    if end:
        body['end_date'] = end
    return co.run('customer membership set', body)['membership']


def lines(co):
    return [{'item': co.widget, 'quantity': '3'}]


def receipt(co, customer, **kw):
    return co.run('sales-receipt post', {'customer': customer, 'lines': lines(co), 'deposit_to': 'Undeposited Funds',
                                         'payment_method': 'Cash', **kw})


def test_a_job_inherits_its_ancestors_membership_unless_it_suppresses_it(root):
    co = setup(root)
    owner = enroll(co, co.parent)
    shown = co.run('customer membership inherit', {'customer': co.own, 'inherit': False})
    assert shown['inherit'] is False and shown['version'] == 1 and shown['changed']
    today = co.run('customer membership show', {'customer': co.subjob})
    # The grandchild climbs two levels; the owner is the top customer, not the job between.
    assert today['effective']['id'] == owner['id'] and today['effective_owner']['id'] == co.parent
    assert today['inherits'] is True and today['inheritance_version'] is None
    assert co.run('customer membership show', {'customer': co.own})['effective'] is None
    sub = receipt(co, co.subjob)
    profile = sub['revision']['profile']
    assert sub['total_minor_units'] == MEMBER and sub['revision']['lines'][0]['unit_price']['amount'] == '17.49'
    # The sale captures sold-to (the grandchild) and the entitlement owner (the parent) separately.
    assert profile['customer']['id'] == co.subjob and profile['membership']['owner']['id'] == co.parent
    assert profile['origins']['price_level'] == {'kind': 'default', 'source_id': owner['id']}
    assert receipt(co, co.own)['total_minor_units'] == ORDINARY
    # A job's own enrollment wins over the inherited one, and its owner is itself.
    level = co.run('price-level create', {'name': 'Job 20', 'kind': 'fixed_percent', 'percent': '-20'})['id']
    other = co.run('membership-plan create', {'name': 'Job plan', 'price_level': level})['id']
    mine = co.run('customer membership set', {'customer': co.job, 'plan': other, 'start_date': START})['membership']
    # 19.99 x 0.8 = 15.992 -> 15.99; x 3 = 47.97.
    job_sale = receipt(co, co.job)
    assert job_sale['total_minor_units'] == 4797
    captured = job_sale['revision']['profile']['membership']
    assert captured['enrollment_id'] == mine['id'] and captured['owner']['id'] == co.job
    assert receipt(co, co.subjob)['total_minor_units'] == 4797
    # Suppression stops the climb at the job that suppresses, and is versioned.
    off = co.run('customer membership inherit', {'customer': co.subjob, 'inherit': False})
    assert receipt(co, co.subjob)['total_minor_units'] == ORDINARY
    stale = co.fails('customer membership inherit', {'customer': co.subjob, 'inherit': True, 'expected_version': 9})
    assert stale.code == 'E_VERSION_CONFLICT'
    co.run('customer membership inherit', {'customer': co.subjob, 'inherit': True, 'expected_version': off['version']})
    assert co.fails('customer membership inherit', {'customer': co.parent, 'inherit': False}).code == 'E_VALIDATION'
    # The list names who is priced today and through whom.
    rows = {r['customer']['id']: r for r in co.run('customer membership list', {'plan': co.plan, 'as_of': DAY})['items']}
    assert set(rows) == {co.parent}  # the job has its own plan now, the own-only job has none
    every = {r['customer']['id']: r for r in co.run('customer membership list', {'as_of': DAY})['items']}
    assert every[co.subjob]['owner']['id'] == co.job and every[co.subjob]['inherited']
    assert not every[co.job]['inherited'] and co.own not in every
    own_only = co.run('customer membership list', {'include_inherited': False, 'as_of': DAY})['items']
    mine = {co.parent, co.job, co.subjob, co.own}
    assert {r['customer']['id'] for r in own_only} & mine == {co.parent, co.job}
    # The demo's own members (enrolled for 2026's fourth quarter) are listed too.
    demo = {r['customer']['label']: r for r in own_only if r['customer']['id'] not in mine}
    assert set(demo) == {'Membership Example Customer', 'Trade Counter North', 'Trade Counter South'}


def test_a_held_ticket_is_stale_after_a_renewal_or_expiry_even_at_the_same_total(root):
    co = setup(root)
    first = enroll(co, co.parent)
    held = co.run('pos ticket hold', {'ticket': 'member-cart', 'customer': co.job, 'lines': lines(co)})
    priced = co.run('pos sale preview', {'customer': co.job, 'lines': lines(co)})
    assert priced['total']['amount'] == '52.47' and priced['membership']['owner']['id'] == co.parent
    ticket = co.run('pos ticket resume', {'ticket': held['id'], 'expected_version': held['version']})
    cart = dict(pos_register=ticket['pos_register']['id'], customer=co.job, lines=ticket['lines'], ticket=ticket['id'],
                ticket_version=ticket['version'], tender={'payment_method': 'Cash'})
    # Renewal: the old enrollment ends, a new one starts the next day at the same level, so the total is the same.
    co.run('customer membership end', {'membership': first['id'], 'expected_version': 1, 'end_date': START})
    renewed = enroll(co, co.parent, start='2020-01-02')
    again = co.run('pos sale preview', {'customer': co.job, 'lines': lines(co)})
    assert again['total']['amount'] == '52.47' and again['membership']['token'] != priced['membership']['token']
    stale = co.fails('pos sale complete', dict(cart, expected_total='52.47'))
    assert stale.code == 'E_PREVIEW_STALE' and stale.details['expected_membership'] == priced['membership']['token']
    # Expiry: the renewal ends too, so no membership applies and the price is ordinary.
    co.run('customer membership end', {'membership': renewed['id'], 'expected_version': 1, 'end_date': '2020-01-02'})
    expired = co.fails('pos sale complete', dict(cart, expected_total='52.47'))
    assert expired.code == 'E_PREVIEW_STALE' and expired.details['membership'] is None
    assert co.run('pos ticket list')['items'][0]['status'] == 'open'
    # The clerk previews again and charges the new total; that sale posts at ordinary prices.
    fresh = co.run('pos sale preview', {'customer': co.job, 'lines': lines(co)})
    assert fresh['total']['amount'] == '59.97' and fresh.get('membership') is None
    sold = co.run('pos sale complete', dict(cart, expected_total='59.97', expected_membership='none'))
    assert sold['total']['amount'] == '59.97'
    shown = co.run('sales-receipt show', {'sales_receipt': sold['sales_receipt']['id']})
    assert shown['total_minor_units'] == ORDINARY and 'membership' not in shown['revision']['profile']


def test_merging_two_customers_with_overlapping_enrollments_is_refused(root):
    co = setup(root)
    a = co.run('customer create', {'name': 'Merge member A'})['id']
    b = co.run('customer create', {'name': 'Merge member B'})['id']
    enroll(co, a, start='2020-01-01', end='2020-12-31')
    clash = enroll(co, b, start='2020-06-01', end='2021-05-31')
    refused = co.fails('customer merge', {'merged': b, 'into': a})
    assert refused.code == 'E_MERGE_REFUSED' and refused.details['problem'] == 'membership_overlap'
    assert refused.details['membership'] == clash['id']
    co.run('customer membership end', {'membership': clash['id'], 'expected_version': 1, 'end_date': '2020-06-01'})
    # Still overlapping on 2020-06-01 alone: inclusive dates.
    assert co.fails('customer merge', {'merged': b, 'into': a}).code == 'E_MERGE_REFUSED'
    later = co.run('customer create', {'name': 'Merge member C'})['id']
    kept = enroll(co, later, start='2021-01-01')
    co.run('customer merge', {'merged': later, 'into': a})
    # The survivor is priced by the merged customer's enrollment, and is still one entitlement identity:
    # a new enrollment for it that meets the merged one is refused.
    shown = co.run('customer membership show', {'customer': a})
    assert shown['effective']['id'] == kept['id']
    assert receipt(co, a)['revision']['profile']['membership']['enrollment_id'] == kept['id']
    assert co.fails('customer membership set', {'customer': a, 'plan': co.plan,
                                                'start_date': '2022-01-01'}).code == 'E_MEMBERSHIP_OVERLAP'


def test_a_bulk_enrollment_reverses_as_one_unit_and_a_later_sale_blocks_it_by_name(root):
    co = setup(root)
    names = [co.run('customer create', {'name': f'Bulk member {n}'})['id'] for n in (1, 2, 3)]
    trial = co.run('customer membership enroll', {'customers': names, 'plan': co.plan, 'start_date': START},
                   dry_run=True)
    assert trial['batch'] is None and trial['count'] == 3
    assert co.run('customer membership list', {'plan': co.plan})['items'] == []
    # All or none: one customer already enrolled refuses the whole batch.
    enroll(co, names[2])
    refused = co.fails('customer membership enroll', {'customers': names, 'plan': co.plan, 'start_date': START})
    assert refused.code == 'E_MEMBERSHIP_OVERLAP'
    assert {r['customer']['id'] for r in co.run('customer membership list', {'plan': co.plan})['items']} == {names[2]}
    done = co.run('customer membership enroll', {'customers': names[:2], 'plan': co.plan, 'start_date': START})
    assert done['count'] == 2 and done['batch']
    batch = co.run('batch show', {'batch': done['batch']})
    assert batch['batch']['kind'] == 'membership-enroll'
    sale = receipt(co, names[0], number='MEMBER-SR-1')
    assert sale['total_minor_units'] == MEMBER
    blocked = co.fails('batch reverse', {'batch': done['batch']})
    assert blocked.code == 'E_BATCH_BLOCKED' and 'sales receipt MEMBER-SR-1' in blocked.message
    assert blocked.details['blockers'][0]['code'] == 'in_use'
    assert len(co.run('customer membership list', {'plan': co.plan})['items']) == 3
    # Without a dependent sale the batch reverses whole: both enrollments go, and the price is ordinary again.
    fresh = co.run('customer membership enroll', {'customers': [co.run('customer create', {'name': 'Bulk member 4'})['id'],
                                                                co.run('customer create', {'name': 'Bulk member 5'})['id']],
                                                  'plan': co.plan, 'start_date': START})
    reversed_ = co.run('batch reverse', {'batch': fresh['batch']})
    assert reversed_['reversed_now'] == 2 and reversed_['remaining'] == 0
    left = {r['customer']['label'] for r in co.run('customer membership list', {'plan': co.plan})['items']}
    assert left == {'Bulk member 1', 'Bulk member 2', 'Bulk member 3'}
    assert receipt(co, 'Bulk member 4')['total_minor_units'] == ORDINARY
    # Each enrollment's deletion is in the audit trail under batch reverse.
    events = co.run('audit list', {'record_type': 'customer_membership', 'record_id': fresh['enrolled'][0]['id']})['items']
    assert sorted(e['command'] for e in events) == ['batch reverse', 'customer membership enroll']


def test_old_companies_migrate_intact_with_the_layer_off(root, monkeypatch):
    from bookflow.storage.engine import open_database
    from bookflow.storage.migrate import HEADS, migrate_to_head
    co = Company(root)
    head = HEADS['company']
    path = Path(co.run('company show')['path']) / 'company.db'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=OFF')
        db.execute('DROP TABLE customer_membership_inheritance')
        db.execute('ALTER TABLE pos_tickets DROP COLUMN membership_token')
        db.execute("UPDATE alembic_version SET version_num='co0137'")
        before = {name: db.execute('SELECT * FROM ' + name).fetchall()
                  for name in ('customers', 'customer_memberships', 'pos_tickets')}
    # Only this migration is rewound, so only it is run; the file is stamped back to its real head afterwards.
    with monkeypatch.context() as patch:
        patch.setitem(HEADS, 'company', 'co0138')
        with open_database(path, writable=True) as db:
            assert migrate_to_head(db, 'company', None) == ('co0137', 'co0138')
            for name, rows in before.items():
                assert [r[:len(rows[0])] if rows else r for r in db.raw.execute('SELECT * FROM ' + name).fetchall()] == rows
            assert db.raw.execute('SELECT count(*) FROM customer_membership_inheritance').fetchone() == (0,)
            assert db.raw.execute('SELECT count(*) FROM pos_tickets WHERE membership_token IS NOT NULL').fetchone() == (0,)
            assert db.raw.execute('SELECT membership_pricing_enabled FROM company_info').fetchall() == [(0,)]
            assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
    with sqlite3.connect(path) as db:
        db.execute('UPDATE alembic_version SET version_num=?', (head,))
    assert co.run('customer membership list')['layer_enabled'] is False


@pytest.mark.parametrize('surface', SURFACES)
def test_member_price_is_one_price_on_every_sale_and_surface(root, tmp_path, surface):
    """A person (Python, CLI, browser HTTP) and an agent (MCP) see 52.47 for the same job, item and day on an
    invoice, a sales receipt and a POS sale; the three new commands answer the same on each surface."""
    from tests.mcp_matrix_support import Matrix
    co = setup(root)
    enroll(co, co.parent)
    co.run('account create', {'name': 'Member receivable', 'type': 'accounts_receivable'})
    register = co.run('pos sale preview', {'lines': lines(co)})['pos_register']['id']

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path / 'surfaces')
            call = lambda name, values, **kw: matrix.call(surface, name, values, **kw)  # noqa: E731
            job, item = co.job, co.widget
            sold = [{'item': item, 'quantity': '3'}]
            invoice = await call('invoice post', {'customer': job, 'lines': sold, 'ar_account': 'Member receivable'})
            sale = await call('sales-receipt post', {'customer': job, 'lines': sold, 'deposit_to': 'Undeposited Funds',
                                                     'payment_method': 'Cash'})
            till = await call('pos sale preview', {'pos_register': register, 'customer': job, 'lines': sold})
            assert till['total']['amount'] == '52.47' and till['membership']['plan'] == 'Trade 12.5'
            rung = await call('pos sale complete', {'pos_register': register, 'customer': job, 'lines': sold,
                                                    'ticket': f'surface-{surface}', 'expected_total': '52.47',
                                                    'expected_membership': till['membership']['token'],
                                                    'tender': {'payment_method': 'Cash'}})
            assert rung['total']['amount'] == '52.47'
            for document in (invoice, sale):
                assert document['total_minor_units'] == MEMBER
                assert document['revision']['lines'][0]['unit_price']['amount'] == '17.49'
                assert document['revision']['profile']['membership']['owner']['id'] == co.parent
            # Suppression through this surface makes the job's next sale ordinary.
            off = await call('customer membership inherit', {'customer': job, 'inherit': False})
            assert off['inherit'] is False and off['version'] == 1
            plain = await call('pos sale preview', {'pos_register': register, 'customer': job, 'lines': sold})
            assert plain['total']['amount'] == '59.97' and plain.get('membership') is None
            listed = await call('customer membership list', {'plan': co.plan})
            # The job stopped inheriting, so its own job does not climb past it; its sibling still inherits.
            assert {r['customer']['id']: r['inherited'] for r in listed['items']} == {co.parent: False, co.own: True}
            assert listed['layer_enabled']
            other = await call('customer create', {'name': f'Surface member {surface}'})
            made = await call('customer membership enroll', {'customers': [other['id']], 'plan': co.plan,
                                                             'start_date': START})
            assert made['count'] == 1
            overlap = await call('customer membership enroll', {'customers': [other['id']], 'plan': co.plan,
                                                                'start_date': START}, rejected=True)
            assert overlap['code'] == 'E_MEMBERSHIP_OVERLAP'
            undone = await call('batch reverse', {'batch': made['batch']})
            assert undone['reversed_now'] == 1
            assert (await call('customer membership show', {'customer': other['id']}))['history'] == []
        finally:
            await matrix.close()
    anyio.run(witness)
