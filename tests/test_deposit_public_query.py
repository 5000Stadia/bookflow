"""Public query correctness over actual saved deposits, without browser activation."""
import pytest

from tests.test_deposit_command import books, COMPANY  # noqa: F401
from tests.test_deposit_port_denials import world, reading, set_denies  # noqa: F401
from bookflow.core.errors import BookflowError
from bookflow.company import deposit_public_reads as reads, deposit_read_models as m


def post(books, key, amount='25.00', date='2026-06-03'):
    return books['client'].run('deposit post', dict(operation_key=key, document=dict(
        mode='inline', deposit_to=books['bank'], date=date, memo='Counted cash '+key,
        additional=[dict(received_from=dict(kind='customer',id=books['customer']),
            from_account=books['income'], amount=amount)])), company=COMPANY)


def query(books, **args):
    return books['client'].run('deposit query', args, company=COMPANY)


@pytest.mark.timeout(120)
def test_public_query_totals_filters_sort_and_cursor_replay(books):
    a=post(books,'query-a','30.00','2026-06-02')
    b=post(books,'query-b','25.00')
    c=post(books,'query-c','10.00','2026-06-04')
    client=books['client']
    args=dict(deposit=b['deposit']['id'],expected_version=1,operation_key='query-void')
    preview=client.run('deposit void',args,company=COMPANY,reason='Void duplicate',dry_run=True)
    client.run('deposit void',dict(args,dependency_guard=preview['dependency_guard']),company=COMPANY,reason='Void duplicate')
    first=query(books,page=dict(limit=1))
    assert first['total_count']==3 and first['totals']['bank_total']['minor_units']==6500
    assert first['effective_bank_total']['minor_units']==4000
    assert first['items'][0]['current']['deposit_id']==c['deposit']['id']
    cursor=first['next_cursor']
    assert query(books)['items']==query(books,sort='bank_total',direction='asc')['items']
    # The relation's current order can coincide: direction and sort remain bound.
    for changes in (dict(sort='bank_total',direction='asc'),dict(direction='asc')):
        with pytest.raises(BookflowError) as error:query(books,**changes,page=dict(limit=1,cursor=cursor))
        assert error.value.code=='E_QUERY_STALE'
    second=query(books,page=dict(limit=1,cursor=cursor))
    assert second['totals']==first['totals'] and second['total_count']==3
    previous=query(books,page=dict(limit=1,cursor=second['previous_cursor']))
    assert previous==first
    wider=query(books,page=dict(limit=2,cursor=cursor))
    assert len(wider['items'])==2 and wider['totals']==first['totals']
    voided=query(books,status='voided')
    assert voided['total_count']==1 and voided['totals']['bank_total']['minor_units']==2500
    assert voided['effective_bank_total']['minor_units']==0
    exact=query(books,number=a['deposit']['number'],deposit_to=books['bank'],date_from='2026-06-02',date_to='2026-06-02',q='Ada')
    assert exact['total_count']==1 and exact['items'][0]['received_from'][0]['label']=='Ada Waterworks'
    shown=client.run('deposit show',dict(deposit=a['deposit']['id']),company=COMPANY)
    assert shown['totals']==exact['items'][0]['totals']
    with pytest.raises(BookflowError) as error:query(books,status='deleted')
    assert error.value.code=='E_VALIDATION'


@pytest.mark.timeout(120)
def test_actual_reference_redaction_and_connected_denial_are_distinct(world):
    with reading(world) as (s,a,b):
        original=reads.query(s,m.QueryInput(),audience=a)
        bank=original.items[0].selected.deposit_to.id
    set_denies(world,('account',))
    try:
        with reading(world) as (s,a,b):
            redacted=reads.query(s,m.QueryInput(),audience=a)
            assert redacted.total_count==original.total_count==1
            assert redacted.totals==original.totals and redacted.effective_bank_total==original.effective_bank_total
            ref=redacted.items[0].selected.deposit_to
            assert not ref.disclosed and ref.id is None and ref.name is None
            for selector in (bank,'A bank that does not exist'):
                with pytest.raises(BookflowError) as error:reads.query(s,m.QueryInput(deposit_to=selector),audience=a)
                assert error.value.code=='E_PERMISSION'
    finally:set_denies(world,())
    set_denies(world,('customer-work',))
    try:
        with reading(world) as (s,a,b):
            denied=reads.query(s,m.QueryInput(page=m.PageInput(limit=1)),audience=a)
            assert denied.total_count==0 and denied.items==() and denied.totals.bank_total.minor_units==0
    finally:set_denies(world,())


@pytest.mark.timeout(60)
def test_unresolved_candidate_is_not_an_omitted_deposit(books,monkeypatch):
    from bookflow.company import deposit_read_authority
    post(books,'query-unresolved')
    def unresolved(*args,**kwargs):
        raise BookflowError('E_PERMISSION')
    monkeypatch.setattr(deposit_read_authority,'admit',unresolved)
    with pytest.raises(BookflowError) as error:query(books,number='Does not match')
    assert error.value.code=='E_DEPOSIT_SOURCE_INVALID'


