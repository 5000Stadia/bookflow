"""Saved check/card presentation from captured command output, without new defaults."""
from copy import deepcopy
import json

from bookflow.core.errors import BookflowError
from bookflow.core.money import Money


# The three documents that are stored as journal entries and are addressed as themselves.
# A transfer is here because the journal editor now refuses it by name: a page that still
# offered `journal update` on one would offer a button the core declines.
OWNING_NOUNS = (('check', 'check'), ('card-charge', 'card_charge'), ('transfer', 'transfer'))


def owning_record(run, company_id, transaction_id, revision_number=None):
    """Resolve only explicit money-out markers through the owning read commands."""
    from bookflow.core import registry
    for noun, selector in OWNING_NOUNS:
        fields = registry.get(noun + ' show').input_model.model_fields
        raw = {selector: transaction_id}
        if 'include_deleted' in fields:
            raw['include_deleted'] = True
        if revision_number is not None:
            raw['revision_number'] = revision_number
        try:
            return noun, run(noun + ' show', raw, company_id)
        except BookflowError as error:
            if error.code != 'E_RECORD_NOT_FOUND':
                raise
    return None


def _party(line):
    return dict(name_type=line['name_type'], name_id=line['name_id']) if line['name_id'] else None


def editable_values(record):
    revision, document = record['revision'], record['document']
    funding = document.get('funding_details') or revision['lines'][0]
    values = dict(account=document['account_id'], pay_to=_party(funding),
        date=revision['date'], amount=document['amount']['amount'], memo=revision['memo'],
        custom_fields={f['definition_id']: deepcopy(f['value']) for f in revision.get('custom_fields', [])},
        expenses=[], items=[])
    if document['kind'] == 'check':
        values['number'] = document['check_number']
    # There is no separately captured header class. Display each resolved line class
    # explicitly, so replacing a grid cannot reclassify its untouched neighbours.
    item_ids = {item['line_id'] for item in document['items']}
    for line in revision['lines'][1:]:
        if line['line_id'] in item_ids:
            continue
        values['expenses'].append(dict(line_id=line['line_id'], account=line['account_id'],
            amount=line['amount']['amount'], memo=line['description'], party=_party(line),
            class_id=line['class_id'], class_mode='value' if line['class_id'] else 'none'))
    for item in document['items']:
        profile = item['profile']
        row = dict(line_id=item['line_id'], item=profile['item']['id'],
            description=item['description'], quantity=item['quantity'],
            customer=profile['customer']['id'] if profile['customer'] else None,
            billable=profile['billable'],
            class_id=profile['class_id']['id'] if profile['class_id'] else None,
            class_mode='value' if profile['class_id'] else 'none')
        if profile['amount_basis'] == 'amount':
            row['amount'] = item['amount']['amount']
        else:
            row['unit_cost'] = Money(profile['unit_cost_minor_units'], document['currency']).to_dict()['amount']
        values['items'].append(row)
    return values


def detail_context(record):
    document = record['document']
    ids = {item['line_id'] for item in document['items']}
    funding = deepcopy(document.get('funding_details') or record['revision']['lines'][0])
    if isinstance(funding.get('account_snapshot'), str):
        funding['account_snapshot'] = json.loads(funding['account_snapshot'])
    return dict(document=document, funding=funding, revision=record['revision'],
        title='Check' if document['kind'] == 'check' else 'Credit card charge',
        items=[dict(item, unit_cost=Money(item['profile']['unit_cost_minor_units'], document['currency']).to_dict()
                    if item['profile']['unit_cost_minor_units'] is not None else None)
               for item in document['items']],
        expenses=[line for line in record['revision']['lines'][1:] if line['line_id'] not in ids])


def delete_allowed(run, company_id, noun, record):
    """Ask the real command, including activation and bound-principal admission."""
    if record.get('deletion'):
        return False
    try:
        run(noun+' delete', {noun.replace('-','_'): record['id'], 'expected_version':record['version']},
            company_id, headers={'X-Bookflow-Reason':'Preview purchase deletion'}, dry_run=True)
        return True
    except BookflowError as error:
        # Business refusals belong on confirmation; they do not remove authority.
        return error.code in ('E_PERIOD_CLOSED','E_RECONCILIATION_DEPENDENCY','E_DEPOSIT_DEPENDENCY','E_VALIDATION','E_VERSION_CONFLICT','E_HAS_APPLICATIONS','E_SOURCE_CORRECTION_CONFLICT')


