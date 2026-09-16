"""Payment workspace shell; all facts and actions use the shared commands."""
from fastapi import Request

from bookflow.adapters.workbench.transaction_detail import document_noun
from bookflow.core import registry
from bookflow.core.deletion_families import PAYMENT_FAMILIES, capability
from bookflow.core.errors import BookflowError
from bookflow.core.money import Money
from urllib.parse import urlencode


def _settled_document(run, request, company_id, settlement, document_id):
    """Read the receivable a settlement belongs to, from the command that owns its type.

    A settlement names either settleable receivable -- an invoice, or a statement charge
    entered straight onto the account -- and says which, taken from the document's own row.
    The read command, the words on the page and the link out of it all come off that one
    fact. `invoice settlement` and `application show` accept both types; `invoice show`
    accepts only one, so a receipt applied to a charge used to error the whole page.

    Retained settlement history outlives the document, so the read asks for the deleted facts
    wherever that family can be deleted -- asked of the registry, the way every report row's
    own link asks it, because only some families take the flag and the rest refuse it.

    A settlement with no type named is one replayed out of operation history written before
    the type was recorded, which only reaches pages that read stored effects; a settlement
    read live always names it, and an unnamed one opens where it always did.
    """
    noun = document_noun(settlement.get('document_type')) or 'invoice'
    meta = registry.noun_meta(noun)
    fields = registry.get(noun + ' show').input_model.model_fields
    record = run(request, noun + ' show', {meta['identifier']: document_id,
        **({'include_deleted': True} if 'include_deleted' in fields else {})}, company_id)
    return record, dict(noun=noun, label=meta['singular_label'],
                        url=f"/c/{company_id}/{noun}/{document_id}")


