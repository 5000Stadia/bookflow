"""Activated catalogs must use admitted credentials at binary preparation boundaries."""
import hashlib
from pathlib import Path
import time

import anyio
import pytest

from bookflow.core.transfer_protocol import decode_input, encode_input
from tests import provenance
from tests.test_attachment_http import BODY, target, upload
from tests.test_row3_host import hosted, live, PASSWORD, OUTSIDER_PASSWORD, WB  # noqa: F401


def activate(hosted):
    state = hosted.ok('permission.show')
    hosted.ok('permission.activate', {'expected_generation': state['generation'],
                                    'expected_catalog_sha256': state['catalog_sha256']})


def clean(hosted):
    host = hosted.handle.host
    # A network client can finish reading Content-Length before the server's
    # response-finally worker closes its lease. Require bounded quiescence.
    deadline = time.monotonic() + 5
    while (host._transfers or host._readers_attached) and time.monotonic() < deadline:
        time.sleep(.01)
    assert not host._transfers
    assert host._readers_attached == 0
    assert not list((Path(hosted.info()['path']) / 'attachments').glob('.attachment-*.tmp'))


@pytest.fixture
def cookie_api(live):
    import httpx
    with httpx.Client(base_url=live, timeout=30) as api:
        yield api


@pytest.mark.parametrize('direction', ['input', 'output'])
def test_activated_http_cookie_binary(hosted, cookie_api, direction):
    record = target(hosted)
    # Download remains an independent witness even when activated uploads fail.
    seeded = upload(hosted, record).json()['attachment'] if direction == 'output' else None
    activate(hosted)
    response = cookie_api.post('/login', json={'username': hosted.login, 'password': PASSWORD})
    assert response.status_code == 200 and response.cookies.get('bookflow_session')
    if direction == 'input':
        raw = {'record_type': 'customer', 'record_id': record, 'original_filename': 'Receipt é.pdf',
               'media_type': 'application/pdf', 'caption': 'Activated receipt'}
        route, body = 'attachment.add', BODY
    else:
        raw, route, body = {'attachment': seeded['id']}, 'attachment.get', b''
    response = cookie_api.post(f'/companies/{hosted.company_id}/transfers/{route}',
        headers={**WB, 'X-Bookflow-Input': encode_input(raw), 'Content-Type': 'application/octet-stream'},
        content=body)
    assert response.status_code == 200, response.text
    if direction == 'input':
        metadata = response.json()['attachment']
        assert metadata['created_via'] == 'http'
        digest = metadata['sha256']
        assert (Path(hosted.info()['path']) / 'attachments' / digest[:2] / digest).read_bytes() == BODY
    else:
        assert response.content == BODY
        metadata = decode_input(response.headers['x-bookflow-output'])
        assert metadata == seeded
        assert response.headers['content-length'] == str(len(BODY))
        assert response.headers['x-bookflow-sha256'] == hashlib.sha256(BODY).hexdigest()
        assert "filename*=UTF-8''Receipt%20%C3%A9.pdf" in response.headers['content-disposition']
    assert metadata['sha256'] == hashlib.sha256(BODY).hexdigest()
    assert metadata['size_bytes'] == len(BODY)
    assert metadata['original_filename'] == 'Receipt é.pdf'
    assert metadata['media_type'] == 'application/pdf'
    clean(hosted)


