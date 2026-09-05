"""Record-local annotations through the shared workbench and real Chrome."""
from __future__ import annotations

import html
import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_actor
from tests.test_row3_host import hosted
from tests.test_row5_workbench_forms import _browser
from tests.test_row5_browser_acceptance import CHROME, PASSWORD, _Cdp, browser_site


def _config(page):
    assert page.status_code == 200, page.text
    match = re.search(r"data-annotations='([^']+)'", page.text)
    assert match, page.text
    return json.loads(html.unescape(match[1]))


def test_shared_panel_on_record_pages_forms_and_readonly(hosted, root):
    browser = _browser(hosted)
    company = hosted.company_id
    paths = [("company", "self", "company_info", company)]
    for noun in ("account", "customer", "vendor", "item", "term"):
        row = hosted.ok(noun + '.list', company=company)['items'][0]
        paths.append((noun, row['id'], noun, row['id']))
    for noun, selector, kind, key in paths:
        path = f'/c/{company}/{noun}/{selector}'
        for suffix in ('', '/update'):
            page = browser.get(path + suffix)
            config = _config(page)
            assert config['target'] == {'record_type': kind, 'record_id': key}
            assert config['allowed']['note add'] and config['allowed']['attachment add']
            assert page.text.count('data-annotations=') == 1
            assert re.search(r'/static/annotations.js\?v=[0-9a-f]{16}', page.text)
            if suffix:
                # Panel forms are siblings of the master form, never nested within it.
                assert page.text.index('</form>', page.text.index('data-generated-form')) < page.text.index('data-annotations=')
    assert 'data-annotations=' not in browser.get(f'/c/{company}/customer/create').text
    make_actor(root, 'annotation-reader', company_role=(company, 'readonly'))
    token = hosted.ok('token.issue', {'user': 'annotation-reader', 'label': 'reader'})
    reader = TestClient(hosted.handle.app)
    page = reader.get(f'/c/{company}/company/self', headers={'Authorization': 'Bearer ' + token['secret']})
    config = _config(page)
    assert config['allowed']['note list'] and config['allowed']['attachment get']
    assert not any(config['allowed'][name] for name in ('note add', 'note edit', 'attachment add', 'attachment unlink'))
    assert 'data-note-add' not in page.text and 'data-file-add' not in page.text


def _login(browser, site):
    browser.navigate(site.base_url + '/login')
    browser.evaluate(f"""(() => {{
      document.querySelector('[name="username"]').value = {json.dumps(site.login)};
      document.querySelector('[name="password"]').value = {json.dumps(PASSWORD)};
      document.querySelector('form[hx-post="/login"]').requestSubmit();
    }})()""")
    browser.wait_for("!!document.querySelector('.group-grid')")


def _api(browser, site, name, raw):
    return browser.evaluate(f"""(async () => {{
      const r = await fetch('/companies/{site.company_id}/commands/{name}', {{
        method: 'POST', headers: {{'X-Bookflow-Workbench':'1','Content-Type':'application/json'}},
        body: JSON.stringify({json.dumps(raw)})}});
      const value = await r.json(); if (!r.ok) throw Error(JSON.stringify(value)); return value;
    }})()""", await_promise=True)