def install_deletion(app, *, run, render, page_error):
    from fastapi import Request
    from fastapi.responses import RedirectResponse, Response
    from bookflow.core.ids import new_id

    def redirect(request,location):
        if request.headers.get('hx-request','').lower()=='true':
            return Response(status_code=200,headers={'HX-Redirect':location})
        return RedirectResponse(location,status_code=303)

    def routes(noun):
        selector=noun.replace('-','_')
        is_sale=noun in ('invoice','sales-receipt')
        is_bill=noun=='bill'
        is_journal=noun=='journal'
        label='sale' if is_sale else 'bill' if is_bill else 'journal entry' if is_journal else 'purchase'
        template=('sales_delete.html' if is_sale else 'bill_delete.html' if is_bill
                  else 'journal_delete.html' if is_journal else 'purchase_delete.html')
        def display(request,company_id,record_id,values=None,result=None,error=None):
            record=run(request,noun+' show',{selector:record_id,'include_deleted':True},company_id)
            if record.get('deletion') and error is None:
                return redirect(request,f'/c/{company_id}/{noun}/{record_id}?include_deleted=1')
            if values is None and not delete_allowed(lambda *a,**kw:run(request,*a,**kw),company_id,noun,record):
                raise BookflowError('E_PERMISSION')
            values=values if values is not None else dict(expected_version=record['version'],operation_key=new_id(),reason='')
            from bookflow.adapters.workbench import bills as Bills, sales as Sales
            # A journal entry has no document wrapper of its own: `journal_detail.html`
            # renders the record it was handed, which is what the confirmation shows.
            detail = (None if is_journal else Bills.detail_context(record,company_id) if is_bill
                      else Sales.detail_context(record,company_id) if is_sale else detail_context(record))
            if is_sale: detail.update(print_url=None,credit_url=None,links=[])
            # A confirmation page is one document at one version: revision arrows here
            # would move the record out from under the version the form is holding.
            if is_bill: detail.update(links=[])
            return render(template,request,company_id=company_id,noun=noun,record=record,
                purchase=None if is_bill or is_journal else detail,sale=detail if is_sale else None,
                bill=detail if is_bill else None,values=values,result=result,error=error,
                status_code=409 if error and error['code']=='E_VERSION_CONFLICT' else 400 if error else 200)
        def get(company_id:str,record_id:str,request:Request):
            try:return display(request,company_id,record_id)
            except BookflowError as error:return page_error(request,error,company_id=company_id)
        async def post(company_id:str,record_id:str,request:Request):
            values=dict(await request.form())
            try:
                action=values.get('action')
                if action not in ('preview','delete','refresh'):
                    raise BookflowError('E_VALIDATION',message=f'Choose Preview, Delete {label} or Reload current {label}.')
                if action=='refresh':
                    current=run(request,noun+' show',{selector:record_id,'include_deleted':True},company_id)
                    values['expected_version']=current['version']
                    values.pop('confirmed',None)
                    return display(request,company_id,record_id,values)
                if action=='delete' and values.get('confirmed')!='yes':
                    raise BookflowError('E_VALIDATION',message=f'Confirm cancellation before deleting this {label}.')
                try:version=int(values.get('expected_version',''))
                except ValueError:raise BookflowError('E_VALIDATION',message=f'Reload the {label} to read its current version.') from None
                result=run(request,noun+' delete',{selector:record_id,'expected_version':version,
                    'operation_key':values.get('operation_key') or None},company_id,
                    headers={'X-Bookflow-Reason':values.get('reason','')},dry_run=action=='preview')
                if action=='delete':return redirect(request,f'/c/{company_id}/{noun}?deleted={record_id}')
                return display(request,company_id,record_id,values,result=result)
            except BookflowError as error:
                try:return display(request,company_id,record_id,values,error=error.to_dict())
                except BookflowError as refused:return page_error(request,refused,company_id=company_id)
        def history(company_id:str,record_id:str):
            return RedirectResponse(f'/c/{company_id}/{noun}/{record_id}?include_deleted=1&history=1',status_code=303)
        app.add_api_route(f'/c/{{company_id}}/{noun}/{{record_id}}/delete',get,methods=['GET'])
        app.add_api_route(f'/c/{{company_id}}/{noun}/{{record_id}}/delete',post,methods=['POST'])
        app.add_api_route(f'/c/{{company_id}}/{noun}/{{record_id}}/history',history,methods=['GET'])
    for noun in ('check','card-charge','invoice','sales-receipt','bill','journal'):routes(noun)
