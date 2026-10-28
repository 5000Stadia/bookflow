"""The item picture in the browser (R114 increment 1, design/point-of-sale.md §6).

The item page shows the picture beside the item's name with Take photo, Choose picture and Remove. The browser scales
a photo to 1600 px on its long edge and makes a 320 px thumbnail before sending both (static/item_picture.js); the
form posts through htmx. The thumbnail goes up as an ordinary file on the item (`attachment add`), then the picture
through `item picture set`, so both are the item's files. Pictures are served at a content-addressed address through
`attachment get`, only when the bytes are a JPEG, PNG or WebP, with that type, never sniffed as anything else, and
cached for good. Lists, the pick and pack screens and the fulfillment worksheet read thumbnails with `thumbs`.
"""
from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

from bookflow.core.errors import BookflowError

_ID = re.compile(r'[0-9A-Z]{26}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
CACHE = 'private, max-age=31536000, immutable'


def thumbs(run, company_id, item_ids) -> dict[str, str]:
    """``{item_id: url}`` of each item's thumbnail, else its picture; nothing for an item without one, or for a
    reader who may not read items."""
    ids = sorted({i for i in item_ids if i})
    out = {}
    try:
        for start in range(0, len(ids), 64):
            page = run('item query', {'ids': ids[start:start + 64], 'columns': ['picture', 'thumbnail'],
                                      'include_inactive': True, 'limit': 64}, company_id)
            for row in page['items']:
                values = row.get('values') or {}
                shown = values.get('thumbnail') or values.get('picture')
                if shown:
                    out[row['id']] = shown['url']
    except BookflowError:
        return {}
    return out


def mount(app, *, render, run, credential, page_error, host, make_context, role_allows):
    from bookflow.core import registry

    def transfer(request, company_id, name, raw, body=None, reason=None):
        from bookflow.adapters.http.published_transfer import PublishedTransfer
        from bookflow.core.dispatch import guard
        cmd = registry.get(name)
        cred = credential(request)
        update = {'client_name': 'bookflow-workbench', 'idempotency_key': None}
        if reason:
            update['reason'] = reason
        ctx = make_context(request, cred).model_copy(update=update)
        moved = guard(lambda: PublishedTransfer(host, cmd, raw, ctx, cred.user_id, cred.login, company_id, 'option',
                                                False, credential=cred), cred.hub_admin)
        try:
            if body is None:  # a download: the verified bytes
                info, chunks = moved.resource.info, []
                while True:
                    moved.check_output()
                    chunk = moved.reader.read(1 << 16)
                    if not chunk:
                        break
                    chunks.append(chunk)
                data = b''.join(chunks)
                if len(data) != info.size_bytes:
                    raise BookflowError('E_IO', details={'check': 'download_size'})
                return data, info
            from bookflow.core.transfers import CHUNK_BYTES
            for start in range(0, len(body), CHUNK_BYTES):
                moved.body.write(body[start:start + CHUNK_BYTES])
            return guard(moved.finish_input, cred.hub_admin)
        finally:
            moved.close()

    @app.get('/c/{company_id}/picture/{attachment_id}/{sha256}')
    def item_picture_image(request: Request, company_id: str, attachment_id: str, sha256: str):
        from bookflow.company.item_pictures import sniff
        if not _ID.match(attachment_id) or not _SHA.match(sha256):
            return Response(status_code=404)
        try:
            data, info = transfer(request, company_id, 'attachment get', {'attachment': attachment_id})
        except BookflowError as err:
            return Response(status_code=404 if err.code in ('E_RECORD_NOT_FOUND', 'E_IO') else 403)
        kind = sniff(data[:64])
        if info.sha256 != sha256 or kind is None:
            return Response(status_code=404)
        return Response(data, media_type=kind, headers={
            'Cache-Control': CACHE, 'X-Content-Type-Options': 'nosniff', 'Content-Disposition': 'inline',
            'Content-Security-Policy': "default-src 'none'; sandbox"})

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

    @app.post('/c/{company_id}/item-picture/{item_id}')
    async def item_picture_upload(request: Request, company_id: str, item_id: str):
        form = await request.form()
        picture, thumbnail = form.get('picture'), form.get('thumbnail')
        try:
            if picture is None or not hasattr(picture, 'read'):
                raise BookflowError('E_VALIDATION', 'Take a photo or choose a picture first.',
                                    {'fields': [{'field': 'picture', 'problem': 'no file'}]})
            data = await picture.read()
            if not data:
                raise BookflowError('E_VALIDATION', 'Take a photo or choose a picture first.',
                                    {'fields': [{'field': 'picture', 'problem': 'empty'}]})
            name = re.sub(r'[\x00-\x1f\x7f/\\]', '_', (picture.filename or 'picture.jpg').rsplit('/', 1)[-1])[:200] \
                or 'picture.jpg'
            raw = {'item': item_id, 'original_filename': name, 'media_type': picture.content_type or 'image/jpeg'}
            if (form.get('expected_version') or '').isdigit():
                raw['expected_version'] = int(form['expected_version'])
            thumb = await thumbnail.read() if thumbnail is not None and hasattr(thumbnail, 'read') else b''
            if thumb:
                from bookflow.company.item_pictures import THUMBNAIL_MAX_BYTES, require_image
                require_image(thumb[:64], len(thumb), field='thumbnail', limit=THUMBNAIL_MAX_BYTES)
                added = transfer(request, company_id, 'attachment add', {
                    'record_type': 'item', 'record_id': item_id, 'original_filename': 'thumbnail-' + name,
                    'media_type': 'image/jpeg', 'caption': 'Item picture thumbnail'}, thumb, 'Item picture thumbnail')
                raw['thumbnail_attachment'] = added['attachment']['id']
            try:
                transfer(request, company_id, 'item picture set', raw, data, 'Item picture')
            except BookflowError:
                if thumb and added.get('link'):  # the picture was refused: its thumbnail does not stay behind
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
        form = await request.form()
        raw = {'item': item_id}
        if (form.get('expected_version') or '').isdigit():
            raw['expected_version'] = int(form['expected_version'])
        try:
            run(request, 'item picture clear', raw, company_id, {'X-Bookflow-Reason': 'Item picture removed'})
            message, error = 'Picture removed.', None
        except BookflowError as err:
            message, error = None, err.to_dict()
        item = run(request, 'item show', {'item': item_id}, company_id)
        return fragment(request, company_id, item, message=message, error=error)

    return context
