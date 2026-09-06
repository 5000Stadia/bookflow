"""Current-AR labels cannot outlive the receipt that changes their posting facts."""
import json
import sqlite3
from pathlib import Path
import pytest
from tests.test_row5_browser_acceptance import browser_site
from tests.test_row8_register_browser import register_browser
from tests.test_payment_review_gui import setup
from tests.test_customer_payment_browser import click,wait,shot

@pytest.mark.parametrize('width',[1280,390])
@pytest.mark.parametrize('action',['Correct receipt','Unapply recorded applications','Void unapplied receipt','Apply available credit'])
def test_receipt_continuations_discard_stale_current_ar(register_browser,tmp_path,width,action):
    b,run,payer,other,base=setup(register_browser);b.viewport(width,900)
    assert b.evaluate("document.querySelector('#payment-payer-balance').innerText")=='0.00 USD'
    click(b,'preview');click(b,'save')
    b.evaluate(f"Array.from(document.querySelectorAll('#payment-record button')).find(x=>x.textContent==={json.dumps(action)}).click()")
    wait(b)
    facts=b.evaluate("""(()=>{const x=document.querySelector('#payment-balances');return {visible:!!x.getClientRects().length,
      payer:document.querySelector('#payment-payer-balance').innerText,family:document.querySelector('#payment-family-balance').innerText};})()""")
    facts['current_customer']=run('customer show',{'customer':payer})
    path=Path(run('company show',{})['path'])/'company.db'
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        facts['raw_ar']=db.execute("SELECT sum(l.debit_minor_units-l.credit_minor_units) FROM posting_lines l JOIN accounts a ON a.id=l.account_id WHERE a.type='accounts_receivable' AND l.name_type='customer' AND l.name_id=?",(payer,)).fetchone()[0]
    assert facts['raw_ar']==-1000
    assert not facts['visible'] or facts['payer']==facts['family']=='-10.00 USD',facts
    if action=='Apply available credit':assert facts['visible'] and facts['payer']==facts['family']=='-10.00 USD'
    else:assert not facts['visible'] and facts['payer']==facts['family']==''
    assert len(run('payment query',{'customer':payer})['items'])==1
    (tmp_path/'current-ar.json').write_text(json.dumps(facts,indent=2))
    shot(b,tmp_path,'current-ar-continuation',width)