def mount(app, *, render, run, credential, page_error, role_allows, form_page):
    @app.get('/c/{company_id}/invoice/{invoice_id}/settlement')
    def invoice_settlement(request: Request, company_id: str, invoice_id: str):
        try:
            inputs = {'invoice': invoice_id, 'limit': 50}
            for name in ('as_of', 'cursor'):
                if request.query_params.get(name):
                    inputs[name] = request.query_params[name]
            state = run(request, 'invoice settlement', inputs, company_id)
            invoice, document = _settled_document(run, request, company_id, state, invoice_id)
            for group in (state, state['all_committed_current']):
                for field in ('gross', 'applied', 'due'):
                    group[field] = Money(group[field+'_minor_units'], group['currency']).to_dict()
            for row in state['applications']:
                row['amount'] = Money(row['amount_minor_units'], row['currency']).to_dict()
            next_url = '?' + urlencode(dict(request.query_params, cursor=state['next_cursor'])) if state['next_cursor'] else None
            return render('invoice_settlement.html', request, company_id=company_id, invoice=invoice,
                          document=document, settlement=state, next_url=next_url)
        except BookflowError as exc:
            return page_error(request, exc, company_id=company_id)

    @app.get('/c/{company_id}/application/{application_id}')
    @app.get('/c/{company_id}/application/{application_id}/history')
    def application(request: Request, company_id: str, application_id: str):
        if registry.get('application ' + application_id) is not None:
            return form_page(request, company_id, 'application', application_id)
        try:
            shown = run(request, 'application show', {'application': application_id}, company_id)
            history = run(request, 'application history', {'application': application_id, 'limit': 50,
                **({'cursor': request.query_params['cursor']} if request.query_params.get('cursor') else {})}, company_id)
            record = shown['record']
            invoice, document = _settled_document(run, request, company_id,
                shown['current_invoice'], record['paid_transaction_id'])
            # Retained settlement history outlives the receipt: an application whose payer
            # was later deleted is still readable, so this read asks for the deleted facts
            # exactly as the receivable read above already does.
            payment = run(request, 'payment show', {'payment': record['paying_transaction_id'], 'include_deleted': True}, company_id)
            for row in history['items']:
                value = row.get('application') or row.get('allocation')
                if value:
                    row['amount_label'] = Money(value['amount_minor_units'], value['currency']).to_dict()['amount'] + ' ' + value['currency']
            return render('payment_application.html', request, company_id=company_id, application=shown,
                application_history=history, invoice=invoice, document=document, payment=payment,
                amount=Money(record['amount_minor_units'], record['currency']).to_dict())
        except BookflowError as exc:
            return page_error(request, exc, company_id=company_id)

    @app.get('/c/{company_id}/payment')
    def payment_list(request: Request, company_id: str):
        try:
            company = run(request, 'company show', {}, company_id)
            raw = {key: request.query_params[key] for key in ('q', 'payment_method', 'date_from', 'date_to', 'status', 'cursor')
                   if request.query_params.get(key)}
            if request.query_params.get('available'):
                raw['has_available_credit'] = True
            result = run(request, 'payment query', dict(raw, limit=25), company_id)
            for row in result['items']:
                for field in ('received', 'applied', 'unapplied'):
                    row[field] = Money(row[field + '_minor_units'], row['currency']).to_dict()['amount']
            methods = run(request, 'payment-method list', {'include_inactive': True}, company_id)['items']
            next_url = '?' + urlencode(dict(request.query_params, cursor=result['next_cursor'])) if result['next_cursor'] else None
            return render('payment_list.html', request, company_id=company_id, payments=result, methods=methods,
                filters=dict(request.query_params), next_url=next_url,
                may_receive=role_allows(registry.get('payment receive'), company, hub_admin=credential(request).hub_admin))
        except BookflowError as exc:
            return page_error(request, exc, company_id=company_id)

    @app.get('/c/{company_id}/receive-payments')
    def workspace(request: Request, company_id: str):
        try:
            company = run(request, 'company show', {}, company_id)
            cred = credential(request)
            allowed = [verb for verb in ('receive', 'apply', 'update', 'unapply', 'void')
                       if role_allows(registry.get('payment ' + verb), company, hub_admin=cred.hub_admin)]
            # Delete answers to its own explicit family grant rather than to the posting
            # role, so the workspace reads the same effective admission the Users &
            # permissions page displays instead of inferring one from the role.
            family, = PAYMENT_FAMILIES
            if any(row['requirement']['capability'] == capability(family) and row['admitted']
                   for row in run(request, 'membership effective', {'company': company_id}, None)['permissions']):
                allowed.append('delete')
            # Direct document links establish composite authority before a shell
            # or a browser's saved intent may disclose those document facts.
            payment = request.query_params.get('payment')
            selection = request.query_params.get('selection')
            # A deleted receipt keeps its links: the workspace opens it read-only.
            initial = run(request, 'payment show', {'payment': payment, 'include_deleted': True}, company_id) if payment else None
            draft = run(request, 'payment selection show', {'selection': selection}, company_id) if selection else None
            if draft and draft['context']['payment_id'] and not initial:
                initial = run(request, 'payment show', {'payment': draft['context']['payment_id'], 'include_deleted': True}, company_id)
            config = dict(company=company_id, actor=cred.user_id, allowed=allowed, initial=initial, draft=draft,
                preferences=company.get('info', {}), currency=company.get('home_currency', 'USD'),
                customer=request.query_params.get('customer'), invoice=request.query_params.get('invoice'),
                operation=request.query_params.get('operation'),
                # A deleted receipt is retained history and has no editing mode to open,
                # whatever a saved link asks for.
                mode='show' if initial and initial.get('deletion') else request.query_params.get('mode',
                    'apply' if draft and draft['context']['mode'] == 'existing_credit' else 'receive' if draft else 'show' if payment else 'receive'))
            response = render('payments.html', request, company_id=company_id, payment_workspace=config)
            response.headers['Cache-Control'] = 'no-store'
            return response
        except BookflowError as exc:
            return page_error(request, exc, company_id=company_id)

    @app.get('/c/{company_id}/payment-drafts')
    def drafts(request: Request, company_id: str):
        try:
            result = run(request, 'payment selection query', {'state': request.query_params.get('state', 'open'),
                'limit': 25, **({'cursor': request.query_params['cursor']} if request.query_params.get('cursor') else {})}, company_id)
            for row in result['items']:
                party = run(request, 'customer show', {'customer': row['context']['customer_id']}, company_id)
                row['customer_label'] = party['full_name']
            return render('payment_drafts.html', request, company_id=company_id, drafts=result)
        except BookflowError as exc:
            return page_error(request, exc, company_id=company_id)
