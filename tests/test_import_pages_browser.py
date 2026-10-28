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
               f' || !!document.querySelector("#import-workspace:not([data-before]) [data-import-error]")'
               ' || window.__failed', timeout=60)
    assert not b.evaluate('window.__failed'), 'the server failed: ' + str(b.evaluate('window.__failed'))
    assert b.evaluate('window.__sameDocument === true'), 'the form left the page instead of swapping through htmx'
    assert not b.evaluate('!!document.querySelector("[data-import-error]")'), b.evaluate(
        'document.querySelector("[data-import-error]").innerText')


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

    # File, then Check: the client's own words are guessed (Client, E-mail address, Job started); the credit column
    # is not, so the check is clean until it is mapped.
    _choose_file(b, '[data-import-file]', first)
    _click(b, '[data-import-check]', 'check')
    text = _text(b)
    assert 'encoding: cp1252 (guessed)' in text, text[-2000:]
    for header, path in (('Client', 'name'), ('E-mail address', 'email'), ('Job started', 'job_start'),
                         ('Credit', '')):
        assert b.evaluate(f'document.querySelector(\'[data-import-field="{header}"]\').value') == path, header
    # Columns as the file has them, three values each (`import check`'s samples), beside the field picker.
    assert b.evaluate('[...document.querySelectorAll(\'[data-import-column="Client"] .col-samples span\')]'
                      '.map(s => s.textContent)') == ['Café Lumière', 'Müller Bäckerei', 'Søren Hansen']
    _shot(b, width, '2-check-guessed')

    # The credit column mapped on the page, then checked again: the junk amount shows, by column, row and cell, and
    # nothing can preview.
    _set(b, '[data-import-field="Credit"]', 'credit_limit')
    _click(b, '[data-import-recheck-columns]', 'check')
    groups = b.evaluate('[...document.querySelectorAll("[data-problem-column]")].map(g => g.dataset.problemColumn)')
    assert groups == ['Credit'], (groups, _text(b)[-2000:])
    problems = b.evaluate('document.querySelector("[data-import-problems]").innerText')
    assert 'Row 4' in problems and '59?9,56' in problems, problems
    assert b.evaluate('!document.querySelector("[data-import-preview]")'), 'a file with problems cannot preview'
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
    # 'Due in 60' is created due in 60 days: the default is read from its words.
    assert b.evaluate('document.querySelector(\'[data-import-ref="Due in 60"] .days\').value') == '60'
    b.evaluate('''document.querySelector('[data-import-ref="Due in 60"] [data-ref-create]').click()''')
    _shot(b, width, '4-check-decide')
    # Kabe's rule is all or nothing: leaving the bad row out is asked for, by its own button.
    _click(b, '[data-import-partial]', 'preview')
    counts = b.evaluate('Object.fromEntries([...document.querySelectorAll("[data-count]")].map(e => '
                        '[e.dataset.count, +e.querySelector("strong").textContent]))')
    head = b.evaluate('document.querySelector("[data-import-confirm-head]").innerText')
    assert head == 'Import 4 customers and jobs (1 refused, left out)?', head
    assert counts.get('refused') == 1 and sum(counts.values()) == 5, (
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
    assert b.evaluate('document.querySelector("[data-import-undo-who]").innerText').startswith('Imported by ')
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
    assert b.evaluate('document.querySelector("[data-import-start]").innerText') == 'Import 5 vendors'
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


# ------------------------------------------------------------------ the review of increment 3 (r107-inc3-review.md)

def _site(register_browser, width=1440):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 900)
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    def write(name, payload):  # a read takes no reason
        reads = ('.show', '.query')
        return _command(b, env.site, name, payload,
                        **({} if name.endswith(reads) else {'X-Bookflow-Reason': 'Browser test'}))
    return env, b, base, write


def _paste_check(b, base, kind, text):
    b.navigate(f'{base}/import?kind={kind}')
    b.wait_for('!!document.querySelector("[data-import-paste]")', timeout=30)
    b.evaluate(f'''(() => {{ const a = document.querySelector('[data-import-paste]'); a.value = {json.dumps(text)};
        a.dispatchEvent(new Event('input', {{bubbles: true}})); }})()''')
    _click(b, '[data-import-check]', 'check')


