"""Public register commands; all accounting runs through the journal service."""
from bookflow.core.registry import command, Plan
from bookflow.company import registers
from bookflow.company.register_models import (
    RegisterPostInput, RegisterUpdateInput, RegisterWriteOutput,
    RegisterCalculateInput, RegisterCalculateOutput,
)
from bookflow.company.register_query import RegisterQueryInput, RegisterQueryOutput, query


_REFERENCE_ERRORS = ['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION',
                     'E_VALUE_RANGE', 'E_AMOUNT_PRECISION']


def _write(verb, model):
    def planner(inp, ctx, s):
        return registers.prepare(s, ctx, inp, verb)
    cmd = command('register ' + verb, scope='company', description={
        'post': 'Post a domestic account movement with a category or signed split allocations.',
        'update': 'Replace the complete editable register entry while retaining selected and supplied allocation identities.',
    }[verb], input_model=model, output_model=RegisterWriteOutput, writes={'company'},
        required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
        positional=[] if verb == 'post' else ['journal'], clearable=verb == 'update',
        version_source=None if verb == 'post' else ('journal show', 'journal', 'version'),
        error_codes=[*_REFERENCE_ERRORS, 'E_VERSION_CONFLICT', 'E_UNBALANCED_ENTRY',
                     'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER', 'E_REASON_REQUIRED'])(planner)
    cmd.ledger = True
    cmd.applier(registers.apply)
    return cmd


register_post = _write('post', RegisterPostInput)
register_update = _write('update', RegisterUpdateInput)


@command('register calculate', scope='company',
         description='Calculate exact positive split net movement; validate offset accounts, parties and explicit classes without posting.',
         input_model=RegisterCalculateInput, output_model=RegisterCalculateOutput,
         required_role='member', capability='ledger.read', error_codes=_REFERENCE_ERRORS)
def register_calculate(inp, ctx, s):
    return Plan(registers.calculate(inp, s))


@command('register query', scope='company',
         description='Page account register history with normal-side balances and a separate all-entries balance snapshot.',
         input_model=RegisterQueryInput, output_model=RegisterQueryOutput,
         required_role='member', capability='ledger.read',
         error_codes=[*_REFERENCE_ERRORS, 'E_QUERY_STALE'])
def register_query(inp, ctx, s):
    return Plan(query(inp, s, principal_id=ctx.on_behalf_of))


REGISTER_COMMANDS = [register_post, register_update, register_calculate, register_query]
