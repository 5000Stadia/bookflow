"""R188 increment 1: a dated customer membership price on ordinary invoices.

The money, by hand. Widget sells at 19.99. The member level "Member 12.5" takes 12.5% off:
19.99 x 0.875 = 17.49125, to the cent 17.49 (not a half, so every nearest rule agrees).
Three widgets: 3 x 17.49 = 52.47 inside the enrollment, 3 x 19.99 = 59.97 outside it.
A rule 5% off: 19.99 x 0.95 = 18.9905 -> 18.99. A rule 15% off: 19.99 x 0.85 = 16.9915 -> 16.99.
A level "Other 20" 20% off: 19.99 x 0.8 = 15.992 -> 15.99.
"""
from pathlib import Path
import sqlite3

import anyio
import pytest

from bookflow import BookflowError

SURFACES = ('python', 'cli', 'http', 'mcp')


@pytest.fixture
def books(tmp_path, monkeypatch):
    from tests.crm_support import company
    books = company(tmp_path, monkeypatch, 'UTC')
    client, co = books.client, books.company
    income = client.account.create(name='Sales', type='income', company=co)['id']
    client.account.create(name='Receivable', type='accounts_receivable', company=co)
    code = next(x['id'] for x in books.run('sales-tax-code list')['items'] if not x['taxable'])
    books.widget = write(books, 'item create', {'name': 'Widget', 'type': 'service', 'sales_enabled': True,
                         'description': 'Widget', 'income_account_id': income, 'price': '19.99',
                         'sales_tax_code_id': code})['id']
    books.customer = write(books, 'customer create', {'name': 'Member Co'})['id']
    write(books, 'company update', {'enable_price_levels': True, 'membership_pricing_enabled': True})
    write(books, 'price-level create', {'name': 'Member 12.5', 'kind': 'fixed_percent', 'percent': '-12.5'})
    write(books, 'membership-plan create', {'name': 'Trade', 'price_level': 'Member 12.5'})
    return books


def write(books, command, values):
    return books.run(command, values, reason='Membership pricing test')


def enroll(books, start='2026-11-01', end='2026-11-30', **extra):
    return write(books, 'customer membership set', {'customer': books.customer, 'plan': 'Trade',
                                                    'start_date': start, 'end_date': end, **extra})['membership']


def invoice(books, date, **line):
    return write(books, 'invoice post', {'date': date, 'customer': books.customer,
                                         'lines': [{'item': books.widget, 'quantity': '3', **line}]})


def preview(books, date):
    return books.run('price-rule preview', {'item': books.widget, 'customer': books.customer, 'quantity': '3', 'date': date})


def code_of(call):
    with pytest.raises(BookflowError) as e:
        call()
    return e.value.code


def test_member_price_holds_on_both_boundary_days_and_not_outside(books):
    enrolled = enroll(books, member_number='M-1')
    for day, price in (('2026-10-31', '19.99'), ('2026-11-01', '17.49'), ('2026-11-30', '17.49'), ('2026-12-01', '19.99')):
        assert preview(books, day)['unit_price'] == price, day
    inside, outside = invoice(books, '2026-11-01'), invoice(books, '2026-12-01')
    assert (inside['total_minor_units'], outside['total_minor_units']) == (5247, 5997)
    assert inside['revision']['lines'][0]['unit_price']['amount'] == '17.49'
    profile = inside['revision']['profile']
    assert profile['price_level']['label'] == 'Member 12.5'
    assert profile['origins']['price_level'] == {'kind': 'default', 'source_id': enrolled['id']}
    assert profile['membership']['enrollment_id'] == enrolled['id'] and profile['membership']['plan']['label'] == 'Trade'
    assert 'membership' not in outside['revision']['profile'] and outside['revision']['profile']['price_level'] is None
    shown = preview(books, '2026-11-30')
    assert shown['note'] == 'member price from plan Trade (price level Member 12.5)' and shown['membership']['chosen']
    assert preview(books, '2026-12-01')['membership'] is None
    history = books.run('customer membership show', {'customer': 'Member Co', 'as_of': '2026-11-30'})
    assert history['effective']['id'] == enrolled['id'] and history['effective']['member_number'] == 'M-1'
    assert books.run('customer membership show', {'customer': 'Member Co', 'as_of': '2026-12-01'})['effective'] is None