def _submit(b, selector):
    """A click whose answer may be a refusal shown in place (the step stays)."""
    b.evaluate('document.querySelector("#import-workspace").dataset.before = "1"')
    b.evaluate(f'document.querySelector({json.dumps(selector)}).click()')
    b.wait_for('!!document.querySelector("#import-workspace:not([data-before])")', timeout=60)


def _ref(value, inner=''):
    return f'[data-import-ref="{value}"] {inner}'.strip()


def test_the_confirmation_is_true_and_a_wrong_answer_keeps_every_other(register_browser):
    """High 1, Medium 5, 6 and 7: rows left out by an answer were counted as imported; a typo in one answer reset
    every other and left a waiting run; 'Net45' defaulted to 30 days; a text holding "=" could not be decided."""
    env, b, base, write = _site(register_browser)
    text = ('Supplier\tPay terms\nDC 1\tNet 30\nDC 2\tA=B\nDC 3\tWeird terms\nDC 4\tWeird terms\nDC 5\tNet45\n'
            'DC 6\tPay on receipt\n')
    _paste_check(b, base, 'vendor', text)
    assert b.evaluate(f'document.querySelector(\'{_ref("Net45", ".days")}\').value') == '45'
    assert b.evaluate(f'document.querySelector(\'{_ref("Pay on receipt", ".days")}\').value') == '0'
    # "Use" picks from every term the person may read, not three suggestions.
    listed = b.evaluate(f'[...document.querySelector(\'{_ref("A=B", "[data-ref-target]")}\').list.options]'
                        '.map(o => o.value)')
    assert 'Net 30' in listed and len(listed) > 3, listed
    b.evaluate(f'''(() => {{
        document.querySelector('{_ref("Weird terms", "[data-ref-skip]")}').click();
        document.querySelector('{_ref("A=B", "[data-ref-map]")}').click();
        document.querySelector('{_ref("A=B", "[data-ref-target]")}').value = 'Nett 30';
        document.querySelector('{_ref("Net45", "[data-ref-create]")}').click();
        document.querySelector('{_ref("Pay on receipt", "[data-ref-create]")}').click(); }})()''')
    _submit(b, '[data-import-preview]')
    # The refusal is shown beside the answer it is about; every other answer is as it was; no run is left waiting.
    assert b.evaluate('document.querySelector("[data-import-step]").dataset.importStep') == 'check'
    assert 'Net 30' in b.evaluate(f'document.querySelector(\'{_ref("A=B", "[data-ref-error]")}\').innerText')
    assert b.evaluate(f'document.querySelector(\'{_ref("Weird terms", "[data-ref-skip]")}\').checked')
    assert b.evaluate(f'document.querySelector(\'{_ref("Net45", "[data-ref-create]")}\').checked')
    assert b.evaluate(f'document.querySelector(\'{_ref("A=B", "[data-ref-target]")}\').value') == 'Nett 30'
    _shot(b, 1440, 'review-answer-error')
    states = [r['state'] for r in write('import.query', {})['items']]
    assert 'waiting' not in states and 'previewed' not in states, states
    _set(b, _ref('A=B', '[data-ref-target]'), 'Net 30')
    _click(b, '[data-import-preview]', 'preview')
    head = b.evaluate('document.querySelector("[data-import-confirm-head]").innerText')
    assert head == 'Import 4 vendors (2 left out as you chose)?', head
    bar = b.evaluate('Object.fromEntries([...document.querySelectorAll("[data-count]")].map(e => '
                     '[e.dataset.count, +e.querySelector("strong").textContent]))')
    assert bar == {'create': 1, 'answered': 3, 'left_out': 2}, bar
    assert b.evaluate('document.querySelector("[data-import-start]").innerText') == 'Import 4 vendors'
    _shot(b, 1440, 'review-confirm-left-out')
    _click(b, '[data-import-start]', 'done')
    b.wait_for('!!document.querySelector("[data-import-result=done]")', timeout=60)
    assert b.evaluate('document.querySelector("[data-count=created] strong").textContent') == '4'
    assert write('term.show', {'term': 'Net45'})['due_days'] == 45
    assert write('term.show', {'term': 'Pay on receipt'})['due_days'] == 0
    assert write('vendor.show', {'vendor': 'DC 2'})['terms_id'] == write('term.show', {'term': 'Net 30'})['id']


