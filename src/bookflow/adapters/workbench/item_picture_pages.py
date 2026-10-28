"""The item picture in the browser (R114 increment 1, design/point-of-sale.md §6).

The item page shows the picture beside the item's name with Take photo, Choose picture and Remove. The browser scales
a photo to 1600 px on its long edge and makes a 320 px thumbnail before sending both (static/item_picture.js); the
form posts through htmx. The thumbnail goes up as an ordinary file on the item (`attachment add`), then the picture
through `item picture set`, so both are the item's files. A post over the size limits is refused before it is read,
and its work runs in the thread pool. Pictures are served at a content-addressed address through `item picture get`
(a reader of items sees them), streamed, only when the bytes are a JPEG, PNG or WebP, with that type, never sniffed
as anything else, and cached for good. Lists, the pick and pack screens and the fulfillment worksheet read thumbnails
with `thumbs`; an item whose picture has no thumbnail shows a placeholder there, never the full picture.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from bookflow.adapters.http.transfers import LeasedDownload, _TransferOwner
from bookflow.core.errors import BookflowError

_ID = re.compile(r'[0-9A-Z]{26}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
CACHE = 'private, max-age=31536000, immutable'
# A list's mark for an item whose picture has no thumbnail: the full picture is never loaded into a list.
PLACEHOLDER = 'data:image/svg+xml,' + quote(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="24" height="24" fill="#e8e8e8"/>'
    '<path d="M5 17l4-5 3 4 2-2 5 3H5z" fill="#9a9a9a"/><circle cx="16" cy="8" r="2" fill="#9a9a9a"/></svg>')
_FORM_OVERHEAD = 64 * 1024  # field names, boundaries, the expected version


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def thumbs(run, company_id, item_ids) -> dict[str, str]:
    """``{item_id: url}`` of each item's thumbnail, else `PLACEHOLDER` when it has a picture but no thumbnail (a list
    never loads a full picture); nothing for an item without one, or for a reader who may not read items."""
    ids = sorted({i for i in item_ids if i})
    out = {}
    try:
        for start in range(0, len(ids), 64):
            page = run('item query', {'ids': ids[start:start + 64], 'columns': ['picture', 'thumbnail'],
                                      'include_inactive': True, 'limit': 64}, company_id)
            for row in page['items']:
                values = row.get('values') or {}
                if values.get('thumbnail'):
                    out[row['id']] = values['thumbnail']['url']
                elif values.get('picture'):
                    out[row['id']] = PLACEHOLDER
    except BookflowError:
        return {}
    return out


def mount(app, *, render, run, credential, page_error, host, make_context, role_allows):
    from bookflow.core import registry

    def opened(request, company_id, name, raw, reason=None):
        from bookflow.adapters.http.published_transfer import PublishedTransfer
        from bookflow.core.dispatch import guard
        cmd = registry.get(name)
        cred = credential(request)
        update = {'client_name': 'bookflow-workbench', 'idempotency_key': None}
        if reason:
            update['reason'] = reason
        ctx = make_context(request, cred).model_copy(update=update)
        return guard(lambda: PublishedTransfer(host, cmd, raw, ctx, cred.user_id, cred.login, company_id, 'option',
                                               False, credential=cred), cred.hub_admin), cred

    def transfer(request, company_id, name, raw, body, reason=None):
        """An upload of ``body`` through ``name``."""
        from bookflow.core.dispatch import guard
        from bookflow.core.transfers import CHUNK_BYTES
        moved, cred = opened(request, company_id, name, raw, reason)
        try:
            for start in range(0, len(body), CHUNK_BYTES):
                moved.body.write(body[start:start + CHUNK_BYTES])
            return guard(moved.finish_input, cred.hub_admin)
        finally:
            moved.close()

    @app.get('/c/{company_id}/picture/{attachment_id}/{sha256}')
    async def item_picture_image(request: Request, company_id: str, attachment_id: str, sha256: str):
        from bookflow.company.item_pictures import sniff
        from bookflow.core.transfers import CHUNK_BYTES
        if not _ID.match(attachment_id) or not _SHA.match(sha256):
            return Response(status_code=404)
        owner = _TransferOwner()

        def begin():
            moved, _cred = opened(request, company_id, 'item picture get', {'attachment': attachment_id})
            owner.adopt(moved)
            if moved.resource.info.sha256 != sha256:
                return None
            moved.check_output()
            return moved, moved.reader.read(CHUNK_BYTES)

        keep = False
        try:
            try:
                got = await run_in_threadpool(owner.run, begin)
            except BookflowError as err:
                return Response(status_code=404 if err.code in ('E_RECORD_NOT_FOUND', 'E_IO') else 403)
            kind = got and sniff(got[1][:64])
            if kind is None:
                return Response(status_code=404)
            response = _PictureDownload(got[0], owner, got[1], kind)
            keep = True
            return response
        finally:
            if not keep:
                owner.close()

    def fragment(request, company_id, item, *, message=None, error=None, status_code=200):
        company = run(request, 'company show', {}, company_id)
        cred = credential(request)
        return render('item_picture.html', request, status_code=status_code, company_id=company_id,
                      item_picture=context(item, company, cred), picture_message=message, picture_error=error)

    def context(item, company, cred):
        can = all(role_allows(name, company, cred) for name in ('item picture set', 'item picture clear',
                                                              'attachment add'))
        return {'item': item['id'], 'name': item['full_name'], 'version': item['version'],
                'picture': item.get('picture'), 'thumbnail': item.get('thumbnail'), 'can_edit': can}

    def refused(request, company_id, item_id, error):
        item = run(request, 'item show', {'item': item_id}, company_id)
        return fragment(request, company_id, item, error=error.to_dict())

    @app.post('/c/{company_id}/item-picture/{item_id}')
    async def item_picture_upload(request: Request, company_id: str, item_id: str):
        from bookflow.company.item_pictures import PICTURE_MAX_BYTES, THUMBNAIL_MAX_BYTES
        limit = PICTURE_MAX_BYTES + THUMBNAIL_MAX_BYTES + _FORM_OVERHEAD
        too_large = BookflowError('E_VALUE_RANGE', f'A picture may be at most {PICTURE_MAX_BYTES // 1_000_000} MB.',
                                  {'fields': [{'field': 'picture', 'problem': 'too large'}]})
        if _int(request.headers.get('content-length'), 0) > limit:
            return await run_in_threadpool(refused, request, company_id, item_id, too_large)
        body, size = [], 0
        async for chunk in request.stream():  # the body is read up to the limit and no further
            size += len(chunk)
            if size > limit:
                return await run_in_threadpool(refused, request, company_id, item_id, too_large)
            body.append(chunk)
        request._body = b''.join(body)  # Starlette parses the form from this, already read
        try:
            form = await request.form(max_files=2, max_fields=4, max_part_size=PICTURE_MAX_BYTES + 1)
        except HTTPException as e:
            if e.status_code != 400:
                raise
            return await run_in_threadpool(refused, request, company_id, item_id, too_large)
        picture, thumbnail = form.get('picture'), form.get('thumbnail')
        data = await picture.read() if picture is not None and hasattr(picture, 'read') else None
        thumb = await thumbnail.read() if thumbnail is not None and hasattr(thumbnail, 'read') else b''
        fields = {'name': getattr(picture, 'filename', None), 'content_type': getattr(picture, 'content_type', None),
                  'expected_version': form.get('expected_version') or ''}
        return await run_in_threadpool(upload_submit, request, company_id, item_id, data, thumb, fields)

    def upload_submit(request, company_id, item_id, data, thumb, fields):
        try:
            if data is None:
                raise BookflowError('E_VALIDATION', 'Take a photo or choose a picture first.',
                                    {'fields': [{'field': 'picture', 'problem': 'no file'}]})
            if not data:
                raise BookflowError('E_VALIDATION', 'Take a photo or choose a picture first.',
                                    {'fields': [{'field': 'picture', 'problem': 'empty'}]})
            name = re.sub(r'[\x00-\x1f\x7f/\\]', '_', (fields['name'] or 'picture.jpg').rsplit('/', 1)[-1])[:200] \
                or 'picture.jpg'
            raw = {'item': item_id, 'original_filename': name, 'media_type': fields['content_type'] or 'image/jpeg'}
            if fields['expected_version'].isdigit():
                raw['expected_version'] = int(fields['expected_version'])
            added = None
            if thumb and thumb != data:  # a picture no larger than a thumbnail is its own: none is kept
                from bookflow.company.item_pictures import (HEAD_BYTES, THUMBNAIL_CAPTION, THUMBNAIL_MAX_BYTES,
                                                            require_image)
                kind = require_image(thumb[:HEAD_BYTES], len(thumb), field='thumbnail', limit=THUMBNAIL_MAX_BYTES)
                added = transfer(request, company_id, 'attachment add', {
                    'record_type': 'item', 'record_id': item_id, 'original_filename': 'thumbnail-' + name,
                    'media_type': kind, 'caption': THUMBNAIL_CAPTION}, thumb, 'Item picture thumbnail')
                raw['thumbnail_attachment'] = added['attachment']['id']
            try:
                transfer(request, company_id, 'item picture set', raw, data, 'Item picture')
            except BookflowError:
                if added and added.get('link'):  # the picture was refused: its thumbnail does not stay behind
                    try:
                        run(request, 'attachment unlink', {'link': added['link']['id'],
                            'expected_version': added['link']['version']}, company_id,
                            {'X-Bookflow-Reason': 'Item picture refused'})
                    except BookflowError:
                        pass
                raise
            item = run(request, 'item show', {'item': item_id}, company_id)
            return fragment(request, company_id, item, message='Picture saved.')
        except BookflowError as err:
            item = run(request, 'item show', {'item': item_id}, company_id)
            return fragment(request, company_id, item, error=err.to_dict())

    @app.post('/c/{company_id}/item-picture/{item_id}/clear')
    async def item_picture_clear(request: Request, company_id: str, item_id: str):
        if _int(request.headers.get('content-length'), 0) > _FORM_OVERHEAD:
            return Response(status_code=413)
        form = await request.form(max_files=0, max_fields=4, max_part_size=1024)
        return await run_in_threadpool(clear_submit, request, company_id, item_id, form.get('expected_version') or '')

    def clear_submit(request, company_id, item_id, version):
        raw = {'item': item_id}
        if version.isdigit():
            raw['expected_version'] = int(version)
        try:
            run(request, 'item picture clear', raw, company_id, {'X-Bookflow-Reason': 'Item picture removed'})
            message, error = 'Picture removed.', None
        except BookflowError as err:
            message, error = None, err.to_dict()
        item = run(request, 'item show', {'item': item_id}, company_id)
        return fragment(request, company_id, item, message=message, error=error)

    return context


class _PictureDownload(LeasedDownload):
    """The picture's verified bytes, streamed a chunk at a time in the thread pool, with the image headers; the HTTP
    transfer route's download sends it (bounded waits) and closes the transfer when the response ends."""

    def __init__(self, transfer, owner, first, kind):
        self.transfer, self.owner, self.first = transfer, owner, first
        StreamingResponse.__init__(self, self.chunks(), media_type=kind, headers={
            'Content-Length': str(transfer.resource.info.size_bytes), 'Cache-Control': CACHE,
            'X-Content-Type-Options': 'nosniff', 'Content-Disposition': 'inline',
            'Content-Security-Policy': "default-src 'none'; sandbox"})

    async def chunks(self):
        from bookflow.core.transfers import CHUNK_BYTES
        info, chunk = self.transfer.resource.info, self.first
        digest, size = hashlib.sha256(), 0
        while chunk:
            size += len(chunk)
            if size > info.size_bytes:
                raise BookflowError('E_IO', details={'check': 'download_size'})
            digest.update(chunk)
            yield chunk
            await run_in_threadpool(self.owner.run, self.transfer.check_output)
            chunk = await run_in_threadpool(self.owner.run, self.transfer.reader.read, CHUNK_BYTES)
        if size != info.size_bytes or digest.hexdigest() != info.sha256:
            raise BookflowError('E_IO', details={'check': 'download_digest'})
