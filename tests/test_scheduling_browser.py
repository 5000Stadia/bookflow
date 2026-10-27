"""R111's three pages in actual Chrome: a to-do completed from the To-dos page and from Reminders, and an
overdue notice marked sent. Each form is submitted for real, through htmx, so the workbench header goes
with it (a plain POST is refused with 403 in a real browser, which the test client does not show).
"""
import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser, _command  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

REASON = {'X-Bookflow-Reason': 'Scheduling browser witness'}


def _click_and_wait(b, selector):
    """Click the form's button and wait for htmx to swap that form away."""
    b.evaluate(f'void (window.beforeSubmit = document.querySelector({selector!r}).closest("form"))')
    b.evaluate(f'document.querySelector({selector!r}).click()')
    b.wait_for('!window.beforeSubmit.isConnected')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), b.evaluate('document.body.innerText')


def test_todo_and_notice_forms_submit_in_the_browser(register_browser):
    env, b = register_browser, register_browser.browser
    run = lambda name, data, **headers: _command(b, env.site, name, data, **headers)  # noqa: E731
    base = f'{env.site.base_url}/c/{env.site.company_id}'

    first = run('todo.create', {'title': 'Call the inspector', 'due_date': '2026-01-05'})
    second = run('todo.create', {'title': 'Order more fittings', 'due_date': '2026-01-05'})

    # To-dos: mark the first done.
    b.navigate(base + '/todo')
    done = f'form[action$="/todo/{first["id"]}/_done"] button'
    b.wait_for(f'!!document.querySelector({done!r})')
    _click_and_wait(b, done)
    assert run('todo.show', {'todo': first['id']})['status'] == 'done'

    # Reminders: the second is due, and is completed from there.
    b.navigate(base + '/reminders')
    done = f'form[action$="/todo/{second["id"]}/_done"] button'
    b.wait_for(f'!!document.querySelector({done!r})')
    _click_and_wait(b, done)
    assert run('todo.show', {'todo': second['id']})['status'] == 'done'

    # Overdue notices: one customer owes past due; the notice is marked sent from the list.
    income = run('account.create', dict(name='Notice income', type='income'))['id']
    code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    item = run('item.create', dict(name='Notice call-out', type='service', sales_enabled=True, income_account_id=income,
                                   price='250.00', description='Call-out', sales_tax_code_id=code))['id']
    customer = run('customer.create', dict(name='Late Payer Ltd'))['id']
    run('invoice.post', dict(customer=customer, date='2026-01-02', due_date='2026-02-01',
                             lines=[dict(item=item, quantity='1')]))
    run('company.update', {'overdue_notice_days': 1, 'overdue_notice_template': 'letter'}, **REASON)
    run('notice.generate', {'as_of': '2026-03-05'}, **REASON)
    notice = next(n for n in run('notice.list', {})['items'] if n['customer_id'] == customer)
    b.navigate(base + '/notice')
    sent = f'form[action$="/notice/{notice["id"]}/_sent"] button'
    b.wait_for(f'!!document.querySelector({sent!r})')
    _click_and_wait(b, sent)
    assert run('notice.show', {'notice': notice['id']})['status'] == 'sent'
