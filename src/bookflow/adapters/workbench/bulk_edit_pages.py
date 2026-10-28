"""Editing many records at once in the browser (R107 increment 6, design/bulk-import.md §7, §9 Browser).

One page, `/c/<company>/bulk-edit?kind=item`, in three steps, each reachable again by its URL:

- **Grid**: the list's records (its search text, inactive ones on request) with the chosen columns as cells to type
  in; a column operation ("Price up 5 percent, round to 0.05"); a pasted block. At 390 px each record is a card.
- **Changes**: the diff the preview stored, each record's fields before and after, money totals, and each record its
  update refused; one confirmation naming the count ("Change 20 items").
- **Done**: each record's outcome (merged, conflict, refused named), then Undo: the reverse plan first (`bulk-edit
  reverse` dry run), a blocked plan naming each record changed since with the choice to leave those, and one
  confirmation.

The page runs only the registry's commands (`bulk-edit columns`, `grid`, `preview`, `apply`, `reverse`, `show`), so
it does what the CLI and MCP do. Every post goes through htmx (cookie writes need the header only htmx sends), and a
refusal answers 200 with the error in place. Only edited cells are sent: a cell gets its form name when it changes,
so a 2,000-row grid posts what was typed, not 6,000 fields.
"""
from __future__ import annotations

from urllib.parse import quote, urlencode

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from bookflow.core.errors import BookflowError

KINDS = (('item', 'Items'), ('customer', 'Customers and jobs'), ('vendor', 'Vendors'), ('account', 'Accounts'))
OPS = (('percent', 'Up or down by a percent'), ('add', 'Add an amount'), ('multiply', 'Multiply by'),
       ('set', 'Set every one to'), ('clear', 'Clear'), ('copy_down', 'Copy the first one down'),
       ('replace', 'Find and replace'), ('set_from', 'Copy from another column'))
STEPS = (('grid', 'Grid'), ('changes', 'Changes'), ('done', 'Done'))
MAX_FIELDS = 20_000  # edited cells (each one field) and the page's own
OUTCOMES = {'updated': 'Changed', 'merged': 'Changed (merged with another edit)', 'conflict': 'Not changed: edited '
            'meanwhile', 'refused': 'Not changed: refused', 'not_run': 'Not run', 'change': 'Will change',
            'rolled_back': 'Put back'}


