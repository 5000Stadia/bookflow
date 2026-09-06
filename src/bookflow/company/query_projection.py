"""Opt-in public projections and typed custom predicates, without row-by-row shows."""
from __future__ import annotations

import json
import sqlalchemy as sa
from bookflow.company import schema
from bookflow.company.query_catalog import definition_rows, error, LABEL_REFERENCES
from bookflow.core.exact import parse_custom_number_nano_units, format_quantity_micro_units, format_percentage_millionths
from bookflow.core.money import Money


def reference_value(value):
    if value is None:
        return None
    result = json.loads(value)
    result['active'] = bool(result['active'])
    return result


def custom_predicates(noun, criteria, session, table):
    if not criteria:
        return sa.true()
    from bookflow.company.query_providers import contains_any
    from bookflow.company.list_service import normalize_lookup_key
    definitions = definition_rows(noun, session, [criterion.definition for criterion in criteria])
    wanted = [criterion.value for criterion in criteria if criterion.kind == 'choice']
    choices = {row['id']: row for row in session.company.conn.execute(sa.select(schema.custom_field_choices).where(
        schema.custom_field_choices.c.id.in_(wanted))).mappings()} if wanted else {}
    session.company.raw.create_function('bookflow_custom_number', 1, parse_custom_number_nano_units, deterministic=True)
    session.company.raw.create_function('bookflow_query_contains', -1, contains_any, deterministic=True)
    session.company.raw.create_function('bookflow_choice_key', 1, normalize_lookup_key, deterministic=True)
    values = schema.custom_field_values
    predicates = []
    for criterion in criteria:
        definition = definitions[criterion.definition]
        if criterion.kind != 'presence' and criterion.kind != definition['kind']:
            raise error(noun, 'Custom filter kind does not match definition', definition=criterion.definition, kind=criterion.kind)
        source = sa.select(values.c.record_id).where(values.c.def_id == criterion.definition,
            values.c.record_type == noun.replace('-', '_'), values.c.active.is_(True))
        if criterion.kind == 'presence':
            predicate = table.c.id.in_(source)
            predicates.append(~predicate if criterion.operator == 'is_missing' else predicate)
            continue
        left, right = values.c.canonical_text, criterion.value
        if criterion.kind == 'number':
            left, right = sa.func.bookflow_custom_number(left), parse_custom_number_nano_units(right)
        elif criterion.kind == 'bool':
            right = 'true' if right else 'false'
        elif criterion.kind == 'choice':
            choice = choices.get(right)
            if choice is None or choice['definition_id'] != criterion.definition:
                raise error(noun, 'Unknown choice or choice belongs to another definition', definition=criterion.definition, choice=right)
            # Retired labels may be reused. Only active logical choices can own current values.
            same = sa.and_(sa.literal(bool(choice['active'])), sa.func.bookflow_choice_key(left) == choice['value_key'])
            predicates.append(table.c.id.in_(source.where(~same if criterion.operator == 'ne' else same)))
            continue
        operation = criterion.operator
        comparison = {'eq': lambda: left == right, 'ne': lambda: left != right,
            'lt': lambda: left < right, 'lte': lambda: left <= right,
            'gt': lambda: left > right, 'gte': lambda: left >= right,
            'contains': lambda: sa.func.bookflow_query_contains(normalize_lookup_key(right), left) == 1}[operation]()
        predicates.append(table.c.id.in_(source.where(comparison)))
    return sa.and_(*predicates)


def _reference(target, identifier, session):
    from bookflow.company.lists import get_list_definition
    definition = get_list_definition(target)
    table = getattr(schema, definition.table).alias()
    label = table.c[definition.display_field]
    if target == 'account':
        if session.company_info_row['show_lowest_subaccount_only']:
            label = table.c.name
        if session.company_info_row['use_account_numbers']:
            label = sa.case((table.c.number.is_not(None), table.c.number + ' · ' + label), else_=label)
    return sa.select(sa.func.json_object('id', table.c.id, 'label', label, 'active', table.c.active,
        'noun', sa.literal(target))).where(table.c.id == identifier).scalar_subquery()


