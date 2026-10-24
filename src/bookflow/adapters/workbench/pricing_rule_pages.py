"""Price rules: the anchor's Advanced Pricing rule list and rule form, on the `price-rule` commands (R110).

One page lists the rules in priority order with their summaries, holds the rule form (new, or editing
the rule named by ?rule=), the company's conflict preference, and a price check that shows what a line
would cost and which rule it would come from. Every write goes through the command it names, with a
reason for the audit trail.
"""
from __future__ import annotations

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError

LISTS = ('items', 'item_categories', 'customers', 'customer_types', 'classes', 'sales_reps')
SINGLE = ('date_from', 'date_to', 'min_quantity', 'value', 'priority', 'description')
CHOICES = ('kind', 'direction', 'basis', 'rounding')


def _split(text):
    return [part.strip() for part in (text or '').split(',') if part.strip()]


def _breaks(text):
    out = []
    for part in _split(text):
        quantity, _, value = part.partition(':')
        if not value.strip():
            raise BookflowError('E_VALIDATION', message='Write quantity breaks as quantity:value, such as 10:5, 50:10.',
                                details={'fields': [{'field': 'breaks', 'problem': 'write breaks as quantity:value'}]})
        out.append({'min_quantity': quantity.strip(), 'value': value.strip()})
    return out


def rule_values(rule):
    """A rule as the form shows it."""
    values = {key: ', '.join(n['label'] for n in rule[key]) for key in LISTS}
    values.update({key: '' if rule.get(key) is None else str(rule[key]) for key in SINGLE})
    values.update({key: rule[key] for key in CHOICES})
    values['name'] = rule['name']
    values['breaks'] = ', '.join(f"{b['min_quantity']}:{b['value']}" for b in rule['breaks'] or [])
    return values


def form_input(form, *, editing):
    raw = {}
    for key in LISTS:
        if editing or _split(form.get(key)):
            raw[key] = _split(form.get(key))
    for key in SINGLE:
        text = (form.get(key) or '').strip()
        if text:
            raw[key] = int(text) if key == 'priority' and text.isdigit() else text
        elif editing and key in ('date_from', 'date_to', 'min_quantity', 'description'):
            raw[key] = None
    for key in CHOICES:
        if (form.get(key) or '').strip():
            raw[key] = form[key].strip()
    if (form.get('breaks') or '').strip():
        raw['breaks'] = _breaks(form['breaks'])
        raw.pop('value', None)
    if (form.get('name') or '').strip():
        raw['name'] = form['name'].strip()
    return raw


def mount(app, *, render, run, credential, page_error, role_allows):
    def page(request, company_id, *, values=None, result=None, error=None, status_code=200, check=None, editing=None):
        company = run(request, 'company show', {}, company_id)
        if not role_allows('price-rule list', company, credential(request)):
            raise BookflowError('E_PERMISSION', details={'capability': 'price-level', 'required_role': 'member'})
        listed = run(request, 'price-rule list', {'include_inactive': True}, company_id)
        selected = editing or request.query_params.get('rule')
        editing = next((r for r in listed['rules'] if selected in (r['id'], r['name'])), None) if selected else None
        if editing and values is None:
            values = rule_values(editing)
        return render('price_rules.html', request, status_code=status_code, company=company,
                      company_id=company['company_id'], rules=listed['rules'], policy=listed['conflict_policy'],
                      values=values or {}, result=result, error=error, check=check, editing=editing,
                      can_write=role_allows('price-rule create', company, credential(request)))

    @app.get('/c/{company_id}/price-rules')
    def price_rules_page(request: Request, company_id: str):
        try:
            return page(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    def submit(request, company_id, form):
        action = form.get('action') or ''
        headers = {'X-Bookflow-Reason': form['ctx:reason'].strip()} if (form.get('ctx:reason') or '').strip() else None
        check, editing = None, None
        try:
            if action == 'create':
                out = run(request, 'price-rule create', form_input(form, editing=False), company_id, headers=headers)
                result = f"Added price rule {out['name']}: {out['summary']}."
            elif action == 'update':
                editing = form.get('rule')
                raw = dict(form_input(form, editing=True), rule=form['rule'], expected_version=int(form['expected_version']))
                out = run(request, 'price-rule update', raw, company_id, headers=headers)
                result = f"Saved price rule {out['name']}: {out['summary']}."
                editing = None
            elif action.startswith(('activate:', 'deactivate:')):
                verb, rule, version = action.split(':', 2)
                out = run(request, f'price-rule {verb}', {'rule': rule, 'expected_version': int(version)}, company_id,
                          headers=headers)
                result = f"{'Turned on' if verb == 'activate' else 'Turned off'} price rule {out['name']}."
            elif action == 'policy':
                out = run(request, 'price-rule set-preferences', {'conflict_policy': form.get('conflict_policy')},
                          company_id, headers=headers)
                result = ('The lowest price now wins.' if out['conflict_policy'] == 'lowest_price'
                          else 'The first rule by priority now wins.')
            elif action == 'check':
                raw = {key: form[key].strip() for key in ('item', 'customer', 'quantity', 'date')
                       if (form.get(key) or '').strip()}
                check = run(request, 'price-rule preview', raw, company_id)
                result = None
            else:
                return page(request, company_id)
        except BookflowError as e:
            if e.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                return page_error(request, e)
            return page(request, company_id, values=dict(form), error=e.to_dict(), status_code=400, editing=editing)
        return page(request, company_id, result=result, check=check, values={} if check is None else dict(form))

    @app.post('/c/{company_id}/price-rules')
    async def price_rules_submit(request: Request, company_id: str):
        form = dict(await request.form())
        try:
            return await run_in_threadpool(submit, request, company_id, form)
        except BookflowError as e:
            return page_error(request, e)