def mount(app, *, render, run, credential, page_error, role_allows):
    def company(request, company_id):
        return run(request, 'company show', {}, company_id)

    def reason(form, default):
        text = (form.get('ctx:reason') or '').strip()
        return {'X-Bookflow-Reason': text or default}

    def kind_of(value):
        return value if value in dict(KINDS) else 'item'

    def selection(source) -> dict:
        out = {}
        if (source.get('q') or '').strip():
            out['query'] = source['q'].strip()
        if source.get('inactive') in ('on', '1', 'true'):
            out['include_inactive'] = True
        return out

    def columns_of(source, defaults) -> list[str]:
        ticked = source.getlist('col') if hasattr(source, 'getlist') else []
        picked = list(ticked) or [c for c in (source.get('columns') or '').split(',') if c]
        return picked or defaults

    def page(request, company_id, *, kind, step='grid', status_code=200, **ctx):
        shown = company(request, company_id)
        cols = run(request, 'bulk-edit columns', {'kind': kind}, company_id)['columns']
        defaults = [c['field'] for c in cols if c['default']]
        source = ctx.pop('source', request.query_params)
        chosen = columns_of(source, defaults)
        if step == 'grid' and 'grid' not in ctx:
            ctx['grid'] = run(request, 'bulk-edit grid', {'kind': kind, 'columns': chosen, **selection(source)},
                              company_id)
        result = ctx.get('result')
        response = render('bulk_edit.html', request, status_code=status_code, company=shown,
                          company_id=shown['company_id'], kind=kind, kinds=KINDS, kind_label=dict(KINDS)[kind],
                          columns=cols, chosen=chosen, by_field={c['field']: c for c in cols}, ops=OPS, step=step,
                          steps=STEPS, outcomes=OUTCOMES, q=source.get('q') or '',
                          inactive=source.get('inactive') in ('on', '1', 'true'),
                          can_write=role_allows('bulk-edit preview', shown, credential(request)), **ctx)
        run_id = (result or {}).get('run', {}).get('id') if result else None
        if run_id:  # a reload comes back to this edit
            response.headers['HX-Push-Url'] = f"/c/{quote(shown['company_id'], safe='')}/bulk-edit?run={run_id}"
        return response

    def run_page(request, company_id, run_id, **ctx):
        result = run(request, 'bulk-edit show', {'run': run_id, 'limit': 5000}, company_id)
        state = result['run']['state']
        step = 'changes' if state == 'previewed' else 'done'
        return page(request, company_id, kind=kind_of(result['run']['kind']), step=step, result=result,
                    source={}, **ctx)

    @app.get('/c/{company_id}/bulk-edit')
    def bulk_edit_page(request: Request, company_id: str):
        try:
            if request.query_params.get('run'):
                return run_page(request, company_id, request.query_params['run'])
            return page(request, company_id, kind=kind_of(request.query_params.get('kind')))
        except BookflowError as e:
            return page_error(request, e)

    def changes_of(form) -> list[dict]:
        out = []
        for key, value in form.items():
            if key.startswith('cell:'):
                _, record, field = key.split(':', 2)
                out.append({'record': record, 'field': field, 'value': value})
        return out

    def operation_of(form) -> list[dict]:
        op = form.get('op') or ''
        field = form.get('op_field') or ''
        if not op or not field:
            return []
        out = {'op': op, 'field': field}
        if op == 'set':
            out['value'] = form.get('op_value', '')
        elif op in ('add', 'multiply', 'percent'):
            out['by'] = (form.get('op_by') or '').strip()
            if (form.get('op_round') or '').strip():
                out['round'] = form['op_round'].strip()
        elif op == 'replace':
            out['find'] = form.get('op_find', '')
            out['replace'] = form.get('op_replace', '')
        elif op == 'set_from':
            out['from'] = form.get('op_from', '')
        return [out]

    def act(request, company_id, form):
        action = form.get('action') or 'preview'
        kind = kind_of(form.get('kind'))
        run_id = form.get('run') or ''
        if action == 'grid':
            return page(request, company_id, kind=kind, source=form)
        if action == 'preview':
            raw = {'kind': kind, **selection(form), 'changes': changes_of(form), 'operations': operation_of(form)}
            if (form.get('paste') or '').strip():
                raw['paste'] = form['paste']
                raw['paste_columns'] = columns_of(form, [])
            if not (raw['changes'] or raw['operations'] or raw.get('paste')):
                raise BookflowError('E_VALIDATION', message='Type in a cell, choose a column change or paste cells '
                                    'first.', details={'fields': [{'field': 'op', 'problem': 'nothing to change'}]})
            out = run(request, 'bulk-edit preview', raw, company_id,
                      headers=reason(form, f'Edit {dict(KINDS)[kind].lower()} together'))
            return run_page(request, company_id, out['run']['id'])
        if action == 'apply':
            run(request, 'bulk-edit apply', {'run': run_id}, company_id, headers=reason(form, 'Apply a grid edit'))
            return run_page(request, company_id, run_id)
        if action == 'undo-plan':
            try:
                plan = run(request, 'bulk-edit reverse', {'run': run_id}, company_id, dry_run=True)
                return run_page(request, company_id, run_id, undo={'state': 'planned', 'plan': plan})
            except BookflowError as e:
                if e.code != 'E_BATCH_BLOCKED':
                    raise
                skip = run(request, 'bulk-edit reverse', {'run': run_id, 'skip_changed': True}, company_id,
                           dry_run=True) if all(b.get('code') in ('edited_again', 'later_batch')
                                                for b in (e.details or {}).get('blockers', [])) else None
                return run_page(request, company_id, run_id, undo={'state': 'blocked', 'error': e.to_dict(),
                                                                   'blockers': (e.details or {}).get('blockers', []),
                                                                   'skip_plan': skip})
        if action == 'undo':
            raw = {'run': run_id}
            if form.get('skip_changed') == 'on':
                raw['skip_changed'] = True
            if form.get('fingerprint'):
                raw['fingerprint'] = form['fingerprint']
            out = run(request, 'bulk-edit reverse', raw, company_id, headers=reason(form, 'Undo a grid edit'))
            return run_page(request, company_id, run_id, undo={'state': 'done', 'result': out})
        raise BookflowError('E_VALIDATION', message=f'Unknown action {action!r}.', details={'field': 'action'})

    def submit(request, company_id, form):
        try:
            return act(request, company_id, form)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                raise
            error = e.to_dict()
            run_id = form.get('run') or ''
            if run_id:
                return run_page(request, company_id, run_id, error=error)
            kind = kind_of(form.get('kind'))
            return page(request, company_id, kind=kind, source=form, error=error,
                        typed={k: v for k, v in form.items() if k.startswith('cell:')},
                        values={k: v for k, v in form.items() if k.startswith('op') or k in ('paste', 'ctx:reason')})

    @app.post('/c/{company_id}/bulk-edit')
    async def bulk_edit_post(request: Request, company_id: str):
        try:
            data = await request.form(max_fields=MAX_FIELDS)
        except HTTPException as e:
            if e.status_code != 400:
                raise
            error = BookflowError('E_VALIDATION', message=f'Too many cells at once ({e.detail}); change fewer.',
                                  details={'fields': [{'field': 'cells', 'problem': 'too many'}]})
            return await run_in_threadpool(page, request, company_id, kind='item', error=error.to_dict())
        form = {k: v for k, v in data.multi_items() if isinstance(v, str)}
        try:
            return await run_in_threadpool(submit, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)


def grid_url(company_id: str, kind: str, **params) -> str:
    query = urlencode({'kind': kind, **{k: v for k, v in params.items() if v}})
    return f'/c/{quote(company_id, safe="")}/bulk-edit?{query}'
