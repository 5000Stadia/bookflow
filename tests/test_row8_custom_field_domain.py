"""Isolated owner-slot and immutable snapshot witnesses."""
from copy import deepcopy
from dataclasses import replace

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from bookflow.company import custom_fields as cf, journal_custom_fields as journal, schema, undo
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from tests.test_row5_custom_fields import conn, _definition  # noqa: F401


def definition(conn, **kwargs):
    return _definition(conn, scopes=("journal_entry",), **kwargs)


def prepare(conn, owner, patch, previous=None, *, creating=False, refresh=False):
    return journal.prepare(conn, owner, cf.CustomFieldValuePatch(patch), previous or {},
                           creating=creating, refresh=refresh)


def edit(conn, item, **patch):
    return cf.update_definition(conn, item['id'], patch, actor_id=new_id(), interface='python')


@pytest.mark.parametrize('kind,value,canonical', [
    ('text', '', ''), ('text', '  untouched  ', '  untouched  '),
    ('number', '000.000', '0'), ('number', '-9223372036.854775808', '-9223372036.854775808'),
    ('date', '2024-02-29', '2024-02-29'), ('bool', False, 'false'), ('choice', ' WEB ', 'Web'),
])
def test_snapshot_typed_values_and_safe_touches(conn, kind, value, canonical):
    kwargs = {'choices': (cf.CustomFieldChoiceInput(value='Web'),)} if kind == 'choice' else {}
    item = definition(conn, name='Field', kind=kind, **kwargs)
    owner = new_id()
    plan = prepare(conn, owner, {item['id']: value}, creating=True)
    captured = plan.snapshot[item['id']]
    assert captured['canonical_text'] == canonical
    assert captured['value'] == (False if kind == 'bool' else canonical)
    assert set(captured) == set(journal.SnapshotField.model_fields)
    touched, = journal.touches(plan)
    assert touched.before is None and touched.version_before is touched.version_after is None
    assert set(touched.after) == {'id', 'def_id', 'record_type', 'record_id', 'active', 'canonical_text'}
    assert touched.db == 'company' and touched.record_type == 'custom_field_value'
    journal.apply(conn, plan)
    assert conn.execute(sa.select(schema.custom_field_values.c.canonical_text)).scalar_one() == canonical
    same = prepare(conn, owner, {item['id']: value}, plan.snapshot)
    assert not same.changed and not journal.touches(same)


def test_clear_and_reactivate_reuses_slot_and_omitted_update_never_defaults(conn):
    item = definition(conn, name='Text', default='First')
    owner = new_id()
    first = prepare(conn, owner, {}, creating=True)
    journal.apply(conn, first)
    cleared = prepare(conn, owner, {item['id']: None}, first.snapshot)
    assert cleared.snapshot == {} and cleared.changed
    assert journal.touches(cleared)[0].after['canonical_text'] == 'First'
    journal.apply(conn, cleared)
    edit(conn, item, default='New', required=True)
    assert not prepare(conn, owner, {}).changed
    restored = prepare(conn, owner, {item['id']: 'Back'})
    assert restored.snapshot[item['id']]['value_id'] == first.snapshot[item['id']]['value_id']
    journal.apply(conn, restored)
    with pytest.raises(BookflowError, match='E_VALIDATION'):
        prepare(conn, owner, {item['id']: None}, restored.snapshot)


@pytest.mark.parametrize('scope', ['customer', 'vendor', 'employee', 'other_name', 'item', 'journal_entry'])
def test_required_null_create_rejected_even_with_default(conn, scope):
    item = _definition(conn, name='Required', scopes=(scope,), required=True, default='Default')
    with pytest.raises(BookflowError, match='E_VALIDATION'):
        cf.plan_owner_value_patch(conn, record_type=scope, record_id=new_id(),
                                  patch={item['id']: None}, creating=True)


def test_optional_null_suppresses_default_and_missing_required_still_errors(conn):
    item = definition(conn, name='Optional', default='Default')
    assert prepare(conn, new_id(), {item['id']: None}, creating=True).snapshot == {}
    definition(conn, name='Required', required=True)
    with pytest.raises(BookflowError, match='E_VALIDATION'):
        prepare(conn, new_id(), {}, creating=True)


