"""The Scan field's look-up (R104 increment 3, design/advanced-inventory.md §7).

`GET /c/{company}/barcode/lookup?code=...` answers what the Scan field typed: a barcode (`barcode
lookup`) or, failing that, an item's id or name (`item show`), as every `item` field resolves. It
writes nothing, so it needs no form and no workbench header. The answer is JSON for scan.js:
``found``, ``item_id``, ``item_name``, ``message`` and, for an unknown code, ``assign_url``.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from bookflow.adapters.workbench import routing as Routing
from bookflow.core.errors import BookflowError


def mount(app, *, run):
    @app.get('/c/{company_id}/barcode/lookup')
    def barcode_lookup(request: Request, company_id: str):
        code = (request.query_params.get('code') or '').strip()
        if not code:
            return JSONResponse({'found': False, 'message': 'Scan or type an item first.'}, status_code=400)
        try:
            answer = run(request, 'barcode lookup', {'code': code}, company_id)
            if answer['found']:
                match = answer['match']
                return JSONResponse({'found': match['active'], 'item_id': match['id'], 'item_name': match['name'],
                                     'code': code, 'message': answer['message']})
            if answer['ambiguous']:
                return JSONResponse({'found': False, 'ambiguous': True, 'code': code, 'message': answer['message']})
            try:
                item = run(request, 'item show', {'item': code}, company_id)
            except BookflowError as exc:
                if exc.code not in ('E_RECORD_NOT_FOUND', 'E_VALIDATION'):
                    raise
            else:
                return JSONResponse({'found': bool(item.get('active', True)), 'item_id': item['id'],
                                     'item_name': item['full_name'], 'code': code,
                                     'message': f"{item['full_name']}."})
            return JSONResponse({'found': False, 'code': code,
                                 'message': f'No item has the code {code}.',
                                 'assign_url': Routing.base(company_id, 'item')})
        except BookflowError as exc:
            status = 403 if exc.code == 'E_PERMISSION' else 400
            return JSONResponse({'found': False, 'code': code, 'message': exc.message, 'error': exc.code},
                                status_code=status, headers={'Cache-Control': 'no-store'})