def test_a_refusing_preview_offers_both_ways_on_and_hand_mapping_works(register_browser):
    """High 2 and 3: a preview refusing a row the check passed left a disabled button and no way on; "Map the
    columns below by hand" looped on "the preview needs decisions first"."""
    env, b, base, write = _site(register_browser)
    write('vendor.create', {'name': 'Old Supply', 'email': 'old@example.test'})
    _paste_check(b, base, 'vendor', 'Supplier\tEmail\tPhone\tCity\nOld Supply\tnew@example.test\t1\tX\n'
                 'New Supply\tn@example.test\t2\tY\n')
    _click(b, '[data-import-preview]', 'preview')
    assert b.evaluate('!document.querySelector("[data-import-start]")'), 'no disabled dead end'
    assert 'all or nothing' in b.evaluate('document.querySelector("[data-import-blocked]").innerText')
    assert b.evaluate('!!document.querySelector("[data-import-refused-form] [data-import-recheck]")')
    _shot(b, 1440, 'review-preview-refused')
    _click(b, '[data-import-partial]', 'preview')
    assert b.evaluate('document.querySelector("[data-import-start]").innerText') == 'Import 1 record'
    _click(b, '[data-import-start]', 'done')
    b.wait_for('!!document.querySelector("[data-import-result=done]")', timeout=60)
    assert write('vendor.show', {'vendor': 'New Supply'})['email'] == 'n@example.test'
    # A saved mapping that probably fits is offered; choosing to map by hand works without changing a column.
    b.evaluate('document.querySelector("[data-import-save-name]").value = "Supply list"')
    _click(b, '[data-import-save]', 'done')
    _paste_check(b, base, 'vendor', 'Supplier\tEmail\tPhone\tCity\tFax\nHand Supply\th@example.test\t3\tZ\t555\n')
    assert b.evaluate('!!document.querySelector(\'input[name=mapping][value=""]\')'), _text(b)[-1500:]
    b.evaluate('document.querySelector(\'input[name=mapping][value=""]\').click()')
    _set(b, '[data-import-field="Fax"]', '')
    _click(b, '[data-import-preview]', 'preview')
    _click(b, '[data-import-start]', 'done')
    b.wait_for('!!document.querySelector("[data-import-result=done]")', timeout=60)
    assert write('vendor.show', {'vendor': 'Hand Supply'})['email'] == 'h@example.test'


def test_cancel_keeps_undo_and_a_stale_confirm_says_nothing_new(register_browser):
    """Medium 4 and 9: a cancelled run that had made records offered no Undo; a confirm another run had already
    landed said "Import finished" and offered Save mapping and Undo for nothing."""
    env, b, base, write = _site(register_browser)
    text = 'Supplier\tCredit limit\n' + ''.join(f'Stop {i}\t{"12x" if i == 3 else "5"}\n' for i in range(1, 7))
    preview = write('import.preview', {'kind': 'vendor', 'content': text, 'partial': True, 'stop_after_refusals': 1})
    assert write('import.run', {'run': preview['run']['id']})['run']['state'] == 'stopped'
    b.navigate(f'{base}/import?run={preview["run"]["id"]}')
    b.wait_for('!!document.querySelector("[data-import-progress]")', timeout=30)
    assert 'already made stay until you undo' in b.evaluate(
        'document.querySelector("[data-import-cancel]").getAttribute("hx-confirm")')
    assert b.evaluate('!!document.querySelector("[data-import-undo-plan]")'), 'a stopped run offers Undo'
    b.evaluate('window.confirm = () => true')
    _click(b, '[data-import-cancel]', 'done')
    assert 'The 2 vendors already made stay until you undo them below' in b.evaluate(
        'document.querySelector("[data-import-message]").innerText')
    _shot(b, 1440, 'review-cancelled-undo')
    _click(b, '[data-import-undo-plan]', 'done')
    _click(b, '[data-import-undo-confirm]', 'done')
    b.wait_for('!!document.querySelector("[data-import-undone]")', timeout=60)
    names = [v['name'] for v in write('vendor.query', {})['items']]
    assert not [n for n in names if n.startswith('Stop ')], names  # undone: none of them is active
    # Two previews of one file; the second lands; the first's confirm then says nothing new was imported.
    again = 'Supplier\nStale 1\nStale 2\n'
    first = write('import.preview', {'kind': 'vendor', 'content': again})
    second = write('import.preview', {'kind': 'vendor', 'content': again})
    write('import.run', {'run': second['run']['id']})
    b.navigate(f'{base}/import?run={first["run"]["id"]}')
    b.wait_for('!!document.querySelector("[data-import-start]")', timeout=30)
    assert b.evaluate('document.querySelectorAll("[data-import-sample] .import-record").length') == 2, 'reload'
    _click(b, '[data-import-start]', 'done')
    b.wait_for('!!document.querySelector("[data-import-nothing-new]")', timeout=60)
    assert 'all rows were already in' in b.evaluate('document.querySelector("[data-import-nothing-new]").innerText')
    _shot(b, 1440, 'review-nothing-new')
    assert b.evaluate('!document.querySelector("[data-import-save-form]") && !document.querySelector("[data-import-undo]")')


