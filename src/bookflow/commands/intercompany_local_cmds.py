"""Local-only intercompany setup switches and untargeted proposals, with no posting door."""
from bookflow.company import intercompany_local as local, intercompany_models as m
from bookflow.core.registry import command, Plan


def plan_settings_show(inp, ctx, s):
    return Plan(local.settings(s.company))


def plan_settings_update(inp, ctx, s):
    return local.settings_update(inp, ctx, s)


def plan_draft_create(inp, ctx, s):
    return local.draft_save(inp, ctx, s)


def plan_draft_update(inp, ctx, s):
    return local.draft_save(inp, ctx, s)


def plan_draft_show(inp, ctx, s):
    return Plan(local.draft_output(local.resolve_draft(s.company, inp.draft)))


def plan_draft_list(inp, ctx, s):
    return Plan(local.draft_list(inp, s))


def register(name, inp, out, planner, description, *, write=False, settings=False, positional=()):
    cmd = command(name, scope='company', input_model=inp, output_model=out, description=description,
        writes={'company'} if write else set(), truth='company',
        capability='company' if settings else 'intercompany.post' if write else 'intercompany.read',
        required_role='admin' if settings else 'standard' if write else 'member',
        positional=list(positional), accepts_idempotency_key=write,
        error_codes=['E_VALIDATION', 'E_FEATURE_DISABLED', 'E_PERMISSION', 'E_RECORD_NOT_FOUND',
                     'E_VERSION_CONFLICT', 'E_INACTIVE_REFERENCE', 'E_REASON_REQUIRED'])(planner)
    cmd.authorize_input = local.authorize_settings if settings else local.authorize_draft
    if write:
        cmd.plan_in_write = True
        cmd.applier(local.apply)
    return cmd


settings_show = register('intercompany-settings show', m.Empty, m.SettingsOutput, plan_settings_show,
    'Show local linked-entry, template and bulk switches. Every switch starts off; no counterpart is consulted.', settings=True)
settings_update = register('intercompany-settings update', m.SettingsUpdate, m.SettingsOutput, plan_settings_update,
    'Replace local feature switches with the expected version and a reason. This creates no accounts, relationship, or ledger entry.', write=True, settings=True)
draft_create = register('intercompany-draft create', m.DraftCreate, m.DraftOutput, plan_draft_create,
    'Save an untargeted local proposal for an advance or expense allocation. For example, amount_minor_units 10000 in USD means 100.00. No company, relationship or remote account is selected; nothing posts.', write=True)
draft_update = register('intercompany-draft update', m.DraftUpdate, m.DraftOutput, plan_draft_update,
    'Replace a local untargeted draft with its expected version and a reason. Omitted optional fields clear their values. Nothing posts or reserves a remote record.', write=True, positional=['draft'])
draft_show = register('intercompany-draft show', m.DraftSelect, m.DraftOutput, plan_draft_show,
    'Read a local untargeted draft. No counterpart information is queried or returned.', positional=['draft'])
draft_list = register('intercompany-draft list', m.DraftList, m.DraftPage, plan_draft_list,
    'List local untargeted drafts in stable ID order, fifty by default. No counterpart information is queried or returned.')
