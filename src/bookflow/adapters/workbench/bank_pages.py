"""Bank inbox: command outputs rendered as cards and sorting sheets, with no accounting logic."""
from urllib.parse import urlencode

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse
from starlette.datastructures import FormData, QueryParams

from bookflow.adapters.workbench import home
from bookflow.core.errors import BookflowError

FILTERS = ('account', 'state', 'date_from', 'date_to', 'text', 'amount', 'cursor', 'import_id')
ACTS = ('add', 'match', 'ignore', 'transfer', 'unmatch', 'deposit', 'pay-bills')


def mount(app, *, render, run, credential, page_error, role_allows):
    def page(request, cid, source=None, *, error=None, result=None, preview=None):
        source = request.query_params if source is None else source
        if not hasattr(source, 'getlist'):
            source = FormData(source)
        company = run(request, 'company show', {}, cid)
        permits = lambda cmd: role_allows(cmd.name, company, credential(request))
        allowed = {act: role_allows('bank-line ' + act, company, credential(request)) for act in ACTS}
        for act, commands in {'add': ('check post', 'deposit post', 'card-charge post', 'card-credit post'),
                           'transfer': ('transfer post',), 'match': ('register query',),
                           'deposit': ('deposit sources', 'deposit post'),
                           'pay-bills': ('bill query', 'payment-method list', 'bill pay')}.items():
            allowed[act] = allowed[act] and all(role_allows(name, company, credential(request)) for name in commands)
        query = {k: source[k] for k in FILTERS if source.get(k)}
        inbox = run(request, 'bank-line query', {**query, 'limit': 100}, cid)
        accounts = run(request, 'account list', {}, cid)['items']
        imports = run(request, 'bank-feed query', {**({'account': query['account']} if query.get('account') else {}),
                                                  'limit': 50}, cid)
        selected = next((line for line in inbox['lines'] if line['id'] == source.get('line')), None)
        mode = source.get('mode', 'add')
        if selected and not allowed.get(mode, False):
            raise BookflowError('E_PERMISSION', message='You may not perform this bank line action.')
        extras = {}
        if selected and mode == 'match':
            extras['register'] = run(request, 'register query', {
                'account': selected['account_id'], 'date_from': source.get('search_from') or selected['date'],
                'date_to': source.get('search_to') or selected['date'], 'limit': 100,
                **({'cursor': source['search_cursor']} if source.get('search_cursor') else {})}, cid)
        if selected and mode == 'deposit':
            extras['sources'] = run(request, 'deposit sources', {'date': selected['date'], 'limit': 100,
                **({'cursor': source['source_cursor']} if source.get('source_cursor') else {})}, cid)
        if selected and mode == 'pay-bills':
            extras['bills'] = run(request, 'bill query', {'status': 'posted', 'limit': 100,
                **({'cursor': source['bill_cursor']} if source.get('bill_cursor') else {})}, cid)
            extras['methods'] = run(request, 'payment-method list', {}, cid)['items']
        # Combine only pairs the command has already identified, when both ends are on this page.
        by_id = {line['id']: line for line in inbox['lines']}
        shown, used = [], set()
        for line in inbox['lines']:
            if line['id'] in used:
                continue
            pair_id = line.get('pair_line_id')
            for proposal in line['proposals']:
                if proposal['kind'] == 'transfer' and proposal['unique'] and len(proposal['line_ids']) == 1:
                    pair_id = proposal['line_ids'][0]
                    break
            pair = by_id.get(pair_id)
            if pair and pair['id'] not in used:
                if line['amount'] > 0:
                    line, pair = pair, line
                line = {**line, 'shown_pair': pair}
                used.add(pair['id'])
            used.add(line['id'])
            shown.append(line)
        inbox = {**inbox, 'lines': shown}
        def url(**changes):
            mode_path = '/' + changes.pop('mode') if changes.get('mode') in ACTS else ''
            return '/c/' + cid + '/bank-inbox' + mode_path + '?' + urlencode({k: v for k, v in {**query, **changes}.items() if v})
        response = render('bank_inbox.html', request, company=company, company_id=cid, inbox=inbox,
                      accounts=accounts, imports=imports, filters=query, selected=selected, mode=mode,
                      values=source, allowed=allowed, error=error, result=result, preview=preview,
                      bank_url=url, may_open=lambda path: home.page_open(cid, path, permits) is True, **extras)
        response.headers['HX-Push-Url'] = url(**({'line': selected['id'], 'mode': mode} if selected else {}))
        return response

    @app.get('/c/{company_id}/bank-inbox')
    def inbox(request: Request, company_id: str):
        try:
            if request.query_params.get('mode') in ACTS:
                return RedirectResponse('/c/' + company_id + '/bank-inbox/' + request.query_params['mode']
                                        + '?' + urlencode(dict(request.query_params)), status_code=303)
            return page(request, company_id)
        except BookflowError as exc:
            return page_error(request, exc)

    @app.get('/c/{company_id}/bank-inbox/{mode}')
    def sort_sheet(request: Request, company_id: str, mode: str):
        try:
            if mode not in ACTS:
                raise BookflowError('E_VALIDATION', message='Choose a bank line action.')
            source = QueryParams({**dict(request.query_params), 'mode': mode})
            return page(request, company_id, source)
        except BookflowError as exc:
            return page_error(request, exc)

    def act(request, cid, form):
        if form.get('browse_source') or form.get('browse_bill'):
            cursor_key = 'source_cursor' if form.get('browse_source') else 'bill_cursor'
            cursor = form.get('browse_source') or form.get('browse_bill')
            source = FormData([(k, v) for k, v in form.multi_items() if k != cursor_key] + [(cursor_key, cursor)])
            return page(request, cid, source)
        action = form.get('action', '')
        if action not in ACTS:
            raise BookflowError('E_VALIDATION', message='Choose a bank line action.')
        ids = form.getlist('selected')
        raw = {'line': form['line']} if form.get('line') else {'lines': (
            [{'line': line} for line in ids] if action in ('add', 'match') else ids)}
        fields = {
            'add': ('payee', 'payee_type', 'category', 'class_id', 'memo', 'number'),
            'match': ('entry',), 'ignore': ('ignore_reason', 'note'),
            'transfer': ('pair', 'to', 'entry'), 'unmatch': (),
            'deposit': ('adjustment_account', 'adjustment_payee', 'adjustment_payee_type',
                        'adjustment_memo', 'adjustment_class_id', 'memo'),
            'pay-bills': ('method', 'check_number', 'memo', 'discount_account'),
        }[action]
        for key in fields:
            if form.get(key):
                raw['account' if key == 'category' else key] = form[key]
        if action == 'add':
            raw['always'] = form.get('always') == 'on'
            raw['splits'] = [{'account': form[f'split_account:{i}'], 'amount': form.get(f'split_amount:{i}', ''),
                              **({'class_id': form[f'split_class:{i}']} if form.get(f'split_class:{i}') else {})}
                             for i in range(6) if form.get(f'split_account:{i}')]
        if action == 'deposit':
            if not raw.get('adjustment_payee'):
                raw.pop('adjustment_payee_type', None)
            raw['payments'] = form.getlist('payment')
        if action == 'pay-bills':
            raw['bills'] = [{'bill': bill, **{key: form[f'{key}:{bill}'] for key in ('amount', 'discount')
                                            if form.get(f'{key}:{bill}')}} for bill in form.getlist('bill')]
        dry = form.get('preview') == 'yes'
        out = run(request, 'bank-line ' + action, raw, cid, dry_run=dry,
                  headers={'X-Bookflow-Reason': form.get('ctx:reason') or 'Sort bank inbox'})
        if dry:
            return page(request, cid, form, preview=out)
        kept = {k: form[k] for k in FILTERS if form.get(k)}
        return page(request, cid, kept, result=out)

    def submit(request, cid, form):
        try:
            return act(request, cid, form)
        except BookflowError as exc:
            if exc.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                raise
            return page(request, cid, form, error=exc.to_dict())

    @app.post('/c/{company_id}/bank-inbox')
    async def post(request: Request, company_id: str):
        form = await request.form(max_fields=20_000)
        try:
            return await run_in_threadpool(submit, request, company_id, form)
        except BookflowError as exc:
            return page_error(request, exc)
