"""Filtered totals and every continuation retain independent settlement cents."""
import pytest

from bookflow import BookflowError
from tests.test_service_sales_lifecycle import COMPANY, sale
from tests.test_payment_receipts import method


def test_filtered_pages_keep_complete_counts_order_money_and_staleness(client, sale):
    payer=sale['customer']
    client.customer.update(customer=payer,expected_version=1,name='Window Straße',company=COMPANY)
    invoice=client.run('invoice post',dict(customer=payer,date='2026-06-01',lines=[
        dict(item=sale['item'],quantity='1',net_amount='100')]),company=COMPANY)
    pm=method(client)
    paid=[]
    for i,amount in enumerate(('50','70','40')):
        extra=dict(applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='20')])) if i==0 else {}
        paid.append(client.run('payment receive',dict(customer=payer,date='2026-06-02',amount=amount,
            payment_method=pm,operation_key=f'window-{i}',**extra),company=COMPANY))
    client.run('payment void',dict(payment=paid[2]['id'],expected_version=1,operation_key='window-void'),
        reason='Cancel unneeded receipt',company=COMPANY)
    expected={paid[0]['id']:(5000,2000,3000),paid[1]['id']:(7000,0,7000),paid[2]['id']:(4000,0,0)}
    for filters,order in [({'q':'WINDOW STRASSE'},[2,0,1]),({'has_available_credit':True},[0,1]),
            ({'has_available_credit':False},[2]),({'q':'Absent window remittance'},[])]:
        request=dict(customer=payer,sort='unapplied',direction='asc',limit=1,**filters)
        rows=[];cursor=None
        while True:
            page=client.run('payment query',dict(request,**({'cursor':cursor} if cursor else {})),company=COMPANY)
            assert page['total_count']==len(order)
            rows.extend(page['items']);cursor=page['next_cursor']
            if cursor is None:break
        assert [row['id'] for row in rows]==[paid[i]['id'] for i in order]
        for row in rows:
            assert tuple(row[key] for key in ('received_minor_units','applied_minor_units','unapplied_minor_units'))==expected[row['id']]
    request=dict(q='WINDOW STRASSE',limit=1)
    first=client.run('payment query',request,company=COMPANY)
    client.customer.update(customer=payer,expected_version=2,name='Changed current name',company=COMPANY)
    with pytest.raises(BookflowError) as caught:
        client.run('payment query',dict(request,cursor=first['next_cursor']),company=COMPANY)
    assert caught.value.code=='E_QUERY_STALE'
    assert client.run('payment query',request,company=COMPANY)['total_count']==3
    assert client.customer.show(customer=payer,company=COMPANY)['current_balance']['minor_units']==-2000


def test_filtered_page_does_not_sum_separate_extreme_receipts(client):
    pm=method(client)
    for i in range(2):
        payer=client.customer.create(name=f'Window extreme {i}',company=COMPANY)['id']
        client.run('payment receive',dict(customer=payer,date='2026-06-02',amount='90000000000000000.00',
            payment_method=pm,operation_key=f'window-extreme-{i}'),company=COMPANY)
    page=client.run('payment query',dict(q='Window extreme',has_available_credit=True,limit=1),company=COMPANY)
    assert page['total_count']==2
    ids=[]
    while True:
        row=page['items'][0];ids.append(row['id'])
        assert row['received_minor_units']==row['unapplied_minor_units']==9000000000000000000
        assert row['applied_minor_units']==0
        if not page['next_cursor']:break
        page=client.run('payment query',dict(q='Window extreme',has_available_credit=True,limit=1,cursor=page['next_cursor']),company=COMPANY)
    assert len(set(ids))==2