def test_choice_spelling_preserves_canonical_and_identity_refreshes_only_metadata(conn):
    choice = cf.CustomFieldChoiceInput(value='Web')
    item = definition(conn, name='Source', kind='choice', choices=(choice,), default='Web')
    owner = new_id()
    first = prepare(conn, owner, {}, creating=True)
    journal.apply(conn, first)
    changed = edit(conn, item, name='Origin', position=9,
                   choices=(dict(id=choice.id, value='WEB'),))
    assert changed['default'] == 'WEB'
    equal = prepare(conn, owner, {item['id']: ' web '}, first.snapshot)
    assert not equal.changed and equal.snapshot == first.snapshot
    refreshed = prepare(conn, owner, {}, first.snapshot, refresh=True)
    fact = refreshed.snapshot[item['id']]
    assert fact['name'] == 'Origin' and fact['position'] == 9
    assert fact['canonical_text'] == fact['value'] == 'Web'
    assert fact['choice_label'] == 'WEB' and fact['choice_id'] == choice.id
    assert fact['value_id'] == first.snapshot[item['id']]['value_id']
    assert refreshed.changed and not journal.touches(refreshed)
    edit(conn, item, active=False)
    assert not prepare(conn, owner, {item['id']: 'WEB'}, first.snapshot).changed
    with pytest.raises(BookflowError, match='E_RECORD_IN_USE'):
        edit(conn, item, default=None, choices=(dict(value='New'),))
    with pytest.raises(BookflowError, match='E_RECORD_IN_USE'):
        edit(conn, item, default=None, choices=(dict(value='WEB'),))


def test_retained_choice_default_must_remain_available(conn):
    choice = cf.CustomFieldChoiceInput(value='Web')
    item = definition(conn, name='Source', kind='choice', choices=(choice,), default='Web')
    with pytest.raises(BookflowError, match='E_VALIDATION'):
        edit(conn, item, choices=(dict(value='Phone'),))
    assert edit(conn, item, default=None, choices=(dict(value='Phone'),))['default'] is None


@pytest.mark.parametrize('damage', ['owner', 'definition', 'row_id', 'active', 'float_active', 'canonical', 'before', 'omit_mutation',
                                  'snapshot_omit', 'snapshot_value', 'snapshot_id', 'snapshot_name', 'snapshot_kind',
                                  'snapshot_version', 'snapshot_choice'])
def test_validator_rejects_corrupt_pending_facts(conn, damage):
    item = definition(conn, name='Field')
    owner = new_id()
    first = prepare(conn, owner, {item['id']: 'Old'}, creating=True)
    journal.apply(conn, first)
    plan = prepare(conn, owner, {item['id']: 'New'}, first.snapshot)
    mutation, = plan.owner_plan.mutations
    changes = {'owner': {'record_id': new_id()}, 'definition': {'definition_id': new_id()},
               'row_id': {'row_id': new_id()}, 'active': {'active': False}, 'float_active': {'active': 1.0},
               'canonical': {'canonical_text': 'Forged'}, 'before': {'before_canonical_text': 'Forged'}}
    if damage in changes:
        plan = replace(plan, owner_plan=replace(plan.owner_plan, mutations=(replace(mutation, **changes[damage]),)))
    elif damage == 'omit_mutation':
        plan = replace(plan, owner_plan=replace(plan.owner_plan, mutations=(), logical_paths=()))
    else:
        snapshot = deepcopy(plan.snapshot)
        if damage == 'snapshot_omit':
            snapshot.clear()
        else:
            key, value = {'snapshot_value': ('value', 'Forged'), 'snapshot_id': ('value_id', new_id()),
                          'snapshot_name': ('name', 'Forged'), 'snapshot_kind': ('kind', 'date'),
                          'snapshot_version': ('definition_version', 99),
                          'snapshot_choice': ('choice_id', new_id())}[damage]
            snapshot[item['id']][key] = value
        plan = replace(plan, snapshot=snapshot)
    with pytest.raises(BookflowError, match='E_INTERNAL'):
        journal.validate(conn, plan, owner, plan.snapshot)


def test_actual_revision_snapshot_is_validated_separately_and_old_facts_preserved(conn):
    item = definition(conn, name='Original')
    owner = new_id()
    first = prepare(conn, owner, {item['id']: 'Value'}, creating=True)
    journal.apply(conn, first)
    edit(conn, item, name='Current')
    plan = prepare(conn, owner, {}, first.snapshot)
    corrupt = deepcopy(plan.snapshot)
    corrupt[item['id']]['name'] = 'Current'
    with pytest.raises(BookflowError, match='E_INTERNAL'):
        journal.validate(conn, plan, owner, corrupt)
    forged = replace(plan, snapshot=corrupt)
    with pytest.raises(BookflowError, match='E_INTERNAL'):
        journal.validate(conn, forged, owner, corrupt)
    assert journal.project(plan.snapshot)[0]['name'] == 'Original'


def test_multiple_inserts_preserve_definition_to_slot_identity(conn):
    items = [definition(conn, name=name) for name in ['Zulu', 'Alpha', 'Beta']]
    plan = prepare(conn, new_id(), {item['id']: item['name'] for item in items}, creating=True)
    assert [row['name'] for row in journal.project(plan.snapshot)] == ['Alpha', 'Beta', 'Zulu']
    assert len({row['value_id'] for row in plan.snapshot.values()}) == 3


