"""Paying bills in the browser: the payables mirror of the customer payment workspace.

Three pages and no business logic. The Pay Bills window is a shell whose facts and writes are
the shared commands; the list and the detail page present exactly what ``bill payment query``
and ``bill payment show`` returned. Nothing here resolves a default, decides what a selection
settles or computes an amount.

**The grouping a person has to see before they save.** ``bill pay`` groups the bills it is
handed by ``(vendor, payable account, currency, funding account, method)`` and writes one
payment per group, so two vendors are never paid by one check. The funding account, the method
and the currency are one choice for the whole window, which leaves the vendor and the payable
as the pair that actually splits a selection -- that pair is ``GROUP_FIELDS``, and the window
reads it off the rows ``bill query`` returned rather than deriving a second opinion about it.

**Open balance comes from the bill, not from the aging report.** ``report unpaid-bills`` still
reads a literal zero for what has been applied, so a bill it lists as open may already be paid.
``bill query`` carries each bill's own ``settlement_current``, which is what a settlement wrote,
so that is what the window selects from.
"""
from copy import deepcopy
from urllib.parse import quote, urlencode

# One home for the order an address reads in, shared with the printed documents.
from bookflow.documents.model import address_lines

# What splits one ``bill pay`` into several payments once the window's own single funding
# account, method and currency are held constant. Asked of the rows rather than assumed.
GROUP_FIELDS = ('vendor_id', 'ap_account_id')

LIST_FILTERS = ('vendor', 'bill', 'status', 'date_from', 'date_to', 'number', 'check_number')


def _url(company_id, *parts, **query):
    path = '/c/' + quote(str(company_id), safe='') + ''.join(
        '/' + quote(str(part), safe='') for part in parts)
    kept = {key: value for key, value in query.items() if value not in (None, '')}
    return path + ('?' + urlencode(kept) if kept else '')


def list_view(result, company_id, filters):
    """Rows for the bill payment list, each carrying where to open it."""
    rows = []
    for row in result['items']:
        settlement = row['settlement_current']
        rows.append({**row, 'detail_url': _url(company_id, 'bill-payment', row['id']),
                     'vendor_url': _url(company_id, 'vendor', row['vendor_id']),
                     'applied': settlement['applied']['amount'],
                     'unapplied': settlement['unapplied']['amount'],
                     'settlement_status': settlement['status']})
    next_url = None
    if result['next_cursor']:
        next_url = _url(company_id, 'bill-payment', **dict(filters, cursor=result['next_cursor']))
    return dict(rows=rows, count=result['count'], next_url=next_url,
                restart_url=_url(company_id, 'bill-payment', **filters))


def detail_context(record, company_id):
    """Template context for one saved bill payment, including safe local links."""
    record = deepcopy(record)
    revision = record['revision']
    lines = []
    for line in revision['lines']:
        lines.append({**line, 'bill_url': _url(company_id, 'bill', line['bill_id'])
                      if line['bill_id'] else None})
    issuer = revision.get('issuer_snapshot') or {}
    return dict(record=record, revision=revision, profile=revision['profile'], lines=lines,
                settlement=record['settlement_current'],
                applications=record['applications'],
                vendor_url=_url(company_id, 'vendor', record['vendor_id']),
                list_url=_url(company_id, 'bill-payment'),
                pay_url=_url(company_id, 'pay-bills'),
                issuer=issuer,
                issuer_address=address_lines({key.removeprefix('address_'): value
                                              for key, value in issuer.items()
                                              if key.startswith('address_')}))


def mount(app, *, render, run, credential, page_error, role_allows, form_page):
    from fastapi import Request

    from bookflow.core import registry
    from bookflow.core.errors import BookflowError

    @app.get('/c/{company_id}/pay-bills')
    def pay_bills_window(request: Request, company_id: str):
        try:
            company = run(request, 'company show', {}, company_id)
            cred = credential(request)
            allowed = [name for name in ('bill pay', 'bill payment show', 'bill payment query')
                       if role_allows(registry.get(name), company, hub_admin=cred.hub_admin)]
            config = dict(company=company_id, actor=cred.user_id, allowed=allowed,
                          currency=company.get('home_currency', 'USD'),
                          vendor=request.query_params.get('vendor'),
                          group_fields=list(GROUP_FIELDS))
            response = render('pay_bills.html', request, company_id=company_id,
                              pay_bills=config)
            response.headers['Cache-Control'] = 'no-store'
            return response
        except BookflowError as exc:
            return page_error(request, exc, company_id=company_id)

    @app.get('/c/{company_id}/bill-payment')
    def bill_payment_list(request: Request, company_id: str):
        try:
            company = run(request, 'company show', {}, company_id)
            filters = {key: request.query_params[key] for key in LIST_FILTERS
                       if request.query_params.get(key)}
            raw = dict(filters, limit=25, direction='desc')
            if request.query_params.get('cursor'):
                raw['cursor'] = request.query_params['cursor']
            result = run(request, 'bill payment query', raw, company_id)
            cred = credential(request)
            return render('bill_payment_list.html', request, company_id=company_id,
                          payments=list_view(result, company_id, filters), filters=filters,
                          may_pay=role_allows(registry.get('bill pay'), company,
                                              hub_admin=cred.hub_admin))
        except BookflowError as exc:
            if exc.code == 'E_QUERY_STALE':
                return render('error.html', request, status_code=409, company_id=company_id,
                              error={**exc.to_dict(), 'message': 'The list changed while you were '
                                     'browsing. Restart to see current results.'},
                              status=409, restart_url=_url(company_id, 'bill-payment'))
            return page_error(request, exc, company_id=company_id)

    @app.get('/c/{company_id}/bill-payment/{payment_id}')
    def bill_payment_detail(request: Request, company_id: str, payment_id: str):
        # `bill payment` is a two-word noun, so its own segment is `bill-payment` and this
        # window sits on the path the generated pages also address. A segment that names one
        # of the noun's verbs is that verb's form, exactly as the deposit window reads it.
        if registry.get('bill payment ' + payment_id) is not None:
            return form_page(request, company_id, 'bill payment', payment_id, None)
        try:
            shown = run(request, 'bill payment show', {'payment': payment_id}, company_id)
            return render('bill_payment_detail.html', request, company_id=company_id,
                          payment=detail_context(shown, company_id))
        except BookflowError as exc:
            return page_error(request, exc, company_id=company_id)
