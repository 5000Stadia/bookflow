"""The three credit documents in the browser: credit memo, customer refund, vendor credit.

Presentation and navigation only. Nothing here posts an entry, resolves a default or computes
an amount: every figure on every page below was returned by ``credit-memo show``,
``customer-refund show``, ``vendor-credit show``, ``invoice query`` or
``customer-credit apply``. A credit read years from now shows what its revision captured.

**What lives here and why.**

*The pickers.* ``FORM_DEFINITIONS`` says which list each control on the three document windows
searches, exactly as ``bill_contract`` does for a bill. It sits in the workbench rather than
beside the other contracts because these three nouns have no Row 5 list definition to hang it
on and the declaration is pure presentation -- ``pages`` reads it as the last fallback after a
list definition and a domain form definition.

*The two seeded openings.* A return and a refund are both written against something that
already exists, so both windows can be opened from it. ``/credit-memo/post?invoice=<id>``
opens the credit memo with one returned row per line of that invoice, already naming the
source invoice and the source line, so the only thing left to type is how much came back.
``/customer-refund/post?credit_memo=<id>`` opens the refund with that credit already in its
sources. Both seed the *attempted* controls rather than the form's originals: an original is
the baseline a correction is compared against, and a value equal to it is not submitted at
all, which would silently drop every seeded row.

*The apply surface.* ``customer-credit apply`` needs the credit's own version and the version
of every invoice it touches. A person cannot be asked to find six version numbers, so the page
reads them and carries them in its own hidden fields -- and a stale one is answered by the
command with ``E_VERSION_CONFLICT`` rather than being reconciled here.
"""
from copy import deepcopy
from dataclasses import dataclass
from urllib.parse import quote, urlencode

from bookflow.company.lists import ReferenceDefinition
from bookflow.core.money import Money
# One home for the order an address reads in, shared with the printed documents.
from bookflow.documents.model import address_lines


@dataclass(frozen=True)
class CreditFormDefinition:
    """Which list each control searches. Declaration only; it decides nothing."""

    references: tuple[ReferenceDefinition, ...]


# A credit memo is the invoice read backwards, so its pickers are the invoice's: the same
# customer list, the same items, the same classes and tax codes. `source_invoice` and
# `source_line` are deliberately absent -- an invoice is a document rather than a list, so
# there is no list command behind a picker for one. The window seeds them from the invoice
# the return was opened from instead, which is the way a return is actually written.
_CREDIT_MEMO_FORM = CreditFormDefinition(tuple(ReferenceDefinition(field, target) for field, target in (
    ('customer', 'customer'), ('ar_account', 'account'), ('class_id', 'class'),
    ('customer_tax_code', 'sales-tax-code'), ('sales_tax_item', 'item'),
    ('customer_message_item', 'customer-message'),
    ('lines.item', 'item'), ('lines.class_id', 'class'), ('lines.tax_code', 'sales-tax-code'),
)) + (ReferenceDefinition('lines.unit', 'unit-of-measure', child_units=True),))

_CUSTOMER_REFUND_FORM = CreditFormDefinition(tuple(ReferenceDefinition(field, target) for field, target in (
    ('customer', 'customer'), ('funding_account', 'account'), ('method', 'payment-method'),
    ('class_id', 'class'),
)))

_VENDOR_CREDIT_FORM = CreditFormDefinition(tuple(ReferenceDefinition(field, target) for field, target in (
    ('vendor', 'vendor'), ('ap_account', 'account'), ('class_id', 'class'),
    ('expenses.account', 'account'), ('expenses.customer', 'customer'),
    ('expenses.class_id', 'class'),
)))

FORM_DEFINITIONS = {'credit-memo': _CREDIT_MEMO_FORM,
                    'customer-refund': _CUSTOMER_REFUND_FORM,
                    'vendor-credit': _VENDOR_CREDIT_FORM}

# The three documents, by the route segment they live under.
NOUNS = ('credit-memo', 'customer-refund', 'vendor-credit')

# Which template renders each saved document.
TEMPLATES = {'credit-memo': 'credit_memo_detail.html',
             'customer-refund': 'customer_refund_detail.html',
             'vendor-credit': 'vendor_credit_detail.html'}

