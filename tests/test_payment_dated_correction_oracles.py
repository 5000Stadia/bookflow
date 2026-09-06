from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import posted


def test_forward_corrected_invoice_is_not_effective_before_corrected_date(client,sale):
    invoice=posted(client,sale['customer'],sale['item'],'1.00','RECHECK-FORWARD-DATE')
    client.run('invoice update',dict(invoice=invoice['id'],expected_version=1,date='2026-06-10',operation_key='recheck-forward-date'),company=COMPANY,reason='Correct invoice date')
    state=client.run('invoice settlement',dict(invoice=invoice['id'],as_of='2026-06-05'),company=COMPANY)
    assert state['gross_minor_units']==state['applied_minor_units']==state['due_minor_units']==0
    assert state['status']=='not_effective',state['status']


def test_forward_corrected_then_voided_invoice_is_not_effective_before_corrected_date(client,sale):
    invoice=posted(client,sale['customer'],sale['item'],'1.00','RECHECK-FORWARD-VOID')
    client.run('invoice update',dict(invoice=invoice['id'],expected_version=1,date='2026-06-10',operation_key='recheck-forward-void'),company=COMPANY,reason='Correct invoice date')
    client.run('invoice void',dict(invoice=invoice['id'],expected_version=2),company=COMPANY,reason='Cancel corrected invoice')
    state=client.run('invoice settlement',dict(invoice=invoice['id'],as_of='2026-06-05'),company=COMPANY)
    assert state['gross_minor_units']==state['applied_minor_units']==state['due_minor_units']==0
    assert state['status']=='not_effective',state['status']
