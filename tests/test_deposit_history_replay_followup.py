"""Owned malformed-stream witnesses: not supported ordinary business inputs.

Each damaged projection and audit image agree, and the final live pointer agrees
with replay when the named check is removed. Thus a projection-only check cannot
substitute for the event-stream invariant. All corruption and trigger removal is
inside a rollback-only savepoint, with full company+hub storage equality proved.
"""
import inspect
import sqlite3
import pytest
import sqlalchemy as sa
from bookflow.company import deposit_dependency_history as history, schema as c
from bookflow.company.deposit_dependency_models import InspectionRoot, PageInput
from bookflow.company.deposit_dependency_pages import changes_page
from bookflow.core import audit
from bookflow.core.errors import BookflowError
from bookflow.core.publication import OSBinding
from tests.test_deposit_lifecycle import driver,additional_document,replacement
from tests.test_service_sales_lifecycle import sale,COMPANY
from tests.test_payment_receipts import method
from tests.test_deposit_dependency_binding import observe,_storage
from tests.test_row8_journal import database_path


@pytest.fixture
def replay_world(client,sale,driver,monkeypatch):
    payment_method=method(client)
    payments=[client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='1.00',payment_method=payment_method,operation_key='replay-source-'+str(i)),company=COMPANY) for i in range(2)]
    document=additional_document(client,sale)
    document['sources']=[dict(source_type='payment',source=p['id'],expected_version=1) for p in payments]
    # The baseline predates the bank stream, so a malformed first version is
    # an intervening-history defect rather than a corrupted signed baseline.
    target=InspectionRoot(kind='payment',id=payments[0]['id'])
    def guard(s):
        binding=OSBinding.from_session(s);recipe,facts=history.capture(s,target,binding)
        assert not facts.unknown
        return history.issue(s,recipe,facts,binding)
    token=observe(client,monkeypatch,guard)
    posted=driver.run('post',dict(operation_key='replay-first',document=document))
    body=replacement(posted,document);body['memo']='Actual producer restatement'
    body['sources']=[dict(source_type='payment',source=p['id'],expected_version=2) for p in payments]
    edited=driver.run('update',dict(operation_key='replay-second',deposit=posted.current.id,expected_version=1,document=body),reason='Restate owned claims and bank versions')
    return target,token,payments,posted,edited


def _snapshot(s):
    return tuple(s.company.raw.iterdump()),tuple(s.hub.raw.iterdump())


def _mutant(fragment,replacement):
    """Only an in-memory copy of this reader is mutated; source stays unchanged."""
    source=inspect.getsource(history._current_relations)
    assert source.count(fragment)==1
    scope={}
    exec(compile(source.replace(fragment,replacement),'<owned replay mutation>','exec'),history.__dict__,scope)
    return scope['_current_relations']


def _unknown(s,target,token,expected):
    binding=OSBinding.from_session(s)
    compared=history.compare(s,token,target,binding)
    assert not compared.matches and compared.unknown_history
    assert compared.unknown_records==(expected,)
    recipe,facts=history.capture(s,target,binding)
    assert facts.unknown==(expected,)
    with pytest.raises(BookflowError) as error:history.issue(s,recipe,facts,binding)
    assert error.value.code=='E_PREVIEW_STALE' and error.value.details['history']=='unknown_history'
    page=changes_page(s,token,target,PageInput(limit=1),binding)
    assert page.unknown_history


def _rewrite(s,table,row,changes,kind):
    # Demonstrate the real immutable-row guard before its narrowly authorized
    # malformed-history bypass. Check/FK constraints stay enabled throughout.
    statement=table.update().where(table.c.id==row['id']).values(**changes)
    with pytest.raises(sa.exc.IntegrityError):s.company.conn.execute(statement)
    trigger=table.name+'_immutable_update'
    s.company.raw.execute('DROP TRIGGER "'+trigger+'"')
    s.company.conn.execute(statement)
    entry=s.company.conn.execute(sa.select(c.audit_entries).where(c.audit_entries.c.record_type==kind,c.audit_entries.c.record_id==row['id'])).mappings().one()
    image=audit.decode_snapshot(entry['after']);assert image['id']==row['id']
    image.update(changes)
    s.company.conn.execute(c.audit_entries.update().where(c.audit_entries.c.id==entry['id']).values(after=audit.encode_snapshot(image)))
    assert s.company.raw.execute('PRAGMA foreign_keys').fetchone()==(1,)
    assert not s.company.raw.execute('PRAGMA foreign_key_check').fetchall()


