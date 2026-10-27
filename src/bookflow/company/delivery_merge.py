"""Merge fields for delivery templates (R112; design/document-delivery.md §3).

A template's subject and body use a **closed list** of fields, `{{group.field}}`, and one conditional,
`{{if group.field}} ... {{end}}`, which shows its text only when the field has a value. There is no expression
language and no nesting: a template an agent can edit must never run code. Saving a template with a field its kind
does not offer is refused, naming the field. A field with no value at draft time becomes empty text and a warning.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from bookflow.core.errors import BookflowError

COMMON = ('company.name', 'company.phone', 'company.email', 'company.website', 'sender.name',
          'customer.name', 'customer.first_name', 'customer.last_name', 'customer.account_number')
DOCUMENT = ('document.kind', 'document.number', 'document.date', 'document.due_date', 'document.total',
            'document.balance_due', 'document.po_number', 'document.terms')
STATEMENT = ('statement.date_from', 'statement.date_to', 'statement.balance', 'statement.overdue')
NOTICE = ('notice.overdue', 'notice.oldest_due_date', 'notice.days_overdue', 'notice.letter')
FIELDS = {
    'invoice': COMMON + DOCUMENT,
    'sales-receipt': COMMON + DOCUMENT,
    'estimate': COMMON + DOCUMENT,
    'statement': COMMON + STATEMENT,
    'notice': COMMON + NOTICE,
}
DESCRIPTIONS = {
    'company.name': "The company's display name", 'company.phone': "The company's phone",
    'company.email': "The company's email", 'company.website': "The company's website",
    'sender.name': 'The person drafting, or the company when an agent or the scheduler drafts',
    'customer.name': 'Customer (or job) full name', 'customer.first_name': "Customer's first name",
    'customer.last_name': "Customer's last name", 'customer.account_number': "Customer's account number",
    'document.kind': 'Invoice, Sales receipt or Estimate', 'document.number': 'Document number',
    'document.date': 'Document date', 'document.due_date': 'Due date (invoices)',
    'document.total': 'Document total', 'document.balance_due': 'Balance due (invoices)',
    'document.po_number': "Customer's P.O. number", 'document.terms': 'Payment terms',
    'statement.date_from': 'Statement period start', 'statement.date_to': 'Statement date',
    'statement.balance': 'Balance due at the statement date', 'statement.overdue': 'Part of the balance past due',
    'notice.overdue': 'Overdue amount', 'notice.oldest_due_date': 'Oldest overdue due date',
    'notice.days_overdue': 'Days the oldest is overdue', 'notice.letter': "The notice's collection letter",
}
TOKEN = re.compile(r'\{\{\s*(?:(if)\s+([a-z_]+\.[a-z_]+)|(end)|([a-z_]+\.[a-z_]+))\s*\}\}')


@dataclass(frozen=True)
class Part:
    kind: str  # 'text', 'field', 'if', 'end'
    value: str


def _invalid(field, problem):
    return BookflowError('E_VALIDATION', message=f'{field}: {problem}',
                         details={'fields': [{'field': field, 'problem': problem}]})


def parse(text: str, kind: str, where: str) -> list[Part]:
    """Split a template into text, fields and conditionals, refusing anything outside the closed list."""
    allowed = FIELDS[kind]
    parts, at, open_if = [], 0, False
    for match in TOKEN.finditer(text):
        if match.start() > at:
            parts.append(Part('text', text[at:match.start()]))
        if_word, if_field, end, field = match.groups()
        name = if_field or field
        if name is not None and name not in allowed:
            raise _invalid(where, f'unknown merge field {{{{{name}}}}} for {kind} (delivery-template fields --kind '
                                  f'{kind} lists them)')
        if if_word:
            if open_if:
                raise _invalid(where, '{{if}} cannot be nested; close the first with {{end}}')
            open_if = True
            parts.append(Part('if', name))
        elif end:
            if not open_if:
                raise _invalid(where, '{{end}} without an {{if}}')
            open_if = False
            parts.append(Part('end', ''))
        else:
            parts.append(Part('field', name))
        at = match.end()
    if at < len(text):
        parts.append(Part('text', text[at:]))
    if open_if:
        raise _invalid(where, '{{if}} without its {{end}}')
    for part in parts:
        if part.kind == 'text' and ('{{' in part.value or '}}' in part.value):
            snippet = part.value[part.value.find('{{'):][:40] if '{{' in part.value else part.value[:40]
            raise _invalid(where, f'not a merge field: {snippet!r} (fields look like {{{{customer.name}}}})')
    return parts


def check(kind: str, subject: str, body: str) -> None:
    parse(subject, kind, 'subject')
    parse(body, kind, 'body')


def fill(text: str, kind: str, values: dict[str, str | None], where: str, empty: set[str]) -> str:
    """The text with every field filled; a field with no value adds its name to `empty`."""
    out, skipping = [], False
    for part in parse(text, kind, where):
        if part.kind == 'if':
            skipping = not values.get(part.value)
        elif part.kind == 'end':
            skipping = False
        elif skipping:
            continue
        elif part.kind == 'text':
            out.append(part.value)
        else:
            value = values.get(part.value)
            if not value:
                empty.add(part.value)
            out.append(value or '')
    return ''.join(out)


DEFAULTS = {
    'invoice': ('Invoice', 'Invoice {{document.number}} from {{company.name}}{{if document.po_number}} '
                '(PO {{document.po_number}}){{end}}',
                'Dear {{customer.name}},\n\nPlease find attached invoice {{document.number}} for '
                '{{document.total}}{{if document.due_date}}, due {{document.due_date}}{{end}}.\n\n'
                'Thank you for your business.\n\n{{sender.name}}\n{{company.name}}'
                '{{if company.phone}}\n{{company.phone}}{{end}}'),
    'sales-receipt': ('Sales receipt', 'Receipt {{document.number}} from {{company.name}}',
                      'Dear {{customer.name}},\n\nThank you for your purchase. Your receipt {{document.number}} for '
                      '{{document.total}} is attached.\n\n{{sender.name}}\n{{company.name}}'
                      '{{if company.phone}}\n{{company.phone}}{{end}}'),
    'estimate': ('Estimate', 'Estimate {{document.number}} from {{company.name}}',
                 'Dear {{customer.name}},\n\nPlease find attached estimate {{document.number}} for '
                 '{{document.total}}. Let us know if you have any questions or would like to go ahead.\n\n'
                 '{{sender.name}}\n{{company.name}}{{if company.phone}}\n{{company.phone}}{{end}}'),
    'statement': ('Statement', 'Statement from {{company.name}}',
                  'Dear {{customer.name}},\n\nPlease find attached your statement for {{statement.date_from}} to '
                  '{{statement.date_to}}. The balance due is {{statement.balance}}.\n\n'
                  'If you have any questions about your account, please contact us.\n\n'
                  '{{sender.name}}\n{{company.name}}{{if company.phone}}\n{{company.phone}}{{end}}'),
    'notice': ('Overdue notice', 'Past due: {{customer.name}}', '{{notice.letter}}'),
}
