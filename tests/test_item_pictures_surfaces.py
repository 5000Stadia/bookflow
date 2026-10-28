"""R114 increment 1: the item picture through Python, the CLI, HTTP and MCP.

Each surface gets an identical demo copy. It uploads a PNG as Pipe Sealant's picture (`item picture set`, a binary
transfer), is refused a HEIC photo with what to do, chooses the attached photo with a thumbnail (`item picture use`,
which unlinks the replaced PNG), downloads the thumbnail (`item picture get`, a binary download), and removes the
picture (`item picture clear`). Every surface ends with the same picture facts, bytes and refusal.
"""
import io
import json

import anyio
import pytest

import bookflow
from bookflow.core.transfer_protocol import encode_input
from tests.conftest import Cli
from tests.mcp_matrix_support import Matrix
from tests.test_item_pictures import HEIC, JPEG, png

COMMANDS = frozenset(('item picture set', 'item picture use', 'item picture get', 'item picture clear'))
COMPANY = 'Demo Plumbing Co'


@pytest.mark.timeout(900)
def test_the_same_item_picture_through_python_cli_http_and_mcp(root, client, tmp_path):
    company = client.company.list()['items'][0]['company_id']
    item = client.item.show(item='Pipe Sealant', company=company)['id']
    with io.BytesIO(JPEG) as body:
        old = client.run('attachment add', {'record_type': 'item', 'record_id': item, 'original_filename': 'old.jpg',
                                            'media_type': 'image/jpeg'}, company=company, input_stream=body)['attachment']
    with io.BytesIO(png(2, 2)) as body:
        thumb = client.run('attachment add', {'record_type': 'item', 'record_id': item, 'original_filename': 't.png',
                                              'media_type': 'image/png'}, company=company, input_stream=body)['attachment']
    inbox = tmp_path / 'inbox'
    inbox.mkdir(mode=0o700)
    files = {'photo.png': png(), 'photo.heic': HEIC}
    for name, data in files.items():
        (inbox / name).write_bytes(data)
    outbox = tmp_path / 'outbox'
    outbox.mkdir(mode=0o700)

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path, mcp_args=['--input-dir', str(inbox), '--output-dir', str(outbox)])
            results = {}
            for surface in matrix.documents:
                async def upload(name, rejected=False):
                    raw = {'item': item, 'original_filename': name, 'media_type': 'image/png'}
                    data = files[name]
                    if surface == 'python':
                        try:
                            document, error = bookflow.connect(data_root=str(matrix.roots[surface])).run(
                                'item picture set', raw, company=company, reason='Registry parity',
                                input_stream=io.BytesIO(data)), False
                        except bookflow.BookflowError as exc:
                            document, error = exc.to_dict(), True
                    elif surface == 'cli':
                        done = Cli(matrix.roots[surface]).run(
                            '--company', company, '--reason', 'Registry parity', 'item', 'picture', 'set', item,
                            str(inbox / name), '--media-type', 'image/png', '--json', expect=None)
                        error = done.returncode != 0
                        document = json.loads(done.stderr.strip().splitlines()[-1] if error else done.stdout)
                    elif surface == 'http':
                        hosted = matrix.hosts[surface]
                        reply = hosted.api.post(f'/companies/{company}/transfers/item.picture.set', headers={
                            **hosted.bearer, 'X-Bookflow-Input': encode_input(raw), 'X-Bookflow-Reason': 'Registry parity',
                            'Content-Type': 'application/octet-stream'}, content=data)
                        error, document = reply.status_code >= 400, reply.json()
                    else:
                        reply = await matrix.mcp.call_tool('bookflow_run', {
                            'command': 'item picture set', 'input': raw, 'company': company, 'reason': 'Registry parity',
                            'transport': {'input_file': str(inbox / name)}})
                        error, document = reply.is_error, reply.structured_content
                    assert bool(error) == rejected, (surface, document)
                    return document

                async def download(attachment):
                    raw = {'attachment': attachment}
                    if surface == 'python':
                        stream = io.BytesIO()
                        bookflow.connect(data_root=str(matrix.roots[surface])).run(
                            'item picture get', raw, company=company, output_stream=stream)
                        return stream.getvalue()
                    if surface == 'cli':
                        target = tmp_path / f'cli-{attachment}.png'
                        Cli(matrix.roots[surface]).run('--company', company, 'item', 'picture', 'get', attachment,
                                                       '--out', str(target), '--json')
                        return target.read_bytes()
                    if surface == 'http':
                        hosted = matrix.hosts[surface]
                        reply = hosted.api.post(f'/companies/{company}/transfers/item.picture.get', headers={
                            **hosted.bearer, 'X-Bookflow-Input': encode_input(raw)})
                        assert reply.status_code == 200, reply.text
                        return reply.content
                    target = outbox / f'mcp-{attachment}.png'
                    reply = await matrix.mcp.call_tool('bookflow_run', {
                        'command': 'item picture get', 'input': raw, 'company': company,
                        'transport': {'output_file': str(target)}})
                    assert not reply.is_error, reply.structured_content
                    return target.read_bytes()

                call = matrix.call
                done = await upload('photo.png')
                refused = await upload('photo.heic', rejected=True)
                used = await call(surface, 'item picture use', {'item': item, 'attachment': old['id'],
                                                                'thumbnail_attachment': thumb['id']})
                fetched = await download(thumb['id'])
                cleared = await call(surface, 'item picture clear', {'item': item})
                shown = await call(surface, 'item show', {'item': item})
                results[surface] = (
                    (done['picture']['sha256'], done['picture']['media_type'], done['picture']['chosen']),
                    (refused.get('code') or refused.get('error', {}).get('code'), 'Most Compatible' in json.dumps(refused)),
                    (used['picture']['attachment_id'], used['thumbnail']['attachment_id'],
                     used['unlinked'] == [done['picture']['attachment_id']]),
                    fetched,
                    sorted(cleared['unlinked']),
                    shown['picture'],
                )
            first = results['python']
            assert first[1] == ('E_VALIDATION', True) and first[2] == (old['id'], thumb['id'], True)
            assert first[3] == png(2, 2) and first[4] == sorted((old['id'], thumb['id'])) and first[5] is None
            assert all(value == first for value in results.values()), results
        finally:
            await matrix.close()

    anyio.run(witness)
