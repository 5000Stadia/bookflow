"""Till receipt read, shared by every interface and printed output."""
from bookflow.company import pos_receipts as r
from bookflow.core.registry import command, Plan

def plan_show(inp, ctx, s):
    return Plan(r.read(s, inp))

show = command('pos receipt show', scope='company', description=(
    'Read a completed POS receipt for printing or reprinting. Original posted lines and totals survive voiding; '
    'voided copies say VOID. Needs pos.sell; no accounting details are exposed. Off unless pos_enabled is on.'),
    input_model=r.ReceiptInput, output_model=r.ReceiptOutput, writes=set(), required_role='member',
    capability='pos.sell', positional=['sale'], error_codes=['E_FEATURE_DISABLED', 'E_RECORD_NOT_FOUND', 'E_VALIDATION'])(plan_show)
show.authorize_input = r.authorize
