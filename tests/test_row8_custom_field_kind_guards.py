"""Captured field kinds survive the preview/writer boundary on public commands."""
import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import journals, schema
from tests.test_row8_journal import COMPANY, journal_accounts  # noqa: F401
from tests.test_row8_custom_field_integration import definition, post, complete_state


@pytest.mark.parametrize('surface', ['journal', 'register'])
def test_changed_definition_kind_rejects_without_any_write(client, journal_accounts, surface):
    field = definition(client, 'Guarded numeric label')
    client.run('custom-field update', {'custom_field': field['id'], 'kind': 'number',
        'expected_version': field['version']}, company=COMPANY)
    before = complete_state(client)
    args = {'custom_fields': {field['id']: '001'}, 'custom_field_kinds': {field['id']: 'text'}}
    with pytest.raises(BookflowError) as error:
        if surface == 'journal':
            post(client, journal_accounts, **args)
        else:
            client.register.post(account=journal_accounts[0], category=journal_accounts[1],
                date='2026-04-01', direction='decrease', amount='1.00', company=COMPANY, **args)
    assert error.value.code == 'E_VALIDATION'
    assert complete_state(client) == before


def test_kind_guard_is_rechecked_inside_writer_and_rolls_back(client, journal_accounts, monkeypatch):
    field = definition(client, 'Concurrent type guard')
    before = complete_state(client)
    original = journals.apply

    def changed_definition(plan, ctx, session):
        assert session.company.write_transaction
        session.company.conn.execute(schema.custom_field_defs.update().where(
            schema.custom_field_defs.c.id == field['id']).values(kind='number'))
        return original(plan, ctx, session)

    # The preview phase reads text; a changed writer snapshot cannot turn 001
    # into number 1 after that preview has validated.
    from bookflow.core import registry
    monkeypatch.setattr(registry.get('journal post'), 'apply', changed_definition)
    with pytest.raises(BookflowError) as error:
        post(client, journal_accounts, custom_fields={field['id']: '001'},
             custom_field_kinds={field['id']: 'text'}, idempotency_key='kind-race')
    assert error.value.code == 'E_VALIDATION'
    assert complete_state(client) == before
    assert client.run('custom-field show', {'custom_field': field['id']}, company=COMPANY)['kind'] == 'text'


@pytest.mark.parametrize('patch,kinds', [({}, {'invalid': 'text'}), ({}, {'field': 'text'}),
                                        ({'field': None}, {'field': 'text'}),
                                        ({'field': 'x'}, {'field': 'unknown'})])
def test_kind_metadata_must_describe_a_supplied_typed_value(client, journal_accounts, patch, kinds):
    field = definition(client, 'Metadata guard')
    replace = lambda values: {field['id'] if k == 'field' else k: v for k, v in values.items()}
    before = complete_state(client)
    with pytest.raises(BookflowError) as error:
        post(client, journal_accounts, custom_fields=replace(patch), custom_field_kinds=replace(kinds))
    assert error.value.code == 'E_VALIDATION'
    assert complete_state(client) == before
