"""A customer's membership panel (R188 increment 1): enrollment history, enroll or end, and a price preview.

Every action runs the same commands as the CLI and MCP (`customer membership show|set|end`,
`membership-plan list`, `price-rule preview`), so the capability checks are theirs.
"""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError

_ACTIONS = ('customer membership set', 'customer membership end', 'membership-plan list', 'price-rule preview')


def mount(app, *, render, run, credential, page_error, role_allows):
    def page(request, company_id, customer_id, *, values=None, error=None, preview=None, status_code=200):
        company = run(request, 'company show', {}, company_id)
        cred = credential(request)
        allowed = {name: role_allows(name, company, cred) for name in _ACTIONS}
        shown = run(request, 'customer membership show', {'customer': customer_id}, company_id)
        plans = run(request, 'membership-plan list', {}, company_id)['items'] if allowed['membership-plan list'] else []
        return render('membership.html', request, status_code=status_code, company_id=company_id,
                      customer_id=customer_id, shown=shown, plans=plans, allowed=allowed, values=values or {},
                      error=error, preview=preview, notice=request.query_params.get('done'))

    @app.get('/c/{company_id}/customer/{customer_id:ulid}/membership')
    def membership(request: Request, company_id: str, customer_id: str):
        try:
            return page(request, company_id, customer_id)
        except BookflowError as e:
            return page_error(request, e)

    def _text(form, key):
        value = (form.get(key) or '').strip()
        return value or None

    def submit(request, company_id, customer_id, form):
        action = form.get('action') or 'enroll'
        headers = {'X-Bookflow-Reason': _text(form, 'ctx:reason') or 'Maintain customer membership'}
        preview = None
        try:
            if action == 'enroll':
                raw = {'customer': customer_id, 'plan': _text(form, 'plan'), 'start_date': _text(form, 'start_date')}
                for key in ('end_date', 'member_number'):
                    if _text(form, key):
                        raw[key] = _text(form, key)
                run(request, 'customer membership set', raw, company_id, headers=headers)
                said = 'Customer enrolled.'
            elif action == 'end':
                raw = {'membership': _text(form, 'membership'), 'end_date': _text(form, 'end_date'),
                       'expected_version': int(form.get('expected_version') or '')}
                run(request, 'customer membership end', raw, company_id, headers=headers)
                said = 'Enrollment ended.'
            elif action == 'preview':
                raw = {'customer': customer_id, 'item': _text(form, 'item'), 'quantity': _text(form, 'quantity') or '1'}
                if _text(form, 'date'):
                    raw['date'] = _text(form, 'date')
                preview = run(request, 'price-rule preview', raw, company_id)
                return page(request, company_id, customer_id, values=dict(form), preview=preview)
            else:
                raise BookflowError('E_VALIDATION', message='Unknown membership action.')
        except (BookflowError, ValueError) as e:
            if isinstance(e, ValueError):
                e = BookflowError('E_VALIDATION', message='Use the current enrollment version.')
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                return page_error(request, e)
            return page(request, company_id, customer_id, values=dict(form), error=e.to_dict(), status_code=400)
        return RedirectResponse(f'/c/{company_id}/customer/{customer_id}/membership?' + urlencode({'done': said}),
                                status_code=303)

    @app.post('/c/{company_id}/customer/{customer_id:ulid}/membership')
    async def membership_action(request: Request, company_id: str, customer_id: str):
        return await run_in_threadpool(submit, request, company_id, customer_id, dict(await request.form()))
