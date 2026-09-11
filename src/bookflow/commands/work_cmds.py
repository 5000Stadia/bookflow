"""Discoverable customer-work operations shared by library, CLI and HTTP."""
from bookflow.core.registry import command, Plan
from bookflow.company import work, work_models as models
from bookflow.company.work_outputs import WorkWriteOutput, WorkOutput, WorkPageOutput, WorkHistoryOutput

WRITE_DESCRIPTIONS = {
    'create': 'Create a non-posting customer work document with captured scope, commercial defaults, exact prices and estimated costs. Preview resolved facts before saving.',
    'update': 'Revise the whole work document using its expected version. Preserve immutable history; acceptance and completion changes follow explicit lifecycle constraints.',
    'copy': 'Copy captured work into an independent draft with new identities; estimate copies may be alternatives in the same group. Source notes and files remain linked.',
    'estimate': 'Make an estimate from this proposal using a permanent conversion key. Preserve the selected source revision and return the original destination on retry.',
    'work-order': 'Make one work order from the accepted estimate using a permanent conversion key. Preserve agreed facts and shared billing roots without posting any sale.',
    'complete': 'Mark work complete, recording actual start/end and filling remaining completed quantities in preview. This records completion without invoicing or claiming payment.',
    'void': 'Withdraw an estimate with a required reason. An estimate posts nothing, so nothing is reversed and no money moves; what changes is that it can no longer become an invoice, a sales receipt or a work order, and it stays readable in full -- every revision, line and captured fact, with the reason recorded as its decision. Terminal: a voided estimate cannot be updated, completed or voided again, and it is refused while a work order or a bill already consumes its work. Copying it into a fresh draft still works, which is how a withdrawn quote is re-quoted.',
}
READ_DESCRIPTIONS = {
    'show': 'Show current or historical work facts, exact quoted totals, costs, completed quantities and source links; internal notes remain internal.',
    'query': 'Find work by customer, number, title, date, status, availability and net amount, oldest first or newest first. Continue bounded pages while the company audit watermark is unchanged.',
    'history': 'Page immutable work revisions in revision order, including decision evidence, linked source revisions and current document identity.',
}


def _register(kind, prefix):
    noun = kind.replace('_', '-')
    commands = []
    verbs = ['create', 'update', 'copy', 'show', 'query', 'history',
             'estimate' if kind == 'proposal' else 'work-order' if kind == 'estimate' else 'complete']
    if kind == 'estimate':
        # Only the estimate carries the verb today: the proposal is a draft nobody has agreed
        # to, and a work order is withdrawn by cancelling the work rather than the quote.
        verbs.append('void')
    for verb in verbs:
        model_name = prefix + ''.join(word.title() for word in verb.split('-')) + 'Input'
        model = models.WorkQueryInput if verb == 'query' else getattr(models, model_name)
        write = verb in WRITE_DESCRIPTIONS
        def planner(inp, ctx, s, kind=kind, verb=verb, write=write):
            if write:
                return work.prepare(s, ctx, inp, kind, verb)
            if verb == 'show':
                return Plan(work.show(s, inp, kind, ctx))
            return Plan(work.page(s, ctx, inp, kind, history=verb == 'history'))
        cmd = command(noun + ' ' + verb, scope='company',
            description=(WRITE_DESCRIPTIONS if write else READ_DESCRIPTIONS)[verb],
            input_model=model, output_model=WorkWriteOutput if write else WorkOutput if verb == 'show' else WorkPageOutput if verb == 'query' else WorkHistoryOutput,
            writes={'company'} if write else set(), required_role='standard' if write else 'member',
            capability='customer-work', accepts_idempotency_key=write,
            positional=[] if verb in ('create', 'query') else [kind], clearable=verb == 'update',
            version_source=(noun + ' show', kind, 'version') if write and verb != 'create' else None,
            error_codes=['E_RECORD_NOT_FOUND'] + (['E_VERSION_CONFLICT', 'E_DUPLICATE_NUMBER', 'E_INACTIVE_REFERENCE',
                'E_VALUE_RANGE', 'E_AMOUNT_PRECISION', 'E_REASON_REQUIRED', 'E_PREVIEW_STALE', 'E_WORK_DEPENDENCY', 'E_FEATURE_DISABLED',
                'E_CONVERSION_KEY_REUSED'] if write else ['E_QUERY_STALE']),
        )(planner)
        if write:
            cmd.applier(work.apply)
        if verb in ('estimate', 'work-order'):
            def replay(inp, ctx, s, hit, kind=kind, verb=verb):
                return work.replay_conversion(s, ctx, inp, kind, verb, hit)
            cmd.replay = replay
        commands.append(cmd)
    return commands


WORK_COMMANDS = _register('proposal', 'Proposal') + _register('estimate', 'Estimate') + _register('work_order', 'WorkOrder')
