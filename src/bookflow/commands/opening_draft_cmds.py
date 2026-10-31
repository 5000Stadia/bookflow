"""`opening-draft`: guided opening balances for a business with no old books (R107 increment 7, design/bulk-import.md
§6). The work lives in ``company/opening_drafts.py``; every posting is the `cutover` engine's, through the ordinary
commands, as one batch of the shared ledger.

Capability `import` (reads at member, writes at standard), checked beside each posting's own command capability:
the draft grants nothing. `accept_differences` needs the capability `opening.accept-differences` besides.
"""
from bookflow.company import opening_drafts as od
from bookflow.company import opening_draft_models as m
from bookflow.core.registry import Applied, Plan, Touched, command

_C = ('E_VALIDATION', 'E_RECORD_NOT_FOUND')
_CHECK = (' Before `apply`, show your principal the plan\'s exact details once (the as-of date, the counts and totals, '
          'and which checks are given and which derived) and apply on their yes: an opening sets how the books start.')
_CREATE = (
    'Start guided opening balances for a business with no old books: keep what the owner knows as of the cutover '
    'date, each step as rows with a header row, pasted or typed: ' + m.STEPS_HELP + '. Give `given` totals '
    '(receivables, payables, stock) to check the rows against; a total left out is taken from the rows and labelled '
    'derived. Nothing posts: then `opening-draft plan`. Example: {"as_of": "2026-09-30", "balances": {"content": '
    '"Account\\tBalance\\nChecking\\t12430.55\\n"}, "given": {"payables": "3100.00"}}.')
_PLAN = (
    'Plan a draft through the cutover engine, writing nothing to the books: every record it would make, the opening '
    'journal, the clearing account\'s parts and the checks, each `given` (a figure the owner gave, checked against the '
    'rows) or `derived` (taken from the rows; nothing independent checks it), and every exception, blocking first. '
    '`ready` is true when nothing blocks.' + _CHECK)
_APPLY = (
    'Post a planned opening through the cutover engine, as one batch: the accounts\' balances in one opening journal '
    'against a clearing account, each open invoice and bill for its open amount, each item\'s stock. All or nothing: a '
    'command refused part way undoes what the opening had posted (rolled_back). A run stopped any other way stays '
    'applying and the same call continues it. Refused while the plan has a blocking exception; `accept_differences` '
    '(a person\'s act; an agent needs the capability opening.accept-differences) posts one whose given totals do not '
    'tie, the difference left in the clearing account.' + _CHECK)


def _touched(draft_id: str, action: str, before: int | None, after: int, values: dict) -> list[Touched]:
    shown = {k: values[k] for k in ('label', 'as_of', 'state') if k in values}
    return [Touched('opening_draft', draft_id, action, before, after, shown, db='company')]


def plan_create(inp, ctx, s) -> Plan:
    values = od.prepare_create(s, ctx, inp)
    preview = od.out(s, values)
    return Plan(m.DraftOutput(**preview), {'values': values})


def apply_create(plan: Plan, ctx, s) -> Applied:
    values = plan.data['values']
    od.insert(s, values)
    return Applied(plan.preview, _touched(values['id'], 'create', None, 1, values), f"started {values['label']}")


def plan_update(inp, ctx, s) -> Plan:
    draft, changes = od.prepare_update(s, ctx, inp)
    return Plan(m.DraftOutput(**od.out(s, {**draft, **changes})), {'draft': draft, 'changes': changes})


def apply_update(plan: Plan, ctx, s) -> Applied:
    draft, changes = plan.data['draft'], plan.data['changes']
    od.write(s, draft['id'], changes)
    return Applied(plan.preview, _touched(draft['id'], 'update', draft['version'], changes['version'], {**draft, **changes}),
                   f"changed {draft['label']}")


def plan_discard(inp, ctx, s) -> Plan:
    draft, changes = od.prepare_discard(s, inp)
    return Plan(m.DraftOutput(**od.out(s, {**draft, **changes})), {'draft': draft, 'changes': changes})


