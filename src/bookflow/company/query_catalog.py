"""Public master column catalogs and bounded, company-authorized discovery.

Declarations select public show fields. Schema columns are never the discovery API.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import get_args, get_origin, Literal, Any
import sqlalchemy as sa
from pydantic import BaseModel

from bookflow.company import schema
from bookflow.company.lists import get_list_definition
from bookflow.company.query import page_state, continuation
from bookflow.company.query_models import Descriptor, OptionsInput, OptionsOutput
from bookflow.core import registry
from bookflow.core.errors import BookflowError

COMMON = ('id', 'version', 'created_at', 'created_by', 'created_via', 'updated_at', 'updated_by', 'updated_via', 'active')
ALIASES = {
    'parent': 'parent_id', 'base': 'base_unit', 'purchase': 'default_purchase_unit',
    'sales': 'default_sales_unit', 'shipping': 'default_shipping_unit',
    'check_name': 'print_name_on_check_as', 'default_accounts': 'expense_accounts',
    'billing_rate': 'billing_rate_level_id', 'linked_customer': 'linked_customer_id',
    'linked_vendor': 'linked_vendor_id', 'delivery_method': 'preferred_delivery_method',
    'ship_method': 'preferred_ship_method_id', 'unit_set': 'unit_of_measure_set',
    'every_conversion': 'units', 'per_item_summary': 'items', 'choice_summary': 'choices',
}
EXCLUDED = {'seed_key', 'created_by', 'created_via', 'updated_by', 'updated_via', 'custom_fields'}
DERIVED = {'bill_of_material_cost', 'combined_percent', 'calculated_due_date', 'calculated_discount_date'}
EXPANSIONS = {'job_dates': ('job_start', 'job_projected_end', 'job_end'),
              'reorder_points': ('reorder_point_min', 'reorder_point_max'),
              'emergency_contact': ('emergency_contact_name', 'emergency_contact_relationship', 'emergency_contact_phone', 'emergency_contact_email')}
LABELS = {'id': 'Record ID', 'version': 'Version', 'full_name': 'Name', 'active': 'Active',
          'tax_id_last4': 'Tax ID (last four)', 'current_balance': 'Current balance',
          'expense_accounts': 'Expense accounts', 'units': 'All unit conversions', 'items': 'Per-item prices',
          'preferred_delivery_method': 'Delivery method', 'preferred_ship_method_id': 'Ship method'}
LABEL_REFERENCES = {
    'terms': ('term', 'terms_id'), 'price_level': ('price-level', 'price_level_id'),
    'payment_method': ('payment-method', 'preferred_payment_method_id'),
    'customer_type': ('customer-type', 'customer_type_id'), 'vendor_type': ('vendor-type', 'vendor_type_id'),
    'sales_rep': ('sales-rep', 'sales_rep_id'), 'category': ('item-category', 'category_id'),
    'default_class': ('class', 'default_class_id'), 'sales_tax_code': ('sales-tax-code', 'sales_tax_code_id'),
    'unit_of_measure_set': ('unit-of-measure', 'unit_of_measure_set_id'), 'preferred_vendor': ('vendor', 'preferred_vendor_id'),
}


def error(noun, problem, **details):
    return BookflowError('E_LIST_FILTER', details={'problem': problem, 'discover': f'{noun} query options', **details})


def key_for(noun, key):
    if key == 'tax_code':
        return 'sales_tax_code_id' if noun == 'customer' else 'sales_tax_code'
    if key == 'tax_item':
        return 'sales_tax_item_id'
    return ALIASES.get(key, key)


def title(key):
    return LABELS.get(key, key.removesuffix('_id').replace('_', ' ').capitalize())


def _type(annotation):
    args = get_args(annotation)
    nullable = type(None) in args or annotation is type(None)
    annotation = next((arg for arg in args if arg is not type(None)), annotation) if nullable else annotation
    origin = get_origin(annotation)
    enum = list(get_args(annotation)) if origin is Literal else []
    if annotation is Any:
        kind, nullable = 'scalar', True
    elif enum:
        kind = 'choice'
    elif annotation is bool:
        kind = 'bool'
    elif annotation is int:
        kind = 'integer'
    elif origin in (list, tuple):
        kind = 'collection'
    elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
        kind = 'money' if {'amount', 'currency'} <= annotation.model_fields.keys() else 'object'
    else:
        kind = 'text'
    return kind, nullable, enum


@lru_cache(maxsize=None)
def builtins(noun):
    definition = get_list_definition(noun)
    model = registry.get(f'{noun} show').output_model
    fields = model.model_fields
    names = []
    for token in definition.all_columns:
        if token == '$custom':
            continue
        if token == '$common':
            names.extend(COMMON)
        elif token == '$stored':
            names.extend(name for name in fields if name not in EXCLUDED | DERIVED and not name.endswith('_ref'))
        elif token == 'all-account-references':
            names.extend(name for name in fields if name.endswith('_account_id'))
        elif token in EXPANSIONS:
            names.extend(EXPANSIONS[token])
        else:
            names.append(key_for(noun, token))
    names.extend((definition.display_field, *COMMON))
    references = {ref.field: ref.target for ref in definition.references if not ref.many and '.' not in ref.field}
    result = {}
    for name in dict.fromkeys(names):
        field = fields.get(name)
        if (name == 'postal_code' and noun in ('customer', 'vendor')) or (name == 'price_level' and noun == 'customer'):
            result[name] = Descriptor(key=name, label=title(name), kind='reference' if name == 'price_level' else 'text',
                reference_noun='price-level' if name == 'price_level' else None, nullable=True)
            continue
        if field is None:
            raise ValueError(f'{noun}: declared public column {name} has no show field')
        kind, nullable, enum = _type(field.annotation)
        ref = references.get(name) or (LABEL_REFERENCES[name][0] if name in LABEL_REFERENCES else None)
        if ref:
            kind = 'reference'
        if name.endswith('_date') or name in ('job_start', 'job_end', 'job_projected_end', 'hire_date', 'release_date'):
            kind = 'date'
        if name.endswith('_percent') or name == 'percent' or name.startswith(('quantity_', 'reorder_point_')) or name == 'assembly_build_point':
            kind = 'number'
        sort = next((s for s in definition.sorts if key_for(noun, s) == name), None)
        result[name] = Descriptor(key=name, label=title(name), kind=kind, nullable=nullable,
            reference_noun=ref, choices=enum, sortable=sort is not None, sort_key=sort)
    return result


def defaults(noun):
    return [key_for(noun, key) for key in get_list_definition(noun).summary_columns]


def definition_rows(noun, session, ids):
    """One bounded lookup validates both scope and retained inactive definitions."""
    if not ids:
        return {}
    d, scopes = schema.custom_field_defs, schema.custom_field_scopes
    statement = sa.select(d).where(d.c.id.in_(ids), sa.exists(sa.select(1).where(
        scopes.c.definition_id == d.c.id, scopes.c.record_type == noun.replace('-', '_'), scopes.c.active.is_(True))))
    rows = {row['id']: dict(row) for row in session.company.conn.execute(statement).mappings()}
    missing = sorted(set(ids) - rows.keys())
    if missing:
        raise error(noun, 'Unknown custom definition or wrong owner target', definitions=missing)
    return rows


def custom_descriptor(row, noun):
    kind = row['kind']
    operators = ['eq', 'ne']
    if kind == 'text':
        operators += ['contains']
    elif kind in ('number', 'date'):
        operators += ['lt', 'lte', 'gt', 'gte']
    return Descriptor(key='custom:' + row['id'], label=row['name'], kind=kind, nullable=True,
        definition=row['id'], active=bool(row['active']), owner_target=noun.replace('-', '_'),
        operators=operators + ['is_missing', 'is_present'])


def selected_descriptors(noun, columns, session):
    if len(columns) != len(set(columns)):
        raise error(noun, 'Duplicate selected columns')
    catalog = builtins(noun)
    dynamic = definition_rows(noun, session, [name[7:] for name in columns if name.startswith('custom:')])
    result = []
    for name in columns:
        if name.startswith('custom:'):
            result.append(custom_descriptor(dynamic[name[7:]], noun))
        elif name in catalog:
            result.append(catalog[name])
        else:
            raise error(noun, 'Unknown selected column', column=name)
    return result


def filter_descriptors(noun, session=None):
    definition = get_list_definition(noun)
    output = registry.get(f'{noun} show').output_model.model_fields
    refs = {ref.field: ref.target for ref in definition.references if not ref.many}
    result = []
    for spec in definition.filters:
        name = spec.field
        kind = {'boolean': 'bool', 'integer': 'integer', 'identifier': 'reference', 'text': 'text'}[spec.kind]
        enum = []
        currency, scale = None, None
        if name in output:
            inferred, _nullable, enum = _type(output[name].annotation)
            if inferred == 'choice':
                kind = inferred
            elif inferred == 'money' and spec.kind == 'integer':
                from bookflow.core.money import CURRENCIES
                kind = 'money'
                if session is not None:
                    currency = session.company_info_row['home_currency']
                    scale = CURRENCIES[currency][0]
        result.append(Descriptor(key=name, label=title(name), kind=kind, reference_noun=refs.get(name),
            operators=['eq'], choices=enum, currency=currency, scale=scale))
    return result


def options(noun, inp: OptionsInput, session, *, principal_id=None):
    state = page_state(session, noun + ' query options', inp, principal_id)
    fixed = []
    d, scopes, choices = schema.custom_field_defs, schema.custom_field_scopes, schema.custom_field_choices
    if inp.kind == 'choices':
        definition = definition_rows(noun, session, [inp.definition])[inp.definition]
        if definition['kind'] != 'choice':
            raise error(noun, 'Definition is not a choice field', definition=inp.definition)
        statement = sa.select(choices).where(choices.c.definition_id == inp.definition)
        if not inp.include_inactive:
            statement = statement.where(choices.c.active.is_(True))
        if inp.keys is not None:
            statement = statement.where(choices.c.id.in_(inp.keys))
        if inp.query:
            from bookflow.company.query_providers import contains_any
            session.company.raw.create_function('bookflow_query_contains', -1, contains_any, deterministic=True)
            from bookflow.company.list_service import normalize_lookup_key
            statement = statement.where(sa.func.bookflow_query_contains(normalize_lookup_key(inp.query), choices.c.value) == 1)
        rows = list(session.company.conn.execute(statement.order_by(choices.c.id).limit(inp.limit + 1).offset(state.offset)).mappings())
        items = [Descriptor(key=row['id'], label=row['value'], kind='choice', active=bool(row['active']),
            definition=inp.definition, owner_target=noun.replace('-', '_')) for row in rows]
    else:
        if inp.kind == 'columns':
            fixed = list(builtins(noun).values())
        elif inp.kind == 'filters':
            fixed = filter_descriptors(noun, session)
        else:
            fixed = [Descriptor(key=key, label=title(key), kind='text', sortable=True, sort_key=key)
                     for key in get_list_definition(noun).sorts]
        if inp.query:
            from bookflow.company.query_providers import contains_any
            from bookflow.company.list_service import normalize_lookup_key
            needle = normalize_lookup_key(inp.query)
            fixed = [item for item in fixed if contains_any(needle, item.label, item.key)]
        if inp.keys is not None:
            fixed = [item for item in fixed if item.key in inp.keys]
        # A SQL union bounds dynamic definitions together with the small static catalog.
        parts = [sa.select(sa.literal(item.key).label('key'), sa.literal(item.model_dump_json()).label('payload')) for item in fixed]
        if inp.kind in ('columns', 'filters') and get_list_definition(noun).custom_fields:
            dynamic = sa.select(('custom:' + d.c.id).label('key'), sa.func.json_object(
                'id', d.c.id, 'name', d.c.name, 'kind', d.c.kind, 'active', d.c.active).label('payload')).where(
                sa.exists(sa.select(1).where(scopes.c.definition_id == d.c.id,
                    scopes.c.record_type == noun.replace('-', '_'), scopes.c.active.is_(True))))
            if not inp.include_inactive:
                dynamic = dynamic.where(d.c.active.is_(True))
            if inp.keys is not None:
                dynamic = dynamic.where(('custom:' + d.c.id).in_(inp.keys))
            if inp.query:
                session.company.raw.create_function('bookflow_query_contains', -1, contains_any, deterministic=True)
                dynamic = dynamic.where(sa.func.bookflow_query_contains(needle, d.c.name, d.c.id) == 1)
            parts.append(dynamic)
        if parts:
            combined = sa.union_all(*parts).subquery()
            rows = session.company.conn.execute(sa.select(combined).order_by(combined.c.key).limit(inp.limit + 1).offset(state.offset)).mappings()
            items = []
            for row in rows:
                value = json.loads(row['payload'])
                items.append(custom_descriptor(value, noun) if 'id' in value else Descriptor.model_validate(value))
        else:
            items = []
    more = len(items) > inp.limit
    items = items[:inp.limit]
    return OptionsOutput(kind=inp.kind, items=items, count=len(items), default_columns=defaults(noun),
                         next_cursor=continuation(state, len(items), more))
