"""Sales snapshot scopes and historical unit-factor dependency seams."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from bookflow.company import custom_fields as cf, journal_custom_fields as custom, schema, undo, units
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from tests.test_row5_custom_fields import conn, _definition  # noqa: F401


@pytest.mark.parametrize('scope', ['invoice', 'sales_receipt'])
@pytest.mark.parametrize('kind,value', [('text', ''), ('bool', False), ('number', '0')])
def test_sales_snapshot_scope_values_clear_and_history(conn, scope, kind, value):
    field = _definition(conn, name='Original', kind=kind, scopes=(scope,), default=value)
    owner = new_id()
    plan = custom.prepare(conn, owner, cf.CustomFieldValuePatch({}), {}, creating=True, record_type=scope)
    assert plan.snapshot[field['id']]['value'] == value
    custom.validate(conn, plan, owner, plan.snapshot, record_type=scope)
    with pytest.raises(BookflowError, match='E_INTERNAL'):
        custom.validate(conn, plan, owner, plan.snapshot)
    custom.apply(conn, plan)
    cf.update_definition(conn, field['id'], {'name': 'Renamed'}, actor_id=new_id(), interface='python')
    kept = custom.prepare(conn, owner, cf.CustomFieldValuePatch({}), plan.snapshot, creating=False, record_type=scope)
    assert kept.snapshot == plan.snapshot
    cleared = custom.prepare(conn, owner, cf.CustomFieldValuePatch({field['id']: None}), plan.snapshot, creating=False, record_type=scope)
    custom.apply(conn, cleared)
    assert cleared.snapshot == {}
    assert undo._active_dependents(conn, 'custom_field', field['id'])
    with pytest.raises(BookflowError, match='E_RECORD_IN_USE'):
        cf.update_definition(conn, field['id'], {'kind': 'date'}, actor_id=new_id(), interface='python')
    restored = custom.prepare(conn, owner, cf.CustomFieldValuePatch({field['id']: value}), {}, creating=False, record_type=scope)
    assert restored.snapshot[field['id']]['value_id'] == plan.snapshot[field['id']]['value_id']


@pytest.mark.parametrize('scope', ['invoice', 'sales_receipt'])
def test_expected_scope_independent_of_derived_plan_and_header(conn, scope):
    owner = new_id()
    plan = custom.prepare(conn, owner, cf.CustomFieldValuePatch({}), {}, creating=True, record_type=scope)
    forged = replace(plan, owner_plan=replace(plan.owner_plan, record_type='journal_entry'))
    with pytest.raises(BookflowError, match='E_INTERNAL'):
        custom.validate(conn, forged, owner, {}, record_type=scope)
    # Minimal aggregate authority on the legacy fixture: no revision is necessary
    # for an existing header's type to fence a pending plan.
    conn.exec_driver_sql('CREATE TABLE transactions (id TEXT PRIMARY KEY, type TEXT, current_revision_id TEXT)')
    conn.exec_driver_sql('CREATE TABLE transaction_revisions (id TEXT PRIMARY KEY, custom_fields_snapshot TEXT)')
    conn.exec_driver_sql('INSERT INTO transactions VALUES (?, ?, NULL)', (owner, 'journal_entry'))
    with pytest.raises(BookflowError, match='E_INTERNAL'):
        custom.prepare(conn, owner, cf.CustomFieldValuePatch({}), {}, creating=True, record_type=scope)


@pytest.mark.parametrize('scope', ['invoice', 'sales_receipt'])
def test_sales_required_and_kind_scope(conn, scope):
    field = _definition(conn, name='Required', scopes=(scope,), required=True, default='value')
    with pytest.raises(BookflowError, match='E_VALIDATION'):
        custom.prepare(conn, new_id(), cf.CustomFieldValuePatch({field['id']: None}), {}, creating=True, record_type=scope)
    patch = cf.CustomFieldValuePatch({field['id']: 'value'})
    custom.validate_kinds(conn, patch, SimpleNamespace(root={field['id']: 'text'}), record_type=scope)
    with pytest.raises(BookflowError, match='E_RECORD_NOT_FOUND'):
        custom.validate_kinds(conn, patch, SimpleNamespace(root={field['id']: 'text'}))
    with pytest.raises(BookflowError, match='E_VALIDATION'):
        custom.validate_kinds(conn, patch, SimpleNamespace(root={field['id']: 'bool'}), record_type=scope)


def test_factor_update_and_undo_read_all_history_without_cache(conn):
    db = SimpleNamespace(conn=conn)
    actor = new_id()
    made = units.plan_unit_create(db, {'name': 'Count', 'units': [
        {'name': 'Each', 'abbreviation': 'ea', 'is_base': True, 'base_factor': '1'},
        {'name': 'Pack', 'abbreviation': 'pk', 'base_factor': '6'},
    ]}, actor_id=actor, via='python')
    units.persist_unit_mutation(db, made)
    owner = made.after['id']
    payload = units._owner_input(made.after, made.after_children)
    payload['units'][1]['base_factor'] = '12'
    changed = units.plan_unit_update(db, owner, payload, actor_id=actor, via='python')
    units.persist_unit_mutation(db, changed)
    # A table appearing after earlier checks must immediately protect its rows.
    # No current-header join: replaced/voided revisions are equally authoritative.
    conn.exec_driver_sql('CREATE TABLE sales_line_profiles (document_line_id TEXT PRIMARY KEY, transaction_id TEXT, revision_id TEXT, unit_id TEXT, unit_factor_nanounits INTEGER)')
    conn.exec_driver_sql('INSERT INTO sales_line_profiles VALUES (?, ?, ?, ?, ?)',
                         (new_id(), new_id(), new_id(), payload['units'][1]['id'], 12_000_000_000))
    payload['units'][1]['base_factor'] = '24'
    with pytest.raises(BookflowError, match='E_ACTIVE_DEPENDENTS'):
        units.plan_unit_update(db, owner, payload, actor_id=actor, via='python')
    current = changed.after_snapshot
    desired = deepcopy(current)
    desired['units'][1]['base_factor'] = '6'
    with pytest.raises(BookflowError, match='E_ACTIVE_DEPENDENTS'):
        undo._validate_domain_target(conn, undo.HANDLERS.get('unit_of_measure', 'update'), current, desired, {'units'}, actor_id=actor, via='python', at='2026-09-05T00:00:00Z')
    assert units._children(db, owner)[1]['base_factor_nanounits'] == 12_000_000_000
    payload['units'][1]['base_factor'] = '12'
    payload['units'].append({'name': 'Case', 'abbreviation': 'cs', 'base_factor': '48'})
    added = units.plan_unit_update(db, owner, payload, actor_id=actor, via='python')
    units.persist_unit_mutation(db, added)
    payload = units._owner_input(added.after, added.after_children)
    payload['units'][2]['base_factor'] = '96'
    assert units.plan_unit_update(db, owner, payload, actor_id=actor, via='python') is not None


@pytest.mark.parametrize('scope', ['invoice', 'sales_receipt'])
def test_voided_header_preserves_captured_revision_authority(conn, scope):
    import json

    field = _definition(conn, name='Captured', scopes=(scope,))
    owner = new_id()
    plan = custom.prepare(conn, owner, cf.CustomFieldValuePatch({field['id']: 'value'}), {}, creating=True, record_type=scope)
    custom.apply(conn, plan)
    conn.exec_driver_sql('CREATE TABLE transactions (id TEXT PRIMARY KEY, type TEXT, current_revision_id TEXT, status TEXT)')
    conn.exec_driver_sql('CREATE TABLE transaction_revisions (id TEXT PRIMARY KEY, custom_fields_snapshot TEXT)')
    revision = new_id()
    conn.exec_driver_sql('INSERT INTO transactions VALUES (?, ?, ?, ?)', (owner, scope, revision, 'voided'))
    conn.exec_driver_sql('INSERT INTO transaction_revisions VALUES (?, ?)', (revision, json.dumps(plan.snapshot)))
    assert undo._active_dependents(conn, 'custom_field', field['id'])
    assert not custom.prepare(conn, owner, cf.CustomFieldValuePatch({}), plan.snapshot, creating=False, record_type=scope).changed
    forged = deepcopy(plan.snapshot)
    forged[field['id']]['name'] = 'Forged historical name'
    with pytest.raises(BookflowError, match='E_INTERNAL'):
        custom.prepare(conn, owner, cf.CustomFieldValuePatch({}), forged, creating=False, record_type=scope)
