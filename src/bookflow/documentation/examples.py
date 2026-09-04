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