@pytest.mark.parametrize('direction', ['input', 'output'])
@pytest.mark.timeout(180)
def test_activated_actual_mcp_binary(hosted, live, tmp_path, monkeypatch, direction):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from bookflow.adapters.http.published_transfer import PublishedTransfer
    record = target(hosted)
    seeded = upload(hosted, record).json()['attachment'] if direction == 'output' else None
    activate(hosted)
    observer = hosted.ok('token.issue', {'label': 'activation observer'})['secret']
    inbox, outbox = tmp_path / 'inbox', tmp_path / 'outbox'
    inbox.mkdir(); outbox.mkdir()
    source = inbox / 'Receipt é.pdf'
    source.write_bytes(BODY)
    executions, reopens = [], []
    execute = PublishedTransfer._execute
    from bookflow.adapters.mcp.runtime import Runtime
    runtime = Runtime.for_host(hosted.handle.host)
    reopen = runtime.reopen_output

    def counted_execute(self, *args, **kwargs):
        executions.append(self.cmd.name)
        return execute(self, *args, **kwargs)

    def counted_reopen(*args, **kwargs):
        reopens.append(True)
        return reopen(*args, **kwargs)

    monkeypatch.setattr(PublishedTransfer, '_execute', counted_execute)
    monkeypatch.setattr(runtime, 'reopen_output', counted_reopen)

    async def witness():
        params = StdioServerParameters(command=provenance.launcher(),
            args=['mcp', '--url', live, '--input-dir', str(inbox), '--output-dir', str(outbox)],
            env=provenance.child_env(BOOKFLOW_TOKEN=hosted.secret, BOOKFLOW_COMPANY=hosted.company_id,
                                    BOOKFLOW_DATA_ROOT=str(tmp_path / 'absent')), cwd=str(tmp_path))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.discover()
                destination = outbox / 'download.pdf'
                if direction == 'input':
                    arguments = {'command': 'attachment add', 'input': {'record_type': 'customer',
                        'record_id': record, 'original_filename': source.name, 'media_type': 'application/pdf'},
                        'transport': {'input_file': str(source)}}
                else:
                    arguments = {'command': 'attachment get', 'input': {'attachment': seeded['id']},
                                 'transport': {'output_file': str(destination)}}
                reply = await session.call_tool('bookflow_run', arguments)
                assert not reply.is_error, reply.structured_content
                metadata = reply.structured_content['attachment'] if direction == 'input' else reply.structured_content
                assert metadata['sha256'] == hashlib.sha256(BODY).hexdigest()
                assert metadata['size_bytes'] == len(BODY)
                assert metadata['original_filename'] == source.name
                assert metadata['media_type'] == 'application/pdf'
                if direction == 'input':
                    assert metadata['created_via'] == 'mcp'
                    digest = metadata['sha256']
                    assert (Path(hosted.info()['path']) / 'attachments' / digest[:2] / digest).read_bytes() == BODY
                else:
                    assert metadata == seeded
                    assert destination.read_bytes() == BODY
                    assert reply.meta['bookflow_transport']['output_file'] == str(destination)
                    reference = reply.meta['bookflow_transport']['operation_ref']
                    recovered_path = outbox / 'recovered.pdf'
                    recovered = await session.call_tool('bookflow_run', {'input_ref': reference,
                        'action': 'execute', 'output_file': str(recovered_path)})
                    assert not recovered.is_error, recovered
                    assert recovered.structured_content == metadata
                    assert recovered_path.read_bytes() == BODY
                    assert reopens == [True]
                    assert executions == ['attachment get']
                    revoked = hosted.call('token.revoke', {'token': hosted.token},
                                          headers={'Authorization': 'Bearer ' + observer})
                    assert revoked.status_code == 200, revoked.text
                    denied_path = outbox / 'denied.pdf'
                    denied = await session.call_tool('bookflow_run', {'input_ref': reference,
                        'action': 'execute', 'output_file': str(denied_path)})
                    assert denied.is_error, denied
                    assert not denied_path.exists()
                    assert executions == ['attachment get']
                assert not list(outbox.glob('.bookflow-mcp-*'))
    anyio.run(witness)
    # Inspector remains valid after revoking the MCP credential.
    hosted.bearer = {'Authorization': 'Bearer ' + observer}
    clean(hosted)
    assert not runtime.intents.active


@pytest.mark.parametrize('direction', ['input', 'output'])
def test_activated_cookie_outsider_cannot_transfer(hosted, cookie_api, direction):
    record = target(hosted)
    attachment = upload(hosted, record).json()['attachment']['id']
    activate(hosted)
    assert cookie_api.post('/login', json={'username': 'outsider', 'password': OUTSIDER_PASSWORD}).status_code == 200
    raw = ({'record_type': 'customer', 'record_id': record, 'original_filename': 'denied.pdf'}
           if direction == 'input' else {'attachment': attachment})
    route = 'attachment.add' if direction == 'input' else 'attachment.get'
    response = cookie_api.post(f'/companies/{hosted.company_id}/transfers/{route}',
        headers={**WB, 'X-Bookflow-Input': encode_input(raw), 'Content-Type': 'application/octet-stream'},
        content=BODY if direction == 'input' else b'')
    assert response.status_code == 404, response.text
    assert response.json()['code'] == 'E_COMPANY_NOT_FOUND'
    clean(hosted)


@pytest.mark.parametrize('direction', ['input', 'output'])
def test_activated_http_revocation_before_headers(hosted, monkeypatch, direction):
    from tests.test_transfer_publication import test_transfer_revocation_before_headers_does_not_publish_metadata
    activate(hosted)
    test_transfer_revocation_before_headers_does_not_publish_metadata(hosted, monkeypatch, direction)


@pytest.mark.timeout(180)
def test_activated_mcp_revocation_mid_download(hosted, live, tmp_path, monkeypatch):
    from tests.test_mcp_delivery_faults import test_real_mcp_revocation_mid_download_has_unknown_outcome_and_no_partial_file
    activate(hosted)
    test_real_mcp_revocation_mid_download_has_unknown_outcome_and_no_partial_file(hosted, live, tmp_path, monkeypatch)
    assert hosted.handle.host._readers_attached == 0
