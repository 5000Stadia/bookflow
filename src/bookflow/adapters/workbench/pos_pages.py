"""The POS till and shared company layout, edited through the pos-box command boundary.

GET reads the stored layout (or its deterministic category seed preview) once alongside the active sales items.
Taps and lookup remain local. Layout writes use a versioned htmx form, and group pictures use binary transfers.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError

# What a box can ring up: an item a sales receipt sells. Discounts and subtotals are rung up by lookup (they are not
# boxes); payments, tax items and fixed assets are never sold at the till.
BOX_TYPES = ('service', 'inventory_part', 'non_inventory_part', 'other_charge', 'inventory_assembly', 'group')
LOOKUP_TYPES = BOX_TYPES + ('discount', 'subtotal')
_SELLERS = ('standard', 'admin', 'owner')


def _picture(item: dict[str, Any]) -> str | None:
    shown = item.get('thumbnail') or item.get('picture')
    return shown.get('url') if shown else None


def _items(listed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for item in listed:
        if not item.get('active', True) or not item.get('sales_enabled') or item.get('type') not in LOOKUP_TYPES:
            continue
        price = item.get('price') or None
        out.append(dict(id=item['id'], name=item['full_name'], type=item['type'], category=item.get('category'),
                        price=price['amount'] if price else None, barcode=item.get('barcode'),
                        sku=item.get('manufacturer_part_number'), description=item.get('description'),
                        on_hand=item.get('quantity_on_hand') if item['type'] in ('inventory_part',
                                                                                 'inventory_assembly') else None,
                        tracking=item.get('tracking') or 'none', picture=_picture(item),
                        # A percentage charge has no price of its own: it is rung up by lookup, like a discount.
                        box=item['type'] in BOX_TYPES and not (item['type'] == 'other_charge' and price is None)))
    return out


def mount(app, *, render, run, credential, page_error, role_allows, holds=None, host=None, make_context=None):
    @app.get('/c/{company_id}/pos')
    def pos_page(request: Request, company_id: str):
        try:
            company = run(request, 'company show', {}, company_id)
            registers = run(request, 'pos-register list', {}, company_id)['items']
            cid = company['company_id']
            items = _items(run(request, 'item list', {}, cid)['items'])
            held = holds(request, cid, 'pos.price-override') if holds is not None else None
            can_override = bool(held) if held is not None else company.get('role') in _SELLERS
            data = dict(company=cid, currency=company.get('home_currency') or 'USD',
                        registers=[dict(id=r['id'], name=r['name'], methods=r['payment_methods'])
                                   for r in registers],
                        items=items, layout=run(request, 'pos-box list', {}, cid), can_override=can_override,
                        can_layout=role_allows('pos-box create', company, credential(request)),
                        can_void=role_allows('pos sale void', company, credential(request)),
                        held=run(request, 'pos ticket list', {}, cid)['items'])
            return render('pos.html', request, company=company, company_id=cid, registers=registers, pos_data=data)
        except BookflowError as e:
            return page_error(request, e)

    @app.post('/c/{company_id}/pos/layout')
    async def pos_layout(request: Request, company_id: str):
        if int(request.headers.get('content-length', '0')) > 65536:
            return Response(status_code=413)
        form = await request.form(max_files=0, max_fields=2, max_part_size=65536)
        name = str(form.get('command', ''))
        if name not in ('create', 'update', 'move', 'remove', 'view', 'seed'):
            return JSONResponse({'message': 'Unknown layout change.'}, status_code=400)
        try:
            raw = json.loads(str(form.get('body', '{}')))
        except (ValueError, TypeError):
            return JSONResponse({'message': 'Invalid layout change.'}, status_code=400)

        def change():
            try:
                return JSONResponse(run(request, 'pos-box ' + name, raw, company_id,
                                        {'X-Bookflow-Reason': 'POS layout'}))
            except BookflowError as err:
                return JSONResponse(err.to_dict(), status_code=400)
        return await run_in_threadpool(change)

    def opened(request, company_id, name, raw):
        from bookflow.adapters.http.published_transfer import PublishedTransfer
        from bookflow.core import registry
        from bookflow.core.dispatch import guard
        cred = credential(request)
        ctx = make_context(request, cred).model_copy(update={
            'client_name': 'bookflow-workbench', 'idempotency_key': None, 'reason': 'POS group picture'})
        return guard(lambda: PublishedTransfer(host, registry.get(name), raw, ctx, cred.user_id, cred.login,
                                               company_id, 'option', False, credential=cred), cred.hub_admin), cred

    @app.get('/c/{company_id}/pos/picture/{attachment_id}/{sha256}')
    async def pos_picture(request: Request, company_id: str, attachment_id: str, sha256: str):
        from bookflow.adapters.http.transfers import _TransferOwner
        from bookflow.adapters.workbench.item_picture_pages import _PictureDownload, _ID, _SHA
        from bookflow.company.item_pictures import sniff
        from bookflow.core.transfers import CHUNK_BYTES
        if not _ID.match(attachment_id) or not _SHA.match(sha256):
            return Response(status_code=404)
        owner = _TransferOwner()
        def begin():
            moved, _ = opened(request, company_id, 'pos-box picture get', {'attachment': attachment_id})
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

    @app.post('/c/{company_id}/pos/picture')
    async def pos_picture_upload(request: Request, company_id: str):
        from bookflow.company.item_pictures import PICTURE_MAX_BYTES
        from bookflow.core.dispatch import guard
        from bookflow.core.transfers import CHUNK_BYTES
        limit = PICTURE_MAX_BYTES + 65536
        if int(request.headers.get('content-length', '0')) > limit:
            return JSONResponse({'message': 'The picture is too large.'}, status_code=413)
        parts, size = [], 0
        async for part in request.stream():
            size += len(part)
            if size > limit:
                return JSONResponse({'message': 'The picture is too large.'}, status_code=413)
            parts.append(part)
        request._body = b''.join(parts)
        form = await request.form(max_files=1, max_fields=2, max_part_size=limit)
        picture = form.get('picture')
        if not hasattr(picture, 'read'):
            return JSONResponse({'message': 'Choose a picture first.'}, status_code=400)
        body = await picture.read()
        try:
            version = int(str(form.get('expected_version', '')))
        except ValueError:
            return JSONResponse({'message': 'Reload the layout before changing its picture.'}, status_code=400)
        raw = {'pos_box': form.get('pos_box'), 'expected_version': version,
               'original_filename': picture.filename or 'picture.jpg',
               'media_type': picture.content_type or 'application/octet-stream'}
        def upload():
            try:
                moved, cred = opened(request, company_id, 'pos-box picture set', raw)
                try:
                    for start in range(0, len(body), CHUNK_BYTES):
                        moved.body.write(body[start:start + CHUNK_BYTES])
                    result = guard(moved.finish_input, cred.hub_admin)
                finally:
                    moved.close()
                return JSONResponse(result)
            except BookflowError as err:
                return JSONResponse(err.to_dict(), status_code=400)
        return await run_in_threadpool(upload)
