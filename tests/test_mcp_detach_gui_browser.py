"""The shared hub detach form publishes its own receipt to a usable destination."""
import json
import os
from pathlib import Path
import sqlite3

import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser
from tests.test_mcp_workbench_control_browser import form, stage
from tests.test_service_sales_browser import _fill, _click, _contained


@pytest.mark.parametrize('width',[1280,390])
@pytest.mark.timeout(120)
@pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')
def test_generated_own_detach_preview_redirect_and_full_receipt(register_browser,tmp_path,width):
    env,b=register_browser,register_browser.browser
    root=Path(os.environ['BOOKFLOW_DATA_ROOT']);assert root.is_relative_to(tmp_path)
    def current():
        with sqlite3.connect((root/'hub.db').as_uri()+'?mode=ro',uri=True) as db:
            return db.execute('SELECT path FROM companies WHERE id=?',(env.site.company_id,)).fetchone()
    before=current();assert before
    original=root/before[0]/'company.db';assert original.is_file()
    b.viewport(width,900)
    form(b,env.site.base_url+'/hub/company/detach')
    _fill(b,'f:company',env.site.company_id)
    _fill(b,'ctx:reason','Detach the owned browser fixture')
    stage(b)
    assert not b.evaluate('document.querySelector(".error")?.textContent')
    preview=json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))
    assert preview['dry_run'] and current()==before and original.is_file()
    _contained(b,width)
    _click(b,'submit')
    b.wait_for('document.readyState === "complete" && location.pathname === "/companies"')
    assert current() is None and original.is_file()
    assert b.evaluate('new URL(location.href).searchParams.has("flash")')
    # The exact core receipt remains readable after its company ceases to exist
    # in discovery. It is not replaced by another company's data or a denied page.
    b.evaluate('document.querySelector(".save-feedback summary").click()')
    assert b.evaluate('document.querySelector(".save-feedback details").open')
    receipt=b.evaluate('[...document.querySelectorAll(".save-feedback pre")].map(e=>e.textContent)')
    parsed=[]
    for text in receipt:
        try:parsed.append(json.loads(text))
        except ValueError:pass
    assert parsed == [{**preview,'dry_run':False}]
    assert 'Saved successfully' in b.evaluate('document.querySelector(".save-feedback").innerText')
    assert not b.evaluate('document.querySelector(".error")?.textContent')
    _contained(b,width)
