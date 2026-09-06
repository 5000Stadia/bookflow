"""Calculator entry travels through actual browser forms and preserved write gates."""
import json
import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command, _simple_draft
from tests.test_service_sales_browser import _fill, _choose, _value, _click, _preview, _saved, _contained

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def value(b, selector):
    return b.evaluate(f'document.querySelector({json.dumps(selector)}).value')


def fill(b, selector, text):
    b.evaluate(f'''(() => {{ const e = document.querySelector({json.dumps(selector)});
    e.value = {json.dumps(text)}; e.dispatchEvent(new Event('input', {{bubbles:true}})); }})()''')


@pytest.mark.parametrize('width', [1280, 390])
def test_register_live_math_rounding_invalid_and_exclusions(register_browser, width):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 900)
    _simple_draft(env, '1')
    field = '[name="amount"]'
    fill(b, field, '10 / 3')
    assert value(b, field) == '10 / 3'
    assert '3.33' in b.evaluate('document.querySelector("[name=amount]").nextElementSibling.textContent')
    # Click Record with the formula still present: payload must use calculated value.
    b.evaluate('document.querySelector("#register-record").click()')
    b.wait_for('!document.querySelector("#register-receipt").hidden')
    assert _command(b, env.site, 'account.show', {'account': env.bank['id']})['balance']['minor_units'] == -333
    assert not b.evaluate('document.querySelector("[name=number]")?.hasAttribute("data-math-scale")')
    assert not b.evaluate('document.querySelector("[name=date]").hasAttribute("data-math-scale")')
    fill(b, field, '1 / 0')
    b.evaluate('document.querySelector("#register-record").click()')
    assert 'divide by zero' in b.evaluate('document.querySelector("[name=amount]").validationMessage')
    assert value(b, field) == '1 / 0'
    assert _command(b, env.site, 'account.show', {'account': env.bank['id']})['balance']['minor_units'] == -333
    fill(b, field, '.1 + .2')
    b.evaluate('document.querySelector("[name=amount]").dispatchEvent(new KeyboardEvent("keydown", {key:"Enter",bubbles:true,cancelable:true}))')
    assert value(b, field) == '0.3'
    assert _command(b, env.site, 'account.show', {'account': env.bank['id']})['balance']['minor_units'] == -333
    _contained(b, width)


@pytest.mark.parametrize('width', [1280, 390])
def test_generated_sales_math_preview_and_save(register_browser, width):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 900)
    run = lambda name, data: _command(b, env.site, name, data)
    income = run('account.create', dict(name='Math income', type='income'))['id']
    run('account.create', dict(name='Math receivables', type='accounts_receivable'))
    run('customer.create', dict(name='Math customer'))
    code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    run('item.create', dict(name='Math service', type='service', sales_enabled=True,
        income_account_id=income, price='12', description='Math service', sales_tax_code_id=code))
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/invoice/post')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    _fill(b, 'f:date', '2026-01-12')
    _choose(b, 'f:customer', 'Math customer')
    _choose(b, 'f:ar_account', 'Math receivables')
    b.evaluate('document.querySelector("[data-collection-path=lines] > [data-collection-add]").click()')
    _choose(b, 'c:lines:0:item', 'Math service')
    _fill(b, 'c:lines:0:quantity', '1/3')
    _fill(b, 'c:lines:0:unit_price', '(2+3)*2')
    _fill(b, 'f:memo', '1+2 is a memo')
    _preview(b)
    assert _value(b, 'c:lines:0:quantity') == '0.333333'
    assert _value(b, 'c:lines:0:unit_price') == '10'
    # Enter remains non-submitting after a result has become an ordinary number.
    assert b.evaluate('''(() => {
      const e=document.querySelector('[name$=":quantity"]');
      const event=new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true});
      e.dispatchEvent(event); return event.defaultPrevented;
    })()''')
    assert _value(b, 'f:memo') == '1+2 is a memo'
    assert '3.33' in b.evaluate('document.querySelector(".sales-document").innerText')
    # Even successful math is a new edit, requiring another preview.
    _fill(b, 'c:lines:0:quantity', '.25+.25')
    _click(b, 'submit')
    assert not _value(b, 'f:expected_facts_fingerprint')
    assert run('account.show', dict(account=income))['balance']['minor_units'] == 0
    _preview(b)
    _click(b, 'submit')
    identity = _saved(b, 'invoice')
    saved = run('invoice.show', dict(invoice=identity))
    assert saved['total_minor_units'] == 500
    assert saved['revision']['lines'][0]['quantity'] == '0.5'
    _contained(b, width)


