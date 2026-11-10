"""A customer's membership panel (R188 increment 1) and the Members view of the customer list (increment 2).

The panel shows enrollment history, enroll or end, whether a job inherits its parent's membership, and a price
preview. The Members view lists who a plan prices on a date (jobs included), enrolls many customers as one
reversible batch and reverses that batch. Every action runs the same commands as the CLI and MCP
(`customer membership show|set|end|inherit|list|enroll`, `membership-plan list`, `price-rule preview`,
`batch reverse`), so the capability checks are theirs.
"""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError

_ACTIONS = ('customer membership set', 'customer membership end', 'customer membership inherit', 'membership-plan list',
            'price-rule preview')
_LIST_ACTIONS = ('customer membership enroll', 'membership-plan list', 'batch reverse')


def mount(app, *, render, run, credential, page_error, role_allows):
    def page(request, company_id, customer_id, *, values=None, error=None, preview=None, status_code=200):
        company = run(request, 'company show', {}, company_id)
        cred = credential(request)
        allowed = {name: role_allows(name, company, cred) for name in _ACTIONS}
        shown = run(request, 'customer membership show', {'customer': customer_id}, company_id)
        record = run(request, 'customer show', {'customer': customer_id}, company_id)
        parent = record.get('parent_id')
        plans = run(request, 'membership-plan list', {}, company_id)['items'] if allowed['membership-plan list'] else []
        return render('membership.html', request, status_code=status_code, company_id=company_id,
                      customer_id=customer_id, shown=shown, plans=plans, allowed=allowed, values=values or {},
                      is_job=bool(parent),
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
            elif action == 'inherit':
                raw = {'customer': customer_id, 'inherit': form.get('inherit') == 'true'}
                if _text(form, 'expected_version'):
                    raw['expected_version'] = int(form['expected_version'])
                out = run(request, 'customer membership inherit', raw, company_id, headers=headers)
                said = ('This job now uses its parent customer\'s membership.' if out['inherit']
                        else 'This job no longer inherits a membership.')
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


    def members_link(request, company_id) -> bool:
        """The customer list shows its Members view only while the layer is on and the reader may list members."""
        company = run(request, 'company show', {}, company_id)
        return bool((company.get('info') or {}).get('membership_pricing_enabled')) and role_allows(
            'customer membership list', company, credential(request))

    def members_page(request, company_id, *, values=None, error=None, result=None, reversal=None, status_code=200):
        company = run(request, 'company show', {}, company_id)
        cred = credential(request)
        allowed = {name: role_allows(name, company, cred) for name in _LIST_ACTIONS}
        query = request.query_params
        raw = {'include_inherited': query.get('jobs', 'true') != 'false'}
        for key in ('plan', 'as_of', 'cursor'):
            if (query.get(key) or '').strip():
                raw[key] = query[key].strip()
        listed = run(request, 'customer membership list', raw, company_id)
        plans = run(request, 'membership-plan list', {}, company_id)['items'] if allowed['membership-plan list'] else []
        return render('members.html', request, status_code=status_code, company_id=company_id, listed=listed,
                      plans=plans, allowed=allowed, filters=raw, values=values or {}, error=error, result=result,
                      reversal=reversal, notice=query.get('done'))

    @app.get('/c/{company_id}/memberships')
    def members(request: Request, company_id: str):
        try:
            return members_page(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    def members_submit(request, company_id, form):
        action = form.get('action') or 'enroll'
        headers = {'X-Bookflow-Reason': _text(form, 'ctx:reason') or 'Enroll customers in a membership plan'}
        try:
            if action == 'enroll':
                names = [line.strip() for line in (form.get('customers') or '').splitlines() if line.strip()]
                raw = {'customers': names, 'plan': _text(form, 'plan'), 'start_date': _text(form, 'start_date')}
                if _text(form, 'end_date'):
                    raw['end_date'] = _text(form, 'end_date')
                result = run(request, 'customer membership enroll', raw, company_id, headers=headers)
                return members_page(request, company_id, result=result)
            if action == 'reverse':
                reversal = run(request, 'batch reverse', {'batch': _text(form, 'batch')}, company_id, headers=headers)
                return members_page(request, company_id, reversal=reversal)
            raise BookflowError('E_VALIDATION', message='Unknown membership action.')
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                return page_error(request, e)
            return members_page(request, company_id, values=dict(form), error=e.to_dict(), status_code=400)

    @app.post('/c/{company_id}/memberships')
    async def members_action(request: Request, company_id: str):
        return await run_in_threadpool(members_submit, request, company_id, dict(await request.form()))

    return members_link
