"""Genuine input/domain errors use verified delivery, including requested files."""
import json
import os
from pathlib import Path
import sys

import anyio

from tests.test_row3_host import hosted, live


def test_installed_input_rejections_publish_exact_error_files_and_no_writes(hosted, live, tmp_path, monkeypatch):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    inbox, outbox = tmp_path / 'inbox', tmp_path / 'outbox'
    inbox.mkdir(mode=0o700); outbox.mkdir(mode=0o700)
    source = inbox / 'receipt.pdf'; source.write_bytes(b'%PDF-1.4\nreceipt')
    before = hosted.ok('audit.list', {'limit': 200}, company=hosted.company_id)
    from bookflow.core import registry
    def unexpected_execution(*args, **kwargs):
        raise AssertionError('A validation/preparation rejection executed a business planner')
    for name in ('company update', 'account list', 'attachment add', 'attachment get'):
        monkeypatch.setattr(registry.get(name), 'plan', unexpected_execution)

    cases = [
        ('company update', {'unregistered_input': True}, {}),
        ('company update', {'unregistered_input': True}, {'prepare_only': True}),
        ('account list', {'unregistered_input': True}, {}),
        ('attachment add', {'unregistered_input': True}, {'input_file': str(source)}),
        ('account create', {'name': 'Checking', 'type': 'bank'}, {}),
        ('attachment get', {'attachment': '01ARZ3NDEKTSV4RRFFQ69G5FAV'}, {}),
        ('attachment add', {'record_type': 'customer', 'record_id': '01ARZ3NDEKTSV4RRFFQ69G5FAV', 'original_filename': 'receipt.pdf', 'media_type': 'application/pdf'}, {'input_file': str(source)}),
    ]
    async def witness():
        binary = os.environ.get('BOOKFLOW_MCP_TEST_BINARY', str(Path(sys.executable).with_name('bookflow')))
        params = StdioServerParameters(command=binary, args=['mcp', '--url', live, '--input-dir', str(inbox), '--output-dir', str(outbox)],
            env={'BOOKFLOW_TOKEN': hosted.secret, 'BOOKFLOW_COMPANY': hosted.company_id, 'BOOKFLOW_DATA_ROOT': str(tmp_path / 'absent')}, cwd=str(tmp_path))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.discover()
                for index, (command, raw, transport) in enumerate(cases):
                    destination = outbox / (str(index) + '.json')
                    reply = await session.call_tool('bookflow_run', {'command': command, 'input': raw,
                        'transport': {**transport, 'result_file': str(destination)}})
                    assert reply.is_error, reply
                    assert reply.structured_content['delivery'] == 'complete_json_file', reply
                    assert reply.meta['bookflow_transport']['response_kind'] == 'verified_command_completion'
                    document = json.loads(destination.read_bytes())
                    assert document['code'] == ('E_NAME_TAKEN' if command == 'account create' else 'E_RECORD_NOT_FOUND' if index >= 5 else 'E_VALIDATION')
                    assert set(document) == {'code', 'message', 'details'}
                    assert 'operation_ref' not in document['details'] and 'outcome' not in document['details']
                    if not command.startswith('attachment '):
                        original = hosted.call(command.replace(' ', '.'), raw, company=hosted.company_id)
                        assert original.json() == document
                    again = await session.call_tool('bookflow_run', {'input_ref': reply.structured_content['operation_ref'], 'action': 'execute'})
                    assert again.is_error and again.structured_content == document
    anyio.run(witness)
    assert hosted.ok('audit.list', {'limit': 200}, company=hosted.company_id) == before
    assert not hosted.handle.host._transfers
    assert not hosted.handle.host._mcp_runtime.intents.active
    assert {p.name for p in outbox.iterdir()} == {str(i) + '.json' for i in range(len(cases))}


