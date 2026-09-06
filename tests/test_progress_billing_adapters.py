"""Actual adapter execution and authority boundaries for partial billing intent."""
import sqlite3

import pytest

from bookflow import BookflowError
from tests.conftest import make_actor, as_user
from tests.test_service_sales_lifecycle import sale, snapshot, COMPANY
from tests.test_row8_journal import hosted, database_path
from tests.test_work_billing_lifecycle import accepted, bill
from tests.test_customer_work_lifecycle import run


def test_actual_cli_preview_post_and_http_amount_remainder(client,sale,cli,request):
    source = accepted(client,sale)
    info = client.company.show(company=COMPANY)
    path = database_path(client)
    hosted = request.getfixturevalue('hosted')
    def stored():
        with sqlite3.connect(path) as db:
            names = [row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            return {name: db.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall() for name in names}
    before = stored()
    args = ['estimate','invoice',source['id'],'--expected-version','2','--conversion-key','cli-progress',
            '--date','2026-01-13','--percent','25','--company',COMPANY]
    preview = cli.json(*args,'--dry-run')
    assert preview['subtotal_minor_units'] == 617
    assert stored() == before
    first = cli.json(*args,'--expected-facts-fingerprint',preview['facts_fingerprint'])
    assert first['updated_via'] == 'cli' and first['subtotal_minor_units'] == 617
    cid = info['id']
    shown = hosted.ok('estimate.show',dict(estimate=source['id']),company=cid)
    second = hosted.ok('estimate.invoice',dict(estimate=source['id'],expected_version=shown['version'],
        conversion_key='http-progress',date='2026-01-13',
        selections=[dict(line_id=source['revision']['lines'][0]['line_id'],net_amount='10.00')]),company=cid)
    assert second['updated_via'] == 'http' and second['subtotal_minor_units'] == 1000
    line = second['revision']['lines'][0]
    assert line['quantity'] == '500/617' and line['quantity_microunits'] is None
    state = hosted.ok('estimate.billing',dict(estimate=source['id']),company=cid)
    assert state['remaining_net_minor_units'] == 851
    assert cli.json(*args)['id'] == first['id']


def test_readonly_partial_replay_and_foreign_company_isolation(client,sale,root):
    source = accepted(client,sale)
    data = dict(estimate=source['id'],expected_version=2,conversion_key='partial-authority',
                date='2026-01-13',percent='25')
    first = client.run('estimate invoice',data,company=COMPANY,idempotency_key='partial-authority-cache')
    cid = client.company.show(company=COMPANY)['id']
    make_actor(root,'progress-reader',company_role=(cid,'readonly'))
    reader = as_user(root,'progress-reader')
    assert reader.run('estimate billing',dict(estimate=source['id']),company=COMPANY)['remaining_net_minor_units'] == 1851
    with pytest.raises(BookflowError) as err:
        reader.run('estimate invoice',data,company=COMPANY,idempotency_key='partial-authority-cache')
    assert err.value.code == 'E_PERMISSION'
    other = client.company.new(legal_name='Separate progress company',display_name='Separate progress company',
        home_currency='USD',chart='general',organization='Demo Holdings LLC')
    with pytest.raises(BookflowError) as err:
        reader.run('invoice show',dict(invoice=first['id']),company=other['company_id'])
    assert err.value.code == 'E_COMPANY_NOT_FOUND'


def test_fully_billed_scope_keeps_100_percent_when_extra_charge_is_added(client,sale):
    source = accepted(client,sale)
    first = bill(client,source,percent='100')
    original = first['revision']['lines'][0]
    changed = client.run('invoice update',dict(invoice=first['id'],expected_version=1,
        lines=[dict(line_id=original['line_id'],item=sale['item']),
               dict(item=sale['item'],net_amount='5',description='Additional agreed work')]),company=COMPANY)
    assert changed['subtotal_minor_units'] == 2968
    state = run(client,'estimate','billing',estimate=source['id'])
    assert state['remaining_net_minor_units'] == 0 and state['lines'][0]['billed_scope_percent'] == '100'
    assert state['lines'][0]['billed_net_minor_units'] == 2468
    assert not state['can_invoice']


def test_closed_period_partial_rejects_without_reserving_source_or_key(client,sale):
    from bookflow.company import schema as c
    from bookflow.storage.engine import open_database
    source = accepted(client,sale)
    with open_database(database_path(client),writable=True) as db:
        db.conn.execute(c.company_info.update().values(closing_date='2026-01-31'))
        db.conn.commit()
    before = snapshot(client)
    with pytest.raises(BookflowError) as err:
        bill(client,source,percent='25')
    assert err.value.code == 'E_PERIOD_CLOSED'
    assert snapshot(client) == before
