"""Company Users & permissions: shared typed commands, no policy calculations."""
import json
from functools import lru_cache
from fastapi import Request
from fastapi.responses import HTMLResponse
from bookflow.adapters.workbench.transaction_detail import IRREGULAR as _IRREGULAR
from bookflow.core.deletion_families import FAMILIES, TOMBSTONE_TABLE, capability
from bookflow.core.errors import BookflowError


def _detail_noun(family):
    return _IRREGULAR.get(family, family.replace('_', '-'))


# core.deletion_families owns which families exist and which of them have shipped
# retained-deletion storage. A family becomes grantable here the moment it appears
# there, so setup can never fall behind an activated Delete command.
DELETABLE = tuple(family for family in FAMILIES if family in TOMBSTONE_TABLE)
CAPS = tuple(capability(family) for family in DELETABLE)
FIELDS = tuple(family + '_delete' for family in DELETABLE)
# The page each deletable family is read on. `transaction_detail` already owns which stored
# document types are not named after their own page -- a journal entry is read at `journal`
# -- so that one mapping answers here too rather than being written out a second time.
NOUNS = tuple(_detail_noun(family) for family in DELETABLE)


@lru_cache(maxsize=1)
def grant_controls():
    """One checkbox per deletable family, named the way its own pages name it."""
    from bookflow.adapters.workbench import naming
    from bookflow.core import registry
    labels = []
    for noun in NOUNS:
        labels.append(naming.subject(noun, registry.noun_meta(noun)))
    return tuple(zip(FIELDS, labels))


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
                **{field:cap in grants for field,cap in zip(FIELDS,CAPS)},
                allow_post='ledger.post' not in denies,allow_read='ledger.read' not in denies,
                other_grants=json.dumps([x for x in grants if x not in CAPS]),
                other_denies=json.dumps([x for x in denies if x not in ('ledger.post','ledger.read')]))
        effective = run(request,'membership effective',{'company':company_id,'user':selected},None) if selected else None
        controls = grant_controls()
        return render('permissions.html',request,company_id=company_id,company_label=current['company_name'],
            current=current,rows=rows,values=values,result=result,error=error,effective=effective,
            delete_grants=controls,delete_nouns=', '.join(label.lower() for _,label in controls),
            shown_capabilities=('ledger.read','ledger.post')+CAPS,
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
        for key in FIELDS+('allow_post','allow_read'):
            values[key] = key in form
        result = error = None
        try:
            grants = json.loads(values.get('other_grants','[]'))
            denies = json.loads(values.get('other_denies','[]'))
            if not isinstance(grants,list) or not isinstance(denies,list):
                raise ValueError
            grants += [cap for cap,key in zip(CAPS,FIELDS) if values[key]]
            denies += [cap for cap,key in (('ledger.post','allow_post'),('ledger.read','allow_read')) if not values[key]]
            raw = dict(user=values['user'],company=company_id,expected_version=int(values['expected_version']))
            action = values.get('action','preview')
            if action not in ('preview','save','revoke'):
                raise ValueError
            grants = [x for x in grants if not (x in ('ledger.post','ledger.read') and x in denies)]
            denies = [x for x in denies if x not in [cap for cap,key in zip(CAPS,FIELDS) if values[key]]]
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
