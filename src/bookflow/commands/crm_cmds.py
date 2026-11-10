"""Lead lists and next-call follow-ups; no optional pipeline or campaign operations."""
from bookflow.company import crm, crm_models as m
from bookflow.core.registry import command, Plan


def plan_show(inp, ctx, s):
    return Plan(m.LeadOutput(**crm.project(s, crm.resolve(s, inp.lead))))


def plan_list(inp, ctx, s):
    return Plan(crm.page(s, ctx, inp))


def authorize(inp, ctx, s):
    crm.require_read(s)


_ERRORS = ['E_VALIDATION', 'E_PERMISSION', 'E_RECORD_NOT_FOUND', 'E_AMBIGUOUS_SELECTOR']
for _verb, _input, _planner in (('show', m.LeadSelector, plan_show), ('list', m.LeadListInput, plan_list)):
    _cmd = command('lead ' + _verb, scope='company', capability='crm.read', required_role='member',
        description='Show a prospect and its next accessible follow-up.' if _verb == 'show' else 'Page leads by name, lifecycle and owner with exact counts; optional CRM layers are not required.',
        input_model=_input, output_model=m.LeadOutput if _verb == 'show' else m.LeadPage,
        positional=['lead'] if _verb == 'show' else [], error_codes=[*_ERRORS, 'E_QUERY_STALE'])(_planner)


def _write(verb, input_model):
    def planner(inp, ctx, s):
        return crm.prepare(s, ctx, inp, verb)
    cmd = command('lead ' + verb, scope='company', capability='crm.write', required_role='standard',
        description={'create': 'Record a prospect with only a display name; contacts and ownership are optional. No customer or accounting entry is created.',
            'update': 'Edit lead identity and stable contact collections with an expected version.',
            'close': 'Close a lead with the command reason and an optional duplicate link; open follow-ups remain open.',
            'reopen': 'Reopen a closed lead explicitly; its notes and follow-ups retain their identities.',
            'archive': 'Archive or restore a lead in ordinary lists, retaining lifecycle, notes and open follow-ups.'}[verb],
        input_model=input_model, output_model=m.LeadWriteOutput, writes={'company'}, accepts_idempotency_key=True,
        positional=['display_name'] if verb == 'create' else ['lead'],
        version_source=None if verb == 'create' else ('lead show', 'lead', 'version'),
        error_codes=[*_ERRORS, 'E_VERSION_CONFLICT', 'E_IDEMPOTENCY_MISMATCH'])(planner)
    cmd.authorize_input = authorize
    cmd.plan_in_write = True
    cmd.replay = crm.replay
    cmd.applier(crm.apply)
    return cmd


create = _write('create', m.LeadCreateInput)
update = _write('update', m.LeadUpdateInput)
close = _write('close', m.LeadCloseInput)
reopen = _write('reopen', m.LeadVersionInput)
archive = _write('archive', m.LeadArchiveInput)
