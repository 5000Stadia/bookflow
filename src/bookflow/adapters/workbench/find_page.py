"""The Find page: `transaction find` as a filter form above its rows; every figure comes from the command.

Reached from the header (the "Find transactions" link) at ``/c/<company>/find``. The filters are
plain query parameters, so a search is a link that can be kept or shared, and each row opens the
document's own page.
"""

from urllib.parse import urlencode

from bookflow.adapters.workbench import routing as Routing
from bookflow.company.transaction_find import FAMILIES
from bookflow.core.errors import BookflowError

TEXT = ('name', 'number', 'amount', 'amount_from', 'amount_to', 'date_from', 'date_to', 'memo', 'account', 'cursor')
STATUS = {'open': 'Open', 'paid': 'Paid', 'posted': 'Posted', 'closed': 'Closed', 'void': 'Void'}


def inputs(params) -> tuple[dict, dict]:
    """(the command's input, the form's values) from the page's query parameters."""
    form = {name: (params.get(name) or '').strip() for name in TEXT}
    form['types'] = [value for value in params.getlist('types') if value in FAMILIES]
    # The form sends a hidden 0 before the checkbox's 1, so a cleared box still says so.
    voided = params.getlist('include_voided')
    form['include_voided'] = not voided or '1' in voided
    raw = {name: value for name, value in form.items() if name in TEXT and value}
    if form['types']:
        raw['types'] = form['types']
    if not form['include_voided']:
        raw['include_voided'] = False
    return raw, form


def view(result, form, company_id):
    rows = []
    for item in result['items']:
        noun = FAMILIES[item['type']][1]
        rows.append({**item, 'url': f"/c/{company_id}/{Routing.segment(noun)}/{item['id']}",
                     'status_label': STATUS[item['status']]})
    following = None
    if result['next_cursor']:
        query = [(name, value) for name, value in form.items() if name in TEXT and name != 'cursor' and value]
        query += [('types', value) for value in form['types']]
        if not form['include_voided']:
            query.append(('include_voided', '0'))
        following = f'/c/{company_id}/find?' + urlencode(query + [('cursor', result['next_cursor'])])
    return {**result, 'rows': rows, 'next_url': following}


def install(app, *, host, run, render, page_error, may=None):
    from fastapi import Request
    from fastapi.responses import HTMLResponse
    from bookflow.core.permission_package import read_endpoint

    @app.get('/c/{company_id}/find', response_class=HTMLResponse)
    @read_endpoint(host)
    def find_page(company_id: str, request: Request):
        """Find any transaction: the filters, and the documents they match."""
        try:
            show = run(request, 'company show', {}, company_id)
        except BookflowError as error:
            return page_error(request, error)
        if may is not None and not may(request, show, 'transaction find'):
            # A reader who may not search transactions (a POS clerk) gets the refusal page up front.
            return page_error(request, BookflowError('E_PERMISSION', message=(
                'Finding transactions needs permission to read the books here.'),
                details={'capability': 'ledger.read', 'required_role': 'member'}), company_id=show['company_id'])
        raw, form = inputs(request.query_params)
        found, error = None, None
        if request.query_params:
            try:
                found = view(run(request, 'transaction find', raw, company_id), form, show['company_id'])
            except BookflowError as refused:
                error = refused.to_dict()
        return render('find.html', request, company=show, company_id=show['company_id'], form=form, found=found,
                      error=error, families=[(key, label) for key, (label, _noun) in FAMILIES.items()])
