"""Real private draft producers; no public deposit registration or permission mock.

Stored reads protect presence, not cross-revision ordinal stability. The legacy
acceptance witness reconstructs the two c43e5c8 acceptance statements in memory;
its ordinary admission, bundle/persist and subsequent unpatched reads stay real.
"""
import inspect
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import deposit_drafts as drafts, deposit_selection as selection
from bookflow.company import deposit_draft_models as m, deposit_draft_validation as validation
from bookflow.company import deposit_financial_derivation as derivation, payment_queries as q, schema as c
from bookflow.company.deposit_models import ComponentOccurrence, SemanticKey
from tests.test_tax_policy_sales import tax_sale, request as tax_request
from tests.test_service_sales_lifecycle import sale, COMPANY, snapshot
from tests.test_deposit_draft_financial import run_private, financial
from tests.test_deposit_lifecycle import driver
from tests.test_deposit_sources import uf
from tests.test_payment_receipts import method


@pytest.fixture
def world(client, tax_sale, run_private, driver):
    bank=client.account.create(name='Zero-capacity bank',type='bank',company=COMPANY)['id']
    destination=uf(client);payment_method=method(client)
    def post(nets=('10.00','20.00'),policy='line_component_half_even',single=False):
        data=dict(tax_request(tax_sale,policy,nets=nets),deposit_to=destination,payment_method=payment_method)
        if single:data['sales_tax_item']=tax_sale['rules'][0]
        return client.run('sales-receipt post',data,company=COMPANY)
    def revise(receipt,nets=None,group=None):
        data=dict(sales_receipt=receipt['id'],expected_version=receipt['version'])
        if nets is not None:
            data['lines']=[dict(line_id=line['line_id'],item=tax_sale['item'],net_amount=net,tax_code=tax_sale['taxable'])
                for line,net in zip(receipt['revision']['lines'],nets)]
        if group is not None:data['sales_tax_item']=group
        return client.run('sales-receipt update',data,company=COMPANY,reason='Owned occurrence transition')
    def run(verb,body,kind='draft',owner=None):
        module=selection if kind=='selection' else drafts
        return run_private(lambda s,ctx:(owner or module.run)(s,ctx,module.INPUTS[verb].model_validate(body),verb))
    def create(receipt=None):
        value=run('create',dict(header=dict(date='2026-06-03',deposit_to=bank)))
        return add(value,receipt) if receipt else value
    def add(value,receipt,kind='draft'):
        return run('update',{kind:value.id,'expected_version':value.version,'set_sources':[dict(
            source_type='sales_receipt',source=receipt['id'],expected_version=receipt['version'])]},kind)
    def read(function):
        with driver.session() as s:return function(s,None)
    def load(value,kind='draft'):
        return read(lambda s,ctx:drafts.load(s,value.id,kind=kind))
    def accept(child,parent,owner=None):
        return run('accept',dict(selection=child.id,expected_version=child.version,draft=parent.id,
            expected_draft_version=parent.version),'selection',owner)
    return SimpleNamespace(post=post,revise=revise,run=run,create=create,add=add,load=load,accept=accept,
        client=client,facts=tax_sale,private=run_private,read=read,driver=driver)


def orders(row):return {o.key:o.ordinal for o in row.occurrences if o.present}


def row(world,value,kind='draft'):return world.load(value,kind)[2].sources[0]


