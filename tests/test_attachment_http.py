"""Raw HTTP file transfers, authority boundaries and independent binary resources."""

import asyncio
import hashlib
import io
from pathlib import Path

import pytest
from starlette.requests import Request

from bookflow import BookflowError
from bookflow.core.transfer_protocol import encode_input
from tests.test_row3_host import hosted, WB  # noqa: F401

BODY = b'%PDF-1.4\n' + bytes(range(256)) * 1024 + b'\n%%EOF\n'


def target(hosted):
    return hosted.ok('customer.create', {'name': 'HTTP attachment target'}, company=hosted.company_id)['id']


def headers(hosted, raw, **extra):
    return {**hosted.bearer, 'X-Bookflow-Input': encode_input(raw),
            'Content-Type': 'application/octet-stream', **extra}


def upload(hosted, record_id, body=BODY, **extra):
    raw = {'record_type': 'customer', 'record_id': record_id, 'original_filename': 'Receipt é.pdf',
           'media_type': 'application/pdf', 'caption': 'Service document'}
    return hosted.api.post(f'/companies/{hosted.company_id}/transfers/attachment.add',
                           headers=headers(hosted, raw, **extra), content=body)


def test_http_upload_download_exact_and_activity(hosted):
    rid = target(hosted)
    response = upload(hosted, rid)
    assert response.status_code == 200, response.text
    added = response.json()
    attachment = added['attachment']
    assert added['link']['linked_by_name']
    assert attachment['sha256'] == hashlib.sha256(BODY).hexdigest()
    assert attachment['size_bytes'] == len(BODY)
    result = hosted.api.post(f'/companies/{hosted.company_id}/transfers/attachment.get',
                            headers=headers(hosted, {'attachment': attachment['id']}))
    assert result.status_code == 200 and result.content == BODY
    assert result.headers['content-length'] == str(len(BODY))
    assert result.headers['x-bookflow-sha256'] == attachment['sha256']
    assert result.headers['cache-control'] == 'no-store'
    assert result.headers['x-content-type-options'] == 'nosniff'
    assert "filename*=UTF-8''Receipt%20%C3%A9.pdf" in result.headers['content-disposition']
    feed = hosted.ok('activity', {'record_type': 'customer', 'record_id': rid}, company=hosted.company_id)
    assert any(item['kind'] == 'attachment' and item['attachment_id'] == attachment['id'] for item in feed['items'])
    assert not hosted.handle.host._transfers


def test_http_body_and_metadata_boundaries(hosted):
    rid = target(hosted)
    hosted.ok('company.update', {'attachment_max_bytes': 3}, company=hosted.company_id)
    over = upload(hosted, rid, b'four')
    assert over.status_code == 422 and over.json()['code'] == 'E_VALUE_RANGE'
    empty = upload(hosted, rid, b'')
    assert empty.status_code == 200, empty.text
    wrong = hosted.api.post(f'/companies/{hosted.company_id}/transfers/attachment.add',
        headers=headers(hosted, {'record_type': 'customer', 'record_id': rid, 'original_filename': '../bad'}), content=b'a')
    assert wrong.status_code == 422 and wrong.json()['code'] == 'E_VALIDATION'
    assert not hosted.handle.host._transfers


def test_http_dry_run_no_stage_or_metadata(hosted):
    rid = target(hosted)
    store = Path(hosted.info()['path']) / 'attachments'
    before = {str(p.relative_to(store)): p.read_bytes() for p in store.rglob('*') if p.is_file()}
    response = hosted.api.post(f'/companies/{hosted.company_id}/transfers/attachment.add?dry_run=true',
        headers=headers(hosted, {'record_type': 'customer', 'record_id': rid, 'original_filename': 'dry.pdf'}), content=BODY)
    assert response.status_code == 200 and response.json()['dry_run']
    assert {str(p.relative_to(store)): p.read_bytes() for p in store.rglob('*') if p.is_file()} == before
    assert hosted.ok('attachment.list', {'record_type': 'customer', 'record_id': rid}, company=hosted.company_id)['count'] == 0
    assert not hosted.handle.host._transfers


def test_http_retries_bind_actual_bytes(hosted):
    rid = target(hosted)
    first = upload(hosted, rid, **{'Idempotency-Key': 'receipt'})
    again = upload(hosted, rid, **{'Idempotency-Key': 'receipt'})
    mismatch = upload(hosted, rid, b'different', **{'Idempotency-Key': 'receipt'})
    assert first.status_code == again.status_code == 200
    assert again.json()['idempotent_replay']
    assert mismatch.status_code == 409 and mismatch.json()['code'] == 'E_IDEMPOTENCY_MISMATCH'


def test_json_route_requires_binary_resource(hosted):
    rid = target(hosted)
    result = hosted.call('attachment.add', {'record_type': 'customer', 'record_id': rid,
                                         'original_filename': 'missing.pdf'}, company=hosted.company_id)
    assert result.status_code == 400 and result.json()['code'] == 'E_USAGE'


def endpoint(hosted):
    return next(r.endpoint for r in hosted.handle.app.routes if getattr(r, 'path', '') == '/companies/{company_id}/transfers/{route}')


