"""Display defaults respect company calendar and preserve submitted/source intent."""
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import subprocess

import pytest

from bookflow.adapters.workbench import date_defaults as D
from bookflow.adapters.workbench.forms import leaves
from bookflow.core import registry
from tests.test_row3_host import hosted, WB
from tests.test_row5_workbench_forms import _browser
from tests.test_sales_document_form import _seed


@pytest.mark.parametrize('zone,instant,want', [
    ('America/Los_Angeles', '2028-01-01T00:30:00+00:00', '2027-12-31'),
    ('Pacific/Kiritimati', '2028-02-28T12:30:00+00:00', '2028-02-29'),
    ('America/Los_Angeles', '2028-03-12T10:30:00+00:00', '2028-03-12'),
    ('invalid/zone', '2028-01-01T00:30:00+00:00', '2028-01-01'),
    (None, '2028-01-01T00:30:00+00:00', '2028-01-01'),
])
def test_company_calendar_and_documented_utc_fallback(zone, instant, want):
    assert D.company_today({'info': {'timezone': zone}}, now=datetime.fromisoformat(instant)) == want


def test_explicit_map_matches_real_typed_fields_and_excludes_unrelated_dates():
    for command, fields in {**{k:(v,) for k,v in D.TRANSACTIONS.items()}, **D.REPORTS}.items():
        cmd = registry.get(command)
        assert cmd is not None, command
        typed = {leaf['path'] for leaf in leaves(cmd.input_model) if leaf['date']}
        assert set(fields) <= typed, (command, fields, typed)
        attempted = {'f:operation_key': 'existing-initializer'}
        D.seed(command, '2028-02-29', initial_get=True, query={}, originals={}, attempted=attempted)
        assert attempted == {'f:operation_key': 'existing-initializer', **{
            'f:' + field: '2028-01-01' if field == 'date_from' else '2028-02-29' for field in fields}}
    for command in ('invoice update', 'invoice void', 'estimate copy', 'estimate invoice',
                    'vendor-credit apply', 'customer-credit apply', 'reconcile start', 'custom-field create'):
        attempted = {}
        D.seed(command, '2028-02-29', initial_get=True, query={}, originals={}, attempted=attempted)
        assert attempted == {}, command


def test_initialization_never_replaces_blank_invalid_saved_or_source_dates():
    cases = [
        (False, {}, {}, {}, {}),
        (True, {}, {}, {'f:date':''}, {'f:date':''}),
        (True, {}, {}, {'f:date':'bad'}, {'f:date':'bad'}),
        (True, {}, {'date':'2020-01-02'}, {}, {}),
        (True, {'f:date':''}, {}, {}, {'f:date':''}),
        (True, {'date':'not-a-date'}, {}, {}, {'f:date':'not-a-date'}),
        (True, {'receipt':'source'}, {}, {}, {}),
        (True, {'purchase_order':'source'}, {}, {}, {}),
        (True, {'cursor':'continuation'}, {}, {}, {}),
    ]
    for initial, query, originals, attempted, want in cases:
        D.seed('invoice post', '2028-02-29', initial_get=initial, query=query,
               originals=originals, attempted=attempted)
        assert attempted == want
    attempted = {}
    D.seed('deposit post', '2028-02-29', initial_get=True, query={},
           originals={'document': {'date': ''}}, attempted=attempted)
    assert attempted == {}
    attempted = {}
    D.seed('report profit-and-loss', '2028-02-29', initial_get=True,
           query={'date_to': '2020-12-31'}, originals={}, attempted=attempted)
    assert attempted == {'f:date_to': '2020-12-31'}  # no invented conflicting lower bound


def test_presets_use_company_day_even_when_browser_calendar_differs():
    script = Path('src/bookflow/adapters/workbench/static/dates.js').read_text()
    # Exercise the exported calendar functions, before DOM enhancement starts.
    calendar = script.split('  const controls =')[0] + '\n})();'
    check = """
const assert = require('node:assert/strict');
for (const zone of ['America/Los_Angeles', 'Pacific/Kiritimati']) {
 process.env.TZ=zone;
 assert.deepEqual(BookflowDates.range('today'), ['2028-02-29','2028-02-29']);
 assert.deepEqual(BookflowDates.range('year-to-date'), ['2028-01-01','2028-02-29']);
 assert.deepEqual(BookflowDates.range('last-quarter'), ['2027-10-01','2027-12-31']);
}
"""
    subprocess.run(['node', '-e', "global.document={body:{dataset:{companyToday:'2028-02-29'}}};\n" + calendar + check],
                   check=True, capture_output=True, text=True)


def _value(page, name):
    match = re.search(r'<input\b[^>]*name="' + re.escape(name) + r'"[^>]*>', page.text)
    assert match, name
    value = re.search(r'value="([^"]*)"', match.group())
    return html.unescape(value.group(1)) if value else ''


def test_fresh_forms_reports_custom_screens_and_retained_attempts(hosted, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2028, 2, 28, 12, 30, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(D, 'datetime', Clock)
    company = hosted.company_id
    hosted.ok('company.update', {'timezone':'Pacific/Kiritimati'}, company=company,
              headers={'X-Bookflow-Reason':'Company calendar witness'})
    browser = _browser(hosted)
    base = f'/c/{company}'
    for route, field in [('invoice/post','date'), ('check/post','date'),
                         ('item-receipt/post','date'), ('deposit/post','document.date')]:
        page = browser.get(base + '/' + route)
        assert page.status_code == 200, page.text[:500]
        assert _value(page, 'f:' + field) == '2028-02-29', route
        if route == 'deposit/post':
            assert _value(page, 'f:operation_key').startswith('WB-')
    for report, expected in [('profit-and-loss', {'date_from':'2028-01-01','date_to':'2028-02-29'}),
                             ('balance-sheet', {'date_to':'2028-02-29'}),
                             ('ar-aging', {'as_of':'2028-02-29'})]:
        page = browser.get(base + '/report/' + report)
        assert page.status_code == 200
        payload = {f'f:{key}': _value(page, 'f:' + key) for key in expected}
        assert payload == {'f:' + key:value for key,value in expected.items()}
        run = browser.post(base + '/report/' + report, data={**payload, 'action':'submit'}, headers=WB)
        assert run.status_code == 200 and 'Structured report data' in run.text
    for query, want in [('?f:date=', ''), ('?date=bad-date', 'bad-date')]:
        assert _value(browser.get(base + '/invoice/post' + query), 'f:date') == want
    for day in ('', 'bad-date'):
        page = browser.post(base + '/invoice/post', data={'action':'preview','f:date':day}, headers=WB)
        assert _value(page, 'f:date') == day
    saved = _seed(hosted, 'invoice')
    assert _value(browser.get(base + f'/invoice/{saved}/update'), 'f:date') == '2026-01-12'
    for path, config_id in [('receive-payments','payment-config'), ('pay-bills','pay-bills-config')]:
        page = browser.get(base + '/' + path)
        assert page.status_code == 200
        raw = re.search(r'<script[^>]*id="' + config_id + r'"[^>]*>(.*?)</script>', page.text, re.S)
        assert raw
        assert json.loads(raw.group(1))['today'] == '2028-02-29'
