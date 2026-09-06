"""Independent shared-draft reconstruction and complete authority oracles."""
import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import payment_authority
from tests.test_service_sales_lifecycle import COMPANY, sale
from tests.test_payment_receipts import method
from tests.test_work_billing_lifecycle import accepted, bill


def test_draft_query_reconstructs_origins_and_bounds_sql_per_page(client, sale):
    invoice = client.run('invoice post', dict(customer=sale['customer'], date='2026-06-01',
        lines=[dict(item=sale['item'], quantity='1', net_amount='100')]), company=COMPANY)
    baseline = client.run('payment selection query', {}, company=COMPANY)['total_count']
    drafts = []
    for i in range(201):
        draft = client.run('payment selection create', dict(mode='new_receipt', customer=sale['customer'],
            date='2026-06-02', amount='150'), company=COMPANY)
        if i % 40 == 0:
            draft = client.run('payment selection update', dict(selection=draft['id'], expected_version=draft['version'],
                set_items=[dict(invoice=invoice['id'], expected_version=1, amount='50', amount_origin='entered')]), company=COMPANY)
            draft = client.run('payment selection clear', dict(selection=draft['id'], expected_version=draft['version']), company=COMPANY)
            draft = client.run('payment selection update', dict(selection=draft['id'], expected_version=draft['version'],
                set_items=[dict(invoice=invoice['id'], expected_version=1, amount='25', amount_origin='calculated')]), company=COMPANY)
        drafts.append(draft)
    sql_counts = []
    for limit in (10, 200):
        statements = []
        def capture(connection, cursor, statement, parameters, context, many):
            statements.append(statement)
        sa.event.listen(sa.engine.Engine, 'before_cursor_execute', capture)
        try:
            page = client.run('payment selection query', dict(limit=limit), company=COMPANY)
        finally:
            sa.event.remove(sa.engine.Engine, 'before_cursor_execute', capture)
        assert len(page['items']) == limit and page['total_count'] == baseline + 201
        sql_counts.append(len(statements))
        for row in page['items']:
            assert row == client.run('payment selection show', dict(selection=row['id']), company=COMPANY)
    assert sql_counts[0] == sql_counts[1], sql_counts
    seen, cursor = set(), None
    while True:
        page = client.run('payment selection query', dict(limit=200, **({'cursor':cursor} if cursor else {})), company=COMPANY)
        assert not seen.intersection(row['id'] for row in page['items'])
        seen.update(row['id'] for row in page['items'])
        cursor = page['next_cursor']
        if cursor is None:
            break
    assert {row['id'] for row in drafts} <= seen and len(seen)==baseline+201
    original = client.run('payment selection items', dict(selection=drafts[0]['id'],revision=2),company=COMPANY)['items'][0]
    assert original['amount_minor_units']==5000 and original['amount_origin']=='entered'
    current = client.run('payment selection items', dict(selection=drafts[0]['id']),company=COMPANY)['items'][0]
    # Calculated rows recompute against the explicit150 cash and100 invoice due;
    # the retired entered50 revision remains independently readable above.
    assert current['amount_minor_units']==10000 and current['amount_origin']=='calculated'


