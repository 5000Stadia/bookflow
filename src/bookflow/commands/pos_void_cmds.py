"""A POS void has the same reversal and immutable history as a typed receipt void."""
from bookflow.company import pos_void
from bookflow.core.registry import command


def plan_void(inp, ctx, s):
    return pos_void.prepare(s, ctx, inp)


sale_void = command(
    'pos sale void', scope='company', description=(
        'Void a completed POS sale through the same reversal as sales-receipt void, with a required context reason '
        'and receipt expected_version. The sale selector accepts the POS sale ID, ticket key, receipt ID or number; '
        'an ordinary typed receipt is refused. Keeps the original receipt and immutable reversal history. '
        'A deposited sale is refused naming its deposit; a reconciled sale is refused. A sale with a POS refund is refused naming the refund. Needs pos.void, not pos.sell '
        'or ledger.post. Off unless company setting pos_enabled is on (E_FEATURE_DISABLED).'),
    input_model=pos_void.PosSaleVoidInput, output_model=pos_void.PosSaleVoidOutput,
    writes={'company'}, required_role='member', capability='pos.void', accepts_idempotency_key=True,
    positional=['sale'], error_codes=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_PERIOD_CLOSED',
        'E_VALIDATION', 'E_REASON_REQUIRED', 'E_FEATURE_DISABLED', 'E_DEPOSIT_DEPENDENCY',
        'E_RECONCILIATION_DEPENDENCY', 'E_WORK_DEPENDENCY'])(plan_void)
sale_void.applier(pos_void.apply)
sale_void.authorize_input = pos_void.authorize
sale_void.authorization = 'member pos.void'
sale_void.ledger = True
