"""v1.6 browser fixes, in a real browser at phone width.

The ticking page shows only the reconcile controls (no raw bulk/filter fields under them) and
still saves and finishes; a chosen statement file lands in the import's text area with its line
breaks; the backups list fits a 390 px phone.
"""
import json

import pytest

from tests.test_reconciliation_window_browser import _reach_the_ticking_page, _set, _submit
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_service_sales_browser import _contained

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
PHONE = 390


def _shown(b, name):
    """Whether the control named `name` is visible on the page."""
    return b.evaluate(f'''(() => {{const e=document.getElementsByName({json.dumps(name)})[0];
        return !!e && !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);}})()''')


def test_the_ticking_page_shows_only_its_own_controls_and_still_finishes(register_browser):
    env, b = register_browser, register_browser.browser
    _reach_the_ticking_page(env, b, PHONE)
    for name in ('f:all', 'f:all_action', 'f:filters.payee', 'f:filters.sort', 'empty:entries',
                 'ctx:source_ref', 'ctx:directive', 'ctx:idempotency_key'):
        assert not _shown(b, name), f'{name} still shows under the ticking area'
    assert _shown(b, 'ctx:reason'), 'the reason recorded with the save must stay'
    assert b.evaluate('document.querySelector("button[name=action][value=preview]").hidden')
    assert b.evaluate('document.querySelector("button[name=action][value=submit]").textContent') == 'Save marks'
    _contained(b, PHONE)

    rows = '[data-reconcile-list] .reconcile-movement input[type=checkbox]'
    b.evaluate(f'document.querySelectorAll({json.dumps(rows)}).forEach(e => {{ if(!e.checked) e.click(); }})')
    b.wait_for('document.querySelector(".reconcile-difference").dataset.balanced === "true"')
    _set(b, 'ctx:reason', 'Tick the January statement')
    _submit(b)
    b.wait_for('!!document.querySelector("[data-reconcile-finish]") && '
               '!document.querySelector("[data-reconcile-finish]").disabled')
    b.evaluate('document.querySelector("[data-reconcile-finish]").click()')
    b.wait_for('!!document.querySelector(".reconcile-certificate:not([hidden])")')
    assert 'Statement certified' in b.evaluate('document.querySelector(".reconcile-certificate").textContent')


def test_a_chosen_statement_file_fills_the_text_area_and_backups_fit_a_phone(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(PHONE, 900)
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/reconcile/import')
    b.wait_for('!!document.querySelector("textarea[name=\\"f:content\\"]")')
    text = 'Date,Description,Amount\n2026-09-30,MONTHLY SERVICE FEE,-12.00\n'
    b.evaluate(f'''(() => {{const pick=document.querySelector('input[type=file][data-text-file-into]');
        const files=new DataTransfer(); files.items.add(new File([{json.dumps(text)}], 'september.csv', {{type:'text/csv'}}));
        pick.files=files.files; pick.dispatchEvent(new Event('change', {{bubbles:true}}));}})()''')
    b.wait_for('document.querySelector("textarea[name=\\"f:content\\"]").value.includes("SERVICE FEE")')
    assert b.evaluate('document.querySelector("textarea[name=\\"f:content\\"]").value') == text
    _contained(b, PHONE)

    _command(b, env.site, 'company.backup', {}, **{'X-Bookflow-Reason': 'Phone layout check'})
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/company/backup')
    b.wait_for('!!document.querySelector("[data-backup-row]")')
    _contained(b, PHONE)
    # The file name reads as a line of its own, not one letter per line.
    cell = b.evaluate('''(() => {const r=document.querySelector('[data-backup-row] td').getBoundingClientRect();
        return {width:r.width, height:r.height};})()''')
    assert cell['width'] > 200 and cell['height'] < 80, cell
