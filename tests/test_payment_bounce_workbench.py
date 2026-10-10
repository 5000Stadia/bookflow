"""R176 in the browser: the generic form for `payment bounce`, reached from Customers, records the return.

The form is the command's own (one field per input, the bank and customer fees as grouped fields), so
what it submits is exactly what the other surfaces take. The receipt's page then says it bounced.
"""
import re

from fastapi.testclient import TestClient

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

HEADERS = {"X-Bookflow-Workbench": "1", "X-Bookflow-Client-Name": "bookflow-workbench"}


def _browser(hosted):
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    return browser


def test_the_bounced_check_form_records_the_return_and_the_receipt_says_so(hosted):
    browser = _browser(hosted)
    base = f"/companies/{hosted.company_id}/commands"

    def run(name, body):
        answered = browser.post(f"{base}/{name.replace(' ', '.')}", json=body, headers={**HEADERS, "X-Bookflow-Reason": "bounce witness"})
        assert answered.status_code == 200, answered.text[:600]
        return answered.json()

    bank = run("account create", {"name": "Bounce Checking", "type": "bank"})["id"]
    income = run("account create", {"name": "Bounce Sales", "type": "income"})["id"]
    run("account create", {"name": "Bounce Bank Fees", "type": "expense"})
    exempt = next(row["id"] for row in browser.post(f"{base}/sales-tax-code.list", json={}, headers=HEADERS).json()["items"] if not row["taxable"])
    item = run("item create", {"name": "Bounce Service", "type": "service", "sales_enabled": True, "description": "Service",
                               "sales_tax_code_id": exempt, "income_account_id": income, "price": "50"})["id"]
    customer = run("customer create", {"name": "Bounce Customer"})["id"]
    sale = run("invoice post", {"customer": customer, "date": "2026-07-01", "lines": [
        {"item": item, "quantity": "1", "unit_price": "75.00", "tax_code": exempt}]})
    check = run("payment receive", {"customer": customer, "date": "2026-07-02", "amount": "75.00", "payment_method": "Check",
                                    "reference": "311", "deposit_to": bank, "operation_key": "bounce-wb-receive",
                                    "applications": {"mode": "inline", "items": [
                                        {"invoice": sale["id"], "expected_version": 1, "amount": "75.00"}]}})
    form = browser.get(f"/c/{hosted.company_id}/payment/bounce", headers=HEADERS)
    assert form.status_code == 200
    for name in ("payment", "expected_version", "date", "operation_key", "bank_fee_amount", "bank_fee_account",
                 "customer_fee_amount", "customer_fee_item", "customer_fee_account"):
        assert re.search(rf'name="f:{re.escape(name)}"', form.text), (name, form.text[:1500])
    # The Customers page offers it as a task.
    assert f"/c/{hosted.company_id}/payment/bounce" in browser.get(f"/c/{hosted.company_id}/_group/customers", headers=HEADERS).text

    submitted = browser.post(f"/c/{hosted.company_id}/payment/bounce", headers=HEADERS, follow_redirects=False, data={
        "f:payment": check["id"], "f:expected_version": str(check["version"]), "f:date": "2026-07-08",
        "f:operation_key": "bounce-wb", "f:bank_fee_amount": "10.00", "f:bank_fee_account": "Bounce Bank Fees",
        "ctx:reason": "Bank returned check 311"})
    assert submitted.status_code in (200, 303), submitted.text[:800]
    shown = browser.post(f"{base}/payment.show", json={"payment": check["id"]}, headers=HEADERS).json()
    assert shown["bounce"]["note"] == "bounced on 2026-07-08" and shown["bounce"]["returned"]["minor_units"] == 7500
    page = browser.get(f"/c/{hosted.company_id}/receive-payments?payment={check['id']}", headers=HEADERS)
    assert page.status_code == 200


def test_an_agent_records_the_return_over_http_under_the_existing_role_rules(hosted):
    """`payment bounce` asks for ledger.post at standard, as the payment writes it is made of do."""
    from bookflow.core.config import Config
    from tests.conftest import hosted_call, make_agent
    from tests.test_entry_review import Caller, _setup
    person, bot, agent, bank, _ = _setup(hosted)
    income = person.ok('account create', {'name': 'Agent bounce sales', 'type': 'income'})['id']
    fees = person.ok('account create', {'name': 'Agent bounce fees', 'type': 'expense'})['id']
    exempt = next(r['id'] for r in person.ok('sales-tax-code list', {})['items'] if not r['taxable'])
    item = person.ok('item create', dict(name='Agent bounce service', type='service', sales_enabled=True, description='Service',
                                         sales_tax_code_id=exempt, income_account_id=income, price='60'))['id']
    customer = person.ok('customer create', {'name': 'Agent bounce customer'})['id']
    sale = person.ok('invoice post', dict(customer=customer, date='2026-07-01', lines=[
        dict(item=item, quantity='1', unit_price='60.00', tax_code=exempt)]))
    check = person.ok('payment receive', dict(
        customer=customer, date='2026-07-02', amount='60.00', payment_method='Check', reference='9', deposit_to=bank,
        operation_key='agent-bounce-receive', applications=dict(mode='inline', items=[
            dict(invoice=sale['id'], expected_version=1, amount='60.00')])))
    raw = dict(payment=check['id'], expected_version=check['version'], date='2026-07-08', operation_key='agent-bounce',
               bank_fee_amount='5.00', bank_fee_account=fees)
    done = bot.ok('payment bounce', raw)
    assert done['returned']['minor_units'] == 6000 and [row['number'] for row in done['reopened_invoices']]
    assert person.ok('payment show', {'payment': check['id']})['bounce']['note'] == 'bounced on 2026-07-08'
    # Without a reason an agent's write is refused, as every agent write is.
    refused = bot.call('payment bounce', dict(raw, operation_key='agent-bounce-2'), **{'X-Bookflow-Reason': ''})
    assert refused.status_code != 200
