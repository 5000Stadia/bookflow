"""The shared POS layout through Python, CLI, HTTP and MCP."""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

COMMANDS = frozenset(f'pos-box {verb}' for verb in ('list', 'seed', 'create', 'update', 'move', 'remove', 'view'))


@pytest.mark.timeout(900)
def test_the_same_layout_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in matrix.documents:
                async def call(command, body=None, **kwargs):
                    return await matrix.call(surface, command, body or {}, **kwargs)

                off = await call('pos-box list', rejected=True)
                info = await call('company show')
                await call('company update', {'pos_enabled': True, 'expected_version': info['info_version']})
                layout = await call('pos-box list')
                layout = await call('pos-box seed', {'mode': 'empty', 'expected_version': layout['version']})
                layout = await call('pos-box create', {'kind': 'group', 'label': 'Drinks',
                                                       'expected_version': layout['version']})
                group = next(box['id'] for box in layout['items'] if box['label'] == 'Drinks')
                layout = await call('pos-box create', {'kind': 'item', 'item': 'Pipe Sealant', 'parent': group,
                                                       'expected_version': layout['version']})
                box = next(box['id'] for box in layout['items'] if box['kind'] == 'item')
                layout = await call('pos-box update', {'pos_box': group, 'label': 'Supplies', 'colour': '#448866',
                                                       'expected_version': layout['version']})
                layout = await call('pos-box view', {'pos_box': group, 'style': 'list',
                                                     'expected_version': layout['version']})
                stale = await call('pos-box move', {'pos_box': box, 'position': 0,
                                                    'expected_version': layout['version'] - 1}, rejected=True)
                layout = await call('pos-box move', {'pos_box': box, 'position': 0,
                                                    'expected_version': layout['version']})
                layout = await call('pos-box remove', {'pos_box': box, 'expected_version': layout['version']})
                shown = await call('pos-box list')
                item = await call('item show', {'item': 'Pipe Sealant'})
                results[surface] = (off.get('code') or off.get('error', {}).get('code'),
                                    stale.get('code') or stale.get('error', {}).get('code'),
                                    [(b['kind'], b['label'], b['colour'], b['view_style']) for b in shown['items']],
                                    shown['initialized'], item['name'])
            first = results['python']
            assert first == ('E_FEATURE_DISABLED', 'E_VERSION_CONFLICT',
                             [('group', 'Supplies', '#448866', 'list')], True, 'Pipe Sealant')
            assert all(result == first for result in results.values()), results
        finally:
            await matrix.close()

    anyio.run(witness)

PICTURE_COMMANDS = frozenset(('pos-box picture set', 'pos-box picture get'))


@pytest.mark.timeout(900)
def test_the_same_group_picture_through_python_cli_http_and_mcp(root, tmp_path):
    import io
    import json
    import bookflow
    from bookflow.core.transfer_protocol import encode_input
    from tests.conftest import Cli
    from tests.test_item_pictures import png

    data = png(2, 2)
    inbox = tmp_path / 'inbox'
    inbox.mkdir(mode=0o700)
    source = inbox / 'group.png'
    source.write_bytes(data)
    outbox = tmp_path / 'outbox'
    outbox.mkdir(mode=0o700)

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path, mcp_args=['--input-dir', str(inbox), '--output-dir', str(outbox)])
            results = {}
            for surface in matrix.documents:
                info = await matrix.call(surface, 'company show', {})
                await matrix.call(surface, 'company update', {'pos_enabled': True, 'expected_version': info['info_version']})
                layout = await matrix.call(surface, 'pos-box list', {})
                layout = await matrix.call(surface, 'pos-box create', {'kind': 'group', 'label': 'Photo group',
                                                                      'expected_version': layout['version']})
                group = next(b['id'] for b in layout['items'] if b['label'] == 'Photo group')
                raw = {'pos_box': group, 'expected_version': layout['version'], 'original_filename': 'group.png',
                       'media_type': 'image/png'}
                company = matrix.company
                if surface == 'python':
                    c = bookflow.connect(data_root=str(matrix.roots[surface]))
                    done = c.run('pos-box picture set', raw, company=company, reason='Layout parity',
                                 input_stream=io.BytesIO(data))
                elif surface == 'cli':
                    reply = Cli(matrix.roots[surface]).run('--company', company, '--reason', 'Layout parity',
                        'pos-box', 'picture', 'set', group, str(source), '--expected-version', str(layout['version']),
                        '--media-type', 'image/png', '--json')
                    done = json.loads(reply.stdout)
                elif surface == 'http':
                    host = matrix.hosts[surface]
                    reply = host.api.post(f'/companies/{company}/transfers/pos-box.picture.set', headers={
                        **host.bearer, 'X-Bookflow-Input': encode_input(raw), 'X-Bookflow-Reason': 'Layout parity',
                        'Content-Type': 'application/octet-stream'}, content=data)
                    assert reply.status_code == 200, reply.text
                    done = reply.json()
                else:
                    reply = await matrix.mcp.call_tool('bookflow_run', {
                        'command': 'pos-box picture set', 'input': raw, 'company': company, 'reason': 'Layout parity',
                        'transport': {'input_file': str(source)}})
                    assert not reply.is_error, reply.structured_content
                    done = reply.structured_content
                picture = next(b['picture'] for b in done['items'] if b['id'] == group)
                attachment = picture['attachment_id']
                raw = {'attachment': attachment}
                if surface == 'python':
                    stream = io.BytesIO()
                    c.run('pos-box picture get', raw, company=company, output_stream=stream)
                    fetched = stream.getvalue()
                elif surface == 'cli':
                    target = tmp_path / 'cli.png'
                    Cli(matrix.roots[surface]).run('--company', company, 'pos-box', 'picture', 'get', attachment,
                                                   '--out', str(target), '--json')
                    fetched = target.read_bytes()
                elif surface == 'http':
                    reply = host.api.post(f'/companies/{company}/transfers/pos-box.picture.get', headers={
                        **host.bearer, 'X-Bookflow-Input': encode_input(raw)})
                    assert reply.status_code == 200, reply.text
                    fetched = reply.content
                else:
                    target = outbox / 'mcp.png'
                    reply = await matrix.mcp.call_tool('bookflow_run', {
                        'command': 'pos-box picture get', 'input': raw, 'company': company,
                        'transport': {'output_file': str(target)}})
                    assert not reply.is_error, reply.structured_content
                    fetched = target.read_bytes()
                assert fetched == data
                results[surface] = (picture['sha256'], picture['media_type'], done['version'])
            assert all(result == results['python'] for result in results.values()), results
        finally:
            await matrix.close()

    anyio.run(witness)
