"""POS day hand-off to the ordinary deposit command."""
from bookflow.company import pos_deposits as core
from bookflow.core.registry import command

@command('pos deposit prepare', scope='company',
    description='Prepare Make Deposits for a register day or closed shift, defaulting to cash methods. Returns an ordinary deposit post document; posts no receipt or deposit. Creates the named Cash Over and Short expense account on first need. Banked receipts are excluded; a partly banked cash bundle with a variance requires manual review. Cash refunds are already bank postings and are not deducted a second time. Requires ledger.post.',
    input_model=core.PrepareInput, output_model=core.PrepareOutput, writes={'company'},
    required_role='standard', capability='ledger.post',
    error_codes=['E_FEATURE_DISABLED', 'E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_WORK_DEPENDENCY'])
def plan_prepare(inp, ctx, s):
    return core.prepare(s, ctx, inp)

plan_prepare.applier(core.apply)
