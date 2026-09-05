"""Foreign posting parity, authority and home-currency boundaries."""
import json

import pytest

from bookflow import BookflowError
from bookflow.company import journals
from tests.conftest import as_user, make_actor
from tests.test_row3_host import hosted  # noqa: F401
from tests.test_row8_journal import COMPANY, journal_accounts  # noqa: F401
from tests.test_foreign_journals import DATE, post, rate, entered
from tests.test_row8_custom_field_integration import complete_state


def test_cli_http_python_share_original_money_and_errors(hosted, client, cli):
    cid = hosted.company_id
    rows = [{'account':'Checking','side':'debit','amount':{'minor_units':2345,'currency':'JPY'}},
            {'account':'Service Income','side':'credit','amount':'15.95'}]
    saved = cli.json('journal','post','--company',cid,'--date',DATE,'--lines',json.dumps(rows),'--rate','0.0068')
    assert saved['total_minor_units']==1595
    public = hosted.ok('journal.show', {'journal':saved['id']}, company=cid)
    assert public['revision']['lines'][0]['original_amount']=={'amount':'2345','minor_units':2345,'currency':'JPY'}
    second = hosted.ok('journal.post', {'date':DATE,'lines':rows,'rate':'0.0068'},company=cid)
    assert second['total_minor_units']==1595
    bad = hosted.api.post(f'/companies/{cid}/commands/journal.post', json={'date':DATE,'lines':rows},headers=hosted.bearer)
    assert bad.json()['code']=='E_NO_EXCHANGE_RATE'
    standalone, exit_code = cli.error('journal','post','--company',cid,'--date',DATE,'--lines',json.dumps(rows))
    assert exit_code and standalone['code']=='E_NO_EXCHANGE_RATE'
    hosted.handle.stop()
    assert public == client.journal.show(journal=saved['id'],company=cid)


def test_non_usd_home_currency_and_readonly_isolation(client, root):
    company = client.company.new(legal_name='Yen ledger',home_currency='JPY',organization='Demo Holdings LLC')['company_id']
    saved = client.journal.post(company=company,date=DATE,rate='50',lines=[
        {'account':'Checking','side':'debit','amount':'0.03 USD'},
        {'account':'Opening Balance Equity','side':'credit','amount':'0.03 USD'}])
    assert saved['total']=={'amount':'2','currency':'JPY','minor_units':2}
    assert saved['revision']['lines'][0]['original_amount']['minor_units']==3
    make_actor(root,'foreign-reader',company_role=(company,'readonly'))
    reader=as_user(root,'foreign-reader')
    assert reader.journal.show(company=company,journal=saved['id'])['total_minor_units']==2
    with pytest.raises(BookflowError) as error:
        reader.journal.post(company=company,date=DATE,rate='50',
            lines=[{key:value for key,value in row.items() if key!='line_id'} for row in entered(saved)])
    assert error.value.code=='E_PERMISSION'
    with pytest.raises(BookflowError) as error:
        reader.journal.query(company=COMPANY)
    assert error.value.code=='E_COMPANY_NOT_FOUND'


def test_foreign_closed_period_and_register_edit_denial(client, journal_accounts):
    rate(client)
    saved=post(client,journal_accounts)
    before=complete_state(client)
    with pytest.raises(BookflowError) as error:
        client.register.update(company=COMPANY,journal=saved['id'],expected_version=1,
            selected_line_id=saved['revision']['lines'][0]['line_id'],
            category_line_id=saved['revision']['lines'][1]['line_id'],
            date=DATE,account=journal_accounts[0],category=journal_accounts[1],direction='increase',amount='15.95')
    assert error.value.code=='E_VALIDATION' and complete_state(client)==before
    client.company.update(company=COMPANY,closing_date=DATE)
    before=complete_state(client)
    for args in ({'date':'2026-04-01'},{'rate':'0.007'}):
        with pytest.raises(BookflowError) as error:
            client.journal.update(company=COMPANY,journal=saved['id'],expected_version=1,**args)
        assert error.value.code=='E_PERIOD_CLOSED' and complete_state(client)==before


def test_foreign_late_audit_failure_rolls_back_complete_write(client,journal_accounts,monkeypatch):
    rate(client)
    before=complete_state(client)
    original=journals.audit.write_event_to
    def fail(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError('late foreign audit failure')
    with monkeypatch.context() as patch:
        patch.setattr(journals.audit,'write_event_to',fail)
        with pytest.raises(RuntimeError, match='late foreign audit failure'):
            post(client,journal_accounts,idempotency_key='foreign-rollback')
    assert complete_state(client)==before
    saved=post(client,journal_accounts,idempotency_key='foreign-rollback')
    assert saved['total_minor_units']==1595
