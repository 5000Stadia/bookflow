"""Unicode attribution has an explicit, strict and backwards-compatible transport."""
from urllib.parse import quote

import pytest
from bookflow.adapters.http.app import decode_context_headers
from starlette.datastructures import Headers
from tests.test_row3_host import hosted  # noqa: F401


@pytest.mark.parametrize('encoding,value', [('unknown','text'),('percent-utf8','%'),
    ('percent-utf8','%XZ'),('percent-utf8','%FF'),('percent-utf8','%C3')])
def test_invalid_context_transport_is_rejected_before_write(hosted, encoding, value):
    before=hosted.ok('journal.query',company=hosted.company_id)['count']
    response=hosted.call('register.post', {'account':'Checking','category':'Professional Fees',
        'date':'2026-01-01','direction':'decrease','amount':'1.00'}, company=hosted.company_id,
        headers={'X-Bookflow-Context-Encoding':encoding,'X-Bookflow-Reason':value})
    assert response.status_code == 422 and response.json()['code'] == 'E_VALIDATION'
    assert hosted.ok('journal.query',company=hosted.company_id)['count'] == before


def test_encoded_context_and_ordinary_percent_header_remain_distinct(hosted, cli):
    reason='修理 receipt + 100%'
    source='工事/é😀'
    result=hosted.ok('register.post', {'account':'Checking','category':'Professional Fees',
        'date':'2026-01-01','direction':'decrease','amount':'1.00'}, company=hosted.company_id,
        headers={'X-Bookflow-Context-Encoding':'percent-utf8','X-Bookflow-Reason':quote(reason,safe=''),
                 'X-Bookflow-Source-Ref':quote(source,safe=''), 'X-Bookflow-Client-Name':quote('現場 app',safe=''),
                 'Idempotency-Key':quote('key+%literal',safe='')})
    event=hosted.ok('audit.show',{'event':result['revision']['audit_event_id']},company=hosted.company_id)
    assert (event['reason'],event['source_ref'],event['client_name']) == (reason,source,'現場 app')
    original=hosted.ok('register.post', {'account':'Checking','category':'Professional Fees',
        'date':'2026-01-01','direction':'decrease','amount':'1.00'}, company=hosted.company_id,
        headers={'X-Bookflow-Reason':'%E4%BF%AE + 100%'})
    assert hosted.ok('audit.show',{'event':original['revision']['audit_event_id']},company=hosted.company_id)['reason'] == '%E4%BF%AE + 100%'
    saved=cli.json('register','post','--account','Checking','--category','Professional Fees',
        '--date','2026-01-01','--direction','decrease','--amount','1.00','--company',hosted.company_id,
        '--reason',reason,'--source-ref',source)
    audit=hosted.ok('audit.show',{'event':saved['revision']['audit_event_id']},company=hosted.company_id)
    assert (audit['reason'],audit['source_ref']) == (reason,source)


def test_context_is_decoded_once_and_plus_is_literal():
    assert decode_context_headers(Headers({'X-Bookflow-Context-Encoding':'percent-utf8',
        'X-Bookflow-Reason':'%2520+text'}))['reason'] == '%20+text'
