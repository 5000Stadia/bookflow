"""Complete filtered print views, shared continuation, and real browser paper output."""
import base64
import html
import io
import json
import re
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import QueryParams

from bookflow.adapters.workbench import report_export as Export, report_print as Print
from bookflow.core import registry
from bookflow.core.errors import BookflowError
from tests.test_report_export import _sample_filters, _shown
from tests.test_row5_browser_acceptance import CHROME, PASSWORD, _Cdp

LONG_TEXT = ' A long inspection description with all captured work notes retained. ' * 4
PERIOD = {'date_from': '2026-09-01', 'date_to': '2026-09-30'}


@pytest.fixture(scope='module')
def print_site(tmp_path_factory):
    import bookflow
    from bookflow.commands.host_cmds import start_serving
    from bookflow.core.config import os_login
    from bookflow.core.context import client_version
    directory = tmp_path_factory.mktemp('full-report')
    root = directory / 'root'
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('BOOKFLOW_DATA_ROOT', str(root))
        patch.delenv('BOOKFLOW_COMPANY', raising=False)
        client = bookflow.connect(data_root=str(root))
        client.init()
        client.organization.new(name='Print organization')
        company = client.company.new(legal_name='Complete Report Company', home_currency='USD',
            organization='Print organization', timezone='UTC', chart='general')['company_id']
        bank = client.account.create(company=company, name='Print bank', type='bank')['id']
        contra = client.account.create(company=company, name='Excluded counterpart', type='equity')['id']
        def post(day, amounts, prefix, offset=0):
            lines = [dict(account=bank, side='debit', amount=amount,
                          description=f'{prefix}-{i+offset:03d}' + (LONG_TEXT if prefix == 'PRINT-ROW' and i+offset == 0 else '')) for i, amount in enumerate(amounts)]
            total = sum(int(amount.split('.')[0]) * 100 + int(amount.split('.')[1]) for amount in amounts)
            lines.append(dict(account=contra, side='credit', amount=f'{total // 100}.{total % 100:02d}', description='EXCLUDED-ACCOUNT'))
            client.run('journal post', {'date': day, 'number': prefix + str(offset), 'lines': lines}, company=company)
        post('2026-08-31', ['10.00'], 'OPENING-ONLY')
        post('2026-09-15', ['1.00'] * 115, 'PRINT-ROW')
        post('2026-09-16', ['1.00'] * 114 + ['123456789.01'], 'PRINT-ROW', offset=115)
        post('2026-10-01', ['999.00'], 'EXCLUDED-DATE')
        client.run('user set-password', {'username': os_login(), 'password': PASSWORD})
        client.run('user add', {'username': 'print-outsider', 'password': PASSWORD})
        token = client.token.issue(label='report-print-test')['secret']
        handle = start_serving(root, client_version(), bind='127.0.0.1:8765', secure_cookies=False, publish_descriptor=False)
        site = SimpleNamespace(handle=handle, company=company, bank=bank, secret=token,
                               login=os_login(), directory=directory)
        try:
            yield site
        finally:
            handle.stop()


@pytest.fixture
def browser(print_site):
    with TestClient(print_site.handle.app) as browser:
        assert browser.post('/login', json={'username': print_site.login, 'password': PASSWORD}).status_code == 200
        yield browser


def test_complete_boundary_and_no_partial_report():
    cmd = registry.get('report general-ledger')
    for count in (10200, 10201):
        def read(name, raw, company):
            start = int(raw.get('cursor', 0))
            stop = min(start + raw['limit'], count)
            return {'rows': list(range(start, stop)), 'totals': {'sentinel': 42}, 'metadata': {'watermark': 7},
                    'count': stop - start, 'next_cursor': str(stop) if stop < count else None}
        if count == 10200:
            result = Print.complete(read, 'CO', cmd, {})
            assert result['rows'] == list(range(count)) and result['count'] == count
            assert result['totals'] == {'sentinel': 42} and result['next_cursor'] is None
        else:
            with pytest.raises(BookflowError, match='No partial report'):
                Print.complete(read, 'CO', cmd, {})


def test_stale_restart_discards_entire_abandoned_result():
    attempts = 0
    def read(name, raw, company):
        nonlocal attempts
        if not raw.get('cursor'):
            attempts += 1
            return dict(rows=[f'first-{attempts}'], next_cursor='next', totals={'version': attempts},
                        metadata={'version': attempts}, columns=[attempts])
        if attempts == 1:
            raise BookflowError('E_QUERY_STALE')
        return dict(rows=['second-2'], next_cursor=None)
    result = Print.complete(read, 'CO', registry.get('report general-ledger'), {})
    assert result == dict(rows=['first-2', 'second-2'], count=2, next_cursor=None,
                         totals={'version': 2}, metadata={'version': 2}, columns=[2])
    def stale(*args):
        raise BookflowError('E_QUERY_STALE')
    with pytest.raises(BookflowError, match='E_QUERY_STALE'):
        Print.complete(stale, 'CO', registry.get('report general-ledger'), {})