@pytest.mark.parametrize('policy', ['line_component_half_even','line_combined_half_up','invoice_combined_half_up'])
def test_real_zero_mixed_positive_add_reload_and_consume(world,policy):
    mixed=('0.10','0.10') if policy=='invoice_combined_half_up' else ('0.10','0.30')
    for label,nets in [('zero',('0.01','0.01')),('mixed',mixed),('positive',('10.00','20.00'))]:
        receipt=world.post(nets,policy)
        taxes=[c['tax_minor_units'] for line in receipt['revision']['lines'] for c in line['tax_components']]
        assert (all(v==0 for v in taxes) if label=='zero' else
                (0 in taxes and any(v>0 for v in taxes)) if label=='mixed' else all(v>0 for v in taxes))
        draft=world.create(receipt);manifest=world.load(draft)[2];source=manifest.sources[0]
        positive={v.key for v in source.source.components}
        assert set(orders(source))==positive<=set(source.source.semantic_presence)
        assert manifest.summary.bank_total==source.source.cash_minor_units
        posted=financial(world.private,dict(operation_key=policy+label,
            document=dict(mode='draft',draft=draft.id,expected_version=draft.version)))
        assert posted.current.revision_bank_total==source.source.cash_minor_units
        assert all(cell.units>0 for cell in posted.effect.financial.cells)
        assert all(leg.signed_debit!=0 for leg in posted.effect.financial.legs)
        h,_,loaded,_=world.load(draft)
        assert h['state']=='consumed' and loaded==manifest


def test_zero_positive_zero_positive_ordinals(world):
    receipt=world.post(('0.01','0.01'));draft=world.create(receipt)
    previous=row(world,draft)
    assert len(previous.occurrences)==2 and len(previous.source.semantic_presence)==6
    for nets in [('10.00','20.00'),('0.01','0.01'),('10.00','20.00')]:
        receipt=world.revise(receipt,nets);draft=world.add(draft,receipt);current=row(world,draft)
        prior=orders(previous);now=orders(current)
        assert current.row_id==previous.row_id and current.ordinal==previous.ordinal
        assert all(now[k]==v for k,v in prior.items())
        assert all(v>max(prior.values()) for k,v in now.items() if k not in prior)
        assert len(set(now.values()))==len(now)==6
        previous=current


def test_selection_zeroing_and_posted_voided_clones(world):
    receipt=world.post();draft=world.create(receipt);parent=row(world,draft)
    child=world.run('create',dict(draft=draft.id,expected_version=draft.version),'selection')
    assert row(world,child,'selection').occurrences==parent.occurrences
    receipt=world.revise(receipt,('0.01','0.01'));child=world.add(child,receipt,'selection')
    accepted=world.accept(child,draft);draft=accepted.draft;current=row(world,draft)
    assert orders(current)==orders(parent) and len(current.source.components)==2
    posted=financial(world.private,dict(operation_key='retained-zero-post',document=dict(mode='draft',draft=draft.id,expected_version=draft.version)))
    effect=posted.effect.financial.intent.sources[0]
    assert effect.occurrences==current.occurrences
    edit=world.run('create',dict(from_deposit=posted.current.id,expected_version=1))
    assert row(world,edit).occurrences==effect.occurrences
    voided=financial(world.private,dict(deposit=posted.current.id,expected_version=1,operation_key='retained-zero-void'),'void')
    copied=world.run('create',dict(copy_from_voided=posted.current.id,expected_version=voided.current.version))
    assert row(world,copied).occurrences==effect.occurrences
    # The first revision clones the Effect; it has no draft predecessor proof.
    assert world.load(edit)[1]['previous_revision_id'] is None
    assert world.load(copied)[1]['previous_revision_id'] is None


def acceptance_case(world):
    receipt=world.post(single=True);draft=world.create(receipt)
    receipt=world.revise(receipt,group=world.facts['group']);draft=world.add(draft,receipt)
    parent=row(world,draft)
    child=world.run('create',dict(draft=draft.id,expected_version=draft.version),'selection')
    first=row(world,child,'selection')
    child=world.run('update',dict(selection=child.id,expected_version=child.version,remove_sources=[receipt['id']]),'selection')
    child=world.add(child,receipt,'selection');readded=row(world,child,'selection')
    assert readded.row_id!=first.row_id and orders(readded)!=orders(parent)
    return receipt,draft,parent,child,readded


