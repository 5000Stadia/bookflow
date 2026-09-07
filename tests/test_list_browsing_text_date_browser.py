"""Dedicated list text/date choosers: exact predicates and visible read results."""
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from bookflow.company import query_providers
from tests.test_mcp_registry_work import company_snapshot
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command, _key, _tab_to


pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Chrome unavailable")


def _ready(browser):
    browser.wait_for("document.querySelector('#browse-form')?.dataset.ready === '1'")


def _replace_document(browser, action):
    browser.evaluate("void(window.previousBrowseDocument = document.documentElement)")
    action()
    browser.wait_for("!window.previousBrowseDocument?.isConnected && document.querySelector('#browse-form')?.dataset.ready === '1'")


def _choose(browser, definition, label, operator, value, kind):
    browser.evaluate("document.querySelector('#clear-filters').click();document.querySelector('#filter-controls').open=true")
    browser.evaluate(f"document.querySelector('#filter-search').value={json.dumps(label)};document.querySelector('#find-filters').click()")
    key = 'custom:' + definition
    browser.wait_for(f"[...document.querySelector('#available-filters').options].some(o => o.value === {json.dumps(key)})")
    browser.evaluate(f"document.querySelector('#available-filters').value={json.dumps(key)};document.querySelector('#available-filters').dispatchEvent(new Event('change'))")
    browser.wait_for("!!document.querySelector('#filter-value')")
    assert browser.evaluate("document.querySelector('#filter-value').type") == kind
    browser.evaluate(f"document.querySelector('#filter-operator').value={json.dumps(operator)};document.querySelector('#filter-operator').dispatchEvent(new Event('change'))")
    if operator == 'is_missing':
        assert not browser.evaluate("!!document.querySelector('#filter-value')")
    else:
        browser.evaluate(f"document.querySelector('#filter-value').value={json.dumps(value)}")
        assert browser.evaluate("document.querySelector('#filter-value').value") == value
    # The same actual Add and Apply controls are reachable using Tab/Enter.
    _tab_to(browser, '#add-filter')
    _key(browser, 'Enter')
    assert not browser.evaluate("document.querySelector('#browse-error').textContent")
    assert browser.evaluate("(() => {const r=document.querySelector('#add-filter').getBoundingClientRect();return r.width>=44 && r.height>=44;})()")
    browser.evaluate("document.querySelector('#filter-controls').open=false")
    _tab_to(browser, '#browse-form button[type=submit]')
    _replace_document(browser, lambda: _key(browser, 'Enter'))