@pytest.mark.parametrize('verb', Export.reports())
def test_print_and_return_links_preserve_typed_filters(verb):
    cmd = registry.get('report ' + verb)
    inputs = _sample_filters(cmd)
    for key, value in list(inputs.items()):
        if value is True:
            inputs[key] = False
    for link in (Print.print_url('CO', verb, {**inputs, 'limit': 1, 'cursor': 'later'}),
                 Print.return_url('CO', verb, inputs)):
        assert Export.inputs_from(cmd, QueryParams(urlsplit(link).query)) == inputs
        assert 'f%3Acursor' not in link and 'f%3Alimit' not in link


@pytest.mark.parametrize('verb', Export.reports())
def test_all_registered_report_presenters_have_complete_views(print_site, browser, verb):
    cmd = registry.get('report ' + verb)
    raw = {name: ('2026-09-01' if name == 'date_from' else '2026-09-30')
           for name, field in cmd.input_model.model_fields.items() if field.is_required()}
    response = browser.get(Print.print_url(print_site.company, verb, raw))
    assert response.status_code == 200, response.text
    assert 'data-report-full="1"' in response.text and 'Complete filtered report' in response.text
    assert '<table' in response.text, verb
    assert response.headers['cache-control'] == 'no-store'
    assert 'id="statement-next-page"' not in response.text


def test_long_filtered_route_has_all_rows_totals_and_honest_failures(print_site, browser, monkeypatch):
    url = Print.print_url(print_site.company, 'general-ledger', {**PERIOD, 'account': print_site.bank})
    response = browser.get(url + '&f%3Alimit=1&f%3Acursor=forged-page')
    assert response.status_code == 200
    result = _shown(response)
    assert len(result['rows']) == result['count'] == 232
    assert [r['description'].split()[0] for r in result['rows'] if r['kind'] == 'posting'] == [f'PRINT-ROW-{i:03d}' for i in range(230)]
    assert result['rows'][0]['signed_balance']['amount'] == '10.00'
    assert result['rows'][-1]['signed_balance']['amount'] == '123457028.01'
    assert result['totals']['period_debits']['amount'] == '123457018.01'
    assert result['totals']['period_credits']['amount'] == '0.00'
    assert 'EXCLUDED-DATE' not in response.text and 'EXCLUDED-ACCOUNT' not in response.text
    assert 'All 232 matching rows' in response.text
    assert 'id="report-print-return"' in response.text
    assert 'Displayed-page copy only' not in response.text
    denied_basis = browser.get(url + '&f%3Abasis=cash')
    assert 'E_VALIDATION' in denied_basis.text and 'id="report-print-scope"' not in denied_basis.text
    monkeypatch.setattr(Export, 'PAGES', 0)
    capped = browser.get(url)
    assert 'exceeds the printable limit of 200 rows' in capped.text
    assert 'id="report-print-scope"' not in capped.text and 'PRINT-ROW-000' not in capped.text


def test_unauthorized_company_and_unknown_report_do_not_print(print_site):
    with TestClient(print_site.handle.app) as outsider:
        outsider.post('/login', json={'username': 'print-outsider', 'password': PASSWORD})
        response = outsider.get(Print.print_url(print_site.company, 'general-ledger', PERIOD))
        assert response.status_code >= 400
        assert 'Complete Report Company' not in response.text and 'PRINT-ROW' not in response.text
    with TestClient(print_site.handle.app) as anonymous:
        response = anonymous.get(Print.print_url(print_site.company, 'unknown', PERIOD))
        assert 'data-report-full="1"' not in response.text


@pytest.fixture
def print_live(print_site):
    from tests.test_row3_host import live
    yield from live.__wrapped__(print_site)