@pytest.mark.skipif(not CHROME.is_file(), reason='real Chrome is not installed')
@pytest.mark.timeout(120)
def test_real_browser_notes_files_conflicts_drafts_and_narrow_keyboard(browser_site, tmp_path):
    browser = _Cdp(tmp_path / 'chrome-annotations')
    site = browser_site
    download_dir = tmp_path / 'downloads'
    download_dir.mkdir()
    pdf = tmp_path / 'receipt.pdf'
    pdf.write_bytes(b'%PDF-1.4\n% annotation round trip\n%%EOF\n')
    try:
        _login(browser, site)
        customer = _api(browser, site, 'customer.list', {})['items'][0]
        target = {'record_type': 'customer', 'record_id': customer['id']}
        # Real bounded traversal: a second note page and a second chronological feed page.
        for i in range(21):
            _api(browser, site, 'note.add', {**target, 'body': f'Prior note {i}'})
        url = f"{site.base_url}/c/{site.company_id}/customer/{customer['id']}/update"
        browser.navigate(url)
        browser.wait_for("document.querySelector('[data-section=notes] [data-more]')?.hidden === false")
        for width in (280, 390, 1280):
            browser.viewport(width, 850)
            layout = browser.evaluate("""(() => ({
              overflow: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - innerWidth,
              outside: [...document.querySelectorAll('.annotations button,.annotations input,.annotations textarea')]
                .filter(e => e.getClientRects().length && (e.getBoundingClientRect().left < 0 || e.getBoundingClientRect().right > innerWidth + 1))
                .map(e => e.outerHTML)
            }))()""")
            assert layout['overflow'] <= 1 and layout['outside'] == [], layout
        browser.viewport(390, 850)
        browser.evaluate("document.querySelector('[data-section=notes] [data-more]').click()")
        browser.wait_for("document.querySelectorAll('[data-note-id]').length >= 21 && document.querySelector('[data-section=notes] [data-more]').hidden")
        browser.evaluate("document.querySelector('[data-section=activity] [data-more]').click()")
        browser.wait_for("document.querySelectorAll('[data-section=activity] li').length >= 21")
        dates = browser.evaluate("[...document.querySelectorAll('[data-section=activity] time')].map(e => e.dateTime)")
        assert dates == sorted(dates)
        browser.evaluate("document.querySelector('[name=\"f:name\"]').value = 'Unsaved master name'")
        unsafe = '<img src=x onerror="window.annotationUnsafe=true"> & <script>bad()</script>'
        browser.evaluate(f"document.querySelector('#annotation-note').value = {json.dumps(unsafe)}")
        # Reach and activate the native submit button using the keyboard.
        browser.evaluate("document.querySelector('#annotation-note').focus()")
        browser.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'Tab', 'code': 'Tab', 'windowsVirtualKeyCode': 9})
        browser.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'Tab', 'code': 'Tab', 'windowsVirtualKeyCode': 9})
        assert browser.evaluate("document.activeElement.textContent") == 'Add note'
        browser.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13, 'text': '\r'})
        browser.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13})
        browser.wait_for("document.querySelector('[data-note-add] [data-status]').textContent && !document.querySelector('[data-note-add]').hasAttribute('aria-busy')")
        assert browser.evaluate("document.querySelector('[data-note-add] [data-status]').textContent") == 'Note added.'
        browser.wait_for(f"[...document.querySelectorAll('[data-note-id] .annotation-text')].some(e => e.textContent === {json.dumps(unsafe)})")
        assert not browser.evaluate("!!window.annotationUnsafe || !!document.querySelector('.annotations img,.annotations script:not([src])')")
        note = next(x for x in _api(browser, site, 'note.list', target)['items'] if x['body'] == unsafe)
        browser.evaluate(f"document.querySelector('[data-note-id=\"{note['id']}\"] button').click()")
        browser.evaluate("document.querySelector('[data-note-edit] textarea').value = 'My unsaved note draft'")
        _api(browser, site, 'note.edit', {'note': note['id'], 'expected_version': note['version'], 'body': 'Another writer'})
        browser.evaluate("document.querySelector('[data-note-edit]').requestSubmit()")
        browser.wait_for("document.querySelector('[data-note-edit] [role=status]').dataset.failed === 'true'")
        assert browser.evaluate("document.querySelector('[data-note-edit] textarea').value") == 'My unsaved note draft'
        browser.evaluate("document.querySelector('[data-section=notes] [data-refresh]').click()")
        assert browser.evaluate("document.querySelector('[data-note-edit] textarea').value") == 'My unsaved note draft'
        # Upload via the actual chooser and raw-body transfer route.
        node = browser.call('DOM.getDocument')['root']['nodeId']
        file_node = browser.call('DOM.querySelector', {'nodeId': node, 'selector': '#annotation-file'})['nodeId']
        browser.call('DOM.setFileInputFiles', {'nodeId': file_node, 'files': [str(pdf)]})
        browser.evaluate("document.querySelector('#annotation-caption').value = 'Receipt caption'; document.querySelector('[data-file-add]').requestSubmit()")
        browser.wait_for("document.querySelector('[data-file-add] [data-status]').textContent === 'File attached.'")
        browser.wait_for("document.querySelector('[data-link-id]')?.textContent.includes('receipt.pdf') && document.querySelector('[data-section=files] [data-list-status]').textContent.includes('loaded')")
        browser.call('Browser.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': str(download_dir)})
        # Exercise the local-HTTP SHA-256 fallback even on secure loopback Chrome.
        browser.evaluate("Object.defineProperty(window.crypto, 'subtle', {value: undefined, configurable: true})")
        browser.evaluate("document.querySelector('[data-link-id] button').click()")
        browser.wait_for("document.querySelector('[data-link-id] [role=status]').textContent && !document.querySelector('[data-link-id]').hasAttribute('aria-busy')")
        assert 'File verified' in browser.evaluate("document.querySelector('[data-link-id] [role=status]').textContent")
        deadline = time.monotonic() + 5
        while not (download_dir / pdf.name).exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert (download_dir / pdf.name).read_bytes() == pdf.read_bytes()
        # A corrupt download must never reach the browser's save click.
        browser.evaluate("""window.annotationFetch = window.fetch;
          window.fetch = async (url, options) => String(url).endsWith('/attachment.get')
            ? new Response('wrong', {headers:{'Content-Length':'5','X-Bookflow-SHA256':'0'.repeat(64)}})
            : annotationFetch(url, options);
          document.querySelector('[data-link-id] button').click();""")
        browser.wait_for("document.querySelector('[data-link-id] [role=status]').dataset.failed === 'true'")
        assert 'No file was saved' in browser.evaluate("document.querySelector('[data-link-id] [role=status]').textContent")
        # Failed upload retains caption, explicitly clears the file for reselection.
        browser.evaluate("""window.fetch = async (url, options) => String(url).endsWith('/attachment.add')
          ? new Response(JSON.stringify({code:'E_IO',message:'Interrupted upload'}), {status:503}) : annotationFetch(url, options);""")
        browser.call('DOM.setFileInputFiles', {'nodeId': file_node, 'files': [str(pdf)]})
        browser.evaluate("document.querySelector('#annotation-caption').value = 'Keep caption'; document.querySelector('[data-file-add]').requestSubmit()")
        browser.wait_for("document.querySelector('[data-file-add] [data-status]').dataset.failed === 'true'")
        assert browser.evaluate("document.querySelector('#annotation-caption').value") == 'Keep caption'
        assert browser.evaluate("document.querySelector('#annotation-file').files.length") == 0
        assert 'Select the file again' in browser.evaluate("document.querySelector('[data-file-add] [data-status]').textContent")
        browser.evaluate("window.fetch = annotationFetch; window.confirm = () => false; document.querySelectorAll('[data-link-id] button')[1].click()")
        assert browser.evaluate("!!document.querySelector('[data-link-id]')")
        browser.evaluate("window.confirm = () => true; document.querySelectorAll('[data-link-id] button')[1].click()")
        browser.wait_for("![...document.querySelectorAll('[data-link-id]')].some(e => e.textContent.includes('receipt.pdf'))")
        assert browser.evaluate("document.querySelector('[name=\"f:name\"]').value") == 'Unsaved master name'
        assert browser.evaluate("document.querySelector('[data-note-edit] textarea').value") == 'My unsaved note draft'
        assert browser.evaluate('location.href') == url
    finally:
        browser.close()


