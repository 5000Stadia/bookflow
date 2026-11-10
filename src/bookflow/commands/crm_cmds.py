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


# ---------------------------------------------------------------- increment 2: conversion, history, imports

def plan_convert(inp, ctx, s):
    from bookflow.company import crm_conversion
    return crm_conversion.prepare(s, ctx, inp)


def _apply_convert(plan, ctx, s):
    from bookflow.company import crm_conversion
    return crm_conversion.apply(plan, ctx, s)


def _replay_convert(inp, ctx, s, hit):
    from bookflow.company import crm_conversion
    return crm_conversion.replay(inp, ctx, s, hit)


convert = command('lead convert', scope='company', capability='crm.convert', required_role='standard',
    description=('Confirm-first: convert a lead to a new top-level customer (native customer validation; name, '
                 'address, contacts and summary copied, nothing financial invented) or link it to an existing '
                 'customer (link only unless contact_transfers names exact fields). Run --dry-run to preview copied '
                 'fields, contact mapping and matching customers. One reversible batch; `batch reverse` undoes it '
                 'while nothing later depends on the customer. Example: {"lead": "01J...", "expected_version": 2, '
                 '"target": "new"}.'),
    input_model=m.LeadConvertInput, output_model=m.LeadConvertOutput, writes={'company'}, accepts_idempotency_key=True,
    positional=['lead'], version_source=('lead show', 'lead', 'version'),
    error_codes=[*_ERRORS, 'E_VERSION_CONFLICT', 'E_IDEMPOTENCY_MISMATCH', 'E_CRM_ALREADY_CONVERTED', 'E_NAME_TAKEN'])(plan_convert)
convert.authorize_input = authorize
convert.plan_in_write = True
convert.replay = _replay_convert
convert.applier(_apply_convert)


def plan_timeline(inp, ctx, s):
    from bookflow.company import crm_conversion
    return Plan(crm_conversion.timeline(s, ctx, inp))


timeline = command('crm timeline', scope='company', capability='crm.read', required_role='member',
    description=('One history for a lead and the customer it became: notes, to-dos, files and conversion events, '
                 'newest first, each labelled "From lead ..." on the customer side. Sources the reader may not read '
                 'are left out and named. Example: {"lead": "01J..."} or {"customer": "Ada Lane"}.'),
    input_model=m.TimelineInput, output_model=m.TimelineOutput,
    error_codes=[*_ERRORS, 'E_QUERY_STALE'])(plan_timeline)


def plan_import(inp, ctx, s):
    from bookflow.company import crm_imports
    results, pending = crm_imports.plan(s, ctx, inp)
    out = m.LeadImportOutput(source=inp.source, batch_id=None, created=0, unchanged=len(results) - len(pending),
                             rows=results, dry_run=s.dry_run)
    return Plan(out, dict(results=results, pending=pending, input=inp))


def _apply_import(plan, ctx, s):
    from bookflow.commands.import_cmds import _finalized
    from bookflow.company import crm_imports
    return _finalized(s, ctx, 'lead.import', lambda: crm_imports.run(s, ctx, plan.data['input'], plan.data['results'],
                                                                     plan.data['pending']), m.LeadImportOutput)


lead_import = command('lead import', scope='company', capability='crm.bulk', required_role='standard',
    description=('Import prospects keyed by a source and a per-row source key: a repeated row is unchanged, a row '
                 'whose key returns with different values refuses, and new rows land one by one in one reversible '
                 'batch per source, so a failed run resumes by running it again. Also needs import and crm.write. '
                 '`batch reverse` closes and archives unused imported leads and releases their keys. Example: '
                 '{"source": "fair-2026", "rows": [{"source_key": "17", "display_name": "Ada Lane"}]}.'),
    input_model=m.LeadImportInput, output_model=m.LeadImportOutput, writes={'company'},
    error_codes=[*_ERRORS, 'E_VERSION_CONFLICT'])(plan_import)
lead_import.authorize_input = authorize
lead_import.applier(_apply_import)