def test_a_saved_invoice_keeps_its_prices_after_the_enrollment_ends_or_the_plan_changes(books):
    enrolled = enroll(books, end=None)
    posted = invoice(books, '2026-11-15')
    assert posted['total_minor_units'] == 5247
    write(books, 'price-level create', {'name': 'Other 20', 'kind': 'fixed_percent', 'percent': '-20'})
    plan = books.run('membership-plan show', {'plan': 'Trade'})
    write(books, 'membership-plan update', {'plan': 'Trade', 'expected_version': plan['version'], 'price_level': 'Other 20'})
    assert preview(books, '2026-11-15')['unit_price'] == '15.99'
    write(books, 'customer membership end', {'membership': enrolled['id'], 'expected_version': 1, 'end_date': '2026-11-10'})
    assert preview(books, '2026-11-15')['unit_price'] == '19.99'
    again = books.run('invoice show', {'invoice': posted['id']})
    assert again['total_minor_units'] == 5247 and again['revision']['profile']['membership']['enrollment_id'] == enrolled['id']
    line_id = posted['revision']['lines'][0]['line_id']
    memo = write(books, 'invoice update', {'invoice': posted['id'], 'expected_version': 1, 'memo': 'Same lines',
                                           'lines': [{'item': books.widget, 'line_id': line_id}]})
    assert memo['total_minor_units'] == 5247 and memo['revision']['profile']['price_level']['label'] == 'Member 12.5'
    ended = books.run('customer membership show', {'customer': books.customer, 'as_of': '2026-11-15'})
    assert ended['effective'] is None and ended['history'][0]['status'] == 'ended'


def test_an_explicit_unit_price_or_price_level_still_wins(books):
    enroll(books)
    write(books, 'price-level create', {'name': 'Other 20', 'kind': 'fixed_percent', 'percent': '-20'})
    assert invoice(books, '2026-11-15', unit_price='10.00')['total_minor_units'] == 3000
    assert invoice(books, '2026-11-15', price_level='Other 20')['total_minor_units'] == 4797
    picked = write(books, 'invoice post', {'date': '2026-11-15', 'customer': books.customer, 'price_level': 'Other 20',
                                           'lines': [{'item': books.widget, 'quantity': '3'}]})
    assert picked['total_minor_units'] == 4797 and 'membership' not in picked['revision']['profile']


def test_price_rules_compete_with_the_member_level_as_the_design_says(books):
    enroll(books)
    write(books, 'price-rule create', {'name': 'Five off', 'items': ['Widget'], 'value': '5', 'priority': 1})
    # Lowest price: the member level 17.49 beats the rule 18.99.
    chosen = preview(books, '2026-11-15')
    assert (chosen['unit_price'], chosen['source'], chosen['membership']['chosen']) == ('17.49', 'price_level', True)
    # Priority: the first matching rule wins over a level, and the preview says the plan was passed over.
    write(books, 'price-rule set-preferences', {'conflict_policy': 'priority'})
    ruled = preview(books, '2026-11-15')
    assert (ruled['unit_price'], ruled['source']) == ('18.99', 'rule')
    assert ruled['membership'] == dict(ruled['membership'], chosen=False,
                                       reason='the first matching price rule wins under the priority preference')
    assert invoice(books, '2026-11-15')['total_minor_units'] == 5697
    # Lowest price again with a deeper rule: 16.99 under the member 17.49.
    write(books, 'price-rule set-preferences', {'conflict_policy': 'lowest_price'})
    write(books, 'price-rule create', {'name': 'Fifteen off', 'items': ['Widget'], 'value': '15', 'priority': 2})
    deeper = preview(books, '2026-11-15')
    assert (deeper['unit_price'], deeper['membership']['reason']) == ('16.99', 'a matching price rule gives a lower price')
    assert deeper['note'].endswith('; member plan Trade not used: a matching price rule gives a lower price')


def test_overlapping_enrollments_are_refused_and_adjacent_ones_accepted(books):
    first = enroll(books, end=None)
    assert code_of(lambda: enroll(books, start='2027-01-01', end='2027-01-31')) == 'E_MEMBERSHIP_OVERLAP'
    write(books, 'customer membership end', {'membership': first['id'], 'expected_version': 1, 'end_date': '2026-11-30'})
    for start, end in (('2026-11-30', '2026-12-31'), ('2026-10-01', '2026-11-01'), ('2026-10-01', None)):
        assert code_of(lambda: enroll(books, start=start, end=end)) == 'E_MEMBERSHIP_OVERLAP', (start, end)
    after = enroll(books, start='2026-12-01', end='2026-12-31')
    before = enroll(books, start='2026-10-01', end='2026-10-31')
    assert after['status'] == before['status'] == 'active'
    assert code_of(lambda: write(books, 'customer membership set', {
        'customer': books.customer, 'plan': 'Trade', 'membership': after['id'], 'expected_version': 1,
        'start_date': '2026-11-15', 'end_date': '2026-12-31'})) == 'E_MEMBERSHIP_OVERLAP'
    assert len(books.run('customer membership show', {'customer': books.customer})['history']) == 3