@pytest.mark.skipif(not CHROME.is_file(), reason='real Chrome is not installed')
@pytest.mark.timeout(120)
def test_browser_company_account_file_pages_pending_and_refresh_race(browser_site, tmp_path):
    browser = _Cdp(tmp_path / 'chrome-annotation-pages')
    site = browser_site
    try:
        _login(browser, site)
        account = _api(browser, site, 'account.list', {})['items'][0]
        for noun, key in [('company', site.company_id), ('account', account['id'])]:
            browser.navigate(f'{site.base_url}/c/{site.company_id}/{noun}/{key}')
            browser.wait_for("document.querySelector('[data-section=notes] [data-list-status]')?.textContent.includes('loaded') || document.querySelector('[data-section=notes] [data-list-status]')?.textContent === 'No entries.'")
            browser.evaluate(f"document.querySelector('#annotation-note').value = 'Browser {noun} note'; document.querySelector('[data-note-add]').requestSubmit()")
            browser.wait_for("document.querySelector('[data-note-add] [data-status]').textContent === 'Note added.'")
            # Actual transfer route on both additional required targets.
            response = browser.evaluate("""(async () => {
              const config = JSON.parse(document.querySelector('[data-annotations]').dataset.annotations);
              const metadata = {...config.target, original_filename:'page.pdf',media_type:'application/pdf',caption:'Page file'};
              const r = await fetch('/companies/'+config.company+'/transfers/attachment.add', {
                method:'POST', headers:{'X-Bookflow-Workbench':'1','Content-Type':'application/octet-stream',
                  'X-Bookflow-Input':btoa(JSON.stringify(metadata)).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'')},
                body:'%PDF-1.4\\nPage sample\\n%%EOF\\n'});
              return {status:r.status, value:await r.json()};
            })()""", await_promise=True)
            assert response['status'] == 200, response
            browser.evaluate("document.querySelector('[data-section=files] [data-refresh]').click()")
            browser.wait_for("[...document.querySelectorAll('[data-link-id]')].some(e => e.textContent.includes('Page file'))")
        # Seed enough distinct real links to force the files continuation control.
        statuses = browser.evaluate("""(async () => {
          const config = JSON.parse(document.querySelector('[data-annotations]').dataset.annotations), statuses=[];
          for (let i=0;i<21;i++) {
            const metadata = {...config.target,original_filename:'page-'+i+'.txt',caption:'Paged '+i};
            const r = await fetch('/companies/'+config.company+'/transfers/attachment.add', {method:'POST',
              headers:{'X-Bookflow-Workbench':'1','Content-Type':'application/octet-stream',
                'X-Bookflow-Input':btoa(JSON.stringify(metadata)).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'')},body:'Unique file '+i});
            statuses.push(r.status); await r.text();
          } return statuses;
        })()""", await_promise=True, timeout=30)
        assert statuses == [200] * 21
        browser.evaluate("document.querySelector('[data-section=files] [data-refresh]').click()")
        browser.wait_for("!document.querySelector('[data-section=files] [data-more]').hidden")
        browser.evaluate("document.querySelector('[data-section=files] [data-more]').click()")
        browser.wait_for("document.querySelectorAll('[data-link-id]').length >= 22 && document.querySelector('[data-section=files] [data-more]').hidden")
        # Delay a write: busy feedback cannot announce success or clear input early.
        browser.evaluate("""window.realAnnotationFetch=fetch;
          window.fetch = (url,options) => String(url).endsWith('/note.add')
            ? new Promise(resolve => {window.finishAnnotation = () => resolve(new Response(JSON.stringify({code:'E_IO',message:'Connection failed'}),{status:503}));})
            : realAnnotationFetch(url,options);
          document.querySelector('#annotation-note').value='Pending note draft';
          document.querySelector('[data-note-add]').requestSubmit();""")
        assert browser.evaluate("document.querySelector('[data-note-add]').getAttribute('aria-busy')") == 'true'
        assert browser.evaluate("document.querySelector('#annotation-note').value") == 'Pending note draft'
        assert browser.evaluate("document.querySelector('[data-note-add] [data-status]').textContent") == 'Adding note…'
        browser.evaluate('finishAnnotation()')
        browser.wait_for("document.querySelector('[data-note-add] [data-status]').dataset.failed === 'true'")
        assert browser.evaluate("document.querySelector('#annotation-note').value") == 'Pending note draft'
        # Start a read, then open an edit before that read completes.
        browser.evaluate("""window.fetch=(url,options) => String(url).endsWith('/note.list')
          ? new Promise(resolve => {window.finishList = () => realAnnotationFetch(url,options).then(resolve);})
          : realAnnotationFetch(url,options);
          document.querySelector('[data-section=notes] [data-refresh]').click();
          document.querySelector('[data-note-id] button').click();
          document.querySelector('[data-note-edit] textarea').value='Draft opened during refresh';
          finishList();""")
        browser.wait_for("document.querySelector('[data-section=notes] [data-list-status]').textContent.includes('Your draft is unchanged')")
        assert browser.evaluate("document.querySelector('[data-note-edit] textarea').value") == 'Draft opened during refresh'
        browser.evaluate("window.fetch=realAnnotationFetch;document.querySelector('[data-note-edit]').requestSubmit()")
        browser.wait_for("document.querySelector('[data-section=notes] [data-list-status]').textContent === 'Note saved.'")
        assert browser.evaluate("[...document.querySelectorAll('[data-note-id] .annotation-text')].some(e => e.textContent==='Draft opened during refresh')")
        assert browser.evaluate("document.querySelector('#annotation-note').value") == 'Pending note draft'
        # A same-size corrupt download isolates the checksum check from the size check.
        browser.evaluate("""window.fetch=async (url,options) => {
          const r=await realAnnotationFetch(url,options);
          if (!String(url).endsWith('/attachment.get')) return r;
          const bytes=new Uint8Array(await r.arrayBuffer()); bytes[0]^=1;
          return new Response(bytes,{headers:r.headers});
        }; document.querySelector('[data-link-id] button').click();""")
        browser.wait_for("document.querySelector('[data-link-id] [role=status]').dataset.failed==='true'")
        assert 'checksum verification failed' in browser.evaluate("document.querySelector('[data-link-id] [role=status]').textContent")
    finally:
        browser.close()
