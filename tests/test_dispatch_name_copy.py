import json
from bookflow.core import registry
from bookflow.company import info
from tests.test_service_sales_lifecycle import COMPANY

def test_failed_copy_then_writable_open(client, monkeypatch, tmp_path):
    captured=[]
    def watch(name, call):
        command=registry.get(name); original=command.plan
        def plan(inp,ctx,s):
            captured.append(dict(hub=s.company_row['display_name'],session=s.company_info_row['display_name'],stored=s.company.raw.execute('SELECT display_name FROM company_info').fetchone()[0]))
            return original(inp,ctx,s)
        with monkeypatch.context() as patch:
            patch.setattr(command,'plan',plan)
            return call()
    watch('company show',lambda:client.run('company show',{},company=COMPANY))
    before=captured[-1]
    def fail(*args,**kwargs):raise OSError('owned informational copy fault')
    with monkeypatch.context() as patch:
        patch.setattr(info,'write_display_name_copy',fail)
        renamed=client.run('company rename',{'name':'Issuer copy recovery witness','move':False},company=COMPANY)
    company=renamed['company_id']
    watch('company show',lambda:client.run('company show',{},company=company))
    readonly=captured[-1]
    watch('account create',lambda:client.account.create(name='Issuer recovery witness equity',type='equity',company=company))
    writable=captured[-1]
    (tmp_path/'copy-refresh.json').write_text(json.dumps(dict(before=before,readonly=readonly,writable=writable),indent=2))
    assert readonly['hub']=='Issuer copy recovery witness'
    assert readonly['session']==readonly['stored']==before['session']
    assert writable['stored']==writable['hub']=='Issuer copy recovery witness'
    assert writable['session']==writable['stored']==writable['hub']
