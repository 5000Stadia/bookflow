"""Deterministic command examples; values are illustrative and model-validated."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Example:
    invocation: str
    input: dict[str, Any]


ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_CUTOVER_FILES = [{"attachment": "01ARZ3NDEKTSV4RRFFQ69G5FB3"}, {"attachment": "01ARZ3NDEKTSV4RRFFQ69G5FB4"},
                  {"attachment": "01ARZ3NDEKTSV4RRFFQ69G5FB5"}]

EXAMPLES: dict[str, Example] = {
    "mcp": Example("bookflow mcp --url http://127.0.0.1:8765 --token-env BOOKFLOW_TOKEN", {"url": "http://127.0.0.1:8765"}),
    "activity": Example(f'bookflow activity customer {ID} --company "Demo Plumbing Co" --kinds \'["note","attachment"]\' --limit 25 --json', {"record_type": "customer", "record_id": ID, "kinds": ["note", "attachment"], "limit": 25}),
    "attachment add": Example(f'bookflow attachment add customer {ID} receipt.pdf --company "Demo Plumbing Co" --caption "Service receipt" --reason "File service receipt" --json', {"record_type": "customer", "record_id": ID, "original_filename": "receipt.pdf", "media_type": "application/pdf", "caption": "Service receipt"}),
    "attachment link": Example(f'bookflow attachment link {ID} customer {ID} --company "Demo Plumbing Co" --caption "Related receipt" --reason "Link receipt" --json', {"attachment": ID, "record_type": "customer", "record_id": ID, "caption": "Related receipt"}),
    "attachment unlink": Example(f'bookflow attachment unlink {ID} --expected-version 1 --company "Demo Plumbing Co" --reason "Remove association" --json', {"link": ID, "expected_version": 1}),
    "attachment list": Example(f'bookflow attachment list customer {ID} --company "Demo Plumbing Co" --limit 25 --json', {"record_type": "customer", "record_id": ID, "limit": 25}),
    "attachment get": Example(f'bookflow attachment get {ID} --out downloaded-receipt.pdf --company "Demo Plumbing Co" --json', {"attachment": ID}),
    "company backup": Example('bookflow company backup --company "Demo Plumbing Co" --json', {}),
    "company restore": Example('bookflow company restore "/srv/backups/Demo Plumbing Co 2026-09-28-170500.bookflow-backup" --as-copy --name "Demo Plumbing (restored)" --json', {"archive": "/srv/backups/Demo Plumbing Co 2026-09-28-170500.bookflow-backup", "as_copy": True, "name": "Demo Plumbing (restored)"}),
    "backup schedule": Example('bookflow backup schedule "Demo Plumbing Co" --daily-at 02:00 --destination /mnt/offsite/bookflow --keep 14 --json', {"company": "Demo Plumbing Co", "daily_at": "02:00", "destination": "/mnt/offsite/bookflow", "keep": 14}),
    "backup list": Example('bookflow backup list --company "Demo Plumbing Co" --json', {}),
    "backup verify": Example('bookflow backup verify --company "Demo Plumbing Co" --json', {}),
    "backup rehearse": Example('bookflow backup rehearse --company "Demo Plumbing Co" --json', {}),
    "company compact": Example('bookflow company compact --company "Demo Plumbing Co" --limit 200 --dry-run --reason "Preview unlinked file collection" --json', {"limit": 200}),
    "audit list": Example('bookflow audit list --company "Demo Plumbing Co" --limit 5 --json', {"limit": 5}),
    "audit show": Example(f'bookflow audit show {ID} --company "Demo Plumbing Co" --json', {"event": ID}),
    "audit tail": Example('bookflow audit tail --company "Demo Plumbing Co" --after 42 --json', {"after": 42}),
    "company attach": Example('bookflow company attach /example/company --administrator jordan --json', {"path": "/example/company", "administrator": "jordan"}),
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
    "permission show": Example('bookflow permission show --json', {}),
    "permission activate": Example('bookflow permission activate --expected-generation 1 --expected-catalog-sha256 "' + '0' * 64 + '" --json',
                                   {"expected_generation": 1, "expected_catalog_sha256": "0" * 64}),
    "membership effective": Example('bookflow membership effective --company "Demo Plumbing Co" --user jordan --json',
                                     {"company": "Demo Plumbing Co", "user": "jordan"}),
    "membership grant": Example("bookflow membership grant jordan --company 'Demo Plumbing Co' --role standard --expected-version 1 --grants '[\"transaction.check.delete\"]' --denies '[\"ledger.post\"]' --json",
                                {"user": "jordan", "company": "Demo Plumbing Co", "role": "standard", "expected_version": 1, "grants": ["transaction.check.delete"], "denies": ["ledger.post"]}),
    "membership revoke": Example('bookflow membership revoke jordan --company "Demo Plumbing Co" --json',
                                 {"user": "jordan", "company": "Demo Plumbing Co"}),
    "user list": Example('bookflow user list --company "Demo Plumbing Co" --json', {"company": "Demo Plumbing Co"}),
    "membership list": Example('bookflow membership list --user jordan --json', {"user": "jordan"}),
    "agent create": Example('bookflow agent create books-agent --display-name "Books agent" --owner jordan --json',
                            {"username": "books-agent", "display_name": "Books agent", "owner": "jordan"}),
    "agent show": Example("bookflow agent show books-agent --json", {"agent": "books-agent"}),
    "agent list": Example("bookflow agent list --principal jordan --json", {"principal": "jordan"}),
    "agent assign": Example("bookflow agent assign books-agent --principal jordan --confirm-permitted-use --expected-version 1 --json",
                            {"agent": "books-agent", "principal": "jordan", "confirm_permitted_use": True, "expected_version": 1}),
    "agent unassign": Example("bookflow agent unassign books-agent --principal jordan --json",
                              {"agent": "books-agent", "principal": "jordan"}),
    "agent authorize": Example("bookflow agent authorize books-agent --confirm-permitted-use --acknowledge-fresh-context --json",
                               {"agent": "books-agent", "confirm_permitted_use": True, "acknowledge_fresh_context": True}),
    "agent deactivate": Example('bookflow agent deactivate books-agent --reason "retired" --json', {"agent": "books-agent"}),
    "agent activate": Example("bookflow agent activate books-agent --json", {"agent": "books-agent"}),
    "user deactivate": Example('bookflow user deactivate jordan --reason "left the company" --json', {"user": "jordan"}),
    "user activate": Example("bookflow user activate jordan --json", {"user": "jordan"}),
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
    "report cash-flows": Example('bookflow report cash-flows --date-from 2026-01-01 --date-to 2026-12-31 --company "Reference Plumbing Co" --json', {"date_from": "2026-01-01", "date_to": "2026-12-31"}),
    "report income-tax-summary": Example('bookflow report income-tax-summary --date-from 2026-01-01 --date-to 2026-12-31 --company "Reference Plumbing Co" --json', {"date_from": "2026-01-01", "date_to": "2026-12-31"}),
    "report trial-balance": Example('bookflow report trial-balance --date-to 2026-12-31 --company "Demo Plumbing Co" --json', {"date_to": "2026-12-31"}),
    "cutover plan": Example('bookflow cutover plan --as-of 2026-09-30 --files \'[{"attachment": "01ARZ3NDEKTSV4RRFFQ69G5FB3"}, {"attachment": "01ARZ3NDEKTSV4RRFFQ69G5FB4"}, {"attachment": "01ARZ3NDEKTSV4RRFFQ69G5FB5"}]\' --mappings-accounts \'{"6150 · Uncategorized Expenses": "6300"}\' --company "Riverbend Plumbing" --json',
                            {"as_of": "2026-09-30", "files": _CUTOVER_FILES, "mappings": {"accounts": {"6150 · Uncategorized Expenses": "6300"}}}),
    "cutover apply": Example('bookflow cutover apply --as-of 2026-09-30 --files \'[{"attachment": "01ARZ3NDEKTSV4RRFFQ69G5FB3"}, {"attachment": "01ARZ3NDEKTSV4RRFFQ69G5FB4"}, {"attachment": "01ARZ3NDEKTSV4RRFFQ69G5FB5"}]\' --company "Riverbend Plumbing" --reason "Move in from the old books" --idempotency-key move-in-2026-09-30 --json',
                             {"as_of": "2026-09-30", "files": _CUTOVER_FILES}),
    "cutover tie-out": Example('bookflow cutover tie-out --as-of 2026-09-30 --files \'[{"attachment": "01ARZ3NDEKTSV4RRFFQ69G5FB3"}, {"attachment": "01ARZ3NDEKTSV4RRFFQ69G5FB4"}, {"attachment": "01ARZ3NDEKTSV4RRFFQ69G5FB5"}]\' --company "Riverbend Plumbing" --json',
                               {"as_of": "2026-09-30", "files": _CUTOVER_FILES}),
    "report export": Example('bookflow report export profit-and-loss --filters \'{"date_from": "2026-01-01", "date_to": "2026-12-31"}\' --company "Demo Plumbing Co" --json',
                             {"report": "profit-and-loss", "filters": {"date_from": "2026-01-01", "date_to": "2026-12-31"}}),
    "report general-ledger": Example('bookflow report general-ledger --date-from 2026-01-01 --date-to 2026-12-31 --account Checking --company "Demo Plumbing Co" --json', {"date_from": "2026-01-01", "date_to": "2026-12-31", "account": "Checking"}),
    "report transaction-detail": Example('bookflow report transaction-detail --date-from 2026-01-01 --date-to 2026-12-31 --accounts \'["Checking"]\' --company "Demo Plumbing Co" --json', {"date_from": "2026-01-01", "date_to": "2026-12-31", "accounts": ["Checking"]}),
    "report reconciliation-discrepancy": Example('bookflow report reconciliation-discrepancy --account Checking --as-of 2026-12-31 --company "Demo Plumbing Co" --json', {"account": "Checking", "as_of": "2026-12-31"}),
    "report entries-to-review": Example('bookflow report entries-to-review --as-of 2026-12-31 --company "Demo Plumbing Co" --json', {"as_of": "2026-12-31"}),
    "report prior-balances": Example('bookflow report prior-balances --as-of 2026-12-31 --company "Demo Plumbing Co" --json', {"as_of": "2026-12-31"}),
    "review mark": Example(f'bookflow review mark {ID} --note "Checked against the bank statement" --company "Demo Plumbing Co" --reason "Owner review" --json', {"transaction": ID, "note": "Checked against the bank statement"}),
    "report missing-checks": Example('bookflow report missing-checks --as-of 2026-12-31 --account Checking --company "Demo Plumbing Co" --json', {"as_of": "2026-12-31", "account": "Checking"}),
    "account uncategorized": Example('bookflow account uncategorized --limit 5 --company "Demo Plumbing Co" --json', {"limit": 5}),
    "report statement": Example('bookflow report statement --date-from 2026-01-01 --date-to 2026-12-31 --customer "Adams Plumbing" --company "Demo Plumbing Co" --json', {"date_from": "2026-01-01", "date_to": "2026-12-31", "customer": "Adams Plumbing"}),
    "report ar-aging": Example('bookflow report ar-aging --as-of 2026-12-31 --company "Demo Plumbing Co" --json', {"as_of": "2026-12-31"}),
    "report open-invoices": Example('bookflow report open-invoices --as-of 2026-12-31 --past-due-only --company "Demo Plumbing Co" --json', {"as_of": "2026-12-31", "past_due_only": True}),
    "report ap-aging": Example('bookflow report ap-aging --as-of 2026-12-31 --company "Demo Plumbing Co" --json', {"as_of": "2026-12-31"}),
    "report profit-and-loss-by-job": Example('bookflow report profit-and-loss-by-job --date-from 2026-01-01 --date-to 2026-12-31 --columns 50 --company "Demo Plumbing Co" --json', {"date_from": "2026-01-01", "date_to": "2026-12-31", "columns": 50}),
    "report profit-and-loss-by-class": Example('bookflow report profit-and-loss-by-class --date-from 2026-01-01 --date-to 2026-12-31 --columns 50 --company "Demo Plumbing Co" --json', {"date_from": "2026-01-01", "date_to": "2026-12-31", "columns": 50}),
    "report unbilled-costs": Example('bookflow report unbilled-costs --as-of 2026-12-31 --company "Demo Plumbing Co" --json', {"as_of": "2026-12-31"}),
    "report collections": Example('bookflow report collections --as-of 2026-12-31 --minimum-bucket days_1_30 --company "Demo Plumbing Co" --json', {"as_of": "2026-12-31", "minimum_bucket": "days_1_30"}),
    "report unpaid-bills": Example('bookflow report unpaid-bills --as-of 2026-12-31 --past-due-only --company "Demo Plumbing Co" --json', {"as_of": "2026-12-31", "past_due_only": True}),
    "report sales-by-customer": Example('bookflow report sales-by-customer --date-from 2026-01-01 --date-to 2026-12-31 --company "Demo Plumbing Co" --json', {"date_from": "2026-01-01", "date_to": "2026-12-31"}),
    "report sales-by-item": Example('bookflow report sales-by-item --date-from 2026-01-01 --date-to 2026-12-31 --company "Demo Plumbing Co" --json', {"date_from": "2026-01-01", "date_to": "2026-12-31"}),
    "report sales-by-rep": Example('bookflow report sales-by-rep --date-from 2026-01-01 --date-to 2026-12-31 --company "Demo Plumbing Co" --json', {"date_from": "2026-01-01", "date_to": "2026-12-31"}),
    "report expenses-by-vendor": Example('bookflow report expenses-by-vendor --date-from 2026-01-01 --date-to 2026-12-31 --company "Demo Plumbing Co" --json', {"date_from": "2026-01-01", "date_to": "2026-12-31"}),
    "report inventory-valuation": Example('bookflow report inventory-valuation --as-of 2026-12-31 --company "Demo Plumbing Co" --json', {"as_of": "2026-12-31"}),
    "report stock-status": Example('bookflow report stock-status --as-of 2026-12-31 --company "Demo Plumbing Co" --json', {"as_of": "2026-12-31"}),
    "report customer-balance-summary": Example('bookflow report customer-balance-summary --as-of 2026-12-31 --company "Demo Plumbing Co" --json', {"as_of": "2026-12-31"}),
    "report customer-balance-detail": Example('bookflow report customer-balance-detail --as-of 2026-12-31 --company "Demo Plumbing Co" --json', {"as_of": "2026-12-31"}),
    "report vendor-balance-summary": Example('bookflow report vendor-balance-summary --as-of 2026-12-31 --company "Demo Plumbing Co" --json', {"as_of": "2026-12-31"}),
    "report open-purchase-orders": Example('bookflow report open-purchase-orders --date-to 2026-12-31 --company "Demo Plumbing Co" --json', {"date_to": "2026-12-31"}),
    "report purchases-by-vendor": Example('bookflow report purchases-by-vendor --date-from 2026-01-01 --date-to 2026-12-31 --company "Demo Plumbing Co" --json', {"date_from": "2026-01-01", "date_to": "2026-12-31"}),
    "report purchases-by-item": Example('bookflow report purchases-by-item --date-from 2026-01-01 --date-to 2026-12-31 --company "Demo Plumbing Co" --json', {"date_from": "2026-01-01", "date_to": "2026-12-31"}),
    "report deposit-detail": Example('bookflow report deposit-detail --date-from 2026-01-01 --date-to 2026-12-31 --company "Demo Plumbing Co" --json', {"date_from": "2026-01-01", "date_to": "2026-12-31"}),
    "report transaction-list-by-date": Example('bookflow report transaction-list-by-date --date-from 2026-12-01 --date-to 2026-12-31 --company "Demo Plumbing Co" --json', {"date_from": "2026-12-01", "date_to": "2026-12-31"}),
    "report vendor-1099-summary": Example('bookflow report vendor-1099-summary --date-from 2026-01-01 --date-to 2026-12-31 --company "Demo Plumbing Co" --json', {"date_from": "2026-01-01", "date_to": "2026-12-31"}),
    "report vendor-balance-detail": Example('bookflow report vendor-balance-detail --as-of 2026-12-31 --vendor "Central Supply" --company "Demo Plumbing Co" --json', {"as_of": "2026-12-31", "vendor": "Central Supply"}),
})


EXAMPLES.update({
    "inventory adjust": Example(
        'bookflow inventory adjust --item "Brass Shutoff Valve" --date 2026-01-05 --adjustment-account "Opening Balance Equity" --quantity-change 24 --value-change 273.60 --memo "Opening stock counted" --company "Demo Plumbing Co" --reason "Set opening stock" --json',
        {"item": "Brass Shutoff Valve", "date": "2026-01-05",
         "adjustment_account": "Opening Balance Equity", "quantity_change": "24",
         "value_change": "273.60", "memo": "Opening stock counted"}),
    "inventory void": Example(
        f'bookflow inventory void {ID} --expected-version 1 --company "Demo Plumbing Co" --reason "Counted the wrong bin" --json',
        {"adjustment": ID, "expected_version": 1}),
    "inventory show": Example(
        f'bookflow inventory show {ID} --company "Demo Plumbing Co" --json', {"adjustment": ID}),
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


# The three money-out documents carry one lifecycle between them, so the examples that show it
# are written once per verb rather than once per noun.
for _noun, _selector, _account in (('check', 'check', 'Checking'),
                                   ('card-charge', 'card_charge', '"Company Credit Card"')):
    EXAMPLES[_noun + ' show'] = Example(
        f'bookflow {_noun} show {ID} --company "Demo Plumbing Co" --json', {_selector: ID})
    EXAMPLES[_noun + ' update'] = Example(
        f'bookflow {_noun} update {ID} --expected-version 1 --amount 300.00 '
        f'--expenses \'[{{"account":"Office Supplies","amount":"200.00","memo":"Parts"}},'
        f'{{"account":"Professional Fees","amount":"100.00","memo":"Filing"}}]\' '
        f'--company "Demo Plumbing Co" --reason "Parts line was understated" --json',
        {_selector: ID, 'expected_version': 1, 'amount': '300.00',
         'expenses': [{'account': 'Office Supplies', 'amount': '200.00', 'memo': 'Parts'},
                      {'account': 'Professional Fees', 'amount': '100.00', 'memo': 'Filing'}]})
    EXAMPLES[_noun + ' void'] = Example(
        f'bookflow {_noun} void {ID} --expected-version 2 --company "Demo Plumbing Co" '
        f'--reason "Never cashed" --json', {_selector: ID, 'expected_version': 2})
    EXAMPLES[_noun + ' query'] = Example(
        f'bookflow {_noun} query --account {_account} --date-from 2026-01-01 --date-to 2026-12-31 '
        f'--limit 25 --company "Demo Plumbing Co" --json',
        {'account': _account.strip('"'), 'date_from': '2026-01-01', 'date_to': '2026-12-31',
         'limit': 25})
    EXAMPLES[_noun + ' history'] = Example(
        f'bookflow {_noun} history {ID} --include-deleted --limit 25 --company "Demo Plumbing Co" --json',
        {_selector: ID, 'limit': 25, 'include_deleted': True})

# A card credit is a card charge in the other direction: a refund put back on the card.
EXAMPLES.update({
    'card-credit post': Example(
        'bookflow card-credit post --account "Company Credit Card" --pay-to-name-type vendor --pay-to-name-id "Northside Supply" --date 2026-04-08 --amount 42.50 --memo "Returned the wrong valve" --expenses \'[{"account":"Office Supplies","amount":"42.50","memo":"Valve returned"}]\' --company "Demo Plumbing Co" --reason "Record the refund on the card" --json',
        {"account": "Company Credit Card", "pay_to": {"name_type": "vendor", "name_id": "Northside Supply"},
         "date": "2026-04-08", "amount": "42.50", "memo": "Returned the wrong valve",
         "expenses": [{"account": "Office Supplies", "amount": "42.50", "memo": "Valve returned"}]}),
    'card-credit show': Example(
        f'bookflow card-credit show {ID} --company "Demo Plumbing Co" --json', {'card_credit': ID}),
    'card-credit update': Example(
        f'bookflow card-credit update {ID} --expected-version 1 --amount 45.00 '
        f'--expenses \'[{{"account":"Office Supplies","amount":"45.00","memo":"Valve returned"}}]\' '
        f'--company "Demo Plumbing Co" --reason "The refund was 45.00" --json',
        {'card_credit': ID, 'expected_version': 1, 'amount': '45.00',
         'expenses': [{'account': 'Office Supplies', 'amount': '45.00', 'memo': 'Valve returned'}]}),
    'card-credit void': Example(
        f'bookflow card-credit void {ID} --expected-version 2 --company "Demo Plumbing Co" '
        f'--reason "Refund went to the other card" --json', {'card_credit': ID, 'expected_version': 2}),
    'card-credit query': Example(
        'bookflow card-credit query --account "Company Credit Card" --date-from 2026-01-01 --date-to 2026-12-31 '
        '--limit 25 --company "Demo Plumbing Co" --json',
        {'account': 'Company Credit Card', 'date_from': '2026-01-01', 'date_to': '2026-12-31', 'limit': 25}),
    'card-credit history': Example(
        f'bookflow card-credit history {ID} --limit 25 --company "Demo Plumbing Co" --json',
        {'card_credit': ID, 'limit': 25}),
})

EXAMPLES.update({
    'transfer show': Example(f'bookflow transfer show {ID} --company "Demo Plumbing Co" --json',
                             {'transfer': ID}),
    'transfer update': Example(
        f'bookflow transfer update {ID} --expected-version 1 --to-account Savings --amount 400.00 '
        f'--company "Demo Plumbing Co" --reason "Wrong destination account" --json',
        {'transfer': ID, 'expected_version': 1, 'to_account': 'Savings', 'amount': '400.00'}),
    'transfer void': Example(
        f'bookflow transfer void {ID} --expected-version 2 --company "Demo Plumbing Co" '
        f'--reason "The transfer was never made" --json', {'transfer': ID, 'expected_version': 2}),
    'transfer query': Example(
        'bookflow transfer query --account Checking --date-from 2026-01-01 --date-to 2026-12-31 '
        '--limit 25 --company "Demo Plumbing Co" --json',
        {'account': 'Checking', 'date_from': '2026-01-01', 'date_to': '2026-12-31', 'limit': 25}),
    'transfer history': Example(
        f'bookflow transfer history {ID} --limit 25 --company "Demo Plumbing Co" --json',
        {'transfer': ID, 'limit': 25}),
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
                # A subtotal sums the lines above it; the discount below it takes its
                # percentage off that subtotal.
                'lines': [{'item': 'Mainline Clearing', 'quantity': '1', 'unit_price': '125.00'},
                          {'item': 'Copper Coupling', 'quantity': '2'}, {'item': 'Work Order Subtotal'},
                          {'item': 'Loyalty Discount', 'percent': '5'}]}
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
    'estimate void': Example(f'bookflow estimate void {ID} --expected-version 2 --company "Demo Plumbing Co" --reason "Customer changed their mind" --json', {'estimate': ID, 'expected_version': 2}),
    'estimate work-order': Example(f'bookflow estimate work-order {ID} --expected-version 2 --conversion-key "drain-dispatch-2026-09" --date 2026-09-03 --company "Demo Plumbing Co" --reason "Make a work order from the accepted estimate" --json', {'estimate': ID, 'expected_version': 2, 'conversion_key': 'drain-dispatch-2026-09', 'date': '2026-09-03'}),
    'work-order complete': Example(f'bookflow work-order complete {ID} --expected-version 1 --actual-start 2026-09-03T10:00:00-05:00 --actual-end 2026-09-03T11:00:00-05:00 --company "Demo Plumbing Co" --reason "Customer work completed" --json', {'work_order': ID, 'expected_version': 1, 'actual_start': '2026-09-03T10:00:00-05:00', 'actual_end': '2026-09-03T11:00:00-05:00'}),
})

# Recorded time: a much smaller input than a quote, because a person logging hours is saying
# who, for whom, when, how long and as what. The duration is decimal hours.
_TIME = {'employee': 'Dana Fitter', 'customer': 'Riverside Apartments', 'date': '2026-09-03',
         'duration': '1.5', 'item': 'Mainline Clearing', 'note': 'Cleared the main drain and tested flow.'}
EXAMPLES['time-activity create'] = Example(
    'bookflow time-activity create ' + ' '.join('--' + k.replace('_', '-') + ' ' + _shlex.quote(v)
                                                for k, v in _TIME.items())
    + ' --company "Demo Plumbing Co" --reason "Monday timesheet" --json', dict(_TIME))
EXAMPLES['time-activity show'] = Example(
    f'bookflow time-activity show {ID} --company "Demo Plumbing Co" --json', {'time_activity': ID})
EXAMPLES['time-activity update'] = Example(
    f'bookflow time-activity update {ID} --duration 2 --expected-version 1 --company "Demo Plumbing Co" --reason "Timesheet said two hours" --json',
    {'time_activity': ID, 'duration': '2', 'expected_version': 1})
EXAMPLES['time-activity void'] = Example(
    f'bookflow time-activity void {ID} --expected-version 1 --company "Demo Plumbing Co" --reason "Logged against the wrong job" --json',
    {'time_activity': ID, 'expected_version': 1})
EXAMPLES['time-activity query'] = Example(
    f'bookflow time-activity query --customer "Riverside Apartments" --date-from 2026-09-01 --date-to 2026-09-30 --limit 25 --company "Demo Plumbing Co" --json',
    {'customer': 'Riverside Apartments', 'date_from': '2026-09-01', 'date_to': '2026-09-30', 'limit': 25})
EXAMPLES['time-activity history'] = Example(
    f'bookflow time-activity history {ID} --limit 25 --company "Demo Plumbing Co" --json', {'time_activity': ID, 'limit': 25})

# Linked work billing uses canonical sources and permanent conversion intent.
for _noun, _selector in (("estimate", "estimate"), ("work-order", "work_order"),
                         ("time-activity", "time_activity")):
    EXAMPLES[_noun + " billing"] = Example(
        f'bookflow {_noun} billing {ID} --company "Demo Plumbing Co" --limit 50 --json', {_selector: ID, 'limit': 50})
    EXAMPLES[_noun + " invoice"] = Example(
        f'bookflow {_noun} invoice {ID} --expected-version 2 --conversion-key "bill-work-2026-09" --date 2026-09-04 --percent 25 --company "Demo Plumbing Co" --reason "Invoice one quarter of agreed scope" --json',
        {_selector: ID, 'expected_version': 2, 'conversion_key': 'bill-work-2026-09', 'date': '2026-09-04', 'percent': '25'})
    EXAMPLES[_noun + " sales-receipt"] = Example(
        f'bookflow {_noun} sales-receipt {ID} --expected-version 2 --conversion-key "paid-work-2026-09" --date 2026-09-04 --deposit-to Checking --payment-method Cash --amount-received "10.81" --company "Demo Plumbing Co" --reason "Record paid work" --json',
        {_selector: ID, 'expected_version': 2, 'conversion_key': 'paid-work-2026-09', 'date': '2026-09-04', 'deposit_to': 'Checking', 'payment_method': 'Cash', 'amount_received': '10.81'})

# Row22 receipt preparation and immutable settlement commands.
_PAYMENT_RECEIVE = dict(customer='Riverside Apartments', date='2026-06-01', amount='147.00', payment_method='Check', reference='1042',
                        operation_key='example-receipt-1',
                        applications=dict(mode='inline', items=[dict(invoice=ID, expected_version=1, amount='147.00')]),
                        discounts=[dict(invoice=ID, amount='3.00')])
_PAYMENT_EXAMPLES = {
    'payment receive': _PAYMENT_RECEIVE,
    'payment apply': dict(payment=ID, expected_version=1, date='2026-06-01', operation_key='example-apply-1', applications=dict(mode='inline', items=[dict(invoice=ID, expected_version=1, amount='50.00')])),
    'payment show': dict(payment=ID),
    'payment update': dict(payment=ID, expected_version=1, operation_key='example-correction-1', memo='Corrected remittance note'),
    'payment unapply': dict(payment=ID, expected_version=2, operation_key='example-unapply-1', applications=[dict(application_id=ID, invoice_expected_version=2)]),
    'payment void': dict(payment=ID, expected_version=3, operation_key='example-void-1'),
    'payment bounce': dict(payment=ID, expected_version=2, date='2026-07-09', operation_key='example-bounce-1',
                           bank_fee_amount='12.00', bank_fee_account='Bank Service Charges',
                           customer_fee_amount='35.00'),
    'payment history': dict(payment=ID, limit=25),
    'payment settlement changes': dict(guard='authenticated-guard-from-payment-show', limit=25),
    'application show': dict(application=ID),
    'application history': dict(application=ID, limit=25),
    'payment query': dict(payment_method='Check', limit=25),
    'payment invoices': dict(mode='new_receipt', customer='Riverside Apartments', date='2026-06-01'),
    'payment suggest': dict(mode='new_receipt', customer='Riverside Apartments', date='2026-06-01', amount='400.00', strategy='exact_then_oldest'),
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
    **{'payment ' + verb: 'payment' for verb in ('update', 'unapply', 'void', 'history', 'bounce')},
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
        # The CLI flattens the applications object into one flag per member (blueprint 5.1).
        for _flag, _member in ([(_field + '_' + key, member) for key, member in _value.items()]
                               if _field == 'applications' and isinstance(_value, dict) else [(_field, _value)]):
            _args.extend(['--' + _flag.replace('_', '-'), _payment_json.dumps(_member, separators=(',', ':')) if isinstance(_member, (dict, list)) else str(_member)])
    _args.extend(['--company', 'Demo Plumbing Co', '--json'])
    if _name in ('payment update', 'payment unapply', 'payment void'):
        _args.extend(['--reason', 'Correct recorded remittance'])
    if _name == 'payment bounce':
        _args.extend(['--reason', 'Bank returned the check unpaid'])
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
    "deposit history": Example(
        f'bookflow deposit history {ID} --page-limit 50 --company "Demo Plumbing Co" --json',
        {"deposit": ID, "page": {"limit": 50}},
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
        ' --bills \'[{"bill":"BILL-104","amount":"607.60","discount":"12.40"},{"bill":"BILL-108","amount":"250.00"}]\''
        ' --company "Demo Plumbing Co" --reason "Pay the April bills" --json',
        {"date": "2026-04-15", "funding_account": "Checking", "method": "Check",
         "check_number": "1041", "memo": "April payables",
         "bills": [{"bill": "BILL-104", "amount": "607.60", "discount": "12.40"},
                   {"bill": "BILL-108", "amount": "250.00"}]}),
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
    "bill payment history": Example(
        f'bookflow bill payment history {ID} --limit 25 --company "Demo Plumbing Co" --json',
        {"payment": ID, "limit": 25}),
})


# A vendor credit is the bill read backwards, so its entry example is the bill's with the
# amount coming back instead of going out; applying it reads like applying a check, because it
# is the same settlement edge.
EXAMPLES.update({
    "vendor-credit post": Example(
        'bookflow vendor-credit post --vendor "Northside Supply" --date 2026-04-18'
        ' --supplier-reference CN-118 --memo "Returned fittings"'
        ' --expenses \'[{"account":"Office Supplies","amount":"46.25","memo":"Returned fittings"}]\''
        ' --company "Demo Plumbing Co" --reason "Enter the vendor credit" --json',
        {"vendor": "Northside Supply", "date": "2026-04-18", "supplier_reference": "CN-118",
         "memo": "Returned fittings",
         "expenses": [{"account": "Office Supplies", "amount": "46.25",
                       "memo": "Returned fittings"}]}),
    "vendor-credit update": Example(
        f'bookflow vendor-credit update {ID} --expected-version 1 --supplier-reference CN-119'
        ' --company "Demo Plumbing Co" --reason "The credit note number was mistyped" --json',
        {"credit": ID, "expected_version": 1, "supplier_reference": "CN-119"}),
    "vendor-credit show": Example(
        f'bookflow vendor-credit show {ID} --company "Demo Plumbing Co" --json', {"credit": ID}),
    "vendor-credit history": Example(
        f'bookflow vendor-credit history {ID} --limit 25 --company "Demo Plumbing Co" --json',
        {"credit": ID, "limit": 25}),
    "vendor-credit query": Example(
        'bookflow vendor-credit query --vendor "Northside Supply" --date-from 2026-04-01'
        ' --status posted --limit 25 --company "Demo Plumbing Co" --json',
        {"vendor": "Northside Supply", "date_from": "2026-04-01", "status": "posted", "limit": 25}),
    "vendor-credit void": Example(
        f'bookflow vendor-credit void {ID} --expected-version 1 --company "Demo Plumbing Co"'
        ' --reason "Credited against the wrong vendor" --json',
        {"credit": ID, "expected_version": 1}),
    "vendor-credit apply": Example(
        f'bookflow vendor-credit apply {ID} --expected-version 1'
        ' --bills \'[{"bill":"BILL-104"}]\''
        ' --company "Demo Plumbing Co" --reason "Settle the April bill with the credit" --json',
        {"credit": ID, "expected_version": 1, "bills": [{"bill": "BILL-104"}]}),
    "vendor-credit unapply": Example(
        f'bookflow vendor-credit unapply {ID} --expected-version 2 --company "Demo Plumbing Co"'
        ' --reason "Applied to the wrong bill" --json',
        {"credit": ID, "expected_version": 2}),
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
    "credit-memo update": Example(
        f'bookflow credit-memo update {ID} --expected-version 1 --memo "Corrected credit note"'
        ' --company "Demo Plumbing Co" --reason "Correct the note" --json',
        {"credit_memo": ID, "expected_version": 1, "memo": "Corrected credit note"}),
    "credit-memo show": Example(
        f'bookflow credit-memo show {ID} --company "Demo Plumbing Co" --json', {"credit_memo": ID}),
    "credit-memo history": Example(
        f'bookflow credit-memo history {ID} --limit 25 --company "Demo Plumbing Co" --json',
        {"credit_memo": ID, "limit": 25}),
})


# Sales tax is read before it is paid, so the liability example names the period a bookkeeper
# would actually ask about -- the quarter's tax and what is owed at its end -- and the remittance
# answers exactly that period.
EXAMPLES.update({
    "sales-tax liability": Example(
        'bookflow sales-tax liability --date-from 2026-01-01 --date-to 2026-03-31 --limit 25'
        ' --company "Demo Plumbing Co" --json',
        {"date_from": "2026-01-01", "date_to": "2026-03-31", "limit": 25}),
    "sales-tax pay": Example(
        'bookflow sales-tax pay --agency "State Board of Equalization" --date 2026-04-20'
        ' --through-date 2026-03-31 --funding-account "Checking" --method "Check"'
        ' --check-number 1052 --memo "Q1 sales tax"'
        ' --company "Demo Plumbing Co" --reason "Remit the first-quarter sales tax" --json',
        {"agency": "State Board of Equalization", "date": "2026-04-20",
         "through_date": "2026-03-31", "funding_account": "Checking", "method": "Check",
         "check_number": "1052", "memo": "Q1 sales tax"}),
    "sales-tax payment show": Example(
        f'bookflow sales-tax payment show {ID} --company "Demo Plumbing Co" --json',
        {"payment": ID}),
    "sales-tax payment query": Example(
        'bookflow sales-tax payment query --agency "State Board of Equalization"'
        ' --date-from 2026-01-01 --status posted --limit 25 --company "Demo Plumbing Co" --json',
        {"agency": "State Board of Equalization", "date_from": "2026-01-01",
         "status": "posted", "limit": 25}),
    "sales-tax payment void": Example(
        f'bookflow sales-tax payment void {ID} --expected-version 1 --company "Demo Plumbing Co"'
        ' --reason "Remitted from the wrong bank account" --json',
        {"payment": ID, "expected_version": 1}),
    # Adjust Sales Tax Due: the agency charged a late-filing penalty the sales never recorded.
    "sales-tax adjust": Example(
        'bookflow sales-tax adjust --agency "State Board of Equalization" --date 2026-04-20'
        ' --adjustment-account "Penalties" --direction increase --amount 25.00'
        ' --memo "Late-filing penalty, Q1" --company "Demo Plumbing Co"'
        ' --reason "Record the penalty the agency assessed" --json',
        {"agency": "State Board of Equalization", "date": "2026-04-20",
         "adjustment_account": "Penalties", "direction": "increase", "amount": "25.00",
         "memo": "Late-filing penalty, Q1"}),
    "sales-tax adjustment show": Example(
        f'bookflow sales-tax adjustment show {ID} --company "Demo Plumbing Co" --json',
        {"adjustment": ID}),
    "sales-tax adjustment query": Example(
        'bookflow sales-tax adjustment query --agency "State Board of Equalization"'
        ' --date-from 2026-01-01 --status posted --limit 25 --company "Demo Plumbing Co" --json',
        {"agency": "State Board of Equalization", "date_from": "2026-01-01",
         "status": "posted", "limit": 25}),
    "sales-tax adjustment void": Example(
        f'bookflow sales-tax adjustment void {ID} --expected-version 1 --company "Demo Plumbing Co"'
        ' --reason "Entered against the wrong agency" --json',
        {"adjustment": ID, "expected_version": 1}),
})


# What a credit can become: it is listed, applied to an invoice, taken back off, refunded in
# cash, or voided outright. Each of the three dispositions the anchor's own dialog offers has
# a command here, and the list is where you find what a customer still has in hand.
EXAMPLES.update({
    "credit-memo query": Example(
        'bookflow credit-memo query --customer "Rivera Construction" --available-only --limit 25'
        ' --company "Demo Plumbing Co" --json',
        {"customer": "Rivera Construction", "available_only": True, "limit": 25}),
    "credit-memo void": Example(
        f'bookflow credit-memo void {ID} --expected-version 1 --company "Demo Plumbing Co"'
        ' --reason "Issued to the wrong customer" --json',
        {"credit_memo": ID, "expected_version": 1}),
    "customer-credit apply": Example(
        f'bookflow customer-credit apply {ID} --expected-version 1'
        ' --applications \'[{"invoice":"INV-118","expected_version":1,"amount":"30.00"}]\''
        ' --company "Demo Plumbing Co" --reason "Use the credit against the open invoice" --json',
        {"credit_memo": ID, "expected_version": 1,
         "applications": [{"invoice": "INV-118", "expected_version": 1, "amount": "30.00"}]}),
    "customer-credit unapply": Example(
        f'bookflow customer-credit unapply {ID} --expected-version 2'
        f' --applications \'[{{"application_id":"{ID}","invoice_expected_version":2}}]\''
        ' --company "Demo Plumbing Co" --reason "Applied to the wrong invoice" --json',
        {"credit_memo": ID, "expected_version": 2,
         "applications": [{"application_id": ID, "invoice_expected_version": 2}]}),
    "customer-refund post": Example(
        'bookflow customer-refund post --date 2026-04-12 --funding-account "Checking"'
        ' --method "Check" --check-number 1041'
        f' --sources \'[{{"credit_memo":"{ID}"}}]\''
        ' --company "Demo Plumbing Co" --reason "The customer asked for the money back" --json',
        {"date": "2026-04-12", "funding_account": "Checking", "method": "Check",
         "check_number": "1041", "sources": [{"credit_memo": ID}]}),
    "customer-refund show": Example(
        f'bookflow customer-refund show {ID} --company "Demo Plumbing Co" --json', {"refund": ID}),
    "customer-refund history": Example(
        f'bookflow customer-refund history {ID} --limit 25 --company "Demo Plumbing Co" --json',
        {"refund": ID, "limit": 25}),
    "customer-refund query": Example(
        'bookflow customer-refund query --customer "Rivera Construction" --limit 25'
        ' --company "Demo Plumbing Co" --json',
        {"customer": "Rivera Construction", "limit": 25}),
    "customer-refund update": Example(
        f'bookflow customer-refund update {ID} --expected-version 1 --check-number 1042'
        ' --company "Demo Plumbing Co" --reason "The first cheque was spoiled" --json',
        {"refund": ID, "expected_version": 1, "check_number": "1042"}),
    "customer-refund void": Example(
        f'bookflow customer-refund void {ID} --expected-version 1 --company "Demo Plumbing Co"'
        ' --reason "Drawn on the wrong bank account" --json',
        {"refund": ID, "expected_version": 1}),
})


# A statement charge is entered the way the anchor's own guide describes it: the quarter hour
# a lawyer bills today, charged to the client's account with no invoice, and read as one total
# on the statement at the end of the month.
EXAMPLES.update({
    "statement-charge post": Example(
        'bookflow statement-charge post --customer "Hayes, Marcus" --date 2026-04-18'
        ' --item "Consultation" --quantity 0.25 --rate 240.00'
        ' --description "Call about the lease renewal"'
        ' --company "Demo Plumbing Co" --reason "Charge the client for the call" --json',
        {"customer": "Hayes, Marcus", "date": "2026-04-18", "item": "Consultation",
         "quantity": "0.25", "rate": "240.00",
         "description": "Call about the lease renewal"}),
    "statement-charge show": Example(
        f'bookflow statement-charge show {ID} --company "Demo Plumbing Co" --json',
        {"statement_charge": ID}),
    "statement-charge query": Example(
        'bookflow statement-charge query --customer "Hayes, Marcus" --date-from 2026-04-01'
        ' --status posted --limit 25 --company "Demo Plumbing Co" --json',
        {"customer": "Hayes, Marcus", "date_from": "2026-04-01", "status": "posted", "limit": 25}),
    "statement-charge void": Example(
        f'bookflow statement-charge void {ID} --expected-version 1 --company "Demo Plumbing Co"'
        ' --reason "Charged to the wrong client" --json',
        {"statement_charge": ID, "expected_version": 1}),
})
# A purchase order is the bill's form before anything is owed: the same vendor and the same
# job and class columns, one ordered grid whose rows may name an item or an account, and no
# accounting at all until a bill is entered from it.
EXAMPLES.update({
    "purchase-order post": Example(
        'bookflow purchase-order post --vendor "Northside Supply" --date 2026-04-02'
        ' --expected-date 2026-04-16 --reference QUOTE-88 --memo "April restock"'
        ' --lines \'[{"item":"Copper Pipe","quantity":"40","rate":"12.50"},'
        '{"account":"Office Supplies","description":"Fittings","amount":"75.00"}]\''
        ' --company "Demo Plumbing Co" --reason "Order April stock" --json',
        {"vendor": "Northside Supply", "date": "2026-04-02", "expected_date": "2026-04-16",
         "reference": "QUOTE-88", "memo": "April restock",
         "lines": [{"item": "Copper Pipe", "quantity": "40", "rate": "12.50"},
                   {"account": "Office Supplies", "description": "Fittings", "amount": "75.00"}]}),
    "purchase-order show": Example(
        f'bookflow purchase-order show {ID} --company "Demo Plumbing Co" --json',
        {"purchase_order": ID}),
    "purchase-order update": Example(
        f'bookflow purchase-order update {ID} --status partly_received --expected-version 1'
        ' --company "Demo Plumbing Co" --reason "Half the order arrived" --json',
        {"purchase_order": ID, "status": "partly_received", "expected_version": 1}),
    "purchase-order void": Example(
        f'bookflow purchase-order void {ID} --expected-version 1 --company "Demo Plumbing Co"'
        ' --reason "The vendor cannot supply it" --json',
        {"purchase_order": ID, "expected_version": 1}),
    "purchase-order close-lines": Example(
        f'bookflow purchase-order close-lines {ID} --lines \'["1"]\' --company "Demo Plumbing Co"'
        ' --reason "The rest is never coming" --json',
        {"purchase_order": ID, "lines": ["1"]}),
    "purchase-order query": Example(
        'bookflow purchase-order query --vendor "Northside Supply" --open-only --limit 25'
        ' --company "Demo Plumbing Co" --json',
        {"vendor": "Northside Supply", "open_only": True, "limit": 25}),
    "purchase-order history": Example(
        f'bookflow purchase-order history {ID} --limit 25 --company "Demo Plumbing Co" --json',
        {"purchase_order": ID, "limit": 25}),
})
EXAMPLES.update({
    "billing-group create": Example(
        'bookflow billing-group create --name "Monthly retainers"'
        ' --customers \'["Riverside Apartments","Rivera Construction"]\''
        ' --company "Demo Plumbing Co" --reason "Set up the retainer round" --json',
        {"name": "Monthly retainers",
         "customers": ["Riverside Apartments", "Rivera Construction"]}),
    "billing-group rename": Example(
        f'bookflow billing-group rename {ID} --name "Monthly retainers 2026" --expected-version 1'
        ' --company "Demo Plumbing Co" --reason "Name the year" --json',
        {"billing_group": ID, "name": "Monthly retainers 2026", "expected_version": 1}),
    "billing-group delete": Example(
        f'bookflow billing-group delete {ID} --expected-version 1 --company "Demo Plumbing Co"'
        ' --reason "The retainer round ended" --json',
        {"billing_group": ID, "expected_version": 1}),
    "billing-group add": Example(
        f'bookflow billing-group add {ID} --customers \'["Harborview Condos"]\''
        ' --company "Demo Plumbing Co" --reason "New retainer customer" --json',
        {"billing_group": ID, "customers": ["Harborview Condos"]}),
    "billing-group remove": Example(
        f'bookflow billing-group remove {ID} --customers \'["Harborview Condos"]\''
        ' --company "Demo Plumbing Co" --reason "They ended the retainer" --json',
        {"billing_group": ID, "customers": ["Harborview Condos"]}),
    "billing-group show": Example(
        f'bookflow billing-group show {ID} --company "Demo Plumbing Co" --json',
        {"billing_group": ID}),
    "billing-group list": Example(
        'bookflow billing-group list --query retainer --limit 25 --company "Demo Plumbing Co" --json',
        {"query": "retainer", "limit": 25}),
    "batch-invoice post": Example(
        'bookflow batch-invoice post --date 2026-09-01 --billing-group "Monthly retainers"'
        ' --lines \'[{"item":"Service Call","quantity":"1"}]\' --memo "September retainer"'
        ' --dry-run --company "Demo Plumbing Co" --reason "Bill the monthly retainer" --json',
        {"date": "2026-09-01", "billing_group": "Monthly retainers",
         "lines": [{"item": "Service Call", "quantity": "1"}], "memo": "September retainer"}),
    "batch-invoice retry": Example(
        f'bookflow batch-invoice retry {ID} --company "Demo Plumbing Co"'
        ' --reason "The tax code is fixed now" --json',
        {"batch": ID}),
    "batch-invoice show": Example(
        f'bookflow batch-invoice show {ID} --company "Demo Plumbing Co" --json', {"batch": ID}),
    "batch-invoice query": Example(
        'bookflow batch-invoice query --date-from 2026-01-01 --date-to 2026-12-31 --limit 25'
        ' --company "Demo Plumbing Co" --json',
        {"date_from": "2026-01-01", "date_to": "2026-12-31", "limit": 25}),
})
EXAMPLES.update({
    "memorized create": Example(
        'bookflow memorized create --name "Monthly office rent" --command "bill post"'
        ' --payload \'{"vendor":"Harbor Property","expenses":[{"account":"Rent","amount":"1800.00"}]}\''
        ' --frequency monthly --start-date 2026-05-01 --days-in-advance 5'
        ' --mode enter_automatically --company "Demo Plumbing Co" --json',
        {"name": "Monthly office rent", "command": "bill post",
         "payload": {"vendor": "Harbor Property",
                     "expenses": [{"account": "Rent", "amount": "1800.00"}]},
         "frequency": "monthly", "start_date": "2026-05-01", "days_in_advance": 5,
         "mode": "enter_automatically"}),
    "memorized update": Example(
        'bookflow memorized update "Monthly office rent" --expected-version 1'
        ' --frequency quarterly --start-date 2026-07-01 --company "Demo Plumbing Co" --json',
        {"memorized": "Monthly office rent", "expected_version": 1,
         "frequency": "quarterly", "start_date": "2026-07-01"}),
    "memorized delete": Example(
        'bookflow memorized delete "Monthly office rent" --expected-version 2'
        ' --company "Demo Plumbing Co" --json',
        {"memorized": "Monthly office rent", "expected_version": 2}),
    "memorized show": Example(
        'bookflow memorized show "Monthly office rent" --company "Demo Plumbing Co" --json',
        {"memorized": "Monthly office rent"}),
    "memorized list": Example(
        'bookflow memorized list --due-only --limit 25 --company "Demo Plumbing Co" --json',
        {"due_only": True, "limit": 25}),
    "memorized enter": Example(
        'bookflow memorized enter "Monthly office rent" --date 2026-05-01'
        ' --company "Demo Plumbing Co" --reason "Landlord asked for it early" --json',
        {"memorized": "Monthly office rent", "date": "2026-05-01"}),
    "memorized process": Example(
        'bookflow memorized process --as-of 2026-05-01 --limit 50 --company "Demo Plumbing Co"'
        ' --reason "Monthly run" --json',
        {"as_of": "2026-05-01", "limit": 50}),
    "memorized retry": Example(
        f'bookflow memorized retry {ID} --expected-version 2 --company "Demo Plumbing Co"'
        ' --reason "The account is active again" --json',
        {"occurrence": ID, "expected_version": 2}),
    "memorized skip": Example(
        f'bookflow memorized skip {ID} --expected-version 2 --company "Demo Plumbing Co"'
        ' --reason "No rent due this month" --json',
        {"occurrence": ID, "expected_version": 2}),
    "memorized-group create": Example(
        'bookflow memorized-group create --name "Month-end closing" --frequency monthly'
        ' --start-date 2026-05-31 --mode enter_automatically --company "Demo Plumbing Co" --json',
        {"name": "Month-end closing", "frequency": "monthly", "start_date": "2026-05-31",
         "mode": "enter_automatically"}),
    "memorized-group update": Example(
        'bookflow memorized-group update "Month-end closing" --expected-version 1 --status paused'
        ' --company "Demo Plumbing Co" --json',
        {"memorized_group": "Month-end closing", "expected_version": 1, "status": "paused"}),
    "memorized-group delete": Example(
        'bookflow memorized-group delete "Month-end closing" --expected-version 2'
        ' --company "Demo Plumbing Co" --json',
        {"memorized_group": "Month-end closing", "expected_version": 2}),
    "memorized-group show": Example(
        'bookflow memorized-group show "Month-end closing" --company "Demo Plumbing Co" --json',
        {"memorized_group": "Month-end closing"}),
    "memorized-group list": Example(
        'bookflow memorized-group list --limit 25 --company "Demo Plumbing Co" --json',
        {"limit": 25}),
    "memorized-group enter": Example(
        'bookflow memorized-group enter "Month-end closing" --date 2026-05-31'
        ' --company "Demo Plumbing Co" --reason "Close the month" --json',
        {"memorized_group": "Month-end closing", "date": "2026-05-31"}),
})

# Bank reconciliation: adopt an opening balance, open a statement, tick it, certify it. The
# invocation is derived from the payload so the shown command and the shown input cannot drift.
_RECONCILE_FINGERPRINT = 'c' * 64
_RECONCILE_EXAMPLES = {
    'reconcile opening start': dict(
        account='Checking', opening_date='2026-01-31', entered_balance='1250.00',
        evidence=dict(format=1, statement_reference='Jan 2026 checking statement', entered_text=None),
        operation_key='example-opening-1'),
    'reconcile start': dict(
        account='Checking', statement_date='2026-02-28', ending_balance='1482.50',
        operation_key='example-statement-1'),
    'reconcile finish': dict(
        draft=ID, expected_version=2, operation_key='example-finish-1',
        expected_facts_fingerprint=_RECONCILE_FINGERPRINT, dependency_guard=_RECONCILE_FINGERPRINT),
    'reconcile candidates': dict(
        draft=ID, limit=50,
        filters=dict(from_date='2026-02-01', to_date='2026-02-28', sort='date')),
    'reconcile preview': dict(draft=ID, expected_version=2),
}
# Only the writes carry a reason; the two reads change nothing and are not asked for one.
_RECONCILE_REASONS = {
    'reconcile opening start': 'Adopt the checking account opening balance',
    'reconcile start': 'Reconcile the February checking statement',
    'reconcile mark': 'Clear the movements the statement shows',
    'reconcile finish': 'Certify the February checking statement',
}
for _name, _payload in _RECONCILE_EXAMPLES.items():
    _args = ['bookflow', *_name.split()]
    _positional = 'draft' if 'draft' in _payload else None
    if _positional:
        _args.append(str(_payload[_positional]))
    for _field, _value in _payload.items():
        if _field == _positional:
            continue
        _args.extend(['--' + _field.replace('_', '-'),
                      _payment_json.dumps(_value, separators=(',', ':')) if isinstance(_value, (dict, list)) else str(_value)])
    _args.extend(['--company', 'Demo Plumbing Co'])
    if _name in _RECONCILE_REASONS:
        _args.extend(['--reason', _RECONCILE_REASONS[_name]])
    _args.append('--json')
    EXAMPLES[_name] = Example(' '.join(_payment_shell.quote(value) for value in _args), _payload)

# `reconcile mark` takes either named movements or `--all`; the example shows the one a person
# types, ticking everything on the statement. The named form is in the command's description.
_markall_input = dict(draft=ID, expected_version=1, operation_key='example-mark-1', all=True)
EXAMPLES['reconcile mark'] = Example(
    f'bookflow reconcile mark {ID} --expected-version 1 --operation-key example-mark-1 --all '
    '--company "Demo Plumbing Co" --reason "Clear everything the statement shows" --dry-run --json',
    _markall_input)

# The statement file travels as its text: the shell reads it, the command never opens a path.
EXAMPLES['reconcile import'] = Example(
    'bookflow reconcile import --account Checking --content "$(cat checking-2026-09.ofx)" --start'
    ' --company "Demo Plumbing Co" --reason "Import the September checking statement" --json',
    {"account": "Checking", "start": True,
     "content": "OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\n\n<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS>"
                "<CURDEF>USD<BANKTRANLIST><STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260930<TRNAMT>-12.00"
                "<FITID>202609300001<NAME>MONTHLY SERVICE FEE</STMTTRN></BANKTRANLIST>"
                "<LEDGERBAL><BALAMT>6236.95<DTASOF>20260930</LEDGERBAL></STMTRS></STMTTRNRS>"
                "</BANKMSGSRSV1></OFX>"})

# Receiving uses the same registered commands in CLI, HTTP, Python and MCP.
EXAMPLES.update({
    'item-receipt post': Example(
        f'bookflow item-receipt post --vendor "Northside Supply" --date 2026-04-02 --shipping 12.00 --items \'[{{"item":"{ID}","quantity":"6","unit_cost":"10.00"}}]\' --company "Demo Plumbing Co" --reason "Receive goods before bill" --json',
        {'vendor': 'Northside Supply', 'date': '2026-04-02', 'shipping': '12.00', 'items': [{'item': ID, 'quantity': '6', 'unit_cost': '10.00'}]}),
    'item-receipt show': Example(
        f'bookflow item-receipt show {ID} --company "Demo Plumbing Co" --json', {'receipt': ID}),
    'item-receipt query': Example(
        'bookflow item-receipt query --unbilled-only --limit 50 --company "Demo Plumbing Co" --json', {'unbilled_only': True, 'limit': 50}),
    'item-receipt update': Example(
        f'bookflow item-receipt update {ID} --expected-version 1 --memo "Dock counted six" --company "Demo Plumbing Co" --reason "Record dock note" --json',
        {'receipt': ID, 'expected_version': 1, 'memo': 'Dock counted six'}),
    'item-receipt void': Example(
        f'bookflow item-receipt void {ID} --expected-version 1 --company "Demo Plumbing Co" --reason "Return received goods" --json',
        {'receipt': ID, 'expected_version': 1}),
    'item-receipt history': Example(
        f'bookflow item-receipt history {ID} --company "Demo Plumbing Co" --json', {'receipt': ID}),
})

for noun, selector in (('check','check'),('card-charge','card_charge')):
    EXAMPLES[noun+' delete'] = Example(
        f'bookflow {noun} delete {ID} --expected-version 1 --operation-key purchase-delete-1 --reason "Remove duplicate purchase" --company "Demo Plumbing Co" --json',
        {selector:ID,'expected_version':1,'operation_key':'purchase-delete-1'})

for _sales_noun in ('invoice', 'sales-receipt'):
    EXAMPLES[_sales_noun+' delete'] = Example(
        f'bookflow {_sales_noun} delete {ID} --expected-version 1 --operation-key sales-delete-example --reason "Remove duplicate sale" --dry-run --json',
        {_sales_noun.replace('-', '_'): ID, 'expected_version': 1, 'operation_key': 'sales-delete-example'})

EXAMPLES['payment delete'] = Example(
    f'bookflow payment delete {ID} --expected-version 1 --operation-key payment-delete-example --reason "Remove duplicate receipt" --company "Demo Plumbing Co" --json',
    {'payment': ID, 'expected_version': 1, 'operation_key': 'payment-delete-example'})

EXAMPLES['journal delete'] = Example(
    f'bookflow journal delete {ID} --expected-version 1 --operation-key journal-delete-example --reason "Remove duplicate entry" --company "Demo Plumbing Co" --json',
    {'journal': ID, 'expected_version': 1, 'operation_key': 'journal-delete-example'})

EXAMPLES['bill delete'] = Example(
    f'bookflow bill delete {ID} --expected-version 1 --operation-key bill-delete-example --reason "Remove duplicate bill" --company "Demo Plumbing Co" --json',
    {'bill': ID, 'expected_version': 1, 'operation_key': 'bill-delete-example'})
EXAMPLES['credit-memo delete'] = Example(
    f'bookflow credit-memo delete {ID} --expected-version 1 --operation-key credit-delete-example --reason "Credited the wrong customer" --company "Demo Plumbing Co" --json',
    {'credit_memo': ID, 'expected_version': 1, 'operation_key': 'credit-delete-example'})
# A deposit is confirmed against the dependency guard its own preview minted, so the
# example shows the second call -- the one that actually saves.
EXAMPLES['deposit delete'] = Example(
    f'bookflow deposit delete {ID} --expected-version 1 --operation-key deposit-delete-example --dependency-guard GUARD --reason "Banked into the wrong account" --company "Demo Plumbing Co" --json',
    {'deposit': ID, 'expected_version': 1, 'operation_key': 'deposit-delete-example'})


# The examples a first-time agent reaches for first (blind trials, 2026-09-27) show the real
# search and filter fields rather than only a page size.
EXAMPLES.update({
    "customer query": Example('bookflow customer query --query Riverside --limit 25 --company "Demo Plumbing Co" --json',
                              {"query": "Riverside", "limit": 25}),
    "invoice query": Example('bookflow invoice query --customer "Riverside Apartments" --settlement open --direction asc'
                             ' --limit 25 --company "Demo Plumbing Co" --json',
                             {"customer": "Riverside Apartments", "settlement": "open", "direction": "asc", "limit": 25}),
})

# Restoring a deleted document posts a new one; the preview shows what it would post.
EXAMPLES['journal restore'] = Example(
    f'bookflow journal restore {ID} --reason "Deleted by mistake" --company "Demo Plumbing Co" --dry-run --json',
    {'journal': ID})
EXAMPLES['invoice restore'] = Example(
    f'bookflow invoice restore {ID} --date 2026-07-01 --reason "Deleted by mistake" --company "Demo Plumbing Co" --json',
    {'invoice': ID, 'date': '2026-07-01'})
# R117: merging a duplicate customer or vendor. `--dry-run` is the preview.
EXAMPLES.update({
    "customer merge": Example('bookflow customer merge "Riverside Apts" "Riverside Apartments" --reason "Same customer entered twice"'
                              ' --company "Demo Plumbing Co" --json',
                              {"merged": "Riverside Apts", "into": "Riverside Apartments"}),
    "customer unmerge": Example('bookflow customer unmerge "Riverside Apts" --reason "They are two companies after all"'
                                ' --company "Demo Plumbing Co" --json', {"merged": "Riverside Apts"}),
    "vendor merge": Example('bookflow vendor merge "Central Supply Co" "Central Supply" --reason "Same supplier entered twice"'
                            ' --company "Demo Plumbing Co" --json',
                            {"merged": "Central Supply Co", "into": "Central Supply"}),
    "vendor unmerge": Example('bookflow vendor unmerge "Central Supply Co" --reason "Two suppliers after all"'
                              ' --company "Demo Plumbing Co" --json', {"merged": "Central Supply Co"}),
    # R117, second part: accounts and items, the same alias with their editable references re-pointed.
    "account merge": Example('bookflow account merge "Office Supply" "Office Supplies" --reason "Same account entered twice"'
                             ' --company "Demo Plumbing Co" --json',
                             {"merged": "Office Supply", "into": "Office Supplies"}),
    "account unmerge": Example('bookflow account unmerge "Office Supply" --reason "Two accounts after all"'
                               ' --company "Demo Plumbing Co" --json', {"merged": "Office Supply"}),
    "item merge": Example('bookflow item merge "Drain Clean" "Drain Cleaning" --reason "Same service entered twice"'
                          ' --company "Demo Plumbing Co" --json',
                          {"merged": "Drain Clean", "into": "Drain Cleaning"}),
    "item unmerge": Example('bookflow item unmerge "Drain Clean" --reason "Two services after all"'
                            ' --company "Demo Plumbing Co" --json', {"merged": "Drain Clean"}),
})
# R179: what a 1099 vendor was paid before the books began here, from the old books' 1099 Summary.
EXAMPLES["vendor 1099-opening"] = Example(
    'bookflow vendor 1099-opening "Delgado, Ray" --year 2026 --as-of 2026-06-30 --amount 7550.00'
    ' --reason "Paid January to June in the old books" --company "Demo Plumbing Co" --json',
    {"vendor": "Delgado, Ray", "year": 2026, "as_of": "2026-06-30", "amount": "7550.00"})
# R170 blind July: a small text attachment read as text -- a statement's summary box, kept as an attachment.
EXAMPLES["attachment text"] = Example(f'bookflow attachment text {ID} --company "Demo Plumbing Co" --json', {"attachment": ID})
# R138: find any transaction -- "the $184.00 check to Regional Parts".
EXAMPLES["transaction find"] = Example(
    'bookflow transaction find --types \'["check"]\' --name Regional --amount 184.00 --company "Demo Plumbing Co" --json',
    {"types": ["check"], "name": "Regional", "amount": "184.00"})
# R154: a non-inventory part converted to an inventory part, one way. `--dry-run` is the preview.
EXAMPLES["item convert"] = Example(
    'bookflow item convert "Copper Coupling" --to inventory --reason "We stock couplings now"'
    ' --company "Demo Plumbing Co" --json', {"item": "Copper Coupling", "to": "inventory"})
# R120: month-end finance charges by the company policy; run with --dry-run first to see the rows.
EXAMPLES["finance-charge assess"] = Example(
    'bookflow finance-charge assess --date 2026-07-31 --reason "Month-end finance charges" --company "Demo Plumbing Co" --json',
    {"date": "2026-07-31"})
# R149: a vendor's refund check for an open vendor credit, banked and settled in one step.
EXAMPLES["vendor-credit refund"] = Example(
    f'bookflow vendor-credit refund {ID} --deposit-to Checking --date 2026-05-20 --amount 120.00'
    ' --payment-method Check --check-number 8812 --company "Demo Plumbing Co"'
    ' --reason "Acme sent a refund check for our return" --json',
    {"credit": ID, "deposit_to": "Checking", "date": "2026-05-20", "amount": "120.00",
     "payment_method": "Check", "check_number": "8812"})
# R109: budgets, the budget and comparison reports, and memorized reports.
_CO = ' --company "Demo Plumbing Co"'
EXAMPLES.update({
    "budget create": Example('bookflow budget create --name "Plan 2026" --fiscal-year 2026 --from-actuals' + _CO + ' --reason "Next year\'s budget" --json',
                             {"name": "Plan 2026", "fiscal_year": 2026, "from_actuals": True}),
    "budget set": Example('bookflow budget set "Plan 2026" --account "Service Income" --start-month 2026-03 --amounts \'["1750.00","1800.00"]\'' + _CO + ' --reason "Spring estimate" --json',
                          {"budget": "Plan 2026", "account": "Service Income", "start_month": "2026-03", "amounts": ["1750.00", "1800.00"]}),
    "budget fill": Example('bookflow budget fill "Plan 2026" --account Rent --amount 1000.00 --change-percent 1' + _CO + ' --reason "Rent rises 1% a month" --json',
                           {"budget": "Plan 2026", "account": "Rent", "amount": "1000.00", "change_percent": "1"}),
    "budget clear": Example('bookflow budget clear "Plan 2026" --account Rent' + _CO + ' --reason "Start rent over" --json',
                            {"budget": "Plan 2026", "account": "Rent"}),
    "budget rename": Example('bookflow budget rename "Plan 2026" --name "Plan 2026 (board)"' + _CO + ' --reason "Approved" --json',
                             {"budget": "Plan 2026", "name": "Plan 2026 (board)"}),
    "budget delete": Example('bookflow budget delete "Plan 2026 draft" --expected-version 3' + _CO + ' --reason "Superseded" --json',
                             {"budget": "Plan 2026 draft", "expected_version": 3}),
    "budget show": Example('bookflow budget show "Plan 2026"' + _CO + ' --json', {"budget": "Plan 2026"}),
    "budget list": Example('bookflow budget list --fiscal-year 2026' + _CO + ' --json', {"fiscal_year": 2026}),
    "report budget-overview": Example('bookflow report budget-overview --budget "Plan 2026" --columns quarter' + _CO + ' --json',
                                      {"budget": "Plan 2026", "columns": "quarter"}),
    "report budget-vs-actual": Example('bookflow report budget-vs-actual --budget "Plan 2026" --date-from 2026-07-01 --date-to 2026-09-30' + _CO + ' --json',
                                       {"budget": "Plan 2026", "date_from": "2026-07-01", "date_to": "2026-09-30"}),
    "report profit-and-loss-comparison": Example('bookflow report profit-and-loss-comparison --date-from 2026-01-01 --date-to 2026-06-30 --compare previous_year' + _CO + ' --json',
                                                 {"date_from": "2026-01-01", "date_to": "2026-06-30", "compare": "previous_year"}),
    "report balance-sheet-comparison": Example('bookflow report balance-sheet-comparison --date-to 2026-12-31' + _CO + ' --json',
                                               {"date_to": "2026-12-31"}),
    "memorized-report create": Example('bookflow memorized-report create --name "Budget this quarter" --report budget-vs-actual --filters \'{"budget": "Plan 2026"}\' --date-range this_fiscal_quarter --group Company' + _CO + ' --reason "Monthly review" --json',
                                       {"name": "Budget this quarter", "report": "budget-vs-actual", "filters": {"budget": "Plan 2026"}, "date_range": "this_fiscal_quarter", "group": "Company"}),
    "memorized-report update": Example('bookflow memorized-report update "Budget this quarter" --group Accountant' + _CO + ' --reason "Refile" --json',
                                       {"memorized_report": "Budget this quarter", "group": "Accountant"}),
    "memorized-report delete": Example('bookflow memorized-report delete "Budget this quarter"' + _CO + ' --reason "No longer used" --json',
                                       {"memorized_report": "Budget this quarter"}),
    "memorized-report show": Example('bookflow memorized-report show "Budget this quarter"' + _CO + ' --json', {"memorized_report": "Budget this quarter"}),
    "memorized-report list": Example('bookflow memorized-report list --group Company' + _CO + ' --json', {"group": "Company"}),
    "memorized-report run": Example('bookflow memorized-report run "Budget this quarter"' + _CO + ' --json', {"memorized_report": "Budget this quarter"}),
})
# R124: customer prepayments -- a deposit held until the work bills.
_C = ' --company "Demo Plumbing Co" --json'
EXAMPLES["prepayment receive"] = Example(
    'bookflow prepayment receive --document "estimate DEMO-WORK-EST-1B" --amount 200.00 --payment-method Check'
    ' --reference 4471' + _C, {"document": "estimate DEMO-WORK-EST-1B", "amount": "200.00", "payment_method": "Check",
                               "reference": "4471"})
EXAMPLES["prepayment attach"] = Example(
    'bookflow prepayment attach 57 --document "work-order DEMO-WORK-WO-2"' + _C,
    {"receipt": "57", "document": "work-order DEMO-WORK-WO-2"})
EXAMPLES["prepayment apply"] = Example(
    'bookflow prepayment apply --invoice DEMO-SALE-INV-ACTIVE --document "estimate DEMO-WORK-EST-1B" --amount 150.00' + _C,
    {"invoice": "DEMO-SALE-INV-ACTIVE", "document": "estimate DEMO-WORK-EST-1B", "amount": "150.00"})
EXAMPLES["prepayment unapply"] = Example(
    'bookflow prepayment unapply 1044 --reason "Applied to the wrong invoice"' + _C, {"draw": "1044"})
EXAMPLES["prepayment refund"] = Example(
    'bookflow prepayment refund --document "estimate DEMO-WORK-EST-1B" --bank-account Checking --payment-method Check'
    ' --check-number 2210' + _C, {"document": "estimate DEMO-WORK-EST-1B", "bank_account": "Checking",
                                   "payment_method": "Check", "check_number": "2210"})
EXAMPLES["prepayment show"] = Example(
    'bookflow prepayment show --customer "Commercial Example Customer"' + _C, {"customer": "Commercial Example Customer"})
EXAMPLES["prepayment query"] = Example('bookflow prepayment query --held-only' + _C, {"held_only": True})
EXAMPLES["report customer-prepayments"] = Example(
    'bookflow report customer-prepayments --as-of 2026-12-31' + _C, {"as_of": "2026-12-31"})
# R101: the sales order. "Take an order for 10 valves for Riverside; 4 of the visit's parts are not wanted."
_ORDER = {'customer': 'Riverside Apartments', 'date': '2026-10-22',
          'lines': [{'item': 'Brass Shutoff Valve', 'quantity': '10'}, {'item': 'Mainline Clearing', 'quantity': '1'}]}
EXAMPLES['sales-order create'] = Example(
    'bookflow sales-order create --customer "Riverside Apartments" --date 2026-10-22 --lines '
    + _shlex.quote(_json.dumps(_ORDER['lines'])) + ' --company "Demo Plumbing Co" --reason "Riverside ordered 10 valves" --json', _ORDER)
EXAMPLES['sales-order show'] = Example(f'bookflow sales-order show {ID} --company "Demo Plumbing Co" --json', {'sales_order': ID})
EXAMPLES['sales-order update'] = Example(
    f'bookflow sales-order update {ID} --expected-version 1 --status closed --company "Demo Plumbing Co"'
    ' --reason "Nothing more will ship" --json', {'sales_order': ID, 'expected_version': 1, 'status': 'closed'})
EXAMPLES['sales-order copy'] = Example(
    f'bookflow sales-order copy {ID} --expected-version 1 --date 2026-11-01 --company "Demo Plumbing Co" --reason "Same order again" --json',
    {'sales_order': ID, 'expected_version': 1, 'date': '2026-11-01'})
EXAMPLES['sales-order void'] = Example(
    f'bookflow sales-order void {ID} --expected-version 2 --company "Demo Plumbing Co" --reason "Order withdrawn" --json',
    {'sales_order': ID, 'expected_version': 2})
EXAMPLES['sales-order query'] = Example(
    'bookflow sales-order query --customer "Riverside Apartments" --status open --limit 25 --company "Demo Plumbing Co" --json',
    {'customer': 'Riverside Apartments', 'status': 'open', 'limit': 25})
EXAMPLES['sales-order history'] = Example(f'bookflow sales-order history {ID} --limit 25 --company "Demo Plumbing Co" --json',
                                          {'sales_order': ID, 'limit': 25})
EXAMPLES['estimate sales-order'] = Example(
    f'bookflow estimate sales-order {ID} --expected-version 2 --conversion-key "riverside-order-2026-10" --date 2026-10-22'
    ' --ship-date 2026-10-29 --company "Demo Plumbing Co" --reason "Make the order from the accepted estimate" --json',
    {'estimate': ID, 'expected_version': 2, 'conversion_key': 'riverside-order-2026-10', 'date': '2026-10-22', 'ship_date': '2026-10-29'})
# R119: fixed-asset depreciation and loans.
EXAMPLES.update({
    "fixed-asset setup": Example(
        'bookflow fixed-asset setup "Service truck" --method declining_balance --declining-percent 200 --salvage 2000.00 --life-months 60 --period year --reason "Book depreciation"' + _C,
        {"item": "Service truck", "method": "declining_balance", "declining_percent": 200, "salvage": "2000.00", "life_months": 60, "period": "year"}),
    "fixed-asset schedule": Example('bookflow fixed-asset schedule "Service truck"' + _C, {"item": "Service truck"}),
    "fixed-asset list": Example('bookflow fixed-asset list' + _C, {}),
    "fixed-asset depreciate": Example(
        'bookflow fixed-asset depreciate --through 2026-11-30 --reason "Month-end depreciation"' + _C, {"through": "2026-11-30"}),
    "fixed-asset dispose": Example(
        'bookflow fixed-asset dispose "Service truck" --date 2026-12-01 --proceeds 30000.00 --deposit-to Checking --reason "Sold the truck"' + _C,
        {"item": "Service truck", "date": "2026-12-01", "proceeds": "30000.00", "deposit_to": "Checking"}),
    "loan create": Example(
        'bookflow loan create --name "Truck loan" --liability-account "Truck Loan" --interest-account "Interest Expense" --principal 25000.00 --annual-rate 6.5 --term-payments 60 --first-payment-date 2026-02-01 --payment-account Checking --reason "New truck loan"' + _C,
        {"name": "Truck loan", "liability_account": "Truck Loan", "interest_account": "Interest Expense", "principal": "25000.00",
         "annual_rate": "6.5", "term_payments": 60, "first_payment_date": "2026-02-01", "payment_account": "Checking"}),
    "loan update": Example('bookflow loan update "Truck loan" --annual-rate 5.9 --reason "Rate adjusted"' + _C,
                           {"loan": "Truck loan", "annual_rate": "5.9"}),
    "loan show": Example('bookflow loan show "Truck loan"' + _C, {"loan": "Truck loan"}),
    "loan list": Example('bookflow loan list' + _C, {}),
    "loan pay": Example('bookflow loan pay "Truck loan" --reason "December payment"' + _C, {"loan": "Truck loan"}),
})
# R103: build finished assemblies from their components; run with --dry-run first to see max_buildable.
EXAMPLES["assembly build"] = Example(
    'bookflow assembly build --item "Two-Valve Kit" --quantity 2 --date 2026-07-31 --reason "Built two kits today"'
    ' --company "Demo Plumbing Co" --json', {"item": "Two-Valve Kit", "quantity": "2", "date": "2026-07-31"})
EXAMPLES["assembly update"] = Example(
    'bookflow assembly update 01JASSEMBLYBUILD0000000001 --quantity 3 --reason "Three, not two"'
    ' --company "Demo Plumbing Co" --json', {"build": "01JASSEMBLYBUILD0000000001", "quantity": "3"})
EXAMPLES["assembly finalize"] = Example(
    'bookflow assembly finalize 01JASSEMBLYBUILD0000000001 --reason "Valves arrived"'
    ' --company "Demo Plumbing Co" --json', {"build": "01JASSEMBLYBUILD0000000001"})
EXAMPLES["assembly void"] = Example(
    'bookflow assembly void 01JASSEMBLYBUILD0000000001 --reason "Entered twice"'
    ' --company "Demo Plumbing Co" --json', {"build": "01JASSEMBLYBUILD0000000001"})
EXAMPLES["assembly delete"] = Example(
    'bookflow assembly delete 01JASSEMBLYBUILD0000000001 --reason "Not building these after all"'
    ' --company "Demo Plumbing Co" --json', {"build": "01JASSEMBLYBUILD0000000001"})
EXAMPLES["assembly show"] = Example(
    'bookflow assembly show 01JASSEMBLYBUILD0000000001 --company "Demo Plumbing Co" --json',
    {"build": "01JASSEMBLYBUILD0000000001"})
EXAMPLES["assembly list"] = Example(
    'bookflow assembly list --status pending --company "Demo Plumbing Co" --json', {"status": "pending"})
# R102: what customers have ordered and not yet received, by item, with on hand, available and on order.
EXAMPLES['report open-sales-orders'] = Example(
    'bookflow report open-sales-orders --by item --date-to 2026-10-31 --company "Demo Plumbing Co" --json', {'by': 'item', 'date_to': '2026-10-31'})

# Mileage (R118): a trip by total miles, billed to a job, and the rate that prices the deduction.
EXAMPLES["trip create"] = Example(
    'bookflow trip create --vehicle "Service van" --trip-end-date 2026-10-20 --total-miles 42 --billable'
    ' --customer "Riverside Apartments" --item "Mainline Clearing" --billing-rate 0.70 --notes "Site visit"'
    ' --reason "Mileage log" --company "Demo Plumbing Co" --json',
    {'vehicle': 'Service van', 'trip_end_date': '2026-10-20', 'total_miles': '42', 'billable': True,
     'customer': 'Riverside Apartments', 'item': 'Mainline Clearing', 'billing_rate': '0.70', 'notes': 'Site visit'})
EXAMPLES["trip invoice"] = Example(
    f'bookflow trip invoice {ID} --expected-version 1 --conversion-key riverside-miles-oct --date 2026-10-22'
    ' --reason "Bill October mileage" --company "Demo Plumbing Co" --json',
    {'trip': ID, 'expected_version': 1, 'conversion_key': 'riverside-miles-oct', 'date': '2026-10-22'})
EXAMPLES["mileage-rate set"] = Example(
    'bookflow mileage-rate set --effective-date 2026-01-01 --rate 0.70 --reason "Standard rate for 2026"'
    ' --company "Demo Plumbing Co" --json', {'effective_date': '2026-01-01', 'rate': '0.70'})
_MILES_CO = ' --company "Demo Plumbing Co" --json'
EXAMPLES.update({
    'vehicle create': Example('bookflow vehicle create --name "Service van" --description "Ford Transit, plate 7ABC123"'
                              ' --reason "New van"' + _MILES_CO, {'name': 'Service van', 'description': 'Ford Transit, plate 7ABC123'}),
    'vehicle update': Example('bookflow vehicle update "Service van" --active false --reason "Sold the van"' + _MILES_CO,
                              {'vehicle': 'Service van', 'active': False}),
    'vehicle show': Example('bookflow vehicle show "Service van"' + _MILES_CO, {'vehicle': 'Service van'}),
    'vehicle list': Example('bookflow vehicle list' + _MILES_CO, {}),
    'mileage-rate delete': Example('bookflow mileage-rate delete 2027-01-01 --reason "Entered a year early"' + _MILES_CO,
                                   {'effective_date': '2027-01-01'}),
    'mileage-rate list': Example('bookflow mileage-rate list' + _MILES_CO, {}),
    'trip update': Example(f'bookflow trip update {ID} --expected-version 1 --total-miles 40 --reason "Odometer note said 40"'
                           + _MILES_CO, {'trip': ID, 'expected_version': 1, 'total_miles': '40'}),
    'trip void': Example(f'bookflow trip void {ID} --expected-version 2 --reason "Logged twice"' + _MILES_CO,
                         {'trip': ID, 'expected_version': 2}),
    'trip show': Example(f'bookflow trip show {ID}' + _MILES_CO, {'trip': ID}),
    'trip query': Example('bookflow trip query --vehicle "Service van" --date-from 2026-10-01 --date-to 2026-10-31 --unreimbursed'
                          + _MILES_CO, {'vehicle': 'Service van', 'date_from': '2026-10-01', 'date_to': '2026-10-31',
                                        'unreimbursed': True}),
    'trip billing': Example(f'bookflow trip billing {ID}' + _MILES_CO, {'trip': ID}),
    'trip sales-receipt': Example(
        f'bookflow trip sales-receipt {ID} --expected-version 1 --conversion-key riverside-miles-cash --date 2026-10-22'
        ' --deposit-to "Undeposited Funds" --amount-received 29.40 --reason "Paid on site"' + _MILES_CO,
        {'trip': ID, 'expected_version': 1, 'conversion_key': 'riverside-miles-cash', 'date': '2026-10-22',
         'deposit_to': 'Undeposited Funds', 'amount_received': '29.40'}),
    'trip reimburse': Example(
        f'bookflow trip reimburse --trips {ID} --pay-to \'{{"name_type": "employee", "name_id": "Casey Worker"}}\''
        ' --account Checking --expense-account "Travel Mileage" --date 2026-10-31 --reason "October mileage"' + _MILES_CO,
        {'trips': [ID], 'pay_to': {'name_type': 'employee', 'name_id': 'Casey Worker'}, 'account': 'Checking',
         'expense_account': 'Travel Mileage', 'date': '2026-10-31'}),
    'report mileage-by-vehicle': Example('bookflow report mileage-by-vehicle --date-from 2026-01-01 --date-to 2026-12-31'
                                         + _MILES_CO, {'date_from': '2026-01-01', 'date_to': '2026-12-31'}),
    'report mileage-by-job': Example('bookflow report mileage-by-job --date-from 2026-01-01 --date-to 2026-12-31 --detail'
                                     + _MILES_CO, {'date_from': '2026-01-01', 'date_to': '2026-12-31', 'detail': True}),
})
EXAMPLES["customer-credit transfer"] = Example(
    'bookflow customer-credit transfer --from-customer "Payment Example Customer:Job B"'
    ' --to-customer "Payment Example Customer:Job A" --amount 10.00 --date 2026-10-20 --operation-key xfer-b-to-a'
    ' --reason "Customer asked to use the Job B credit on Job A"' + _C,
    {"from_customer": "Payment Example Customer:Job B", "to_customer": "Payment Example Customer:Job A",
     "amount": "10.00", "date": "2026-10-20", "operation_key": "xfer-b-to-a"})
EXAMPLES["customer-credit transfer-void"] = Example(
    f'bookflow customer-credit transfer-void {ID} --reason "Moved to the wrong job"' + _C, {"transfer": ID})

# Price rules (R110): a promotion for one customer's category, quantity breaks, and the price a line would get.
_PRICE_CO = ' --company "Demo Plumbing Co" --json'
EXAMPLES.update({
    'price-rule create': Example(
        'bookflow price-rule create --name "Riverside clearing 10% off" --customers "Riverside Apartments"'
        ' --items "Mainline Clearing" --value 10 --date-to 2026-12-31 --reason "Year-end deal"' + _PRICE_CO,
        {'name': 'Riverside clearing 10% off', 'customers': ['Riverside Apartments'], 'items': ['Mainline Clearing'],
         'value': '10', 'date_to': '2026-12-31'}),
    'price-rule update': Example(
        'bookflow price-rule update "Riverside clearing 10% off" --expected-version 1 --rounding x.99'
        ' --reason "Charm pricing"' + _PRICE_CO,
        {'rule': 'Riverside clearing 10% off', 'expected_version': 1, 'rounding': 'x.99'}),
    'price-rule activate': Example(f'bookflow price-rule activate {ID} --expected-version 2 --reason "Deal back on"' + _PRICE_CO,
                                   {'rule': ID, 'expected_version': 2}),
    'price-rule deactivate': Example(f'bookflow price-rule deactivate {ID} --expected-version 1 --reason "Deal ended"' + _PRICE_CO,
                                     {'rule': ID, 'expected_version': 1}),
    'price-rule show': Example('bookflow price-rule show "Riverside clearing 10% off"' + _PRICE_CO,
                               {'rule': 'Riverside clearing 10% off'}),
    'price-rule list': Example('bookflow price-rule list --customer "Riverside Apartments"' + _PRICE_CO,
                               {'customer': 'Riverside Apartments'}),
    'price-rule preview': Example(
        'bookflow price-rule preview --item "Mainline Clearing" --customer "Riverside Apartments" --quantity 10'
        ' --date 2026-11-02' + _PRICE_CO,
        {'item': 'Mainline Clearing', 'customer': 'Riverside Apartments', 'quantity': '10', 'date': '2026-11-02'}),
    'price-rule preferences': Example('bookflow price-rule preferences' + _PRICE_CO, {}),
    'price-rule set-preferences': Example(
        'bookflow price-rule set-preferences --conflict-policy priority --reason "Rules over price levels"' + _PRICE_CO,
        {'conflict_policy': 'priority'}),
})
# R101 increment 4: bill what can ship on an order; retries with the same key return the same invoice.
EXAMPLES['sales-order invoice'] = Example(
    'bookflow sales-order invoice DEMO-SO-1 --expected-version 2 --conversion-key "riverside-ship-1" --date 2026-10-22'
    ' --company "Demo Plumbing Co" --reason "Shipped what we had" --json',
    {'sales_order': 'DEMO-SO-1', 'expected_version': 2, 'conversion_key': 'riverside-ship-1', 'date': '2026-10-22'})
EXAMPLES['sales-order billing'] = Example('bookflow sales-order billing DEMO-SO-1 --company "Demo Plumbing Co" --json',
                                          {'sales_order': 'DEMO-SO-1'})
# R101 increment 5: order what an order is short from the items' preferred vendor; a retry orders nothing.
EXAMPLES['sales-order purchase-order'] = Example(
    'bookflow sales-order purchase-order DEMO-SO-1 --date 2026-10-22 --company "Demo Plumbing Co" --reason "Order the backorder" --json',
    {'sales_order': 'DEMO-SO-1', 'date': '2026-10-22'})
EXAMPLES['work-order purchase-order'] = Example(
    'bookflow work-order purchase-order DEMO-SO-WO-1 --date 2026-10-22 --vendor "Central Supply" --lines '
    + _shlex.quote(_json.dumps([{'item': 'Brass Shutoff Valve', 'quantity': '3'}]))
    + ' --company "Demo Plumbing Co" --reason "Spares for the job" --json',
    {'work_order': 'DEMO-SO-WO-1', 'date': '2026-10-22', 'vendor': 'Central Supply',
     'lines': [{'item': 'Brass Shutoff Valve', 'quantity': '3'}]})

# Timekeeping (R106): a week's timesheet, the clock and the job calendar.
_TIME_CO = ' --company "Demo Plumbing Co" --json'
EXAMPLES['timesheet show'] = Example(
    'bookflow timesheet show --employee "Casey Worker" --week 2026-10-20' + _TIME_CO,
    {'employee': 'Casey Worker', 'week': '2026-10-20'})
EXAMPLES['clock in'] = Example(
    'bookflow clock in --employee "Casey Worker" --customer "Riverside Apartments" --item "Mainline Clearing"'
    ' --notes "Callout" --reason "Casey started the Riverside callout"' + _TIME_CO,
    {'employee': 'Casey Worker', 'customer': 'Riverside Apartments', 'item': 'Mainline Clearing', 'notes': 'Callout'})
EXAMPLES['clock out'] = Example(
    'bookflow clock out --employee "Casey Worker" --reason "Casey finished"' + _TIME_CO, {'employee': 'Casey Worker'})
EXAMPLES['schedule create'] = Example(
    'bookflow schedule create --employee "Casey Worker" --customer "Riverside Apartments" --starts 2026-10-26T08:00'
    ' --ends 2026-10-26T12:00 --notes "Water heater" --reason "Book the install"' + _TIME_CO,
    {'employee': 'Casey Worker', 'customer': 'Riverside Apartments', 'starts': '2026-10-26T08:00',
     'ends': '2026-10-26T12:00', 'notes': 'Water heater'})
EXAMPLES['schedule calendar'] = Example(
    'bookflow schedule calendar --employee "Casey Worker" --view day --date 2026-10-26' + _TIME_CO,
    {'employee': 'Casey Worker', 'view': 'day', 'date': '2026-10-26'})
EXAMPLES['timesheet copy-last'] = Example(
    'bookflow timesheet copy-last --employee "Casey Worker" --week 2026-10-27' + _TIME_CO,
    {'employee': 'Casey Worker', 'week': '2026-10-27'})
EXAMPLES['timesheet save'] = Example(
    'bookflow timesheet save --employee "Casey Worker" --week 2026-10-20 --rows \'[{"customer": "Riverside Apartments",'
    ' "item": "Mainline Clearing", "billable": true, "notes": "Main line", "hours": ["8", "7.5", null, null, null, null,'
    ' null]}]\' --reason "Casey\'s week"' + _TIME_CO,
    {'employee': 'Casey Worker', 'week': '2026-10-20', 'rows': [
        {'customer': 'Riverside Apartments', 'item': 'Mainline Clearing', 'billable': True, 'notes': 'Main line',
         'hours': ['8', '7.5', None, None, None, None, None]}]})
EXAMPLES['timesheet preferences'] = Example('bookflow timesheet preferences' + _TIME_CO, {})
EXAMPLES['timesheet set-preferences'] = Example(
    'bookflow timesheet set-preferences --clock-rounding 15 --reason "Round punches to the quarter hour"' + _TIME_CO,
    {'clock_rounding': '15'})
EXAMPLES['clock cancel'] = Example(
    'bookflow clock cancel --employee "Casey Worker" --reason "Clocked in by mistake"' + _TIME_CO,
    {'employee': 'Casey Worker'})
EXAMPLES['clock status'] = Example('bookflow clock status' + _TIME_CO, {})
EXAMPLES['clock link'] = Example(
    'bookflow clock link --employee "Casey Worker" --user casey --reason "Casey\'s own login"' + _TIME_CO,
    {'employee': 'Casey Worker', 'user': 'casey'})
EXAMPLES['schedule update'] = Example(
    f'bookflow schedule update {ID} --expected-version 1 --starts 2026-10-26T13:00 --ends 2026-10-26T16:00'
    ' --reason "Customer asked for the afternoon"' + _TIME_CO,
    {'entry': ID, 'expected_version': 1, 'starts': '2026-10-26T13:00', 'ends': '2026-10-26T16:00'})
EXAMPLES['schedule cancel'] = Example(
    f'bookflow schedule cancel {ID} --expected-version 2 --reason "Customer postponed"' + _TIME_CO,
    {'entry': ID, 'expected_version': 2})
EXAMPLES['schedule show'] = Example(f'bookflow schedule show {ID}' + _TIME_CO, {'entry': ID})
# Inventory sites and transfers (R104 increment 1).
_C = ' --company "Demo Plumbing Co"'
EXAMPLES.update({
    'inventory-site create': Example('bookflow inventory-site create --name "Van 1" --description "Service van"' + _C
                                     + ' --reason "Track the van stock" --json', {'name': 'Van 1', 'description': 'Service van'}),
    'inventory-site update': Example('bookflow inventory-site update Main --name Warehouse' + _C
                                     + ' --reason "Name the default site" --json', {'site': 'Main', 'name': 'Warehouse'}),
    'inventory-site activate': Example('bookflow inventory-site activate "Van 1"' + _C + ' --reason "Back in service" --json',
                                       {'site': 'Van 1'}),
    'inventory-site deactivate': Example('bookflow inventory-site deactivate "Van 1"' + _C + ' --reason "Van sold" --json',
                                         {'site': 'Van 1'}),
    'inventory-site show': Example('bookflow inventory-site show "Van 1"' + _C + ' --json', {'site': 'Van 1'}),
    'inventory-site list': Example('bookflow inventory-site list' + _C + ' --json', {}),
    'inventory-transfer post': Example(
        'bookflow inventory-transfer post --from-site "Van 1" --to-site Warehouse'
        ' --lines \'[{"item":"Widget","quantity":"5"}]\'' + _C + ' --reason "Unloaded 5 widgets from the van" --json',
        {'from_site': 'Van 1', 'to_site': 'Warehouse', 'lines': [{'item': 'Widget', 'quantity': '5'}]}),
    'inventory-transfer update': Example(
        'bookflow inventory-transfer update T-1 --expected-version 1 --lines \'[{"item":"Widget","quantity":"4"}]\''
        + _C + ' --reason "Only four came off" --json',
        {'transfer': 'T-1', 'expected_version': 1, 'lines': [{'item': 'Widget', 'quantity': '4'}]}),
    'inventory-transfer void': Example('bookflow inventory-transfer void T-1 --expected-version 2' + _C
                                       + ' --reason "Entered twice" --json', {'transfer': 'T-1', 'expected_version': 2}),
    'inventory-transfer show': Example('bookflow inventory-transfer show T-1' + _C + ' --json', {'transfer': 'T-1'}),
    'inventory-transfer history': Example('bookflow inventory-transfer history T-1' + _C + ' --json', {'transfer': 'T-1'}),
    'inventory-transfer query': Example('bookflow inventory-transfer query --site "Van 1" --limit 25' + _C + ' --json',
                                        {'site': 'Van 1', 'limit': 25}),
    'report quantity-by-site': Example('bookflow report quantity-by-site --as-of 2026-12-31 --site "Van 1"' + _C + ' --json',
                                       {'as_of': '2026-12-31', 'site': 'Van 1'}),
})
# Accountant review (R121): the external accountant, the accountant's copy and what comes back, the checklist.
_ACCT_CO = ' --company "Demo Plumbing Co" --json'
_CHANGES = '/home/owner/Downloads/Demo Plumbing Co accountant changes 2026-10-14-091500.json'
EXAMPLES.update({
    'accountant add': Example('bookflow accountant add jlee --through 2026-09-30 --note "Q3 review" --reason "Q3 review"' + _ACCT_CO,
                              {'user': 'jlee', 'through': '2026-09-30', 'note': 'Q3 review'}),
    'accountant remove': Example('bookflow accountant remove jlee --reason "Review finished"' + _ACCT_CO, {'user': 'jlee'}),
    'accountant list': Example('bookflow accountant list' + _ACCT_CO, {}),
    'accountant reclassify': Example(
        f'bookflow accountant reclassify {ID} --from-account "Office Supplies" --to-account "Repairs and Maintenance"'
        ' --amount 120.00 --reason "Repairs booked to office"' + _ACCT_CO,
        {'transaction': ID, 'from_account': 'Office Supplies', 'to_account': 'Repairs and Maintenance', 'amount': '120.00'}),
    'accountant mark-entry': Example(f'bookflow accountant mark-entry {ID} --note "Agreed to the bank statement"'
                                     ' --reason "Q3 review"' + _ACCT_CO, {'transaction': ID, 'note': 'Agreed to the bank statement'}),
    'accountant reviewed-through': Example('bookflow accountant reviewed-through 2026-09-30 --note "Q3 adjustments posted"'
                                           ' --reason "Q3 review"' + _ACCT_CO, {'through': '2026-09-30', 'note': 'Q3 adjustments posted'}),
    'accountant-copy export': Example('bookflow accountant-copy export 2026-09-30 --reason "Q3 review by our accountant"' + _ACCT_CO,
                                      {'through': '2026-09-30'}),
    'accountant-copy status': Example('bookflow accountant-copy status' + _ACCT_CO, {}),
    'accountant-copy cancel': Example('bookflow accountant-copy cancel --reason "Accountant sent a list of entries instead"' + _ACCT_CO, {}),
    'accountant-copy changes': Example('bookflow accountant-copy changes --reason "Q3 adjustments for the owner"'
                                       ' --company "Demo Plumbing Co (restored)" --json', {}),
    'accountant-copy preview': Example(f'bookflow accountant-copy preview "{_CHANGES}"' + _ACCT_CO, {'file': _CHANGES}),
    'accountant-copy import': Example(f'bookflow accountant-copy import "{_CHANGES}" --reason "Q3 adjustments"' + _ACCT_CO,
                                      {'file': _CHANGES}),
    'report accountant-review': Example('bookflow report accountant-review --as-of 2026-09-30' + _ACCT_CO, {'as_of': '2026-09-30'}),
    'report accountant-changes': Example('bookflow report accountant-changes' + _ACCT_CO, {}),
    # Performance tracking (R180).
    'perf report': Example('bookflow perf report --since 7d --surface http --json', {'since': '7d', 'surface': 'http'}),
    'perf settings': Example('bookflow perf settings --recording off --json', {'recording': 'off'}),
    'perf generate': Example('bookflow perf generate --output /srv/bench --profile large --json',
                             {'output': '/srv/bench', 'profile': 'large'}),
    'perf bench': Example('bookflow perf bench --template /srv/bench/bench-large-s180-co0088 --name before --json',
                          {'template': '/srv/bench/bench-large-s180-co0088', 'name': 'before'}),
    'perf compare': Example('bookflow perf compare --run after --baseline before --threshold 25 --json',
                            {'run': 'after', 'baseline': 'before', 'threshold': 25}),
})
