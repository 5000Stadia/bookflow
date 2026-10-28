"""R107 increment 3: the import pages in real headless Chrome, at 1440 and 390 (design/bulk-import.md §9, §11).

A person imports criterion 1's messy customer file (Windows-1252, semicolons, renamed headers, day-first dates,
parenthesized negatives with decimal commas, two unknown terms) from the browser: the first upload's check shows
every problem at once by column with row numbers and cell text; the columns are mapped on the page and a corrected
file uploaded; the unknown terms are answered on the page (one used, one created); the preview shows the counts and a
sample; Import runs it; the refused row downloads as a CSV whose formula cell is guarded; Undo reverses the whole
import as one unit. A pasted table (vendors) goes the same way, and a run left mid-way resumes when its page is
opened again. Every post is a real click through htmx (the page never navigates). With BF_IMPORT_SHOTS set to a
folder, every step is saved there at each width.
"""
import base64
import csv
import io
import json
import os
import time
from pathlib import Path

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

HEADER = 'Client;E-mail address;Telephone;Pay terms;Credit;Job started;Town;Job of\r\n'
ROWS = [
    'Café Lumière;cafe@example.test;0412 555 010;Net 30 days;(1.250,00);;Lyon;',
    'Müller Bäckerei;mueller@example.test;0049 30 1234;Net 30;2.500,00;;Berlin;',
    'Søren Hansen;soren@example.test;;Due in 60;(300,00);;Aarhus;',
    'Zoë Àlvarez;zoe@example.test;0611 22 33;Net 30 days;=1+2;;Sevilla;',
    'Terrace fit-out;;;Net 30 days;;25/03/2026;Lyon;Café Lumière',
]
JUNK = 'Ölmühle Kern;kern@example.test;;Net 30;59?9,56;;Graz;'


def _file(folder: Path, name: str, rows) -> Path:
    path = folder / name
    path.write_bytes((HEADER + '\r\n'.join(rows) + '\r\n').encode('cp1252'))
    return path


def _text(b):
    return b.evaluate('document.body.innerText')


def _fits(b):
    assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), b.evaluate(
        '[...document.querySelectorAll("body *")].filter(e => e.getBoundingClientRect().right > innerWidth + 1)'
        '.slice(0, 5).map(e => e.tagName + "." + e.className).join(" ")')


def _shot(b, width, name):
    if width == 390:
        _fits(b)
    folder = os.environ.get('BF_IMPORT_SHOTS')
    if not folder:
        return
    Path(folder).mkdir(parents=True, exist_ok=True)
    b.evaluate('(() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); })()')
    height = b.evaluate('Math.max(innerHeight, document.documentElement.scrollHeight)')
    (Path(folder) / f'{name}-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {
        'format': 'png', 'captureBeyondViewport': True,
        'clip': {'x': 0, 'y': 0, 'width': width, 'height': height, 'scale': 1}})['data']))


def _choose_file(b, selector, path):
    root = b.call('DOM.getDocument', {'depth': 0})['root']['nodeId']
    node = b.call('DOM.querySelector', {'nodeId': root, 'selector': selector})['nodeId']
    assert node, selector
    b.call('DOM.setFileInputFiles', {'files': [str(path)], 'nodeId': node})


def _click(b, selector, step):
    """A real click on a submit button; the answer swaps the workspace in place (htmx), never a navigation."""
    b.evaluate('''(() => { window.__sameDocument = true; window.__failed = null;
        document.body.addEventListener('htmx:responseError', e => { window.__failed = e.detail.xhr.status; }); })()''')
    b.evaluate('document.querySelector("#import-workspace").dataset.before = "1"')
    b.evaluate(f'document.querySelector({json.dumps(selector)}).click()')
    b.wait_for(f'!!document.querySelector("[data-import-step={step}]:not([data-before])")'
               f' || !!document.querySelector("[data-import-error]") || window.__failed', timeout=60)
    assert not b.evaluate('window.__failed'), 'the server failed: ' + str(b.evaluate('window.__failed'))
    assert b.evaluate('window.__sameDocument === true'), 'the form left the page instead of swapping through htmx'
    assert not b.evaluate('!!document.querySelector("[data-import-error]")'), _text(b)[-2000:]


def _set(b, selector, value):
    b.evaluate(f'''(() => {{ const e = document.querySelector({json.dumps(selector)});
        e.value = {json.dumps(value)}; e.dispatchEvent(new Event('change', {{bubbles: true}})); }})()''')


def _downloaded(b, folder):
    for _ in range(200):
        done = [p for p in folder.iterdir() if p.suffix == '.csv' and p.stat().st_size]
        if done and not any(p.suffix == '.crdownload' for p in folder.iterdir()):
            return done[0]
        time.sleep(0.05)
    raise AssertionError(f'nothing downloaded: {sorted(p.name for p in folder.iterdir())}; ' + _text(b)[-800:])