# The list columns each list page shows, in the order a bookkeeper reads them.
COLUMNS = {
    'credit-memo': ['number', 'date', 'customer_name', 'total', 'available', 'status'],
    'customer-refund': ['number', 'date', 'customer_name', 'payment_method_name', 'total',
                        'status'],
    'vendor-credit': ['number', 'date', 'vendor_name', 'total', 'unapplied', 'status'],
}


def _url(company_id, *parts, **query):
    path = '/c/' + quote(str(company_id), safe='') + ''.join(
        '/' + quote(str(part), safe='') for part in parts)
    kept = {key: value for key, value in query.items() if value not in (None, '')}
    return path + ('?' + urlencode(kept) if kept else '')


def list_rows(noun, items):
    """Decorate list rows with the one figure the list exists to answer.

    A credit list is opened to answer "what has this party still got in hand", so the row
    carries it. Both figures are the server's own: the credit memo's comes from the worth
    its query attached to the row, the vendor credit's from its settlement.
    """
    rows = []
    for row in items:
        extra = {}
        if noun == 'credit-memo' and isinstance(row.get('source_current'), dict):
            extra['available'] = row['source_current']['available']
        if noun == 'vendor-credit' and isinstance(row.get('settlement_current'), dict):
            extra['unapplied'] = row['settlement_current']['unapplied']
        rows.append({**row, **extra})
    return rows


def _links(company_id, noun, record, preview):
    """Revision arrows for a document that keeps more than one revision."""
    if preview:
        return []
    revision = record['revision']
    url = _url(company_id, noun, record['id'])
    number = revision['revision_number']
    links = []
    if number > 1:
        links.append(('Previous revision', url + '?revision_number=' + str(number - 1)))
    if revision['id'] != record['current_revision_id']:
        links += [('Next revision', url + '?revision_number=' + str(number + 1)),
                  ('Current revision', url)]
    if noun == 'credit-memo':
        links.append(('History', url + '/history'))
    return links


def _issuer(revision):
    issuer = revision.get('issuer_snapshot') or {}
    return issuer, address_lines({key.removeprefix('address_'): value
                                  for key, value in issuer.items()
                                  if key.startswith('address_')})


def detail_context(noun, record, company_id, *, preview=False):
    """Template context for one saved credit document, with safe local links only."""
    record = deepcopy(record)
    revision = record['revision']
    issuer, issuer_address = _issuer(revision)
    view = dict(record=record, revision=revision, profile=revision['profile'], preview=preview,
                links=_links(company_id, noun, record, preview), issuer=issuer,
                issuer_address=issuer_address, noun=noun,
                template=TEMPLATES[noun])
    if noun == 'credit-memo':
        source = record.get('source_current') or {}
        # Every returned line of one credit memo comes from the same invoice, so the document
        # says which invoice once rather than repeating an id down the grid.
        returned_from = next((line['source_transaction_id'] for line in revision['lines']
                              if line.get('source_transaction_id')), None)
        # A preview has no saved document behind it, so it carries no link into one.
        saved = None if preview else record['id']
        view.update(source=source,
                    returned_from=_url(company_id, 'invoice', returned_from) if returned_from else None,
                    customer_url=_url(company_id, 'customer', record['customer_id']),
                    apply_url=_url(company_id, 'credit-memo', saved, 'apply') if saved else None,
                    refund_url=(_url(company_id, 'customer-refund', 'post', credit_memo=saved)
                                if saved else None),
                    spendable=bool(source.get('available_minor_units'))
                              and record['status'] != 'voided' and not preview,
                    title='Credit memo')
    elif noun == 'customer-refund':
        # The captured source rows hold minor units alone, so the display figure is made here
        # from the document's own currency rather than being recomputed from anything else.
        currency = record['currency']
        sources = []
        for row in revision['profile']['sources']:
            sources.append({**row, 'url': _url(company_id, 'credit-memo', row['credit_memo_id']),
                            'amount': _money(row['amount_minor_units'], currency),
                            'available_before': _money(row['available_minor_units'], currency)})
        view.update(sources=sources, consumptions=record.get('consumptions') or [],
                    customer_url=None if preview else _url(company_id, 'customer', record['customer_id']),
                    title='Customer refund')
    else:
        applications = [row for row in (record.get('applications') or []) if row.get('active')]
        view.update(settlement=record.get('settlement_current') or {},
                    applications=[{**row,
                                   'url': _url(company_id, 'bill', row['obligation_transaction_id'])}
                                  for row in applications],
                    vendor_url=None if preview else _url(company_id, 'vendor', record['vendor_id']),
                    title='Vendor credit')
    return view


