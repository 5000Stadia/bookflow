"""Company Users & permissions: shared typed commands, no policy calculations."""
import json
from fastapi import Request
from fastapi.responses import HTMLResponse
from bookflow.core.errors import BookflowError


CAPS = ('transaction.check.delete','transaction.card_charge.delete')


def install(app, *, run, render, page_error):
    def view(request,company_id,values=None,result=None,error=None):
        current = run(request,'membership effective',{'company':company_id},None)
        rows = run(request,'membership list',{'company':company_id,'include_inactive':True},None)['items']
        selected = (values or {}).get('user') or request.query_params.get('user')
        member = next((row for row in rows if row['scope_type']=='company' and row['scope_id']==company_id and
                       selected in (row['user_id'],row['username'])),None)
        if values is None:
            grants,denies = (member['grants'],member['denies']) if member else ([],[])
            values = dict(user=selected or '',role=member['role'] if member else 'standard',
                expected_version=member['version'] if member else 0,
                check_delete=CAPS[0] in grants,card_delete=CAPS[1] in grants,
                allow_post='ledger.post' not in denies,allow_read='ledger.read' not in denies,
                other_grants=json.dumps([x for x in grants if x not in CAPS]),
                other_denies=json.dumps([x for x in denies if x not in ('ledger.post','ledger.read')]))
        effective = run(request,'membership effective',{'company':company_id,'user':selected},None) if selected else None
        return render('permissions.html',request,company_id=company_id,company_label=current['company_name'],
            current=current,rows=rows,values=values,result=result,error=error,effective=effective,
            status_code=409 if error and error['code']=='E_VERSION_CONFLICT' else 400 if error else 200)

    @app.get('/c/{company_id}/users',response_class=HTMLResponse)
    def company_users(company_id: str, request: Request):
        try:
            return view(request,company_id)
        except BookflowError as exc:
            return page_error(request,exc,company_id=company_id)

    @app.post('/c/{company_id}/users',response_class=HTMLResponse)
    async def update_company_users(company_id: str, request: Request):
        form = await request.form()
        values = dict(form)
        for key in ('check_delete','card_delete','allow_post','allow_read'):
            values[key] = key in form
        result = error = None
        try:
            grants = json.loads(values.get('other_grants','[]'))
            denies = json.loads(values.get('other_denies','[]'))
            if not isinstance(grants,list) or not isinstance(denies,list):
                raise ValueError
            grants += [cap for cap,key in zip(CAPS,('check_delete','card_delete')) if values[key]]
            denies += [cap for cap,key in (('ledger.post','allow_post'),('ledger.read','allow_read')) if not values[key]]
            raw = dict(user=values['user'],company=company_id,expected_version=int(values['expected_version']))
            action = values.get('action','preview')
            if action not in ('preview','save','revoke'):
                raise ValueError
            grants = [x for x in grants if not (x in ('ledger.post','ledger.read') and x in denies)]
            denies = [x for x in denies if x not in [cap for cap,key in zip(CAPS,('check_delete','card_delete')) if values[key]]]
            if action != 'revoke':
                raw.update(role=values['role'],grants=grants,denies=denies)
            result = run(request,'membership revoke' if action=='revoke' else 'membership grant',raw,None,
                headers={'X-Bookflow-Reason':values.get('reason') or None},dry_run=action=='preview')
            if action != 'preview':
                values['expected_version'] = result['version']
        except (ValueError,KeyError,TypeError):
            error = BookflowError('E_VALIDATION',message='Choose a user, role and observed membership version.').to_dict()
        except BookflowError as exc:
            error = exc.to_dict()
        try:
            return view(request,company_id,values,result,error)
        except BookflowError as exc:
            return page_error(request,exc,company_id=company_id)
