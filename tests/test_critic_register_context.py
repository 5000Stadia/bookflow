from urllib.parse import quote
import pytest
from starlette.datastructures import Headers
from bookflow.adapters.http.app import decode_context_headers
from tests.test_row3_host import hosted

FIELDS={'reason':'X-Bookflow-Reason','source_ref':'X-Bookflow-Source-Ref',
        'directive_id':'X-Bookflow-Directive','idempotency_key':'Idempotency-Key',
        'client_name':'X-Bookflow-Client-Name','client_version':'X-Bookflow-Client-Version'}

def test_all_six_headers_decode_once_without_touching_identity():
    original='現場 + %E4%BF%AE 😀'
    headers=Headers({**{h:quote(original,safe='') for h in FIELDS.values()},
        'X-Bookflow-Context-Encoding':'percent-utf8','Authorization':'Bearer untouched%25',
        'X-Bookflow-Company':'untouched%25'})
    assert decode_context_headers(headers)=={f:original for f in FIELDS}
    assert headers['Authorization']=='Bearer untouched%25'
    assert headers['X-Bookflow-Company']=='untouched%25'

@pytest.mark.parametrize('header',FIELDS.values())
def test_malformed_each_context_header_has_no_audit_or_ledger_effect(hosted,header):
    cid=hosted.company_id
    before=hosted.ok('audit.tail',{},company=cid)['high_water']
    body={'account':'Checking','category':'Professional Fees','date':'2026-04-01',
          'direction':'decrease','amount':'2.35'}
    response=hosted.call('register.post',body,company=cid,headers={
        'X-Bookflow-Context-Encoding':'percent-utf8',header:'%C0%AF'})
    assert response.status_code==422 and response.json()['code']=='E_VALIDATION'
    assert hosted.ok('audit.tail',{},company=cid)['high_water']==before
