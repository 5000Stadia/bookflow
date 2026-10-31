"""Inputs and outputs of the POS layer's registers (R114 increment 4, design/point-of-sale.md §2)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from bookflow.company.journal_models import _Version
from bookflow.company.sales_models import Selector


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


_OPTIONAL = ('site', 'deposit_account', 'payment_methods', 'walk_in_customer', 'refund_account', 'receipt_width',
             'receipt_message', 'count_shifts', 'decoder_order')


class _RegisterFields(_Strict):
    site: Selector | None = Field(None, description='Inventory site its stock leaves from (ID or name); default the '
                                  'default site.')
    deposit_account: Selector | None = Field(None, description='Where its takings are deposited: Undeposited Funds '
                                             '(the default) or a bank account (ID, number or name).')
    payment_methods: list[Selector] | None = Field(None, min_length=1, max_length=20, description=(
        'Payment methods it offers, in the order shown (IDs or names); default Cash, then the card and check '
        'methods.'))
    walk_in_customer: Selector | None = Field(None, description=(
        'Customer a sale with no customer is recorded against (ID or name); never shown on the POS screen or '
        "receipt. On create, omitted: a new customer named Walk-in (Main) or \"Walk-in (<register>)\"."))
    refund_account: Selector | None = Field(None, description='Bank account cash refunds leave from (ID, number or '
                                            "name); default the company's first bank account; null for none yet.")
    receipt_width: int | None = Field(None, description='Receipt paper width in millimetres, 80 or 58; default 80.')
    receipt_message: str | None = Field(None, max_length=500, description='Line printed at the foot of every '
                                        'receipt; null for none.')
    count_shifts: bool | None = Field(None, description='Count the drawer in shifts (float, count, over/short); '
                                      'default off.')
    decoder_order: str | None = Field(None, max_length=200, description=(
        "Camera decoder order for this register's devices, such as \"zxing-wasm,native\"; null uses the host's "
        'scan_decoders.'))

    @model_serializer(mode='wrap')
    def only_supplied(self, handler):
        values = handler(self)
        for key in _OPTIONAL:
            if key not in self.model_fields_set:
                values.pop(key, None)
        return values


class RegisterCreateInput(_RegisterFields):
    name: str = Field(min_length=1, max_length=200, description='Register name, such as "Front counter"; unique.')


class RegisterUpdateInput(_RegisterFields):
    pos_register: Selector = Field(description='Register ID or name.')
    expected_version: _Version | None = None
    name: str | None = Field(None, min_length=1, max_length=200, description='New name.')


class RegisterShowInput(_Strict):
    pos_register: Selector = Field(description='Register ID or name.')


class RegisterListInput(_Strict):
    pass


class Named(_Strict):
    id: str
    name: str


class PaymentMethodRef(_Strict):
    id: str
    name: str
    kind: str


class RegisterOutput(_Strict):
    id: str
    version: int
    name: str
    site: Named
    deposit_account: Named
    payment_methods: list[PaymentMethodRef]
    walk_in_customer: Named = Field(description='The customer a sale with no customer is recorded against.')
    refund_account: Named | None = Field(description='Bank account cash refunds leave from; null until one is set.')
    receipt_width: int
    receipt_message: str | None
    count_shifts: bool
    decoder_order: str | None
    warnings: list[str] = Field(default_factory=list, description=(
        'What needs attention: a record it points at that is no longer active, or no refund account yet.'))


class RegisterWriteOutput(RegisterOutput):
    changed: bool = True
    created_walk_in_customer: bool = Field(False, description='True when this call made the walk-in customer.')


class RegisterPageOutput(_Strict):
    items: list[RegisterOutput]