def plan_show(inp, ctx, s) -> Plan:
    return Plan(m.DraftOutput(**od.out(s, od.row(s, inp.draft))))


def plan_query(inp, ctx, s) -> Plan:
    return Plan(m.QueryOutput(rows=od.query(s, inp)))


def plan_plan(inp, ctx, s) -> Plan:
    draft = od.row(s, inp.draft)
    if draft['state'] not in od.WRITABLE:
        raise od.BookflowError('E_VALIDATION', message=(
            f"Opening draft {draft['id']} is {draft['state']}; a plan is of a draft not yet applied"
            + (f" (`opening-draft tie-out {draft['id']}` proves an applied one)." if draft['state'] == 'applied' else ".")),
            details={'field': 'draft', 'state': draft['state']})
    result = od.plan(s, ctx, draft)
    changes = {'state': 'planned', 'summary': od.summary(result), 'version': draft['version'] + 1, 'updated_at': od.now()}
    after = od.out(s, {**draft, **changes}, next_=f"opening-draft apply {draft['id']}" if result.ready else
                   f"opening-draft update {draft['id']}")
    return Plan(m.PlanOutput(draft=after, plan=result), {'draft': draft, 'changes': changes})


def plan_apply(inp, ctx, s) -> Plan:
    planned = od.prepare_apply(s, ctx, inp)
    draft = planned.data['draft']
    return Plan(m.ApplyOutput(draft=od.out(s, draft), apply=planned.preview), {'planned': planned})


def apply_apply(plan: Plan, ctx, s) -> Applied:
    draft, result = od.apply(plan.data['planned'], ctx, s)
    out = m.ApplyOutput(draft=od.out(s, draft), apply=result, outside_batch=od.outside_batch(result))
    return Applied(out, [], result.summary, finalized=True, audited=True)


def plan_tie_out(inp, ctx, s) -> Plan:
    draft = od.row(s, inp.draft)
    return Plan(m.TieOutOutput(draft=od.out(s, draft), tie_out=od.tie_out(s, ctx, draft, inp.detail)))


def _reverse_out(draft, done) -> m.ReverseOutput:
    return m.ReverseOutput(draft=draft, **{k: done.get(k) for k in m.ReverseOutput.model_fields if k != 'draft'})


def plan_reverse(inp, ctx, s) -> Plan:
    if s.dry_run:
        draft, done = od.reverse(s, ctx, inp, dry_run=True)
        return Plan(_reverse_out(od.out(s, draft), done), {'input': inp})
    draft = od.row(s, inp.draft)
    return Plan(m.ReverseOutput(draft=od.out(s, draft), batch={}, state='live', reversed_now=0, remaining=0, steps=[],
                                blockers=[], fingerprint=None, reversal_batch=None, event_id=None), {'input': inp})


def apply_reverse(plan: Plan, ctx, s) -> Applied:
    from bookflow.commands.import_cmds import _finalized

    def work():
        draft, done = od.reverse(s, ctx, plan.data['input'], dry_run=False)
        return _reverse_out(od.out(s, draft), done).model_dump()
    return _finalized(s, ctx, 'opening-draft.reverse', work, m.ReverseOutput)


def _read(name, description, inp, out, planner, *, positional=(), errors=_C):
    return command(name, scope='company', description=description, input_model=inp, output_model=out,
                   required_role='member', capability='import', positional=list(positional),
                   error_codes=list(errors))(planner)


def _write(name, description, inp, out, planner, applier, *, positional=(), errors=_C, **kw):
    cmd = command(name, scope='company', description=description, input_model=inp, output_model=out,
                  writes={'company'}, required_role='standard', capability='import', positional=list(positional),
                  error_codes=list(errors), **kw)(planner)
    cmd.applier(applier)
    return cmd


def _state_only(plan: Plan, ctx, s) -> Applied:
    draft, changes = plan.data['draft'], plan.data['changes']
    od.write(s, draft['id'], changes)
    return Applied(plan.preview, _touched(draft['id'], 'update', draft['version'], changes['version'], {**draft, **changes}),
                   f"{changes['state']} {draft['label']}")


