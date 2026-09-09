"""Public deposit wire contracts.

The private aggregate keeps physical posting, attribution and profile facts that
belong to the ledger, not to a command result. These models are the closed
projection every adapter returns: the document, the receipts it banks, the money
entered beside them, and the movement each bank account sees.
"""
from typing import Literal

from bookflow.company.deposit_models import ID, SignedMoney
from bookflow.company.sales_models import StrictModel


class AccountRef(StrictModel):
    id: ID
    name: str
    full_name: str
    number: str | None
    type: str


class PartyRef(StrictModel):
    kind: Literal['customer', 'vendor', 'employee', 'other_name'] | None = None
    id: ID | None = None
    name: str | None = None


class BankedReceipt(StrictModel):
    """One customer payment or sales receipt this deposit takes out of Undeposited Funds."""
    row_id: ID
    source_type: Literal['payment', 'sales_receipt']
    source: ID
    expected_version: int
    receipt_date: str
    received_from: str
    payment_method: str | None
    reference: str | None
    memo: str | None
    memo_origin: Literal['source', 'entered']
    undeposited_funds_account: ID
    amount: SignedMoney


class OtherMoney(StrictModel):
    """Money entered on the deposit itself: interest, an owner contribution, a processor fee."""
    row_id: ID
    received_from: PartyRef
    account: AccountRef
    amount: SignedMoney
    memo: str | None
    check_number: str | None
    payment_method: str | None
    class_name: str | None


class CashBackOut(StrictModel):
    account: AccountRef
    amount: SignedMoney
    memo: str | None


class BankMovement(StrictModel):
    """What one bank or card account sees on its statement from this deposit."""
    role: str
    account_id: ID
    active: bool
    date: str
    statement_amount: SignedMoney
    signed_debit: SignedMoney


class MembershipChangeOut(StrictModel):
    source: ID
    kind: Literal['claim', 'release']
    amount: SignedMoney


class DepositState(StrictModel):
    id: ID
    version: int
    number: str
    status: Literal['posted', 'voided']
    date: str
    currency: str
    subtotal: SignedMoney
    cash_back: SignedMoney
    bank_total: SignedMoney
    posting_total: SignedMoney
    effective_bank_total: SignedMoney
    banked_receipt_ids: list[ID]


class DepositWriteOutput(StrictModel):
    schema_version: Literal[1] = 1
    command: Literal['deposit post', 'deposit update', 'deposit void']
    action: Literal['post', 'update', 'void']
    operation_key: str
    operation_id: ID | None
    changed: bool
    new_effect: bool
    idempotent_replay: bool = False
    dry_run: bool = False
    facts_fingerprint: str
    dependency_guard: str
    deposit: DepositState
    deposit_to: AccountRef
    receipts: list[BankedReceipt]
    other_money: list[OtherMoney]
    cash_back: CashBackOut | None
    bank_movements: list[BankMovement]
    memberships: list[MembershipChangeOut]
    posting_batch_ids: list[ID]
    audit_event_id: ID


class DepositCandidate(StrictModel):
    """One receipt sitting in Undeposited Funds, ready to be banked."""
    source_type: Literal['payment', 'sales_receipt']
    source: ID
    expected_version: int
    number: str
    date: str
    recorded_at: str
    received_from: str
    payment_method: str | None
    payment_method_type: str | None
    reference: str | None
    memo: str | None
    undeposited_funds_account: ID
    amount: SignedMoney
    eligible: bool
    reason: str | None
    deposited: bool


class DepositSourcesOutput(StrictModel):
    items: list[DepositCandidate]
    total_count: int
    subtotal: SignedMoney
    facts_fingerprint: str
    next_cursor: str | None


def _money(units, currency):
    return SignedMoney(minor_units=units, currency=currency)


def _account(value):
    return AccountRef(id=value.id, name=value.name, full_name=value.full_name, number=value.number, type=value.type)


def _payer(source):
    profile = source.profile
    party = profile.payer if source.source_type == 'payment' else profile.customer
    return party.label


