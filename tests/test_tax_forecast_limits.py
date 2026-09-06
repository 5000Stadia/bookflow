"""Complete combined-tax forecasts and real bounded recovery at both span caps."""
import pytest
from bookflow import BookflowError
from tests.test_tax_policy_sales import sale,tax_sale,request,COMPANY
from tests.test_customer_work_lifecycle import run
from tests.test_work_billing_lifecycle import accepted,bill
from tests.test_progress_billing_lifecycle import current


@pytest.mark.timeout(1200)
@pytest.mark.parametrize('count,installments,remaining_net,remaining_tax',[(1,401,202,20),(21,201,2142,214)])
def test_complete_forecast_above_execution_caps(client,tax_sale,count,installments,remaining_net,remaining_tax):
    # 10% combined captured rate (two 5% components), one cent per unit.
    source=accepted(client,tax_sale,**dict(request(tax_sale),lines=[dict(item=tax_sale['item'],quantity=str(installments+1),net_amount=f'{(installments+1)//100}.{(installments+1)%100:02d}',tax_code=tax_sale['taxable']) for _ in range(count)]))
    line_ids=[row['line_id'] for row in source['revision']['lines']]
    bills=[]
    for index in range(installments):
        bills.append(bill(client,dict(source,version=2+index),f'cap-installment-{index}',selections=[dict(line_id=key,quantity='1') for key in line_ids]))
    for invoice in bills[::2]:
        client.run('invoice void',dict(invoice=invoice['id'],expected_version=1),company=COMPANY,reason='Release alternating exact scope')
    run(client,'company','update',progress_billing_enabled=False)
    state=run(client,'estimate','billing',estimate=source['id'])
    assert state['remaining_net_minor_units']==remaining_net
    assert state['remaining_tax_minor_units']==remaining_tax
    assert not state['can_bill_together']
    reason='line_span_limit' if count==1 else 'conversion_span_limit'
    assert reason in {row['code'] for row in state['forecast_eligibility_reasons']}
    source=current(client,source)
    with pytest.raises(BookflowError) as exc:bill(client,source,'over-cap')
    assert exc.value.code=='E_VALUE_RANGE'
    if count==1:
        assert state['lines'][0]['recommended_net_amount']['minor_units']==200
        assert exc.value.details['recommended_net_amount']['minor_units']==200
        first=bill(client,source,'bounded-net',selections=[dict(line_id=line_ids[0],net_amount='2.00')])
        assert first['subtotal_minor_units']==200 and first['tax_minor_units']==20
    else:
        first=bill(client,source,'bounded-lines',line_ids=line_ids[:19])
        assert first['subtotal_minor_units']==1938 and first['tax_minor_units']==194
    state=run(client,'estimate','billing',estimate=source['id'])
    assert state['can_bill_together']
    second=bill(client,current(client,source),'finish-bounded')
    assert second['revision']['tax_calculation_details']['attribution']==state['forecast_tax_attribution']
    done=run(client,'estimate','billing',estimate=source['id'])
    assert done['remaining_net_minor_units']==done['remaining_tax_minor_units']==0
    assert first['subtotal_minor_units']+second['subtotal_minor_units']==remaining_net
