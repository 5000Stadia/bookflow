"""Named statement errors and company visibility survive each existing adapter."""
import os
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest
import bookflow
from bookflow.core.errors import BookflowError
from tests.conftest import as_user,make_actor
from tests.test_row3_host import hosted,PASSWORD,WB  # noqa: F401


def test_statement_errors_match_http_browser_python_and_cli(hosted,root):
    h=hosted;cid=h.company_id
    api=TestClient(h.handle.app)
    assert api.post('/login',json={'username':h.login,'password':PASSWORD}).status_code==200
    base={'date_to':'2026-12-31','include_zero':True,'limit':1}
    first=h.ok('report.balance-sheet',base,company=cid)
    version=h.info()['info_version']
    h.ok('company.update',{'phone':'555-0109','expected_version':version},company=cid)
    cases=[('balance-sheet',{**base,'cursor':first['next_cursor']},'E_QUERY_STALE'),
           ('balance-sheet',{'date_to':'2026-12-31','basis':'cash'},'E_VALIDATION'),
           ('profit-and-loss',{'date_from':'2026-12-31','date_to':'2026-01-01'},'E_VALIDATION')]
    for _ in range(2):
        h.ok('journal.post',{'date':'2026-02-01','lines':[
            {'account':'Checking','side':'debit','amount':'46116860184273879.04'},
            {'account':'Service Income','side':'credit','amount':'46116860184273879.04'}]},company=cid)
    cases += [('balance-sheet',{'date_to':'2026-12-31'},'E_VALUE_RANGE'),
              ('profit-and-loss',{'date_from':'2026-01-01','date_to':'2026-12-31'},'E_VALUE_RANGE')]
    for name,body,code in cases:
        response=h.call('report.'+name,body,company=cid)
        assert response.json()['code']==code,response.text
        form={'f:'+key:str(value).lower() if isinstance(value,bool) else str(value) for key,value in body.items()}
        response=api.post(f'/c/{cid}/report/{name}',data=form,headers=WB)
        assert code in response.text
    h.handle.stop()
    c=bookflow.connect(data_root=str(root))
    for name,body,code in cases:
        with pytest.raises(BookflowError) as caught: c.run('report '+name,body,company=cid)
        assert caught.value.code==code
        args=[sys.executable,'-m','bookflow.adapters.cli.app','--json','report',name,'--company',cid]
        for key,value in body.items():
            if isinstance(value,bool):
                if value: args.append('--'+key.replace('_','-'))
            else: args.extend(['--'+key.replace('_','-'),str(value)])
        output=subprocess.run(args,env={**os.environ,'BOOKFLOW_DATA_ROOT':str(root)},capture_output=True,text=True)
        assert output.returncode!=0 and code in output.stdout+output.stderr


def test_readonly_statements_and_hidden_sibling(root):
    c=bookflow.connect(data_root=str(root));cid=c.company.list()['items'][0]['company_id']
    make_actor(root,'statement-reader',company_role=(cid,'readonly'))
    reader=as_user(root,'statement-reader')
    other=c.company.new(legal_name='Hidden Books',home_currency='USD',organization='Demo Holdings LLC')['company_id']
    for name,body in [('report profit-and-loss',{'date_from':'2026-01-01','date_to':'2026-12-31'}),
                      ('report balance-sheet',{'date_to':'2026-12-31'})]:
        assert reader.run(name,body,company=cid)['metadata']['company_id']==cid
        with pytest.raises(BookflowError) as caught: reader.run(name,body,company=other)
        assert caught.value.code=='E_COMPANY_NOT_FOUND'
