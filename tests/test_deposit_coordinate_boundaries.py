"""Pure tamper admission, exact source identity, stale writers and page barriers."""
from dataclasses import replace
import copy
import json
import pytest
from bookflow import BookflowError
from bookflow.core.context import Context,Interface
from bookflow.core.publication import OSBinding
from bookflow.company import schema as c, deposit_coordination as coord
from bookflow.company import deposit_coordinate_persistence as persistence, deposit_coordinate_validation as validation
from bookflow.company import deposit_operation_pages as pages
from bookflow.company.deposit_dependency_models import PageInput
from bookflow.company.deposit_coordinate_models import CoordinateInput
from tests.test_deposit_coordinate_persistence import n2,prepare,sale,driver


def test_complete_row_validator_rejects_independent_tampers_before_any_write(client,sale,driver,n2):
    inp,*_=n2
    ctx=Context.new(Interface.python,'C validation witness',reason='Correct actual cash')
    with driver.session() as s:
        p=prepare(s,ctx,inp);bundle=persistence.build(s,ctx,p);validation.validate(s,ctx,bundle)
        baseline=tuple(s.company.raw.iterdump())
        candidates=[]
        db=copy.deepcopy(bundle.deposit_bundle);db['pending']['custom_field_values']=[]
        candidates.append(replace(bundle,deposit_bundle=db))
        db=copy.deepcopy(bundle.deposit_bundle);db['bank_current'][0]['unclassified_column']=None
        candidates.append(replace(bundle,deposit_bundle=db))
        source=bundle.source_rows
        lines=list(source.posting_lines)
        lines[-1]=lines[-1].model_copy(update={'account_id':lines[0].account_id})
        candidates.append(replace(bundle,source_rows=source.model_copy(update={'posting_lines':tuple(lines)})))
        source=bundle.source_rows
        candidates.append(replace(bundle,source_rows=source.model_copy(update={'application_allocations':()})))
        for field,value in [('number','wrong-number'),('current_revision_id',bundle.deposit_bundle['data']['prior']['id'])]:
            db=copy.deepcopy(bundle.deposit_bundle);db['header'][field]=value
            candidates.append(replace(bundle,deposit_bundle=db))
        db=copy.deepcopy(bundle.deposit_bundle);db['pending']['deposit_memberships'][0]['amount_minor_units']+=1
        candidates.append(replace(bundle,deposit_bundle=db))
        db=copy.deepcopy(bundle.deposit_bundle);db['pending']['deposit_cash_cells'].pop()
        candidates.append(replace(bundle,deposit_bundle=db))
        for table,field,value in [('posting_lines','account_snapshot','{}'),('deposit_components','currency','CAD'),('bank_effect_versions','batch_id',bundle.deposit_bundle['data']['prior']['id'])]:
            db=copy.deepcopy(bundle.deposit_bundle)
            assert db['pending'][table]
            index=next((i for i,v in enumerate(db['pending'][table]) if not v.get('reversed_line_id')),0)
            db['pending'][table][index][field]=value
            candidates.append(replace(bundle,deposit_bundle=db))
        event=copy.deepcopy(bundle.audit_event);event.entries[0]['after']=b'\0{}'
        candidates.append(replace(bundle,audit_event=event))
        effect=bundle.output.effect.model_copy(update={'target_ids':bundle.output.effect.target_ids[:-1]})
        candidates.append(replace(bundle,output=bundle.output.model_copy(update={'effect':effect})))
        for broken in candidates:
            with pytest.raises((BookflowError,ValueError,KeyError)):validation.validate(s,ctx,broken)
            assert tuple(s.company.raw.iterdump())==baseline
        # A typed container is not proof of a legitimate overlay.
        with pytest.raises(BookflowError):
            from bookflow.company import deposit_lifecycle
            deposit_lifecycle.resolve_replacement(s,ctx,inp.replacement.document,identity=inp.deposit,
                old=bundle.deposit_bundle['data']['before'],prior=bundle.deposit_bundle['data']['prior'],previous=bundle.deposit_bundle['financial'],
                keys=[],maximum=1,mapping={},binding=p.binding,overlay=type('Duck',(),dict(source_id=inp.source_action.input.payment,deposit_id=inp.deposit,retained_row=None))())
        assert tuple(s.company.raw.iterdump())==baseline