opening_draft_create = _write('opening-draft create', _CREATE, m.CreateInput, m.DraftOutput, plan_create, apply_create,
                              positional=('as_of',))
opening_draft_update = _write(
    'opening-draft update', 'Change a draft not yet applied: a step\'s rows (an empty `content` clears the step), the '
    'cutover date, the totals given, the stock items confirmed to make, the clearing account. Carries the version '
    'read. The draft returns to `draft` and needs a new plan. Example: {"draft": "01J...", "expected_version": 2, '
    '"open_invoices": {"content": "Customer,Number,Date,Terms,Open amount\\nAcme,1001,09/02/2026,Net 30,412.50\\n"}}.',
    m.UpdateInput, m.DraftOutput, plan_update, apply_update, positional=('draft',),
    errors=('E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'))
opening_draft_show = _read('opening-draft show', 'A draft as kept: each step\'s rows with what they alone show to be wrong '
                           '(line, code, problem), the totals given, its state and its last plan or apply.',
                           m.DraftInput, m.DraftOutput, plan_show, positional=('draft',))
opening_draft_query = _read('opening-draft query', 'List opening drafts, newest first, by state.', m.QueryInput,
                            m.QueryOutput, plan_query, errors=('E_VALIDATION',))
opening_draft_discard = _write('opening-draft discard', 'Put a draft not yet applied aside (state discarded); it is '
                               'kept, never deleted. An applied opening is reversed instead (`opening-draft reverse`).',
                               m.DraftInput, m.DraftOutput, plan_discard, _state_only, positional=('draft',))
opening_draft_plan = _write('opening-draft plan', _PLAN, m.DraftInput, m.PlanOutput, plan_plan, _state_only,
                            positional=('draft',))
opening_draft_apply = _write('opening-draft apply', _APPLY, m.ApplyInput, m.ApplyOutput, plan_apply, apply_apply,
                             positional=('draft',),
                             errors=('E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_CUTOVER_BLOCKED', 'E_CUTOVER_INCOMPLETE',
                                     'E_VERSION_CONFLICT', 'E_PERMISSION', 'E_REASON_REQUIRED'))
opening_draft_tie_out = command(
    'opening-draft tie-out', scope='company', required_role='standard', capability='import', positional=['draft'],
    error_codes=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_DB_BUSY', 'E_QUERY_STALE'], input_model=m.TieOutInput,
    output_model=m.TieOutOutput, description=( 'Prove an applied opening: the books as of the cutover date against every figure, each '
    'check `given` ("tied to the 12,430.55 you gave") or `derived` ("taken from your invoice list; not independently '
    'checked"), receivables and payables by customer and vendor, stock by item, and the clearing account at 0.00. '
    '`tied` needs every check tied and the clearing account at 0.00. Writes nothing.'))(plan_tie_out)
opening_draft_reverse = _write(
    'opening-draft reverse', 'Undo an applied opening as one unit through its batch, for as long as nothing later '
    'depends on it: each invoice, credit memo, bill, journal and sales receipt deleted with its history kept, each '
    'vendor credit and stock adjustment voided, each customer, vendor, item and term it made renamed and deactivated. '
    'Accounts are not retired (an account keeps its history): the clearing account stays at 0.00 for the next '
    'opening; `stays` lists that and anything else the opening set outside its batch. Refused (E_BATCH_BLOCKED), '
    'naming each record and what clears it, while one is in use (a payment applied to an opening invoice, stock it '
    'brought in sold since) or was changed since; then nothing changes. Needs your own delete grants for those '
    'families. --dry-run shows the plan and a fingerprint. Confirm with your principal first.',
    m.ReverseInput, m.ReverseOutput, plan_reverse, apply_reverse, positional=('draft',),
    errors=('E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_BATCH_BLOCKED', 'E_VERSION_CONFLICT'))

OPENING_DRAFT_COMMANDS = [opening_draft_create, opening_draft_update, opening_draft_show, opening_draft_query,
                          opening_draft_discard, opening_draft_plan, opening_draft_apply, opening_draft_tie_out,
                          opening_draft_reverse]
