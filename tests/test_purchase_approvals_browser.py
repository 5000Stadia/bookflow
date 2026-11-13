"""Release a saved purchase and receive it through real htmx at both screen widths."""
import base64

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_purchase_drafts_browser import _click, _set
from tests.test_service_sales_browser import _fill

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def test_draft_submit_approve_release_receive_at_desktop_and_phone(register_browser, tmp_path):
    env = register_browser
    b, site = env.browser, env.site
    base = f'{site.base_url}/c/{site.company_id}'
    headers = {'X-Bookflow-Reason': 'Purchase browser witness'}
    vendor = _command(b, site, 'vendor.create', dict(name='Browser release supplier'))
    accounts = _command(b, site, 'account.query', dict(limit=200))['items']
    income = next(row['id'] for row in accounts if row['type'] == 'income')
    cogs = next(row['id'] for row in accounts if row['type'] == 'cost_of_goods_sold')
    item = _command(b, site, 'item.create', dict(name='Browser release part', type='inventory_part',
        description='Receive after review', purchase_description='Receive after review',
        price='15.00', cost='12.00', income_account_id=income, cogs_account_id=cogs))
    for width in (1440, 390):
        b.viewport(width, 900 if width == 1440 else 844)
        b.navigate(base + '/purchase-drafts?new=1&kind=purchase_order')
        b.wait_for("!!document.querySelector('[data-draft-form]')")
        for field, value in {'vendor': vendor['id'], 'date': '2026-06-01', 'order:0:item': item['id'],
            'order:0:quantity': '10', 'order:0:rate': '12.00', 'reason': 'Review supplier order before release'}.items():
            _set(b, field, value)
        _click(b, '[data-draft-save]')
        assert b.evaluate("!!document.querySelector('[data-draft-saved]')"), b.evaluate('document.body.innerText')
        draft_id = b.evaluate("document.querySelector('#purchase-drafts').dataset.draftId")
        draft = _command(b, site, 'purchase-draft.show', dict(draft=draft_id))
        assert draft['kind'] == 'purchase_order' and draft['preview']['total']['minor_units'] == 12000
        if width == 1440:
            policy = _command(b, site, 'approval-policy.create', dict(target='purchase_order',
                eligible_members=[draft['created_by']], allow_self=True, operation_key='browser-policy'), **headers)
            _command(b, site, 'approval-policy.publish', dict(policy=policy['id'], expected_version=1,
                confirm=True, operation_key='browser-publish'), **headers)
            b.navigate(base + '/purchase-drafts?draft=' + draft_id)
        for action, state in [('submit', 'pending'), ('approve', 'approved'), ('commit', 'committed')]:
            b.wait_for("!!document.querySelector('[data-approval-form]')")
            b.evaluate("document.querySelector('[data-approval-form] [name=reason]').value='Purchase browser witness'")
            _click(b, f'[data-approval-form] [value="{action}"]')
            assert b.evaluate("document.querySelector('[data-draft-state]').innerText") == state
            assert b.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        document = b.evaluate("document.querySelector('[data-draft-document]').getAttribute('href')")
        order_id = document.rsplit('/', 1)[-1]
        b.navigate(base + '/item-receipt/post?purchase_order=' + order_id)
        b.wait_for("!!document.querySelector('[data-generated-form]')")
        _fill(b, 'f:date', '2026-06-02')
        quantity = b.evaluate("document.querySelector('[data-collection-path=items] [data-collection-item] [name$=\":quantity\"]').name")
        _fill(b, quantity, '3')
        _fill(b, 'ctx:reason', 'Three ordered units arrived')
        b.evaluate("window.receivingForm=document.querySelector('[data-generated-form]'); window.sameReceivingDocument=true; document.querySelector('button[value=submit]').click()")
        b.wait_for("!document.querySelector('[data-generated-form]') || !!document.querySelector('.error')", timeout=60)
        assert not b.evaluate("document.querySelector('.error')?.textContent"), b.evaluate('document.body.innerText')
        order = _command(b, site, 'purchase-order.show', dict(purchase_order=order_id))
        assert order['line_tracking'][0]['received_quantity'] == '3'
        assert order['line_tracking'][0]['open_quantity'] == '7'
        b.navigate(base + '/purchase-order/' + order_id)
        b.wait_for("document.body.innerText.includes('Browser release part')")
        assert b.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        shot = b.call('Page.captureScreenshot', {'captureBeyondViewport': True, 'fromSurface': True})
        (tmp_path / f'purchase-release-receive-{width}.png').write_bytes(base64.b64decode(shot['data']))
