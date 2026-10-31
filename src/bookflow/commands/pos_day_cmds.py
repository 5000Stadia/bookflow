"""Daily POS reports and nonposting drawer counts."""
from bookflow.company import pos_day
from bookflow.company import pos_day_models as m
from bookflow.core.registry import Plan, command

_ERRORS = ['E_FEATURE_DISABLED', 'E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_WORK_DEPENDENCY', 'E_IDEMPOTENCY_MISMATCH']

@command('report pos-day',scope='company',description='Day summary for a register, or a frozen closed-shift Z report: sales, tax, voids, refunds by tender, old held tickets and expected cash in the drawer. Date defaults to today in the company timezone. Needs pos.sell.',input_model=m.DayInput,output_model=m.DayOutput,writes=set(),required_role='member',capability='pos.sell',error_codes=_ERRORS)
def pos_day(inp,ctx,s):
    from bookflow.company import pos_day as core
    core.authorize(s)
    return Plan(core.report(s,inp))

@command('pos shift open',scope='company',description='Open a counted register shift with an opening float. One open shift per register; permanent operation_key makes retries safe. Posts nothing. Needs pos.sell.',input_model=m.ShiftOpenInput,output_model=m.ShiftOutput,writes={'company'},required_role='member',capability='pos.sell',accepts_idempotency_key=True,error_codes=_ERRORS)
def shift_open(inp,ctx,s):
    from bookflow.company import pos_day as core
    return core.prepare_open(s,ctx,inp)

@command('pos shift close',scope='company',description='Close a shift with counted cash; expected equals float plus cash sales less change and cash refunds. Captures a read-only Z report and over/short. Posts nothing. Retry the same permanent operation_key and count safely. Needs pos.sell; making a deposit separately needs ledger.post.',input_model=m.ShiftCloseInput,output_model=m.ShiftOutput,writes={'company'},required_role='member',capability='pos.sell',accepts_idempotency_key=True,error_codes=_ERRORS)
def shift_close(inp,ctx,s):
    from bookflow.company import pos_day as core
    return core.prepare_close(s,ctx,inp)

from bookflow.company import pos_day as _core
shift_open.applier(_core.apply_open)
shift_close.applier(_core.apply_close)