def test_disjoint_financial_writer_stales_complete_intent_and_preserves_rows(client,sale,driver,n2):
    inp,*_=n2
    ctx=Context.new(Interface.python,'C race witness',reason='Correct actual cash')
    wire=inp.model_dump(mode='json',by_alias=True,exclude_unset=True)
    wire['operation_key']='C-competing';wire['replacement']['document']['memo']='Other concurrent field'
    other=CoordinateInput.model_validate(wire)
    with driver.session() as s:
        first=prepare(s,ctx,inp);second=prepare(s,ctx,other)
    with driver.session() as s:
        winner=persistence.execute(s,ctx,first)
        assert winner.new_effect
    before=driver.dump()
    with driver.session() as s:
        with pytest.raises(BookflowError) as caught:persistence.execute(s,ctx,second)
        assert caught.value.code=='E_PREVIEW_STALE'
        assert caught.value.details['history']=='known_stale'
        assert caught.value.details['changes']['total_count']>0
    assert driver.dump()==before


def test_preview_page_logical_ids_and_literal_text_are_stable(client,sale,driver,n2):
    inp,*_=n2
    ctx=Context.new(Interface.python,'C preview witness',reason='Correct actual cash')
    with driver.session() as s:
        p=prepare(s,ctx,inp)
        first=pages.preview_items(s,ctx,p,'memberships',PageInput(limit=1))
        assert first.total_count==4 and first.next_cursor
        second=pages.preview_items(s,ctx,p,'memberships',PageInput(limit=1,cursor=first.next_cursor))
        assert second.items!=first.items
        with pytest.raises(BookflowError):pages.preview_items(s,ctx,p,'cash_allocations',PageInput(limit=1,cursor=first.next_cursor))
        result=persistence.build(s,ctx,p).output
        generated=result.effect.identities[0].physical_id
        financial=result.effect.deposit.financial
        row=financial.intent.sources[0].model_copy(update={'memo':generated})
        altered=financial.model_copy(update={'intent':financial.intent.model_copy(update={'sources':(row,*financial.intent.sources[1:])})})
        output=result.model_copy(update={'effect':result.effect.model_copy(update={'deposit':result.effect.deposit.model_copy(update={'financial':altered})})})
        assert validation.logical_collections(output)['request_sources'][0]['memo']==generated


def test_real_independent_writers_never_overwrite_disjoint_edits(client,sale,driver,n2):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    inp,*_=n2;ctx=Context.new(Interface.python,'C simultaneous writers',reason='Correct actual cash')
    other=inp.model_dump(mode='json',by_alias=True,exclude_unset=True)
    other['operation_key']='C-second-writer';other['replacement']['document']['memo']='Independent other field'
    with driver.session() as s:plans=[prepare(s,ctx,inp),prepare(s,ctx,CoordinateInput.model_validate(other))]
    barrier=Barrier(2)
    def write(index):
        barrier.wait(timeout=10)
        try:
            with driver.session() as s:return persistence.execute(s,ctx,plans[index])
        except BookflowError as error:return error
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(write,range(2)))
    for index,result in enumerate(results):
        if isinstance(result,BookflowError) and result.code=='E_DB_BUSY':
            baseline=driver.dump()
            with pytest.raises(BookflowError) as error:
                with driver.session() as s:persistence.execute(s,ctx,plans[index])
            results[index]=error.value
            assert driver.dump()==baseline
    winners=[v for v in results if not isinstance(v,BookflowError)]
    losses=[v for v in results if isinstance(v,BookflowError)]
    assert len(winners)==len(losses)==1
    assert losses[0].code=='E_PREVIEW_STALE' and losses[0].details['history']=='known_stale'
    assert losses[0].details['changes']['total_count']>0
    with driver.session() as s:
        assert s.company.raw.execute("SELECT count(*) FROM deposit_operations WHERE command='deposit coordinate'").fetchone()==(1,)
        assert {v.after.version-v.before.version for v in winners[0].effect.headers}=={1}
        assert s.company.raw.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_closed_period_blocks_new_correction_but_not_original_recovery(client,sale,driver,n2):
    from tests.test_service_sales_lifecycle import COMPANY
    inp,*_=n2;ctx=Context.new(Interface.python,'C closed period',reason='Correct actual cash')
    with driver.session() as s:result=persistence.execute(s,ctx,prepare(s,ctx,inp))
    info=client.company.show(company=COMPANY)
    client.company.update(expected_version=info['info_version'],closing_date='2026-06-03',company=COMPANY)
    baseline=driver.dump()
    with driver.session() as s:
        assert persistence.recover(s,ctx,inp,OSBinding.from_session(s)).effect==result.effect
        wire=inp.model_dump(mode='json',by_alias=True,exclude_unset=True)
        wire['operation_key']='C-closed-new';wire['expected_version']=2
        wire['source_action']['input'].update(expected_version=3,amount='130')
        wire['source_action']['input']['invoice_versions'][0]['expected_version']=3
        wire['replacement']['document']['sources'][1]['expected_version']=3
        with pytest.raises(BookflowError) as denied:prepare(s,ctx,CoordinateInput.model_validate(wire))
        assert denied.value.code=='E_PERIOD_CLOSED'
    assert driver.dump()==baseline