def candidate(value):
    source = value.source
    return DepositCandidate(source_type=source.source_type, source=source.transaction_id,
        expected_version=source.expected_header_version, number=value.number, date=source.receipt_date,
        recorded_at=value.recorded_at, received_from=value.current_name,
        payment_method=value.payment_method_label, payment_method_type=value.payment_method_type,
        reference=source.source_reference, memo=source.source_memo,
        undeposited_funds_account=source.uf_account,
        amount=_money(source.cash_minor_units, source.currency), eligible=value.eligible,
        reason=value.reason, deposited=value.membership_id is not None)


def sources_page(s, page):
    """Company home currency keeps an empty page's subtotal well formed."""
    currency = s.company_info_row['home_currency']
    return DepositSourcesOutput(items=[candidate(v) for v in page.items], total_count=page.total_count,
        subtotal=_money(page.subtotal, currency), facts_fingerprint=page.facts_fingerprint,
        next_cursor=page.next_cursor)


def _receipt(row):
    source = row.source
    profile = source.profile
    method = profile.payment_method
    return BankedReceipt(row_id=row.row_id, source_type=source.source_type, source=source.transaction_id,
        expected_version=source.expected_header_version, receipt_date=source.receipt_date,
        received_from=_payer(source), payment_method=method.label if method else None,
        reference=source.source_reference, memo=row.memo, memo_origin=row.memo_origin,
        undeposited_funds_account=source.uf_account,
        amount=_money(source.cash_minor_units, source.currency))


def _other(row, currency):
    return OtherMoney(row_id=row.row_id, received_from=PartyRef(kind=row.dimensions.party_kind,
        id=row.dimensions.party_id, name=row.dimensions.party_name), account=_account(row.account),
        amount=_money(row.units, currency), memo=row.memo, check_number=row.check_number,
        payment_method=row.payment_method.label if row.payment_method else None,
        class_name=row.dimensions.class_name)


def write_output(output, *, dry_run=False):
    """Project the owned private lifecycle result onto the public contract."""
    effect = output.effect
    financial = effect.financial
    intent = financial.intent
    currency = effect.after.currency
    return DepositWriteOutput(command=output.command, action=effect.action, operation_key=output.operation_key,
        operation_id=output.operation_id, changed=output.changed, new_effect=output.new_effect,
        idempotent_replay=output.idempotent_replay, dry_run=dry_run,
        facts_fingerprint=output.facts_fingerprint, dependency_guard=output.dependency_guard,
        deposit=DepositState(id=output.current.id, version=output.current.version, number=output.current.number,
            status=output.current.status, date=output.current.revision_date, currency=currency,
            subtotal=_money(output.current.revision_subtotal, currency),
            cash_back=_money(output.current.revision_cash_back, currency),
            bank_total=_money(output.current.revision_bank_total, currency),
            posting_total=_money(output.current.revision_posting_total, currency),
            effective_bank_total=_money(output.current.effective_bank_total, currency),
            banked_receipt_ids=list(output.current.active_source_ids)),
        deposit_to=_account(intent.bank),
        receipts=[_receipt(row) for row in intent.sources],
        other_money=[_other(row, currency) for row in intent.additional],
        cash_back=CashBackOut(account=_account(intent.cash_back.account),
            amount=_money(intent.cash_back.units, currency), memo=intent.cash_back.memo) if intent.cash_back else None,
        bank_movements=[BankMovement(role=v.role, account_id=v.account_id, active=v.active, date=v.date,
            statement_amount=_money(v.statement_amount, v.currency),
            signed_debit=_money(v.signed_debit, v.currency)) for v in effect.bank_effects],
        memberships=[MembershipChangeOut(source=v.source_id, kind=v.kind,
            amount=_money(v.amount_minor_units, v.currency)) for v in effect.memberships],
        posting_batch_ids=list(effect.batch_ids), audit_event_id=effect.audit_event_id)
