"""Real desktop/phone use of the shared master-list controls."""
import json
from urllib.parse import urlencode
import pytest
from tests.test_row5_browser_acceptance import browser_site, _Cdp, PASSWORD
from tests.test_row8_register_browser import register_browser, _command, _key, _tab_to


def _activate(b, selector):
    """Activate a visible, uncovered control with a real pointer/touch event."""
    target = json.dumps(selector)
    point = b.evaluate(f"""(() => {{
        const e = document.querySelector({target});
        if (!e) throw new Error('Missing control: ' + {target});
        e.scrollIntoView({{block: 'center'}});
        const r = e.getBoundingClientRect();
        const x = r.left + r.width / 2, y = r.top + r.height / 2;
        if (!r.width || !r.height || !e.contains(document.elementFromPoint(x, y)))
            throw new Error('Control is not visible/reachable: ' + {target});
        return {{x, y, phone: innerWidth < 700}};
    }})()""")
    if point['phone']:
        b.call('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [
            {'x': point['x'], 'y': point['y']}]})
        b.call('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    else:
        for event in ('mousePressed', 'mouseReleased'):
            b.call('Input.dispatchMouseEvent', {'type': event, 'x': point['x'],
                'y': point['y'], 'button': 'left', 'clickCount': 1})


def _expand(b, selector):
    if not b.evaluate(f'document.querySelector({json.dumps(selector)}).open'):
        _activate(b, selector + ' > summary')
    b.wait_for(f'document.querySelector({json.dumps(selector)}).open')


def _customize(b, section):
    _expand(b, '.list-customize')
    _expand(b, section)


def _sort(b, width):
    if width == 390:
        _expand(b, '.master-mobile-sort')
        _activate(b, '.master-mobile-sort button')
    else:
        _activate(b, '#master-results th button')


def _results(width):
    return '.master-mobile-results' if width == 390 else '#master-results'


@pytest.mark.parametrize('width', [1280, 390])
def test_named_columns_filters_and_readable_collections(register_browser, width, tmp_path):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 850)
    command = lambda name, payload: _command(b, env.site, name, payload)
    defs = {}
    for kind in ('bool', 'number', 'choice'):
        data = {'name': 'Browsing ' + kind, 'kind': kind, 'scopes': ['customer']}
        if kind == 'choice':
            data['choices'] = [{'value': f'Option {i:03d}'} for i in range(205)]
        defs[kind] = command('custom-field.create', data)
    customer = command('customer.create', {'name': 'Browsing example', 'email': 'browse@example.invalid',
        'credit_limit': '90071992547409.93', 'custom_fields': {defs['bool']['id']: False, defs['number']['id']: '0', defs['choice']['id']: 'Option 204'}})
    command('customer.create', {'name': 'Browsing missing'})
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.navigate(base + '/customer?query=Browsing')
    b.wait_for("document.querySelector('#browse-form')?.dataset.ready === '1'")
    assert '2 matching records' in b.evaluate("document.querySelector('.browse-count').textContent")
    # Currency amounts translate exactly to the legacy integer-minor-unit filter.
    _customize(b, '#filter-controls')
    b.evaluate("document.querySelector('#filter-search').value='Current balance'")
    _activate(b, '#find-filters')
    b.wait_for("[...document.querySelector('#available-filters').options].some(o=>o.value==='current_balance')")
    b.evaluate("document.querySelector('#available-filters').value='current_balance';document.querySelector('#available-filters').dispatchEvent(new Event('change'))")
    b.wait_for("document.querySelector('#filter-editor').textContent.includes('Amount (USD)')")
    b.evaluate("document.querySelector('#filter-value').value='90071992547409.93'")
    _activate(b, '#add-filter')
    assert b.evaluate("document.querySelector('#browse-legacy input').value") == 'current_balance=9007199254740993'
    _activate(b, '#clear-filters')
    _activate(b, '#filter-controls > summary')
    # Use the chooser, not a handcrafted projection URL.
    _customize(b, '#column-controls')
    b.evaluate("document.querySelector('#column-search').value='Email'")
    _activate(b, '#find-columns')
    b.wait_for("[...document.querySelector('#available-columns').options].some(o=>o.value==='email')")
    b.evaluate("document.querySelector('#available-columns').value='email'")
    _activate(b, '#add-column')
    assert 'email' in b.evaluate("document.querySelector('#browse-columns').value")
    # Keyboard activates the actual submit button.
    _tab_to(b, '#browse-form button[type=submit]'); _key(b, 'Enter')
    b.wait_for("document.querySelector('#master-results')?.textContent.includes('browse@example.invalid')")
    _customize(b, '#filter-controls')
    b.evaluate("document.querySelector('#filter-search').value='Browsing bool'")
    _activate(b, '#find-filters')
    key = 'custom:' + defs['bool']['id']
    b.wait_for(f"[...document.querySelector('#available-filters').options].some(o=>o.value==={json.dumps(key)})")
    b.evaluate(f"document.querySelector('#available-filters').value={json.dumps(key)};document.querySelector('#available-filters').dispatchEvent(new Event('change'))")
    b.wait_for("!!document.querySelector('#filter-value')")
    b.evaluate("document.querySelector('#filter-value').value='false'")
    _activate(b, '#add-filter')
    _activate(b, '#browse-form button[type=submit]')
    b.wait_for("document.querySelector('.browse-count')?.textContent.includes('1 matching records')")
    assert customer['id'] in b.evaluate(f"document.querySelector('{_results(width)}').innerHTML")
    if width == 390:
        assert 'Browsing example' in b.evaluate("document.querySelector('.master-mobile-record h2').innerText")
        assert 'browse@example.invalid' in b.evaluate("document.querySelector('.master-mobile-record').innerText")
    assert 'Yes' not in b.evaluate("document.querySelector('#active-criteria').textContent")
    # A sortable heading retains the selected filter and columns.
    _sort(b, width)
    b.wait_for("location.search.includes('sort=') && !!document.querySelector('#active-criteria')")
    assert defs['bool']['id'] in b.evaluate("document.querySelector('#browse-custom').value")
    # A late choice is reached through bounded discovery, using search in the value chooser.
    _customize(b, '#filter-controls')
    b.evaluate("document.querySelector('#filter-search').value='Browsing choice'")
    _activate(b, '#find-filters')
    key = 'custom:' + defs['choice']['id']
    b.wait_for(f"[...document.querySelector('#available-filters').options].some(o=>o.value==={json.dumps(key)})")
    b.evaluate(f"document.querySelector('#available-filters').value={json.dumps(key)};document.querySelector('#available-filters').dispatchEvent(new Event('change'))")
    b.wait_for("!!document.querySelector('#filter-editor input[type=search]')")
    b.evaluate("document.querySelector('#filter-editor input[type=search]').value='Option 204'")
    _activate(b, '#filter-editor button')
    b.wait_for("[...document.querySelector('#filter-value').options].some(o=>o.textContent==='Option 204')")
    b.evaluate("const s=document.querySelector('#filter-value');s.value=[...s.options].find(o=>o.textContent==='Option 204').value")
    _activate(b, '#add-filter')
    _activate(b, '#browse-form button[type=submit]')
    b.wait_for(f"location.search.includes({json.dumps(defs['choice']['id'])}) && document.querySelector('#browse-form')?.dataset.ready === '1' && document.querySelector('#active-criteria')?.textContent.includes('Option 204')")
    assert '1 matching records' in b.evaluate("document.querySelector('.browse-count').textContent")
    # Exact accepted money must survive browser rendering beyond Number's integer precision.
    b.navigate(base + '/customer?' + urlencode({'query':'Browsing example','columns':'full_name,credit_limit'}))
    b.wait_for("!!document.querySelector('#master-results')")
    if width == 390:
        assert b.evaluate("document.querySelector('.master-mobile-record').getBoundingClientRect().width > 0")
        assert not b.evaluate("document.querySelector('.master-mobile-record details').open")
        assert '90071992547409.93' not in b.evaluate("document.querySelector('.master-mobile-record').innerText")
        _expand(b, '.master-mobile-record details')
    assert '90071992547409.93' in b.evaluate(f"document.querySelector('{_results(width)}').innerText")
    vendor = command('vendor.create', {'name':'Browsing vendor', 'contacts':[{'role':'primary','display_name':'Readable Contact','work_phone':'555-0109'}]})
    b.navigate(base + '/vendor/' + vendor['id'])
    b.wait_for("!!document.querySelector('.master-sections')")
    assert 'Readable Contact' in b.evaluate("document.querySelector('.master-sections').textContent")
    assert not b.evaluate("!!document.querySelector('.master-sections pre')")
    assert b.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    import base64
    (tmp_path / f'master-detail-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {'format':'png'})['data']))


@pytest.mark.parametrize('width', [1280, 390])
def test_reorder_reset_zero_missing_paging_and_stale_restart(register_browser, width, tmp_path):
    env,b=register_browser,register_browser.browser
    b.viewport(width,850)
    command=lambda name,data:_command(b,env.site,name,data)
    definition=command('custom-field.create',{'name':'Browser zero criterion','kind':'number','scopes':['customer']})
    for index in range(3):
        data={'name':f'Paged browser {index}'}
        if index<2:data['custom_fields']={definition['id']:'0' if index==0 else '1'}
        command('customer.create',data)
    base=f'{env.site.base_url}/c/{env.site.company_id}'
    b.navigate(base+'/customer?'+urlencode({'query':'Paged browser','limit':'1','columns':'full_name,email'}))
    b.wait_for("document.querySelector('#browse-form')?.dataset.ready==='1'")
    assert '3 matching records; 1 on this page' in b.evaluate("document.querySelector('.browse-count').textContent")
    assert b.evaluate("document.querySelector('#master-results th').getAttribute('aria-sort')")=='ascending'
    _sort(b, width)
    b.wait_for("location.search.includes('direction=desc') && document.querySelector('#browse-form')?.dataset.ready==='1'")
    assert 'Paged browser 2' in b.evaluate(f"document.querySelector('{_results(width)}').innerText")
    _sort(b, width)
    b.wait_for("location.search.includes('direction=asc') && document.querySelector('#browse-form')?.dataset.ready==='1'")
    assert 'Paged browser 0' in b.evaluate(f"document.querySelector('{_results(width)}').innerText")
    # Keyboard column reordering and removing are the same controls as touch buttons.
    _customize(b, '#column-controls')
    _tab_to(b,'#chosen-columns li:nth-child(2) button');_key(b,'Enter')
    assert b.evaluate("document.querySelector('#browse-columns').value")=='email,full_name'
    assert b.evaluate("document.activeElement.closest('li')===document.querySelector('#chosen-columns li') && !document.activeElement.hidden")
    _key(b,'Enter')
    assert b.evaluate("document.querySelector('#browse-columns').value")=='full_name,email'
    assert b.evaluate("document.activeElement.closest('li')===document.querySelector('#chosen-columns li:nth-child(2)')")
    _activate(b, '#reset-columns')
    b.wait_for("document.querySelector('#browse-columns').value.startsWith('full_name,company_name')")
    _customize(b, '#filter-controls')
    b.evaluate("document.querySelector('#filter-search').value='Browser zero criterion'")
    _activate(b, '#find-filters')
    key='custom:'+definition['id']
    b.wait_for(f"[...document.querySelector('#available-filters').options].some(o=>o.value==={json.dumps(key)})")
    b.evaluate(f"document.querySelector('#available-filters').value={json.dumps(key)};document.querySelector('#available-filters').dispatchEvent(new Event('change'))")
    b.wait_for("!!document.querySelector('#filter-value')")
    b.evaluate("document.querySelector('#filter-value').value='0'")
    _activate(b, '#add-filter')
    _activate(b, '#browse-form button[type=submit]')
    b.wait_for(f"location.search.includes({json.dumps(definition['id'])}) && document.querySelector('#browse-form')?.dataset.ready==='1'")
    assert 'Paged browser 0' in b.evaluate(f"document.querySelector('{_results(width)}').innerText")
    assert '1 matching records' in b.evaluate("document.querySelector('.browse-count').textContent")
    _activate(b, '#clear-filters')
    _activate(b, '#browse-form button[type=submit]')
    b.wait_for("document.querySelector('.browse-count')?.textContent.includes('3 matching records')")
    # Capture and use a real continuation, then invalidate it through an ordinary create.
    next_url=b.evaluate("document.querySelector('a[rel=next]').href")
    b.navigate(next_url);b.wait_for("document.querySelector('#master-results')?.textContent.includes('Paged browser 1')")
    next_url=b.evaluate("document.querySelector('a[rel=next]').href")
    command('customer.create',{'name':'Paged browser new'})
    b.navigate(next_url);b.wait_for("document.body.textContent.includes('Restart to see current results')")
    restart=b.evaluate("[...document.querySelectorAll('a')].find(a=>a.textContent.toLowerCase().includes('restart')).href")
    assert 'query=Paged' in restart and 'columns=' in restart and 'cursor=' not in restart
    b.navigate(restart);b.wait_for("document.querySelector('#browse-form')?.dataset.ready==='1'")
    _customize(b, '#filter-controls')
    b.evaluate("document.querySelector('#filter-search').value='Browser zero criterion'")
    _activate(b, '#find-filters')
    b.wait_for(f"[...document.querySelector('#available-filters').options].some(o=>o.value==={json.dumps(key)})")
    b.evaluate(f"document.querySelector('#available-filters').value={json.dumps(key)};document.querySelector('#available-filters').dispatchEvent(new Event('change'));document.querySelector('#filter-operator').value='is_missing';document.querySelector('#filter-operator').dispatchEvent(new Event('change'))")
    _activate(b, '#add-filter')
    _activate(b, '#browse-form button[type=submit]')
    b.wait_for("location.search.includes('is_missing') && document.querySelector('#browse-form')?.dataset.ready==='1'")
    assert '2 matching records' in b.evaluate("document.querySelector('.browse-count').textContent")
    # Active-only discovery cannot erase an explicit retained inactive criterion.
    command('custom-field.update', {'custom_field':definition['id'], 'expected_version':definition['version'], 'active':False})
    _expand(b, '.list-customize')
    _activate(b, '#metadata-active-only')
    b.wait_for(f"![...document.querySelector('#available-filters').options].some(o=>o.value==={json.dumps(key)})")
    _activate(b, '#browse-form button[type=submit]')
    b.wait_for("location.search.includes('metadata_active_only=1') && document.querySelector('#active-criteria')?.textContent.includes('(inactive)') && document.querySelector('#browse-form')?.dataset.ready==='1'")
    assert '2 matching records' in b.evaluate("document.querySelector('.browse-count').textContent")
    assert definition['id'] in b.evaluate("document.querySelector('#browse-custom').value")
    for noun in ('employee','item','unit-of-measure','price-level','custom-field'):
        rows=command(noun+'.query',{'limit':1})['items']
        assert rows,noun
        b.navigate(base+'/'+noun+'/'+rows[0]['id'])
        b.wait_for("!!document.querySelector('.master-sections')")
        assert not b.evaluate("!!document.querySelector('.master-sections pre')")
        assert b.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),noun
    import base64
    (tmp_path/f'complex-details-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot',{'format':'png'})['data']))
