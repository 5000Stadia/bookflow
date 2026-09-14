"""Captured item rows and stock effects owned by a check or card purchase."""
import json
import sqlalchemy as sa
from bookflow.company import bills, inventory_effects, journals, schema as c
from bookflow.company.bill_facts import BillItemProfile
from bookflow.company.check_models import MoneyOutItemOutput
from bookflow.core.money import Money
from bookflow.core.exact import format_quantity_micro_units


def stored(s, lines):
    ids = [line['id'] for line in lines]
    rows = s.company.conn.execute(sa.select(c.money_out_item_lines).where(
        c.money_out_item_lines.c.document_line_id.in_(ids))).mappings()
    by_id = {row['document_line_id']: row for row in rows}
    return [dict(line_id=line['line_id'], family='item', amount_minor_units=line['amount_minor_units'] if line['kind'] == 'journal' else 0,
                 memo=line['description'], profile=BillItemProfile.model_validate_json(
                     by_id[line['id']]['line_snapshot'])) for line in lines if line['id'] in by_id]


def resolve(s, inputs, previous, header_class, currency):
    if inputs is None:
        return previous
    allowed = {line['line_id'] for line in previous}
    seen = set()
    result = []
    for index, item in enumerate(inputs):
        if item.line_id is not None:
            if item.line_id not in allowed or item.line_id in seen:
                raise journals.invalid(f'items.{index}.line_id', 'must identify a distinct existing item row')
            seen.add(item.line_id)
        line = bills._item_line(s, item, header_class, currency, index)
        line['line_id'] = item.line_id
        result.append(line)
    return result


def output(item, line_id, currency):
    return MoneyOutItemOutput(line_id=line_id, quantity=format_quantity_micro_units(item["profile"].quantity_microunits), description=item['memo'], profile=item['profile'],
                             amount=Money(item['amount_minor_units'], currency).to_dict())


def changed(s, header, items):
    if header is None:
        return False
    revision = journals.revision(s, header)
    lines = journals.rows(s, c.document_lines, c.document_lines.c.revision_id == revision['id'],
                          order=c.document_lines.c.position)
    return items != stored(s, lines)


def attach(s, fresh, items):
    """Bind captured facts and stock movements to this exact journal revision's legs."""
    if not fresh.data['changed']:
        return [], None, []
    d = fresh.data
    pending, header = d['pending'], d['header']
    revision = fresh.preview.revision
    positive = [line for line in pending['document_lines'] if line['kind'] == 'journal']
    positive = iter(positive[-sum(bool(item['amount_minor_units']) for item in items):] if any(item['amount_minor_units'] for item in items) else [])
    free = iter(line for line in pending['document_lines'] if line['kind'] == 'purchase')
    lines = [next(positive if item['amount_minor_units'] else free) for item in items]
    rows, entries, outputs = [], [], []
    for item, line in zip(items, lines):
        facts = item['profile']
        rows.append(dict(document_line_id=line['id'], transaction_id=header['id'],
                         revision_id=line['revision_id'], item_id=facts.item.id,
                         line_snapshot=json.dumps(facts.model_dump(), sort_keys=True)))
        entry = inventory_effects.purchase_entry(facts, key=line['id'],
            amount=item['amount_minor_units'], offset_account_id=d['purchase_funding']['account_id'],
            class_id=line['class_id'])
        if entry is not None:
            entries.append(entry)
        outputs.append(output(item, line['line_id'], line['currency']))
    stock = inventory_effects.plan(s, entries=entries,
        reversing=inventory_effects.own_movements(s, header['id']) if d['before'] else (),
        date=revision.date, currency=revision.currency, field='items')
    inventory_effects.open_dates(s, stock)
    sources = {source['document_line_id']: source['posting_line_id']
               for source in pending['posting_line_sources'] if source['reversed_source_id'] is None}
    legs = {leg['id']: leg for leg in pending['posting_lines']}
    for movement in stock.movements:
        if movement.key is not None:
            line = next(row for row in lines if row['id'] == movement.key)
            inventory_effects.bind(movement, legs.get(sources.get(movement.key)), transaction_id=header['id'],
                                   revision_id=line['revision_id'], document_line_id=line['id'], batch=next(b for b in pending['posting_batches'] if b['kind'] != 'reversal'))
    inventory_effects.bind_reversals(stock, pending['posting_lines'], pending['posting_batches'])
    inventory_effects.check(s, stock, pending['posting_lines'])
    return rows, stock, outputs