# ---------------------------------------------------------------- the seeded openings


def return_rows(invoice):
    """Attempted controls that open a credit memo as a return of one posted invoice.

    One row per line of the invoice, each naming the source invoice and the source line and
    nothing else: what came back is a quantity a person types, and every cent of a returned
    line is priced from what that invoice captured rather than from anything entered here.
    """
    attempted = {'f:customer': str(invoice['customer_id']),
                 'f:ar_account': str(invoice['revision']['profile']['control_account']['id']),
                 'collection:lines': '1'}
    index = 0
    for line in invoice['revision']['lines']:
        if line.get('pricing_basis') == 'allocated':
            # A line billed from quoted work is priced on its source; the return path for
            # one is the source document, not this window.
            continue
        attempted[f'c:lines:{index}:source_invoice'] = str(invoice['id'])
        attempted[f'c:lines:{index}:source_line'] = str(line['line_id'])
        attempted[f'c:lines:{index}:quantity'] = str(line['quantity'])
        index += 1
    if not index:
        return {}
    return attempted


def untouched_return_defaults(raw):
    """Drop the empty `use_defaults` the grid submits on a row that is a return.

    Every row panel renders its `use_defaults` collection with its own marker, so an untouched
    row arrives carrying an empty list rather than carrying nothing at all. A returned line
    refuses any mention of `use_defaults` -- its price and its tax are the source invoice's --
    so an empty one has to go, or the browser could never save a return at all. A non-empty one
    stays and is refused by the command, because that one a person chose.
    """
    for row in raw.get('lines') or []:
        if isinstance(row, dict) and row.get('source_invoice') and not row.get('use_defaults'):
            row.pop('use_defaults', None)
    return raw


def refund_rows(credits):
    """Attempted controls that open a refund paying out the credits it was opened from."""
    attempted = {'collection:sources': '1'}
    for index, credit in enumerate(credits):
        attempted[f'c:sources:{index}:credit_memo'] = str(credit['id'])
        attempted[f'c:sources:{index}:amount'] = credit['source_current']['available']['amount']
    return attempted


def refund_note(credits):
    """What the seeded rows are, in words, because the rows themselves carry only ids."""
    if not credits:
        return None
    parts = ['{} · {} · {} {} still available'.format(
        credit['number'], credit['date'], credit['source_current']['available']['amount'],
        credit['source_current']['available']['currency']) for credit in credits]
    return ('Paying back ' + ('this credit: ' if len(parts) == 1 else 'these credits: ')
            + '; '.join(parts) + '. Lower an amount to pay back only part of one, or remove '
            'its row to leave it for an invoice.')


# ---------------------------------------------------------------- applying and unapplying


def _money(amount_minor_units, currency):
    """One display figure from the server's own minor units. Nothing here recomputes money."""
    return Money(amount_minor_units, currency).to_dict()


def apply_context(credit, invoices, standing, company_id):
    """Everything the apply page shows, and every version it has to carry.

    ``invoices`` are this customer's posted invoices as ``invoice query`` returned them, each
    carrying its own settlement; ``standing`` are the applications this credit still has on
    them. Every amount is the server's, and every version is the one the read answered with.
    """
    source = credit.get('source_current') or {}
    available = int(source.get('available_minor_units') or 0)
    currency = credit['currency']
    rows = []
    for invoice in invoices:
        settlement = invoice.get('settlement_current') or {}
        due = int(settlement.get('due_minor_units') or 0)
        if invoice['status'] == 'voided' or due <= 0 or settlement.get('currency') != currency:
            continue
        rows.append({
            'id': invoice['id'], 'number': invoice['number'], 'date': invoice['date'],
            'due_date': invoice.get('due_date'), 'total': invoice['total'],
            'version': settlement['version'],
            'due': _money(due, settlement['currency']),
            'suggested': _money(min(due, available), settlement['currency']) if available else None,
            'url': _url(company_id, 'invoice', invoice['id']),
        })
    return dict(credit=credit, source=source, rows=rows, standing=standing,
                currency=currency, available=source.get('available'),
                credit_url=_url(company_id, 'credit-memo', credit['id']),
                apply_url=_url(company_id, 'credit-memo', credit['id'], 'apply'),
                unapply_url=_url(company_id, 'credit-memo', credit['id'], 'unapply'),
                spendable=available > 0 and credit['status'] != 'voided')


