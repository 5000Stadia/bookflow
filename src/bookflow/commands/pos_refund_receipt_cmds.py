"""Print the captured facts of a POS return through the thermal receipt route."""
from bookflow.company import pos_refund_receipts as r, pos_refunds
from bookflow.core.registry import command, Plan


def plan_show(inp, ctx, s):
    return Plan(r.read(s, inp))


show = command('pos refund receipt', scope='company', description=(
    'Read a POS refund for printing or reprinting, by refund ID, operation key, credit memo ID or number in sale. '
    'The receipt says REFUND and identifies money returned or store credit. Captured lines and totals survive '
    'subsequent document changes. Needs pos.refund; off unless pos_enabled is on.'),
    input_model=r.RefundReceiptInput, output_model=r.RefundReceiptOutput, writes=set(), required_role='member',
    capability='pos.refund', positional=['sale'],
    error_codes=['E_FEATURE_DISABLED','E_RECORD_NOT_FOUND','E_VALIDATION','E_PERMISSION'])(plan_show)
show.authorize_input = pos_refunds.authorize
