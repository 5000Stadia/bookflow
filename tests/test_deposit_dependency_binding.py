"""Actual offline authenticated dispatch and unchanged hosted OS capture."""
import copy
import pytest
from bookflow.core import registry
from bookflow.core.publication import OSBinding
from bookflow.core.errors import BookflowError
from bookflow.company import deposit_dependency_history as history
from bookflow.company.deposit_dependency_models import InspectionRoot
from tests.test_deposit_lifecycle import driver, additional_document
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_commit_hooks import owner_host


def observe(client, monkeypatch, callback, company=COMPANY):
    command = registry.get('company show'); original = command.plan; found = []
    def plan(inp, ctx, s):
        found.append(callback(s))
        return original(inp, ctx, s)
    with monkeypatch.context() as patch:
        patch.setattr(command, 'plan', plan)
        client.run('company show', {}, company=company)
    assert len(found) == 1
    return found[0]


def test_offline_actual_binding_guard_roundtrip_and_tamper(client, sale, driver, monkeypatch):
    document = additional_document(client, sale)
    posted = driver.run('post', {'operation_key': 'binding-history', 'document': document})
    root = InspectionRoot(kind='deposit', id=posted.current.id)
    before = driver.dump()
    def captured(s):
        binding = OSBinding.from_session(s)
        recipe, facts = history.capture(s, root, binding)
        assert not facts.unknown, facts.unknown
        token = history.issue(s, recipe, facts, binding)
        comparison = history.compare(s, token, root, binding)
        assert comparison.matches and not comparison.unknown_history and comparison.changes == ()
        assert comparison.baseline == comparison.current
        with pytest.raises(BookflowError) as error:
            history.compare(s, token[:-2] + ('AA' if token[-2:] != 'AA' else 'BB'), root, binding)
        assert error.value.code == 'E_VALIDATION'
        with pytest.raises(BookflowError) as error:
            history.capture(s, root, None)
        assert error.value.code == 'E_UNAUTHENTICATED'
        return token
    assert observe(client, monkeypatch, captured)
    assert driver.dump() == before


def test_hosted_capture_equals_authenticated_session_constructor(owner_host):
    host, uid, login, _ = owner_host
    binding = OSBinding.capture(host, login)
    actual = host.run_write(uid, login, lambda s: OSBinding.from_session(s))
    assert binding == actual
    assert (binding.user_id, binding.actor_kind, binding.on_behalf_of, binding.authority_epoch, binding.token_id) == (uid, 'human', None, None, None)
