"""Private deposit statement identities over validated business effects.

Inverse posting batches are not new statement entries. Callers append the next
version to the same key, including an inactive zero version for void/zero net.
"""
from typing import Literal
from bookflow.company.deposit_models import Frozen, ID
from bookflow.company.deposit_validation import validate, require
from pydantic import Field
from bookflow.core.exact import INT64_MAX


class BankEffect(Frozen):
    transaction_id: ID
    role: Literal['main_bank','cash_back','additional']
    row_id: ID
    account_id: ID
    active: bool
    signed_debit: int = Field(ge=-INT64_MAX,le=INT64_MAX)
    statement_amount: int = Field(ge=-INT64_MAX,le=INT64_MAX)
    date: str
    currency: str

    @property
    def identity(self):
        return self.transaction_id,self.role,'' if self.role=='main_bank' else self.row_id


def enumerate_deposit(effect, *, header_row_id, previous=(), void=False):
    validate(effect)
    require(effect.inverse_of is None and type(void) is bool)
    intent=effect.intent
    values=[]
    def add(role,row_id,account,signed):
        # Cash on hand is not a bank statement. Negative/additional bank legs
        # are separate keys, never implicitly netted into the main deposit.
        if account.type not in ('bank','credit_card') or signed == 0 or void:return
        values.append(BankEffect(transaction_id=intent.deposit_id,role=role,row_id=row_id,account_id=account.id,
            active=True,signed_debit=signed,statement_amount=-signed if account.type == 'credit_card' else signed,date=intent.date,currency=intent.currency))
    add('main_bank',header_row_id,intent.bank,effect.bank_total)
    if intent.cash_back:add('cash_back',header_row_id,intent.cash_back.account,effect.cash_back)
    for row in intent.additional:add('additional',row.row_id,row.account,-row.units)
    active={v.identity for v in values}
    require(len({v.identity for v in previous})==len(previous))
    for old in previous:
        require(old.transaction_id==intent.deposit_id)
        if old.identity not in active:
            values.append(old.model_copy(update={'active':False,'signed_debit':0,'statement_amount':0,'date':intent.date}))
    return tuple(sorted(values,key=lambda v:v.identity))