@pytest.mark.parametrize('width', (1440, 390))
def test_a_person_imports_the_messy_customer_file_and_undoes_it(register_browser, tmp_path, width):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    run = lambda name, payload: _command(b, env.site, name, payload)
    b.viewport(width, 900)
    first = _file(tmp_path, 'customers.csv', ROWS[:3] + [JUNK, ROWS[4]])
    fixed = _file(tmp_path, 'customers-fixed.csv', ROWS)

    # The customer list links to the import page.
    b.navigate(f'{base}/customer')
    b.wait_for('!!document.querySelector("[data-list-import]")', timeout=30)
    b.evaluate('document.querySelector("[data-list-import]").click()')
    b.wait_for('!!document.querySelector("[data-import-step=file]")', timeout=30)
    assert b.evaluate('document.querySelector("[data-import-kind]").value') == 'customer'
    _shot(b, width, '1-file')

    # File, then Check: the guessed mapping lacks the name, and the junk amount is shown with its row and cell text.
    _choose_file(b, '[data-import-file]', first)
    _click(b, '[data-import-check]', 'check')
    text = _text(b)
    assert 'encoding: cp1252 (guessed)' in text, text[-2000:]
    problems = b.evaluate('document.querySelector("[data-import-problems]").innerText')
    assert 'Name' in problems or 'name' in problems, problems
    assert b.evaluate('!document.querySelector("[data-import-preview]")'), 'a file with problems cannot preview'
    # Columns as the file has them, three values each (`import check`'s samples), beside the field picker.
    assert b.evaluate('[...document.querySelectorAll(\'[data-import-column="Client"] .col-samples span\')]'
                      '.map(s => s.textContent)') == ['Café Lumière', 'Müller Bäckerei', 'Søren Hansen']
    _shot(b, width, '2-check-problems')

    # Columns mapped on the page, then checked again: now only the junk amount is left, by column, row and cell.
    _set(b, '[data-import-field="Client"]', 'name')
    _set(b, '[data-import-field="E-mail address"]', 'email')
    _set(b, '[data-import-field="Credit"]', 'credit_limit')
    _set(b, '[data-import-field="Job started"]', 'job_start')
    _click(b, '[data-import-recheck-columns]', 'check')
    groups = b.evaluate('[...document.querySelectorAll("[data-problem-column]")].map(g => g.dataset.problemColumn)')
    assert groups == ['Credit'], (groups, _text(b)[-2000:])
    problems = b.evaluate('document.querySelector("[data-import-problems]").innerText')
    assert 'Row 4' in problems and '59?9,56' in problems, problems
    _shot(b, width, '3-check-junk')

    # The corrected file (the mapping carries over); its new formula row is the one problem left.
    _choose_file(b, '[data-import-reupload] [data-import-file]', fixed)
    _click(b, '[data-import-recheck]', 'check')
    problems = b.evaluate('document.querySelector("[data-import-problems]").innerText')
    assert 'Row 4' in problems and '=1+2' in problems and '59?9' not in problems, problems
    assert b.evaluate('document.querySelector("[data-import-field=Client]").value') == 'name'
    # The unknown terms are answered here, beside the problems: 'Net 30 days' is Net 30 (suggested); 'Due in 60'
    # is created, due in 60 days.
    refs = b.evaluate('[...document.querySelectorAll("[data-import-ref]")].map(e => e.dataset.importRef)')
    assert sorted(refs) == ['Due in 60', 'Net 30 days'], refs
    net30 = '[data-import-ref="Net 30 days"]'
    assert b.evaluate(f'document.querySelector(\'{net30} [data-ref-target]\').value') == 'Net 30'
    b.evaluate('''(() => { const li = document.querySelector('[data-import-ref="Due in 60"]');
        li.querySelector('[data-ref-create]').click(); li.querySelector('.days').value = '60'; })()''')
    _shot(b, width, '4-check-decide')
    # Kabe's rule is all or nothing: leaving the bad row out is asked for, by its own button.
    _click(b, '[data-import-partial]', 'preview')
    counts = b.evaluate('Object.fromEntries([...document.querySelectorAll("[data-count]")].map(e => '
                        '[e.dataset.count, +e.querySelector("strong").textContent]))')
    assert counts.get('refused') == 1 and sum(counts.values()) == 5 and 'Import 4 records' in _text(b), (
        counts, b.evaluate('document.querySelector("[data-import-refused]")?.innerText'))
    assert b.evaluate('!!document.querySelector("[data-import-sample]")')
    _shot(b, width, '5-preview-confirm')

    # Run: the one confirmation, then the progress card, then Done.
    _click(b, '[data-import-start]', 'done')
    b.wait_for('!!document.querySelector("[data-import-result=done]")', timeout=60)
    assert 'run=' in b.evaluate('location.search'), 'a reload must come back to the run'
    _shot(b, width, '6-done')
    cafe = run('customer.show', {'customer': 'Café Lumière'})
    assert cafe['credit_limit']['amount'] == '-1250.00' and cafe['email'] == 'cafe@example.test'
    job = run('customer.show', {'customer': 'Café Lumière:Terrace fit-out'})
    assert job['job_start'] == '2026-03-25' and job['parent_id'] == cafe['id']  # day-first, read as written
    assert cafe['terms_id'] == run('term.show', {'term': 'Net 30'})['id']
    assert run('term.show', {'term': 'Due in 60'})['due_days'] == 60
    assert run('customer.show', {'customer': 'Søren Hansen'})['billing_address']['city'] == 'Aarhus'

    # The refused row downloads from a real click; the spreadsheet would run no formula.
    folder = tmp_path / 'downloads'
    folder.mkdir()
    b.call('Browser.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': str(folder)})
    b.evaluate('document.querySelector("[data-import-refused-download]").click()')
    content = _downloaded(b, folder).read_bytes().decode('utf-8-sig')
    rows = list(csv.reader(io.StringIO(content)))
    assert rows[0][:3] == ['Client', 'E-mail address', 'Telephone'] and len(rows) == 2, rows
    assert rows[1][0] == 'Zoë Àlvarez' and rows[1][4] == "'=1+2", rows
    assert not [c for r in rows for c in r if c[:1] in ('=', '+', '@') or (c[:1] == '-' and not c[1:2].isdigit())]

    # Save the mapping, then Undo the whole import as one unit.
    b.evaluate('document.querySelector("[data-import-save-name]").value = "Old system customers"')
    _click(b, '[data-import-save]', 'done')
    assert 'Saved as' in b.evaluate('document.querySelector("[data-import-message]").innerText')
    _click(b, '[data-import-undo-plan]', 'done')
    assert b.evaluate('!!document.querySelector("[data-import-undo-confirm]")'), _text(b)[-1500:]
    _shot(b, width, '7-undo-confirm')
    _click(b, '[data-import-undo-confirm]', 'done')
    b.wait_for('!!document.querySelector("[data-import-undone]")', timeout=60)
    _shot(b, width, '8-undone')
    shown = run('customer.show', {'customer': cafe['id']})
    assert shown['active'] is False and shown['name'].startswith('Café Lumière (reversed '), shown


