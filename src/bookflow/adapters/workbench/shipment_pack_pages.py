"""The pack screen (R181 increment 4): boxes for a draft or a shipped shipment, built for a phone like the pick screen.

``/shipments/{number}/pack`` shows the shipment's lines with a Box column, each box as a card with its computed and
measured weight, package type, dimensions and tracking number, and on top a Scan field with a Box field beside it:
a scan (a serial number, a lot or an item code; Enter submits, as a scanner in keyboard mode sends it) puts that unit
in the current box. Box 0 takes it out again. Print packing slip and, for a draft, Ship. Packing is an optional
layer: while the company has pick and pack off the page answers with the switch's own message. ``/shipments/new``'s
Make a draft goes through here too (`shipment create`).
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError


def _box(text):
    """The Box field as a whole box number (blank: box 1); anything else is the screen's own refusal."""
    text = (text or '').strip() or '1'
    if not (text.isascii() and text.isdigit()):
        raise BookflowError('E_VALIDATION', message='Box is a whole number: 1, 2, 3 (0 takes a unit out of its box).',
                            details={'fields': [{'field': 'box', 'problem': 'a whole box number'}]})
    return int(text)


_PASS = ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER')


def mount(app, *, render, run, credential, page_error, role_allows):
    def _company(request, company_id):
        company = run(request, 'company show', {}, company_id)
        if not company.get('info', {}).get('pick_and_pack_enabled'):
            from bookflow.company.shipment_packing import disabled_error
            raise disabled_error()
        return company

    def headers(form, default=None):
        reason = (form.get('ctx:reason') or '').strip() or default
        return {'X-Bookflow-Reason': reason} if reason else None

    def pack_page(request, company_id, number, *, values=None, result=None, error=None, status_code=200):
        company = _company(request, company_id)
        shown = run(request, 'shipment show', {'shipment': number}, company_id)
        packing = shown.get('packing') or {'packages': [], 'unboxed': [], 'packed': False, 'total_weight': '0',
                                           'weight_unit': company['info'].get('weight_unit') or 'lb', 'unweighed': '0'}
        where = {}
        for box in packing['packages']:
            for content in box['contents']:
                where.setdefault(content['line_identity'], []).append(f"{box['box']}: {content['quantity']}")
        boxes = [b['box'] for b in packing['packages']]
        values = dict(values or {})
        values.setdefault('box', str(values.get('box') or (boxes[-1] if boxes else 1)))
        from bookflow.adapters.workbench.item_picture_pages import thumbs as item_thumbs
        thumbs = item_thumbs(lambda name, raw, company: run(request, name, raw, company), company_id,
                             [line['item_id'] for line in shown['lines']])
        return render('shipment_pack.html', request, status_code=status_code, company=company, thumbs=thumbs,
                      company_id=company['company_id'], shipment=shown, packing=packing, where=where,
                      next_box=max(boxes, default=0) + 1, values=values, result=result, error=error, quote=quote,
                      can_pack=role_allows('shipment pack', company, credential(request)) and
                      shown['status'] in ('draft', 'shipped'),
                      can_ship=role_allows('shipment post', company, credential(request)) and shown['status'] == 'draft')

    @app.get('/c/{company_id}/shipments/{number}/pack')
    def shipment_pack_page(request: Request, company_id: str, number: str):
        try:
            return pack_page(request, company_id, number)
        except BookflowError as e:
            return page_error(request, e)

    def submit(request, company_id, number, form):
        action = form.get('action') or ''
        values = {'box': (form.get('box') or '').strip()}
        try:
            if action == 'scan':
                code = (form.get('code') or '').strip()
                if not code:
                    raise BookflowError('E_VALIDATION', message='Scan or type a serial number or item first.',
                                        details={'fields': [{'field': 'unit', 'problem': 'scan or type a unit'}]})
                raw = {'shipment': number, 'unit': code, 'box': _box(values['box'])}
                if (form.get('quantity') or '').strip():
                    raw['quantity'] = form['quantity'].strip()
                out = run(request, 'shipment pack', raw, company_id, headers=headers(form, 'Packed'))
                where = 'out of its box' if raw['box'] == 0 else f"in box {raw['box']}"
                result = f'{code} {where}.' + (' Everything is packed.' if (out.get('packing') or {}).get('packed') else '')
            elif action == 'box':
                raw = {'shipment': number, 'box': _box(form.get('box'))}
                for key in ('package_type', 'weight', 'dimensions', 'tracking_number'):
                    if key in form:
                        raw[key] = (form.get(key) or '').strip()
                out = run(request, 'shipment pack', raw, company_id, headers=headers(form, 'Box details'))
                result = f"Box {raw['box']} saved."
            elif action == 'all':
                lines = (form.get('line') or '').strip()
                raw = {'shipment': number, 'line': lines, 'box': _box(values['box']),
                       'quantity': (form.get('quantity') or '').strip() or None}
                raw = {k: v for k, v in raw.items() if v is not None}
                out = run(request, 'shipment pack', raw, company_id, headers=headers(form, 'Packed'))
                result = f"Line {lines} in box {raw['box']}."
            elif action == 'ship':
                out = run(request, 'shipment post', {'shipment': number}, company_id,
                          headers=headers(form, 'Shipped the packed draft'))
                return pack_page(request, company_id, out['number'],
                                 result=f"Shipment {out['number']} shipped with {len((out.get('packing') or {}).get('packages') or [])} boxes.")
            else:
                return pack_page(request, company_id, number, values=values)
        except BookflowError as e:
            if e.code in _PASS:
                raise
            # htmx swaps only a 2xx answer: the page carrying the error is the answer to a scan.
            return pack_page(request, company_id, number, values=values, error=e.to_dict(),
                             status_code=200 if request.headers.get('HX-Request') else 400)
        return pack_page(request, company_id, number, values=values, result=result)

    @app.post('/c/{company_id}/shipments/{number}/pack')
    async def shipment_pack_submit(request: Request, company_id: str, number: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(submit, request, company_id, number, form)
        except BookflowError as e:
            return page_error(request, e)

    @app.get('/c/{company_id}/shipments/{number}/packing-slip')
    def shipment_packing_slip(request: Request, company_id: str, number: str):
        """The packing slip, whole or ``?per_box=1`` a page per box: the same renderer as every printed document."""
        from bookflow.adapters.workbench.document_print import _pdf
        from bookflow.documents import render as render_pdf
        try:
            identity = {'document': number, 'per_box': request.query_params.get('per_box') in ('1', 'true')}
            rendered = render_pdf(lambda name, raw, company: run(request, name, raw, company), company_id,
                                  'packing-slip', identity)
        except BookflowError as e:
            return page_error(request, e, company_id=company_id)
        return _pdf(rendered)