# Explicit bounded collection relationships; full children are retrieved through query children.
COLLECTIONS = {
    ('vendor', 'expense_accounts'): ('vendor_expense_accounts', 'vendor_id'),
    ('unit-of-measure', 'units'): ('unit_conversions', 'unit_of_measure_id'),
    ('price-level', 'items'): ('price_level_items', 'price_level_id'),
    ('item', 'members'): ('item_members', 'owner_item_id'),
    ('item', 'vendor_profiles'): ('item_vendor_profiles', 'item_id'),
    ('custom-field', 'choices'): ('custom_field_choices', 'definition_id'),
    ('custom-field', 'scopes'): ('custom_field_scopes', 'definition_id'),
}


def configure(noun, descriptors, p, session):
    """Add only declared selected expressions; return their exact decoders."""
    from bookflow.commands import party_cmds as party
    table = p.table
    currency = session.company_info_row['home_currency']
    decoders = {}
    for descriptor in descriptors:
        name = descriptor.key
        if name.startswith('custom:'):
            values = schema.custom_field_values
            p.columns[name] = sa.select(values.c.canonical_text).where(values.c.def_id == descriptor.definition,
                values.c.record_type == noun.replace('-', '_'), values.c.record_id == table.c.id,
                values.c.active.is_(True)).scalar_subquery()
            if descriptor.kind == 'bool':
                decoders[name] = lambda value, row: value == 'true' if value is not None else None
            continue
        if (noun, name) in COLLECTIONS:
            child_name, owner = COLLECTIONS[noun, name]
            child = getattr(schema, child_name)
            p.columns[name] = sa.select(sa.func.count()).select_from(child).where(child.c[owner] == table.c.id).scalar_subquery()
            decoders[name] = lambda value, row, key=name: {'count': value, 'command': noun + ' query children', 'record': row['id'], 'column': key}
            continue
        if descriptor.reference_noun and '|' not in descriptor.reference_noun:
            field = LABEL_REFERENCES[name][1] if name in LABEL_REFERENCES else name
            identifier = table.c.get(field)
            if noun == 'customer' and field in ('terms_id', 'sales_tax_code_id', 'sales_tax_item_id', 'price_level_id',
                    'customer_type_id', 'sales_rep_id', 'preferred_payment_method_id', 'preferred_ship_method_id', 'default_class_id'):
                identifier = party._customer_effective(field)
                if field == 'sales_rep_id':
                    identifier = sa.func.coalesce(party._customer_effective('job_sales_rep_id'), identifier)
            if identifier is None and name.startswith('linked_'):
                link = schema.customer_vendor_links
                own, other = ('customer_id', 'vendor_id') if noun == 'customer' else ('vendor_id', 'customer_id')
                identifier = sa.select(link.c[other]).where(link.c[own] == table.c.id, link.c.active.is_(True)).scalar_subquery()
            if identifier is not None:
                p.columns[name] = _reference(descriptor.reference_noun, identifier, session)
                decoders[name] = lambda value, row: reference_value(value)
                continue
        if name in p.columns:
            continue
        if name in table.c:
            p.columns[name] = table.c[name]
            if noun == 'customer' and name == 'preferred_delivery_method':
                p.columns[name] = party._customer_effective(name)
            if name == 'tax_id_last4' and not party._can_reveal_tax(session):
                p.columns[name] = sa.null()
            continue
        if name == 'postal_code':
            if noun == 'customer':
                source = table.alias()
                owner = party._customer_inherited_expression('query_postal_owner', lambda ancestor: sa.case((
                    sa.or_(*(ancestor.c['billing_' + leaf].is_not(None) for leaf in party.AddressOutput.model_fields)), ancestor.c.id)))
                p.columns[name] = sa.select(source.c.billing_postal_code).where(source.c.id == owner).scalar_subquery()
            else:
                p.columns[name] = table.c.address_postal_code
        elif noun in ('customer', 'vendor') and name == 'email':
            contacts = schema.customer_contacts if noun == 'customer' else schema.vendor_contacts
            owner = party._customer_collection_owner('contact_mode') if noun == 'customer' else table.c.id
            p.columns[name] = party._contact_sort(table, contacts, noun + '_id', owner, 'primary_email', primary_only=True, fold=False)
        elif name + '_minor_units' in table.c:
            p.columns[name] = table.c[name + '_minor_units']
            currency_key = '__currency_' + name
            p.extra[currency_key] = table.c.get(name + '_currency', sa.literal(currency))
            decoders[name] = lambda value, row, ck=currency_key: Money(value, row[ck]).to_dict() if value is not None else None
        elif name + '_microunits' in table.c:
            p.columns[name] = table.c[name + '_microunits']
            decoders[name] = lambda value, row: format_quantity_micro_units(value) if value is not None else None
        elif name + '_millionths' in table.c or (noun == 'item' and name == 'charge_percent'):
            p.columns[name] = table.c[name + '_millionths' if name != 'charge_percent' else 'other_charge_percent_millionths']
            decoders[name] = lambda value, row: format_percentage_millionths(value) if value is not None else None
        elif name in ('available_balance', 'billing_rate_level_id', 'calculated_due_date', 'calculated_discount_date'):
            p.columns[name] = sa.null()
        elif name in ('average_cost', 'inventory_value'):
            p.columns[name] = sa.literal(0)
            decoders[name] = lambda value, row: Money(value, currency).to_dict()
        elif name.startswith('quantity_'):
            p.columns[name] = sa.literal('0')
        elif name == 'inventory_values_available':
            p.columns[name] = sa.false()
        elif name in ('child_count', 'has_children'):
            child = table.alias('query_children')
            count = sa.select(sa.func.count()).select_from(child).where(child.c.parent_id == table.c.id, child.c.active.is_(True)).scalar_subquery()
            p.columns[name] = count if name == 'child_count' else count > 0
        elif name == 'is_system':
            p.columns[name] = table.c.system_role.is_not(None)
        elif name == 'conversion_target':
            p.columns[name] = table.c.converted_to_type
        elif name == 'item_count':
            p.columns[name] = p.sorts['item_count']
        elif name == 'usage_count':
            # Owning profile contract still exposes the staged count, not operational usage.
            p.columns[name] = sa.literal(0)
        elif name == 'resolved_currency':
            p.columns[name] = sa.func.coalesce(table.c.currency, currency)
        elif name == 'default' and noun == 'custom-field':
            p.columns[name] = table.c.default_canonical_text
            p.extra['__custom_kind'] = table.c.kind
            from bookflow.company.custom_fields import typed_value_from_canonical
            decoders[name] = lambda value, row: typed_value_from_canonical(row['__custom_kind'], value) if value is not None else None
        elif name in ('address', 'emergency_contact'):
            prefix = name + '_'
            leaves = [col for col in table.c if col.name.startswith(prefix)]
            p.columns[name] = sa.case((sa.or_(*(col.is_not(None) for col in leaves)), sa.func.json_object(
                *[part for col in leaves for part in (col.name[len(prefix):], col)])), else_=None)
            decoders[name] = lambda value, row: json.loads(value) if value else None
        else:
            label_refs = {'terms': ('term', 'terms_id'), 'price_level': ('price-level', 'price_level_id'),
                'payment_method': ('payment-method', 'preferred_payment_method_id'),
                'category': ('item-category', 'category_id'), 'default_class': ('class', 'default_class_id'),
                'sales_tax_code': ('sales-tax-code', 'sales_tax_code_id'), 'unit_of_measure_set': ('unit-of-measure', 'unit_of_measure_set_id')}
            if name in label_refs:
                target, field = label_refs[name]
                identifier = party._customer_effective(field) if noun == 'customer' else table.c[field]
                p.columns[name] = _reference(target, identifier, session)
                decoders[name] = lambda value, row: json.loads(value)['label'] if value else None
            else:
                raise ValueError(f'{noun}: missing declared selected projection {name}')
    return decoders
