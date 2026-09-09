"""The two registered public deposit detail commands and their strict inputs."""
import subprocess
import sys

import pytest
from pydantic import ValidationError

from bookflow.company import deposit_public_models as w
from bookflow.company import deposit_read_models as m
from bookflow.core import registry
from bookflow.core.errors import BookflowError

NAMES = ('deposit show', 'deposit items')


@pytest.fixture(scope='module', autouse=True)
def loaded():
    registry.load_all()


def test_exactly_two_deposit_commands_are_registered():
    assert sorted(c.name for c in registry.all_commands() if c.noun == 'deposit') == sorted(NAMES)
    assert [module for module, nouns in registry.NOUN_MODULES.items() if 'deposit' in nouns] == [
        'bookflow.commands.deposit_cmds']
    # Query, history, print and every write stay unregistered in this stage.
    assert registry.REGISTRY.get('deposit query') is None
    assert registry.REGISTRY.get('deposit post') is None
    assert registry.REGISTRY.get('deposit history') is None


@pytest.mark.parametrize('name', NAMES)
def test_each_command_is_a_company_scoped_member_read(name):
    command = registry.REGISTRY[name]
    assert command.scope == 'company' and command.kind == 'read' and not command.is_write
    assert command.writes == frozenset() and command.apply is None
    assert command.required_role == 'member' and command.capability == 'ledger.read'
    assert command.positional == ['deposit']
    assert not command.accepts_idempotency_key and not command.local_only
    assert not command.clearable and not command.streams and not command.bootstrap
    assert command.feature is None and command.transfer is None
    assert command.version_source is None and not command.requires_explicit_grant
    assert command.description.endswith('.')
    assert 'E_RECORD_NOT_FOUND' in command.error_codes and 'E_PERMISSION' in command.error_codes


def test_the_registered_contracts_are_the_public_wire_models():
    assert registry.REGISTRY['deposit show'].input_model is m.ShowInput
    assert registry.REGISTRY['deposit show'].output_model is w.DepositDetail
    assert registry.REGISTRY['deposit items'].input_model is m.ItemsInput
    assert registry.REGISTRY['deposit items'].output_model is w.DepositItemsPage
    for name in NAMES:
        schema = registry.REGISTRY[name].output_model.model_json_schema()
        assert schema['additionalProperties'] is False
        assert 'guard' not in registry.REGISTRY[name].output_model.model_fields


@pytest.mark.parametrize('name', NAMES)
def test_plan_time_execution_is_refused_without_an_authenticated_reader(name):
    command = registry.REGISTRY[name]
    with pytest.raises(BookflowError) as caught:
        command.plan(None, None, None)
    assert caught.value.code == 'E_INTERNAL'


def test_the_strict_input_semantics_are_the_private_reader_semantics():
    identity = '0' * 26
    assert m.ShowInput(deposit=identity).revision_number is None
    with pytest.raises(ValidationError):
        m.ShowInput(deposit=identity, revision_number=None)
    with pytest.raises(ValidationError):
        m.ShowInput(deposit=identity, as_of=None)
    with pytest.raises(ValidationError):
        m.ShowInput(deposit=identity, unexpected='x')
    with pytest.raises(ValidationError):
        m.ItemsInput(deposit=identity, kind='everything')
    page = m.ItemsInput(deposit=identity, kind='sources').page
    assert page.limit == 50 and page.cursor is None
    with pytest.raises(ValidationError):
        m.ItemsInput(deposit=identity, kind='sources', page=dict(limit=201))


def test_a_cold_registry_load_builds_only_the_deposit_module():
    witness = """
import sys
from bookflow.core import registry
registry.load_all('deposit show')
assert 'deposit show' in registry.REGISTRY and 'deposit items' in registry.REGISTRY
assert 'payment show' not in registry.REGISTRY
assert 'register query' not in registry.REGISTRY
assert 'bookflow.commands.payment_cmds' not in sys.modules
"""
    result = subprocess.run([sys.executable, '-c', witness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
