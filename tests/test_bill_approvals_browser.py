"""Real htmx continuation separates submission, decision and money at phone width."""
import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_purchase_drafts_browser import _click

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def test_phone_approval_then_commit(register_browser):
    env=register_browser
    b,site=env.browser,env.site
    vendor=_command(b,site,'vendor.create',{'name':'Approval phone supplier'})
    headers={'X-Bookflow-Reason':'Phone approval witness'}
    draft=_command(b,site,'purchase-draft.create',dict(date='2026-02-01',vendor=vendor['id'],
        expenses=[dict(account=env.expense['id'],amount='80.00')],operation_key='phone-draft'),**headers)
    policy=_command(b,site,'approval-policy.create',dict(eligible_members=[draft['created_by']],allow_self=True,operation_key='phone-policy'),**headers)
    _command(b,site,'approval-policy.publish',dict(policy=policy['id'],expected_version=1,confirm=True,operation_key='phone-publish'),**headers)
    b.viewport(390,844)
    b.navigate(f'{site.base_url}/c/{site.company_id}/purchase-drafts?draft={draft["id"]}')
    for action,state in [('submit','pending'),('approve','approved'),('commit','committed')]:
        b.wait_for("!!document.querySelector('[data-approval-form]')")
        b.evaluate("document.querySelector('[data-approval-form] [name=reason]').value='Phone approval witness'")
        _click(b,f'[data-approval-form] [value="{action}"]')
        assert b.evaluate("document.querySelector('[data-draft-state]').innerText")==state
        if state!='committed':
            shown=_command(b,site,'purchase-draft.show',dict(draft=draft['id']))
            assert shown['preview']['accounting_effect']=='none'
    assert '80.00' in b.evaluate("document.querySelector('#purchase-drafts').innerText")