@pytest.mark.parametrize('damage', [dict(value=1.0), dict(kind='number', value='1', canonical_text='1.0'),
                                  dict(kind='bool', value='false', canonical_text='false'),
                                  dict(choice_label='Extra'), dict(definition_version=True)])
def test_snapshot_model_rejects_loose_or_inconsistent_values(damage):
    raw = dict(definition_id=new_id(), value_id=new_id(), name='Field', kind='text', value='Value',
               canonical_text='Value', definition_version=1, position=0)
    with pytest.raises(ValidationError):
        journal.SnapshotField.model_validate(raw | damage)


def test_journal_dependency_guards_and_first_use_survive_clear(conn):
    item = definition(conn, name='Field')
    owner = new_id()
    first = prepare(conn, owner, {item['id']: 'Used'}, creating=True)
    journal.apply(conn, first)
    assert undo._active_dependents(conn, 'custom_field', item['id']) == [dict(record_type='custom_field_value', count=1)]
    with pytest.raises(sa.exc.IntegrityError):
        with conn.begin_nested():
            conn.execute(schema.custom_field_defs.delete().where(schema.custom_field_defs.c.id == item['id']))
    cleared = prepare(conn, owner, {item['id']: None}, first.snapshot)
    journal.apply(conn, cleared)
    assert undo._active_dependents(conn, 'custom_field', item['id']) == []
    with pytest.raises(BookflowError, match='E_RECORD_IN_USE'):
        edit(conn, item, scopes=('customer',))
    with pytest.raises(BookflowError, match='E_RECORD_IN_USE'):
        edit(conn, item, kind='number')


def test_undo_choice_guard_uses_normalized_key_and_stable_id(conn):
    choice = cf.CustomFieldChoiceInput(value='Web')
    item = definition(conn, name='Source', kind='choice', choices=(choice,))
    owner = new_id()
    plan = prepare(conn, owner, {item['id']: 'Web'}, creating=True)
    journal.apply(conn, plan)
    edit(conn, item, choices=(dict(id=choice.id, value='WEB'),))
    current = undo._custom_field_snapshot(conn, item['id'])
    desired = deepcopy(current)
    desired['choices'][0]['value'] = 'web'
    undo._validate_custom_definition(conn, current, desired, {'choices'})
    desired['choices'][0]['id'] = new_id()
    with pytest.raises(BookflowError, match='E_UNDO_CONFLICT'):
        undo._validate_custom_definition(conn, current, desired, {'choices'})


@pytest.mark.parametrize('scope', sorted(cf.LIST_VALUE_SCOPES))
def test_shared_list_choice_equality_and_retirement_use_normalized_keys(conn, scope):
    choice = cf.CustomFieldChoiceInput(value='Café')
    item = _definition(conn, name='Source', kind='choice', scopes=(scope,), choices=(choice,))
    owner = new_id()
    first = cf.plan_owner_value_patch(conn, record_type=scope, record_id=owner,
                                     patch={item['id']: 'Café'}, creating=True)
    cf.apply_owner_value_plan(conn, first)
    edit(conn, item, choices=(dict(id=choice.id, value='CAFÉ'),))
    equal = cf.plan_owner_value_patch(conn, record_type=scope, record_id=owner,
                                     patch={item['id']: ' cafe\u0301 '})
    assert not equal.changed
    assert conn.execute(sa.select(schema.custom_field_values.c.canonical_text)).scalar_one() == 'Café'
    with pytest.raises(BookflowError, match='E_RECORD_IN_USE'):
        edit(conn, item, choices=(dict(value='CAFÉ'),))


def test_cleared_choice_can_retire_and_reactivation_captures_new_identity(conn):
    choice = cf.CustomFieldChoiceInput(value='Web')
    item = definition(conn, name='Source', kind='choice', choices=(choice,))
    owner = new_id()
    first = prepare(conn, owner, {item['id']: 'Web'}, creating=True)
    journal.apply(conn, first)
    clear = prepare(conn, owner, {item['id']: None}, first.snapshot)
    journal.apply(conn, clear)
    replacement = cf.CustomFieldChoiceInput(value='WEB')
    edit(conn, item, choices=(replacement,))
    restored = prepare(conn, owner, {item['id']: 'web'})
    assert restored.snapshot[item['id']]['choice_id'] == replacement.id
    assert restored.snapshot[item['id']]['canonical_text'] == 'WEB'
    assert restored.snapshot[item['id']]['value_id'] == first.snapshot[item['id']]['value_id']
    journal.apply(conn, restored)
    # The retired old same-key choice does not block unrelated definition edits.
    edit(conn, item, position=2)