def test_dynamic_currency_mode_and_inactive_values(register_browser):
    b = register_browser.browser
    # Actual loaded scripts with isolated controls exercising mutable field context.
    b.evaluate('''(() => {
      const f=document.createElement('form'); f.id='calculator-witness';
      f.dataset.mathCompanyCurrency='USD';
      f.innerHTML=`<select name="currency"><option>USD</option><option>JPY</option><option>BAD</option></select>
      <input name="price" data-math-currency="company" data-math-currency-field="currency">
      <select name="mode"><option>quantity</option><option>net_amount</option><option>rebill_allocation_id</option></select>
      <input name="scope" data-math-scale="6" data-math-currency="USD" data-math-mode-field="mode"
        data-math-active='[{"name":"mode","values":["quantity","net_amount"]}]'>
      <select name="custom-action"><option>set</option><option>clear</option><option>keep</option></select>
      <input name="custom" data-math-scale="9" data-math-active='[{"name":"custom-action","values":["set"]}]'>
      <input name="count" data-math-scale="0">`;
      document.body.append(f); window.bookflowMath.refresh();
    })()''')
    price = '#calculator-witness [name=price]'
    fill(b, price, '10/3')
    assert '3.33' in b.evaluate('document.querySelector("#calculator-witness [name=price]").nextElementSibling.textContent')
    b.evaluate('document.querySelector("#calculator-witness [name=currency]").value="JPY"')
    # Programmatic currency switch without change event still resolves current precision.
    assert b.evaluate('window.bookflowMath.prepare(document.querySelector("#calculator-witness"))')
    # A zero-decimal currency rounds; unlike a count, it does not reject a fraction.
    assert value(b, price) == '3'
    fill(b, price, '1000 / 3 JPY')
    assert b.evaluate('window.bookflowMath.prepare(document.querySelector("#calculator-witness"))')
    assert value(b, price) == '333 JPY'
    scope = '#calculator-witness [name=scope]'
    fill(b, scope, '1/3')
    b.evaluate('document.querySelector("#calculator-witness [name=mode]").value="net_amount"')
    assert b.evaluate('window.bookflowMath.prepare(document.querySelector("#calculator-witness"))')
    assert value(b, scope) == '0.33'
    fill(b, scope, '1/0')
    b.evaluate('document.querySelector("#calculator-witness [name=mode]").value="rebill_allocation_id"')
    fill(b, '#calculator-witness [name=custom]', '2+3')
    b.evaluate('document.querySelector("#calculator-witness [name=custom-action]").value="clear"')
    assert b.evaluate('window.bookflowMath.prepare(document.querySelector("#calculator-witness"))')
    assert value(b, '#calculator-witness [name=custom]') == '2+3'
    assert value(b, '#calculator-witness [name=custom-action]') == 'clear'
    assert value(b, scope) == '1/0'
    b.evaluate('document.querySelector("#calculator-witness [name=currency]").value="BAD"')
    fill(b, price, '1/2')
    assert not b.evaluate('window.bookflowMath.prepare(document.querySelector("#calculator-witness"))')
    assert value(b, price) == '1/2'


def test_register_splits_recalculate_and_numeric_custom_clear(register_browser):
    from tests.test_row8_register_browser import _choose as choose_register
    env, b = register_browser, register_browser.browser
    definition = _command(b, env.site, 'custom-field.create', dict(name='Math measurement', kind='number', scopes=['journal_entry']))
    b.navigate(env.url)
    b.wait_for('!!document.querySelector("[data-custom-field]")')
    b.evaluate('document.querySelector("#register-splits-open").click()')
    choose_register(b, '#register-allocations .register-picker input', 'CDP supplies')
    fill(b, '#register-allocations input[inputmode=decimal]', '120 / 3')
    b.evaluate('document.querySelector("#register-recalculate").click()')
    b.wait_for('document.querySelector("[name=amount]").value === "40.00"')
    assert value(b, '#register-allocations input[inputmode=decimal]') == '40'
    custom = f'[data-custom-field="{definition["id"]}"]'
    fill(b, custom, '1 / 3')
    # Closed split controls remain part of the entry and must still be normalized.
    b.evaluate('document.querySelector("#register-split-close").click()')
    fill(b, '#register-allocations input[inputmode=decimal]', '20 * 2')
    b.evaluate('document.querySelector("#register-record").click()')
    b.wait_for('!document.querySelector("#register-receipt").hidden')
    identity = b.evaluate('document.querySelector("#register-receipt a").href.split("/").pop()')
    saved = _command(b, env.site, 'journal.show', {'journal': identity})
    assert saved['revision']['custom_fields'][0]['value'] == '0.333333333'
    b.navigate(env.url)
    b.wait_for('!!document.querySelector("[data-custom-field]")')
    _simple_draft(env, '1')
    fill(b, custom, '1 / 0')
    b.evaluate(f'''(() => {{ const e=document.querySelector('[data-custom-action="{definition['id']}"]');
      e.value='clear'; e.dispatchEvent(new Event('change',{{bubbles:true}})); }})()''')
    b.evaluate('document.querySelector("#register-record").click()')
    b.wait_for('!document.querySelector("#register-receipt").hidden')
    identity = b.evaluate('document.querySelector("#register-receipt a").href.split("/").pop()')
    saved = _command(b, env.site, 'journal.show', {'journal': identity})
    assert not saved['revision']['custom_fields']