def test_remove_readd_accept_and_new_keys_rows(world):
    receipt,draft,parent,child,_=acceptance_case(world)
    draft=world.accept(child,draft).draft
    assert row(world,draft).row_id==parent.row_id and orders(row(world,draft))==orders(parent)
    # Append a genuinely new tax key to a retained parent, and a wholly new row.
    agency=world.client.vendor.create(name='Third agency',is_tax_agency=True,company=COMPANY)['id']
    rule=world.client.run('item create',dict(name='Third rule',type='sales_tax_item',tax_percent='5',
        tax_agency_vendor_id=agency,liability_account_id=world.facts['liability']),company=COMPANY)['id']
    group=world.client.run('item create',dict(name='Three rules',type='sales_tax_group',members=[
        dict(component_item_id=k,quantity='1') for k in (*world.facts['rules'],rule)]),company=COMPANY)['id']
    child=world.run('create',dict(draft=draft.id,expected_version=draft.version),'selection')
    receipt=world.revise(receipt,group=group);child=world.add(child,receipt,'selection')
    fresh=world.post();child=world.add(child,fresh,'selection')
    selected={r.source.transaction_id:r for r in world.load(child,'selection')[2].sources}
    draft=world.accept(child,draft).draft
    current={r.source.transaction_id:r for r in world.load(draft)[2].sources}
    old=orders(parent);new=orders(current[receipt['id']])
    assert all(new[k]==v for k,v in old.items())
    appended={k:v for k,v in new.items() if k not in old}
    assert len(appended)==2 and min(appended.values())>max(old.values())
    assert len(set(new.values()))==len(new)
    assert current[fresh['id']].occurrences==selected[fresh['id']].occurrences


def test_old_accepted_reordered_history_loads_without_normalization(world):
    _,draft,parent,child,readded=acceptance_case(world)
    # Recreate only the pre-correction c43e5c8 acceptance algorithm, not an auth
    # bypass. All old admission/version/stale/bundle/persist checks still execute.
    source=inspect.getsource(selection.run)
    expression='deposits.occurrences(row.source,prior.occurrences) if prior else row.occurrences'
    guard='            v.require_occurrence_retention(parent_value,parent_result,strict_ordinals=True)\n'
    assert source.count(expression)==source.count(guard)==1
    source=source.replace(expression,'row.occurrences').replace(guard,'')
    namespace=dict(vars(selection));exec(compile(source,'<c43e5c8 acceptance fixture>','exec'),namespace)
    accepted=world.accept(child,draft,namespace['run']).draft
    before=snapshot(world.client);loaded=row(world,accepted)
    assert snapshot(world.client)==before
    assert loaded.row_id==parent.row_id and orders(loaded)==orders(readded)!=orders(parent)
    assert world.load(accepted)[2].sources[0]==loaded  # Repeat leaves stored order intact.


@pytest.fixture
def retained(world):
    receipt=world.post();draft=world.create(receipt);prior=world.load(draft)[2]
    receipt=world.revise(receipt,('0.01','0.01'));draft=world.add(draft,receipt)
    h,r,current,_=world.load(draft)
    zero=next(o for o in current.sources[0].occurrences if o.key not in {x.key for x in current.sources[0].source.components})
    return world,draft,h,r,prior,current,zero


def altered(manifest,occurrences):
    row=manifest.sources[0].model_copy(update={'occurrences':tuple(occurrences)})
    return manifest.model_copy(update={'sources':(row,)})


