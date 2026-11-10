"""Lead list and detail over the shared lead, note, attachment and to-do commands."""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError


def mount(app, *, render, run, credential, page_error, role_allows, annotations):
    def permitted(request, company, name):
        return role_allows(name, company, credential(request))

    def back_url(company_id, value):
        prefix = f'/c/{company_id}/lead'
        return value if value and (value == prefix or value.startswith(prefix + '?')) else prefix

    def page(request, company_id, lead_id=None, *, values=None, error=None, status_code=200):
        company = run(request, 'company show', {}, company_id)
        allowed = {name: permitted(request, company, name) for name in (
            'lead create', 'lead update', 'lead close', 'lead reopen', 'lead archive',
            'todo list', 'todo create', 'todo complete')}
        back = back_url(company_id, request.query_params.get('back'))
        context = dict(company_id=company_id, allowed=allowed, error=error, values=values or {},
                       notice=request.query_params.get('done'), back=back)
        if lead_id:
            lead = run(request, 'lead show', {'lead': lead_id}, company_id)
            tasks = run(request, 'todo list', {'link_type': 'lead', 'link': lead_id, 'status': 'all'}, company_id) if allowed['todo list'] else None
            context.update(lead=lead, tasks=tasks, annotations=annotations(
                company_id, 'lead', lead_id, lead, company, credential(request)))
        else:
            raw = {'status': request.query_params.get('status') or 'open',
                   'include_archived': request.query_params.get('include_archived') == '1'}
            for key in ('search', 'owner', 'cursor'):
                if request.query_params.get(key):
                    raw[key] = request.query_params[key]
            listed = run(request, 'lead list', raw, company_id)
            state = dict(request.query_params)
            next_url = None
            if listed.get('next_cursor'):
                next_url = f'/c/{company_id}/lead?' + urlencode(dict(state, cursor=listed['next_cursor']))
            context.update(listed=listed, filters=raw, next_url=next_url,
                           return_query=urlencode({'back': str(request.url.path) + ('?' + str(request.url.query) if request.url.query else '')}))
        return render('crm_lead.html' if lead_id else 'crm_leads.html', request, status_code=status_code, **context)

    @app.get('/c/{company_id}/lead')
    def leads(request: Request, company_id: str):
        try:
            return page(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    @app.get('/c/{company_id}/lead/{lead_id:ulid}')
    def lead_detail(request: Request, company_id: str, lead_id: str):
        try:
            return page(request, company_id, lead_id)
        except BookflowError as e:
            return page_error(request, e)

    def submit(request, company_id, form, lead_id=None):
        action = form.get('action') or 'create'
        headers = {'X-Bookflow-Reason': (form.get('ctx:reason') or '').strip() or 'Maintain lead and follow-ups'}
        try:
            if action == 'create':
                raw = {key: form[key].strip() for key in ('display_name', 'company_name', 'first_name', 'last_name', 'source', 'summary', 'owner') if form.get(key, '').strip()}
                contact = {key: form['contact_' + key].strip() for key in ('display_name', 'primary_email', 'work_phone') if form.get('contact_' + key, '').strip()}
                if contact:
                    raw['contacts'] = [dict(contact, role='primary')]
                result = run(request, 'lead create', raw, company_id, headers=headers)
                lead_id = result['id']
                said = 'Lead added.'
            elif action == 'follow-up':
                raw = {'title': form.get('title', ''), 'link_type': 'lead', 'link': lead_id}
                for key in ('due_date', 'assignee', 'notes', 'priority'):
                    if form.get(key, '').strip():
                        raw[key] = form[key].strip()
                run(request, 'todo create', raw, company_id, headers=headers)
                said = 'Follow-up added.'
            elif action == 'complete':
                run(request, 'todo complete', {'todo': form.get('todo'), 'expected_version': int(form['expected_version'])}, company_id, headers=headers)
                said = 'Follow-up completed.'
            else:
                raise BookflowError('E_VALIDATION', message='Unknown lead action.')
        except (BookflowError, ValueError) as e:
            if isinstance(e, ValueError):
                e = BookflowError('E_VALIDATION', message='Use the current record version.')
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                return page_error(request, e)
            return page(request, company_id, lead_id, values=dict(form), error=e.to_dict(), status_code=400)
        params = {'done': said, 'back': back_url(company_id, form.get('back'))}
        target = f'/c/{company_id}/lead/{lead_id}?' + urlencode(params)
        return RedirectResponse(target, status_code=303)

    @app.post('/c/{company_id}/lead')
    async def lead_create(request: Request, company_id: str):
        return await run_in_threadpool(submit, request, company_id, dict(await request.form()))

    @app.post('/c/{company_id}/lead/{lead_id:ulid}')
    async def lead_action(request: Request, company_id: str, lead_id: str):
        return await run_in_threadpool(submit, request, company_id, dict(await request.form()), lead_id)
