"""Work policy controls, history, closed-disclosure print and progress on two widths."""
import base64
import subprocess
import pytest
from tests.test_row5_browser_acceptance import CHROME,browser_site
from tests.test_row8_register_browser import register_browser,_command
from tests.test_service_sales_browser import _fill,_click,_contained,_value,_preview
from tests.test_customer_work_browser import visit,preview,saved

pytestmark=pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')


@pytest.mark.parametrize('width',[1280,390])
def test_work_policy_print_and_remaining_forecast(register_browser,width,tmp_path):
    env,b=register_browser,register_browser.browser
    run=lambda name,data:_command(b,env.site,name,data)
    b.viewport(width,900 if width==1280 else 844)
    proposal=run('proposal.show',dict(proposal='DEMO-TAX-PROP-LINE'))
    base=f'{env.site.base_url}/c/{env.site.company_id}'
    visit(b,base+'/proposal/'+proposal['id']+'/update')
    assert _value(b,'f:sales_tax_calculation')=='line_combined_half_up'
    _fill(b,'f:sales_tax_calculation','invoice_combined_half_up')
    preview(b);_contained(b,width)
    assert run('proposal.show',dict(proposal=proposal['id']))['tax_minor_units']==2
    _click(b,'submit');assert saved(b,'proposal')==proposal['id']
    revised=run('proposal.show',dict(proposal=proposal['id']))
    assert revised['tax_minor_units']==1 and revised['revision']['tax_calculation_details']['origin']['kind']=='explicit'
    b.navigate(base+'/proposal/'+proposal['id']+'?revision_number=1')
    b.wait_for('!!document.querySelector(".tax-details")')
    pdf=tmp_path/f'work-history-{width}.pdf'
    pdf.write_bytes(base64.b64decode(b.call('Page.printToPDF',dict(printBackground=True))['data']))
    text=subprocess.check_output(['pdftotext',str(pdf),'-'],text=True)
    (tmp_path/f'work-history-{width}.txt').write_text(text)
    assert all(value in text for value in ('Explicit document choice','Stable tax order: 1','Stable tax order: 2','10% on 0.05','0.02'))
    assert not b.evaluate('document.querySelector(".tax-details").open')
    order=run('work-order.show',dict(work_order='DEMO-TAX-WO'))
    b.navigate(base+'/work-order/'+order['id']+'/billing')
    b.wait_for('!!document.querySelector("[aria-label=\\"Remaining tax forecast\\"]")')
    assert 'Forecast for billing all remaining scope together' in b.evaluate('document.body.innerText')
    assert 'Remaining net 0.05' in b.evaluate('document.body.innerText')
    _contained(b,width)
    b.evaluate('document.querySelector("[aria-label^=Remaining]").scrollIntoView()')
    (tmp_path/f'work-tax-forecast-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot',dict(format='png'))['data']))
    visit(b,base+'/work-order/'+order['id']+'/invoice')
    _fill(b,'f:date','2026-11-14')
    _preview(b);_contained(b,width)
    assert 'Progress after this proposed bill' in b.evaluate('document.body.innerText')
    assert 'No positive charge remains' in b.evaluate('document.body.innerText')
    assert run('work-order.billing',dict(work_order=order['id']))['remaining_net_minor_units']==5