@pytest.mark.parametrize('kind', ['text', 'date'])
@pytest.mark.parametrize('width', [1280, 390])
def test_text_date_named_criteria_exact_wire_results_and_retained_identity(
        register_browser, kind, width, tmp_path, monkeypatch):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 850)
    prefix = 'Criterion ' + kind
    label = 'Browser ' + kind + ' criterion'
    definition = _command(b, env.site, 'custom-field.create',
                          {'name': label, 'kind': kind, 'scopes': ['customer']})
    # Text equality is case-sensitive and literal, including Unicode, spaces
    # and SQL wildcard characters. Date range includes the leap-day boundary.
    value = '  Ångström %_  ' if kind == 'text' else '2028-02-29'
    values = ([value, value, '  ångström %_  ', None] if kind == 'text' else
              ['2028-02-29', '2028-03-01', '2028-02-28', None])
    records = []
    for index, stored in enumerate(values):
        raw = {'name': f'{prefix} {index}'}
        if stored is not None:
            raw['custom_fields'] = {definition['id']: stored}
        records.append(_command(b, env.site, 'customer.create', raw)['id'])

    captured = []
    actual = query_providers.query_page

    def observe(noun, inp, *args, **kwargs):
        result = actual(noun, inp, *args, **kwargs)
        if noun == 'customer':
            captured.append(deepcopy(inp.model_dump(mode='json')))
        return result

    monkeypatch.setattr(query_providers, 'query_page', observe)
    data_root = Path(os.environ['BOOKFLOW_DATA_ROOT'])
    baseline = company_snapshot(data_root)
    evidence = []

    def check(phase, criterion, expected_ids, current_label):
        wire = json.loads(b.evaluate("document.querySelector('#browse-custom').value"))
        assert wire == [criterion]
        url_wire = parse_qs(urlsplit(b.evaluate('location.href')).query)['custom_filters'][0]
        assert json.loads(url_wire) == [criterion]
        assert captured[-1]['custom_filters'] == [criterion]
        ids = b.evaluate("[...document.querySelectorAll('#master-results tbody tr')].map(r => r.dataset.recordId)")
        assert ids == expected_ids
        count = b.evaluate("document.querySelector('.browse-count').textContent")
        assert count.strip() == f'{len(expected_ids)} matching records; {len(expected_ids)} on this page.'
        text = b.evaluate("document.querySelector('#active-criteria').textContent")
        assert current_label in text and definition['id'] not in text
        assert b.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        assert company_snapshot(data_root) == baseline
        evidence.append({'phase': phase, 'wire': wire, 'query_input': captured[-1],
                         'record_ids': ids, 'count': count, 'criterion_text': text})
        (tmp_path / 'chooser-evidence.json').write_text(json.dumps(evidence, indent=2))
        # Keep the actual viewport width while including the count and rows
        # below the fold; a top-only phone image omits that evidence.
        height = b.evaluate('Math.max(innerHeight, document.documentElement.scrollHeight)')
        (tmp_path / f'{kind}-{width}-{phase}.png').write_bytes(base64.b64decode(
            b.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': True,
                'clip': {'x': 0, 'y': 0, 'width': width, 'height': height, 'scale': 1}})['data']))

    try:
        base = f'{env.site.base_url}/c/{env.site.company_id}/customer'
        b.navigate(base + '?' + urlencode({'query': prefix}))
        _ready(b)
        assert '4 matching records' in b.evaluate("document.querySelector('.browse-count').textContent")
        operator = 'eq' if kind == 'text' else 'gte'
        criterion = {'definition': definition['id'], 'kind': kind,
                     'operator': operator, 'value': value}
        _choose(b, definition['id'], label, operator, value, kind)
        check('matching', criterion, records[:2], label)
        _replace_document(b, lambda: b.evaluate("document.querySelector('#master-results th button').click()"))
        assert 'direction=desc' in b.evaluate('location.search')
        check('sorted', criterion, list(reversed(records[:2])), label)
        # A current metadata label changes without changing the stored values
        # or the definition-valued predicate. This is the only post-setup write.
        renamed = label + ' renamed'
        _command(b, env.site, 'custom-field.update',
                 {'custom_field': definition['id'], 'expected_version': definition['version'], 'name': renamed})
        baseline = company_snapshot(data_root)
        current_url = b.evaluate('location.href')
        _replace_document(b, lambda: b.navigate(current_url))
        check('renamed-reload', criterion, list(reversed(records[:2])), renamed)
        operator = 'ne' if kind == 'text' else 'lt'
        criterion = {**criterion, 'operator': operator}
        _choose(b, definition['id'], renamed, operator, value, kind)
        check('nonmatching', criterion, [records[2]], renamed)
        # Absence is a different typed branch, not an empty date/text value.
        criterion = {'definition': definition['id'], 'kind': 'presence', 'operator': 'is_missing'}
        _choose(b, definition['id'], renamed, 'is_missing', None, kind)
        check('absent', criterion, [records[3]], renamed)
    finally:
        # Preserve the actual final page even when an assertion fails.
        (tmp_path / 'final-page.txt').write_text(b.evaluate('document.body.innerText'))
        (tmp_path / 'final.png').write_bytes(base64.b64decode(
            b.call('Page.captureScreenshot', {'format': 'png'})['data']))
