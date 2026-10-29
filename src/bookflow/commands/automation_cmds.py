"""`automation`: the rules that write with nobody asking at the time, across kinds (R187 increment 2,
design/principal-directives.md §3). The work lives in ``company/automation.py``.

Capability `automation` (reads at member, pause at standard), checked beside each rule's own kind's commands: a rule
is read only by a caller its kind's `show` admits, and paused only by one its kind's `update` admits, so `automation`
grants nothing. Making a rule live is never done here: each kind's own command does it, checked against
`automate.<kind>`.
"""
from bookflow.company import automation
from bookflow.company import automation_models as m
from bookflow.company import delivery_schedules  # noqa: F401 - registers the delivery kind
from bookflow.core.registry import Applied, Plan, command

_LIST = ('The automations: rules that write with nobody asking at the time (a send-mode delivery schedule; bank rules '
         'and invoice schedules once they exist), each with its state, who established it and, for a live one whose '
         'establisher no longer holds the authority, why its next firing pauses it.')
_SHOW = ('One automation: what it does unattended, its state, who made it live and when, why it is paused or back in '
         'draft, the capability making it live needs, and the command that does it. Example: {"kind": "delivery", '
         '"rule": "Daily invoices"}.')
_PAUSE = ('Pause a live automation: it runs nothing until a holder of its automate.<kind> capability makes it live '
          'again. Open to anyone who may edit the rule (stopping is safe); its establisher gets one to-do. '
          'Example: {"kind": "delivery", "rule": "Daily invoices"}.')


def _output(s, k, row, warnings=()) -> m.AutomationOutput:
    return m.AutomationOutput(**automation.describe(s, k, row), show_command=k.show_command,
                              live_command=k.live_command, warnings=list(warnings))


def plan_list(inp, ctx, s) -> Plan:
    kinds = [automation.kind_of(inp.kind)] if inp.kind else [automation.KINDS[x] for x in sorted(automation.KINDS)]
    rows = []
    for k in kinds:
        if not automation.may(s, k.show_command):
            continue
        for row in k.rows(s, inp.state):
            d = automation.describe(s, k, row)
            rows.append(m.AutomationRow(**{f: d.get(f) for f in m.AutomationRow.model_fields}))
    return Plan(m.AutomationPageOutput(rows=rows[:inp.limit], count=len(rows), truncated=len(rows) > inp.limit))


def plan_show(inp, ctx, s) -> Plan:
    k = automation.kind_of(inp.kind)
    automation.require_command(s, k.show_command)
    return Plan(_output(s, k, k.get(s, inp.rule)))


def plan_pause(inp, ctx, s) -> Plan:
    k = automation.kind_of(inp.kind)
    automation.require_command(s, k.edit_command)
    row = k.get(s, inp.rule)
    if row['state'] != 'live':
        said = 'It is already paused.' if row['state'] == 'paused' else 'It is a draft: it runs nothing.'
        return Plan(_output(s, k, row, [said]), dict(idle=True))
    why = f'paused by {automation.name_of(s, s.actor.id)}' + (f': {ctx.reason}' if ctx.reason else '')
    preview = _output(s, k, dict(row, state='paused', paused_reason=why[:500]))
    return Plan(preview, dict(kind=k.kind, row=row, why=why))


def apply_pause(plan, ctx, s) -> Applied:
    if plan.data.get('idle'):
        return Applied(plan.preview, [], 'nothing changed')
    k = automation.KINDS[plan.data['kind']]
    current = k.get(s, plan.data['row']['id'])
    if current['version'] != plan.data['row']['version']:
        from bookflow.core.errors import BookflowError
        raise BookflowError('E_VERSION_CONFLICT', details={'record_type': k.record_type, 'record_id': current['id']})
    after, touched = k.pause(s, ctx, current, plan.data['why'])
    return Applied(_output(s, k, after), touched, f"automation {after['name']} paused")


_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND']
automation_list = command('automation list', scope='company', description=_LIST,
                          input_model=m.AutomationQueryInput, output_model=m.AutomationPageOutput,
                          required_role='member', capability='automation', error_codes=['E_VALIDATION'])(plan_list)
automation_show = command('automation show', scope='company', description=_SHOW, input_model=m.AutomationSelector,
                          output_model=m.AutomationOutput, required_role='member', capability='automation',
                          positional=['kind', 'rule'], error_codes=[*_ERRORS, 'E_PERMISSION'])(plan_show)
automation_pause = command('automation pause', scope='company', description=_PAUSE, input_model=m.AutomationSelector,
                           output_model=m.AutomationOutput, writes={'company'}, required_role='standard',
                           capability='automation', accepts_idempotency_key=True, positional=['kind', 'rule'],
                           error_codes=[*_ERRORS, 'E_PERMISSION', 'E_VERSION_CONFLICT', 'E_IDEMPOTENCY_MISMATCH',
                                        'E_DIRECTIVE_NOT_FOUND', 'E_DIRECTIVE_INACTIVE'])(plan_pause)
automation_pause.applier(apply_pause)
