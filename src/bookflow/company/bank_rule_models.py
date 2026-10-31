"""Inputs and outputs of `bank-rule` (R108 increment 4, design/bank-feeds.md §5, §9)."""
from typing import Literal

from pydantic import Field, model_validator

from bookflow.company.reconciliation_commands_models import AccountSelector, Count, ID, Model, Units

Direction = Literal['in', 'out', 'any']
Action = Literal['categorise', 'transfer', 'ignore']
PayeeType = Literal['customer', 'vendor', 'employee', 'other_name']
IgnoreReason = Literal['already_entered', 'not_ours', 'duplicate', 'other']
Status = Literal['active', 'suggested', 'disabled', 'retired']


class Condition(Model):
    field: Literal['payee', 'payee_key', 'memo', 'number', 'amount'] = Field(description=(
        "What is tested: the bank's payee text, payee_key (the payee as Bookflow reads it: letters only, store and "
        'reference numbers, dates and processor words dropped), memo, number, or amount (the size of the line, in '
        'either direction).'))
    op: Literal['contains', 'starts_with', 'equals', 'between'] = Field(description=(
        'contains, starts_with or equals (text, case-folded with spaces collapsed; payee_key takes equals); between '
        '(amount only, value to value_to inclusive). No regular expressions.'))
    value: str = Field(min_length=1, max_length=200, description='The text, or an amount such as 45.00.')
    value_to: str | None = Field(None, min_length=1, max_length=40, description='between: the upper amount.')

    @model_validator(mode='after')
    def shape(self):
        if self.field == 'amount':
            if self.op not in ('equals', 'between'):
                raise ValueError('an amount condition is equals or between')
        elif self.op == 'between':
            raise ValueError('between is for amount only')
        elif self.field == 'payee_key' and self.op != 'equals':
            raise ValueError('a payee_key condition is equals')
        if (self.op == 'between') != (self.value_to is not None):
            raise ValueError('value_to goes with between, and only with it')
        return self


_PAYEE = ('Categorise: who the line is from or to, an existing name (or its id) on the payee_type list; shown on the '
          'line and used when it is added.')
_ACCOUNT = ('Categorise: the account a line goes to when added (an expense or income account...).')


class _Rule(Model):
    account: AccountSelector | None = Field(None, description=(
        'The bank or card account whose lines it fits; omitted or null, every feed account.'))
    direction: Direction | None = Field(None, description='in (money in), out (money out) or any; default any.')
    conditions: tuple[Condition, ...] | None = Field(None, min_length=1, max_length=10, description=(
        'All must hold. Example: [{"field": "payee", "op": "contains", "value": "SHELL"}].'))
    action: Action | None = Field(None, description=(
        'categorise (propose a payee, account, class and memo; the default), transfer (propose a transfer to '
        'transfer_to) or ignore (propose setting the line aside with ignore_reason).'))
    payee: str | None = Field(None, min_length=1, max_length=1000, description=_PAYEE)
    payee_type: PayeeType | None = Field(None, description='The list the payee is on; default vendor.')
    category: str | None = Field(None, min_length=1, max_length=1004, description=_ACCOUNT)
    class_id: str | None = Field(None, max_length=1004, description='Categorise: the class an added line takes.')
    memo: str | None = Field(None, max_length=4000, description="Categorise: the memo; default the bank's text.")
    transfer_to: AccountSelector | None = Field(None, description='Transfer: the other account.')
    ignore_reason: IgnoreReason | None = Field(None, description='Ignore: why (default other).')
    auto_add: bool | None = Field(None, description=(
        'Categorise: ask to add matching lines unattended at each import. A request: it acts only once a holder of '
        'automate.bank-rule makes the rule live (`bank-rule update --live`), and then under that holder\'s '
        'authority.'))
    priority: int | None = Field(None, strict=True, ge=1, le=100_000, description=(
        'Lower is tried first; default 100 (a learned rule is 1000, so a written rule beats it).'))


class CreateInput(_Rule):
    name: str = Field(min_length=1, max_length=200, description='A name to know it by, unique.')
    conditions: tuple[Condition, ...] = Field(min_length=1, max_length=10, description=(
        'All must hold. Example: [{"field": "payee", "op": "contains", "value": "SHELL"}].'))


class RuleSelector(Model):
    rule: str = Field(min_length=1, max_length=200, description='The rule, by id or name.')


