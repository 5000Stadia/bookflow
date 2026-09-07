"""Independent whole-cash and exact sequential-cent G1 witnesses."""
from collections import defaultdict
import copy
import pytest
from pydantic import ValidationError
from bookflow.company.deposit_models import Account, Additional, Dimensions, Intent, CashBack, SourceRow, amount, SignedMoney
from bookflow.company import deposits, deposit_validation
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX


def account(id, type='bank'):
    return Account(id=id, name=id,full_name=id,number=None,normal_balance='credit' if type=='credit_card' else 'debit',type=type, system_role=None, active=True, currency='USD')


def dims(party='payer', cls=None):
    return Dimensions(party_kind='customer', party_id=party, party_name=party, class_id=cls, class_name=cls)


def additional(id, ordinal, units, type='other_income'):
    return Additional(row_id=id, ordinal=ordinal, account=account(id+'acct',type), units=units, dimensions=dims(id))


def intent(*rows, cash=None):
    return Intent(deposit_id='deposit', date='2026-06-03', currency='USD', bank=account('bank'), sources=(), additional=rows,
                  cash_back=CashBack(account=account('cash','other_current_asset'), units=cash) if cash else None)


def nets(effect):
    values=defaultdict(int)
    for leg in effect.legs:values[leg.account_id]+=leg.signed_debit
    return dict(values)


def test_n6_additional_cashback_and_independent_sequential_cent_tie():
    plain=deposits.prepare(intent(additional('receipt',1,1000)))
    assert nets(plain)=={'bank':1000,'receiptacct':-1000}
    zero=deposits.prepare(intent(additional('receipt',1,1000),cash=1000))
    assert (zero.posting_total,zero.subtotal,zero.bank_total)==(1000,1000,0)
    assert nets(zero)=={'cash':1000,'receiptacct':-1000}
    assert all(not leg.key.startswith('main_bank') for leg in zero.legs)
    tied=deposits.prepare(intent(additional('one',1,1),additional('two',2,2),additional('fee',3,-1,'expense'),cash=1))
    assert {(c.row_id,c.bucket):c.units for c in tied.cells}=={('two','cash_back'):1,('one','additional:fee'):1,('two','main_bank'):1}
    assert nets(tied)=={'cash':1,'feeacct':1,'bank':1,'oneacct':-1,'twoacct':-2}
    reordered=deposits.prepare(tied.intent.model_copy(update={'additional':tuple(reversed(tied.intent.additional))}))
    assert {(c.row_id,c.bucket):c.units for c in reordered.cells}=={(c.row_id,c.bucket):c.units for c in tied.cells}
    bad=tied.model_copy(update={'cells':tuple(c.model_copy(update={'row_id':'one' if c.row_id=='two' else 'two'}) for c in tied.cells)})
    with pytest.raises(BookflowError):deposit_validation.validate(bad)
    wrong=tied.model_copy(update={'legs':tuple(l.model_copy(update={'dimensions':dims('wrong')}) if l.key.startswith('main_bank') else l for l in tied.legs)})
    with pytest.raises(BookflowError):deposit_validation.validate(wrong)


@pytest.mark.parametrize('rows,cash', [((1000,),1001),((0,),None),((-1,),None),((INT64_MAX,1,-1),None)])
def test_n6_reject_nonpositive_cashback_and_positive_funding_overflow(rows,cash):
    with pytest.raises(BookflowError):deposits.prepare(intent(*(additional(str(i),i+1,n) for i,n in enumerate(rows)),cash=cash))


@pytest.mark.parametrize('value',[1.0,True,{'minor_units':1,'currency':'USD'},'1.001','1 USD x','1 EUR'])
def test_strict_money(value):
    with pytest.raises((BookflowError,ValueError,ValidationError)):amount(value,'USD')


def test_signed_exact_money():
    assert amount('-3.00','USD')==-300
    assert amount(SignedMoney(minor_units=-300,currency='USD'),'USD')==-300


