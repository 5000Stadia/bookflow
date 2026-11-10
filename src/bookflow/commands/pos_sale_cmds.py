"""Ringing up at the POS (R114 increment 5, design/point-of-sale.md §3, §4): preview, complete, and the customer chip.

Off unless company setting `pos_enabled` is on (E_FEATURE_DISABLED). The work lives in ``company/pos_sales.py``.
"""
from bookflow.company import pos_sales
from bookflow.company import pos_sale_models as m
from bookflow.core.registry import Plan, command

_OFF = ' Off unless company setting pos_enabled is on (E_FEATURE_DISABLED).'
_SALE_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_FEATURE_DISABLED', 'E_PERIOD_CLOSED', 'E_COMMISSION_REQUIRED',
                'E_VALUE_RANGE', 'E_AMOUNT_PRECISION']


def plan_preview(inp, ctx, s) -> Plan:
    return Plan(pos_sales.preview(s, ctx, inp))


def plan_complete(inp, ctx, s) -> Plan:
    return pos_sales.plan_complete(s, ctx, inp)


def plan_customer_search(inp, ctx, s) -> Plan:
    return Plan(pos_sales.search_customers(s, inp))


def plan_customer_add(inp, ctx, s) -> Plan:
    return pos_sales.plan_add_customer(s, ctx, inp)


def plan_commission_options(inp, ctx, s) -> Plan:
    from bookflow.company import commissions, pos_layer
    from bookflow.company.commission_models import RecipientOptionsInput
    pos_layer.require_enabled(s)
    pos_layer.require_seller(s)
    commissions.require_capability(s, 'commission.attribute', commissions.ATTRIBUTE_THRESHOLD)
    return Plan(commissions.recipient_options(s, RecipientOptionsInput(
        item=inp.item, customer=inp.customer, search=inp.search, limit=inp.limit, document_kind='pos_sale')))


sale_preview = command(
    'pos sale preview', scope='company', description=(
        "The exact totals of a cart, as `sales-receipt post`'s dry run gives them: the lines as they will post (price "
        "levels, price rules, discounts and subtotals resolved), tax and total, for the chosen customer or the "
        "register's walk-in (null on screen). Writes nothing. A typed unit_price needs pos.price-override. Needs "
        'pos.sell.' + _OFF),
    input_model=m.PosSalePreviewInput, output_model=m.PosSalePreviewOutput, writes=set(), required_role='member',
    capability='pos.sell', error_codes=_SALE_ERRORS + ['E_PERMISSION'])(plan_preview)
sale_preview.authorize_input = pos_sales.authorize
sale_preview.authorization = 'member pos.sell; pos.price-override when a line has unit_price'

sale_complete = command(
    'pos sale complete', scope='company', description=(
        'Complete a POS sale: posts the sales receipt `sales-receipt post` would, through the same core, with the '
        "facts the register fixes (its deposit account, its site, today's date, the chosen customer or its walk-in, "
        'payment methods it offers), and records the ticket, register, clerk and tenders. Split tenders post an invoice '
        'Due on receipt and one payment per distinct method in the same transaction. Supply tender for one method, '
        'or tenders with explicit positive amounts; only cash can give change. One payment method: cash '
        'takes at least the total and returns the change; any other method takes the total, with an optional '
        'reference. expected_total is the total shown (pos sale preview); a different total posts nothing '
        '(E_PREVIEW_STALE). The ticket key is the operation key: completing again with the same key returns the sale '
        'already posted and never posts twice. An agent records sales with `sales-receipt post`. Needs pos.sell; a '
        'typed unit_price also pos.price-override. While commission attribution is on, each commission-enabled line '
        'names its recipient (`commission`, or `member_commissions` on a group line; needs commission.attribute), '
        'else E_COMMISSION_REQUIRED names the lines.' + _OFF),
    input_model=m.PosSaleCompleteInput, output_model=m.PosSaleOutput, writes={'company'}, required_role='member',
    capability='pos.sell', accepts_idempotency_key=True,
    error_codes=_SALE_ERRORS + ['E_PERMISSION', 'E_PREVIEW_STALE', 'E_IDEMPOTENCY_MISMATCH', 'E_VERSION_CONFLICT', 'E_WORK_DEPENDENCY'])(plan_complete)
sale_complete.applier(pos_sales.apply_complete)
sale_complete.authorize_input = pos_sales.authorize
sale_complete.authorization = 'member pos.sell; pos.price-override when a line has unit_price'
sale_complete.ledger = True

customer_search = command(
    'pos customer search', scope='company', description=(
        "Find a customer at the till by part of their name, phone number or email: id, name, phone and email only, "
        "for the POS customer chip. Registers' walk-in customers are never listed. Needs pos.sell." + _OFF),
    input_model=m.CustomerSearchInput, output_model=m.CustomerSearchOutput, writes=set(), required_role='member',
    capability='pos.sell', positional=['q'], error_codes=['E_FEATURE_DISABLED'])(plan_customer_search)

customer_add = command(
    'pos customer add', scope='company', description=(
        'Add a customer at the till with a name and, optionally, a phone number and email; the rest of the record '
        'is filled in later from Customers. Needs pos.sell.' + _OFF),
    input_model=m.CustomerAddInput, output_model=m.CustomerAddOutput, writes={'company'}, required_role='member',
    capability='pos.sell', accepts_idempotency_key=True,
    error_codes=['E_VALIDATION', 'E_NAME_TAKEN', 'E_FEATURE_DISABLED'])(plan_customer_add)
customer_add.applier(pos_sales.apply_add_customer)

from bookflow.company.commission_models import PosCommissionOptionsInput, RecipientOptionsOutput  # noqa: E402

commission_options = command(
    'pos commission-options', scope='company', description=(
        "Who can get a till line's commission (salesperson, affiliate, \"who sold it\"): active recipient IDs and names "
        'only, with the customer\'s header rep as a suggestion, never a selection. No rates, earnings, payout or '
        'payroll details. commission_enabled false means the item (or the company) needs no recipient. Needs pos.sell '
        'and commission.attribute (the "Clerk with commission attribution" preset).' + _OFF),
    input_model=PosCommissionOptionsInput, output_model=RecipientOptionsOutput, writes=set(), required_role='member',
    capability='pos.sell', error_codes=['E_FEATURE_DISABLED', 'E_PERMISSION', 'E_RECORD_NOT_FOUND'])(plan_commission_options)
