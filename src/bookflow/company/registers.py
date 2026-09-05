"""Translate register intent again inside the journal writer transaction."""
from __future__ import annotations

import json

import sqlalchemy as sa

from bookflow.company import accounts, journals, schema as c
from bookflow.company.journal_models import (
    JournalLineInput, JournalPostInput, JournalUpdateInput,
    parse_domestic_amount, checked_sum,
)
from bookflow.company.register_models import (
    RegisterCalculateOutput, RegisterReceipt, RegisterWriteOutput,
)
from bookflow.core.errors import BookflowError
from bookflow.core.ids import is_ulid
from bookflow.core.money import Money
from bookflow.core.registry import Plan

REGISTER_TYPES = frozenset((
    'bank', 'accounts_receivable', 'other_current_asset', 'fixed_asset', 'other_asset',
    'accounts_payable', 'credit_card', 'other_current_liability', 'long_term_liability', 'equity',
))


def _home(s):
    return s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar_one()


def _selected(s, selector, currency):
    account = journals.active(accounts.resolve_account(s.company, selector), 'account')
    if account['type'] not in REGISTER_TYPES:
        raise journals.invalid('account', 'register requires a balance-sheet posting account')
    if account.get('currency') not in (None, currency):
        raise journals.invalid('account', 'register account must use home currency')
    return account


def _side(normal, direction):
    return normal if direction == 'increase' else _opposite(normal)


def _opposite(side):
    return 'credit' if side == 'debit' else 'debit'


def _identity(value):
    return value.upper() if value and is_ulid(value) else value


def _line(account, side, amount, party, memo, class_id, line_id=None):
    values = dict(account=account, side=side, amount=amount, description=memo,
                  name_type=party.name_type if party else None,
                  name_id=party.name_id if party else None, class_id=class_id)
    if line_id is not None:
        values['line_id'] = line_id
    return JournalLineInput(**values)


def _offsets(inp, s, selected, currency):
    lines, signed = [], []
    normal = accounts.NORMAL_BALANCE[selected['type']]
    for index, allocation in enumerate(inp.allocations):
        amount = parse_domestic_amount(allocation.amount, currency, f'allocations.{index}.amount')
        direction = allocation.direction or inp.direction
        class_id = (getattr(inp, 'class_id', None) if allocation.class_mode == 'inherit' else
                    allocation.class_id if allocation.class_mode == 'value' else None)
        line = _line(allocation.account, _opposite(_side(normal, direction)),
                     allocation.amount, allocation.party, allocation.memo, class_id,
                     allocation.line_id)
        values = journals.line_values(s, line, currency)
        if values['account_id'] == selected['id']:
            raise journals.invalid(f'allocations.{index}.account', 'offset cannot be the selected register account')
        lines.append(line)
        signed.append(amount.minor_units if direction == inp.direction else -amount.minor_units)
    total = checked_sum(signed, 'allocations.total')
    if total <= 0:
        raise journals.invalid('allocations', 'net movement must be positive in the main direction')
    # A net within i64 is insufficient when a mixed split overflows a posting side.
    checked_sum((v for v in signed if v > 0), 'allocations.same_direction_total')
    checked_sum((-v for v in signed if v < 0), 'allocations.reverse_direction_total')
    return lines, total


def calculate(inp, s):
    currency = _home(s)
    selected = _selected(s, inp.account, currency)
    _, total = _offsets(inp, s, selected, currency)
    return RegisterCalculateOutput(amount=Money(total, currency).to_dict(),
                                   currency=currency, direction=inp.direction)


def _compatible(s, inp, header, selected):
    revision = journals.revision(s, header)
    lines = journals.rows(s, c.document_lines,
                          c.document_lines.c.revision_id == revision['id'],
                          order=c.document_lines.c.position)
    own = [line for line in lines if line['account_id'] == selected['id']]
    compatible = (len(own) == 1 and len(lines) >= 2 and own[0]['position'] == 1
                  and own[0]['line_id'] == lines[0]['line_id']
                  and own[0]['class_id'] is None and own[0]['class_name'] is None
                  and own[0]['description'] == revision['memo']
                  and revision['name_type'] is None and revision['name_id'] is None
                  and not json.loads(revision['custom_fields_snapshot'])
                  and all(line['kind'] == 'journal' and all(line[f] is None for f in journals.FACTS)
                          for line in lines))
    if not compatible:
        raise BookflowError('E_VALIDATION', message='Open this entry in the journal editor.', details={
            'fields': [{'field': 'journal', 'problem': 'current journal cannot be edited losslessly in this register'}],
            'open_journal': {'journal': header['id'], 'command': 'journal show'},
        })
    if _identity(inp.selected_line_id) != own[0]['line_id']:
        raise journals.invalid('selected_line_id', 'must retain the current selected-account line identity')


def translate(inp, s, operation):
    # Do this before resolving changed references or checking the current shape.
    header = journals.resolve(s, inp.journal) if operation == 'update' else None
    if header:
        journals.version_meta(s, header, inp.expected_version)
    currency = _home(s)
    selected = _selected(s, inp.account, currency)
    if header:
        _compatible(s, inp, header, selected)
    amount = parse_domestic_amount(inp.amount, currency)
    normal = accounts.NORMAL_BALANCE[selected['type']]
    main = _line(selected['id'], _side(normal, inp.direction), inp.amount,
                 inp.payee, inp.memo, None,
                 inp.selected_line_id if header else None)
    if inp.category is not None:
        offset = _line(inp.category, _opposite(main.side), inp.amount, inp.payee,
                       inp.memo, inp.class_id, inp.category_line_id if header else None)
        values = journals.line_values(s, offset, currency)
        if values['account_id'] == selected['id']:
            raise journals.invalid('category', 'offset cannot be the selected register account')
        offsets = [offset]
    else:
        offsets, total = _offsets(inp, s, selected, currency)
        if total != amount.minor_units:
            raise BookflowError('E_UNBALANCED_ENTRY', details={
                'amount_minor_units': amount.minor_units,
                'allocation_net_minor_units': total, 'currency': currency,
            })
    values = dict(date=inp.date, memo=inp.memo, lines=[main, *offsets])
    if inp.number is not None:
        values['number'] = inp.number
    if header:
        journal = JournalUpdateInput(journal=header['id'], expected_version=inp.expected_version, **values)
    else:
        journal = JournalPostInput(**values)
    receipt = RegisterReceipt(account_id=selected['id'], normal_balance=normal,
                              direction=inp.direction, amount=amount.to_dict())
    return journal, receipt


def _output(journal_output, receipt):
    return RegisterWriteOutput(**journal_output.model_dump(), receipt=receipt)


def prepare(s, ctx, inp, operation):
    journal, receipt = translate(inp, s, operation)
    fresh = journals.prepare(s, ctx, journal, operation)
    return Plan(_output(fresh.preview, receipt), {'input': inp, 'operation': operation})


def apply(plan, ctx, s):
    inp, operation = plan.data['input'], plan.data['operation']
    journal, receipt = translate(inp, s, operation)
    fresh = journals.prepare(s, ctx, journal, operation)
    applied = journals.persist_prepared(fresh, ctx, s, command_name='register ' + operation)
    applied.output = _output(applied.output, receipt)
    return applied