def request_for(hosted, raw, receive, *, authorized=True):
    hdr = headers(hosted, raw) if authorized else {'X-Bookflow-Input': encode_input(raw)}
    return Request({'type': 'http', 'method': 'POST', 'scheme': 'http', 'path': '/', 'query_string': b'',
                    'headers': [(k.lower().encode(), v.encode()) for k, v in hdr.items()],
                    'server': ('localhost', 80), 'client': ('localhost', 123)}, receive)


@pytest.mark.parametrize('case', ['unauthenticated', 'unknown_target', 'malformed_header'])
def test_rejection_never_reads_upload(hosted, case):
    reads = []
    async def receive():
        reads.append(True)
        raise AssertionError('unauthorized upload consumed')
    raw = {'record_type': 'customer', 'record_id': 'invalid', 'original_filename': 'receipt.pdf'}
    req = request_for(hosted, raw, receive, authorized=case != 'unauthenticated')
    if case == 'malformed_header':
        req.scope['headers'] = [(k, b'bad==' if k == b'x-bookflow-input' else v) for k, v in req.scope['headers']]
    with pytest.raises(BookflowError):
        asyncio.run(endpoint(hosted)(hosted.company_id, 'attachment.add', req))
    assert reads == []
    assert not hosted.handle.host._transfers


def test_disconnect_removes_own_stage(hosted):
    rid = target(hosted)
    messages = iter([{'type': 'http.request', 'body': b'partial', 'more_body': True}, {'type': 'http.disconnect'}])
    async def receive():
        return next(messages)
    request = request_for(hosted, {'record_type': 'customer', 'record_id': rid, 'original_filename': 'partial.pdf'}, receive)
    with pytest.raises(BookflowError) as error:
        asyncio.run(endpoint(hosted)(hosted.company_id, 'attachment.add', request))
    assert error.value.code == 'E_IO'
    assert not hosted.handle.host._transfers
    assert not list((Path(hosted.info()['path']) / 'attachments').glob('.attachment-*.tmp'))


def test_corrupt_body_fails_before_success_headers(hosted):
    rid = target(hosted)
    attachment = upload(hosted, rid).json()['attachment']
    digest = attachment['sha256']
    path = Path(hosted.info()['path']) / 'attachments' / digest[:2] / digest
    path.write_bytes(b'corrupt')
    response = hosted.api.post(f'/companies/{hosted.company_id}/transfers/attachment.get',
                              headers=headers(hosted, {'attachment': attachment['id']}))
    assert response.status_code == 400 and response.json()['code'] == 'E_IO'
    assert not hosted.handle.host._transfers


def test_openapi_projects_external_binary_contract(hosted):
    from bookflow.adapters.http.app import build_openapi
    paths = build_openapi('test')['paths']
    add = paths['/companies/{company_id}/transfers/attachment.add']['post']
    get = paths['/companies/{company_id}/transfers/attachment.get']['post']
    assert add['x-bookflow-transfer'] == 'input' and get['x-bookflow-transfer'] == 'output'
    assert 'application/octet-stream' in add['requestBody']['content']
    header = next(p for p in add['parameters'] if p['name'] == 'X-Bookflow-Input')
    assert 'original_filename' in header['x-bookflow-input-schema']['properties']
    assert '/companies/{company_id}/commands/attachment.add' not in paths


@pytest.mark.parametrize("phase", ["prepare", "write"])
def test_cancelled_http_preparation_keeps_owner_until_worker_finishes(hosted, monkeypatch, phase):
    import threading
    from bookflow.adapters.http import transfers as adapter
    actual = adapter.HostedTransfer
    started, release, completed = threading.Event(), threading.Event(), threading.Event()
    created = []
    def delayed(*args, **kwargs):
        transfer = actual(*args, **kwargs)
        created.append(transfer)
        if phase == "prepare":
            started.set()
            assert release.wait(5)
            completed.set()
        return transfer
    monkeypatch.setattr(adapter, 'HostedTransfer', delayed)
    if phase == 'write':
        from bookflow.core.transfers import InputBody
        actual_write = InputBody.write
        def delayed_write(self, chunk):
            actual_write(self, chunk)
            started.set()
            assert release.wait(5)
            completed.set()
        monkeypatch.setattr(InputBody, 'write', delayed_write)
    rid = target(hosted)
    async def receive():
        return {'type': 'http.request', 'body': b'partial', 'more_body': False}
    req = request_for(hosted, {'record_type': 'customer', 'record_id': rid, 'original_filename': 'cancelled.pdf'}, receive)
    async def exercise():
        task = asyncio.create_task(endpoint(hosted)(hosted.company_id, 'attachment.add', req))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(hosted.handle.host._transfers) == 1
        assert list((Path(hosted.info()["path"]) / "attachments").glob(".attachment-*.tmp"))
        release.set()
        assert await asyncio.to_thread(completed.wait, 5)
        for _ in range(100):
            if not hosted.handle.host._transfers:
                break
            await asyncio.sleep(.01)
        assert not hosted.handle.host._transfers
    try:
        asyncio.run(exercise())
    finally:
        release.set()
        for transfer in created:
            transfer.close()
