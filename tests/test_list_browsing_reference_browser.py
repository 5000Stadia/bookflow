"""Retained reference predicates remain human-readable through normal navigation."""
import base64,json
from urllib.parse import urlencode
import pytest
from tests.test_row5_browser_acceptance import browser_site
from tests.test_row8_register_browser import register_browser,_command

@pytest.mark.parametrize('width',[1280,390])
def test_reference_label_activity_survive_navigation(register_browser,width,tmp_path):
    env=register_browser;b=env.browser;b.viewport(width,850)
    cmd=lambda name,payload:_command(b,env.site,name,payload)
    term=cmd('term.create',{'name':'Retained reference term','kind':'standard','due_days':37})
    for i in range(3):cmd('vendor.create',{'name':f'Retained reference vendor {i}','terms_id':term['id']})
    base=f'{env.site.base_url}/c/{env.site.company_id}'
    b.navigate(base+'/vendor?'+urlencode({'query':'Retained reference vendor','limit':'1','columns':'name,terms'}))
    b.wait_for("document.querySelector('#browse-form')?.dataset.ready==='1'")
    b.evaluate("document.querySelector('#filter-controls').open=true;document.querySelector('#filter-search').value='Terms';document.querySelector('#find-filters').click()")
    b.wait_for("[...document.querySelector('#available-filters').options].some(o=>o.value==='terms_id')")
    b.evaluate("document.querySelector('#available-filters').value='terms_id';document.querySelector('#available-filters').dispatchEvent(new Event('change'))")
    b.wait_for("!!document.querySelector('#filter-editor input[type=search]')")
    b.evaluate("document.querySelector('#filter-editor input[type=search]').value='Retained reference term';[...document.querySelectorAll('#filter-editor button')].find(x=>x.textContent==='Find values').click()")
    b.wait_for("[...document.querySelector('#filter-value').options].some(o=>o.textContent==='Retained reference term')")
    b.evaluate("let s=document.querySelector('#filter-value');s.value=[...s.options].find(o=>o.textContent==='Retained reference term').value;document.querySelector('#add-filter').click();document.querySelector('#browse-form').requestSubmit()")
    def check(label):
        b.wait_for("document.querySelector('#browse-form')?.dataset.ready==='1'")
        text=b.evaluate("document.querySelector('#active-criteria').innerText")
        assert label in text and term['id'] not in text
        assert b.evaluate("document.querySelector('#browse-legacy input').value")=='terms_id='+term['id']
        assert '3 matching records' in b.evaluate("document.querySelector('.browse-count').innerText")
        assert b.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    b.wait_for("location.search.includes('terms_id')");check('Retained reference term')
    url=b.evaluate('location.href');b.navigate(url);check('Retained reference term')
    b.evaluate("document.querySelector('#master-results th button').click()")
    b.wait_for("location.search.includes('direction=desc')");check('Retained reference term')
    b.navigate(b.evaluate("document.querySelector('a[rel=next]').href"));check('Retained reference term')
    cursor_url=b.evaluate('location.href')
    changed=cmd('term.update',{'term':term['id'],'expected_version':term['version'],'name':'Renamed reference term'})
    cmd('term.deactivate',{'term':term['id'],'expected_version':changed['version']})
    b.navigate(cursor_url);b.wait_for("document.body.innerText.includes('Restart to see current results')")
    restart=b.evaluate("[...document.querySelectorAll('a')].find(a=>a.textContent.toLowerCase().includes('restart')).href")
    assert 'cursor=' not in restart and term['id'] in restart
    b.navigate(restart);check('Renamed reference term (inactive)')
    b.evaluate("document.querySelector('#active-criteria').scrollIntoView({block:'center'})")
    (tmp_path/f'retained-reference-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot',{'format':'png'})['data']))
    (tmp_path/'criterion.json').write_text(json.dumps({'text':b.evaluate("document.querySelector('#active-criteria').innerText"),'url':b.evaluate('location.href')}))