def test_the_layer_off_changes_no_price_and_refuses_membership_writes(books):
    enroll(books)
    profile_on = invoice(books, '2026-11-15')['revision']['profile']
    info = books.run('company show', {})
    write(books, 'company update', {'membership_pricing_enabled': False, 'expected_version': info['info_version']})
    off = invoice(books, '2026-11-15')
    assert off['total_minor_units'] == 5997 and 'membership' not in off['revision']['profile']
    assert set(profile_on) - set(off['revision']['profile']) == {'membership'}
    assert preview(books, '2026-11-15')['membership'] is None
    assert code_of(lambda: enroll(books, start='2027-01-01', end='2027-01-31')) == 'E_FEATURE_DISABLED'
    assert code_of(lambda: write(books, 'membership-plan create', {'name': 'Other', 'price_level': 'Member 12.5'})) == 'E_FEATURE_DISABLED'
    shown = books.run('customer membership show', {'customer': books.customer, 'as_of': '2026-11-15'})
    assert shown['layer_enabled'] is False and shown['effective'] is None and len(shown['history']) == 1
    # Membership pricing rides on price levels: it cannot be turned on while they are off.
    write(books, 'company update', {'enable_price_levels': False})
    assert code_of(lambda: write(books, 'company update', {'membership_pricing_enabled': True})) == 'E_VALIDATION'


def test_old_companies_migrate_intact_with_the_layer_off(books, monkeypatch):
    from bookflow.storage.engine import open_database
    from bookflow.storage.migrate import HEADS, migrate_to_head
    head = HEADS['company']
    path = Path(books.run('company show', {})['path']) / 'company.db'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=OFF')
        for table in ('customer_memberships', 'membership_plans'):
            db.execute('DROP TABLE ' + table)
        db.execute('ALTER TABLE company_info DROP COLUMN membership_pricing_enabled')
        db.execute("UPDATE alembic_version SET version_num='co0133'")
        before = {name: db.execute('SELECT * FROM ' + name).fetchall() for name in ('customers', 'items', 'price_levels')}
    # Only this migration is rewound, so only it is run; the file is stamped back to its real head afterwards.
    with monkeypatch.context() as patch:
        patch.setitem(HEADS, 'company', 'co0134')
        with open_database(path, writable=True) as db:
            assert migrate_to_head(db, 'company', None) == ('co0133', 'co0134')
            for name, rows in before.items():
                assert db.raw.execute('SELECT * FROM ' + name).fetchall() == rows
            assert db.raw.execute('SELECT membership_pricing_enabled FROM company_info').fetchall() == [(0,)]
            assert db.raw.execute('SELECT count(*) FROM membership_plans').fetchone() == (0,)
            assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
    with sqlite3.connect(path) as db:
        db.execute('UPDATE alembic_version SET version_num=?', (head,))
    assert invoice(books, '2026-11-15')['total_minor_units'] == 5997


@pytest.mark.parametrize('surface', SURFACES)
def test_membership_through_python_cli_http_and_mcp(books, tmp_path, surface):
    from tests.mcp_matrix_support import Matrix
    customer = books.customer

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(books.root, tmp_path / 'surfaces')
            call = lambda name, values, **kw: matrix.call(surface, name, values, **kw)  # noqa: E731
            plan = await call('membership-plan create', {'name': 'Surface plan', 'price_level': 'Member 12.5'})
            made = await call('customer membership set', {'customer': customer, 'plan': plan['id'],
                                                          'start_date': '2026-11-01', 'end_date': '2026-11-30'})
            enrolled = made['membership']
            # Typed refusals, the same on every surface.
            # An unknown field cannot be spelled as a CLI flag, so that case is for the other three.
            unknown = () if surface == 'cli' else ({'start_date': '2027-01-01', 'fee': '5'},)
            for bad in ({'start_date': '2026-13-01'}, {'start_date': '2026-11-01', 'end_date': '2026-10-31'},
                        {'start_date': '2027-01-01', 'member_number': '-bad'}, *unknown):
                refused = await call('customer membership set', {'customer': customer, 'plan': plan['id'], **bad}, rejected=True)
                assert refused['code'] == 'E_VALIDATION', (bad, refused)
            overlap = await call('customer membership set', {'customer': customer, 'plan': plan['id'],
                                                             'start_date': '2026-11-30'}, rejected=True)
            assert overlap['code'] == 'E_MEMBERSHIP_OVERLAP'
            shown = await call('customer membership show', {'customer': customer, 'as_of': '2026-11-30'})
            assert shown['effective']['id'] == enrolled['id']
            priced = await call('price-rule preview', {'item': books.widget, 'customer': customer, 'quantity': '3', 'date': '2026-11-30'})
            assert priced['unit_price'] == '17.49' and priced['membership']['plan']['id'] == plan['id']
            ended = await call('customer membership end', {'membership': enrolled['id'], 'expected_version': 1, 'end_date': '2026-11-15'})
            assert ended['membership']['status'] == 'ended'
            changed = await call('membership-plan update', {'plan': plan['id'], 'expected_version': 1, 'active': False})
            assert changed['active'] is False
            listed = await call('membership-plan list', {'include_inactive': True})
            assert {p['name'] for p in listed['items']} == {'Trade', 'Surface plan'} and listed['layer_enabled']
            assert (await call('membership-plan show', {'plan': 'Surface plan'}))['enrollments'] == 1
        finally:
            await matrix.close()
    anyio.run(witness)