def applications_of(credit_id, invoice, settlement):
    """The applications this credit still has standing on one invoice.

    An application that something reverses is gone; the row that reverses it is not a thing a
    person takes back. Both facts come from the settlement read itself.
    """
    reversed_ids = {row['reverses_application_id'] for row in settlement['applications']
                    if row['reverses_application_id']}
    standing = []
    for row in settlement['applications']:
        if (row['kind'] != 'apply' or row['paying_transaction_id'] != credit_id
                or row['id'] in reversed_ids):
            continue
        standing.append({
            'application_id': row['id'], 'invoice_id': invoice['id'],
            'invoice_number': invoice['number'], 'invoice_version': settlement['version'],
            'amount': _money(row['amount_minor_units'], row['currency']),
            'effective_date': row['effective_date'],
        })
    return standing


def apply_request(credit, form):
    """Translate the ticked rows into exactly the command input an agent would send."""
    applications = []
    for key, value in form.items():
        if not key.startswith('select:') or value != '1':
            continue
        invoice_id = key.removeprefix('select:')
        item = {'invoice': invoice_id, 'expected_version': int(form['version:' + invoice_id])}
        amount = (form.get('amount:' + invoice_id) or '').strip()
        if amount:
            item['amount'] = amount
        applications.append(item)
    raw = {'credit_memo': credit['id'], 'expected_version': credit['version'],
           'applications': applications}
    date = (form.get('date') or '').strip()
    if date:
        raw['date'] = date
    return raw


def unapply_request(credit, form):
    references = []
    for key, value in form.items():
        if not key.startswith('unapply:') or value != '1':
            continue
        application_id = key.removeprefix('unapply:')
        references.append({'application_id': application_id,
                           'invoice_expected_version': int(form['invoice-version:' + application_id])})
    return {'credit_memo': credit['id'], 'expected_version': credit['version'],
            'applications': references}


