import json
import re

import pytest
from jsonschema import Draft202012Validator

from bookflow.adapters.mcp.catalog import BRIDGE_VERSION, HELP_VIEWS, command_help, descriptor, list_commands
from bookflow.adapters.mcp.envelopes import tool_schema, validate
from bookflow.core import registry
from bookflow.core.errors import BookflowError
from bookflow.documentation.examples import EXAMPLES
from bookflow.documentation.generate import command_document


@pytest.mark.timeout(180)
def test_every_registry_command_has_complete_views_and_resolvable_schemas():
    registry.load_all()
    for cmd in registry.all_commands(include_standalone=True):
        for view in HELP_VIEWS:
            doc = command_help(cmd.name, view)
            assert doc['view'] == view and doc['available_views'] == HELP_VIEWS
            assert doc['bridge_version'] == BRIDGE_VERSION == 2
            assert all(doc[key] == value for key, value in descriptor(cmd).items())
            assert set(cmd.error_codes) <= set(doc['error_codes'])
            assert set(doc['context']) <= set(doc['context_schema']['properties'])
            assert Draft202012Validator(doc['context_schema']).is_valid({'dry_run': False, 'reason': None,
                'source_ref': None, 'directive': None, 'idempotency_key': None, 'company': None})
            for key, model in [('input_schema', cmd.input_model), ('output_schema', cmd.output_model)]:
                present = view == 'full' or view == key or (view == 'usage' and key == 'input_schema')
                assert (key in doc) is present
                if present:
                    assert doc[key] == model.model_json_schema()
                    Draft202012Validator.check_schema(doc[key])
                    def references(value):
                        if isinstance(value, dict):
                            if '$ref' in value:
                                target = doc[key]
                                assert value['$ref'].startswith('#/')
                                for part in value['$ref'][2:].split('/'):
                                    target = target[part.replace('~1', '/').replace('~0', '~')]
                            for child in value.values():
                                references(child)
                        elif isinstance(value, list):
                            for child in value:
                                references(child)
                    references(doc[key])
            if view == 'full':
                assert doc['documentation'] == command_document(cmd)
            if view == 'usage':
                assert EXAMPLES[cmd.name].invocation in doc['documentation']
                assert 'CLI invocation' in doc['documentation']
                assert '### Output' not in doc['documentation']
                assert 'input: {}' in doc['documentation']
                if not (cmd.local_only or cmd.standalone):
                    example = json.loads(re.search(r'```json\n(.*?)\n```', doc['documentation'], re.S)[1])
                    assert example['command'] == cmd.name and 'context' not in example
                    validate('bookflow_run', example)
                    cmd.input_model.model_validate(example['input'])
                    assert ('company' in example) == (cmd.scope == 'company')
                    assert ('dry_run' in example) == cmd.is_write
                    assert ('transport' in example) == bool(cmd.transfer)

            if view.endswith('_schema'):
                assert 'documentation' not in doc
    assert len(list_commands()['commands']) == 20


@pytest.mark.parametrize('view', [None, '', 'schema', 1, False])
def test_bad_help_views_reject_in_schema_and_runtime(view):
    raw = {'command': 'invoice post', 'view': view}
    assert not Draft202012Validator(tool_schema('bookflow_help')).is_valid(raw)
    with pytest.raises(BookflowError) as caught:
        validate('bookflow_help', raw)
    assert caught.value.code == 'E_VALIDATION'


def test_context_constraints_and_document_budget():
    from mcp import types
    for name in ['invoice post', 'invoice update', 'invoice show', 'payment receive', 'payment update']:
        doc = command_help(name)
        document_bytes = len(json.dumps(doc, ensure_ascii=True, separators=(', ', ': ')).encode('utf-8'))
        reply = types.CallToolResult(content=[types.TextContent(text=json.dumps(doc, ensure_ascii=False, allow_nan=False))],
                                     structured_content=doc, is_error=False)
        print(name, 'document_bytes', document_bytes, 'sdk_result_bytes', len(reply.model_dump_json(by_alias=True).encode()))
        if name != 'invoice show':
            assert document_bytes <= 20_000
            context = doc['context_schema']['properties']
            assert context['reason']['anyOf'][0]['maxLength'] == 140
            assert context['dry_run']['default'] is False
            assert context['dry_run']['type'] == 'boolean'
    assert 'does not send' in command_help('invoice post')['documentation']


def test_payment_help_preserves_shared_guidance_and_discovery_paging():
    from bookflow.adapters.mcp.envelopes import TOOLS
    for name in ('payment receive', 'payment apply', 'payment unapply', 'payment void', 'payment update'):
        usage = command_help(name)['documentation']
        full = command_help(name, 'full')['documentation']
        for text in ('input.operation_key', "preview result's facts_fingerprint", 'input.expected_facts_fingerprint',
                     'same operation_key', 'payment operation show', 'invoice settlement'):
            assert text in usage and text in full
    for name in ('payment receive', 'payment update'):
        for text in ('check/reference number', 'internal receipt number', 'Undeposited Funds holding account'):
            assert text in command_help(name)['documentation']
    assert 'remaining 100.00 is unapplied credit owned by the parent' in command_help('payment receive')['documentation']
    description = TOOLS['bookflow_list_commands'][1]
    assert all(text in description for text in ('prefix', 'default 20', 'next_cursor', '200'))
    first = list_commands(prefix='payment', limit=20)
    second = list_commands(prefix='payment', cursor=first['next_cursor'])
    complete = list_commands(prefix='payment', limit=200)
    assert first['commands'] + second['commands'] == complete['commands']
    assert second['next_cursor'] is None
