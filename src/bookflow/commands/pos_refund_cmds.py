"""Receipt and no-receipt returns at the till."""
from bookflow.company import pos_refunds as r, pos_refund_models as m
from bookflow.core.registry import Plan, command

ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_FEATURE_DISABLED', 'E_PERMISSION', 'E_PERIOD_CLOSED',
          'E_VALUE_RANGE', 'E_AMOUNT_PRECISION', 'E_INACTIVE_REFERENCE', 'E_PREVIEW_STALE',
          'E_IDEMPOTENCY_MISMATCH', 'E_WORK_DEPENDENCY', 'E_RETURN_EXHAUSTED', 'E_SOURCE_CORRECTION_CONFLICT']


def plan_refund(inp, ctx, s):
    return r.prepare(s, ctx, inp)


def plan_lines(inp, ctx, s):
    return Plan(r.lines(s, inp))


def plan_show(inp, ctx, s):
    return Plan(r.show(s, inp))


refund = command('pos refund', scope='company', description=(
    'Return selected POS receipt lines, or items without a receipt. Posts a credit memo and a customer refund '
    'atomically; store_credit leaves the memo open for a named customer and refuses the shared walk-in. '
    'Receipt returns preserve captured price, tax and stock cost and cannot exceed sold quantities less active '
    'returns. No-receipt returns use current or entered prices and current average inventory value (purchase '
    'cost when no positive stock exists). Cash and original-method refunds leave the register refund bank account; '
    'card references are recorded, not sent to a processor. A corrected receipt needs a bookkeeper credit memo. '
    'Use dry_run to preview, then expected_total to guard the amount. operation_key never expires: retry the same '
    'request after a lost answer. Requires pos.refund; this also authorizes no-receipt prices.'),
    input_model=m.PosRefundInput, output_model=m.PosRefundOutput, writes={'company'}, required_role='member',
    capability='pos.refund', accepts_idempotency_key=True, error_codes=ERRORS)(plan_refund)
refund.authorize_input = r.authorize
refund.applier(r.apply)
refund.ledger = True

lines = command('pos refund lines', scope='company', description=(
    'Find a POS sale by receipt barcode/number, sale ID or ticket key. Return its line identities and remaining '
    'returnable quantities, including hand-entered credit memo claims. Requires pos.refund.'),
    input_model=m.PosRefundLinesInput, output_model=m.PosRefundLinesOutput, writes=set(), required_role='member',
    capability='pos.refund', error_codes=ERRORS[:4] + ['E_WORK_DEPENDENCY'])(plan_lines)
lines.authorize_input = r.authorize

show = command('pos refund show', scope='company', description=(
    'Read captured till facts of a completed refund by POS refund ID, operation key, credit memo ID/number or '
    'customer refund ID, without customer account or cost details. Requires pos.refund.'),
    input_model=m.PosRefundShowInput, output_model=m.PosRefundOutput, writes=set(), required_role='member',
    capability='pos.refund', error_codes=ERRORS[:4] + ['E_WORK_DEPENDENCY'])(plan_show)
show.authorize_input = r.authorize
