"""Guided opening balances in the browser (R107 increment 7, design/bulk-import.md §6, §9 Browser).

One page, `/c/<company>/opening`, from Company setup. With no draft it lists the drafts and starts one (the date and
the totals to check against). A draft's steps, each reachable again by its URL (`?draft=<id>&step=stock`):

- **Date and checks**: the cutover date and the totals the owner knows (receivables, payables, stock); a total left
  out is taken from the rows and labelled derived.
- **Balances you know**, **Open invoices**, **Unpaid bills**, **Stock**: each a grid that takes a paste (tab text from
  a spreadsheet, or typed with commas), its header shown to paste under, saved with `opening-draft update`; what the
  rows alone show to be wrong is listed by line at once. Stock items that are not items yet are made only when named
  in "Make these new items", the owner's confirmation.
- **Tie out and post**: the plan (`opening-draft plan`): counts, the checks given and derived apart, what blocks;
  Post (`opening-draft apply`) with one confirmation naming the date and totals; then the tie-out, given and derived
  apart, the clearing account, and Undo (`opening-draft reverse`, its plan first).

Only registry commands run, so the page does what the CLI and MCP do. Every post goes through htmx and a refusal
answers 200 in place. At 390 px the steps stack, the grids take the screen's width and nothing scrolls sideways.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.company import cutover_sources as src
from bookflow.core.errors import BookflowError

STEPS = (('date', 'Date and checks'), ('balances', 'Balances you know'), ('open_invoices', 'Open invoices'),
         ('unpaid_bills', 'Unpaid bills'), ('stock', 'Stock'), ('post', 'Tie out and post'))
GRIDS = {'balances': 'entered_balances', 'open_invoices': 'entered_open_invoices',
         'unpaid_bills': 'entered_unpaid_bills', 'stock': 'entered_stock'}
HELP = {
    'balances': 'One row per account you know: bank and card balances, loans, other assets and liabilities, as a '
                'statement shows them. A card or loan is what you owe. Leave equity out and it is worked out for you.',
    'open_invoices': 'Each invoice a customer still owes, for what is still open. A credit is a negative amount. A '
                     'customer not in your list yet is made from the name.',
    'unpaid_bills': 'Each bill you still owe, for what is still open. A vendor not in your list yet is made from the name.',
    'stock': 'Each item you hold: quantity on hand and its total value or unit cost.',
}
ACCEPTABLE = ('receivables_do_not_tie', 'payables_do_not_tie', 'inventory_does_not_tie', 'trial_balance_unbalanced')


def mount(app, *, render, run, credential, page_error, role_allows):
    def reason(form, default):
        text = (form.get('ctx:reason') or '').strip()
        return {'X-Bookflow-Reason': text or default}

    def url(company_id, draft_id=None, step=None):
        base = f"/c/{quote(company_id, safe='')}/opening"
        if not draft_id:
            return base
        return f"{base}?draft={quote(draft_id, safe='')}" + (f"&step={step}" if step else '')

    def page(request, company_id, *, draft_id=None, step=None, status_code=200, **ctx):
        shown = run(request, 'company show', {}, company_id)
        draft = run(request, 'opening-draft show', {'draft': draft_id}, company_id) if draft_id else None
        drafts = None if draft else run(request, 'opening-draft query', {}, company_id)['rows']
        if draft and step not in dict(STEPS):
            step = 'post' if draft['state'] in ('applied', 'applying', 'failed', 'reversed') else 'date'
        steps = {s['step']: s for s in (draft or {}).get('steps', [])}
        if draft and step == 'post' and 'tie' not in ctx and draft['state'] == 'applied':
            ctx['tie'] = run(request, 'opening-draft tie-out', {'draft': draft['id']}, company_id)['tie_out']
        plan = ctx.pop('plan', None) or ((draft or {}).get('summary') if draft and draft['state'] == 'planned' else None)
        blocking = list((plan or {}).get('exceptions') or [])
        response = render('opening.html', request, status_code=status_code, company=shown,
                          company_id=shown['company_id'], draft=draft, drafts=drafts, step=step, steps=STEPS,
                          grid_step=steps.get(step) if step in GRIDS else None, grids=GRIDS, help=HELP,
                          header=src.entered_header(GRIDS[step]) if step in GRIDS else None, plan=plan,
                          acceptable=ACCEPTABLE, only_acceptable=bool(plan) and not plan.get('ready') and blocking != []
                          and all(e['code'] in ACCEPTABLE for e in blocking if e['severity'] == 'blocking'),
                          can_write=role_allows('opening-draft create', shown, credential(request)), **ctx)
        if draft:
            response.headers['HX-Push-Url'] = url(shown['company_id'], draft['id'], step)
        return response

    @app.get('/c/{company_id}/opening')
    def opening_page(request: Request, company_id: str):
        try:
            q = request.query_params
            return page(request, company_id, draft_id=q.get('draft') or None, step=q.get('step'))
        except BookflowError as e:
            return page_error(request, e)

    def grid_of(form) -> dict:
        out = {'content': form.get('content', '')}
        if form.get('date_order') in ('MDY', 'DMY', 'YMD'):
            out['date_order'] = form['date_order']
        return out

    def given_of(form) -> dict:
        return {name: form[f'given_{name}'].strip() for name in ('receivables', 'payables', 'stock')
                if (form.get(f'given_{name}') or '').strip()}

    def act(request, company_id, form):
        action = form.get('action') or ''
        draft_id = form.get('draft') or None
        step = form.get('step') or None
        if action == 'start':
            made = run(request, 'opening-draft create', {'as_of': form.get('as_of', ''), 'given': given_of(form),
                                                        **({'label': form['label']} if form.get('label') else {})},
                       company_id, headers=reason(form, 'Start opening balances'))
            return page(request, company_id, draft_id=made['id'], step='balances')
        version = int(form.get('version') or 0)
        if action in ('save', 'next'):
            raw = {'draft': draft_id, 'expected_version': version}
            if step == 'date':
                raw.update(as_of=form.get('as_of', ''), given=given_of(form))
            elif step in GRIDS:
                raw[step] = grid_of(form)
                if step == 'stock':
                    raw['make_items'] = [line.strip() for line in (form.get('make_items') or '').splitlines()
                                         if line.strip()]
            run(request, 'opening-draft update', raw, company_id, headers=reason(form, 'Opening balances'))
            if action == 'next':
                keys = [k for k, _ in STEPS]
                step = keys[min(keys.index(step) + 1, len(keys) - 1)]
            return page(request, company_id, draft_id=draft_id, step=step, saved=True)
        if action == 'plan':
            out = run(request, 'opening-draft plan', {'draft': draft_id}, company_id, headers=reason(form, 'Plan the opening'))
            return page(request, company_id, draft_id=draft_id, step='post', plan=out['plan'])
        if action == 'apply':
            raw = {'draft': draft_id}
            if form.get('accept_differences') == 'on':
                raw['accept_differences'] = True
            out = run(request, 'opening-draft apply', raw, company_id, headers=reason(form, 'Post the opening balances'))
            return page(request, company_id, draft_id=draft_id, step='post', posted=out['apply'])
        if action == 'undo-plan':
            try:
                plan = run(request, 'opening-draft reverse', {'draft': draft_id}, company_id, dry_run=True)
                return page(request, company_id, draft_id=draft_id, step='post', undo={'state': 'planned', 'plan': plan})
            except BookflowError as e:
                if e.code != 'E_BATCH_BLOCKED':
                    raise
                return page(request, company_id, draft_id=draft_id, step='post',
                            undo={'state': 'blocked', 'error': e.to_dict(), 'blockers': (e.details or {}).get('blockers', [])})
        if action == 'undo':
            raw = {'draft': draft_id}
            if form.get('fingerprint'):
                raw['fingerprint'] = form['fingerprint']
            run(request, 'opening-draft reverse', raw, company_id, headers=reason(form, 'Undo the opening balances'))
            return page(request, company_id, draft_id=draft_id, step='post', undone=True)
        if action == 'discard':
            run(request, 'opening-draft discard', {'draft': draft_id}, company_id, headers=reason(form, 'Discard a draft'))
            return page(request, company_id)
        raise BookflowError('E_VALIDATION', message=f'Unknown action {action!r}.', details={'field': 'action'})

    def submit(request, company_id, form):
        try:
            return act(request, company_id, form)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                raise
            draft_id = form.get('draft') or None
            return page(request, company_id, draft_id=draft_id, step=form.get('step') or None, error=e.to_dict(),
                        typed=form.get('content') if form.get('step') in GRIDS else None)

    @app.post('/c/{company_id}/opening')
    async def opening_post(request: Request, company_id: str):
        data = await request.form()
        form = {k: v for k, v in data.multi_items() if isinstance(v, str)}
        try:
            return await run_in_threadpool(submit, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)

