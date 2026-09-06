"""Bounded public owned-collection reads for selected list columns."""
from __future__ import annotations
import sqlalchemy as sa
from pydantic import Field, JsonValue
from bookflow.company import schema, list_service
from bookflow.company.lists import get_list_definition
from bookflow.company.query import page_state, continuation
from bookflow.company.query_models import Strict
from bookflow.company.query_projection import COLLECTIONS
from bookflow.company.query_catalog import error
from bookflow.core.exact import format_quantity_micro_units, format_percentage_millionths
from bookflow.core.money import Money


class ChildrenInput(Strict):
    record: str
    column: str
    query: str | None = None
    include_inactive: bool = True
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=2048)


class ChildrenOutput(Strict):
    record: str
    version: int
    column: str
    items: list[dict[str, JsonValue]]
    count: int
    matching_total: int
    next_cursor: str | None


def children(noun, inp, session, *, principal_id=None):
    relation = COLLECTIONS.get((noun, inp.column))
    if relation is None:
        raise error(noun, 'Unknown owned collection', column=inp.column)
    definition = get_list_definition(noun)
    owner = list_service.resolve_selector(session.company, getattr(schema, definition.table), definition, inp.record)
    state = page_state(session, noun + ' query children', inp, principal_id)
    child = getattr(schema, relation[0])
    selected = {key: child.c[key] for key in ('id', 'position', 'active')}
    allowed = {
        ('vendor', 'expense_accounts'): ('account_id',),
        ('unit-of-measure', 'units'): ('name', 'abbreviation', 'is_base'),
        ('price-level', 'items'): ('item_id', 'adjustment_basis'),
        ('item', 'members'): ('component_item_id', 'unit_id'),
        ('item', 'vendor_profiles'): ('vendor_id', 'preferred_rank', 'vendor_item_name', 'lead_time_days', 'manufacturer_part_number', 'availability_notes'),
        ('custom-field', 'choices'): ('value',),
        ('custom-field', 'scopes'): ('record_type',),
    }[(noun, inp.column)]
    selected.update({key: child.c[key] for key in allowed})
    decoders = {}
    money_fields = [name.removesuffix('_minor_units') for name in child.c.keys() if name.endswith('_minor_units')]
    for name in money_fields:
        selected[name] = child.c[name + '_minor_units']
        selected['__' + name + '_currency'] = child.c[name + '_currency']
        decoders[name] = lambda value, row, key=name: Money(value, row['__' + key + '_currency']).to_dict() if value is not None else None
    for col in child.c:
        if col.name.endswith('_microunits'):
            key = col.name.removesuffix('_microunits')
            selected[key] = col
            decoders[key] = lambda value, row: format_quantity_micro_units(value) if value is not None else None
        if col.name.endswith('_millionths'):
            key = col.name.removesuffix('_millionths')
            selected[key] = col
            decoders[key] = lambda value, row: format_percentage_millionths(value) if value is not None else None
    if inp.column == 'units':
        from bookflow.core.exact import format_unit_factor_nano_units
        selected['base_factor'] = child.c.base_factor_nanounits
        decoders['base_factor'] = lambda value, row: format_unit_factor_nano_units(value)
    reference_targets = {'account_id': 'account', 'item_id': 'item', 'component_item_id': 'item', 'vendor_id': 'vendor'}
    from bookflow.company.query_projection import _reference, reference_value
    import json
    for name, target in reference_targets.items():
        if name in selected:
            selected[name.removesuffix('_id')] = _reference(target, selected[name], session)
            decoders[name.removesuffix('_id')] = lambda value, row: reference_value(value)
    if 'unit_id' in selected:
        unit = schema.unit_conversions.alias()
        selected['unit_name'] = sa.select(unit.c.name).where(unit.c.id == child.c.unit_id).scalar_subquery()
    where = child.c[relation[1]] == owner['id']
    if not inp.include_inactive:
        where = sa.and_(where, child.c.active.is_(True))
    if inp.query:
        from bookflow.company.query_providers import contains_any
        session.company.raw.create_function('bookflow_query_contains', -1, contains_any, deterministic=True)
        from bookflow.company.list_service import normalize_lookup_key
        where = sa.and_(where, sa.func.bookflow_query_contains(normalize_lookup_key(inp.query),
            *(column for name, column in selected.items() if not name.startswith('__'))) == 1)
    total = session.company.conn.execute(sa.select(sa.func.count()).select_from(child).where(where)).scalar_one()
    statement = sa.select(*(expression.label(key) for key, expression in selected.items())).where(where).order_by(child.c.position, child.c.id)
    rows = [dict(row) for row in session.company.conn.execute(statement.limit(inp.limit + 1).offset(state.offset)).mappings()]
    more = len(rows) > inp.limit
    rows = rows[:inp.limit]
    items = [{key: decoders[key](value, row) if key in decoders else value for key, value in row.items() if not key.startswith('__')} for row in rows]
    return ChildrenOutput(record=owner['id'], version=owner['version'], column=inp.column, items=items,
        count=len(items), matching_total=total, next_cursor=continuation(state, len(items), more))