@pytest.mark.parametrize('damage',['duplicate_claim','release_identity','release_amount','release_currency','bank_gap','bank_start'])
def test_replay_corruption_is_unknown_and_exact_check_mutation_is_caught(root,client,driver,replay_world,monkeypatch,damage):
    target,token,payments,posted,edited=replay_world
    path=database_path(client);before=_storage(root,path)
    with driver.session() as s:
        binding=OSBinding.from_session(s)
        known=history.compare(s,token,target,binding)
        assert not known.matches and not known.unknown_history and known.changes
        assert not history.capture(s,target,binding)[1].unknown
        claims=list(s.company.conn.execute(sa.select(c.deposit_memberships).order_by(c.deposit_memberships.c.id)).mappings())
        own=[r for r in claims if r['source_transaction_id']==payments[0]['id']]
        assert [r['kind'] for r in own]==['claim','release','claim']
        assert own[1]['reverses_membership_id']==own[0]['id']
        current=s.company.conn.execute(sa.select(c.deposit_current_memberships).where(c.deposit_current_memberships.c.source_transaction_id==payments[0]['id'])).mappings().one()
        assert current['membership_id']==own[-1]['id']
        s.company.raw.execute('SAVEPOINT replay_corruption')
        try:
            if damage.startswith('bank'):
                key=s.company.conn.execute(sa.select(c.bank_effect_keys.c.id).where(c.bank_effect_keys.c.transaction_id==posted.current.id,c.bank_effect_keys.c.role=='main_bank')).scalar_one()
                rows=list(s.company.conn.execute(sa.select(c.bank_effect_versions).where(c.bank_effect_versions.c.key_id==key).order_by(c.bank_effect_versions.c.version)).mappings())
                assert [r['version'] for r in rows]==[1,2]
                # [1,3] misses the interior; [2,3] does not start at one.
                _rewrite(s,c.bank_effect_versions,rows[-1],{'version':3},'bank_effect_version')
                if damage=='bank_start':
                    image=audit.decode_snapshot(s.company.conn.execute(sa.select(c.audit_entries.c.after).where(c.audit_entries.c.record_type=='bank_effect_version',c.audit_entries.c.record_id==rows[0]['id'])).scalar_one())
                    image['version']=2
                    s.company.conn.execute(c.bank_effect_versions.update().where(c.bank_effect_versions.c.id==rows[0]['id']).values(version=2))
                    s.company.conn.execute(c.audit_entries.update().where(c.audit_entries.c.record_type=='bank_effect_version',c.audit_entries.c.record_id==rows[0]['id']).values(after=audit.encode_snapshot(image)))
                assert s.company.conn.execute(sa.select(c.bank_effect_current.c.version_id).where(c.bank_effect_current.c.key_id==key)).scalar_one()==rows[-1]['id']
                expected='bank_effect_current:'+key
                fragment="if [row['version'] for row in rows]!=list(range(1,len(rows)+1)):"
                mutant=_mutant(fragment,'if False:')
            else:
                row=own[1]
                if damage=='duplicate_claim':
                    change={'kind':'claim','reverses_membership_id':None}
                    fragment="if current is not None:history.unknown.add('source_claims:'+source)"
                    mutant=_mutant(fragment,'if current is not None:pass')
                elif damage=='release_identity':
                    other=next(r for r in claims if r['source_transaction_id']==payments[1]['id'] and r['audit_event_id']==edited.effect.audit_event_id and r['kind']=='claim')
                    change={'reverses_membership_id':other['id']}
                    mutant=_mutant("member['reverses_membership_id']!=current['id']",'False')
                else:
                    field='amount_minor_units' if damage=='release_amount' else 'currency'
                    change={field:row[field]+1 if field=='amount_minor_units' else 'EUR'}
                    fragment="('transaction_id','source_transaction_id','source_revision_id','amount_minor_units','currency')"
                    kept=tuple(k for k in ('transaction_id','source_transaction_id','source_revision_id','amount_minor_units','currency') if k!=field)
                    mutant=_mutant(fragment,repr(kept))
                _rewrite(s,c.deposit_memberships,row,change,'deposit_membership')
                expected='source_claims:'+payments[0]['id']
            damaged=_snapshot(s)
            _unknown(s,target,token,expected)
            assert _snapshot(s)==damaged
            with monkeypatch.context() as patch:
                patch.setattr(history,'_current_relations',mutant)
                # The SAME oracle fails when just its reader check is removed.
                with pytest.raises(AssertionError):_unknown(s,target,token,expected)
                recipe,facts=history.capture(s,target,binding)
                assert not facts.unknown
                assert history.issue(s,recipe,facts,binding)
                result=history.compare(s,token,target,binding)
                assert not result.matches and not result.unknown_history
            assert _snapshot(s)==damaged
        finally:
            s.company.raw.execute('ROLLBACK TO replay_corruption')
            s.company.raw.execute('RELEASE replay_corruption')
    assert _storage(root,path)==before
