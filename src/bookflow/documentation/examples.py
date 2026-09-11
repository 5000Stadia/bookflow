"""Deterministic command examples; values are illustrative and model-validated."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Example:
    invocation: str
    input: dict[str, Any]


ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"

EXAMPLES: dict[str, Example] = {
    "mcp": Example("bookflow mcp --url http://127.0.0.1:8765 --token-env BOOKFLOW_TOKEN", {"url": "http://127.0.0.1:8765"}),
    "activity": Example(f'bookflow activity customer {ID} --company "Demo Plumbing Co" --kinds \'["note","attachment"]\' --limit 25 --json', {"record_type": "customer", "record_id": ID, "kinds": ["note", "attachment"], "limit": 25}),
    "attachment add": Example(f'bookflow attachment add customer {ID} receipt.pdf --company "Demo Plumbing Co" --caption "Service receipt" --reason "File service receipt" --json', {"record_type": "customer", "record_id": ID, "original_filename": "receipt.pdf", "media_type": "application/pdf", "caption": "Service receipt"}),
    "attachment link": Example(f'bookflow attachment link {ID} customer {ID} --company "Demo Plumbing Co" --caption "Related receipt" --reason "Link receipt" --json', {"attachment": ID, "record_type": "customer", "record_id": ID, "caption": "Related receipt"}),
    "attachment unlink": Example(f'bookflow attachment unlink {ID} --expected-version 1 --company "Demo Plumbing Co" --reason "Remove association" --json', {"link": ID, "expected_version": 1}),
    "attachment list": Example(f'bookflow attachment list customer {ID} --company "Demo Plumbing Co" --limit 25 --json', {"record_type": "customer", "record_id": ID, "limit": 25}),
    "attachment get": Example(f'bookflow attachment get {ID} --out downloaded-receipt.pdf --company "Demo Plumbing Co" --json', {"attachment": ID}),
    "company compact": Example('bookflow company compact --company "Demo Plumbing Co" --limit 200 --dry-run --reason "Preview unlinked file collection" --json', {"limit": 200}),
    "audit list": Example('bookflow audit list --company "Demo Plumbing Co" --limit 5 --json', {"limit": 5}),
    "audit show": Example(f'bookflow audit show {ID} --company "Demo Plumbing Co" --json', {"event": ID}),
    "audit tail": Example('bookflow audit tail --company "Demo Plumbing Co" --after 42 --json', {"after": 42}),
    "company attach": Example('bookflow company attach /example/company --json', {"path": "/example/company"}),
    "company detach": Example(f"bookflow company detach {ID} --json", {"company": ID}),
    "company list": Example("bookflow company list --json", {}),
    "company new": Example('bookflow company new --organization "Demo Holdings LLC" --legal-name "Example Services LLC" --home-currency USD --timezone America/Los_Angeles --json', {"organization": "Demo Holdings LLC", "legal_name": "Example Services LLC", "home_currency": "USD", "timezone": "America/Los_Angeles"}),
    "company rename": Example('bookflow company rename --company "Demo Plumbing Co" --name "Demo Plumbing" --json', {"name": "Demo Plumbing"}),
    "company show": Example('bookflow company show --company "Demo Plumbing Co" --json', {}),
    "company update": Example('bookflow company update --company "Demo Plumbing Co" --phone 555-0123 --expected-version 4 --json', {"phone": "555-0123", "expected_version": 4}),
    "company use": Example('bookflow company use "Demo Plumbing Co" --json', {"company": "Demo Plumbing Co"}),
    "demo reset": Example("bookflow demo reset --json", {}),
    "directive add": Example('bookflow directive add --company "Demo Plumbing Co" --text "Post approved entries" --json', {"text": "Post approved entries"}),
    "directive deactivate": Example('bookflow directive deactivate SI-1 --company "Demo Plumbing Co" --json', {"directive": "SI-1"}),
    "directive list": Example('bookflow directive list --company "Demo Plumbing Co" --json', {}),
    "directive show": Example('bookflow directive show SI-1 --company "Demo Plumbing Co" --json', {"directive": "SI-1"}),
    "docs generate": Example("bookflow docs generate --output docs --check --json", {"output": "docs", "check": True}),
    "hub audit list": Example("bookflow hub audit list --limit 5 --json", {"limit": 5}),
    "hub audit show": Example(f"bookflow hub audit show {ID} --json", {"event": ID}),
    "hub audit tail": Example("bookflow hub audit tail --after 42 --json", {"after": 42}),
    "init": Example("bookflow init --json", {}),
    "note add": Example(f'bookflow note add customer {ID} --body "Call before arrival" --json', {"record_type": "customer", "record_id": ID, "body": "Call before arrival"}),
    "note show": Example(f'bookflow note show {ID} --json', {"note": ID}),
    "note edit": Example(f'bookflow note edit {ID} --body "Call thirty minutes before arrival" --expected-version 1 --json', {"note": ID, "body": "Call thirty minutes before arrival", "expected_version": 1}),
    "note list": Example(f'bookflow note list customer {ID} --limit 25 --json', {"record_type": "customer", "record_id": ID, "limit": 25}),
    "organization list": Example("bookflow organization list --json", {}),
    "organization new": Example('bookflow organization new --name "Example Holdings LLC" --json', {"name": "Example Holdings LLC"}),
    "organization rename": Example('bookflow organization rename "Example Holdings LLC" --name "Example Group LLC" --json', {"organization": "Example Holdings LLC", "name": "Example Group LLC"}),
    "organization show": Example('bookflow organization show "Demo Holdings LLC" --json', {"organization": "Demo Holdings LLC"}),
    "presence clear": Example(f'bookflow presence clear company_info {ID} --company "Demo Plumbing Co" --json', {"record_type": "company_info", "record_id": ID}),
    "presence set": Example(f'bookflow presence set company_info {ID} --company "Demo Plumbing Co" --json', {"record_type": "company_info", "record_id": ID}),
    "serve": Example("bookflow serve --bind 127.0.0.1:8765 --json", {"bind": "127.0.0.1:8765"}),
    "token issue": Example('bookflow token issue --label "agent client" --days 30 --json', {"label": "agent client", "days": 30}),
    "token list": Example("bookflow token list --json", {}),
    "token revoke": Example(f"bookflow token revoke {ID} --json", {"token": ID}),
    "upgrade": Example("bookflow upgrade --json", {}),
    "user set-password": Example('bookflow user set-password "$USER" --password "$BOOKFLOW_PASSWORD" --json', {"username": "example-user", "password": "correct-horse-battery"}),
    "user add": Example('bookflow user add jordan --display-name "Jordan Reyes" --company "Demo Plumbing Co" --role standard --json',
                        {"username": "jordan", "display_name": "Jordan Reyes", "company": "Demo Plumbing Co", "role": "standard"}),
    "membership grant": Example('bookflow membership grant jordan --company "Demo Plumbing Co" --role standard --json',
                                {"user": "jordan", "company": "Demo Plumbing Co", "role": "standard"}),
    "membership revoke": Example('bookflow membership revoke jordan --company "Demo Plumbing Co" --json',
                                 {"user": "jordan", "company": "Demo Plumbing Co"}),
    "user list": Example('bookflow user list --company "Demo Plumbing Co" --json', {"company": "Demo Plumbing Co"}),
    "membership list": Example('bookflow membership list --user jordan --json', {"user": "jordan"}),
}


EXAMPLES.update({
    "chart list": Example("bookflow chart list --json", {}),
    "chart show": Example("bookflow chart show general --json", {"template_id": "general"}),
    "chart apply": Example(
        'bookflow chart apply general --company "Demo Plumbing Co" --json',
        {"template_id": "general"},
    ),
    "profile list": Example("bookflow profile list --json", {}),
    "profile show": Example("bookflow profile show standard --json", {"profile_id": "standard"}),
    "profile apply": Example(
        'bookflow profile apply standard --company "Demo Plumbing Co" --json',
        {"profile_id": "standard"},
    ),
})


_SUPPORTING_CREATE_INPUTS = {
    "account": {"name": "Example service income", "number": "4099", "type": "income"},
    "customer": {"name": "Example customer", "company_name": "Example Customer LLC"},
    "custom-field": {"name": "Work order", "kind": "text", "scopes": ["customer"]},
    "employee": {"name": "Example employee", "first_name": "Morgan", "last_name": "Lee"},
    "item": {"name": "Example subtotal", "type": "subtotal", "description": "Subtotal"},
    "item-category": {"name": "Services"},
    "class": {"name": "Field work"},
    "term": {"name": "Net 45", "kind": "standard", "due_days": 45},
    "payment-method": {"name": "Mobile wallet", "kind": "other"},
    "other-name": {"name": "Example payee"},
    "price-level": {"name": "Preferred customers", "kind": "fixed_percent", "percent": "-5"},
    "sales-tax-code": {"code": "EX", "description": "Example taxable code", "taxable": True},
    "customer-type": {"name": "Commercial"},
    "vendor-type": {"name": "Materials"},
    "job-type": {"name": "Installation"},
    "sales-rep": {"name": "Example rep", "initials": "ER", "name_type": "employee", "name_id": ID},
    "ship-method": {"name": "Local courier", "display_order": 20},
    "customer-message": {"name": "Thanks", "text": "Thank you for your business.", "display_order": 20},
    "unit-of-measure": {
        "name": "Count",
        "units": [{"name": "Each", "abbreviation": "ea", "is_base": True, "base_factor": "1"}],
    },
    "vendor": {"name": "Example vendor", "company_name": "Example Vendor LLC"},
}

for _noun, _create_input in _SUPPORTING_CREATE_INPUTS.items():
    _selector = _noun.replace("-", "_")
    _prefix = f'bookflow {_noun}'
    _company = '--company "Demo Plumbing Co" --json'
    EXAMPLES.update({
        f"{_noun} create": Example(f"{_prefix} create {_company}", _create_input),
        f"{_noun} show": Example(f"{_prefix} show {ID} {_company}", {_selector: ID}),
        f"{_noun} list": Example(f"{_prefix} list {_company}", {}),
        f"{_noun} query": Example(f"{_prefix} query --limit 25 {_company}", {"limit": 25}),
        f"{_noun} update": Example(
            f"{_prefix} update {ID} --expected-version 1 {_company}",
            {_selector: ID, "expected_version": 1},
        ),
        f"{_noun} activate": Example(
            f"{_prefix} activate {ID} --expected-version 2 {_company}",
            {_selector: ID, "expected_version": 2},
        ),
        f"{_noun} deactivate": Example(
            f"{_prefix} deactivate {ID} --expected-version 1 {_company}",
            {_selector: ID, "expected_version": 1},
        ),
    })


EXAMPLES.update({
    "customer link-vendor": Example(
        f'bookflow customer link-vendor {ID} {ID} --expected-customer-version 1 '
        f'--expected-vendor-version 1 --company "Demo Plumbing Co" --json',
        {"customer": ID, "vendor": ID, "expected_customer_version": 1, "expected_vendor_version": 1},
    ),
    "customer unlink-vendor": Example(
        f'bookflow customer unlink-vendor {ID} --expected-customer-version 2 '
        f'--expected-vendor-version 2 --expected-link-version 1 --company "Demo Plumbing Co" --json',
        {"customer": ID, "expected_customer_version": 2, "expected_vendor_version": 2, "expected_link_version": 1},
    ),
    "other-name convert": Example(
        f'bookflow other-name convert {ID} --to vendor --expected-version 1 '
        f'--company "Demo Plumbing Co" --json',
        {"other_name": ID, "to": "vendor", "expected_version": 1},
    ),
    "undo": Example(
        f'bookflow undo {ID} --company "Demo Plumbing Co" --reason "Correct duplicate setup" --json',
        {"event_id": ID},
    ),
})


EXAMPLES.update({
    "journal post": Example(
        'bookflow journal post --date 2026-01-15 --lines \'[{"account":"Checking","side":"debit","amount":"125.00"},{"account":"Service Income","side":"credit","amount":"125.00"}]\' --company "Demo Plumbing Co" --reason "Record service receipt" --json',
        {"date": "2026-01-15", "lines": [{"account": "Checking", "side": "debit", "amount": "125.00"}, {"account": "Service Income", "side": "credit", "amount": "125.00"}]},
    ),
    "journal show": Example(f'bookflow journal show {ID} --company "Demo Plumbing Co" --json', {"journal": ID}),
    "journal update": Example(f'bookflow journal update {ID} --memo "Service receipt" --expected-version 1 --company "Demo Plumbing Co" --reason "Clarify receipt" --json', {"journal": ID, "memo": "Service receipt", "expected_version": 1}),
    "journal void": Example(f'bookflow journal void {ID} --expected-version 1 --company "Demo Plumbing Co" --reason "Duplicate receipt" --json', {"journal": ID, "expected_version": 1}),
    "journal query": Example('bookflow journal query --date-from 2026-01-01 --date-to 2026-12-31 --limit 25 --company "Demo Plumbing Co" --json', {"date_from": "2026-01-01", "date_to": "2026-12-31", "limit": 25}),
    "journal history": Example(f'bookflow journal history {ID} --limit 25 --company "Demo Plumbing Co" --json', {"journal": ID, "limit": 25}),
    "report profit-and-loss": Example('bookflow report profit-and-loss --date-from 2026-01-01 --date-to 2026-12-31 --company "Reference Plumbing Co" --json', {"date_from": "2026-01-01", "date_to": "2026-12-31"}),
    "report balance-sheet": Example('bookflow report balance-sheet --date-to 2026-12-31 --company "Reference Plumbing Co" --json', {"date_to": "2026-12-31"}),
    "report trial-balance": Example('bookflow report trial-balance --date-to 2026-12-31 --company "Demo Plumbing Co" --json', {"date_to": "2026-12-31"}),
    "report general-ledger": Example('bookflow report general-ledger --date-from 2026-01-01 --date-to 2026-12-31 --account Checking --company "Demo Plumbing Co" --json', {"date_from": "2026-01-01", "date_to": "2026-12-31", "account": "Checking"}),
    "report statement": Example('bookflow report statement --date-from 2026-01-01 --date-to 2026-12-31 --customer "Adams Plumbing" --company "Demo Plumbing Co" --json', {"date_from": "2026-01-01", "date_to": "2026-12-31", "customer": "Adams Plumbing"}),
    "report ar-aging": Example('bookflow report ar-aging --as-of 2026-12-31 --company "Demo Plumbing Co" --json', {"as_of": "2026-12-31"}),
    "report open-invoices": Example('bookflow report open-invoices --as-of 2026-12-31 --past-due-only --company "Demo Plumbing Co" --json', {"as_of": "2026-12-31", "past_due_only": True}),
    "report ap-aging": Example('bookflow report ap-aging --as-of 2026-12-31 --company "Demo Plumbing Co" --json', {"as_of": "2026-12-31"}),
    "report unpaid-bills": Example('bookflow report unpaid-bills --as-of 2026-12-31 --past-due-only --company "Demo Plumbing Co" --json', {"as_of": "2026-12-31", "past_due_only": True}),
})


EXAMPLES.update({
    "register post": Example(
        'bookflow register post --account Checking --date 2026-04-01 --direction decrease --amount 125.00 --category "Professional Fees" --company "Demo Plumbing Co" --reason "Record professional fees" --json',
        {"account": "Checking", "date": "2026-04-01", "direction": "decrease", "amount": "125.00", "category": "Professional Fees"}),
    "register update": Example(
        f'bookflow register update {ID} --expected-version 1 --selected-line-id {ID} --category-line-id {ID} --account Checking --date 2026-04-01 --direction decrease --amount 125.00 --category "Professional Fees" --memo "Professional fee receipt" --company "Demo Plumbing Co" --reason "Clarify payment memo" --json',
        {"journal": ID, "expected_version": 1, "selected_line_id": ID, "category_line_id": ID, "account": "Checking", "date": "2026-04-01", "direction": "decrease", "amount": "125.00", "category": "Professional Fees", "memo": "Professional fee receipt"}),
    "register calculate": Example(
        'bookflow register calculate --account Checking --direction decrease --allocations \'[{"account":"Professional Fees","amount":"120.00"},{"account":"Professional Fees","amount":"20.00","direction":"increase"}]\' --company "Demo Plumbing Co" --json',
        {"account": "Checking", "direction": "decrease", "allocations": [{"account": "Professional Fees", "amount": "120.00"}, {"account": "Professional Fees", "amount": "20.00", "direction": "increase"}]}),
    "register query": Example(
        'bookflow register query --account Checking --date-from 2026-01-01 --date-to 2026-12-31 --limit 25 --company "Demo Plumbing Co" --json',
        {"account": "Checking", "date_from": "2026-01-01", "date_to": "2026-12-31", "limit": 25}),
    "check post": Example(
        'bookflow check post --account Checking --pay-to-name-type vendor --pay-to-name-id "Northside Supply" --date 2026-04-02 --number 1042 --amount 284.60 --memo "March supplies" --expenses \'[{"account":"Office Supplies","amount":"184.60","memo":"Parts"},{"account":"Professional Fees","amount":"100.00","memo":"Filing"}]\' --company "Demo Plumbing Co" --reason "Pay Northside Supply" --json',
        {"account": "Checking", "pay_to": {"name_type": "vendor", "name_id": "Northside Supply"},
         "date": "2026-04-02", "number": "1042", "amount": "284.60", "memo": "March supplies",
         "expenses": [{"account": "Office Supplies", "amount": "184.60", "memo": "Parts"},
                      {"account": "Professional Fees", "amount": "100.00", "memo": "Filing"}]}),
    "card-charge post": Example(
        'bookflow card-charge post --account "Company Credit Card" --pay-to-name-type vendor --pay-to-name-id "Northside Supply" --date 2026-04-03 --amount 75.25 --memo "Fuel on the company card" --expenses \'[{"account":"Office Supplies","amount":"75.25"}]\' --company "Demo Plumbing Co" --reason "Record a card purchase" --json',
        {"account": "Company Credit Card", "pay_to": {"name_type": "vendor", "name_id": "Northside Supply"},
         "date": "2026-04-03", "amount": "75.25", "memo": "Fuel on the company card",
         "expenses": [{"account": "Office Supplies", "amount": "75.25"}]}),
    "transfer post": Example(
        'bookflow transfer post --from-account Checking --to-account "Company Credit Card" --date 2026-04-04 --amount 500.00 --memo "Pay the card down" --company "Demo Plumbing Co" --reason "Pay the card down" --json',
        {"from_account": "Checking", "to_account": "Company Credit Card",
         "date": "2026-04-04", "amount": "500.00", "memo": "Pay the card down"}),
})


EXAMPLES.update({
    "rate set": Example('bookflow rate set --date 2026-07-15 --from-currency JPY --rate 0.0068 --expected-version 0 --company "Demo Plumbing Co" --reason "Enter manual yen rate" --json', {"date":"2026-07-15","from_currency":"JPY","rate":"0.0068","expected_version":0}),
    "rate show": Example('bookflow rate show --date 2026-07-15 --from-currency JPY --company "Demo Plumbing Co" --json', {"date":"2026-07-15","from_currency":"JPY"}),
    "rate query": Example('bookflow rate query --from-currency JPY --limit 25 --company "Demo Plumbing Co" --json', {"from_currency":"JPY","limit":25}),
})


for _noun in ('invoice', 'sales-receipt'):
    _selector = _noun.replace('-', '_')
    _receipt = _noun == 'sales-receipt'
    _payload = {'date': '2026-09-01', 'customer': 'Riverside Apartments',
                'lines': [{'item': 'Mainline Clearing', 'quantity': '1', 'unit_price': '125.00'}]}
    if _receipt:
        _payload.update(deposit_to='Checking', payment_method='Check')
    import json as _json
    import shlex as _shlex
    _options = ' '.join('--' + key.replace('_', '-') + ' ' + _shlex.quote(_json.dumps(value) if isinstance(value, list) else value)
                        for key, value in _payload.items())
    EXAMPLES[_noun + ' post'] = Example('bookflow ' + _noun + ' post ' + _options + ' --company "Demo Plumbing Co" --reason "Record completed service" --json', _payload)
    EXAMPLES[_noun + ' show'] = Example(f'bookflow {_noun} show {ID} --company "Demo Plumbing Co" --json', {_selector: ID})
    EXAMPLES[_noun + ' update'] = Example(f'bookflow {_noun} update {ID} --memo "Completed service" --expected-version 1 --company "Demo Plumbing Co" --json', {_selector: ID, 'memo': 'Completed service', 'expected_version': 1})
    EXAMPLES[_noun + ' void'] = Example(f'bookflow {_noun} void {ID} --expected-version 1 --reason "Duplicate sale" --company "Demo Plumbing Co" --json', {_selector: ID, 'expected_version': 1})
    EXAMPLES[_noun + ' query'] = Example(f'bookflow {_noun} query --date-from 2026-01-01 --date-to 2026-12-31 --limit 25 --company "Demo Plumbing Co" --json', {'date_from': '2026-01-01', 'date_to': '2026-12-31', 'limit': 25})
    EXAMPLES[_noun + ' history'] = Example(f'bookflow {_noun} history {ID} --limit 25 --company "Demo Plumbing Co" --json', {_selector: ID, 'limit': 25})


for _noun in ('proposal', 'estimate', 'work-order'):
    _selector = _noun.replace('-', '_')
    _payload = {'date': '2026-09-01', 'title': 'Clear the main drain', 'customer': 'Riverside Apartments',
                'scope': 'Inspect and clear the main drain; test normal flow.',
                'lines': [{'item': 'Mainline Clearing', 'quantity': '1', 'unit_price': '125.00'}]}
    _options = ' '.join('--' + key.replace('_', '-') + ' ' + _shlex.quote(_json.dumps(value) if isinstance(value, list) else value)
                        for key, value in _payload.items())
    EXAMPLES[_noun + ' create'] = Example('bookflow ' + _noun + ' create ' + _options + ' --company "Demo Plumbing Co" --reason "Prepare customer work" --json', _payload)
    EXAMPLES[_noun + ' show'] = Example(f'bookflow {_noun} show {ID} --company "Demo Plumbing Co" --json', {_selector: ID})
    EXAMPLES[_noun + ' update'] = Example(f'bookflow {_noun} update {ID} --memo "Site visit arranged" --expected-version 1 --company "Demo Plumbing Co" --reason "Record work note" --json', {_selector: ID, 'memo': 'Site visit arranged', 'expected_version': 1})
    EXAMPLES[_noun + ' copy'] = Example(f'bookflow {_noun} copy {ID} --expected-version 1 --date 2026-09-02 --company "Demo Plumbing Co" --reason "Prepare another option" --json', {_selector: ID, 'expected_version': 1, 'date': '2026-09-02'})
    EXAMPLES[_noun + ' query'] = Example(f'bookflow {_noun} query --customer "Riverside Apartments" --date-from 2026-01-01 --date-to 2026-12-31 --limit 25 --company "Demo Plumbing Co" --json', {'customer': 'Riverside Apartments', 'date_from': '2026-01-01', 'date_to': '2026-12-31', 'limit': 25})
    EXAMPLES[_noun + ' history'] = Example(f'bookflow {_noun} history {ID} --limit 25 --company "Demo Plumbing Co" --json', {_selector: ID, 'limit': 25})

EXAMPLES.update({
    'proposal estimate': Example(f'bookflow proposal estimate {ID} --expected-version 1 --conversion-key "drain-quote-2026-09" --date 2026-09-02 --company "Demo Plumbing Co" --reason "Make an estimate from the scope" --json', {'proposal': ID, 'expected_version': 1, 'conversion_key': 'drain-quote-2026-09', 'date': '2026-09-02'}),
    'estimate work-order': Example(f'bookflow estimate work-order {ID} --expected-version 2 --conversion-key "drain-dispatch-2026-09" --date 2026-09-03 --company "Demo Plumbing Co" --reason "Make a work order from the accepted estimate" --json', {'estimate': ID, 'expected_version': 2, 'conversion_key': 'drain-dispatch-2026-09', 'date': '2026-09-03'}),
    'work-order complete': Example(f'bookflow work-order complete {ID} --expected-version 1 --actual-start 2026-09-03T10:00:00-05:00 --actual-end 2026-09-03T11:00:00-05:00 --company "Demo Plumbing Co" --reason "Customer work completed" --json', {'work_order': ID, 'expected_version': 1, 'actual_start': '2026-09-03T10:00:00-05:00', 'actual_end': '2026-09-03T11:00:00-05:00'}),
})

# Linked work billing uses canonical sources and permanent conversion intent.
for _noun, _selector in (("estimate", "estimate"), ("work-order", "work_order")):
    EXAMPLES[_noun + " billing"] = Example(
        f'bookflow {_noun} billing {ID} --company "Demo Plumbing Co" --limit 50 --json', {_selector: ID, 'limit': 50})
    EXAMPLES[_noun + " invoice"] = Example(
        f'bookflow {_noun} invoice {ID} --expected-version 2 --conversion-key "bill-work-2026-09" --date 2026-09-04 --percent 25 --company "Demo Plumbing Co" --reason "Invoice one quarter of agreed scope" --json',
        {_selector: ID, 'expected_version': 2, 'conversion_key': 'bill-work-2026-09', 'date': '2026-09-04', 'percent': '25'})
    EXAMPLES[_noun + " sales-receipt"] = Example(
        f'bookflow {_noun} sales-receipt {ID} --expected-version 2 --conversion-key "paid-work-2026-09" --date 2026-09-04 --deposit-to Checking --payment-method Cash --amount-received "10.81" --company "Demo Plumbing Co" --reason "Record paid work" --json',
        {_selector: ID, 'expected_version': 2, 'conversion_key': 'paid-work-2026-09', 'date': '2026-09-04', 'deposit_to': 'Checking', 'payment_method': 'Cash', 'amount_received': '10.81'})

# Row22 receipt preparation and immutable settlement commands.
_PAYMENT_RECEIVE = dict(customer=ID, date='2026-06-01', amount='150.00', payment_method='Check', operation_key='example-receipt-1')
_PAYMENT_EXAMPLES = {
    'payment receive': _PAYMENT_RECEIVE,
    'payment apply': dict(payment=ID, expected_version=1, date='2026-06-01', operation_key='example-apply-1', applications=dict(mode='inline', items=[dict(invoice=ID, expected_version=1, amount='50.00')])),
    'payment show': dict(payment=ID),
    'payment update': dict(payment=ID, expected_version=1, operation_key='example-correction-1', memo='Corrected remittance note'),
    'payment unapply': dict(payment=ID, expected_version=2, operation_key='example-unapply-1', applications=[dict(application_id=ID, invoice_expected_version=2)]),
    'payment void': dict(payment=ID, expected_version=3, operation_key='example-void-1'),
    'payment history': dict(payment=ID, limit=25),
    'payment settlement changes': dict(guard='authenticated-guard-from-payment-show', limit=25),
    'application show': dict(application=ID),
    'application history': dict(application=ID, limit=25),
    'payment query': dict(payment_method='Check', limit=25),
    'payment invoices': dict(mode='new_receipt', customer=ID, date='2026-06-01'),
    'payment suggest': dict(mode='new_receipt', customer=ID, date='2026-06-01', amount='150.00', strategy='exact_then_oldest'),
    'payment calculate': dict(mode='new_receipt', customer=ID, date='2026-06-01', amount='150.00', amount_mode='entered'),
    'payment selection create': dict(mode='new_receipt', customer=ID, date='2026-06-01', amount='150.00'),
    'payment selection update': dict(selection=ID, expected_version=1, set_items=[dict(invoice=ID, expected_version=1, amount='50.00')]),
    'payment selection clear': dict(selection=ID, expected_version=1),
    'payment selection show': dict(selection=ID),
    'payment selection items': dict(selection=ID, revision=1, limit=25),
    'payment selection query': dict(state='open', limit=25),
    'payment preview items': dict(request=dict(command='payment receive', input=_PAYMENT_RECEIVE), facts_fingerprint='a'*64, kind='source_components'),
    'payment operation show': dict(operation_key='example-receipt-1'),
    'payment operation items': dict(operation_key='example-receipt-1', kind='effect_applications', limit=25),
    'payment settlement': dict(payment=ID, kind='components', limit=25),
    'invoice settlement': dict(invoice=ID),
}
_PAYMENT_POSITIONALS = {
    **{'payment ' + verb: 'payment' for verb in ('update', 'unapply', 'void', 'history')},
    'application show': 'application', 'application history': 'application',
    'payment apply': 'payment', 'payment show': 'payment', 'payment settlement': 'payment',
    'invoice settlement': 'invoice', 'payment operation show': 'operation_key', 'payment operation items': 'operation_key',
    **{'payment selection ' + verb: 'selection' for verb in ('update', 'clear', 'show', 'items')},
}
import json as _payment_json
import shlex as _payment_shell
for _name, _payload in _PAYMENT_EXAMPLES.items():
    _args = ['bookflow', *_name.split()]
    _positional = _PAYMENT_POSITIONALS.get(_name)
    if _positional:
        _args.append(str(_payload[_positional]))
    for _field, _value in _payload.items():
        if _field == _positional:
            continue
        _args.extend(['--' + _field.replace('_', '-'), _payment_json.dumps(_value, separators=(',', ':')) if isinstance(_value, (dict, list)) else str(_value)])
    _args.extend(['--company', 'Demo Plumbing Co', '--json'])
    if _name in ('payment update', 'payment unapply', 'payment void'):
        _args.extend(['--reason', 'Correct recorded remittance'])
    EXAMPLES[_name] = Example(' '.join(_payment_shell.quote(value) for value in _args), _payload)

# Master browsing discovery is additive to the existing bounded query command.
from bookflow.company.lists import LIST_DEFINITIONS as _BROWSING_LISTS
for _noun in _BROWSING_LISTS:
    EXAMPLES[_noun + ' query options'] = Example(
        f'bookflow {_noun} query options --kind columns --limit 50 --company "Demo Plumbing Co" --json',
        {'kind': 'columns', 'limit': 50})
for _noun, _column in {'vendor': 'expense_accounts', 'unit-of-measure': 'units', 'price-level': 'items',
                       'item': 'members', 'custom-field': 'choices'}.items():
    EXAMPLES[_noun + ' query children'] = Example(
        f'bookflow {_noun} query children --record {ID} --column {_column} --limit 50 --company "Demo Plumbing Co" --json',
        {'record': ID, 'column': _column, 'limit': 50})

_RECOVERY_BEGIN = dict(recovery_key='example-recovery', selection=ID, expected_version=2,
    local_baseline_revision=ID, attempt_generation='11111111-1111-4111-8111-111111111111',
    declared_entry_count=0, intent_hash='a'*64, header_intent=dict(action='keep'))
_RECOVERY_COMPARE = dict(recovery_id=ID,attempt_generation=_RECOVERY_BEGIN['attempt_generation'],intent_hash='a'*64)
_RECOVERY_EXAMPLES = {
    'begin':_RECOVERY_BEGIN,
    'upload':dict(recovery_id=ID,chunk_index=0,entries=[dict(invoice_id=ID,observed_invoice_version=1,action='remove')]),
    'seal':dict(recovery_id=ID,expected_recovery_version=2),
    'compare':_RECOVERY_COMPARE,
    'compare-items':dict(**_RECOVERY_COMPARE,facts_fingerprint='b'*64,kind='changes'),
    'apply':dict(**_RECOVERY_COMPARE,expected_recovery_version=3,expected_selection_version=2,expected_facts_fingerprint='b'*64),
    'abort':dict(recovery_id=ID,expected_recovery_version=2,disposition='discard_entire_attempt'),
    'replace':dict(recovery_id=ID,expected_recovery_version=2,replacement=_RECOVERY_BEGIN),
    'show':dict(recovery_id=ID),
    'items':dict(recovery_id=ID,kind='missing_ranges',limit=50),
    'query':dict(state='uploading',limit=50),
}
for _verb,_payload in _RECOVERY_EXAMPLES.items():
    _args=['bookflow','payment','recovery',_verb]
    def _recovery_flags(value,prefix=''):
        for key,item in value.items():
            name=(prefix+'-'+key if prefix else key).replace('_','-')
            if isinstance(item,dict):
                _recovery_flags(item,name)
            else:
                _args.extend(['--'+name,_payment_json.dumps(item,separators=(',',':')) if isinstance(item,list) else str(item)])
    _recovery_flags(_payload)
    _args.extend(['--company','Demo Plumbing Co','--json'])
    if _verb in {'begin','upload','seal','apply','abort','replace'}:
        _args.extend(['--reason','Recover the complete intended draft'])
    EXAMPLES['payment recovery '+_verb]=Example(' '.join(_payment_shell.quote(arg) for arg in _args),_payload)

# Deposits: bank the receipts sitting in Undeposited Funds.
_DEPOSIT_PAYMENT = '01ARZ3NDEKTSV4RRFFQ69G5FB1'
_DEPOSIT_RECEIPT = '01ARZ3NDEKTSV4RRFFQ69G5FB2'
_DEPOSIT_ROWS = [dict(source_type='payment', source=_DEPOSIT_PAYMENT, expected_version=1),
                 dict(source_type='sales_receipt', source=_DEPOSIT_RECEIPT, expected_version=1)]
_DEPOSIT_DOCUMENT = dict(mode='inline', deposit_to='Checking', date='2026-06-03', memo='Saturday receipts',
                         sources=_DEPOSIT_ROWS, additional=[])
_DEPOSIT_REPLACEMENT = dict(_DEPOSIT_DOCUMENT, number='1', cash_back=None, custom_fields={},
                            expected_custom_field_kinds={}, sources=_DEPOSIT_ROWS[:1])
_DEPOSIT_SOURCE_ROWS = _payment_json.dumps(_DEPOSIT_ROWS, separators=(',', ':'))
EXAMPLES.update({
    'deposit sources': Example(
        'bookflow deposit sources --date 2026-06-03 --limit 50 --company "Demo Plumbing Co" --json',
        {'date': '2026-06-03', 'limit': 50}),
    'deposit post': Example(
        'bookflow deposit post --operation-key example-deposit-1 --document-mode inline'
        ' --document-deposit-to Checking --document-date 2026-06-03 --document-memo "Saturday receipts"'
        f" --document-sources '{_DEPOSIT_SOURCE_ROWS}'"
        ' --company "Demo Plumbing Co" --reason "Bank Saturday receipts" --json',
        dict(operation_key='example-deposit-1', document=_DEPOSIT_DOCUMENT)),
    'deposit update': Example(
        f'bookflow deposit update {ID} --expected-version 1 --operation-key example-deposit-2'
        ' --document-mode inline --document-deposit-to Checking --document-date 2026-06-03'
        ' --document-number 1 --document-memo "Saturday receipts" --document-custom-fields "{}"'
        ' --document-expected-custom-field-kinds "{}"'
        f""" --document-sources '{_payment_json.dumps(_DEPOSIT_ROWS[:1], separators=(',', ':'))}'"""
        ' --document-additional "[]" --dependency-guard authenticated-guard-from-deposit-post-dry-run'
        ' --company "Demo Plumbing Co" --reason "Remove a receipt banked in error" --json',
        dict(deposit=ID, expected_version=1, operation_key='example-deposit-2',
             dependency_guard='authenticated-guard-from-deposit-post-dry-run', document=_DEPOSIT_REPLACEMENT)),
    'deposit void': Example(
        f'bookflow deposit void {ID} --expected-version 2 --operation-key example-deposit-3'
        ' --dependency-guard authenticated-guard-from-deposit-void-dry-run'
        ' --company "Demo Plumbing Co" --reason "Deposit never reached the bank" --json',
        dict(deposit=ID, expected_version=2, operation_key='example-deposit-3',
             dependency_guard='authenticated-guard-from-deposit-void-dry-run')),
})

# Public deposit details. The deposit id comes from an ordinary register row
# (`register query`, or the register link in the browser); nothing here needs a
# private source identity. `deposit show` selects one immutable revision with
# --revision-number and evaluates the dated bank effect with --as-of; the
# composition itself is paged by `deposit items` one kind at a time.
EXAMPLES.update({
    "deposit show": Example(
        f'bookflow deposit show {ID} --revision-number 2 --as-of 2026-06-30 --company "Demo Plumbing Co" --json',
        {"deposit": ID, "revision_number": 2, "as_of": "2026-06-30"},
    ),
    "deposit items": Example(
        f'bookflow deposit items {ID} --kind sources --revision-number 2 --page-limit 50 --company "Demo Plumbing Co" --json',
        {"deposit": ID, "kind": "sources", "revision_number": 2, "page": {"limit": 50}},
    ),
})

EXAMPLES['deposit query'] = Example(
    'bookflow deposit query --date-from 2026-06-01 --date-to 2026-06-30 --sort date --direction desc --page-limit 25 --company "Demo Plumbing Co" --json',
    {'date_from':'2026-06-01','date_to':'2026-06-30','sort':'date','direction':'desc','page':{'limit':25}},
)


# A bill is the payables mirror of the invoice, so its examples read like the invoice's: a
# vendor instead of a customer, an expenses grid instead of item lines, and a supplier
# reference that is the vendor's own number rather than ours.
EXAMPLES.update({
    "bill post": Example(
        'bookflow bill post --vendor "Northside Supply" --date 2026-04-02 --supplier-reference INV-7742'
        ' --memo "March parts" --expenses \'[{"account":"Office Supplies","amount":"184.60","memo":"Parts"},'
        '{"account":"Professional Fees","amount":"100.00","memo":"Filing"}]\''
        ' --company "Demo Plumbing Co" --reason "Enter the March bill" --json',
        {"vendor": "Northside Supply", "date": "2026-04-02", "supplier_reference": "INV-7742",
         "memo": "March parts",
         "expenses": [{"account": "Office Supplies", "amount": "184.60", "memo": "Parts"},
                      {"account": "Professional Fees", "amount": "100.00", "memo": "Filing"}]}),
    "bill show": Example(
        f'bookflow bill show {ID} --company "Demo Plumbing Co" --json', {"bill": ID}),
    "bill update": Example(
        f'bookflow bill update {ID} --memo "March parts and filing" --expected-version 1'
        ' --company "Demo Plumbing Co" --reason "Clarify the bill memo" --json',
        {"bill": ID, "memo": "March parts and filing", "expected_version": 1}),
    "bill void": Example(
        f'bookflow bill void {ID} --expected-version 1 --company "Demo Plumbing Co"'
        ' --reason "Billed to the wrong company" --json',
        {"bill": ID, "expected_version": 1}),
    "bill query": Example(
        'bookflow bill query --vendor "Northside Supply" --due-to 2026-04-30 --status posted --limit 25'
        ' --company "Demo Plumbing Co" --json',
        {"vendor": "Northside Supply", "due_to": "2026-04-30", "status": "posted", "limit": 25}),
    "bill history": Example(
        f'bookflow bill history {ID} --limit 25 --company "Demo Plumbing Co" --json',
        {"bill": ID, "limit": 25}),
})


# Paying a bill is one verb across documents, so its example selects two bills and lets the
# grouping decide how many payments that is; the reads are the payables mirror of the receipt's.
EXAMPLES.update({
    "bill pay": Example(
        'bookflow bill pay --date 2026-04-15 --funding-account "Checking" --method "Check"'
        ' --check-number 1041 --memo "April payables"'
        ' --bills \'[{"bill":"BILL-104"},{"bill":"BILL-108","amount":"250.00"}]\''
        ' --company "Demo Plumbing Co" --reason "Pay the April bills" --json',
        {"date": "2026-04-15", "funding_account": "Checking", "method": "Check",
         "check_number": "1041", "memo": "April payables",
         "bills": [{"bill": "BILL-104"}, {"bill": "BILL-108", "amount": "250.00"}]}),
    "bill payment show": Example(
        f'bookflow bill payment show {ID} --company "Demo Plumbing Co" --json', {"payment": ID}),
    "bill payment query": Example(
        'bookflow bill payment query --vendor "Northside Supply" --date-from 2026-04-01'
        ' --status posted --limit 25 --company "Demo Plumbing Co" --json',
        {"vendor": "Northside Supply", "date_from": "2026-04-01", "status": "posted", "limit": 25}),
    "bill payment apply": Example(
        f'bookflow bill payment apply {ID} --expected-version 2'
        ' --bills \'[{"bill":"BILL-112"}]\''
        ' --company "Demo Plumbing Co" --reason "Move the check to the right bill" --json',
        {"payment": ID, "expected_version": 2, "bills": [{"bill": "BILL-112"}]}),
    "bill payment unapply": Example(
        f'bookflow bill payment unapply {ID} --expected-version 1 --company "Demo Plumbing Co"'
        ' --reason "Applied to the wrong bill" --json',
        {"payment": ID, "expected_version": 1}),
    "bill payment void": Example(
        f'bookflow bill payment void {ID} --expected-version 2 --company "Demo Plumbing Co"'
        ' --reason "The check was never sent" --json',
        {"payment": ID, "expected_version": 2}),
})


# A credit memo is entered either way it can be entered: the goodwill credit names its own item
# and price, the return names the invoice line coming back and lets the capture price it.
EXAMPLES.update({
    "credit-memo post": Example(
        'bookflow credit-memo post --customer "Rivera Construction" --date 2026-04-08'
        ' --memo "Goodwill credit for the late visit"'
        ' --lines \'[{"item":"Site visit","quantity":"1","unit_price":"30.00"}]\''
        ' --company "Demo Plumbing Co" --reason "Credit the customer" --json',
        {"customer": "Rivera Construction", "date": "2026-04-08",
         "memo": "Goodwill credit for the late visit",
         "lines": [{"item": "Site visit", "quantity": "1", "unit_price": "30.00"}]}),
    "credit-memo show": Example(
        f'bookflow credit-memo show {ID} --company "Demo Plumbing Co" --json', {"credit_memo": ID}),
    "credit-memo history": Example(
        f'bookflow credit-memo history {ID} --limit 25 --company "Demo Plumbing Co" --json',
        {"credit_memo": ID, "limit": 25}),
})