@pytest.mark.timeout(60)
def test_missing_stored_operation_evidence_fails_even_nonmatching_filter(books,monkeypatch):
    from tests.test_deposit_dependency_binding import observe
    from bookflow.storage.engine import open_database
    good=post(books,'query-good')
    damaged=post(books,'query-damaged')
    path=observe(books['client'],monkeypatch,lambda s:s.company.path,company=COMPANY)
    # Owned disposable DB fault: same missing index/malformed request that a
    # broad E_PERMISSION catch used to mistake for an inaccessible aggregate.
    with open_database(path,writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE')
        # Bypass immutable-write triggers only in this owned corruption fixture.
        for (name,) in db.raw.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('deposit_operations','deposit_operation_targets')").fetchall():
            db.raw.execute('DROP TRIGGER "'+name.replace('"','""')+'"')
        db.raw.execute('DELETE FROM deposit_operation_targets WHERE operation_id=?',(damaged['operation_id'],))
        db.raw.execute("UPDATE deposit_operations SET request_snapshot='{}' WHERE id=?",(damaged['operation_id'],))
        db.raw.execute('COMMIT')
    assert books['client'].run('deposit show',dict(deposit=good['deposit']['id']),company=COMPANY)['deposit_id']==good['deposit']['id']
    for filters in ({},dict(number=good['deposit']['number']),dict(q='nonmatching')):
        with pytest.raises(BookflowError) as error:query(books,**filters)
        assert error.value.code=='E_DEPOSIT_SOURCE_INVALID' and not error.value.details


@pytest.mark.timeout(120)
def test_actual_hosted_query_company_cursor_and_retained_release(books,tmp_path):
    from tests.test_deposit_command import ORGANIZATION
    from tests.conftest import Cli, make_actor, as_user
    from tests.test_row3_host import Hosted
    from bookflow.commands.host_cmds import start_serving
    from bookflow.core.context import Context, client_version
    from bookflow.core import registry
    from bookflow.adapters.http.app import Credential
    from bookflow.adapters.http.execution import run_hosted
    from bookflow.core.publication import PublicationPermit
    from bookflow.hub import credentials as auth
    from bookflow.storage.engine import open_database
    client=books['client'];root=tmp_path/'root'
    post(books,'hosted-query-a');post(books,'hosted-query-b')
    cid=client.company.list()['items'][0]['company_id']
    other=client.company.new(legal_name='Other query books',home_currency='USD',organization=ORGANIZATION)['company_id']
    make_actor(root,'query-outsider',company_role=(other,'standard'))
    outsider=as_user(root,'query-outsider').token.issue(label='Query outsider')
    token=client.token.issue(label='Retained query')
    handle=start_serving(root,client_version(),bind='127.0.0.1:8765',secure_cookies=False)
    try:
        hosted=Hosted(handle,root,'',cid,token,'',{})
        first=hosted.ok('deposit.query',dict(page=dict(limit=1)),company=cid)
        assert first['total_count']==2 and first['totals']['bank_total']['minor_units']==5000
        assert hosted.ok('deposit.query',{},company=other)['total_count']==0
        wrong=hosted.call('deposit.query',dict(page=dict(limit=1,cursor=first['next_cursor'])),company=other)
        assert wrong.status_code==422 and wrong.json()['code']=='E_VALIDATION'
        denied=hosted.call('deposit.query',{},company=cid,headers={'Authorization':'Bearer '+outsider['secret']})
        assert denied.json()['code']=='E_COMPANY_NOT_FOUND'
        assert Cli(root).json('deposit','query','--company',cid)['total_count']==2
        with open_database(root/'hub.db',writable=False) as db:
            row=auth.resolve_token(db,token['secret'])
        credential=Credential(row['user_id'],row['id'],row['kind'],row['label'],hub_admin=True,secret=token['secret'])
        document=run_hosted(handle.host,registry.get('deposit query'),{},Context.new('http','query retained witness'),credential,cid,'option',False)
        retained=PublicationPermit.from_retained(document.permit.retained())
        retained.check(handle.host,credential)
        hosted.ok('token.revoke',dict(token=token['token_id']))
        with pytest.raises(BookflowError) as error:retained.check(handle.host,credential)
        assert error.value.code in ('E_PERMISSION','E_UNAUTHENTICATED')
    finally:handle.stop()


@pytest.mark.timeout(60)
def test_full_revision_and_effective_totals_with_fee_cashback_and_zero_bank(books):
    client=books['client']
    expense=client.account.create(name='Query fees',type='expense',company=COMPANY)['id']
    till=client.account.create(name='Query till',type='other_current_asset',company=COMPANY)['id']
    for index,cash in enumerate(('7.00','97.00')):
        client.run('deposit post',dict(operation_key='query-cashback-'+str(index),document=dict(
            mode='inline',deposit_to=books['bank'],date='2026-06-03',additional=[
                dict(received_from=dict(kind='customer',id=books['customer']),from_account=books['income'],amount='100.00'),
                dict(received_from=dict(kind='customer',id=books['customer']),from_account=expense,amount='-3.00')],
            cash_back=dict(account=till,amount=cash))),company=COMPANY)
    result=query(books,page=dict(limit=1))
    assert result['total_count']==2 and len(result['items'])==1
    assert {key:value['minor_units'] for key,value in result['totals'].items()}==dict(
        posting_total=20000,source_total=0,positive_additional_total=20000,
        negative_additional_total=-600,subtotal=19400,cash_back=10400,bank_total=9000)
    assert result['effective_bank_total']['minor_units']==9000
    assert query(books,sort='bank_total',direction='asc')['items'][0]['totals']['bank_total']['minor_units']==0