def mount(app, *, render, run, credential, page_error, role_allows):
    """Install the credit apply/unapply surface.

    It is mounted before the generated `<noun>/<record>/<verb>` route, which would otherwise
    read `apply` as a command name on the credit memo and answer `unknown command`.
    """
    from fastapi import Request
    from fastapi.responses import RedirectResponse, Response
    from starlette.concurrency import run_in_threadpool

    from bookflow.core import registry
    from bookflow.core.errors import BookflowError

    INVOICE_PAGE = 50

    def _state(request, company_id, credit_id):
        credit = run(request, 'credit-memo show', {'credit_memo': credit_id}, company_id)
        invoices = run(request, 'invoice query',
                       {'customer': credit['customer_id'], 'status': 'posted',
                        'limit': INVOICE_PAGE, 'direction': 'asc'}, company_id)['items']
        standing = []
        for invoice in invoices:
            settlement = invoice.get('settlement_current') or {}
            if not settlement.get('applied_minor_units'):
                continue
            read = run(request, 'invoice settlement', {'invoice': invoice['id'], 'limit': 200},
                       company_id)
            standing += applications_of(credit['id'], invoice, read)
        return credit, invoices, standing

    def _page(request, company_id, credit_id, *, error=None, status_code=200):
        credit, invoices, standing = _state(request, company_id, credit_id)
        company = run(request, 'company show', {}, company_id)
        cred = credential(request)
        view = apply_context(credit, invoices, standing, company_id)
        applier = registry.get('customer-credit apply')
        view['may_apply'] = applier is not None and role_allows(applier, company,
                                                                hub_admin=cred.hub_admin)
        return render('credit_apply.html', request, status_code=status_code,
                      company_id=company_id, apply=view, error=error)

    def _done(request, company_id, credit_id):
        location = _url(company_id, 'credit-memo', credit_id)
        if request.headers.get('hx-request', '').lower() == 'true':
            return Response(status_code=200, headers={'HX-Redirect': location})
        return RedirectResponse(location, status_code=303)

    @app.get('/c/{company_id}/credit-memo/{credit_id}/apply')
    def credit_apply_page(request: Request, company_id: str, credit_id: str):
        try:
            return _page(request, company_id, credit_id)
        except BookflowError as exc:
            return page_error(request, exc, company_id=company_id)

    async def _form(request):
        return {key: str(value) for key, value in (await request.form()).items()}

    # A missing hidden version or a selection the page never rendered is the one problem this
    # surface can hit that the command cannot describe: the command never sees the request.
    LOST = BookflowError('E_VALIDATION', details={'fields': [
        {'field': 'applications',
         'problem': 'A selected row lost its saved version. Reload this page and choose again.'}]})

    def _settle(request, company_id, credit_id, form, *, verb):
        """Send one apply or unapply, or come back to the page carrying what refused it."""
        try:
            credit = run(request, 'credit-memo show', {'credit_memo': credit_id}, company_id)
            build = apply_request if verb == 'apply' else unapply_request
            raw = build(credit, form)
            if not raw['applications']:
                raise BookflowError('E_VALIDATION', details={'fields': [
                    {'field': 'applications',
                     'problem': ('Tick at least one invoice.' if verb == 'apply'
                                 else 'Tick at least one application.')}]})
            headers = {'X-Bookflow-Reason': form['reason']} if form.get('reason') else None
            run(request, 'customer-credit ' + verb, raw, company_id, headers)
        except (BookflowError, KeyError, ValueError) as exc:
            exc = exc if isinstance(exc, BookflowError) else LOST
            # A credential failure is not a form problem: it must carry its own status, or a
            # page POST without the workbench header would answer 200 and look accepted.
            if exc.code in ('E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER'):
                return page_error(request, exc)
            try:
                return _page(request, company_id, credit_id, error=exc.to_dict())
            except BookflowError as inner:
                return page_error(request, inner, company_id=company_id)
        return _done(request, company_id, credit_id)

    @app.post('/c/{company_id}/credit-memo/{credit_id}/apply')
    async def credit_apply_submit(request: Request, company_id: str, credit_id: str):
        form = await _form(request)
        return await run_in_threadpool(_settle, request, company_id, credit_id, form, verb='apply')

    @app.post('/c/{company_id}/credit-memo/{credit_id}/unapply')
    async def credit_unapply_submit(request: Request, company_id: str, credit_id: str):
        form = await _form(request)
        return await run_in_threadpool(_settle, request, company_id, credit_id, form,
                                       verb='unapply')


def editable_values(record):
    """Project credit correction controls from captured facts, without resolving them."""
    from bookflow.adapters.workbench import sales
    from bookflow.company.credit_models import CreditMemoUpdateInput
    baseline = sales.editable_values(record)
    result = {key: value for key, value in baseline.items()
              if key in CreditMemoUpdateInput.model_fields}
    result['ar_account'] = record['revision']['profile']['control_account']['id']
    result['lines'] = []
    for line, ordinary in zip(record['revision']['lines'], baseline['lines'], strict=True):
        if line['source_transaction_id']:
            result['lines'].append(dict(
                line_id=line['line_id'], source_invoice=line['source_transaction_id'],
                source_line=line['source_line_id'], quantity=line['quantity'],
                description=line['description']))
        else:
            from bookflow.company.credit_models import CreditLineInput
            result['lines'].append({key: value for key, value in ordinary.items()
                                    if key in CreditLineInput.model_fields})
    return result


def preserve_line_origins(raw, originals):
    from bookflow.adapters.workbench import sales
    result = deepcopy(raw)
    ordinary = [row for row in result.get('lines', []) if not row.get('source_invoice')]
    if ordinary:
        cleaned = sales.preserve_line_origins({'lines': ordinary}, originals)['lines']
        iterator = iter(cleaned)
        result['lines'] = [row if row.get('source_invoice') else next(iterator)
                           for row in result['lines']]
    return result