@pytest.mark.parametrize('width', (1440, 390))
def test_a_pasted_vendor_table_imports_and_a_run_left_mid_way_resumes(register_browser, width):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    run = lambda name, payload: _command(b, env.site, name, payload)
    write = lambda name, payload: _command(b, env.site, name, payload, **{'X-Bookflow-Reason': 'Browser test'})
    b.viewport(width, 900)
    pasted = ('Supplier\tEmail\tPay terms\tCity\n'
              + ''.join(f'Pasted Supply {n}\tsupply{n}@example.test\tNet 30\tSpringfield\n' for n in range(1, 6)))
    b.navigate(f'{base}/import?kind=vendor')
    b.wait_for('!!document.querySelector("[data-import-paste]")', timeout=30)
    b.evaluate(f'''(() => {{ const area = document.querySelector('[data-import-paste]');
        area.value = {json.dumps(pasted)}; area.dispatchEvent(new Event('input', {{bubbles: true}})); }})()''')
    # The pasted table shows at once, before anything is posted.
    assert b.evaluate('document.querySelectorAll("[data-paste-preview] tbody tr").length') == 5
    assert 'Pasted Supply 1' in b.evaluate('document.querySelector("[data-paste-preview]").innerText')
    _shot(b, width, 'paste-1-file')
    _click(b, '[data-import-check]', 'check')
    assert b.evaluate('!document.querySelector("[data-import-problems]")'), _text(b)[-1500:]
    _shot(b, width, 'paste-2-check')
    _click(b, '[data-import-preview]', 'preview')
    assert 'Import 5 records' in b.evaluate('document.querySelector("[data-import-start]").innerText')
    _shot(b, width, 'paste-3-preview')
    url = b.evaluate('location.href')
    assert 'run=' in url
    run_id = url.split('run=')[1].split('&')[0]

    # The page was closed after one row: opening it again resumes the run where it stopped, to the end.
    out = write('import.run', {'run': run_id, 'limit': 1})
    assert out['run']['state'] == 'running' and out['counts']['created'] == 1
    b.navigate(url)
    b.wait_for('!!document.querySelector("[data-import-result=done]")', timeout=60)
    assert b.evaluate('document.querySelector("[data-count=created] strong").textContent') == '5'
    _shot(b, width, 'paste-4-done')
    assert run('vendor.show', {'vendor': 'Pasted Supply 5'})['email'] == 'supply5@example.test'
