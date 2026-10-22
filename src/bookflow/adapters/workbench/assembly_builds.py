"""Build Assemblies and Pending Builds: the anchor's two windows, on the `assembly` commands (R103).

Build Assemblies asks for the assembly, a date and a quantity and shows the dry run of `assembly build`:
each component with its quantity on hand, reserved for pending builds and needed, and the maximum number
the components on hand allow. *Build* posts it; when the components are short the page says so and
offers *Mark as pending* instead, which reserves them. Pending Builds lists the builds waiting for
components (or every build), each with Finalize, Void and Delete.
"""
from __future__ import annotations

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError

BUILD = 'assembly build'


def mount(app, *, render, run, credential, page_error, role_allows):
    def assemblies(request, company_id):
        out = run(request, 'item query', {'limit': 200}, company_id)
        return [row for row in out['items'] if row.get('type') == 'inventory_assembly']

    def build_page(request, company_id, *, values=None, result=None, error=None, status_code=200):
        company = run(request, 'company show', {}, company_id)
        if not role_allows(BUILD, company, credential(request)):
            raise BookflowError('E_PERMISSION', details={'capability': 'ledger.post', 'required_role': 'standard'})
        values = values or {}
        preview = None
        if values.get('item') and values.get('date') and values.get('quantity') and result is None:
            try:
                preview = run(request, BUILD, {'item': values['item'], 'date': values['date'],
                                               'quantity': values['quantity'], 'pending': True}, company_id, dry_run=True)
            except BookflowError as e:
                if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER', 'E_PERMISSION'):
                    raise
                error = error or e.to_dict()
        if preview:
            values = dict(values, item=preview['item_id'])
        return render('assembly_build.html', request, status_code=status_code, company=company,
                      company_id=company['company_id'], assemblies=assemblies(request, company_id),
                      values=values, preview=preview, result=result, error=error)

    @app.get('/c/{company_id}/assembly/build')
    def assembly_build_page(request: Request, company_id: str):
        try:
            values = {key: request.query_params.get(key, '') for key in ('item', 'date', 'quantity', 'memo')}
            values['quantity'] = values['quantity'] or '1'
            return build_page(request, company_id, values=values)
        except BookflowError as e:
            return page_error(request, e)

    def build_submit(request, company_id, form):
        values = {key: (form.get(key) or '').strip() for key in ('item', 'date', 'quantity', 'memo')}
        action = form.get('action')
        if action not in ('build', 'pending'):
            return build_page(request, company_id, values=values)
        raw = {'item': values['item'], 'date': values['date'], 'quantity': values['quantity'], 'pending': action == 'pending'}
        if values['memo']:
            raw['memo'] = values['memo']
        reason = (form.get('ctx:reason') or '').strip()
        try:
            out = run(request, BUILD, raw, company_id, headers={'X-Bookflow-Reason': reason} if reason else None)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                return page_error(request, e)
            return build_page(request, company_id, values=values, error=e.to_dict(), status_code=400)
        return build_page(request, company_id, values=values, result=out)

    @app.post('/c/{company_id}/assembly/build')
    async def assembly_build_submit(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(build_submit, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)

    def list_page(request, company_id, *, status, result=None, error=None, status_code=200):
        company = run(request, 'company show', {}, company_id)
        out = run(request, 'assembly list', {'status': status} if status else {}, company_id)
        return render('assembly_builds.html', request, status_code=status_code, company=company,
                      company_id=company['company_id'], listing=out, status=status or '', result=result, error=error,
                      can_write=role_allows(BUILD, company, credential(request)))

    @app.get('/c/{company_id}/assembly/builds')
    def assembly_list_page(request: Request, company_id: str):
        try:
            return list_page(request, company_id, status=request.query_params.get('status') or None)
        except BookflowError as e:
            return page_error(request, e)

    def list_submit(request, company_id, form):
        status = (form.get('status') or '').strip() or None
        verb, build = form.get('action', ''), form.get('build', '')
        reason = (form.get('ctx:reason') or '').strip()
        try:
            if verb not in ('finalize', 'void', 'delete'):
                raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'action', 'problem': 'unknown action'}]})
            out = run(request, 'assembly ' + verb, {'build': build}, company_id,
                      headers={'X-Bookflow-Reason': reason} if reason else None)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                return page_error(request, e)
            return list_page(request, company_id, status=status, error=e.to_dict(), status_code=400)
        return list_page(request, company_id, status=status, result=out)

    @app.post('/c/{company_id}/assembly/builds')
    async def assembly_list_submit(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(list_submit, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)
