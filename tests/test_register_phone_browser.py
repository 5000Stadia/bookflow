"""The account register on a phone (R62): entries as rows, and an "Add entry" bottom sheet that
posts through the same register form and command as the desktop row."""
from __future__ import annotations

import base64
import json

from tests.test_row8_register_browser import _command, _type, browser_site, register_browser  # noqa: F401

FITS = "document.documentElement.scrollWidth <= innerWidth + 1"


def _visible(browser, selector):
    return browser.evaluate(f"""(() => {{const n=document.querySelector({json.dumps(selector)});
      return !!n && n.getClientRects().length > 0 && getComputedStyle(n).visibility !== 'hidden';}})()""")


def test_phone_register_rows_and_add_entry_sheet(register_browser, tmp_path):
    env, b = register_browser, register_browser.browser
    checking = _command(b, env.site, 'account.show', {'account': 'Checking'})
    b.viewport(390, 844)
    b.navigate(f"{env.site.base_url}/c/{env.site.company_id}/account/{checking['id']}/register?date_from=2026-01-01&date_to=2026-12-31")
    b.wait_for("document.querySelectorAll('#register-history tr[data-kind=posting]').length > 5")

    # Entries read as rows: date, payee or memo, the movement at the right; the form is out of the way.
    assert b.evaluate(FITS)
    assert not _visible(b, '#register-entry') and _visible(b, '#register-sheet-open')
    row = b.evaluate("""(() => {const r=document.querySelector('#register-history tr[data-kind=posting]');
      const box=e=>e.getBoundingClientRect();
      const amount=[...r.querySelectorAll('.reg-increase,.reg-decrease')].find(e=>e.getClientRects().length);
      return {display:getComputedStyle(r).display, right:box(r).right, date:r.querySelector('time').textContent,
              amountTop:box(amount).top, dateTop:box(r.querySelector('.reg-date')).top,
              balanceTop:box(r.querySelector('.reg-balance')).top, amountRight:box(amount).right,
              headVisible:r.closest('table').tHead.getClientRects()[0].width > 1};})()""")
    assert row['display'] == 'grid' and row['right'] <= 390
    assert row['date'].startswith('Jan 1') and not row['headVisible']
    assert abs(row['amountTop'] - row['dateTop']) < 4 and row['balanceTop'] > row['amountTop']

    # "Add entry" opens the same entry form as a bottom sheet over the list.
    b.evaluate("document.querySelector('#register-sheet-open').click()")
    b.wait_for("document.activeElement.id === 'register-date'")
    sheet = b.evaluate("""(() => {const s=document.querySelector('#register-entry'), r=s.getBoundingClientRect();
      return {position:getComputedStyle(s).position, bottom:r.bottom, role:s.getAttribute('role'),
              fields:['date','number','amount','memo','direction'].every(n=>!!s.querySelector(`[name=${n}]`)),
              payee:!!s.querySelector('#register-payee input'), category:!!s.querySelector('#register-category input')};})()""")
    assert sheet['position'] == 'fixed' and abs(sheet['bottom'] - 844) < 2 and sheet['role'] == 'dialog'
    assert sheet['fields'] and sheet['payee'] and sheet['category']
    assert b.evaluate(FITS)

    # A rejected save keeps the sheet open and shows the error beside Record.
    b.evaluate("document.querySelector('[name=amount]').focus()")
    _type(b, '12.34')
    b.evaluate("document.querySelector('#register-record').click()")
    b.wait_for("!!document.querySelector('#register-error').textContent")
    placed = b.evaluate("""(() => {const e=document.querySelector('#register-error').getBoundingClientRect(),
      r=document.querySelector('#register-record').getBoundingClientRect();
      return {gap:r.top-e.bottom, inView:e.top >= 0 && r.bottom <= innerHeight};})()""")
    assert 0 <= placed['gap'] < 24 and placed['inView']
    assert _visible(b, '#register-entry')

    # Choose a category and post; the sheet closes and the new entry is in view in the list.
    b.evaluate("document.querySelector('#register-category input[role=combobox]').focus()")
    _type(b, 'Professional')
    b.wait_for("!document.querySelector('#register-category .register-options').hidden")
    b.evaluate("document.querySelector('#register-category .register-options button').click()")
    b.evaluate("document.querySelector('[name=memo]').focus()")
    _type(b, 'Phone sheet entry')
    b.evaluate("document.querySelector('#register-record').click()")
    b.wait_for("!document.querySelector('#register-receipt').hidden && document.activeElement.id === 'register-sheet-open'")
    assert not _visible(b, '#register-entry')
    journal = b.evaluate("document.querySelector('#register-receipt a').href").rsplit('/', 1)[1]
    selector = json.dumps(f'#register-history tr.register-saved-row[data-journal="{journal}"]')
    b.wait_for(f"!!document.querySelector({selector})")
    saved = b.evaluate(f"""(() => {{const r=document.querySelector({selector}), box=r.getBoundingClientRect();
      return {{text:r.innerText, top:box.top, bottom:box.bottom}};}})()""")
    assert 'Phone sheet entry' in saved['text'] and '12.34' in saved['text']
    assert saved['top'] >= 0 and saved['bottom'] <= 844
    posted = _command(b, env.site, 'journal.show', {'journal': journal})
    assert posted['revision']['lines'][0]['amount']['amount'] == '12.34'
    assert b.evaluate(FITS)
    picture = b.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})
    (tmp_path / 'register-phone-saved.png').write_bytes(base64.b64decode(picture['data']))