def free_envelopes(pending, items, header, revision, created, prior):
    """Quantity-bearing commercial identities; no monetary line is invented."""
    positive_count = sum(bool(item['amount_minor_units']) for item in items)
    monetary = list(pending['document_lines'])
    prefix = monetary[:-positive_count] if positive_count else monetary
    positive = iter(monetary[-positive_count:] if positive_count else [])
    ordered = []
    for item in items:
        if item['amount_minor_units']:
            ordered.append(next(positive))
            continue
        key = item['line_id']
        if key is not None and key not in prior:
            raise journals.invalid('line_id', 'free item must retain its current owned identity')
        if key is None:
            identity = dict(**created(), transaction_id=header['id'])
            pending['document_line_identities'].append(identity)
            key = identity['id']
        profile = item['profile']
        pending['document_lines'].append(dict(**created(), transaction_id=header['id'],
            revision_id=revision['id'], line_id=key, position=len(pending['document_lines']) + 1,
            kind='purchase', account_id=None, side=None, amount_minor_units=None,
            currency=revision['currency'], account_snapshot=None, description=item['memo'],
            name_type='customer' if profile.customer else None,
            name_id=profile.customer.id if profile.customer else None,
            party_name=profile.customer.label if profile.customer else None,
            class_id=profile.class_id.id if profile.class_id else None,
            class_name=profile.class_id.label if profile.class_id else None,
            original_minor_units=None, original_currency=None, rate_used=None, rate_source=None))
        ordered.append(pending['document_lines'][-1])
    pending['document_lines'] = prefix + ordered
    for position, line in enumerate(pending['document_lines'], 1):
        line['position'] = position


# Internal coordinator inputs. Public journal commands still require two positive lines.
from pydantic import Field
from bookflow.company.journal_models import JournalPostInput, JournalUpdateInput, JournalLineInput

from bookflow.company.register_models import RegisterUpdateInput

class PurchaseRegisterUpdate(RegisterUpdateInput):
    selected_line_id: str | None = None

class PurchasePost(JournalPostInput):
    lines: list[JournalLineInput] = Field(max_length=200)

class PurchaseUpdate(JournalUpdateInput):
    lines: list[JournalLineInput] = Field(max_length=200)


def funding(s, lines):
    row = s.company.conn.execute(sa.select(c.money_out_revision_profiles.c.funding_snapshot)
        .where(c.money_out_revision_profiles.c.revision_id == lines[0]['revision_id'])).scalar_one_or_none()
    return json.loads(row) if row else dict(lines[0])


def translate(s, register, summary, header, expected):
    from bookflow.company import registers, parties, accounts
    selected = registers._selected(s, register.account, summary.currency)
    if header:
        journals.version_meta(s, header, expected)
    party = journals.active(parties.resolve_party(s.company, register.payee.name_type.replace('_', '-'), register.payee.name_id), register.payee.name_type) if register.payee else None
    captured = dict(account_id=selected['id'], currency=summary.currency, side='credit',
        amount_minor_units=summary.amount.minor_units,
        account_snapshot=json.dumps({k: selected.get(k) for k in ('id','name','full_name','number','type')} | {'normal_balance': accounts.NORMAL_BALANCE[selected['type']]}, sort_keys=True),
        name_type=register.payee.name_type if register.payee else None, name_id=party['id'] if party else None,
        party_name=(party.get('full_name') or party.get('name') or party.get('display_name')) if party else None,
        line_id=register.selected_line_id if header else None)
    if header:
        previous = funding(s, journals.rows(s, c.document_lines, c.document_lines.c.revision_id == header['current_revision_id'], order=c.document_lines.c.position))
        if captured['account_id'] == previous['account_id']:
            captured['account_snapshot'] = previous['account_snapshot']
        if (captured['name_type'], captured['name_id']) == (previous['name_type'], previous['name_id']):
            captured['party_name'] = previous['party_name']
    from bookflow.company.sales_models import money
    positive = [a for a in register.allocations if (a.amount.minor_units if not isinstance(a.amount, str) else money(a.amount, summary.currency).minor_units)]
    lines = []
    if summary.amount.minor_units:
        main = registers._line(selected['id'], 'credit', register.amount, register.payee, register.memo, None, captured['line_id'])
        offsets, total = registers._offsets(register.model_copy(update={'allocations': positive}), s, selected, summary.currency, owner='inventory')
        if total != summary.amount.minor_units:
            raise journals.invalid('amount', 'item and expense amounts must match')
        lines = [main, *offsets]
    values = dict(date=register.date, memo=register.memo, lines=lines,
        custom_fields=register.custom_fields, custom_field_kinds=register.custom_field_kinds)
    journal = PurchaseUpdate(journal=header['id'], expected_version=expected, **values) if header else PurchasePost(**values)
    return journal, captured


def funding_changed(s, header, captured):
    if header is None:
        return False
    previous = funding(s, journals.rows(s, c.document_lines,
        c.document_lines.c.revision_id == header['current_revision_id'], order=c.document_lines.c.position))
    return any(captured[key] != previous[key] for key in
        ('account_id', 'currency', 'amount_minor_units', 'name_type', 'name_id', 'party_name', 'account_snapshot'))
