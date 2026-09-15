"""Discoverable job time-tracking operations shared by library, CLI, HTTP and MCP."""
from bookflow.core.registry import command, Plan
from bookflow.company import time_tracking, work, work_models as models
from bookflow.company.work_outputs import WorkWriteOutput, WorkOutput, WorkPageOutput, WorkHistoryOutput

NOUN = 'time-activity'

DESCRIPTIONS = {
    'create': 'Record time somebody worked for a customer or job: who did it, on what date, how long, '
              'and the service item it is charged as. Duration is decimal hours, so 1.5 is an hour and a '
              'half and 0.25 is fifteen minutes. The service item is required, not optional: it is what '
              'carries the hourly rate, the income account the labour lands in and the tax code an invoice '
              'needs, so time recorded without one would have nowhere to post. Give a rate only to override '
              'the item price for this entry. Billable time waits to be carried onto an invoice; '
              'non-billable time is recorded against the job and never billed. Recording time posts nothing '
              'to the ledger and claims no payment.',
    'update': 'Correct recorded time using its expected version and a reason. The prior revision stays '
              'readable in full; what was already invoiced from this entry stays invoiced, and the '
              'correction is refused rather than silently reducing time a bill is standing on.',
    'void': 'Withdraw recorded time with a required reason. It posted nothing, so nothing is reversed and no '
            'money moves; what changes is that it can no longer reach an invoice, and it stays readable in '
            'full -- every revision and captured fact, with the reason recorded as its decision. Terminal: '
            'voided time cannot be updated or voided again, and it is refused while a bill already consumes it.',
    'show': 'Show recorded or historical time: who, which job, the date, the duration, the service item, '
            'the charge and whether it is billable.',
    'query': 'Find recorded time by customer or job, number, date, status and amount, oldest first or newest '
             'first. Continue bounded pages while the company audit watermark is unchanged.',
    'history': 'Page immutable revisions of one time entry in revision order, including the reason each '
               'correction was made for.',
}

ERRORS = {
    'create': ['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION', 'E_VALUE_RANGE',
               'E_AMOUNT_PRECISION', 'E_DUPLICATE_NUMBER', 'E_PREVIEW_STALE'],
    'update': ['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION', 'E_VALUE_RANGE',
               'E_AMOUNT_PRECISION', 'E_DUPLICATE_NUMBER', 'E_PREVIEW_STALE', 'E_VERSION_CONFLICT',
               'E_REASON_REQUIRED', 'E_WORK_DEPENDENCY'],
    'void': ['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_VERSION_CONFLICT', 'E_REASON_REQUIRED',
             'E_PREVIEW_STALE', 'E_WORK_DEPENDENCY'],
    'show': ['E_RECORD_NOT_FOUND'],
    'query': ['E_RECORD_NOT_FOUND', 'E_QUERY_STALE'],
    'history': ['E_RECORD_NOT_FOUND', 'E_QUERY_STALE'],
}

WRITE_VERBS = ('create', 'update', 'void')
OUTPUTS = {'show': WorkOutput, 'query': WorkPageOutput, 'history': WorkHistoryOutput}


def _register(verb):
    write = verb in WRITE_VERBS
    model = (models.WorkQueryInput if verb == 'query' else
             getattr(models, 'TimeActivity' + verb.title() + 'Input'))

    def planner(inp, ctx, s, verb=verb, write=write):
        if write:
            return time_tracking.prepare(s, ctx, inp, verb)
        if verb == 'show':
            return Plan(time_tracking.show(s, inp, ctx))
        return Plan(time_tracking.page(s, ctx, inp, history=verb == 'history'))

    cmd = command(NOUN + ' ' + verb, scope='company', description=DESCRIPTIONS[verb],
        input_model=model, output_model=WorkWriteOutput if write else OUTPUTS[verb],
        writes={'company'} if write else set(), required_role='standard' if write else 'member',
        capability='customer-work', accepts_idempotency_key=write,
        positional=[] if verb in ('create', 'query') else ['time_activity'],
        clearable=verb == 'update',
        version_source=(NOUN + ' show', 'time_activity', 'version') if write and verb != 'create' else None,
        error_codes=ERRORS[verb])(planner)
    if write:
        # The plan already carries the translated customer-work input, so the shared applier
        # re-plans and writes it exactly as it does for every other work kind.
        cmd.applier(work.apply)
    return cmd


TIME_COMMANDS = [_register(verb) for verb in ('create', 'update', 'void', 'show', 'query', 'history')]