def test_installed_retained_recovery_denial_preserves_reference_and_uncertainty(hosted, live, tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from tests.test_publication_review_behaviors import member, downgrade
    actor, headers = member(hosted)
    secret = headers['Authorization'].removeprefix('Bearer ')
    outbox = tmp_path / 'outbox'; outbox.mkdir(mode=0o700)
    async def witness():
        params = StdioServerParameters(command=os.environ['BOOKFLOW_MCP_TEST_BINARY'],
            args=['mcp', '--url', live, '--output-dir', str(outbox)],
            env={'BOOKFLOW_TOKEN': secret, 'BOOKFLOW_COMPANY': hosted.company_id,
                 'BOOKFLOW_DATA_ROOT': str(tmp_path / 'absent')}, cwd=str(tmp_path))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.discover()
                first = await session.call_tool('bookflow_run', {'command': 'company update', 'input': {'fax': 'Retained before downgrade'},
                    'reason': 'Retained recovery witness', 'transport': {'result_file': str(outbox / 'receipt.json')}})
                assert not first.is_error, first
                reference = first.meta['bookflow_transport']['operation_ref']
                downgrade(hosted, actor, 'standard')
                for alias in ('operation_ref', 'input_ref'):
                    for action in ('execute', 'status', 'inspect'):
                        reply = await session.call_tool('bookflow_run', {alias: reference, 'action': action})
                        assert reply.is_error and reply.structured_content['code'] == 'E_PERMISSION', reply
                        detail = reply.structured_content['details']
                        assert detail['operation_ref'] == reference and detail['outcome'] == 'unknown'
                        assert detail['stage'] == 'publication'
                # After cache loss the reference still cannot become a fresh write.
                hosted.handle.host._mcp_runtime.intents.completed.clear()
                lost = await session.call_tool('bookflow_run', {'input_ref': reference, 'action': 'execute'})
                assert lost.is_error and lost.structured_content['code'] == 'E_IO'
                assert lost.structured_content['details']['operation_ref'] == reference
                assert lost.structured_content['details']['outcome'] == 'unknown'
    anyio.run(witness)
    assert hosted.info()['info']['fax'] == 'Retained before downgrade'
    events = hosted.ok('audit.list', {'command': 'company update'}, company=hosted.company_id)['items']
    assert len([e for e in events if e['reason'] == 'Retained recovery witness']) == 1


def test_installed_upload_limit_rejection_delivers_error_file_and_cleans_stage(hosted, live, tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from tests.test_attachment_http import target
    record_id = target(hosted)
    hosted.ok('company.update', {'attachment_max_bytes': 5}, company=hosted.company_id)
    before = hosted.ok('audit.list', {'limit': 200}, company=hosted.company_id)
    inbox, outbox = tmp_path / 'in', tmp_path / 'out'
    inbox.mkdir(mode=0o700); outbox.mkdir(mode=0o700)
    source = inbox / 'receipt.pdf'; source.write_bytes(b'%PDF-1.4\nToo large for this company')
    async def witness():
        params = StdioServerParameters(command=os.environ['BOOKFLOW_MCP_TEST_BINARY'],
            args=['mcp', '--url', live, '--input-dir', str(inbox), '--output-dir', str(outbox)],
            env={'BOOKFLOW_TOKEN': hosted.secret, 'BOOKFLOW_COMPANY': hosted.company_id,
                 'BOOKFLOW_DATA_ROOT': str(tmp_path / 'absent')}, cwd=str(tmp_path))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.discover()
                reply = await session.call_tool('bookflow_run', {'command': 'attachment add', 'input': {
                    'record_type': 'customer', 'record_id': record_id, 'original_filename': 'receipt.pdf', 'media_type': 'application/pdf'},
                    'transport': {'input_file': str(source), 'result_file': str(outbox / 'error.json')}})
                assert reply.is_error and reply.structured_content['delivery'] == 'complete_json_file', reply
                error = json.loads((outbox / 'error.json').read_bytes())
                assert error['code'] == 'E_VALUE_RANGE' and error['details'] == {'field': 'body', 'limit': 5}
    anyio.run(witness)
    assert hosted.ok('audit.list', {'limit': 200}, company=hosted.company_id) == before
    assert hosted.ok('attachment.list', {'record_type': 'customer', 'record_id': record_id}, company=hosted.company_id)['count'] == 0
    assert not hosted.handle.host._transfers and not hosted.handle.host._mcp_runtime.intents.active
    assert [p.name for p in outbox.iterdir()] == ['error.json']