class UpdateInput(_Rule):
    rule: str = Field(min_length=1, max_length=200, description='The rule, by id or name.')
    name: str | None = Field(None, min_length=1, max_length=200, description='A new name.')
    status: Literal['active', 'disabled'] | None = Field(None, description=(
        'active fits lines again; disabled stops it fitting any (`bank-rule deactivate`).'))
    live: bool | None = Field(None, description=(
        'true: make this auto-add or ignore rule act on its own at each import, under your authority (needs '
        'automate.bank-rule). --dry-run shows the check (what it will do and the waiting lines it fits now) and its '
        'fingerprint; pass that as confirm.'))
    confirm: str | None = Field(None, min_length=16, max_length=16, description=(
        "The check's fingerprint from a dry run: needed to make a rule live, and for a holder's change to keep a "
        'live rule live (without it, the change returns the rule to draft).'))
    expected_version: int | None = Field(None, strict=True, ge=1, description='Refused if the rule changed since.')


class QueryInput(Model):
    account: AccountSelector | None = Field(None, description="One account's rules (and the rules for every account).")
    status: Status | None = Field(None, description='Only rules in this status; suggested lists those learned.')
    source: Literal['written', 'learned'] | None = None
    limit: int = Field(100, strict=True, ge=1, le=1000)
    cursor: str | None = Field(None, max_length=20)


class ApplyInput(Model):
    rule: str | None = Field(None, min_length=1, max_length=200, description=(
        'One rule (by id or name); omitted, every active rule in order.'))
    account: AccountSelector | None = Field(None, description='Only this account\'s waiting lines.')
    limit: int = Field(1000, strict=True, ge=1, le=20_000, description='Most waiting lines to read.')


class ConditionOut(Model):
    field: str
    op: str
    value: str
    value_to: str | None = None


class RuleOut(Model):
    id: ID
    name: str
    account_id: ID | None
    account: str | None = Field(description='Its feed account; null for every feed account.')
    direction: Direction
    conditions: tuple[ConditionOut, ...]
    action: Action
    payee: str | None
    payee_type: PayeeType | None
    payee_id: str | None
    category_account_id: ID | None
    category: str | None
    class_id: ID | None
    memo: str | None
    transfer_account_id: ID | None
    ignore_reason: IgnoreReason | None
    auto_add: bool
    priority: int
    source: Literal['written', 'learned']
    status: Status
    state: Literal['draft', 'live', 'paused'] = Field(description=(
        'For an auto-add or ignore rule: draft (acts for nobody), live (acts at each import under its establisher) '
        'or paused (paused_reason says why). Other rules only propose and stay draft.'))
    automation: bool = Field(description='True for an auto-add or ignore rule: one that can act on its own.')
    established_by: str | None
    established_at: str | None
    paused_reason: str | None
    draft_reason: str | None
    learned_from_batch: ID | None
    learned_key: str | None
    edited: bool
    hits: Count = Field(description='Live inbox lines it proposed for or acted on.')
    does: str = Field(description='What it does, in words.')
    version: int
    created_at: str
    created_by: str
    updated_at: str


class CheckLine(Model):
    line_id: ID
    date: str
    amount: Units
    amount_decimal: str
    payee: str


class Check(Model):
    fingerprint: str = Field(description='Pass as confirm to make it live (or keep it live through this change).')
    does: str = Field(description='Exactly what it will do, in words.')
    waiting: Count = Field(description='Waiting lines it fits now (it acts only on lines of later imports).')
    lines: tuple[CheckLine, ...] = Field(description='The first of those lines.')


class RuleOutput(Model):
    rule: RuleOut
    check: Check | None = Field(None, description='For an auto-add or ignore rule: the one check of its exact details.')
    notes: tuple[str, ...] = ()
    summary: str
    next: str | None = None


class QueryOutput(Model):
    rules: tuple[RuleOut, ...]
    next_cursor: str | None


class Applied(Model):
    line_id: ID
    date: str
    amount_decimal: str
    payee: str
    rule_id: ID | None = Field(description='The rule it fits now (null: none fits any longer).')
    rule: str | None
    reason: str | None
    was_rule_id: ID | None


class ApplyOutput(Model):
    lines: tuple[Applied, ...] = Field(description='The waiting lines whose rule proposal changes (changed, unless a '
                                                   'dry run).')
    read: Count
    summary: str
    next: str | None = None