def test_on_a_phone_every_target_is_tappable_and_the_questions_come_first(register_browser):
    """Low 11: 18 px radios and 17 px links; 66 problems in one long list; the questions below 8 column cards; the
    partial button flush against "Check again". Low 10: an oversize upload and a too-wide form refuse on the page."""
    env, b, base, write = _site(register_browser, 390)
    rows = ''.join(f'Phone {i}{": a" if i % 4 == 0 else ""}\t{"x" if i % 3 == 0 else "5"}\tOdd terms {i % 2}\n'
                   for i in range(1, 61))
    _paste_check(b, base, 'vendor', 'Supplier\tCredit limit\tPay terms\n' + rows)
    _fits(b)
    small = b.evaluate('''[...document.querySelectorAll('#import-workspace a, #import-workspace .choice')]
        .filter(e => e.offsetParent).map(e => [e.textContent.trim().slice(0, 30), e.getBoundingClientRect().height])
        .filter(([, h]) => h < 43.5)''')
    assert small == [], small
    groups = b.evaluate('[...document.querySelectorAll("[data-problem-column]")].map(g => [g.open, '
                        'g.querySelector("summary").innerText])')
    assert groups[0][0] and not any(open_ for open_, _ in groups[1:]) and 'problem' in groups[0][1], groups
    top = lambda sel: b.evaluate(f'document.querySelector({json.dumps(sel)}).getBoundingClientRect().top')
    assert top('[data-import-decisions]') < top('[data-import-columns]')
    gap = b.evaluate('''(() => { const a = document.querySelector('[data-import-recheck]').getBoundingClientRect();
        const p = document.querySelector('[data-import-partial]').getBoundingClientRect(); return p.top - a.bottom; })()''')
    assert gap >= 40, gap
    _shot(b, 390, 'phone-check-problems')
    # An upload over the limit is refused before its body is read, on the page.
    out = b.evaluate(f'''(async () => {{ const form = new FormData(); form.append('action', 'check');
        form.append('kind', 'vendor'); form.append('file', new Blob([new Uint8Array(19000000)]), 'big.csv');
        const r = await fetch('{base}/import', {{method: 'POST', body: form,
            headers: {{'HX-Request': 'true', 'X-Bookflow-Workbench': '1'}}}});
        return [r.status, await r.text()]; }})()''', await_promise=True)
    assert out[0] == 200 and 'over 16 MB' in out[1], out[0]
    # A file too wide for the page's form says so on the page.
    wide = '\t'.join(f'C{i}' for i in range(1400)) + '\n' + '\t'.join('v' for _ in range(1400)) + '\n'
    _paste_check(b, base, 'vendor', wide)
    _submit(b, '[data-import-recheck-columns]')
    assert 'too wide' in b.evaluate('document.querySelector("[data-import-error]").innerText')