@pytest.mark.parametrize('damage',['missing_positive','nonsemantic','dropped_zero','renumbered_zero'])
def test_current_image_and_retention_contracts(retained,damage):
    world,_,h,r,prior,current,zero=retained
    occurrences=list(current.sources[0].occurrences)
    if damage=='missing_positive':
        key=current.sources[0].source.components[0].key;occurrences=[o for o in occurrences if o.key!=key]
    elif damage=='nonsemantic':
        occurrences.append(ComponentOccurrence(key=SemanticKey(kind='sale_net',identity='absent'),ordinal=99,present=True))
    elif damage=='dropped_zero':occurrences=[o for o in occurrences if o.key!=zero.key]
    else:occurrences=[o.model_copy(update={'ordinal':99}) if o.key==zero.key else o for o in occurrences]
    changed=altered(current,occurrences)
    before=snapshot(world.client)
    if damage in ('missing_positive','nonsemantic'):
        with pytest.raises(BookflowError) as error:validation.validate_manifest(changed)
        assert error.value.code=='E_VALIDATION'
    else:
        validation.validate_manifest(changed)
        with pytest.raises(BookflowError) as error:
            validation.require_occurrence_retention(prior,changed,strict_ordinals=True)
        assert error.value.details=={'reason':'occurrence_retention'}
        # Company-only reader seam: current hash is repaired, prior is actual
        # stored state. No immutable trigger bypass or forged permission state.
        def check(s,ctx):
            read=derivation.CompanyFacts(derivation.CompanyConnection(s.company.conn))
            revision=dict(r,snapshot=changed.model_dump_json(),manifest_hash=q.digest(changed.model_dump(mode='json')))
            if damage=='dropped_zero':
                with pytest.raises(BookflowError) as caught:derivation.begin_revision(read,h,revision,kind='draft')
                assert caught.value.details=={'reason':'occurrence_retention'}
            else:
                assert derivation.begin_revision(read,h,revision,kind='draft').manifest==changed
        world.read(check)
    assert snapshot(world.client)==before


def test_predecessor_identity_and_hash_before_graph(retained):
    world,_,h,r,_,current,_=retained
    def check(s,ctx):
        read=derivation.CompanyFacts(derivation.CompanyConnection(s.company.conn))
        for changed in (dict(r,previous_revision_id='absent'),dict(r,version=r['version']+1)):
            with pytest.raises(BookflowError) as error:derivation.begin_revision(read,h,changed,kind='draft')
            assert error.value.details=={'reason':'revision_chain'}
        # Wrong current hash wins before any predecessor lookup/retention check.
        with pytest.raises(BookflowError) as error:derivation.begin_revision(read,h,dict(r,manifest_hash='bad',previous_revision_id='absent'),kind='draft')
        assert error.value.details=={'reason':'manifest_hash'}
    world.read(check)


def test_removed_row_does_not_inherit_and_equality_regression(world,monkeypatch):
    receipt=world.post(('0.01','0.01'));draft=world.create(receipt);current=world.load(draft)[2]
    initial=current.sources[0];maximum=max(o.ordinal for o in initial.occurrences)
    extras=[ComponentOccurrence(key=k,ordinal=maximum+i,present=True) for i,k in enumerate(
        (k for k in initial.source.semantic_presence if k not in orders(initial)),1)]
    equality_era=altered(current,(*initial.occurrences,*extras))
    validation.validate_manifest(equality_era)  # Optional never-positive keys remain legal.
    replacement=current.model_copy(update={'sources':(initial.model_copy(update={'row_id':'new-row'}),)})
    validation.require_occurrence_retention(equality_era,replacement,strict_ordinals=True)
    original=validation.validate_manifest
    def old_equality(manifest):
        result=original(manifest)
        for source in manifest.sources:
            validation.require(set(orders(source))==set(source.source.semantic_presence))
        return result
    # Restore only the old equality: the actual valid receipt->draft update must
    # fail atomically. No producer, source, tax or authority mocking.
    empty=world.create();before=snapshot(world.client)
    with monkeypatch.context() as patch:
        patch.setattr(validation,'validate_manifest',old_equality)
        with pytest.raises(BookflowError) as error:world.add(empty,receipt)
        assert error.value.code=='E_VALIDATION'
    assert snapshot(world.client)==before