def test_bank_card_roles_versions_zero_and_void():
    from bookflow.company.bank_effects import enumerate_deposit
    transfer=additional('card',1,1000,'credit_card')
    effect=deposits.prepare(intent(transfer))
    keys=enumerate_deposit(effect,header_row_id='header')
    assert {(v.role,v.signed_debit,v.statement_amount) for v in keys}=={('main_bank',1000,1000),('additional',-1000,1000)}
    emptied=deposits.prepare(intent(transfer,cash=1000))
    current=enumerate_deposit(emptied,header_row_id='header',previous=keys)
    assert {(v.role,v.active,v.signed_debit) for v in current}=={('main_bank',False,0),('additional',True,-1000)}
    moved=effect.model_copy(update={'intent':effect.intent.model_copy(update={'bank':account('otherbank')}),
        'legs':tuple(l.model_copy(update={'account_id':'otherbank'}) if l.account_id=='bank' else l for l in effect.legs)})
    changed=enumerate_deposit(moved,header_row_id='header',previous=keys)
    assert {v.identity for v in changed}=={v.identity for v in keys}
    assert next(v.account_id for v in changed if v.role=='main_bank')=='otherbank'
    canceled=enumerate_deposit(moved,header_row_id='header',previous=changed,void=True)
    assert all(not v.active and v.signed_debit==v.statement_amount==0 for v in canceled)
    with pytest.raises(BookflowError):enumerate_deposit(deposits.inverse(effect,'batch'),header_row_id='header')


def test_replacement_requires_complete_header_and_forbids_draft_override():
    from bookflow.company.deposit_models import PostInput,ReplaceInput
    post=dict(operation_key='strict-model',document=dict(mode='inline',deposit_to='bank',date='2026-06-03',additional=[]))
    assert PostInput.model_validate(post).document.sources==[]
    with pytest.raises(ValidationError):ReplaceInput.model_validate(dict(post,deposit='deposit',expected_version=1))
    with pytest.raises(ValidationError):PostInput.model_validate(dict(operation_key='draft',document=dict(mode='draft',draft='draft',expected_version=1),custom_fields={}))
    with pytest.raises(ValidationError):PostInput.model_validate(dict(operation_key='inline',document=dict(mode='inline',deposit_to='bank',date='2026-06-03',sources=[dict(source_type='payment',source='id',expected_version=1,source_result=True)])))


def test_zero_main_does_not_hide_other_bank_roles_or_card_debt():
    from bookflow.company.bank_effects import enumerate_deposit
    income=additional('income',1,1000)
    base=intent(income)
    cashbank=base.model_copy(update={'cash_back':CashBack(account=account('cashbank'),units=1000)})
    values=enumerate_deposit(deposits.prepare(cashbank),header_row_id='header')
    assert [(v.role,v.account_id,v.signed_debit,v.statement_amount) for v in values]==[('cash_back','cashbank',1000,1000)]
    card=base.model_copy(update={'cash_back':CashBack(account=account('card','credit_card'),units=1000)})
    values=enumerate_deposit(deposits.prepare(card),header_row_id='header')
    assert [(v.role,v.signed_debit,v.statement_amount) for v in values]==[('cash_back',1000,-1000)]
    transfer=intent(additional('frombank',1,1000,'bank'),cash=1000)
    values=enumerate_deposit(deposits.prepare(transfer),header_row_id='header')
    assert [(v.role,v.account_id,v.signed_debit) for v in values]==[('additional','frombankacct',-1000)]


def test_pure_aggregate_tamper_cannot_introduce_bool_money():
    original=deposits.prepare(intent(additional('one',1,1)))
    row=original.intent.additional[0].model_copy(update={'units':True})
    altered=original.model_copy(update={'intent':original.intent.model_copy(update={'additional':(row,)})})
    with pytest.raises(BookflowError):deposit_validation.validate(altered)


def test_effect_has_versioned_strict_round_trip():
    from bookflow.company.deposit_models import Effect
    effect=deposits.prepare(intent(additional('receipt',1,1000)))
    assert Effect.model_validate_json(effect.model_dump_json())==effect
    assert effect.schema_version==1
    for version in (True,1.0,2,'1'):
        with pytest.raises(ValidationError):Effect.model_validate(dict(effect.model_dump(mode='python'),schema_version=version))


@pytest.mark.parametrize('change',[{'party_name':None},{'class_name':'Orphan class'}])
def test_dimension_facts_are_complete_pairs(change):
    with pytest.raises(ValidationError):Dimensions.model_validate(dict(dims().model_dump(),**change))
