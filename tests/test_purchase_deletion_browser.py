"""Visible owning deletion, retained stale draft and historical reads in real Chrome."""
import base64
import json
from pathlib import Path
import pytest
from tests.test_bill_item_lines import books
from tests.test_purchase_deletion_http import office
from tests.test_identity_commands import live
from tests.test_row5_browser_acceptance import CHROME, _Cdp
from tests.payment_raw_evidence import database


@pytest.mark.timeout(180)
@pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')
@pytest.mark.parametrize('width,noun',[(1280,'check'),(390,'card-charge')])
def test_confirmation_refresh_keeps_draft_and_requires_new_confirmation(books,office,live,tmp_path,width,noun):
    company=books['company'];selector=noun.replace('-','_')
    funding=books['bank'] if noun=='check' else office.admin('account.create',dict(name='Delete card',type='credit_card'),company=company)['id']
    raw=dict(account=funding,date='2017-01-02',amount='0' if noun=='card-charge' else '11',
        items=[dict(item=books['delete_stock'],quantity='0.5',unit_cost='0' if noun=='card-charge' else '12.34')],
        expenses=[] if noun=='card-charge' else [dict(account=books['freight'],amount='4.83')])
    post=office.admin(noun+'.post',raw,company=company)
    clerk=office.admin('user.add',dict(username='deleter',password='deleter fixture',company=company,role='standard'))
    state=office.admin('permission.show')
    office.admin('permission.activate',dict(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256']))
    path=Path(office.admin('company.show',company=company)['path'])/'company.db'
    b=_Cdp(tmp_path/'delete-chrome');base=f'{live}/c/{company}/{noun}/{post["id"]}'
    form='document.querySelector("[data-purchase-delete]")'
    def act(action,wait):
        b.evaluate(form+'.querySelector('+json.dumps('button[value='+action+']')+').click()')
        b.wait_for(wait)
    try:
        b.viewport(width,900);b.navigate(live+'/login')
        b.evaluate("""document.querySelector('[name=username]').value='deleter';document.querySelector('[name=password]').value='deleter fixture';document.querySelector('form[hx-post="/login"]').requestSubmit()""")
        b.wait_for('!!document.querySelector(".nav-group")')
        b.navigate(base)
        assert not b.evaluate('!!document.querySelector("a[href$=\\"/delete\\"]")')
        office.admin('membership.grant',dict(user=clerk['user_id'],company=company,expected_version=1,
            grants=['transaction.'+selector+'.delete'],denies=['ledger.post']))
        b.navigate(base)
        assert b.evaluate('!!document.querySelector("a[href$=\\"/delete\\"]")')
        assert not b.evaluate('!!document.querySelector("a[href$=\\"/update\\"],a[href$=\\"/void\\"]")')
        b.navigate(base+'/delete');b.wait_for('!!'+form)
        key=b.evaluate(form+'.elements.operation_key.value')
        b.evaluate(form+'.elements.reason.value="Duplicate delivery — keep this reason"')
        before=database(path)
        act('preview','!!document.querySelector("[role=status]")')
        assert database(path)==before
        assert b.evaluate(form+'.elements.reason.value')=='Duplicate delivery — keep this reason'
        assert b.evaluate(form+'.elements.operation_key.value')==key
        office.admin(noun+'.update',{selector:post['id'],'expected_version':1,'memo':'Another editor corrected memo'},company=company,
            headers={'X-Bookflow-Reason':'Correct memo'})
        saved=database(path)
        b.evaluate(form+'.elements.confirmed.checked=true')
        act('delete','document.querySelector("[role=alert]")?.textContent.includes("E_VERSION_CONFLICT")')
        assert database(path)==saved
        assert b.evaluate(form+'.elements.expected_version.value')=='1'
        act('refresh',form+'?.elements.expected_version.value==="2"')
        assert b.evaluate(form+'.elements.reason.value')=='Duplicate delivery — keep this reason'
        assert b.evaluate(form+'.elements.operation_key.value')==key
        assert not b.evaluate(form+'.elements.confirmed.checked')
        assert 'Another editor corrected memo' in b.evaluate('document.body.textContent')
        act('delete','document.querySelector("[role=alert]")?.textContent.includes("Confirm cancellation")')
        assert database(path)==saved
        folder=Path('notes/phase4-screenshots');folder.mkdir(parents=True,exist_ok=True)
        b.evaluate(form+'.scrollIntoView()')
        (folder/f'delete-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot',{'captureBeyondViewport':True,'fromSurface':True})['data']))
        b.evaluate(form+'.elements.confirmed.checked=true')
        act('delete','location.search.includes("deleted=")')
        assert post['id'] not in {x['id'] for x in office.admin(noun+'.query',{},company=company)['items']}
        b.navigate(base+'?include_deleted=1&history=1&limit=1')
        b.wait_for('!!document.querySelector("[aria-label=\\"Deleted purchase\\"]")')
        assert 'Duplicate delivery — keep this reason' in b.evaluate('document.body.textContent')
        assert 'Revision 1' in b.evaluate('document.querySelector("[aria-label=\\"Purchase history\\"]").textContent')
        assert not b.evaluate('!!document.querySelector("a[href$=\\"/delete\\"],a[href$=\\"/update\\"],a[href$=\\"/void\\"],a[href$=\\"/post\\"]")')
        assert 'Deleted: Duplicate delivery' in b.evaluate('document.querySelector("[aria-label=\\\"Journal entry\\\"] .warn").textContent')
        assert b.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        (folder/f'deleted-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot',{'captureBeyondViewport':True,'fromSurface':True})['data']))
        b.evaluate('document.querySelector("[aria-label=\\"Purchase history pages\\"] a[rel=next]").click()')
        b.wait_for('location.search.includes("cursor=") && document.querySelector("[aria-label=\\"Purchase history\\"]")?.textContent.includes("Revision 2")')
        assert 'Revision 2' in b.evaluate('document.querySelector("[aria-label=\\"Purchase history\\"]").textContent')
        b.evaluate('document.querySelector("[aria-label=\\"Purchase history pages\\"] a[rel=prev]").click()')
        b.wait_for('!location.search.includes("cursor=") && document.querySelector("[aria-label=\\"Purchase history\\"]")?.textContent.includes("Revision 1")')
        assert 'Revision 1' in b.evaluate('document.querySelector("[aria-label=\\"Purchase history\\"]").textContent')
        b.call('Page.navigate',{'url':f'{live}/c/{company}/journal/{post["id"]}/update'})
        b.wait_for('document.readyState==="complete" && location.search.includes("include_deleted=1") && !!document.querySelector("[aria-label=\\\"Deleted purchase\\\"]")')
        assert not b.evaluate('!!document.querySelector("[data-generated-form]")')
    finally:b.close()