@pytest.mark.parametrize('damage',['drop','renumber'])
def test_production_transition_guard_is_atomic(retained,monkeypatch,damage):
    world,draft,_,_,_,current,zero=retained
    source=current.sources[0].source
    original=drafts.source_patch
    def broken(*args,**kwargs):
        result=original(*args,**kwargs)
        occurrences=[o for o in result.occurrences if o.key!=zero.key] if damage=='drop' else [
            o.model_copy(update={'ordinal':99}) if o.key==zero.key else o for o in result.occurrences]
        return result.model_copy(update={'occurrences':tuple(occurrences)})
    before=snapshot(world.client)
    with monkeypatch.context() as patch:
        patch.setattr(drafts,'source_patch',broken)
        with pytest.raises(BookflowError) as caught:
            world.run('update',dict(draft=draft.id,expected_version=draft.version,set_sources=[dict(
                source=source.transaction_id,source_type='sales_receipt',expected_version=source.expected_header_version)]))
        assert caught.value.details=={'reason':'occurrence_retention'}
    assert snapshot(world.client)==before


@pytest.mark.parametrize('damage,reason', [('hash','manifest_hash'),('owner','revision_chain'),('snapshot','invalid_manifest')])
def test_stored_predecessor_damage_is_checked_and_rolled_back(retained,damage,reason):
    world,draft,header,revision,_,_,_=retained
    if damage=='owner':
        other=world.create()
        other=world.run('update',dict(draft=other.id,expected_version=other.version,header=dict(memo='Foreign predecessor')))
        foreign=world.load(other)[1]
        assert foreign['version']==revision['version']-1
        before=snapshot(world.client)
        def check(s,ctx):
            read=derivation.CompanyFacts(derivation.CompanyConnection(s.company.conn))
            # A real foreign row at the right version: exercise the reader owner
            # predicate without defeating composite foreign keys in storage.
            with pytest.raises(BookflowError) as caught:
                derivation.begin_revision(read,header,dict(revision,previous_revision_id=foreign['id']),kind='draft')
            assert caught.value.details=={'reason':reason}
        world.read(check)
        assert snapshot(world.client)==before
        return
    before=snapshot(world.client)
    with world.driver.session() as s:
        original=tuple(s.company.raw.iterdump())
        s.company.conn.exec_driver_sql('SAVEPOINT predecessor_damage')
        try:
            # Owned disposable copy only. Immutable triggers are restored by the
            # savepoint; no fixture corruption survives the diagnostic read.
            names=list(s.company.conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='deposit_draft_revisions'").scalars())
            for name in names:s.company.conn.exec_driver_sql('DROP TRIGGER "'+name.replace('"','""')+'"')
            change={'manifest_hash':'0'*64} if damage=='hash' else {'snapshot':'{}'}
            s.company.conn.execute(c.deposit_draft_revisions.update().where(
                c.deposit_draft_revisions.c.id==revision['previous_revision_id']).values(**change))
            with pytest.raises(BookflowError) as caught:drafts.load(s,draft.id)
            assert caught.value.code=='E_VALIDATION' and caught.value.details=={'reason':reason}
        finally:
            s.company.conn.exec_driver_sql('ROLLBACK TO predecessor_damage')
            s.company.conn.exec_driver_sql('RELEASE predecessor_damage')
        assert tuple(s.company.raw.iterdump())==original
    assert snapshot(world.client)==before


def test_select_matching_preserves_retained_zero_occurrences(world):
    from bookflow.company import deposit_source_queries
    receipt=world.post();draft=world.create(receipt)
    child=world.run('create',dict(draft=draft.id,expected_version=draft.version),'selection')
    prior=row(world,child,'selection')
    world.revise(receipt,('0.01','0.01'))
    filtered=m.SourceFilter(date='2026-06-03',date_from='2026-06-01',date_to='2026-06-01')
    candidates=world.read(lambda s,ctx:deposit_source_queries.query(s,m.SourceQuery(**filtered.model_dump())))
    child=world.run('select-matching',dict(selection=child.id,expected_version=child.version,
        filter=filtered.model_dump(),facts_fingerprint=candidates.facts_fingerprint),'selection')
    current=next(r for r in world.load(child,'selection')[2].sources if r.source.transaction_id==receipt['id'])
    assert current.row_id==prior.row_id and orders(current)==orders(prior)
    assert len(current.source.components)==2
