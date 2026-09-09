"""Actual Chrome journeys against disposable books, at desktop and phone widths."""
import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_service_sales_browser import _fill, _choose, _value, _click, _contained, _error

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def preview(b):
    b.evaluate('void (window.oldWorkForm = document.querySelector("[data-sales-form]"))')
    _click(b, 'preview')
    b.wait_for('!window.oldWorkForm.isConnected')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), b.evaluate('document.body.innerText')
    assert len(_value(b, 'f:expected_facts_fingerprint')) == 64
    assert 'Preview — nothing written' in b.evaluate('document.querySelector(".work-document").innerText')


def saved(b, noun):
    b.wait_for(f'!document.querySelector("[data-generated-form]") && !!document.querySelector(".work-document") && location.pathname.includes("/{noun}/")')
    return b.evaluate('location.pathname').rsplit('/', 1)[-1]


def visit(b, url):
    b.navigate(url)
    b.wait_for('!!document.querySelector("[data-generated-form]")')


@pytest.mark.parametrize('width', [1280, 390])
def test_complete_work_chain_and_alternative(register_browser, width, tmp_path):
    env, b = register_browser, register_browser.browser
    run = lambda name, data: _command(b, env.site, name, data)
    b.viewport(width, 900)
    income = run('account.create', dict(name='Work income', type='income'))['id']
    customer = run('customer.create', dict(name='Work customer'))['id']
    employee = run('employee.create', dict(name='Work technician'))['id']
    custom = run('custom-field.create', dict(name='Work approval flag', kind='bool', scopes=['proposal', 'estimate', 'work_order']))['id']
    run('company.update', dict(units_of_measure_mode='multiple_related_units'))
    units = run('unit-of-measure.create', dict(name='Work units', units=[dict(name='Each', abbreviation='ea', is_base=True, base_factor='1')]))
    code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    item = run('item.create', dict(name='Work labor', type='service', sales_enabled=True,
        income_account_id=income, price='12.34', description='Replace tap', sales_tax_code_id=code, unit_of_measure_set_id=units['id']))['id']
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    visit(b, base + '/proposal/create')
    assert not b.evaluate('!!document.querySelector("textarea[spellcheck=false]")'), b.evaluate('document.body.innerText')
    _fill(b, 'f:date', '2026-01-12'); _fill(b, 'f:title', 'Kitchen tap')
    _choose(b, 'f:customer', 'Work customer')
    b.evaluate('document.querySelector("[data-collection-path=lines] > [data-collection-add]").click()')
    _choose(b, 'c:lines:0:item', 'Work labor')
    _choose(b, 'c:lines:0:unit', 'Each')
    _fill(b, 'cf-state:' + custom, 'set'); _fill(b, 'cf:' + custom, 'false')
    _fill(b, 'c:lines:0:quantity', '2')
    _fill(b, 'c:lines:0:net_amount', '10.01')
    preview(b); _contained(b, width)
    _click(b, 'submit'); proposal = saved(b, 'proposal')
    visit(b, base + '/proposal/' + proposal + '/estimate')
    _fill(b, 'f:date', '2026-01-13')
    key = _value(b, 'f:conversion_key'); assert len(key) >= 32
    _click(b, 'submit'); assert _value(b, 'f:expected_facts_fingerprint') == ''
    preview(b); assert _value(b, 'f:conversion_key') == key
    _click(b, 'submit'); estimate = saved(b, 'estimate')
    visit(b, base + '/estimate/' + estimate + '/copy')
    _fill(b, 'f:date', '2026-01-13')
    _fill(b, 'cf-state:' + custom, 'clear')
    preview(b); _click(b, 'submit'); alternative = saved(b, 'estimate')
    assert alternative != estimate
    alt = run('estimate.show', dict(estimate=alternative))
    assert not any(v['definition_id'] == custom for v in alt['revision']['custom_fields'])
    source = run('estimate.show', dict(estimate=estimate))
    assert next(v['value'] for v in source['revision']['custom_fields'] if v['definition_id'] == custom) is False
    visit(b, base + '/estimate/' + estimate + '/update')
    _fill(b, 'f:status', 'accepted'); _fill(b, 'f:decision_note', 'Customer agreed by phone')
    preview(b); _click(b, 'submit'); assert saved(b, 'estimate') == estimate
    visit(b, base + '/estimate/' + estimate + '/work-order')
    _fill(b, 'f:date', '2026-01-14')
    b.evaluate('document.querySelector("[data-collection-path=assignees] > [data-collection-add]").click()')
    assignee_name = b.evaluate('document.querySelector("[data-collection-path=assignees] [data-collection-items] [data-ref-value]").name')
    _choose(b, assignee_name, 'Work technician')
    _fill(b, 'f:scheduled_start', '2026-01-14T10:00:00Z')
    preview(b); _click(b, 'submit'); order = saved(b, 'work-order')
    visit(b, base + '/work-order/' + order + '/complete')
    _click(b, 'submit'); assert _value(b, 'f:expected_facts_fingerprint') == ''
    _fill(b, 'f:actual_start', '2026-01-14T10:00:00Z')
    if width == 1280:
        _fill(b, 'f:actual_end', '2026-01-14T11:00:00Z')
    preview(b); _click(b, 'submit'); assert saved(b, 'work-order') == order
    record = run('work-order.show', dict(work_order=order))
    assert record['status'] == 'complete'
    assert record['revision']['facts']['assignees'][0]['id'] == employee
    assert record['revision']['lines'][0]['completed_quantity'] == '2'
    assert record['revision']['lines'][0]['unit_price'] is None
    assert record['net_minor_units'] == 1001
    import base64
    (tmp_path / f'work-complete-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})['data']))
    assert run('account.show', dict(account=income))['balance']['minor_units'] == 0
    assert b.evaluate('document.querySelector("[data-annotations]")?.dataset') is not None
    _contained(b, width)
    import json
    config = json.loads(b.evaluate('document.querySelector("[data-annotations]").dataset.annotations'))
    assert config['target']['record_type'] == 'work_document'
    line_url = b.evaluate('Array.from(document.querySelectorAll("a")).find(a=>a.href.includes("annotation_line="))?.href')
    assert line_url
    b.navigate(line_url)
    b.wait_for('!!document.querySelector("[data-annotations]")')
    config = json.loads(b.evaluate('document.querySelector("[data-annotations]").dataset.annotations'))
    assert config['target'] == dict(record_type='work_line', record_id=record['revision']['lines'][0]['line_id'])
    b.navigate(base + '/work-order/' + order + '/history')
    b.wait_for('!!document.querySelector(".work-document")')
    assert 'Revision history' in b.evaluate('document.body.innerText')
    _contained(b, width)


@pytest.mark.parametrize('width', [1280, 390])
def test_stale_conflict_and_editor_origins(register_browser, width):
    env, b = register_browser, register_browser.browser
    run = lambda name, data, **headers: _command(b, env.site, name, data, **headers)
    b.viewport(width, 900)
    income = run('account.create', dict(name='Conflict work income', type='income'))['id']
    customer = run('customer.create', dict(name='Conflict work customer'))['id']
    code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    item = run('item.create', dict(name='Conflict work labor', type='service', sales_enabled=True,
        income_account_id=income, price='10.00', description='Saved scope', sales_tax_code_id=code))['id']
    base = f'{env.site.base_url}/c/{env.site.company_id}/estimate'
    visit(b, base + '/create')
    _fill(b, 'f:date', '2026-01-12'); _fill(b, 'f:title', 'Retain this scope')
    _choose(b, 'f:customer', 'Conflict work customer')
    b.evaluate('document.querySelector("[data-collection-path=lines] > [data-collection-add]").click()')
    _choose(b, 'c:lines:0:item', 'Conflict work labor')
    _fill(b, 'c:lines:0:quantity', '2.5')
    preview(b)
    run('item.update', dict(item=item, price='11.00'))
    _click(b, 'submit'); _error(b, 'E_PREVIEW_STALE')
    assert _value(b, 'f:title') == 'Retain this scope'
    assert _value(b, 'c:lines:0:quantity') == '2.5'
    assert _value(b, 'f:expected_facts_fingerprint') == ''
    preview(b); _click(b, 'submit'); identity = saved(b, 'estimate')
    first = run('estimate.show', dict(estimate=identity))
    visit(b, base + '/' + identity + '/update')
    _fill(b, 'c:lines:0:quantity', '4'); _fill(b, 'f:memo', 'Keep my change')
    preview(b)
    other = run('estimate.update', dict(estimate=identity, expected_version=1, memo='Other writer'))
    _click(b, 'submit'); _error(b, 'E_VERSION_CONFLICT')
    assert _value(b, 'f:expected_version') == '1'
    assert _value(b, 'f:memo') == 'Keep my change'
    assert _value(b, 'c:lines:0:quantity') == '4'
    assert run('estimate.show', dict(estimate=identity))['version'] == other['version']
    _contained(b, width)
    visit(b, base + '/' + identity + '/update')
    run('item.update', dict(item=item, price='99.00', description='New master scope'))
    _fill(b, 'c:lines:0:quantity', '4')
    preview(b); _click(b, 'submit'); assert saved(b, 'estimate') == identity
    updated = run('estimate.show', dict(estimate=identity))
    assert updated['net_minor_units'] == 4400
    assert updated['revision']['lines'][0]['facts']['profile'] == first['revision']['lines'][0]['facts']['profile']
    assert updated['revision']['lines'][0]['facts']['pricing_basis'] == 'catalog'
    _contained(b, width)

    # Real editor mode switches remove competing saved inputs and preserve null cost on copy.
    visit(b, base + '/' + identity + '/update')
    mode = b.evaluate('document.querySelector("[name^=price-mode]").name')
    _fill(b, mode, 'markup')
    _fill(b, 'c:lines:0:estimated_unit_cost', '4.00')
    _fill(b, 'c:lines:0:markup_percent', '25')
    preview(b); _click(b, 'submit'); saved(b, 'estimate')
    marked = run('estimate.show', dict(estimate=identity))
    assert marked['net_minor_units'] == 2000
    assert marked['revision']['lines'][0]['facts']['pricing_basis'] == 'markup'
    visit(b, base + '/' + identity + '/update')
    mode = b.evaluate('document.querySelector("[name^=price-mode]").name')
    _fill(b, mode, 'amount'); _fill(b, 'c:lines:0:net_amount', '10.01')
    b.evaluate('Array.from(document.querySelectorAll("input[type=checkbox]")).find(e=>e.name.startsWith("clear:c:lines:") && e.name.endsWith("estimated_unit_cost")).click()')
    preview(b); _click(b, 'submit'); saved(b, 'estimate')
    amount = run('estimate.show', dict(estimate=identity))
    assert amount['net_minor_units'] == 1001
    assert amount['revision']['lines'][0]['unit_price'] is None
    assert amount['revision']['lines'][0]['estimated_unit_cost'] is None
    visit(b, base + '/' + identity + '/copy')
    _fill(b, 'f:date', '2026-01-15')
    preview(b); _click(b, 'submit'); copied = saved(b, 'estimate')
    copy = run('estimate.show', dict(estimate=copied))
    assert copy['revision']['lines'][0]['facts'] == amount['revision']['lines'][0]['facts']
    _contained(b, width)


def test_bounded_source_links_and_stale_restart(register_browser, monkeypatch):
    from bookflow.company import work
    # Exercise the real cursor contract with a small test page, avoiding 201 fixtures.
    monkeypatch.setattr(work, 'LINK_PAGE_SIZE', 1)
    env, b = register_browser, register_browser.browser
    run = lambda name, data: _command(b, env.site, name, data)
    customer = run('customer.create', dict(name='Source paging customer'))['id']
    source = run('proposal.create', dict(date='2026-01-12', title='Source pages', customer=customer))
    for _ in range(2):
        run('proposal.copy', dict(proposal=source['id'], expected_version=1, date='2026-01-13'))
    base = f'{env.site.base_url}/c/{env.site.company_id}/proposal/{source["id"]}'
    b.navigate(base.rsplit('/', 1)[0] + '?title=Source%20pages')
    b.wait_for('!!document.querySelector("form.list-tools")')
    assert 'Source pages' in b.evaluate('document.querySelector(".table-wrap").innerText')
    b.navigate(base)
    b.wait_for('!!document.querySelector(".work-document a[rel=next]")')
    next_url = b.evaluate('document.querySelector(".work-document a[rel=next]").href')
    assert 'links_cursor=' in next_url
    b.navigate(next_url)
    b.wait_for('!!document.querySelector(".work-document")')
    assert not b.evaluate('!!document.querySelector(".work-document a[rel=next]")')
    run('proposal.update', dict(proposal=source['id'], expected_version=1, memo='New source activity'))
    b.navigate(next_url)
    _error(b, 'E_QUERY_STALE')
    restart = b.evaluate('Array.from(document.querySelectorAll("a")).find(a=>a.textContent.includes("Restart list"))?.href')
    assert restart and 'links_cursor=' not in restart
    b.navigate(restart)
    b.wait_for('!!document.querySelector(".work-document a[rel=next]")')


def test_completion_missing_start_keeps_phone_form_for_correction(register_browser, tmp_path):
    env, b = register_browser, register_browser.browser
    run = lambda name, data: _command(b, env.site, name, data)
    customer = run('customer.create', dict(name='Completion customer'))['id']
    order = run('work-order.create', dict(date='2026-01-14', customer=customer, title='Finish site inspection'))
    b.viewport(390, 900)
    base = f'{env.site.base_url}/c/{env.site.company_id}/work-order/{order["id"]}'
    visit(b, base + '/complete')
    _fill(b, 'f:actual_end', '2026-01-14T11:00:00Z')
    _click(b, 'preview')
    _error(b, 'E_VALIDATION')
    assert 'actual_start' in b.evaluate('document.querySelector(".error").innerText')
    assert 'record when work began' in b.evaluate('document.querySelector(".error").innerText')
    assert _value(b, 'f:actual_end') == '2026-01-14T11:00:00Z'
    assert _value(b, 'f:expected_version') == '1'
    assert _value(b, 'f:actual_start') == ''
    assert b.evaluate('location.pathname').endswith('/complete')
    assert run('work-order.show', dict(work_order=order['id']))['version'] == 1
    _contained(b, 390)
    import base64
    (tmp_path / 'missing-start-validation-390.png').write_bytes(base64.b64decode(
        b.call('Page.captureScreenshot', {'format':'png', 'captureBeyondViewport':True})['data']))
    _fill(b, 'f:actual_start', '2026-01-14T10:00:00Z')
    preview(b)
    _click(b, 'submit')
    assert saved(b, 'work-order') == order['id']
    completed = run('work-order.show', dict(work_order=order['id']))
    assert completed['status'] == 'complete' and completed['version'] == 2
    assert completed['revision']['facts']['actual_start'] == '2026-01-14T10:00:00Z'
    assert completed['revision']['facts']['actual_end'] == '2026-01-14T11:00:00Z'
