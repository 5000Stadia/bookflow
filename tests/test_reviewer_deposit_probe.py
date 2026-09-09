import pytest
from tests.test_deposit_public_reads import public_world, reading, denying, without_host, COMPANY
from bookflow.company import deposit_public_reads as reads, deposit_read_models as m
from bookflow.core.errors import BookflowError


def test_connected_work_deny(public_world):
    from tests.test_service_sales_lifecycle import sale
    from tests.test_work_billing_lifecycle import accepted, bill
    from tests.test_payment_receipts import method
    from tests.test_deposit_draft_financial import financial, run_private
    w=public_world
    with without_host(w):
        client=w['client']; sales={'customer':w['customer'],'item':'Sale witness service'}
        invoice=bill(client,accepted(client,sales),key='review-work')
        payment=client.run('payment receive',dict(customer=sales['customer'],date='2026-06-02',amount='1.00',payment_method=w['method'],operation_key='review-work-pay',applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='1.00')])),company=COMPANY)
        with pytest.MonkeyPatch.context() as patch:
            private=run_private.__wrapped__(client,patch)
            posted=financial(private,dict(operation_key='review-work-deposit',document=dict(mode='inline',deposit_to=w['bank'],date='2026-06-03',sources=[dict(source_type='payment',source=payment['id'],expected_version=1)])))
    with denying(w,('customer-work',)):
        with reading(w) as (s,a,b):
            from bookflow.company import payment_authority
            required=payment_authority.requirements(s.company,[payment['id']])
            assert ('customer-work','member') in required
            assert not a.admits('customer-work')
            with pytest.raises(BookflowError):
                reads.show(s,m.ShowInput(deposit=posted.current.id),audience=a)


def test_denied_annotations_not_acquired(public_world,monkeypatch):
    from bookflow.company import deposit_read_facts
    w=public_world; seen=[]; original=deposit_read_facts._associations
    def observed(*args,**kwargs):
        seen.append(True);return original(*args,**kwargs)
    with denying(w,('note','attachment')):
        with reading(w) as (s,a,b):
            monkeypatch.setattr(deposit_read_facts,'_associations',observed)
            result=reads.show(s,m.ShowInput(deposit=w['deposit']),audience=a)
            assert result.links==()
    assert not seen, 'Denied annotation associations were acquired before public access decision'
