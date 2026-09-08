"""Declaration-only preflight witnesses; no database, seed, or timing threshold."""
from copy import deepcopy
import hashlib
import json

import pytest
from pydantic import BaseModel, create_model
from pydantic.fields import FieldInfo
from pydantic.json_schema import GenerateJsonSchema

from bookflow.company import deposit_read_manifest as manifest, deposit_models
from bookflow.company.deposit_lifecycle_models import LifecycleOutput
from bookflow.company.deposit_coordinate_models import CoordinateOutput
from bookflow.company.deposit_draft_models import Manifest
from bookflow.company.deposit_read_models import Issuer
from bookflow.company.journal_custom_fields import SnapshotField
from bookflow.core.errors import BookflowError


def roots():
    return (deposit_models.Effect, LifecycleOutput, CoordinateOutput,
            Manifest, Issuer, SnapshotField)


def raw_digest(model):
    return hashlib.sha256(json.dumps(model.model_json_schema(), sort_keys=True).encode()).hexdigest()


def accept_declarations(monkeypatch):
    # Test-only future manifest, derived with the unchanged original expression.
    monkeypatch.setattr(manifest, 'CODECS', {m.__name__: raw_digest(m) for m in roots()})


def rebuild(monkeypatch, model):
    # model_rebuild replaces these attributes. Restore the actual declarations
    # after each witness, including the compiled validator and serializer.
    for name in ('__pydantic_core_schema__', '__pydantic_validator__',
                 '__pydantic_serializer__', '__pydantic_complete__'):
        monkeypatch.setattr(model, name, getattr(model, name))
    model.model_rebuild(force=True)


def add_field(monkeypatch, model):
    monkeypatch.setattr(model, '__pydantic_fields__', {
        **model.model_fields, 'cost_witness': FieldInfo(annotation=str, default='witness'),
    })
    rebuild(monkeypatch, model)


def assert_original_failure(monkeypatch):
    with pytest.raises(BookflowError) as optimized:
        manifest.conform()
    with monkeypatch.context() as patch:
        patch.setattr(manifest, '_codec_digest', lambda slot, model: raw_digest(model))
        with pytest.raises(BookflowError) as original:
            manifest.conform()
    assert optimized.value.to_dict() == original.value.to_dict()
    assert optimized.value.code == 'E_DEPOSIT_SOURCE_INVALID'


def test_unchanged_warm_conformance_avoids_schema_generation(monkeypatch):
    assert {m.__name__: raw_digest(m) for m in roots()} == manifest.CODECS
    calls = []
    original = GenerateJsonSchema.generate

    def counted(self, *args, **kwargs):
        calls.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(GenerateJsonSchema, 'generate', counted)
    assert manifest.conform() is None
    cold_calls = len(calls)
    for _ in range(3):
        assert manifest.conform() is None
    assert len(calls) == cold_calls


@pytest.mark.parametrize('change', [
    'expected_codec', 'root_replacement', 'root_rebuild', 'nested_rebuild',
    'nested_config', 'nested_config_in_place', 'core_in_place', 'nested_doc', 'provider_binding',
])
def test_warm_declaration_changes_keep_original_failure(monkeypatch, change):
    account = deposit_models.Account
    if change == 'nested_config_in_place':
        monkeypatch.setattr(account, 'model_config', {
            **account.model_config, 'json_schema_extra': {'x-cost': {'values': [1]}},
        })
        accept_declarations(monkeypatch)
    manifest.conform()
    if change == 'expected_codec':
        monkeypatch.setattr(manifest, 'CODECS', dict(manifest.CODECS, Effect='changed'))
    elif change == 'root_replacement':
        monkeypatch.setattr(deposit_models, 'Effect', create_model(
            'Effect', __base__=deposit_models.Effect, cost_witness=(str, 'witness'),
        ))
    elif change == 'root_rebuild':
        add_field(monkeypatch, deposit_models.Effect)
    elif change == 'nested_rebuild':
        add_field(monkeypatch, deposit_models.Cell)
        rebuild(monkeypatch, deposit_models.Effect)
    elif change == 'nested_config':
        monkeypatch.setattr(account, 'model_config', dict(account.model_config, title='Changed account'))
    elif change == 'nested_config_in_place':
        # No replacement dict and no rebuild: a core-schema-only comparison
        # cannot see this generator input, even though the digest changes.
        core_before = deepcopy(deposit_models.Effect.__pydantic_core_schema__)
        account.model_config['json_schema_extra']['x-cost']['values'][0] = 2
        assert deposit_models.Effect.__pydantic_core_schema__ == core_before
    elif change == 'nested_doc':
        monkeypatch.setattr(account, '__doc__', 'New captured account description')
    elif change == 'provider_binding':
        # The default function bound to another class is a different provider.
        monkeypatch.setattr(deposit_models.Effect, 'model_json_schema', Issuer.model_json_schema)
    else:
        # Mutate a real compiled schema in place, then restore the original
        # object and its contents without rebuilding unrelated declarations.
        core = deposit_models.Effect.__pydantic_core_schema__
        def first_string(node):
            if isinstance(node, dict):
                if node.get('type') == 'str':
                    return node
                children = node.values()
            elif isinstance(node, (list, tuple)):
                children = node
            else:
                return None
            for child in children:
                found = first_string(child)
                if found is not None:
                    return found
        monkeypatch.setitem(first_string(core), 'min_length', 7)
        assert deposit_models.Effect.__pydantic_core_schema__ is core
    assert_original_failure(monkeypatch)


