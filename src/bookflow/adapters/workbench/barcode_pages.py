"""The Scan field's look-up (R104 increment 3, design/advanced-inventory.md §7).

`GET /c/{company}/barcode/lookup?code=...` answers what the Scan field typed. A scanner sends codes,
so the order here is barcode (`barcode lookup`), then an item id or name (`item show`) -- unlike an
`item` field on a document, which takes id, then name, then barcode. When a code and another item's
name collide, the code wins and the message says both. It writes nothing, so it needs no form and no
workbench header. The answer is JSON for scan.js: ``found``, ``item_id``, ``item_name``, ``message``
and, for an unknown code, ``assign_url``.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from bookflow.adapters.workbench import routing as Routing
from bookflow.core.errors import BookflowError


def _item(call, text):
    try:
        return call('item show', {'item': text})
    except BookflowError as exc:
        if exc.code in ('E_RECORD_NOT_FOUND', 'E_VALIDATION'):
            return None
        raise


def answer(call, company_id: str, code: str) -> dict:
    """What the Scan field shows for ``code``; ``call(name, raw)`` runs a command as the viewer."""
    looked = call('barcode lookup', {'code': code})
    if looked['found'] and looked['match']['kind'] in ('serial_number', 'lot'):
        # A unit's serial or lot label (R104 increment 4): the item it belongs to, with the number for the line.
        match = looked['match']
        item = _item(call, match['line_fields']['item'])
        if item is not None:
            numbers = {key: value for key, value in match['line_fields'].items() if key != 'item'}
            return {'found': bool(item.get('active', True)), 'item_id': item['id'], 'item_name': item['full_name'],
                    'code': code, 'message': looked['message'], **numbers}
    if looked['found']:
        match = looked['match']
        message = f"{match['name']} (code {code})."
        named = _item(call, code)
        if named is not None and named['id'] != match['id']:
            message = f"Matched code {code} on {match['name']}; an item named {code} also exists."
        if not match['active']:
            message = looked['message']
        return {'found': bool(match['active']), 'item_id': match['id'], 'item_name': match['name'],
                'code': code, 'message': message}
    if looked['ambiguous']:
        return {'found': False, 'ambiguous': True, 'code': code, 'message': looked['message']}
    item = _item(call, code)
    if item is not None:
        return {'found': bool(item.get('active', True)), 'item_id': item['id'], 'item_name': item['full_name'],
                'code': code, 'message': f"{item['full_name']}."}
    return {'found': False, 'code': code, 'message': f'No item has the code {code}.',
            'assign_url': Routing.base(company_id, 'item')}


def mount(app, *, run):
    @app.get('/c/{company_id}/barcode/lookup')
    def barcode_lookup(request: Request, company_id: str):
        code = (request.query_params.get('code') or '').strip()
        if not code:
            return JSONResponse({'found': False, 'message': 'Scan or type an item first.'}, status_code=400)
        try:
            return JSONResponse(answer(lambda name, raw: run(request, name, raw, company_id), company_id, code),
                                headers={'Cache-Control': 'no-store'})
        except BookflowError as exc:
            status = 403 if exc.code == 'E_PERMISSION' else 400
            return JSONResponse({'found': False, 'code': code, 'message': exc.message, 'error': exc.code},
                                status_code=status, headers={'Cache-Control': 'no-store'})