def test_query_filters_cleared_draft_and_unapplied_source_before_count(client, sale, monkeypatch):
    baseline = client.run('payment selection query', {}, company=COMPANY)['total_count']
    invoice = bill(client, accepted(client, sale))
    paid = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='1',
        payment_method=method(client), operation_key='aggregation-protected-cash', applications=dict(mode='inline',
            items=[dict(invoice=invoice['id'],expected_version=1,amount='1')])),company=COMPANY)
    app = paid['effect']['applications'][0]['application_id']
    client.run('payment unapply',dict(payment=paid['id'],expected_version=1,operation_key='aggregation-unapply',
        applications=[dict(application_id=app,invoice_expected_version=2)]),company=COMPANY,reason='Correct allocation')
    cleared=client.run('payment selection create',dict(mode='new_receipt',customer=sale['customer'],date='2026-06-02'),company=COMPANY)
    client.run('payment selection update',dict(selection=cleared['id'],expected_version=1,set_items=[
        dict(invoice=invoice['id'],expected_version=3,amount='1')]),company=COMPANY)
    client.run('payment selection clear',dict(selection=cleared['id'],expected_version=2),company=COMPANY)
    source=client.run('payment selection create',dict(mode='existing_credit',payment=paid['id'],date='2026-06-02'),company=COMPANY)
    ordinary=client.run('payment selection create',dict(mode='new_receipt',customer=sale['customer'],date='2026-06-02'),company=COMPANY)
    original=payment_authority.require_resource
    def deny_work(s, resource, role):
        if resource=='customer-work':raise BookflowError('E_PERMISSION')
        return original(s,resource,role)
    monkeypatch.setattr(payment_authority,'require_resource',deny_work)
    page=client.run('payment selection query',dict(limit=200),company=COMPANY)
    assert page['total_count']==baseline+1
    ids={row['id'] for row in page['items']}
    assert ordinary['id'] in ids and not ids.intersection((cleared['id'],source['id']))
    for identifier in (cleared['id'],source['id']):
        with pytest.raises(BookflowError) as exc:
            client.run('payment selection show',dict(selection=identifier),company=COMPANY)
        assert exc.value.code=='E_PERMISSION'


def test_payer_and_family_projection_keeps_exact_owner_nets_and_i64_boundary(client, sale):
    payer=sale['customer']
    job=client.customer.create(name='Combined projection job',parent_id=payer,company=COMPANY)['id']
    def invoice(customer,amount):
        return client.run('invoice post',dict(customer=customer,date='2026-06-01',lines=[
            dict(item=sale['item'],quantity='1',net_amount=amount)]),company=COMPANY)
    invoice(payer,'40')
    billed=invoice(job,'100')
    pm=method(client)
    client.run('payment receive',dict(customer=payer,date='2026-06-02',amount='150',payment_method=pm,
        operation_key='combined-family-cash',applications=dict(mode='inline',items=[
            dict(invoice=billed['id'],expected_version=1,amount='100')])),company=COMPANY)
    page=client.run('payment invoices',dict(mode='new_receipt',customer=payer,date='2026-06-02'),company=COMPANY)
    # Parent AR40 minus parent-owned residual50; the child's AR100 is fully settled.
    assert page['payer_balance']['minor_units']==page['family_balance']['minor_units']==-1000
    assert client.customer.show(customer=payer,company=COMPANY)['family_balance']==page['family_balance']
    huge=client.customer.create(name='Exact aggregate overflow witness',company=COMPANY)['id']
    for _ in range(2):invoice(huge,'90000000000000000.00')
    with pytest.raises(BookflowError) as caught:
        client.run('payment invoices',dict(mode='new_receipt',customer=huge,date='2026-06-02'),company=COMPANY)
    assert caught.value.code=='E_VALUE_RANGE'
    # The owning journal path can correct an already-overflowed AR net without
    # first materializing a party balance snapshot. These are legacy AR credits,
    # not payment capacity and not automatic applications to the open invoices.
    ar=billed['revision']['profile']['control_account']['id']
    for _ in range(2):
        client.journal.post(date='2026-06-02',company=COMPANY,lines=[
            dict(account=ar,side='credit',amount='90000000000000000.00',name_type='customer',name_id=huge),
            dict(account=sale['income'],side='debit',amount='90000000000000000.00')])
    page=client.run('payment invoices',dict(mode='new_receipt',customer=huge,date='2026-06-02'),company=COMPANY)
    # Unbounded intermediate18e18 cancels to exact zero; no SQLite overflow/clamp.
    assert page['payer_balance']['minor_units']==page['family_balance']['minor_units']==0
    assert sum(row['due_minor_units'] for row in page['items'])==18000000000000000000