@pytest.mark.parametrize('provider', ['root_method', 'nested_hook', 'config_callback', 'field_callback'])
def test_custom_schema_providers_remain_uncached(monkeypatch, provider):
    model = deposit_models.Effect
    nested = deposit_models.Cell
    state = {'value': 1, 'calls': 0}

    def extra(schema):
        state['calls'] += 1
        schema['x-provider'] = state['value']

    if provider == 'root_method':
        original = BaseModel.model_json_schema.__func__
        def custom(cls, *args, **kwargs):
            schema = original(cls, *args, **kwargs)
            extra(schema)
            return schema
        monkeypatch.setattr(model, 'model_json_schema', classmethod(custom))
    elif provider == 'nested_hook':
        def custom(cls, core, handler):
            schema = handler(core)
            extra(schema)
            return schema
        monkeypatch.setattr(nested, '__get_pydantic_json_schema__', classmethod(custom))
        rebuild(monkeypatch, nested)
        rebuild(monkeypatch, model)
    elif provider == 'config_callback':
        monkeypatch.setattr(nested, 'model_config', dict(nested.model_config, json_schema_extra=extra))
    else:
        field = deepcopy(nested.model_fields['row_id'])
        field.json_schema_extra = extra
        monkeypatch.setattr(nested, '__pydantic_fields__', dict(nested.model_fields, row_id=field))
        rebuild(monkeypatch, nested)
        rebuild(monkeypatch, model)
    accept_declarations(monkeypatch)
    manifest.conform()
    before = state['calls']
    manifest.conform()
    assert state['calls'] > before
    # Same callback and compiled objects, different external state.
    state['value'] = 2
    assert_original_failure(monkeypatch)


def test_unsupported_configuration_uses_original_generation(monkeypatch):
    # Pydantic accepts dict subclasses; the memo deliberately does not infer
    # their copying/equality behavior or extend its supported input inventory.
    class Extra(dict):
        pass

    extra = Extra({'x-unsupported': 1})
    monkeypatch.setattr(Issuer, 'model_config', dict(Issuer.model_config, json_schema_extra=extra))
    accept_declarations(monkeypatch)
    calls = []
    original = GenerateJsonSchema.generate

    def counted(self, schema, *args, **kwargs):
        if schema is Issuer.__pydantic_core_schema__:
            calls.append(1)
        return original(self, schema, *args, **kwargs)

    monkeypatch.setattr(GenerateJsonSchema, 'generate', counted)
    manifest.conform()
    manifest.conform()
    assert len(calls) == 2
    extra['x-unsupported'] = 2
    assert_original_failure(monkeypatch)


def test_generation_exception_and_changed_inputs_do_not_publish(monkeypatch):
    manifest.conform()
    monkeypatch.setattr(Issuer, 'model_config', dict(Issuer.model_config, title='Before generation'))
    accept_declarations(monkeypatch)
    original = GenerateJsonSchema.generate
    state = {'fault': 'raise', 'calls': 0}

    def interrupted(self, schema, *args, **kwargs):
        result = original(self, schema, *args, **kwargs)
        if schema is Issuer.__pydantic_core_schema__:
            state['calls'] += 1
            if state['fault'] == 'raise':
                raise RuntimeError('generation interrupted')
            if state['fault'] == 'change':
                Issuer.model_config['title'] = 'Changed during generation'
        return result

    monkeypatch.setattr(GenerateJsonSchema, 'generate', interrupted)
    for _ in range(2):
        with pytest.raises(RuntimeError, match='generation interrupted'):
            manifest.conform()
    assert state['calls'] == 2
    state['fault'] = 'change'
    assert manifest.conform() is None  # The original call also returns its derived digest.
    state['fault'] = None
    assert_original_failure(monkeypatch)
    assert state['calls'] >= 4