@pytest.mark.skipif(not CHROME.exists(), reason='Chrome is unavailable')
@pytest.mark.timeout(120)
def test_browser_full_filtered_report_desktop_phone_and_multipage_pdf(print_site, print_live, tmp_path):
    from pypdf import PdfReader
    b = _Cdp(tmp_path / 'chrome')
    evidence = print_site.directory / 'browser-evidence'
    evidence.mkdir(exist_ok=True)
    try:
        b.navigate(print_live + '/login')
        b.evaluate('''(() => {document.querySelector('[name=username]').value=%s;
            document.querySelector('[name=password]').value=%s;
            document.querySelector('form[hx-post="/login"]').requestSubmit();})()''' % (json.dumps(print_site.login), json.dumps(PASSWORD)))
        b.wait_for("!!document.querySelector('.nav-group')")
        b.navigate(f'{print_live}/c/{print_site.company}/report/general-ledger')
        fields = {**PERIOD, 'account': print_site.bank, 'limit': '50'}
        b.evaluate('''(() => {const f=document.querySelector('form[data-generated-form]');
            for(const [k,v] of Object.entries(%s)) f.elements.namedItem('f:'+k).value=v;
            f.querySelector('button[value=submit]').click();})()''' % json.dumps(fields))
        b.wait_for("document.querySelectorAll('#report-lines tbody tr[data-account]').length===50")
        assert b.evaluate("!!document.querySelector('#statement-next-page')")
        link = b.evaluate("document.querySelector('#report-print-all').getAttribute('href')")
        assert Export.inputs_from(registry.get('report general-ledger'), QueryParams(urlsplit(link).query)) == {**PERIOD, 'account': print_site.bank, 'basis': 'accrual'}
        b.evaluate("document.querySelector('#report-print-all').click()")
        b.wait_for("document.readyState==='complete' && !!document.querySelector('[data-report-full]')")
        assert b.evaluate("document.querySelectorAll('#report-lines tbody tr[data-account]').length") == 232
        assert b.evaluate("[...document.querySelectorAll('#report-lines .report-description')].map(n=>n.textContent).filter(t=>t.startsWith('PRINT-ROW')).map(t=>t.split(' ')[0])") == [f'PRINT-ROW-{i:03d}' for i in range(230)]
        assert not b.evaluate("document.body.innerText.includes('EXCLUDED-DATE') || document.body.innerText.includes('EXCLUDED-ACCOUNT')")
        assert b.evaluate("document.querySelector('[data-total=period_debits] dd').textContent") == '123457018.01 USD'
        for width in (1280, 390):
            b.viewport(width, 900)
            assert b.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1')
            assert b.evaluate("getComputedStyle(document.querySelector('#report-lines tbody tr')).display") == ('grid' if width == 390 else 'table-row')
            assert b.evaluate("document.querySelector('#report-print-dialog').getBoundingClientRect().width") >= 44
            assert b.evaluate("getComputedStyle(document.querySelector('form[data-generated-form]')).display") == 'none'
            (evidence / f'full-ledger-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {'format': 'png'})['data']))
            pdf = base64.b64decode(b.call('Page.printToPDF', {'printBackground': True, 'preferCSSPageSize': True})['data'])
            (evidence / f'full-ledger-{width}.pdf').write_bytes(pdf)
            pages = PdfReader(io.BytesIO(pdf)).pages
            assert len(pages) > 1
            text = '\n'.join(page.extract_text() for page in pages)
            compact = re.sub(r'\s+', '', text)
            for i in range(230):
                assert compact.count(f'PRINT-ROW-{i:03d}') == 1
            assert all('Account' in page.extract_text() and 'Running balance' in ' '.join(page.extract_text().split())
                       for page in pages if 'PRINT-' in page.extract_text())
            for value in ('Complete Report Company', 'General ledger', 'Accrual', 'USD', '2026-09-01',
                          '2026-09-30', 'Audit watermark', 'All 232 matching rows', '123457018.01', '123457028.01'):
                assert re.sub(r'\s+', '', value) in compact, (value, text[:1800])
            assert 'Print / save PDF' not in text and 'EXCLUDED-' not in text
        # Explicit print action, no automatic dialog; test wiring without opening a modal.
        b.evaluate("window.print=()=>{window.printRequested=true}; document.querySelector('#report-print-dialog').click()")
        assert b.evaluate('window.printRequested===true')
        back = b.evaluate("document.querySelector('#report-print-return').getAttribute('href')")
        assert Export.inputs_from(registry.get('report general-ledger'), QueryParams(urlsplit(back).query)) == {**PERIOD, 'account': print_site.bank, 'basis': 'accrual'}
        # A second presenter and an empty filtered result remain printable.
        b.navigate(print_live + Print.print_url(print_site.company, 'trial-balance', {'date_to': '2026-07-31', 'include_zero': False}))
        b.wait_for("document.readyState==='complete' && !!document.querySelector('[data-report-full]')")
        assert 'No matching report rows.' in b.evaluate('document.body.innerText')
        assert 'All 0 matching rows' in b.evaluate('document.body.innerText')
        empty_pdf = base64.b64decode(b.call('Page.printToPDF', {})['data'])
        (evidence / 'empty-trial-balance.pdf').write_bytes(empty_pdf)
        assert 'No matching report rows.' in ''.join(p.extract_text() for p in PdfReader(io.BytesIO(empty_pdf)).pages)
    finally:
        b.close()
