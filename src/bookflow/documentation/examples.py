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
